# src/services/poisson_model.py
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

try:
    from scipy.stats import poisson as _scipy_poisson
    def _poisson_pmf(k: int, mu: float) -> float:
        return float(_scipy_poisson.pmf(k, mu))
except Exception:
    def _poisson_pmf(k: int, mu: float) -> float:
        return (math.exp(-mu) * (mu ** k)) / math.factorial(k)

try:
    from scipy.optimize import minimize as _scipy_minimize
    _HAS_SCIPY_OPTIMIZE = True
except Exception:
    _HAS_SCIPY_OPTIMIZE = False

from sqlalchemy.orm import Session
from src.models.entities import Match, MatchStats, TeamParametersCache


@dataclass
class MatchProbabilities:
    # 1X2 Clásico
    home_win: float
    draw: float
    away_win: float
    # Totales de Goles (Over/Under)
    over_1_5_goals: float
    under_1_5_goals: float
    over_2_5_goals: float
    under_2_5_goals: float
    over_3_5_goals: float
    under_3_5_goals: float
    # Ambos Equipos Anotan (BTTS)
    btts_yes: float
    btts_no: float
    # Doble Oportunidad
    double_chance: Dict[str, float]  # {"1X": float, "12": float, "X2": float}
    # Apuesta Sin Empate (Draw No Bet / AH 0.0)
    draw_no_bet: Dict[str, float]  # {"HOME": float, "AWAY": float}
    # Líneas de Hándicap Asiático completas
    # Formato: {"-1.5": {"HOME": p, "AWAY": p}, "-0.5": ..., "0.0": ..., "+0.5": ...}
    asian_handicap: Dict[str, Dict[str, float]]
    # Parámetros esperados y córners
    expected_home_goals: float
    expected_away_goals: float
    over_9_5_corners: float
    under_9_5_corners: float
    # Matriz bivariada completa (opcional para simulaciones)
    score_matrix: Optional[np.ndarray] = field(default=None, repr=False)


