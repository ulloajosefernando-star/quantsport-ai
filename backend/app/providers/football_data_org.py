"""Adaptador football-data.org API v4 (https://docs.football-data.org/general/v4/).

Plan gratuito (verificado en football-data.org/pricing, oct-2026): 12 competiciones
(PL, PD, SA, BL1, FL1, CL, DED, PPL, ELC, BSA, WC, EC), 10 llamadas/minuto, marcadores con
retraso. NO incluye Liga MX, cuotas ni estadísticas (son add-ons de pago).
Autenticación: cabecera X-Auth-Token.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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
    ProviderNotConfigured,
)
from app.providers.http import ProviderHttpClient
from app.providers.normalize import COMPETITIONS, canonical_team_name

SOURCE = "football-data.org"
STATUS_MAP = {
    "FINISHED": "finished", "SCHEDULED": "scheduled", "TIMED": "scheduled", "POSTPONED": "postponed",
    "SUSPENDED": "postponed", "CANCELLED": "cancelled", "IN_PLAY": "live", "PAUSED": "live",
}


def parse_matches(data: dict, competition_key: str) -> list[NormalizedEvent]:
    meta = COMPETITIONS.get(competition_key, {})
    out = []
    for m in data.get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        ht = (m.get("score") or {}).get("halfTime") or {}
        status = STATUS_MAP.get(m.get("status", ""), "scheduled")
        season = m.get("season") or {}
        out.append(NormalizedEvent(
            sport="football", competition_key=competition_key, competition_name=meta.get("name", competition_key),
            country=meta.get("country", ""),
            kickoff_utc=datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")),
            home=canonical_team_name(m["homeTeam"]["name"]), away=canonical_team_name(m["awayTeam"]["name"]),
            season=f"{season.get('startDate', '')[:4]}" if season else "",
            status=status,
            home_score=ft.get("home") if status == "finished" else None,
            away_score=ft.get("away") if status == "finished" else None,
            home_score_ht=ht.get("home") if status == "finished" else None,
            away_score_ht=ht.get("away") if status == "finished" else None,
            source=SOURCE, source_event_id=str(m.get("id", "")),
        ))
    return out


class FootballDataOrgProvider(DataProvider):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.info = ProviderInfo(
            key="football_data_org",
            name="football-data.org",
            kind=ProviderKind.REAL,
            sports=["football"],
            capabilities=[Capability.COMPETITIONS, Capability.FIXTURES, Capability.RESULTS, Capability.HISTORICAL],
            configured=bool(settings.football_data_org_token),
            docs_url="https://docs.football-data.org/general/v4/index.html",
            cost_note="Gratis (12 competiciones). Cuotas/estadísticas: add-ons de 15 EUR/mes c/u.",
            coverage_note="Sin Liga MX en el plan gratuito.",
            license_note="Registro gratuito; consultar condiciones de uso en football-data.org.",
            quota_note=f"{settings.football_data_org_per_minute} llamadas/minuto",
        )
        self._client: ProviderHttpClient | None = None
        self._transport = transport

    @property
    def client(self) -> ProviderHttpClient:
        if not self.settings.football_data_org_token:
            raise ProviderNotConfigured("Configura FOOTBALL_DATA_ORG_TOKEN en .env")
        if self._client is None:
            self._client = ProviderHttpClient(
                SOURCE, "https://api.football-data.org", self.settings.cache_dir,
                headers={"X-Auth-Token": self.settings.football_data_org_token},
                per_minute=self.settings.football_data_org_per_minute, transport=self._transport,
            )
        return self._client

    def _code(self, competition_key: str) -> str:
        code = COMPETITIONS.get(competition_key, {}).get("fdorg")
        if not code:
            raise ProviderError(f"{competition_key} no está disponible en football-data.org")
        return code

    def fetch_results(self, competition_key: str, season: str | None = None) -> ProviderBatch:
        params = {"status": "FINISHED"}
        if season:
            params["season"] = season
        data, _ = self.client.get_json(f"/v4/competitions/{self._code(competition_key)}/matches", params,
                                       cache_ttl=6 * 3600, cost=1.0)
        return ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC), events=parse_matches(data, competition_key))

    def fetch_fixtures(self, competition_key: str, days_ahead: int = 7) -> ProviderBatch:
        today = date.today()
        params = {"dateFrom": today.isoformat(), "dateTo": (today + timedelta(days=days_ahead)).isoformat()}
        data, _ = self.client.get_json(f"/v4/competitions/{self._code(competition_key)}/matches", params,
                                       cache_ttl=3 * 3600, cost=1.0)
        return ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC), events=parse_matches(data, competition_key))
