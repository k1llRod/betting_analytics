# src/utils.py
from typing import Any, Dict, List, Optional
import unicodedata

# --- CATÁLOGO MULTILIGAS ---
LEAGUE_CONFIG: Dict[str, Dict[str, Any]] = {
    "LaLiga (España)": {
        "champ_id": 2941,
        "db_key": "LA_LIGA",
    },
    "Premier League (Inglaterra)": {
        "champ_id": 2936,
        "db_key": "PREMIER_LEAGUE",
    },
    "División Profesional (Bolivia)": {
        "champ_id": 40282,
        "db_key": "BOLIVIA",
    },
    "Champions League": {
        "champ_id": 16808,
        "db_key": "LA_LIGA",  # Cruza equipos disponibles en base
    },
}


def clean_str(s: str) -> str:
    """Normaliza texto eliminando acentos y espacios superfluos."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ASCII", "ignore").decode("utf-8")
    return s.strip().upper()


def match_team_name(scraped_name: str, db_teams: List[str]) -> Optional[str]:
    """Empareja nombres de equipos de casas de apuestas con la base de datos."""
    s_norm = clean_str(scraped_name)
    db_norm_map = {clean_str(t): t for t in db_teams}

    if s_norm in db_norm_map:
        return db_norm_map[s_norm]

    # Diccionario unificado de alias (España, Inglaterra, Bolivia)
    aliases = {
        # España
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
        "RCD ESPANYOL BARCELONA": "ESPANYOL",
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
        # Inglaterra
        "MANCHESTER UNITED": "MAN UNITED",
        "MANCHESTER CITY": "MAN CITY",
        "NEWCASTLE UNITED": "NEWCASTLE",
        "TOTTENHAM HOTSPUR": "TOTTENHAM",
        "WOLVERHAMPTON WANDERERS": "WOLVES",
        "NOTTINGHAM FOREST": "NOTTM FOREST",
        "WEST HAM UNITED": "WEST HAM",
        "BRIGHTON & HOVE ALBION": "BRIGHTON",
        "LEICESTER CITY": "LEICESTER",
        "IPSWICH TOWN": "IPSWICH",
        # Bolivia
        "CLUB BOLIVAR": "BOLIVAR",
        "THE STRONGEST": "THE STRONGEST",
        "CLUB ALWAYS READY": "ALWAYS READY",
        "CD JORGE WILSTERMANN": "WILSTERMANN",
        "CLUB AURORA": "AURORA",
        "ORIENTE PETROLERO": "ORIENTE PETROLERO",
        "BLOOMING": "BLOOMING",
        "NACIONAL POTOSI": "NACIONAL POTOSI",
    }

    if s_norm in aliases:
        target = aliases[s_norm]
        for db_clean, db_orig in db_norm_map.items():
            if clean_str(target) == db_clean or target == db_orig:
                return db_orig

    for db_clean, db_orig in db_norm_map.items():
        if db_clean in s_norm or s_norm in db_clean:
            return db_orig

    return None


def calculate_quarter_kelly(
    p_model: float, odds: float, bankroll: float, fraction: float = 0.25
) -> float:
    """Calcula el tamaño de apuesta fraccional (Kelly 1/4 por defecto)."""
    b = odds - 1.0
    q = 1.0 - p_model
    f = (b * p_model - q) / b
    if f <= 0:
        return 0.0
    return round(bankroll * f * fraction, 2)
