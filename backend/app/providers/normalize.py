"""Normalización de nombres de equipos e identificadores de competiciones."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

from app.core.config import DATA_DIR

_STOPWORDS = {"fc", "cf", "cd", "club", "afc", "sc", "ac", "ssc", "as", "the", "de", "futbol", "football", "calcio"}

# Competiciones canónicas: clave interna -> metadatos y códigos por proveedor.
COMPETITIONS: dict[str, dict] = {
    "eng_premier_league": {
        "api_football": 39, "openfootball": "en.1", "tz": "Europe/London",
        "name": "Premier League", "country": "Inglaterra",
        "fdcouk": "E0", "fdorg": "PL", "odds_api": "soccer_epl",
    },
    "eng_championship": {
        "api_football": 40, "openfootball": "en.2", "tz": "Europe/London",
        "name": "Championship", "country": "Inglaterra",
        "fdcouk": "E1", "fdorg": "ELC", "odds_api": "soccer_efl_champ",
    },
    "esp_la_liga": {
        "api_football": 140, "openfootball": "es.1", "tz": "Europe/Madrid",
        "name": "La Liga", "country": "España",
        "fdcouk": "SP1", "fdorg": "PD", "odds_api": "soccer_spain_la_liga",
    },
    "ita_serie_a": {
        "api_football": 135, "openfootball": "it.1", "tz": "Europe/Rome",
        "name": "Serie A", "country": "Italia",
        "fdcouk": "I1", "fdorg": "SA", "odds_api": "soccer_italy_serie_a",
    },
    "ger_bundesliga": {
        "api_football": 78, "openfootball": "de.1", "tz": "Europe/Berlin",
        "name": "Bundesliga", "country": "Alemania",
        "fdcouk": "D1", "fdorg": "BL1", "odds_api": "soccer_germany_bundesliga",
    },
    "fra_ligue_1": {
        "api_football": 61, "openfootball": "fr.1", "tz": "Europe/Paris",
        "name": "Ligue 1", "country": "Francia",
        "fdcouk": "F1", "fdorg": "FL1", "odds_api": "soccer_france_ligue_one",
    },
    "ned_eredivisie": {"api_football": 88, "name": "Eredivisie", "country": "Países Bajos", "fdcouk": "N1", "fdorg": "DED"},
    "por_primeira_liga": {"api_football": 94, "name": "Primeira Liga", "country": "Portugal", "fdcouk": "P1", "fdorg": "PPL"},
    "uefa_champions_league": {
        "api_football": 2, "name": "UEFA Champions League", "country": "Europa",
        "fdorg": "CL", "odds_api": "soccer_uefa_champs_league",
    },
    "mex_liga_mx": {
        "api_football": 262, "name": "Liga MX", "country": "México",
        "fdcouk_new": ("Mexico", "Liga MX"), "odds_api": "soccer_mexico_ligamx",
    },
    "usa_mls": {
        "api_football": 253, "name": "MLS", "country": "Estados Unidos",
        "fdcouk_new": ("USA", "MLS"), "odds_api": "soccer_usa_mls",
    },
}


def competition_by_code(provider_field: str, code: str | tuple) -> str | None:
    for key, meta in COMPETITIONS.items():
        if meta.get(provider_field) == code:
            return key
    return None


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def slug(name: str) -> str:
    text = strip_accents(name).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t not in _STOPWORDS]
    return "_".join(tokens) or re.sub(r"\s+", "_", text.strip())


@lru_cache
def _aliases() -> dict[str, str]:
    path = DATA_DIR / "team_aliases.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for canonical, aliases in raw.items():
        if canonical.startswith("_"):
            continue
        out[slug(canonical)] = canonical
        for a in aliases:
            out[slug(a)] = canonical
    return out


@lru_cache
def _aliases_by_canonical() -> dict[str, list[str]]:
    path = DATA_DIR / "team_aliases.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: list(v) for k, v in raw.items() if not k.startswith("_")}


def known_aliases(canonical: str) -> list[str]:
    """Alias registrados en data/team_aliases.json para un nombre canónico."""
    return _aliases_by_canonical().get(canonical, [])


def canonical_team_name(name: str) -> str:
    """Devuelve el nombre canónico si hay alias registrado; si no, el nombre limpio."""
    cleaned = " ".join(name.strip().split())
    return _aliases().get(slug(cleaned), cleaned)


def team_key(name: str) -> str:
    return slug(canonical_team_name(name))
