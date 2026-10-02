from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests
from sqlalchemy.orm import Session
from src.models.database import SessionLocal
from src.models.entities import MarketOdds, Match


@dataclass
class ScrapedMarket:
    event_name: str
    home_team: str
    away_team: str
    start_date: datetime
    market_type: str  # '1X2', 'OVER_UNDER'
    line: Optional[float]
    selection: str
    price: float


class MiCasinoScraper:
    """Extrae y normaliza cuotas reales de MiCasino.com consumiendo la API de Altenar."""

    ENDPOINT = "https://sb2frontend-altenar2.biahosted.com/api/widget/GetEvents"

    def __init__(self, db: Optional[Session] = None):
        self.db = db

    def fetch_championship_events(
            self,
            champ_id: int = 16808,
            auth_token: Optional[str] = None,
    ) -> List[ScrapedMarket]:
        """Descarga eventos y cuotas para un torneo específico (por defecto: Champions League 16808)."""
        params = {
            "culture": "es-ES",
            "timezoneOffset": "240",
            "integration": "micasino.bo",
            "deviceType": "1",
            "numFormat": "es-ES",
            "countryCode": "BO",
            "eventCount": "0",
            "sportId": "0",
            "champIds": str(champ_id),
        }

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            ),
            "Origin": "https://micasino.com",
            "Referer": "https://micasino.com/",
            "Accept": "application/json",
        }
        if auth_token:
            headers["Authorization"] = auth_token

        response = requests.get(
            self.ENDPOINT, params=params, headers=headers, timeout=15
        )
        if response.status_code != 200:
            raise ValueError(
                f"Error consultando Altenar API: HTTP {response.status_code}"
            )

        data = response.json()
        return self._parse_altenar_json(data)

    def _parse_altenar_json(self, data: Dict[str, Any]) -> List[ScrapedMarket]:
        # 1. Mapeo rápido de cuotas por ID
        odds_lookup = {item["id"]: item for item in data.get("odds", [])}

        # 2. Mapeo de mercados por ID
        markets_lookup = {item["id"]: item for item in data.get("markets", [])}

        scraped: List[ScrapedMarket] = []

        for ev in data.get("events", []):
            raw_name = ev.get("name", "")
            parts = [p.strip() for p in raw_name.split("vs.")]
            if len(parts) != 2:
                continue

            home_team = parts[0].upper()
            away_team = parts[1].upper()
            try:
                start_date = datetime.fromisoformat(
                    ev.get("startDate", "").replace("Z", "+00:00")
                )
            except Exception:
                start_date = datetime.now()

            for market_id in ev.get("marketIds", []):
                market = markets_lookup.get(market_id)
                if not market:
                    continue

                type_id = market.get("typeId")
                odd_ids = market.get("oddIds", [])

                # Mercado 1X2 (typeId == 1)
                if type_id == 1 and len(odd_ids) >= 3:
                    o_home = odds_lookup.get(odd_ids[0])
                    o_draw = odds_lookup.get(odd_ids[1])
                    o_away = odds_lookup.get(odd_ids[2])

                    if o_home and o_draw and o_away:
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="1X2",
                                line=None,
                                selection="HOME",
                                price=float(o_home["price"]),
                            )
                        )
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="1X2",
                                line=None,
                                selection="DRAW",
                                price=float(o_draw["price"]),
                            )
                        )
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="1X2",
                                line=None,
                                selection="AWAY",
                                price=float(o_away["price"]),
                            )
                        )

                # Mercado Total de Goles (typeId == 18)
                elif type_id == 18 and len(odd_ids) >= 2:
                    try:
                        line_val = float(str(market.get("sv", "2.5")).replace(",", "."))
                    except ValueError:
                        line_val = 2.5

                    o_over = odds_lookup.get(odd_ids[0])
                    o_under = odds_lookup.get(odd_ids[1])

                    if o_over and o_under:
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="TOTAL_GOALS",
                                line=line_val,
                                selection="OVER",
                                price=float(o_over["price"]),
                            )
                        )
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="TOTAL_GOALS",
                                line=line_val,
                                selection="UNDER",
                                price=float(o_under["price"]),
                            )
                        )

                # Mercado Ambos Equipos Marcan (BTTS, typeId == 29)
                elif type_id == 29 and len(odd_ids) >= 2:
                    o_yes = odds_lookup.get(odd_ids[0])
                    o_no = odds_lookup.get(odd_ids[1])

                    if o_yes and o_no:
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="BTTS",
                                line=None,
                                selection="YES",
                                price=float(o_yes["price"]),
                            )
                        )
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="BTTS",
                                line=None,
                                selection="NO",
                                price=float(o_no["price"]),
                            )
                        )

                # Mercado Apuesta Sin Empate / Draw No Bet (typeId == 11, AH 0.0)
                elif type_id == 11 and len(odd_ids) >= 2:
                    o_dnb_home = odds_lookup.get(odd_ids[0])
                    o_dnb_away = odds_lookup.get(odd_ids[1])

                    if o_dnb_home and o_dnb_away:
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="DRAW_NO_BET",
                                line=0.0,
                                selection="HOME",
                                price=float(o_dnb_home["price"]),
                            )
                        )
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="DRAW_NO_BET",
                                line=0.0,
                                selection="AWAY",
                                price=float(o_dnb_away["price"]),
                            )
                        )

                # Mercado Doble Oportunidad (typeId == 10, AH +0.5)
                elif type_id == 10 and len(odd_ids) >= 3:
                    o_1x = odds_lookup.get(odd_ids[0])
                    o_12 = odds_lookup.get(odd_ids[1])
                    o_x2 = odds_lookup.get(odd_ids[2])

                    if o_1x and o_12 and o_x2:
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="DOUBLE_CHANCE",
                                line=0.5,
                                selection="1X",
                                price=float(o_1x["price"]),
                            )
                        )
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="DOUBLE_CHANCE",
                                line=None,
                                selection="12",
                                price=float(o_12["price"]),
                            )
                        )
                        scraped.append(
                            ScrapedMarket(
                                event_name=raw_name,
                                home_team=home_team,
                                away_team=away_team,
                                start_date=start_date,
                                market_type="DOUBLE_CHANCE",
                                line=0.5,
                                selection="X2",
                                price=float(o_x2["price"]),
                            )
                        )

        return scraped


if __name__ == "__main__":
    scraper = MiCasinoScraper()
    print("Consultando API de Altenar / MiCasino...")
    results = scraper.fetch_championship_events(champ_id=16808)
    print(f"Total de líneas de mercado extraídas: {len(results)}\n")

    # Muestra de los primeros mercados encontrados
    for item in results[:8]:
        line_str = f" ({item.line})" if item.line else ""
        print(
            f"[{item.event_name}] {item.market_type}{line_str} | Selección:"
            f" {item.selection} -> Cuota: {item.price:.2f}"
        )