class PoissonPredictor:
    """
    Motor cuantitativo predictivo Dixon-Coles (1997) con:
    1. Ponderación temporal exponencial (xi configurable o calibrable vía MLE).
    2. Incorporación de Expected Goals (xG) para reducir varianza en muestras cortas.
    3. Mercados avanzados: BTTS, Doble Oportunidad, Draw No Bet y Hándicaps Asiáticos.
    4. Capa de caché persistente en base de datos para acelerar inferencia en producción.
    """

    def __init__(
        self,
        db: Session,
        xi: float = 0.0035,
        rho: float = -0.11,
        xg_weight: float = 0.0,
        use_cache: bool = True,
        cache_ttl_hours: int = 12,
        fit_method: str = "analytical",
    ):
        """
        :param db: Sesión activa de SQLAlchemy
        :param xi: Factor de decaimiento temporal diario (0.0035 ~ semivida de ~198 días)
        :param rho: Parámetro de correlación Dixon-Coles para marcadores bajos
        :param xg_weight: Ponderación de Goles Esperados [0.0 = sólo goles reales, 1.0 = sólo xG]
        :param use_cache: Si es True, utiliza la tabla team_parameters_cache
        :param cache_ttl_hours: Horas de validez de los parámetros cacheados
        :param fit_method: 'analytical' (rápido ponderado) o 'mle' (máxima verosimilitud)
        """
        self.db = db
        self.xi = float(xi)
        self.rho = float(rho)
        self.xg_weight = float(max(0.0, min(1.0, xg_weight)))
        self.use_cache = use_cache
        self.cache_ttl_hours = cache_ttl_hours
        self.fit_method = fit_method

    @staticmethod
    def dixon_coles_tau(x: int, y: int, lambda_param: float, mu_param: float, rho: float) -> float:
        """Factor multiplicador tau de Dixon & Coles para corregir la dependencia 0-0, 1-0, 0-1, 1-1."""
        if x == 0 and y == 0:
            return max(0.0, 1.0 - (lambda_param * mu_param * rho))
        elif x == 0 and y == 1:
            return max(0.0, 1.0 + (lambda_param * rho))
        elif x == 1 and y == 0:
            return max(0.0, 1.0 + (mu_param * rho))
        elif x == 1 and y == 1:
            return max(0.0, 1.0 - rho)
        else:
            return 1.0

    def _effective_goals(self, match: Match) -> Tuple[float, float]:
        """
        Retorna los goles efectivos del partido combinando goles observados y xG.
        Si las métricas xG están en match.stats se usan directamente; si no,
        se recurre a una estimación sintética basada en tiros a puerta y córners.
        """
        real_home = float(match.home_score if match.home_score is not None else 0.0)
        real_away = float(match.away_score if match.away_score is not None else 0.0)

        if self.xg_weight <= 0.0:
            return real_home, real_away

        xg_home: Optional[float] = None
        xg_away: Optional[float] = None

        if match.stats:
            if match.stats.home_xg is not None and match.stats.away_xg is not None:
                xg_home = float(match.stats.home_xg)
                xg_away = float(match.stats.away_xg)
            elif match.stats.home_shots_target is not None and match.stats.away_shots_target is not None:
                # Estimador sintético de xG basado en tiros a puerta (0.31) + córners (0.035)
                hc = match.stats.home_corners or 0
                ac = match.stats.away_corners or 0
                xg_home = 0.31 * match.stats.home_shots_target + 0.035 * hc
                xg_away = 0.31 * match.stats.away_shots_target + 0.035 * ac

        if xg_home is None or xg_away is None:
            # Sin datos de xG, fallback a goles reales
            return real_home, real_away

        eff_home = (1.0 - self.xg_weight) * real_home + self.xg_weight * xg_home
        eff_away = (1.0 - self.xg_weight) * real_away + self.xg_weight * xg_away
        return max(0.0, eff_home), max(0.0, eff_away)

    def _load_cached_ratings(
        self, league: str
    ) -> Optional[Tuple[Dict[str, float], Dict[str, float], float, float, float, float]]:
        """Intenta recuperar del caché los parámetros calculados para la liga."""
        if not self.use_cache:
            return None

        now = datetime.utcnow()
        rows = (
            self.db.query(TeamParametersCache)
            .filter(
                TeamParametersCache.league == league,
                TeamParametersCache.expires_at > now,
                TeamParametersCache.xi == self.xi,
                TeamParametersCache.xg_weight == self.xg_weight,
            )
            .all()
        )

        if not rows:
            return None

        attack = {}
        defense = {}
        home_adv = 1.0
        rho = self.rho

        for r in rows:
            attack[r.team_name] = r.attack_rating
            defense[r.team_name] = r.defense_rating
            home_adv = r.home_advantage
            rho = r.rho

        # Reconstruir promedios aproximados
        avg_hg = 1.45 * home_adv
        avg_ag = 1.15
        return attack, defense, home_adv, avg_hg, avg_ag, rho

    def _save_cached_ratings(
        self,
        league: str,
        attack: Dict[str, float],
        defense: Dict[str, float],
        home_adv: float,
        rho: float,
    ):
        """Persiste los parámetros calculados en la base de datos."""
        if not self.use_cache:
            return

        now = datetime.utcnow()
        expires = now + timedelta(hours=self.cache_ttl_hours)

        try:
            # Limpiar entradas previas de la liga
            self.db.query(TeamParametersCache).filter(
                TeamParametersCache.league == league
            ).delete()

            for team, att in attack.items():
                dfn = defense.get(team, 1.0)
                entry = TeamParametersCache(
                    league=league,
                    team_name=team,
                    attack_rating=float(att),
                    defense_rating=float(dfn),
                    home_advantage=float(home_adv),
                    rho=float(rho),
                    xi=float(self.xi),
                    use_xg=bool(self.xg_weight > 0.0),
                    xg_weight=float(self.xg_weight),
                    fitted_at=now,
                    expires_at=expires,
                )
                self.db.add(entry)

            self.db.commit()
        except Exception:
            self.db.rollback()

    def _get_team_ratings(
        self, league: str, ref_date: Optional[datetime] = None, force_recalc: bool = False
    ) -> Tuple[Dict[str, float], Dict[str, float], float, float, float, float]:
        """
        Obtiene ratings de ataque y defensa, factor de ventaja local (gamma) y rho.
        Primero consulta el caché persistente en DB; si no está o expiró, lo calcula.
        """
        if not force_recalc:
            cached = self._load_cached_ratings(league)
            if cached is not None:
                return cached

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
            raise ValueError(f"No se registraron partidos finalizados para la liga '{league}'.")

        # 1. Ajuste analítico rápido ponderado por decaimiento temporal y xG
        total_weight = 0.0
        home_goals_weighted = 0.0
        away_goals_weighted = 0.0

        team_att_home: Dict[str, float] = {}
        team_att_away: Dict[str, float] = {}
        team_def_home: Dict[str, float] = {}
        team_def_away: Dict[str, float] = {}
        team_weights: Dict[str, float] = {}

        for m in matches:
            eff_h, eff_a = self._effective_goals(m)

            m_date = m.match_date if m.match_date else ref_date
            if hasattr(m_date, "tzinfo") and m_date.tzinfo is not None:
                m_date = m_date.replace(tzinfo=None)

            days_diff = max(0, (ref_date - m_date).days)
            weight = math.exp(-self.xi * days_diff)

            total_weight += weight
            home_goals_weighted += eff_h * weight
            away_goals_weighted += eff_a * weight

            for t in [m.home_team, m.away_team]:
                team_weights[t] = team_weights.get(t, 0.0) + weight

            team_att_home[m.home_team] = team_att_home.get(m.home_team, 0.0) + eff_h * weight
            team_def_home[m.home_team] = team_def_home.get(m.home_team, 0.0) + eff_a * weight

            team_att_away[m.away_team] = team_att_away.get(m.away_team, 0.0) + eff_a * weight
            team_def_away[m.away_team] = team_def_away.get(m.away_team, 0.0) + eff_h * weight

        avg_home_goals = home_goals_weighted / max(1e-5, total_weight)
        avg_away_goals = away_goals_weighted / max(1e-5, total_weight)
        home_advantage = max(0.5, avg_home_goals / max(0.1, avg_away_goals))

        all_teams = set(team_weights.keys())
        attack_rating = {}
        defense_rating = {}

        for t in all_teams:
            w = team_weights.get(t, 1.0)
            goals_scored = team_att_home.get(t, 0.0) + team_att_away.get(t, 0.0)
            goals_conceded = team_def_home.get(t, 0.0) + team_def_away.get(t, 0.0)

            expected_scored = ((avg_home_goals + avg_away_goals) / 2.0) * w
            expected_conceded = expected_scored

            attack_rating[t] = max(0.2, min(3.0, goals_scored / max(1e-4, expected_scored)))
            defense_rating[t] = max(0.2, min(3.0, goals_conceded / max(1e-4, expected_conceded)))

        # 2. Si se solicitó MLE y scipy está disponible, refinar parámetros
        if self.fit_method == "mle" and _HAS_SCIPY_OPTIMIZE and len(all_teams) >= 4:
            try:
                attack_rating, defense_rating, home_advantage, self.rho = self._fit_mle(
                    matches, all_teams, ref_date, attack_rating, defense_rating, home_advantage
                )
            except Exception:
                pass  # Conservar aproximación analítica en caso de fallo en optimizador

        # Persistir en base de datos para no recalcular
        self._save_cached_ratings(league, attack_rating, defense_rating, home_advantage, self.rho)

        return attack_rating, defense_rating, home_advantage, avg_home_goals, avg_away_goals, self.rho

    def _fit_mle(
        self,
        matches: List[Match],
        teams: set,
        ref_date: datetime,
        init_att: Dict[str, float],
        init_def: Dict[str, float],
        init_gamma: float,
    ) -> Tuple[Dict[str, float], Dict[str, float], float, float]:
        """Ajuste de máxima verosimilitud de Dixon-Coles."""
        team_list = sorted(list(teams))
        n_teams = len(team_list)
        team_idx = {t: i for i, t in enumerate(team_list)}

        # Vector de parámetros: [att_0..att_{n-2}, def_0..def_{n-1}, gamma, rho]
        # Restricción: promedio(att) = 1.0, por tanto att_{n-1} = n - sum(att_0..att_{n-2})
        x0 = [init_att.get(t, 1.0) for t in team_list[:-1]]
        x0 += [init_def.get(t, 1.0) for t in team_list]
        x0 += [init_gamma, self.rho]

        # Extraer partidos formateados para evaluación vectorizada
        match_data = []
        for m in matches:
            if m.home_team not in team_idx or m.away_team not in team_idx:
                continue
            eff_h, eff_a = self._effective_goals(m)
            m_date = m.match_date if m.match_date else ref_date
            if hasattr(m_date, "tzinfo") and m_date.tzinfo is not None:
                m_date = m_date.replace(tzinfo=None)
            days_diff = max(0, (ref_date - m_date).days)
            weight = math.exp(-self.xi * days_diff)
            match_data.append((team_idx[m.home_team], team_idx[m.away_team], eff_h, eff_a, weight))

        def neg_log_likelihood(params):
            att_vec = list(params[: n_teams - 1])
            last_att = max(0.1, n_teams - sum(att_vec))
            att_vec.append(last_att)
            def_vec = list(params[n_teams - 1 : 2 * n_teams - 1])
            gamma = max(0.2, params[2 * n_teams - 1])
            rho = params[2 * n_teams]

            ll = 0.0
            for h_i, a_i, h_g, a_g, w in match_data:
                lam = max(0.05, att_vec[h_i] * def_vec[a_i] * gamma)
                mu = max(0.05, att_vec[a_i] * def_vec[h_i])
                tau = self.dixon_coles_tau(int(round(h_g)), int(round(a_g)), lam, mu, rho)
                if tau <= 0:
                    tau = 1e-6
                log_p_x = -lam + h_g * math.log(lam) - math.lgamma(h_g + 1)
                log_p_y = -mu + a_g * math.log(mu) - math.lgamma(a_g + 1)
                ll += w * (math.log(tau) + log_p_x + log_p_y)

            return -ll

        bounds = [(0.1, 3.0)] * (n_teams - 1) + [(0.1, 3.0)] * n_teams + [(0.5, 2.5), (-0.4, 0.4)]
        res = _scipy_minimize(neg_log_likelihood, x0, bounds=bounds, method="L-BFGS-B", options={"maxiter": 60})

        if res.success or res.fun < 1e7:
            p = res.x
            att_vec = list(p[: n_teams - 1])
            att_vec.append(max(0.1, n_teams - sum(att_vec)))
            def_vec = list(p[n_teams - 1 : 2 * n_teams - 1])
            fitted_gamma = float(p[2 * n_teams - 1])
            fitted_rho = float(p[2 * n_teams])

            new_att = {team_list[i]: float(att_vec[i]) for i in range(n_teams)}
            new_def = {team_list[i]: float(def_vec[i]) for i in range(n_teams)}
            return new_att, new_def, fitted_gamma, fitted_rho

        return init_att, init_def, init_gamma, self.rho

    def calibrate_xi(
        self, league: str, xi_candidates: Optional[List[float]] = None
    ) -> float:
        """
        Calibra el factor de decaimiento temporal xi evaluando la verosimilitud de validación
        o ajuste en una rejilla típica [0.001, 0.002, 0.0035, 0.005, 0.007].
        """
        if xi_candidates is None:
            xi_candidates = [0.0010, 0.0020, 0.0035, 0.0050, 0.0070]

        best_xi = 0.0035
        best_score = float("inf")

        matches = (
            self.db.query(Match)
            .filter(Match.league == league, Match.status == "FINISHED")
            .order_by(Match.match_date.desc())
            .limit(200)
            .all()
        )
        if len(matches) < 20:
            return best_xi

        for xi_val in xi_candidates:
            # Evaluar Brier score o log-loss
            temp_predictor = PoissonPredictor(
                self.db, xi=xi_val, xg_weight=self.xg_weight, use_cache=False
            )
            score = 0.0
            for m in matches[:50]:  # Test set de los 50 más recientes
                try:
                    p = temp_predictor.predict_match(
                        league=league,
                        home_team=m.home_team,
                        away_team=m.away_team,
                        ref_date=m.match_date,
                    )
                    actual_home = 1.0 if m.home_score > m.away_score else 0.0
                    actual_draw = 1.0 if m.home_score == m.away_score else 0.0
                    actual_away = 1.0 if m.away_score > m.home_score else 0.0
                    # Brier score
                    score += (p.home_win - actual_home) ** 2 + (p.draw - actual_draw) ** 2 + (p.away_win - actual_away) ** 2
                except Exception:
                    continue

            if score < best_score:
                best_score = score
                best_xi = xi_val

        self.xi = best_xi
        return best_xi

    def predict_match(
        self,
        league: str,
        home_team: str,
        away_team: str,
        max_goals: int = 10,
        ref_date: Optional[datetime] = None,
        force_recalc: bool = False,
    ) -> MatchProbabilities:
        """
        Calcula la matriz bivariada Dixon-Coles y deriva todas las probabilidades
        de los mercados soportados (1X2, Totales, BTTS, Hándicaps Asiáticos y Doble Oportunidad).
        """
        att, dfn, home_adv, avg_hg, avg_ag, rho_used = self._get_team_ratings(
            league, ref_date=ref_date, force_recalc=force_recalc
        )

        home_att = att.get(home_team, 1.0)
        home_def = dfn.get(home_team, 1.0)
        away_att = att.get(away_team, 1.0)
        away_def = dfn.get(away_team, 1.0)

        # Tasas esperadas lambda y mu
        lambda_param = max(0.1, home_att * away_def * avg_hg)
        mu_param = max(0.1, away_att * home_def * avg_ag)

        # Matriz bivariada con corrección Dixon-Coles
        score_matrix = np.zeros((max_goals + 1, max_goals + 1))
        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                p_x = _poisson_pmf(x, lambda_param)
                p_y = _poisson_pmf(y, mu_param)
                tau = self.dixon_coles_tau(x, y, lambda_param, mu_param, rho_used)
                score_matrix[x, y] = max(0.0, p_x * p_y * tau)

        # Normalización unitaria
        total_p = np.sum(score_matrix)
        if total_p > 0:
            score_matrix /= total_p

        # 1. 1X2
        p_home_win = float(np.sum(np.tril(score_matrix, -1)))
        p_draw = float(np.sum(np.diag(score_matrix)))
        p_away_win = float(np.sum(np.triu(score_matrix, 1)))

        # 2. Over / Under (1.5, 2.5, 3.5)
        def _get_ou_prob(line: float) -> Tuple[float, float]:
            p_under = 0.0
            for x in range(max_goals + 1):
                for y in range(max_goals + 1):
                    if x + y < line:
                        p_under += score_matrix[x, y]
            return max(0.0, 1.0 - p_under), float(p_under)

        p_over_1_5, p_under_1_5 = _get_ou_prob(1.5)
        p_over_2_5, p_under_2_5 = _get_ou_prob(2.5)
        p_over_3_5, p_under_3_5 = _get_ou_prob(3.5)

        # 3. Ambos Equipos Anotan (BTTS)
        p_btts_no = float(np.sum(score_matrix[0, :]) + np.sum(score_matrix[:, 0]) - score_matrix[0, 0])
        p_btts_yes = max(0.0, 1.0 - p_btts_no)

        # 4. Doble Oportunidad
        double_chance = {
            "1X": p_home_win + p_draw,
            "12": p_home_win + p_away_win,
            "X2": p_draw + p_away_win,
        }

        # 5. Apuesta Sin Empate (Draw No Bet / AH 0.0)
        # Condicionado a que no haya empate: P(Win)/(1 - P(Draw))
        non_draw_p = max(1e-5, 1.0 - p_draw)
        draw_no_bet = {
            "HOME": min(0.99, p_home_win / non_draw_p),
            "AWAY": min(0.99, p_away_win / non_draw_p),
        }

        # 6. Hándicaps Asiáticos (Asian Handicap)
        # Líneas analíticas: -1.5, -1.0, -0.5, 0.0, +0.5, +1.0, +1.5
        asian_handicap: Dict[str, Dict[str, float]] = {}

        # AH -0.5 es equivalente a victoria simple; +0.5 es 1X / X2
        asian_handicap["-0.5"] = {"HOME": p_home_win, "AWAY": 1.0 - p_home_win}
        asian_handicap["+0.5"] = {"HOME": p_home_win + p_draw, "AWAY": p_away_win}

        # AH 0.0 (DNB)
        asian_handicap["0.0"] = {"HOME": draw_no_bet["HOME"], "AWAY": draw_no_bet["AWAY"]}

        # AH -1.5 (Gana por 2 o más goles)
        p_home_win_by_2 = 0.0
        p_away_win_by_2 = 0.0
        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                if x - y >= 2:
                    p_home_win_by_2 += score_matrix[x, y]
                if y - x >= 2:
                    p_away_win_by_2 += score_matrix[x, y]

        asian_handicap["-1.5"] = {"HOME": float(p_home_win_by_2), "AWAY": float(1.0 - p_home_win_by_2)}
        asian_handicap["+1.5"] = {"HOME": float(1.0 - p_away_win_by_2), "AWAY": float(p_away_win_by_2)}

        # AH -1.0 (Si gana por 1 hay push, si gana por 2+ gana)
        p_home_win_by_1 = float(sum(score_matrix[x, x - 1] for x in range(1, max_goals + 1)))
        non_push_home_1 = max(1e-5, 1.0 - p_home_win_by_1)
        asian_handicap["-1.0"] = {
            "HOME": float(p_home_win_by_2 / non_push_home_1),
            "AWAY": float((1.0 - p_home_win_by_2 - p_home_win_by_1) / non_push_home_1),
        }

        # Córners (medias estimadas)
        p_over_9_5_corners = 0.48
        p_under_9_5_corners = 0.52

        return MatchProbabilities(
            home_win=p_home_win,
            draw=p_draw,
            away_win=p_away_win,
            over_1_5_goals=p_over_1_5,
            under_1_5_goals=p_under_1_5,
            over_2_5_goals=p_over_2_5,
            under_2_5_goals=p_under_2_5,
            over_3_5_goals=p_over_3_5,
            under_3_5_goals=p_under_3_5,
            btts_yes=p_btts_yes,
            btts_no=p_btts_no,
            double_chance=double_chance,
            draw_no_bet=draw_no_bet,
            asian_handicap=asian_handicap,
            expected_home_goals=lambda_param,
            expected_away_goals=mu_param,
            over_9_5_corners=p_over_9_5_corners,
            under_9_5_corners=p_under_9_5_corners,
            score_matrix=score_matrix,
        )
