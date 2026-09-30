# scan_opportunities.py
from datetime import datetime
from pathlib import Path
import sys
from typing import List, Optional
import unicodedata

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
from src.controllers import SimulationController
from src.models.database import SessionLocal
from src.models.entities import Match
from src.services.micasino_scraper import MiCasinoScraper
from src.services.poisson_model import PoissonPredictor


def clean_str(s: str) -> str:
    """Remueve tildes, caracteres especiales y normaliza a mayúsculas."""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("utf-8")
    return s.strip().upper()


def match_team_name(scraped_name: str, db_teams: List[str]) -> Optional[str]:
    """Empareja los nombres de la casa de apuestas con los nombres de la BD."""
    s_norm = clean_str(scraped_name)
    db_norm_map = {clean_str(t): t for t in db_teams}

    # Coincidencia directa
    if s_norm in db_norm_map:
        return db_norm_map[s_norm]

    # Diccionario de equivalencias comunes entre MiCasino y Football-Data
    aliases = {
        "FC BARCELONA": "BARCELONA",
        "REAL MADRID CF": "REAL MADRID",
        "ATLETICO DE MADRID": "ATH MADRID",
        "ATLETICO MADRID": "ATH MADRID",
        "ATHLETIC CLUB": "ATH BILBAO",
        "ATHLETIC BILBAO": "ATH BILBAO",
        "REAL BETIS": "BETIS",
        "REAL BETIS BALOMPIE": "BETIS",
        "CA OSASUNA": "OSASUNA",
        "RCD ESPANYOL": "ESPANYOL",
        "ESPANYOL": "ESPANYOL",
        "RCD ESPANYOL DE BARCELONA": "ESPANYOL",
        "RCD MALLORCA": "MALLORCA",
        "VALENCIA CF": "VALENCIA",
        "SEVILLA FC": "SEVILLA",
        "VILLARREAL CF": "VILLARREAL",
        "CELTA DE VIGO": "CELTA",
        "RC CELTA DE VIGO": "CELTA",
        "RAYO VALLECANO": "VALLECANO",
        "REAL SOCIEDAD": "SOCIEDAD",
        "GIRONA FC": "GIRONA",
        "GETAFE CF": "GETAFE",
        "UD LAS PALMAS": "LAS PALMAS",
        "CD ALAVES": "ALAVES",
        "DEPORTIVO ALAVES": "ALAVES",
        "CD LEGANES": "LEGANES",
        "REAL VALLADOLID": "VALLADOLID",
    }

    if s_norm in aliases:
        target = aliases[s_norm]
        for db_clean, db_orig in db_norm_map.items():
            if clean_str(target) == db_clean or target == db_orig:
                return db_orig

    # Búsqueda parcial por contención
    for db_clean, db_orig in db_norm_map.items():
        if db_clean in s_norm or s_norm in db_clean:
            return db_orig

    return None


def run_scanner(
        champ_id: int = 2941, league_key: str = "LA_LIGA", min_edge: float = 0.03
):
    print("=" * 75)
    print(
        f"🔎 ESCÁNER DE VALOR (+EV) EN VIVO - LALIGA [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
    )
    print(f"Margen mínimo de ventaja (Edge %): {min_edge * 100:.1f}%\n")

    db = SessionLocal()
    try:
        # Obtener lista de equipos únicos en la BD
        db_teams = [
            t[0]
            for t in db.query(Match.home_team)
            .filter(Match.league == league_key)
            .distinct()
            .all()
        ]

        scraper = MiCasinoScraper()
        markets = scraper.fetch_championship_events(champ_id=champ_id)
        print(f"-> Líneas de cuotas descargadas de MiCasino: {len(markets)}\n")

        predictor = PoissonPredictor(db)
        controller = SimulationController(min_edge=min_edge)

        # Agrupar por partido
        events = {}
        for m in markets:
            events.setdefault(m.event_name, []).append(m)

        opportunities = []

        for event_name, event_markets in events.items():
            first = event_markets[0]
            home_matched = match_team_name(first.home_team, db_teams)
            away_matched = match_team_name(first.away_team, db_teams)

            if not home_matched or not away_matched:
                print(
                    f" [SKIP] {first.home_team} vs {first.away_team} ->"
                    f" ('{home_matched}' vs '{away_matched}')"
                )
                continue

            print(f" [ANALIZANDO] {home_matched} vs {away_matched}...")

            try:
                probs = predictor.predict_match(
                    league=league_key,
                    home_team=home_matched,
                    away_team=away_matched,
                )
            except Exception as e:
                print(f"   Error calculando Poisson: {e}")
                continue

            for m in event_markets:
                p_model = None
                market_label = ""

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

                elif m.market_type == "TOTAL_GOALS" and m.line == 2.5:
                    if m.selection == "OVER":
                        p_model = probs.over_2_5_goals
                        market_label = "Más de 2.5 Goles"
                    elif m.selection == "UNDER":
                        p_model = probs.under_2_5_goals
                        market_label = "Menos de 2.5 Goles"

                if p_model is None:
                    continue

                eval_res = controller.evaluate_market(
                    market_name=market_label,
                    p_model=p_model,
                    odds=m.price,
                    stake=10.0,
                    custom_min_edge=min_edge,
                )

                if eval_res["is_value"]:
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
    run_scanner(champ_id=2941, league_key="LA_LIGA", min_edge=0.03)
