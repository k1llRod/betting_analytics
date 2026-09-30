from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from src.models import Match, MarketOdds

class IngestionService:
    def __init__(self, db_session: Session):
        self.db = db_session

    def clean_team_name(self, name: str) -> str:
        """Normaliza el nombre del equipo para evitar inconsistencias."""
        return name.strip().upper()

    def ingest_match_with_odds(self, match_payload: Dict[str, Any]) -> Match:
        """
        Inserta un partido y sus cuotas asociadas garantizando consistencia.
        """
        home_team = self.clean_team_name(match_payload["home_team"])
        away_team = self.clean_team_name(match_payload["away_team"])

        # 1. Crear la entidad Match
        match = Match(
            match_date=match_payload["date"],
            league=match_payload["league"],
            home_team=home_team,
            away_team=away_team,
            status=match_payload.get("status", "SCHEDULED")
        )
        self.db.add(match)
        self.db.flush()  # Asigna un ID autoincremental a 'match' sin cerrar la transacción

        # 2. Asociar cuotas iniciales si están presentes
        for odd_item in match_payload.get("odds", []):
            odd = MarketOdds(
                match_id=match.id,
                market_type=odd_item["market_type"],
                selection=odd_item["selection"],
                odds=odd_item["odds"],
                bookmaker=odd_item["bookmaker"],
                recorded_at=datetime.utcnow()
            )
            self.db.add(odd)

        self.db.commit()
        return match