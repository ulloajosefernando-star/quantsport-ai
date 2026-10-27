"""Adaptador balldontlie NBA API v1 (https://docs.balldontlie.io).

Plan Free (verificado en balldontlie.io, oct-2026): equipos, jugadores y partidos, 5 consultas por minuto,
0 USD. Las cuotas solo están en el plan GOAT (39.99 USD/mes, fuera de presupuesto): aquí solo se usan
partidos y marcadores. Autenticación: cabecera ``Authorization: <clave>``.

``/v1/games`` pagina con cursor (``meta.next_cursor``, hasta 100 por página) y filtra por temporada,
tipo de temporada y fechas. La temporada se nombra por el año en que empieza (2024 = 2024-25).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
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
    ProviderNotConfigured,
)
from app.providers.http import ProviderHttpClient
from app.providers.normalize import COMPETITIONS, canonical_team_name

SOURCE = "balldontlie"
COMPETITION = "nba"
# Sin pretemporada: solo partidos que cuentan (temporada regular, Copa NBA, play-in y playoffs)
SEASON_TYPES = ("regular", "ist", "playin", "playoffs")
ET = ZoneInfo("America/New_York")
MAX_PAGES = 40


def _kickoff(g: dict) -> datetime:
    raw = g.get("datetime")
    if raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    # Temporadas antiguas solo traen la fecha: se asume las 19:00 del Este (aprox.; solo afecta al orden del día)
    return datetime.combine(date.fromisoformat(g["date"][:10]), time(19, 0), ET).astimezone(UTC)


def _status(g: dict) -> str:
    state = (g.get("status_state") or "").lower()
    if state == "final" or str(g.get("status", "")).lower() == "final":
        return "finished"
    if state in ("in_progress", "halftime"):
        return "live"
    if state in ("postponed", "canceled", "cancelled"):
        return "postponed"
    return "scheduled"


def parse_games(data: list[dict]) -> list[NormalizedEvent]:
    meta = COMPETITIONS[COMPETITION]
    out = []
    for g in data:
        try:
            home, away = g["home_team"]["full_name"], g["visitor_team"]["full_name"]
            kickoff = _kickoff(g)
        except (KeyError, TypeError, ValueError):
            continue
        status = _status(g)
        hs, as_ = g.get("home_team_score"), g.get("visitor_team_score")
        if status == "finished" and not (isinstance(hs, int) and isinstance(as_, int) and hs + as_ > 0):
            continue  # marcador incompleto: no se guarda un resultado dudoso
        season = g.get("season")
        out.append(NormalizedEvent(
            sport="nba", competition_key=COMPETITION, competition_name=meta["name"], country=meta["country"],
            kickoff_utc=kickoff, home=canonical_team_name(home), away=canonical_team_name(away),
            season=f"{season}-{(season + 1) % 100:02d}" if isinstance(season, int) else "",
            status=status, home_score=hs if status == "finished" else None,
            away_score=as_ if status == "finished" else None, source=SOURCE, source_event_id=str(g.get("id", "")),
        ))
    return out


class BallDontLieProvider(DataProvider):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None,
                 sleep=None) -> None:
        self.settings = settings
        self.info = ProviderInfo(
            key="balldontlie", name="balldontlie (NBA)", kind=ProviderKind.REAL, sports=["nba"],
            capabilities=[Capability.RESULTS, Capability.FIXTURES, Capability.HISTORICAL],
            configured=bool(settings.balldontlie_api_key),
            docs_url="https://docs.balldontlie.io",
            cost_note="Plan Free: 0 MXN (5 consultas/min). Cuotas solo en GOAT, 39.99 USD/mes: no se usa.",
            coverage_note="Partidos y marcadores finales de la NBA, temporada por temporada.",
            license_note="Requiere clave personal gratuita; respetar sus términos de uso.",
            quota_note="5 consultas por minuto (el adaptador espera entre llamadas).",
        )
        self._transport = transport
        self._sleep = sleep
        self._client: ProviderHttpClient | None = None

    @property
    def client(self) -> ProviderHttpClient:
        if not self.settings.balldontlie_api_key:
            raise ProviderNotConfigured("Configura BALLDONTLIE_API_KEY en .env (clave gratuita de balldontlie.io)")
        if self._client is None:
            kw = {"sleep": self._sleep} if self._sleep else {}
            self._client = ProviderHttpClient(
                SOURCE, "https://api.balldontlie.io", self.settings.cache_dir,
                headers={"Authorization": self.settings.balldontlie_api_key}, per_minute=5,
                transport=self._transport, **kw,
            )
        return self._client

    def _games(self, params: dict, cache_ttl: float) -> list[dict]:
        rows: list[dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            q = {**params, "per_page": 100, **({"cursor": cursor} if cursor else {})}
            data, _ = self.client.get_json("/v1/games", q, cache_ttl=cache_ttl, cost=0.0)
            if not isinstance(data, dict) or "data" not in data:
                raise ProviderError("balldontlie: respuesta inesperada de /v1/games")
            rows.extend(data["data"])
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor:
                return rows
        raise ProviderError(f"balldontlie: más de {MAX_PAGES} páginas; consulta demasiado amplia")

    def fetch_results(self, competition_key: str = COMPETITION, season: str | None = None) -> ProviderBatch:
        """Todos los partidos de una temporada (año de inicio, p. ej. "2024")."""
        if competition_key != COMPETITION:
            raise ProviderError("balldontlie solo cubre la NBA")
        if not season or not season.isdigit():
            raise ProviderError("Indica la temporada por su año de inicio, p. ej. 2024 para 2024-25")
        finished_season = int(season) < (date.today().year - 1)
        rows: list[dict] = []
        for st in SEASON_TYPES:
            # temporadas terminadas no cambian: caché larga para no repetir consultas
            rows += self._games({"seasons[]": season, "season_type": st},
                                cache_ttl=30 * 86400 if finished_season else 3600)
        events = {e.source_event_id: e for e in parse_games(rows)}
        return ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC), events=list(events.values()))

    def fetch_recent(self, days_back: int = 3, days_ahead: int = 7, today: date | None = None) -> ProviderBatch:
        """Resultados recientes y calendario próximo en una sola consulta por fechas."""
        today = today or datetime.now(ET).date()
        rows = self._games({"start_date": (today - timedelta(days=days_back)).isoformat(),
                            "end_date": (today + timedelta(days=days_ahead)).isoformat()}, cache_ttl=1800)
        return ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC), events=parse_games(rows))

    def fetch_fixtures(self, competition_key: str = COMPETITION, days_ahead: int = 7) -> ProviderBatch:
        b = self.fetch_recent(0, days_ahead)
        b.events = [e for e in b.events if e.status == "scheduled"]
        return b
