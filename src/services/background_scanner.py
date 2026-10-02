# src/services/background_scanner.py
from datetime import datetime
import os
import signal
import sys
import time
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    _HAS_APSCHEDULER = True
except ImportError:
    _HAS_APSCHEDULER = False

from src.controllers import SimulationController
from src.models.database import SessionLocal
from src.models.entities import Match
from src.services.closing_odds_service import ClosingOddsService
from src.services.micasino_scraper import MiCasinoScraper
from src.services.poisson_model import PoissonPredictor
from src.services.result_settlement_service import ResultSettlementService
from src.services.telegram_service import TelegramAlertService
from src.utils import LEAGUE_CONFIG, calculate_quarter_kelly, match_team_name


class BackgroundScannerDaemon:
    """
    Worker en Background desatendido (independiente de Streamlit):
    1. Escaneo periódico de valor (+EV) en Altenar / MiCasino cada 5-15 min.
    2. Alertas inteligentes a Telegram (Edge >= 4%, cuotas 1.60-3.50, confirmación de liquidez).
    3. Captura automática de cuotas de cierre (CLV) 5-15 min antes de cada partido.
    4. Liquidación desatendida de apuestas y computación automática de P&L.
    """

    def __init__(
        self,
        min_edge: float = 0.04,
        min_odds: float = 1.60,
        max_odds: float = 3.50,
        bankroll: float = 500.0,
        xi: float = 0.0035,
        xg_weight: float = 0.0,
    ):
        self.min_edge = float(os.getenv("MIN_EDGE", min_edge))
        self.min_odds = float(os.getenv("MIN_ODDS", min_odds))
        self.max_odds = float(os.getenv("MAX_ODDS", max_odds))
        self.bankroll = float(os.getenv("BANKROLL", bankroll))
        self.xi = float(os.getenv("XI_DECAY", xi))
        self.xg_weight = float(os.getenv("XG_WEIGHT", xg_weight))
        self.telegram = TelegramAlertService()
        self.scheduler = None
        self._running = False

    def scan_opportunities_job(self):
        """Tarea programada: Escaneo multiligas de valor esperado positivo."""
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔍 Ejecutando ronda de escaneo multiligas...")
        db = SessionLocal()
        scraper = MiCasinoScraper()
        predictor = PoissonPredictor(db, xi=self.xi, xg_weight=self.xg_weight, use_cache=True)
        controller = SimulationController(min_edge=self.min_edge)

        opportunities_found = 0
        try:
            for league_label, cfg in LEAGUE_CONFIG.items():
                champ_id = cfg["champ_id"]
                db_key = cfg["db_key"]

                db_teams = [
                    t[0]
                    for t in db.query(Match.home_team)
                    .filter(Match.league == db_key)
                    .distinct()
                    .all()
                ]

                if not db_teams:
                    continue

                try:
                    markets = scraper.fetch_championship_events(champ_id=champ_id)
                except Exception as e:
                    print(f"  [Error API] {league_label}: {e}")
                    continue

                events = {}
                for m in markets:
                    events.setdefault(m.event_name, []).append(m)

                for event_name, event_markets in events.items():
                    first = event_markets[0]
                    h_team = match_team_name(first.home_team, db_teams)
                    a_team = match_team_name(first.away_team, db_teams)

                    if not h_team or not a_team:
                        continue

                    try:
                        probs = predictor.predict_match(
                            league=db_key, home_team=h_team, away_team=a_team
                        )
                    except Exception:
                        continue

                    for m in event_markets:
                        # Filtro de cuota viable (ej. 1.60 a 3.50)
                        if not (self.min_odds <= m.price <= self.max_odds):
                            continue

                        p_model: Optional[float] = None
                        label: str = ""

                        # 1. 1X2
                        if m.market_type == "1X2":
                            if m.selection == "HOME":
                                p_model = probs.home_win
                                label = f"Gana Local ({h_team})"
                            elif m.selection == "DRAW":
                                p_model = probs.draw
                                label = "Empate (X)"
                            elif m.selection == "AWAY":
                                p_model = probs.away_win
                                label = f"Gana Visitante ({a_team})"

                        # 2. Total de Goles
                        elif m.market_type == "TOTAL_GOALS":
                            if m.line == 2.5:
                                p_model = probs.over_2_5_goals if m.selection == "OVER" else probs.under_2_5_goals
                                label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 2.5 Goles"
                            elif m.line == 1.5:
                                p_model = probs.over_1_5_goals if m.selection == "OVER" else probs.under_1_5_goals
                                label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 1.5 Goles"
                            elif m.line == 3.5:
                                p_model = probs.over_3_5_goals if m.selection == "OVER" else probs.under_3_5_goals
                                label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 3.5 Goles"

                        # 3. Ambos Equipos Marcan (BTTS)
                        elif m.market_type == "BTTS":
                            if m.selection == "YES":
                                p_model = probs.btts_yes
                                label = "Ambos Marcan (Sí)"
                            elif m.selection == "NO":
                                p_model = probs.btts_no
                                label = "Ambos Marcan (No)"

                        # 4. Apuesta Sin Empate (Draw No Bet / AH 0.0)
                        elif m.market_type == "DRAW_NO_BET":
                            if m.selection == "HOME":
                                p_model = probs.draw_no_bet["HOME"]
                                label = f"Sin Empate ({h_team})"
                            elif m.selection == "AWAY":
                                p_model = probs.draw_no_bet["AWAY"]
                                label = f"Sin Empate ({a_team})"

                        # 5. Doble Oportunidad (AH +0.5)
                        elif m.market_type == "DOUBLE_CHANCE":
                            if m.selection in probs.double_chance:
                                p_model = probs.double_chance[m.selection]
                                label = f"Doble Oportunidad ({m.selection})"

                        if p_model is None:
                            continue

                        res = controller.evaluate_market(
                            market_name=label,
                            p_model=p_model,
                            odds=m.price,
                            stake=10.0,
                            custom_min_edge=self.min_edge,
                        )

                        # Validación cuantitativa estricta
                        if res["is_value"] and res["p_model"] >= 0.15:
                            kelly_stake = calculate_quarter_kelly(
                                p_model=res["p_model"],
                                odds=res["odds"],
                                bankroll=self.bankroll,
                                fraction=0.25,
                            )
                            opp_data = {
                                "match": f"{h_team} vs {a_team}",
                                "league": league_label,
                                "market": label,
                                "odds": res["odds"],
                                "p_model": res["p_model"],
                                "p_implied": res["p_implied"],
                                "edge": res["edge"],
                                "ev": res["ev"],
                                "kelly_stake": kelly_stake,
                            }

                            opportunities_found += 1
                            print(
                                f"  🎯 [+EV] {opp_data['match']} | {label} @ {res['odds']:.2f} "
                                f"(Edge: +{res['edge'] * 100:.2f}% | Kelly: ${kelly_stake:.2f})"
                            )

                            if self.telegram.is_configured():
                                self.telegram.send_opportunity_alert(
                                    opp_data,
                                    min_edge=self.min_edge,
                                    min_odds=self.min_odds,
                                    max_odds=self.max_odds,
                                )

            print(f"  -> Total de oportunidades detectadas en esta ronda: {opportunities_found}")
        finally:
            db.close()

    def closing_odds_job(self):
        """Tarea programada: Captura de cuotas de cierre 5-15 min antes del pitido inicial."""
        db = SessionLocal()
        try:
            closing_svc = ClosingOddsService(db, telegram=self.telegram)
            audited = closing_svc.audit_pending_closing_odds(window_minutes=15)
            if audited > 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔒 Cuotas de cierre registradas: {audited}")
        except Exception as e:
            print(f"[Error ClosingOddsJob]: {e}")
        finally:
            db.close()

    def settlement_job(self):
        """Tarea programada: Liquidación desatendida de apuestas y cálculo de P&L."""
        db = SessionLocal()
        try:
            settle_svc = ResultSettlementService(db, telegram=self.telegram)
            settled = settle_svc.settle_pending_bets()
            if settled > 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💰 Apuestas liquidadas: {settled}")
        except Exception as e:
            print(f"[Error SettlementJob]: {e}")
        finally:
            db.close()

    def start(self, scan_minutes: int = 10, closing_minutes: int = 5, settlement_minutes: int = 30):
        """Inicia el planificador continuo."""
        self._running = True
        scan_minutes = int(os.getenv("SCAN_INTERVAL_MINUTES", scan_minutes))

        print("=" * 70)
        print("🚀 INICIANDO WORKER EN BACKGROUND - BETTING ANALYTICS")
        print(f"• Escaneo de oportunidades: cada {scan_minutes} min (Edge >= {self.min_edge * 100:.1f}%)")
        print(f"• Rango de cuotas viables: {self.min_odds:.2f} a {self.max_odds:.2f}")
        print(f"• Auditoría de Cuota de Cierre (CLV): cada {closing_minutes} min")
        print(f"• Liquidación automática de resultados: cada {settlement_minutes} min")
        print(f"• Modelo Dixon-Coles xi={self.xi} | xG weight={self.xg_weight}")
        print("=" * 70)

        # Ejecución inicial inmediata de las tareas
        try:
            self.scan_opportunities_job()
            self.closing_odds_job()
            self.settlement_job()
        except Exception as e:
            print(f"Aviso en ronda inicial: {e}")

        if _HAS_APSCHEDULER:
            self.scheduler = BlockingScheduler()
            self.scheduler.add_job(self.scan_opportunities_job, "interval", minutes=scan_minutes)
            self.scheduler.add_job(self.closing_odds_job, "interval", minutes=closing_minutes)
            self.scheduler.add_job(self.settlement_job, "interval", minutes=settlement_minutes)

            try:
                self.scheduler.start()
            except (KeyboardInterrupt, SystemExit):
                print("\n[Detención solicitada] Cerrando scheduler...")
        else:
            # Fallback a bucle de tiempo nativo
            print("APScheduler no detectado. Utilizando temporizador nativo.")
            last_scan = time.time()
            last_closing = time.time()
            last_settle = time.time()

            while self._running:
                now = time.time()
                if now - last_scan >= scan_minutes * 60:
                    self.scan_opportunities_job()
                    last_scan = now
                if now - last_closing >= closing_minutes * 60:
                    self.closing_odds_job()
                    last_closing = now
                if now - last_settle >= settlement_minutes * 60:
                    self.settlement_job()
                    last_settle = now
                time.sleep(10)


if __name__ == "__main__":
    daemon = BackgroundScannerDaemon()
    daemon.start()
