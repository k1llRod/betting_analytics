import io
import pandas as pd
import requests
from src.models.database import SessionLocal
from src.models.entities import Match, MatchStats


def ingest_football_data_season(league_code: str, season: str):
    """Descarga e ingesta una temporada completa desde Football-Data.co.uk.

    Ejemplo: league_code='E0' (Premier League), season='2324' (2023-2024)
    """
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{league_code}.csv"
    response = requests.get(url)
    if response.status_code != 200:
        print(f"Error descargando datos de {url}")
        return

    df = pd.read_csv(io.StringIO(response.content.decode("latin-1")))

    db = SessionLocal()
    try:
        for _, row in df.iterrows():
            if pd.isna(row.get("HomeTeam")) or pd.isna(row.get("FTHG")):
                continue

            match = Match(
                match_date=pd.to_datetime(row["Date"], dayfirst=True),
                league=league_code,
                home_team=str(row["HomeTeam"]).strip().upper(),
                away_team=str(row["AwayTeam"]).strip().upper(),
                home_score=int(row["FTHG"]),
                away_score=int(row["FTAG"]),
                status="FINISHED",
            )
            db.add(match)
            db.flush()

            # Métricas avanzadas disponibles en el dataset
            stats = MatchStats(
                match_id=match.id,
                home_shots_target=int(row.get("HST", 0))
                if not pd.isna(row.get("HST"))
                else None,
                away_shots_target=int(row.get("AST", 0))
                if not pd.isna(row.get("AST"))
                else None,
                home_corners=int(row.get("HC", 0))
                if not pd.isna(row.get("HC"))
                else None,
                away_corners=int(row.get("AC", 0))
                if not pd.isna(row.get("AC"))
                else None,
            )
            db.add(stats)

        db.commit()
        print(
            f"Temporada {season} de {league_code} ingestada con éxito ({len(df)}"
            " partidos)."
        )
    except Exception as e:
        db.rollback()
        print(f"Fallo durante la ingesta: {e}")
    finally:
        db.close()
