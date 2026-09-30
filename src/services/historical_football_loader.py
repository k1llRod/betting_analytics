import io
import pandas as pd
import requests
from sqlalchemy.orm import Session
from src.models.database import SessionLocal
from src.models.entities import Match, MatchStats


class HistoricalFootballLoader:
  """Descarga y persiste datos históricos de fútbol con estadísticas avanzadas."""

  LEAGUE_CODES = {
      # Ligas Europeas principales
      "PREMIER_LEAGUE": "E0",
      "LA_LIGA": "SP1",
      "SERIE_A": "I1",
      "BUNDESLIGA": "D1",
      "LIGUE_1": "F1",
      # Ligas secundarias con alto valor en cuotas
      "PORTUGAL": "P1",
      "CHAMPIONSHIP": "E1",
      "SEGUNDA_SPAIN": "SP2",
  }

  def __init__(self, db: Session):
    self.db = db

  def fetch_season(self, league_key: str, season: str = "2324") -> pd.DataFrame:
    """Descarga el CSV oficial de Football-Data.co.uk.

    :param league_key: Clave en LEAGUE_CODES (ej: 'LA_LIGA', 'PREMIER_LEAGUE')
    :param season: Cadena de 4 dígitos (ej: '2324' para 2023-2024, '2223' para
    2022-2023)
    """
    code = self.LEAGUE_CODES.get(league_key, league_key)
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"

    response = requests.get(url, timeout=15)
    if response.status_code != 200:
      raise ValueError(f"No se pudo descargar {url} (HTTP {response.status_code})")

    content = io.StringIO(response.content.decode("latin-1"))
    return pd.read_csv(content)

  def ingest_season(self, league_key: str, season: str = "2324") -> int:
    df = self.fetch_season(league_key, season)
    inserted_count = 0

    try:
      for _, row in df.iterrows():
        # Descartar filas vacías o partidos no disputados
        if (
            pd.isna(row.get("HomeTeam"))
            or pd.isna(row.get("FTHG"))
            or pd.isna(row.get("Date"))
        ):
          continue

        # Normalización de fecha (soporta formatos comunes de la fuente)
        try:
          match_date = pd.to_datetime(row["Date"], format="%d/%m/%Y")
        except ValueError:
          match_date = pd.to_datetime(row["Date"], format="%d/%m/%y")

        match = Match(
            match_date=match_date,
            league=league_key,
            home_team=str(row["HomeTeam"]).strip().upper(),
            away_team=str(row["AwayTeam"]).strip().upper(),
            home_score=int(row["FTHG"]),
            away_score=int(row["FTAG"]),
            status="FINISHED",
        )
        self.db.add(match)
        self.db.flush()

        stats = MatchStats(
            match_id=match.id,
            home_shots_target=int(row["HST"])
            if not pd.isna(row.get("HST"))
            else None,
            away_shots_target=int(row["AST"])
            if not pd.isna(row.get("AST"))
            else None,
            home_shots_blocked=None,
            away_shots_blocked=None,
            home_corners=int(row["HC"]) if not pd.isna(row.get("HC")) else None,
            away_corners=int(row["AC"]) if not pd.isna(row.get("AC")) else None,
            home_possession=None,
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
    loader = HistoricalFootballLoader(db)
    print("Iniciando ingesta de La Liga (España)...")
    total = loader.ingest_season("LA_LIGA", "2324")
    print(f"Carga completa: {total} partidos ingresados.")
  finally:
    db.close()