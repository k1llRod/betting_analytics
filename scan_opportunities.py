# scan_opportunities.py
from datetime import datetime
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
from src.controllers import SimulationController
from src.models.database import SessionLocal
from src.models.entities import Match
from src.services.micasino_scraper import MiCasinoScraper
from src.services.poisson_model import PoissonPredictor
from src.utils import clean_str, match_team_name


def run_scanner(
    champ_id: int = 2941,
    league_key: str = "LA_LIGA",
    min_edge: float = 0.04,
    xi: float = 0.0035,
    xg_weight: float = 0.0,
):
    print("=" * 75)
    print(
        f"🔎 ESCÁNER MULTIMERCADO (+EV) - {league_key} [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
    )
    print(f"Parámetros: Edge >= {min_edge * 100:.1f}% | Decaimiento ξ={xi} | Peso xG={xg_weight}\n")

    db = SessionLocal()
    try:
        db_teams = [
            t[0]
            for t in db.query(Match.home_team)
            .filter(Match.league == league_key)
            .distinct()
            .all()
        ]

        scraper = MiCasinoScraper()
        markets = scraper.fetch_championship_events(champ_id=champ_id)
        print(f"-> Líneas de cuotas descargadas de Altenar/MiCasino: {len(markets)}\n")

        predictor = PoissonPredictor(db, xi=xi, xg_weight=xg_weight, use_cache=True)
        controller = SimulationController(min_edge=min_edge)

        events = {}
        for m in markets:
            events.setdefault(m.event_name, []).append(m)

        opportunities = []

        for event_name, event_markets in events.items():
            first = event_markets[0]
            home_matched = match_team_name(first.home_team, db_teams)
            away_matched = match_team_name(first.away_team, db_teams)

            if not home_matched or not away_matched:
                continue

            try:
                probs = predictor.predict_match(
                    league=league_key,
                    home_team=home_matched,
                    away_team=away_matched,
                )
            except Exception:
                continue

            for m in event_markets:
                p_model = None
                market_label = ""

                # 1. 1X2
                if m.market_type == "1X2":
                    if m.selection == "HOME":
                        p_model = probs.home_win
                        market_label = f"Gana Local ({home_matched})"
                    elif m.selection == "DRAW":
                        p_model = probs.draw
                        market_label = "Empate (X)"
                    elif m.selection == "AWAY":
                        p_model = probs.away_win
                        market_label = f"Gana Visitante ({away_matched})"

                # 2. Total de Goles
                elif m.market_type == "TOTAL_GOALS":
                    if m.line == 2.5:
                        p_model = probs.over_2_5_goals if m.selection == "OVER" else probs.under_2_5_goals
                        market_label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 2.5 Goles"
                    elif m.line == 1.5:
                        p_model = probs.over_1_5_goals if m.selection == "OVER" else probs.under_1_5_goals
                        market_label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 1.5 Goles"
                    elif m.line == 3.5:
                        p_model = probs.over_3_5_goals if m.selection == "OVER" else probs.under_3_5_goals
                        market_label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 3.5 Goles"

                # 3. Ambos Equipos Marcan (BTTS)
                elif m.market_type == "BTTS":
                    if m.selection == "YES":
                        p_model = probs.btts_yes
                        market_label = "Ambos Marcan (Sí)"
                    elif m.selection == "NO":
                        p_model = probs.btts_no
                        market_label = "Ambos Marcan (No)"

                # 4. Apuesta Sin Empate (AH 0.0)
                elif m.market_type == "DRAW_NO_BET":
                    if m.selection == "HOME":
                        p_model = probs.draw_no_bet["HOME"]
                        market_label = f"Sin Empate ({home_matched})"
                    elif m.selection == "AWAY":
                        p_model = probs.draw_no_bet["AWAY"]
                        market_label = f"Sin Empate ({away_matched})"

                # 5. Doble Oportunidad
                elif m.market_type == "DOUBLE_CHANCE":
                    if m.selection in probs.double_chance:
                        p_model = probs.double_chance[m.selection]
                        market_label = f"Doble Oportunidad ({m.selection})"

                if p_model is None:
                    continue

                eval_res = controller.evaluate_market(
                    market_name=market_label,
                    p_model=p_model,
                    odds=m.price,
                    stake=10.0,
                    custom_min_edge=min_edge,
                )

                if eval_res["is_value"] and eval_res["p_model"] >= 0.10:
                    opportunities.append({
                        "Partido": f"{home_matched} vs {away_matched}",
                        "Mercado": eval_res["market"],
                        "Cuota": f"{eval_res['odds']:.2f}",
                        "P. Modelo": f"{eval_res['p_model'] * 100:.1f}%",
                        "P. Implícita": f"{eval_res['p_implied'] * 100:.1f}%",
                        "Edge": f"{eval_res['edge'] * 100:+.2f}%",
                        "EV ($10)": f"${eval_res['ev']:+.2f}",
                    })

        print("\n" + "=" * 75)
        if opportunities:
            df_ops = pd.DataFrame(opportunities)
            print(
                f"🎯 ¡SE ENCONTRARON {len(opportunities)} OPORTUNIDADES CON VALOR (+EV)!\n"
            )
            print(df_ops.to_string(index=False))
        else:
            print("No se encontraron selecciones con valor que superen el umbral.")
        print("=" * 75)

    finally:
        db.close()


if __name__ == "__main__":
    run_scanner(champ_id=2941, league_key="LA_LIGA", min_edge=0.04)
