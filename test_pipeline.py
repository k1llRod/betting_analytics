# test_pipeline.py
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.controllers import SimulationController
from src.models.database import Base, SessionLocal, engine
from src.services.historical_football_loader import HistoricalFootballLoader
from src.services.poisson_model import PoissonPredictor


def run_test():
    # 1. Asegurar que las tablas existan en PostgreSQL
    print("Creando tablas en la base de datos si no existen...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # 2. Descargar e ingestar una temporada completa de La Liga (España 2023-2024)
        print("\n--- PASO 1: Ingesta Histórica (Football-Data.co.uk) ---")
        loader = HistoricalFootballLoader(db)
        inserted = loader.ingest_season("LA_LIGA", season="2324")
        print(f"-> {inserted} partidos guardados con éxito en la base de datos.")

        # 3. Entrenar el modelo de Poisson con los partidos guardados
        print("\n--- PASO 2: Proyección Estadística (Poisson Predictor) ---")
        predictor = PoissonPredictor(db)

        # Probamos con un enfrentamiento clásico
        home = "REAL MADRID"
        away = "BARCELONA"
        probs = predictor.predict_match("LA_LIGA", home_team=home, away_team=away)

        print(f"Partido analizado: {home} vs {away}")
        print(f"  * Gana Local ({home}):        {probs.home_win * 100:.1f}%")
        print(f"  * Empate:                      {probs.draw * 100:.1f}%")
        print(f"  * Gana Visitante ({away}):     {probs.away_win * 100:.1f}%")
        print(f"  * Más de 2.5 Goles:            {probs.over_2_5_goals * 100:.1f}%")
        print(f"  * Ambos Marcan (Sí):           {probs.btts_yes * 100:.1f}%")
        print(f"  * Más de 9.5 Córners:          {probs.over_9_5_corners * 100:.1f}%")

        # 4. Evaluación de Valor (+EV) con cuotas hipotéticas de la casa
        print("\n--- PASO 3: Detección de Valor (+EV) ---")
        controller = SimulationController(min_edge=0.05)  # 5% de ventaja mínima

        # Evaluamos 'Más de 2.5 Goles' si la casa ofrece cuota 1.95
        eval_goles = controller.evaluate_market(
            market_name=f"{home} vs {away} - Más de 2.5 Goles",
            p_model=probs.over_2_5_goals,
            odds=1.95,
            stake=10.0,
        )

        print(f"Mercado: {eval_goles['market']}")
        print(f"  * Prob. Modelo:       {eval_goles['p_model'] * 100:.1f}%")
        print(f"  * Cuota Casa:         {eval_goles['odds']:.2f}")
        print(f"  * Prob. Implícita:    {eval_goles['p_implied'] * 100:.1f}%")
        print(f"  * Ventaja (Edge):     {eval_goles['edge'] * 100:+.2f}%")
        print(f"  * Valor Esperado EV:  ${eval_goles['ev']:+.2f}")
        print(f"  * ¿Es Oportunidad?:   {eval_goles['is_value']}")

    except Exception as e:
        print(f"\n[ERROR] Ocurrió un fallo en la prueba: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    run_test()
