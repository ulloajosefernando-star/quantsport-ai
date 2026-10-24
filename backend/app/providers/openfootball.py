"""Adaptador openfootball / football.json (https://github.com/openfootball/football.json).

* Datos de DOMINIO PÚBLICO ("Use as you please with no restrictions whatsoever"), sin clave.
* Archivos por temporada y liga: ``https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/en.1.json``
  con ``{"name": ..., "matches": [{"round", "date", "time", "team1", "team2", "score": {"ft": [a, b], "ht": [...]}}]}``.
  En algunos partidos ``score`` es directamente ``[a, b]``; en los no jugados no hay ``score``.
* La última temporada se actualiza a diario desde Football.TXT, que depende de contribuciones manuales:
  sirve como SEGUNDA fuente para cruzar resultados (el Ingestor avisa si dos fuentes no coinciden).
* Las horas no traen zona horaria; se interpretan en la hora local del país de la liga (aproximación documentada).
* No incluye Liga MX ni cuotas.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.core.config import Settings
from app.providers.base import (
    Capability,
    DataProvider,
    NormalizedEvent,
    ProviderBatch,
    ProviderError,
    ProviderInfo,
    ProviderKind,
)
from app.providers.http import ProviderHttpClient
from app.providers.normalize import COMPETITIONS, canonical_team_name

SOURCE = "openfootball"
BASE_URL = "https://raw.githubusercontent.com"
REPO_PATH = "/openfootball/football.json/master"


def season_label(season: str | None, today: date | None = None) -> str:
    """'2025', '2025-26' o '2025/26' -> '2025-26'. Sin temporada: la que está en curso (cambia en julio)."""
    if season:
        start = int(str(season)[:4])
    else:
        today = today or date.today()
        start = today.year if today.month >= 7 else today.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _score(raw) -> tuple[list | None, list | None]:
    if isinstance(raw, list):
        return raw, None
    if isinstance(raw, dict):
        return raw.get("ft"), raw.get("ht")
    return None, None


def _pair(v) -> tuple[int, int] | None:
    if isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and x >= 0 for x in v):
        return int(v[0]), int(v[1])
    return None


def parse_matches(data: dict, competition_key: str, season: str) -> list[NormalizedEvent]:
    meta = COMPETITIONS.get(competition_key, {})
    tz = ZoneInfo(meta.get("tz", "UTC"))
    out = []
    for m in data.get("matches", []):
        try:
            d = datetime.strptime(m["date"], "%Y-%m-%d")
            home, away = m["team1"], m["team2"]
        except (KeyError, TypeError, ValueError):
            continue
        t = str(m.get("time") or "")
        if len(t) == 5 and t[2] == ":":
            d = d.replace(hour=int(t[:2]), minute=int(t[3:]))
        else:
            d = d.replace(hour=12)  # hora desconocida: mediodía local
        kickoff = d.replace(tzinfo=tz).astimezone(UTC)
        ft, ht = _score(m.get("score"))
        ft, ht = _pair(ft), _pair(ht)
        out.append(NormalizedEvent(
            sport="football", competition_key=competition_key, competition_name=meta.get("name", competition_key),
            country=meta.get("country", ""), kickoff_utc=kickoff,
            home=canonical_team_name(home), away=canonical_team_name(away), season=season,
            status="finished" if ft else "scheduled",
            home_score=ft[0] if ft else None, away_score=ft[1] if ft else None,
            home_score_ht=ht[0] if (ft and ht) else None, away_score_ht=ht[1] if (ft and ht) else None,
            source=SOURCE,
        ))
    return out


class OpenFootballProvider(DataProvider):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.info = ProviderInfo(
            key="openfootball",
            name="openfootball (football.json)",
            kind=ProviderKind.DATASET,
            sports=["football"],
            capabilities=[Capability.FIXTURES, Capability.RESULTS, Capability.HISTORICAL],
            configured=True,
            docs_url="https://github.com/openfootball/football.json",
            cost_note="0 MXN, sin clave.",
            coverage_note="Inglaterra (1.ª y 2.ª), España, Alemania, Italia y Francia. Sin Liga MX ni cuotas. "
                          "Útil para cruzar resultados con otras fuentes.",
            license_note="Dominio público.",
            quota_note="Sin cuota; respuestas en caché 6 h.",
        )
        self._client: ProviderHttpClient | None = None
        self._transport = transport

    @property
    def client(self) -> ProviderHttpClient:
        if self._client is None:
            self._client = ProviderHttpClient(SOURCE, BASE_URL, self.settings.cache_dir, per_minute=30,
                                              transport=self._transport)
        return self._client

    def _load(self, competition_key: str, season: str | None) -> tuple[list[NormalizedEvent], str]:
        code = COMPETITIONS.get(competition_key, {}).get("openfootball")
        if not code:
            raise ProviderError(f"{competition_key} no está en openfootball")
        label = season_label(season)
        data, _ = self.client.get_json(f"{REPO_PATH}/{label}/{code}.json", cache_ttl=6 * 3600, cost=0.0)
        if not isinstance(data, dict) or "matches" not in data:
            raise ProviderError("openfootball: archivo con formato inesperado")
        return parse_matches(data, competition_key, label), label

    def fetch_results(self, competition_key: str, season: str | None = None) -> ProviderBatch:
        events, label = self._load(competition_key, season)
        batch = ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC),
                              events=[e for e in events if e.status == "finished"])
        if not batch.events:
            batch.warnings.append(f"openfootball no tiene resultados de {competition_key} {label}.")
        return batch

    def fetch_fixtures(self, competition_key: str, days_ahead: int = 7, season: str | None = None) -> ProviderBatch:
        events, _ = self._load(competition_key, season)
        now = datetime.now(UTC)
        horizon = now + timedelta(days=days_ahead)
        return ProviderBatch(source=SOURCE, fetched_at=now,
                             events=[e for e in events if e.status == "scheduled" and now < e.kickoff_utc <= horizon])
