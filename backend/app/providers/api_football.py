"""Adaptador API-Football v3 (https://www.api-football.com/documentation-v3).

* Base: https://v3.football.api-sports.io · autenticación con la cabecera ``x-apisports-key``.
* Cada solicitud cuesta 1 del cupo diario. Cabeceras de consumo: ``x-ratelimit-requests-limit`` y
  ``x-ratelimit-requests-remaining`` (día); ``X-RateLimit-Remaining`` (minuto).
* Plan gratuito: 100 solicitudes/día (verificado en api-football.com/pricing, oct-2026). Según
  reportes de usuarios, el plan gratuito solo permite temporadas pasadas y rechaza la actual con
  un error ``errors.plan``. El adaptador detecta ese error y lo explica; no se reintenta.
* Las respuestas siempre tienen ``errors`` (lista vacía o diccionario) y ``response``.
* /odds no trae nombres de equipos (solo ``fixture.id``): se cruzan con /fixtures, que se cachea.

Ninguna prueba llama a la API real: usan respuestas simuladas con httpx.MockTransport.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.providers.base import (
    Capability,
    DataProvider,
    NormalizedEvent,
    NormalizedOdds,
    ProviderBatch,
    ProviderError,
    ProviderInfo,
    ProviderKind,
    ProviderNotConfigured,
)
from app.providers.http import ProviderHttpClient, QuotaPolicy
from app.providers.normalize import COMPETITIONS, canonical_team_name

SOURCE = "api-football"
BASE_URL = "https://v3.football.api-sports.io"
FINISHED = {"FT", "AET", "PEN"}
STATUS_MAP = {
    **dict.fromkeys(FINISHED, "finished"),
    **dict.fromkeys(("TBD", "NS"), "scheduled"),
    **dict.fromkeys(("1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE"), "live"),
    "PST": "postponed", "SUSP": "postponed", "CANC": "cancelled", "ABD": "cancelled",
    "AWD": "cancelled", "WO": "cancelled",
}
# Apuestas de /odds que se usan: id -> mercado interno
BET_MATCH_WINNER, BET_OVER_UNDER, BET_BTTS = 1, 5, 8
MAX_ODDS_PAGES = 10


class PlanRestrictedError(ProviderError):
    """El plan contratado no da acceso a lo pedido (p. ej. la temporada actual en el plan gratuito)."""


def check_errors(data: Any) -> None:
    if not isinstance(data, dict) or "response" not in data:
        raise ProviderError("api-football: respuesta con formato inesperado")
    errors = data.get("errors") or {}
    if isinstance(errors, list):
        errors = {str(i): e for i, e in enumerate(errors)}
    if not errors:
        return
    text = "; ".join(f"{k}: {v}" for k, v in errors.items())
    if "plan" in errors:
        raise PlanRestrictedError(
            f"api-football: tu plan no permite esta consulta ({text}). El plan gratuito solo cubre "
            "temporadas pasadas; la temporada actual requiere un plan de pago."
        )
    raise ProviderError(f"api-football: {text}")


def _credits(resp: httpx.Response) -> tuple[float, float | None]:
    remaining = resp.headers.get("x-ratelimit-requests-remaining")
    try:
        return 1.0, float(remaining) if remaining is not None else None
    except ValueError:
        return 1.0, None


def _ts(fx: dict) -> datetime:
    if fx.get("timestamp"):
        return datetime.fromtimestamp(int(fx["timestamp"]), UTC)
    return datetime.fromisoformat(str(fx["date"]).replace("Z", "+00:00")).astimezone(UTC)


def parse_fixtures(data: dict, competition_key: str) -> list[NormalizedEvent]:
    meta = COMPETITIONS.get(competition_key, {})
    out = []
    for item in data.get("response", []):
        fx, teams, league = item.get("fixture") or {}, item.get("teams") or {}, item.get("league") or {}
        short = ((fx.get("status") or {}).get("short") or "").upper()
        status = STATUS_MAP.get(short, "scheduled")
        score = item.get("score") or {}
        # Resultado a 90 minutos: la prórroga y los penales no cuentan para los mercados de tiempo regular.
        ft = score.get("fulltime") or {}
        if status == "finished" and (ft.get("home") is None or ft.get("away") is None):
            ft = item.get("goals") or {}
        ht = score.get("halftime") or {}
        finished = status == "finished" and ft.get("home") is not None and ft.get("away") is not None
        try:
            home, away = teams["home"]["name"], teams["away"]["name"]
            kickoff = _ts(fx)
        except (KeyError, TypeError, ValueError):
            continue
        out.append(NormalizedEvent(
            sport="football", competition_key=competition_key, competition_name=meta.get("name", competition_key),
            country=meta.get("country", ""), kickoff_utc=kickoff,
            home=canonical_team_name(home), away=canonical_team_name(away),
            season=str(league.get("season", "") or ""),
            status=status if (finished or status != "finished") else "scheduled",
            home_score=int(ft["home"]) if finished else None, away_score=int(ft["away"]) if finished else None,
            home_score_ht=ht.get("home") if finished else None, away_score_ht=ht.get("away") if finished else None,
            source=SOURCE, source_event_id=str(fx.get("id", "")),
        ))
    return out


def _parse_value(bet_id: int, value: str) -> tuple[str, str, float | None] | None:
    v = str(value).strip()
    if bet_id == BET_MATCH_WINNER:
        sel = {"Home": "home", "Draw": "draw", "Away": "away"}.get(v)
        return ("1x2", sel, None) if sel else None
    if bet_id == BET_BTTS:
        sel = {"Yes": "yes", "No": "no"}.get(v)
        return ("btts", sel, None) if sel else None
    if bet_id == BET_OVER_UNDER:
        parts = v.split()
        if len(parts) == 2 and parts[0] in ("Over", "Under"):
            try:
                line = float(parts[1])
            except ValueError:
                return None
            return ("totals", parts[0].lower(), line)
    return None


def parse_odds(data: dict, competition_key: str, fixtures: dict[str, NormalizedEvent]) -> tuple[list[NormalizedOdds], int]:
    """Devuelve (cuotas, nº de partidos sin fixture conocido)."""
    out: list[NormalizedOdds] = []
    unknown = 0
    for item in data.get("response", []):
        fid = str((item.get("fixture") or {}).get("id", ""))
        ev = fixtures.get(fid)
        if ev is None:
            unknown += 1
            continue
        if not item.get("update"):
            continue  # sin timestamp no se puede evaluar frescura
        captured = datetime.fromisoformat(str(item["update"]).replace("Z", "+00:00")).astimezone(UTC)
        for bm in item.get("bookmakers", []):
            for bet in bm.get("bets", []):
                for val in bet.get("values", []):
                    parsed = _parse_value(int(bet.get("id", 0)), val.get("value", ""))
                    if parsed is None:
                        continue
                    market, sel, line = parsed
                    try:
                        price = float(val.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if price <= 1.0:
                        continue
                    out.append(NormalizedOdds(
                        sport="football", competition_key=competition_key, kickoff_utc=ev.kickoff_utc,
                        home=ev.home, away=ev.away, bookmaker=str(bm.get("name", "?")), market=market,
                        selection=sel, line=line, odds_decimal=price, captured_at=captured, source=SOURCE,
                        source_event_id=fid,
                    ))
    return out, unknown


class ApiFootballProvider(DataProvider):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.info = ProviderInfo(
            key="api_football",
            name="API-Football (api-sports.io)",
            kind=ProviderKind.REAL,
            sports=["football"],
            capabilities=[Capability.FIXTURES, Capability.RESULTS, Capability.HISTORICAL, Capability.ODDS],
            configured=bool(settings.api_football_key),
            docs_url="https://www.api-football.com/documentation-v3",
            cost_note="Gratis: 100 solicitudes/día. Pro: 19 USD/mes (≈ 350 MXN), 7,500/día.",
            coverage_note="Incluye Liga MX (id 262). El plan gratuito solo da temporadas pasadas; "
                          "la temporada actual requiere plan de pago.",
            license_note="Requiere cuenta en dashboard.api-football.com; respetar términos de uso.",
            quota_note=f"Límite configurado: {settings.api_football_daily_limit} solicitudes/día, "
                       f"{settings.api_football_per_minute}/min",
        )
        self._client: ProviderHttpClient | None = None
        self._transport = transport

    @property
    def client(self) -> ProviderHttpClient:
        if not self.settings.api_football_key:
            raise ProviderNotConfigured("Configura API_FOOTBALL_KEY en .env")
        if self._client is None:
            self._client = ProviderHttpClient(
                SOURCE, BASE_URL, self.settings.cache_dir,
                headers={"x-apisports-key": self.settings.api_football_key},
                per_minute=self.settings.api_football_per_minute,
                quota=QuotaPolicy(self.settings.api_football_daily_limit, "day"), transport=self._transport,
            )
        return self._client

    def _get(self, path: str, params: dict, ttl: float) -> dict:
        data, _ = self.client.get_json(path, params, cache_ttl=ttl, cost=1.0, credits_from_headers=_credits,
                                       validate=check_errors)
        return data

    @staticmethod
    def _league(competition_key: str) -> int:
        lid = COMPETITIONS.get(competition_key, {}).get("api_football")
        if not lid:
            raise ProviderError(f"{competition_key} no tiene id de liga en API-Football")
        return int(lid)

    def current_season(self, competition_key: str) -> str:
        data = self._get("/leagues", {"id": self._league(competition_key), "current": "true"}, ttl=24 * 3600)
        for item in data.get("response", []):
            for season in item.get("seasons", []):
                if season.get("current"):
                    return str(season["year"])
        raise ProviderError("api-football no informa una temporada actual para esta liga")

    def fetch_results(self, competition_key: str, season: str | None = None) -> ProviderBatch:
        season = season or self.current_season(competition_key)
        data = self._get("/fixtures", {"league": self._league(competition_key), "season": season,
                                       "status": "FT-AET-PEN"}, ttl=12 * 3600)
        batch = ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC),
                              events=parse_fixtures(data, competition_key))
        if not batch.events:
            batch.warnings.append(f"API-Football no devolvió partidos finalizados para la temporada {season}.")
        return batch

    def _fixtures_window(self, competition_key: str, days_ahead: int, season: str | None) -> list[NormalizedEvent]:
        season = season or self.current_season(competition_key)
        today = date.today()
        data = self._get("/fixtures", {
            "league": self._league(competition_key), "season": season,
            "from": today.isoformat(), "to": (today + timedelta(days=days_ahead)).isoformat(),
        }, ttl=3 * 3600)
        return parse_fixtures(data, competition_key)

    def fetch_fixtures(self, competition_key: str, days_ahead: int = 7, season: str | None = None) -> ProviderBatch:
        return ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC),
                             events=self._fixtures_window(competition_key, days_ahead, season))

    def fetch_odds(self, competition_key: str, season: str | None = None) -> ProviderBatch:
        season = season or self.current_season(competition_key)
        fixtures = self._fixtures_window(competition_key, 7, season)
        by_id = {e.source_event_id: e for e in fixtures if e.source_event_id}
        batch = ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC), events=fixtures)
        unknown_total, page, total = 0, 1, 1
        while page <= total and page <= MAX_ODDS_PAGES:
            data = self._get("/odds", {"league": self._league(competition_key), "season": season, "page": page},
                             ttl=30 * 60)
            odds, unknown = parse_odds(data, competition_key, by_id)
            batch.odds.extend(odds)
            unknown_total += unknown
            total = int((data.get("paging") or {}).get("total") or 1)
            page += 1
        if total > MAX_ODDS_PAGES:
            batch.warnings.append(f"Solo se leyeron {MAX_ODDS_PAGES} de {total} páginas de cuotas (cuidado de cupo).")
        if unknown_total:
            batch.warnings.append(f"{unknown_total} partidos con cuotas fuera de la ventana de 7 días (omitidos).")
        return batch
