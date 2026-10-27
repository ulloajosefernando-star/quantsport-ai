"""Adaptador The Odds API v4 (https://the-odds-api.com/liveapi/guides/v4/).

* Endpoint de cuotas: GET /v4/sports/{sport}/odds?apiKey=&regions=&markets=&oddsFormat=decimal
* Coste por llamada a /odds = nº mercados × nº regiones (créditos). /sports y /events no cuestan.
* Cabeceras de consumo: x-requests-remaining, x-requests-used, x-requests-last.
* Plan Starter gratuito: 500 créditos/mes (verificado en the-odds-api.com, oct-2026).
* La cobertura de cada liga se verifica en tiempo de ejecución con /v4/sports (gratis);
  no se asume que una liga (p. ej. Liga MX) esté disponible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from app.providers.normalize import COMPETITIONS, canonical_team_name, competition_sport

SOURCE = "the-odds-api"
MARKETS = {"h2h": "1x2", "totals": "totals", "spreads": "ah"}
# Claves del registro de mercados por deporte (en EE. UU. h2h es ganador a dos vías y spreads el hándicap)
SPORT_MARKETS = {"football": MARKETS, "nba": {"h2h": "moneyline", "totals": "totals", "spreads": "spread"}}


def _credits(resp: httpx.Response) -> tuple[float, float | None]:
    last = resp.headers.get("x-requests-last")
    remaining = resp.headers.get("x-requests-remaining")
    return (float(last) if last else 0.0, float(remaining) if remaining else None)


def parse_odds_response(data: list[dict], competition_key: str) -> list[NormalizedOdds]:
    sport = competition_sport(competition_key)
    markets = SPORT_MARKETS.get(sport, MARKETS)
    out: list[NormalizedOdds] = []
    for ev in data:
        kickoff = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        home, away = ev["home_team"], ev["away_team"]
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                market = markets.get(mk["key"])
                if not market:
                    continue
                ts_raw = mk.get("last_update") or bm.get("last_update")
                if not ts_raw:
                    continue  # sin timestamp no se puede evaluar frescura
                captured = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                for o in mk.get("outcomes", []):
                    name, price, point = o.get("name"), o.get("price"), o.get("point")
                    if not price or price <= 1:
                        continue
                    if market in ("1x2", "moneyline"):
                        sel = ("home" if name == home else "away" if name == away
                               else "draw" if name == "Draw" and market == "1x2" else None)
                        line = None
                    elif market == "totals":
                        sel = name.lower() if name in ("Over", "Under") else None
                        line = point
                    else:  # spreads: hándicap asiático en fútbol, spread en NBA
                        sel = "home" if name == home else "away" if name == away else None
                        line = point
                    if sel is None or (market not in ("1x2", "moneyline") and line is None):
                        continue
                    out.append(NormalizedOdds(
                        sport=sport, competition_key=competition_key, kickoff_utc=kickoff,
                        home=canonical_team_name(home), away=canonical_team_name(away),
                        bookmaker=bm.get("title", bm.get("key", "?")), market=market, selection=sel,
                        line=line, odds_decimal=float(price), captured_at=captured, source=SOURCE,
                        source_event_id=ev.get("id", ""),
                    ))
    return out


def parse_events(data: list[dict], competition_key: str) -> list[NormalizedEvent]:
    """Partidos programados que aparecen en la respuesta de cuotas.

    Sirven de calendario cuando no hay otra fuente de fixtures (p. ej. Liga MX con datos gratuitos):
    sin ellos, las cuotas de partidos futuros no tendrían evento al que asociarse.
    """
    meta = COMPETITIONS.get(competition_key, {})
    out = []
    for ev in data:
        try:
            kickoff = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            home, away = ev["home_team"], ev["away_team"]
        except (KeyError, TypeError, ValueError):
            continue
        out.append(NormalizedEvent(
            sport=competition_sport(competition_key), competition_key=competition_key,
            competition_name=meta.get("name", competition_key),
            country=meta.get("country", ""), kickoff_utc=kickoff, home=canonical_team_name(home),
            away=canonical_team_name(away), status="scheduled", source=SOURCE, source_event_id=ev.get("id", ""),
        ))
    return out


def parse_scores(data: list[dict], competition_key: str) -> list[NormalizedEvent]:
    """Partidos TERMINADOS de /scores (``completed`` = true). Los que siguen en juego se ignoran."""
    meta = COMPETITIONS.get(competition_key, {})
    out = []
    for ev in data:
        if not ev.get("completed"):
            continue
        try:
            kickoff = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            home, away = ev["home_team"], ev["away_team"]
            by_team = {x["name"]: int(x["score"]) for x in ev.get("scores") or []}
            hs, as_ = by_team[home], by_team[away]
        except (KeyError, TypeError, ValueError):
            continue
        out.append(NormalizedEvent(
            sport=competition_sport(competition_key), competition_key=competition_key,
            competition_name=meta.get("name", competition_key),
            country=meta.get("country", ""), kickoff_utc=kickoff, home=canonical_team_name(home),
            away=canonical_team_name(away), status="finished", home_score=hs, away_score=as_, source=SOURCE,
            source_event_id=ev.get("id", ""),
        ))
    return out


class TheOddsApiProvider(DataProvider):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.regions = [r.strip() for r in settings.the_odds_api_regions.split(",") if r.strip()]
        self.info = ProviderInfo(
            key="the_odds_api",
            name="The Odds API",
            kind=ProviderKind.REAL,
            sports=["football", "nba", "nfl", "mlb", "tennis"],
            capabilities=[Capability.ODDS, Capability.FIXTURES],  # históricos solo en planes de pago
            configured=bool(settings.the_odds_api_key),
            docs_url="https://the-odds-api.com/liveapi/guides/v4/",
            cost_note="Starter gratis: 500 créditos/mes. 20K: 30 USD/mes (≈ 553 MXN, excede el presupuesto).",
            coverage_note="Cobertura por liga verificada con /v4/sports (sin coste).",
            license_note="Requiere clave personal; respetar términos de uso.",
            quota_note=f"Límite configurado: {settings.the_odds_api_monthly_credits} créditos/mes",
        )
        self._client: ProviderHttpClient | None = None
        self._transport = transport

    @property
    def client(self) -> ProviderHttpClient:
        if not self.settings.the_odds_api_key:
            raise ProviderNotConfigured("Configura THE_ODDS_API_KEY en .env")
        if self._client is None:
            self._client = ProviderHttpClient(
                SOURCE, "https://api.the-odds-api.com", self.settings.cache_dir, per_minute=30,
                quota=QuotaPolicy(self.settings.the_odds_api_monthly_credits, "month"), transport=self._transport,
            )
        return self._client

    def available_sports(self) -> list[dict]:
        data, _ = self.client.get_json(
            "/v4/sports", {"apiKey": self.settings.the_odds_api_key}, cache_ttl=24 * 3600, cost=0.0,
            credits_from_headers=_credits,
        )
        return data

    def fetch_odds(self, competition_key: str, markets: tuple[str, ...] = ("h2h", "totals")) -> ProviderBatch:
        sport_key = COMPETITIONS.get(competition_key, {}).get("odds_api")
        if not sport_key:
            raise ProviderError(f"No hay sport_key de The Odds API para {competition_key}")
        active = {s["key"] for s in self.available_sports() if s.get("active")}
        batch = ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC))
        if sport_key not in active:
            batch.warnings.append(f"{sport_key} no está activo en The Odds API ahora mismo (sin coste consumido).")
            return batch
        cost = float(len(markets) * len(self.regions))
        data, meta = self.client.get_json(
            f"/v4/sports/{sport_key}/odds",
            {
                "apiKey": self.settings.the_odds_api_key, "regions": ",".join(self.regions),
                "markets": ",".join(markets), "oddsFormat": "decimal", "dateFormat": "iso",
            },
            cache_ttl=15 * 60, cost=cost, credits_from_headers=_credits,
        )
        now = datetime.now(UTC)
        batch.events = [e for e in parse_events(data, competition_key) if e.kickoff_utc > now]
        batch.odds = parse_odds_response(data, competition_key)
        if meta.get("from_cache"):
            batch.warnings.append("Cuotas servidas desde caché (≤15 min).")
        return batch

    def fetch_scores(self, competition_key: str, days_from: int = 3) -> ProviderBatch:
        """Resultados de los últimos ``days_from`` días (1-3). Cuesta 2 créditos (1 sin daysFrom)."""
        sport_key = COMPETITIONS.get(competition_key, {}).get("odds_api")
        if not sport_key:
            raise ProviderError(f"No hay sport_key de The Odds API para {competition_key}")
        if not 1 <= days_from <= 3:
            raise ProviderError("daysFrom debe estar entre 1 y 3")
        data, _ = self.client.get_json(
            f"/v4/sports/{sport_key}/scores",
            {"apiKey": self.settings.the_odds_api_key, "daysFrom": days_from, "dateFormat": "iso"},
            cache_ttl=3600, cost=2.0, credits_from_headers=_credits,
        )
        return ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC), events=parse_scores(data, competition_key))

    def fetch_fixtures(self, competition_key: str, days_ahead: int = 7) -> ProviderBatch:
        """Calendario desde /v4/sports/{sport}/events (no consume créditos)."""
        sport_key = COMPETITIONS.get(competition_key, {}).get("odds_api")
        if not sport_key:
            raise ProviderError(f"No hay sport_key de The Odds API para {competition_key}")
        data, _ = self.client.get_json(
            f"/v4/sports/{sport_key}/events", {"apiKey": self.settings.the_odds_api_key, "dateFormat": "iso"},
            cache_ttl=3 * 3600, cost=0.0, credits_from_headers=_credits,
        )
        now = datetime.now(UTC)
        horizon = now + timedelta(days=days_ahead)
        events = [e for e in parse_events(data, competition_key) if now < e.kickoff_utc <= horizon]
        return ProviderBatch(source=SOURCE, fetched_at=now, events=events)
