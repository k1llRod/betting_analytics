# run_bot_scanner.py
import sys
import time
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.models.database import SessionLocal
from src.models.entities import Match
from src.services.micasino_scraper import MiCasinoScraper
from src.services.poisson_model import PoissonPredictor
from src.controllers import SimulationController
from src.services.telegram_service import TelegramAlertService
from src.views.streamlit_view import match_team_name, calculate_quarter_kelly, LEAGUE_CONFIG

# Memoria de alertas enviadas para no repetir (clave: match + market + selección)
SENT_ALERTS = set()


def scan_all_leagues_and_notify(min_edge: float = 0.04, max_odds: float = 12.0, bankroll: float = 500.0):
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando ronda de escaneo multiligas...")

    telegram = TelegramAlertService()
    if not telegram.is_configured():
        print("  ⚠️ Telegram no está configurado en .env (las alertas se imprimirán en consola).")

    db = SessionLocal()
    scraper = MiCasinoScraper()
    predictor = PoissonPredictor(db)
    controller = SimulationController(min_edge=min_edge)

    try:
        for league_label, cfg in LEAGUE_CONFIG.items():
            champ_id = cfg["champ_id"]
            db_key = cfg["db_key"]

            db_teams = [
                t[0] for t in db.query(Match.home_team)
                .filter(Match.league == db_key)
                .distinct().all()
            ]

            if not db_teams:
                continue

            try:
                markets = scraper.fetch_championship_events(champ_id=champ_id)
            except Exception as e:
                print(f"  Error consultando {league_label}: {e}")
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
                    probs = predictor.predict_match(league=db_key, home_team=h_team, away_team=a_team)
                except Exception:
                    continue

                for m in event_markets:
                    if m.price > max_odds:
                        continue

                    p_model = None
                    label = ""

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
                    elif m.market_type == "TOTAL_GOALS" and m.line == 2.5:
                        if m.selection == "OVER":
                            p_model = probs.over_2_5_goals
                            label = "Más de 2.5 Goles"
                        elif m.selection == "UNDER":
                            p_model = probs.under_2_5_goals
                            label = "Menos de 2.5 Goles"

                    if p_model is None:
                        continue

                    res = controller.evaluate_market(
                        market_name=label,
                        p_model=p_model,
                        odds=m.price,
                        stake=10.0,
                        custom_min_edge=min_edge
                    )

                    if res["is_value"] and res["p_model"] >= 0.08:
                        alert_key = f"{h_team}_{a_team}_{label}_{m.price}"
                        if alert_key in SENT_ALERTS:
                            continue

                        kelly_stake = calculate_quarter_kelly(
                            p_model=res["p_model"],
                            odds=res["odds"],
                            bankroll=bankroll,
                            fraction=0.25
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
                            "kelly_stake": kelly_stake
                        }

                        print(
                            f"  🎯 ¡ALERTA! {opp_data['match']} | {label} @ {res['odds']:.2f} (Edge: +{res['edge'] * 100:.2f}%)")
                        if telegram.is_configured():
                            telegram.send_opportunity_alert(opp_data)

                        SENT_ALERTS.add(alert_key)
    finally:
        db.close()


if __name__ == "__main__":
    # Intervalo de escaneo en segundos (ej. 900s = 15 minutos)
    SCAN_INTERVAL_SECONDS = 900
    print("🤖 Iniciando Bot Daemon de Monitoreo Cuantitativo...")
    print(f"Frecuencia de escaneo: cada {SCAN_INTERVAL_SECONDS // 60} minutos.")

    while True:
        try:
            scan_all_leagues_and_notify(min_edge=0.04, max_odds=12.0, bankroll=500.0)
        except Exception as e:
            print(f"Error inesperado en ciclo de escaneo: {e}")

        print(f"Esperando {SCAN_INTERVAL_SECONDS // 60} minutos hasta la próxima verificación...")
        time.sleep(SCAN_INTERVAL_SECONDS)