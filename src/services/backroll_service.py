# src/services/bankroll_service.py
from datetime import datetime
from typing import Any, Dict, List, Optional
import pandas as pd
from sqlalchemy.orm import Session
from src.models.entities import BetLog


class BankrollService:

    def __init__(self, db: Session):
        self.db = db

    def place_bet(
        self,
        league: str,
        match_name: str,
        market: str,
        odds: float,
        p_model: float,
        edge: float,
        stake: float,
    ) -> BetLog:
        bet = BetLog(
            placed_at=datetime.utcnow(),
            league=str(league),
            match_name=str(match_name),
            market=str(market),
            odds=float(odds),
            p_model=float(p_model),
            edge=float(edge),
            stake=float(stake),
            status="PENDING",
            profit_loss=0.0,
        )
        self.db.add(bet)
        self.db.commit()
        self.db.refresh(bet)
        return bet

    def resolve_bet(
            self, bet_id: int, outcome: str, closing_odds: Optional[float] = None
    ) -> Optional[BetLog]:
        bet = self.db.query(BetLog).filter(BetLog.id == bet_id).first()
        if not bet:
            return None

        outcome = outcome.upper()
        bet.status = outcome
        bet.resolved_at = datetime.utcnow()
        if closing_odds:
            bet.closing_odds = closing_odds

        if outcome == "WON":
            bet.profit_loss = round(bet.stake * (bet.odds - 1.0), 2)
        elif outcome == "LOST":
            bet.profit_loss = -round(bet.stake, 2)
        elif outcome == "VOID":
            bet.profit_loss = 0.0

        self.db.commit()
        self.db.refresh(bet)
        return bet

    def get_summary_metrics(self) -> Dict[str, Any]:
        """Calcula las métricas cuantitativas clave del portafolio de apuestas."""
        resolved_bets = (
            self.db.query(BetLog).filter(BetLog.status.in_(["WON", "LOST"])).all()
        )

        pending_bets = (
            self.db.query(BetLog).filter(BetLog.status == "PENDING").all()
        )

        total_resolved = len(resolved_bets)
        total_staked = sum(b.stake for b in resolved_bets)
        total_pl = sum(b.profit_loss for b in resolved_bets)
        won_bets = sum(1 for b in resolved_bets if b.status == "WON")

        yield_pct = (total_pl / total_staked * 100.0) if total_staked > 0 else 0.0
        win_rate = (won_bets / total_resolved * 100.0) if total_resolved > 0 else 0.0

        return {
            "total_bets": total_resolved + len(pending_bets),
            "pending_bets": len(pending_bets),
            "resolved_bets": total_resolved,
            "total_staked": round(total_staked, 2),
            "total_profit_loss": round(total_pl, 2),
            "yield_pct": round(yield_pct, 2),
            "win_rate": round(win_rate, 2),
        }

    def get_history_dataframe(self) -> pd.DataFrame:
        bets = self.db.query(BetLog).order_by(BetLog.placed_at.desc()).all()
        if not bets:
            return pd.DataFrame()

        data = []
        for b in bets:
            # Cálculo de CLV si existe cuota de cierre
            clv_str = "-"
            if b.closing_odds and b.closing_odds > 0:
                clv_val = (b.odds / b.closing_odds - 1.0) * 100.0
                clv_str = f"{clv_val:+.2f}%"

            data.append({
                "ID": b.id,
                "Fecha": b.placed_at.strftime("%Y-%m-%d %H:%M"),
                "Liga": b.league,
                "Partido": b.match_name,
                "Mercado": b.market,
                "Cuota Tomada": b.odds,
                "Cuota Cierre": b.closing_odds if b.closing_odds else "-",
                "CLV": clv_str,
                "P. Modelo": f"{b.p_model * 100:.1f}%",
                "Edge": f"{b.edge * 100:+.2f}%",
                "Stake ($)": b.stake,
                "Estado": b.status,
                "P&L ($)": b.profit_loss,
            })
        return pd.DataFrame(data)
