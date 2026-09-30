# src/services/poisson_model.py
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson
from sqlalchemy.orm import Session
from src.models.entities import Match, MatchStats


@dataclass
class MatchProbabilities:
    home_win: float
    draw: float
    away_win: float
    over_2_5_goals: float
    under_2_5_goals: float
    btts_yes: float
    btts_no: float
    over_9_5_corners: float
    under_9_5_corners: float
    expected_home_goals: float
    expected_away_goals: float


class PoissonPredictor:
    """Motor predictivo Dixon-Coles con corrección de marcadores bajos y ponderación temporal."""

    def __init__(self, db: Session, xi: float = 0.003, rho: float = -0.11):
        """xi: Factor de decaimiento temporal diario (0.003 equivale a ~medio año de vida media)

        rho: Parámetro de correlación para marcadores bajos (Dixon-Coles)
        """
        self.db = db
        self.xi = xi
        self.rho = rho

    def _dixon_coles_tau(
            self, x: int, y: int, lambda_param: float, mu_param: float
    ) -> float:
        """Factor multiplicador tau de Dixon & Coles para corregir la dependencia 0-0, 1-0, 0-1, 1-1."""
        if x == 0 and y == 0:
            return 1.0 - (lambda_param * mu_param * self.rho)
        elif x == 0 and y == 1:
            return 1.0 + (lambda_param * self.rho)
        elif x == 1 and y == 0:
            return 1.0 + (mu_param * self.rho)
        elif x == 1 and y == 1:
            return 1.0 - self.rho
        else:
            return 1.0

    def _get_team_ratings(
            self, league: str, ref_date: Optional[datetime] = None
    ) -> Tuple[Dict[str, float], Dict[str, float], float, float]:
        """Calcula fortalezas de ataque y defensa ponderadas por tiempo."""
        if ref_date is None:
            ref_date = datetime.now()
        if ref_date.tzinfo is not None:
            ref_date = ref_date.replace(tzinfo=None)

        matches: List[Match] = (
            self.db.query(Match)
            .filter(Match.league == league, Match.status == "FINISHED")
            .all()
        )

        if not matches:
            raise ValueError(
                f"No se registraron partidos finalizados para la liga '{league}'."
            )

        total_weight = 0.0
        home_goals_weighted = 0.0
        away_goals_weighted = 0.0

        team_att_home: Dict[str, float] = {}
        team_att_away: Dict[str, float] = {}
        team_def_home: Dict[str, float] = {}
        team_def_away: Dict[str, float] = {}
        team_weights: Dict[str, float] = {}

        for m in matches:
            if m.home_score is None or m.away_score is None:
                continue

            m_date = m.match_date if m.match_date else ref_date
            if hasattr(m_date, "tzinfo") and m_date.tzinfo is not None:
                m_date = m_date.replace(tzinfo=None)

            days_diff = max(0, (ref_date - m_date).days)
            weight = math.exp(-self.xi * days_diff)

            total_weight += weight
            home_goals_weighted += m.home_score * weight
            away_goals_weighted += m.away_score * weight

            # Acumuladores ponderados
            for t in [m.home_team, m.away_team]:
                team_weights[t] = team_weights.get(t, 0.0) + weight

            team_att_home[m.home_team] = (
                    team_att_home.get(m.home_team, 0.0) + m.home_score * weight
            )
            team_def_home[m.home_team] = (
                    team_def_home.get(m.home_team, 0.0) + m.away_score * weight
            )

            team_att_away[m.away_team] = (
                    team_att_away.get(m.away_team, 0.0) + m.away_score * weight
            )
            team_def_away[m.away_team] = (
                    team_def_away.get(m.away_team, 0.0) + m.home_score * weight
            )

        avg_home_goals = home_goals_weighted / max(1e-5, total_weight)
        avg_away_goals = away_goals_weighted / max(1e-5, total_weight)

        all_teams = set(team_weights.keys())
        attack_rating = {}
        defense_rating = {}

        for t in all_teams:
            w = team_weights.get(t, 1.0)
            goals_scored = team_att_home.get(t, 0.0) + team_att_away.get(t, 0.0)
            goals_conceded = team_def_home.get(t, 0.0) + team_def_away.get(t, 0.0)

            expected_scored = ((avg_home_goals + avg_away_goals) / 2.0) * w
            expected_conceded = expected_scored

            attack_rating[t] = max(0.2, goals_scored / max(1e-4, expected_scored))
            defense_rating[t] = max(0.2, goals_conceded / max(1e-4, expected_conceded))

        return attack_rating, defense_rating, avg_home_goals, avg_away_goals

    def predict_match(
            self,
            league: str,
            home_team: str,
            away_team: str,
            max_goals: int = 10,
            ref_date: Optional[datetime] = None,
    ) -> MatchProbabilities:
        att, dfn, avg_hg, avg_ag = self._get_team_ratings(league, ref_date)

        home_att = att.get(home_team, 1.0)
        home_def = dfn.get(home_team, 1.0)
        away_att = att.get(away_team, 1.0)
        away_def = dfn.get(away_team, 1.0)

        # Tasas esperadas (lambda local, mu visitante)
        lambda_param = max(0.1, home_att * away_def * avg_hg)
        mu_param = max(0.1, away_att * home_def * avg_ag)

        # Matriz bivariada con corrección Dixon-Coles
        score_matrix = np.zeros((max_goals + 1, max_goals + 1))
        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                p_x = poisson.pmf(x, lambda_param)
                p_y = poisson.pmf(y, mu_param)
                tau = self._dixon_coles_tau(x, y, lambda_param, mu_param)
                score_matrix[x, y] = max(0.0, p_x * p_y * tau)

        # Normalización para asegurar suma probabilística unitaria exacta = 1.0
        score_matrix /= np.sum(score_matrix)

        # Cálculo de eventos
        p_home_win = float(np.sum(np.tril(score_matrix, -1)))
        p_draw = float(np.sum(np.diag(score_matrix)))
        p_away_win = float(np.sum(np.triu(score_matrix, 1)))

        p_under_2_5 = 0.0
        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                if x + y < 2.5:
                    p_under_2_5 += score_matrix[x, y]
        p_over_2_5 = max(0.0, 1.0 - p_under_2_5)

        p_btts_no = float(
            np.sum(score_matrix[0, :])
            + np.sum(score_matrix[:, 0])
            - score_matrix[0, 0]
        )
        p_btts_yes = max(0.0, 1.0 - p_btts_no)

        # Córners (basado en medias históricas de liga)
        p_over_9_5_corners = 0.48
        p_under_9_5_corners = 0.52

        return MatchProbabilities(
            home_win=p_home_win,
            draw=p_draw,
            away_win=p_away_win,
            over_2_5_goals=p_over_2_5,
            under_2_5_goals=p_under_2_5,
            btts_yes=p_btts_yes,
            btts_no=p_btts_no,
            over_9_5_corners=p_over_9_5_corners,
            under_9_5_corners=p_under_9_5_corners,
            expected_home_goals=lambda_param,
            expected_away_goals=mu_param,
        )
