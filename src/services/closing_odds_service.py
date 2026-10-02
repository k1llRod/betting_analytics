# src/services/closing_odds_service.py
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from src.models.entities import BetLog
from src.services.micasino_scraper import MiCasinoScraper
from src.services.telegram_service import TelegramAlertService
from src.utils import LEAGUE_CONFIG, clean_str, match_team_name


class ClosingOddsService:
    """
    Servicio de Captura Automática de Cuotas de Cierre (Closing Odds) y Auditoría CLV.
    Consulta la API de Altenar en las proximidades del inicio de los partidos
    (ej: 5-15 min antes del pitido) para registrar la cuota final y calcular el CLV real.
    """

    def __init__(self, db: Session, telegram: Optional[TelegramAlertService] = None):
        self.db = db
        self.telegram = telegram or TelegramAlertService()
        self.scraper = MiCasinoScraper()

    def audit_pending_closing_odds(self, window_minutes: int = 15) -> int:
        """
        Escanea apuestas en estado PENDING que aún no tengan cuota de cierre registrada.
        Si el partido inicia en los próximos `window_minutes` (o si ya inició recientemente),
        captura la última cuota disponible en Altenar y la almacena.
        """
        pending_bets: List[BetLog] = (
            self.db.query(BetLog)
            .filter(BetLog.status == "PENDING", BetLog.closing_odds.is_(None))
            .all()
        )

        if not pending_bets:
            return 0

        updated_count = 0
        now = datetime.utcnow()

        # Agrupar apuestas pendientes por liga para minimizar consultas a la API
        by_league: Dict[str, List[BetLog]] = {}
        for bet in pending_bets:
            by_league.setdefault(bet.league, []).append(bet)

        for league_label, bets in by_league.items():
            cfg = LEAGUE_CONFIG.get(league_label)
            if not cfg:
                # Intentar buscar por coincidencia parcial en LEAGUE_CONFIG
                for k, v in LEAGUE_CONFIG.items():
                    if k.lower() in league_label.lower() or league_label.lower() in k.lower():
                        cfg = v
                        break

            if not cfg:
                continue

            champ_id = cfg["champ_id"]
            try:
                scraped_markets = self.scraper.fetch_championship_events(champ_id=champ_id)
            except Exception as e:
                print(f"[ClosingOddsService] Error consultando Altenar para {league_label}: {e}")
                continue

            if not scraped_markets:
                continue

            for bet in bets:
                # Si tenemos kickoff_time registrado, verificar ventana temporal
                if bet.kickoff_time:
                    time_to_kickoff = (bet.kickoff_time.replace(tzinfo=None) - now).total_seconds() / 60.0
                    # Si falta más tiempo que la ventana de captura, esperar
                    if time_to_kickoff > window_minutes:
                        continue

                # Extraer equipos del match_name (ej: "REAL MADRID vs BARCELONA")
                parts = [p.strip().upper() for p in bet.match_name.split("vs")]
                if len(parts) != 2:
                    continue
                bet_home, bet_away = parts[0], parts[1]

                # Buscar la selección correspondiente en las cuotas scrapeadas
                closing_odd_found: Optional[float] = None
                for m in scraped_markets:
                    m_home = clean_str(m.home_team)
                    m_away = clean_str(m.away_team)

                    # Coincidencia de encuentro
                    if clean_str(bet_home) not in m_home and m_home not in clean_str(bet_home):
                        continue
                    if clean_str(bet_away) not in m_away and m_away not in clean_str(bet_away):
                        continue

                    # Coincidencia de mercado
                    market_str = bet.market.upper()
                    if "GANA LOCAL" in market_str and m.market_type == "1X2" and m.selection == "HOME":
                        closing_odd_found = m.price
                    elif "EMPATE" in market_str and m.market_type == "1X2" and m.selection == "DRAW":
                        closing_odd_found = m.price
                    elif "GANA VISITANTE" in market_str and m.market_type == "1X2" and m.selection == "AWAY":
                        closing_odd_found = m.price
                    elif "MÁS DE 2.5" in market_str and m.market_type == "TOTAL_GOALS" and m.selection == "OVER":
                        closing_odd_found = m.price
                    elif "MENOS DE 2.5" in market_str and m.market_type == "TOTAL_GOALS" and m.selection == "UNDER":
                        closing_odd_found = m.price
                    elif "AMBOS MARCAN" in market_str and m.market_type == "BTTS":
                        if "SÍ" in market_str or "SI" in market_str or "YES" in market_str:
                            if m.selection == "YES":
                                closing_odd_found = m.price
                        else:
                            if m.selection == "NO":
                                closing_odd_found = m.price
                    elif "SIN EMPATE" in market_str and m.market_type == "DRAW_NO_BET":
                        if bet_home in market_str and m.selection == "HOME":
                            closing_odd_found = m.price
                        elif bet_away in market_str and m.selection == "AWAY":
                            closing_odd_found = m.price

                    if closing_odd_found:
                        break

                if closing_odd_found and closing_odd_found > 1.0:
                    bet.closing_odds = closing_odd_found
                    clv_pct = ((bet.odds / closing_odd_found) - 1.0) * 100.0
                    self.db.commit()
                    updated_count += 1

                    print(
                        f"[ClosingOddsService] Cuota de cierre registrada: Apuesta #{bet.id} ({bet.match_name} - {bet.market}) "
                        f"Tomada: @{bet.odds:.2f} | Cierre: @{closing_odd_found:.2f} | CLV: {clv_pct:+.2f}%"
                    )

                    if self.telegram.is_configured():
                        self.telegram.send_closing_odds_alert(
                            match_name=bet.match_name,
                            market=bet.market,
                            taken_odds=bet.odds,
                            closing_odds=closing_odd_found,
                            clv_pct=clv_pct,
                        )

        return updated_count
