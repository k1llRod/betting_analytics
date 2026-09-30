from datetime import datetime
from pathlib import Path
import pandas as pd
from sqlalchemy.orm import Session
from src.models.database import SessionLocal
from src.models.entities import Match, MatchStats

# Ruta a la carpeta data en la raíz del proyecto
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class SouthAmericaLoader:
    """Carga partidos históricos de ligas sudamericanas desde archivos locales."""

    def __init__(self, db: Session):
        self.db = db

    def ingest_local_csv(self, filename: str, league_label: str) -> int:
        file_path = DATA_DIR / filename
        if not file_path.exists():
            raise FileNotFoundError(f"No se encontró el archivo: {file_path}")

        df = pd.read_csv(file_path)
        inserted_count = 0

        try:
            for _, row in df.iterrows():
                # Validar columnas mínimas necesarias
                if pd.isna(row.get("HomeTeam")) or pd.isna(row.get("AwayTeam")):
                    continue

                try:
                    home_score = int(row["FTHG"])
                    away_score = int(row["FTAG"])
                except (ValueError, TypeError):
                    continue

                try:
                    match_date = pd.to_datetime(row["Date"])
                except Exception:
                    match_date = datetime.now()

                match = Match(
                    match_date=match_date,
                    league=league_label,
                    home_team=str(row["HomeTeam"]).strip().upper(),
                    away_team=str(row["AwayTeam"]).strip().upper(),
                    home_score=home_score,
                    away_score=away_score,
                    status="FINISHED",
                )
                self.db.add(match)
                self.db.flush()

                stats = MatchStats(
                    match_id=match.id,
                    home_shots_target=None,
                    away_shots_target=None,
                    home_corners=None,
                    away_corners=None,
                )
                self.db.add(stats)
                inserted_count += 1

            self.db.commit()
            return inserted_count
        except Exception as e:
            self.db.rollback()
            raise e


if __name__ == "__main__":
    db = SessionLocal()
    try:
        loader = SouthAmericaLoader(db)
        print("Iniciando carga de partidos locales...")
        total = loader.ingest_local_csv(
            "argentina_2023.csv", league_label="ARGENTINA"
        )
        print(f"Carga completa: {total} partidos ingresados a la base de datos.")
    finally:
        db.close()
