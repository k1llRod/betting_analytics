# src/services/result_settlement_service.py
from datetime import datetime, timedelta
import re
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from src.models.entities import BetLog, Match
from src.services.telegram_service import TelegramAlertService
from src.utils import clean_str


class ResultSettlementService:
    """
    Servicio de Liquidación Desatendida de Resultados:
    Evalúa automáticamente las apuestas pendientes contra los marcadores finales registrados,
    actualiza el estado (WON / LOST / VOID), computa el P&L exacto y emite alertas a Telegram.
    """

    def __init__(self, db: Session, telegram: Optional[TelegramAlertService] = None):
        self.db = db
        self.telegram = telegram or TelegramAlertService()

    def evaluate_bet_outcome(
        self, market_str: str, home_score: int, away_score: int
    ) -> Tuple[str, float]:
        """
        Determina el resultado de la apuesta ('WON', 'LOST', 'VOID') y el factor de retorno.
        :return: (outcome, payout_factor) donde payout_factor es multiplicador sobre ganancia neta.
        """
        m_upper = market_str.upper()

        # 1. Apuesta Sin Empate / Draw No Bet (AH 0.0) - Comprobar antes de 'EMPATE'
        if "SIN EMPATE" in m_upper or "DNB" in m_upper or "DRAW NO BET" in m_upper:
            if home_score == away_score:
                return "VOID", 0.0
            if "LOCAL" in m_upper or "HOME" in m_upper:
                return ("WON" if home_score > away_score else "LOST"), 1.0
            else:
                return ("WON" if away_score > home_score else "LOST"), 1.0

        # 2. Mercado 1X2 Clásico
        if "GANA LOCAL" in m_upper:
            return ("WON" if home_score > away_score else "LOST"), 1.0
        elif "EMPATE" in m_upper or " (X)" in m_upper:
            return ("WON" if home_score == away_score else "LOST"), 1.0
        elif "GANA VISITANTE" in m_upper:
            return ("WON" if away_score > home_score else "LOST"), 1.0

        # 3. Total de Goles (Over / Under)
        total_goals = home_score + away_score
        line_match = re.search(r"(\d+(\.\d+)?)", m_upper)
        line = float(line_match.group(1)) if line_match else 2.5

        if "MÁS DE" in m_upper or "MAS DE" in m_upper or "OVER" in m_upper:
            return ("WON" if total_goals > line else "LOST"), 1.0
        elif "MENOS DE" in m_upper or "UNDER" in m_upper:
            return ("WON" if total_goals < line else "LOST"), 1.0

        # 4. Ambos Equipos Marcan (BTTS)
        if "AMBOS MARCAN" in m_upper or "BTTS" in m_upper:
            btts = home_score > 0 and away_score > 0
            if "SÍ" in m_upper or "SI" in m_upper or "YES" in m_upper:
                return ("WON" if btts else "LOST"), 1.0
            else:
                return ("WON" if not btts else "LOST"), 1.0

        # 5. Doble Oportunidad
        if "1X" in m_upper or "LOCAL O EMPATE" in m_upper:
            return ("WON" if home_score >= away_score else "LOST"), 1.0
        elif "X2" in m_upper or "EMPATE O VISITANTE" in m_upper:
            return ("WON" if away_score >= home_score else "LOST"), 1.0
        elif "12" in m_upper or "LOCAL O VISITANTE" in m_upper:
            return ("WON" if home_score != away_score else "LOST"), 1.0

        # 6. Hándicap Asiático (-0.5, +0.5, -1.5, +1.5)
        if "AH " in m_upper or "HÁNDICAP" in m_upper or "HANDICAP" in m_upper:
            # Buscar valor del hándicap con signo (ej: -0.5, +0.5, -1.0)
            ah_match = re.search(r"([+-]?\d+(\.\d+)?)", m_upper)
            if ah_match:
                h = float(ah_match.group(1))
                diff = home_score - away_score
                margin = diff + h
                if margin > 0:
                    return "WON", 1.0
                elif margin == 0:
                    return "VOID", 0.0
                else:
                    return "LOST", 1.0

        # Por defecto si no se puede inferir con certeza
        return "PENDING", 0.0

    def settle_pending_bets(self) -> int:
        """
        Busca todas las apuestas pendientes, localiza el partido en la base de datos
        y si ya finalizó, liquida la apuesta y registra el P&L.
        """
        pending_bets: List[BetLog] = (
            self.db.query(BetLog)
            .filter(BetLog.status == "PENDING")
            .all()
        )

        if not pending_bets:
            return 0

        settled_count = 0
        now = datetime.utcnow()

        for bet in pending_bets:
            parts = [p.strip().upper() for p in bet.match_name.split("vs")]
            if len(parts) != 2:
                continue
            bet_home, bet_away = parts[0], parts[1]

            # Buscar partido finalizado coincidente en la BD
            # Filtro por nombres limpios
            clean_b_home = clean_str(bet_home)
            clean_b_away = clean_str(bet_away)

            matches: List[Match] = (
                self.db.query(Match)
                .filter(Match.status == "FINISHED")
                .all()
            )

            matched_game: Optional[Match] = None
            for m in matches:
                m_home = clean_str(m.home_team)
                m_away = clean_str(m.away_team)
                if (clean_b_home in m_home or m_home in clean_b_home) and (
                    clean_b_away in m_away or m_away in clean_b_away
                ):
                    matched_game = m
                    break

            if not matched_game or matched_game.home_score is None or matched_game.away_score is None:
                continue

            outcome, mult = self.evaluate_bet_outcome(
                bet.market, matched_game.home_score, matched_game.away_score
            )

            if outcome == "PENDING":
                continue

            bet.status = outcome
            bet.resolved_at = now

            if outcome == "WON":
                bet.profit_loss = round(bet.stake * (bet.odds - 1.0) * mult, 2)
            elif outcome == "LOST":
                bet.profit_loss = -round(bet.stake, 2)
            elif outcome == "VOID":
                bet.profit_loss = 0.0

            self.db.commit()
            settled_count += 1

            print(
                f"[ResultSettlementService] Apuesta #{bet.id} liquidada: "
                f"{bet.match_name} ({matched_game.home_score}-{matched_game.away_score}) | "
                f"{bet.market} -> {outcome} (P&L: ${bet.profit_loss:+.2f})"
            )

            if self.telegram.is_configured():
                self.telegram.send_settlement_alert(
                    match_name=bet.match_name,
                    market=bet.market,
                    outcome=outcome,
                    profit_loss=bet.profit_loss,
                    stake=bet.stake,
                )

        return settled_count
