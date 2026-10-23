"""Fase 3: API-Football, calendario de The Odds API, cuotas de cierre, revisión/fusión de equipos y CLI.

Todo el HTTP se simula con httpx.MockTransport: ninguna prueba consume cuota real.
Los datos son SINTÉTICOS construidos en código.
"""

import math
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backtesting import football_walkforward as wf
from app.core.config import Settings
from app.db.models import Event, OddsSnapshot, Team
from app.ingestion import teams
from app.ingestion.store import Ingestor
from app.providers.api_football import (
    ApiFootballProvider,
    PlanRestrictedError,
    check_errors,
    parse_fixtures,
    parse_odds,
)
from app.providers.base import NormalizedEvent, NormalizedOdds, ProviderBatch, ProviderError, ProviderNotConfigured
from app.providers.the_odds_api import TheOddsApiProvider, parse_events

NOW = datetime.now(UTC).replace(microsecond=0)


def _fixture(fid, home, away, kickoff, short="NS", ft=(None, None), goals=None, season=2026):
    return {
        "fixture": {"id": fid, "date": kickoff.isoformat(), "timestamp": int(kickoff.timestamp()),
                    "status": {"short": short}},
        "league": {"id": 262, "season": season, "round": "Apertura - 10"},
        "teams": {"home": {"id": 1, "name": home}, "away": {"id": 2, "name": away}},
        "goals": {"home": (goals or ft)[0], "away": (goals or ft)[1]},
        "score": {"halftime": {"home": None, "away": None}, "fulltime": {"home": ft[0], "away": ft[1]}},
    }


def _envelope(response, errors=None, paging=None):
    return {"get": "x", "parameters": {}, "errors": errors if errors is not None else [], "results": len(response),
            "paging": paging or {"current": 1, "total": 1}, "response": response}


def _af(tmp_path, handler, **kw):
    settings = Settings(api_football_key="clave-prueba", cache_dir=tmp_path, api_football_per_minute=0, **kw)
    return ApiFootballProvider(settings, transport=httpx.MockTransport(handler))


# ----------------------------------------------------------------- API-Football: parsing
def test_check_errors_formats():
    check_errors(_envelope([]))
    check_errors(_envelope([], errors={}))
    with pytest.raises(PlanRestrictedError, match="temporada actual"):
        check_errors(_envelope([], errors={"plan": "Free plans do not have access to this season."}))
    with pytest.raises(ProviderError, match="token"):
        check_errors(_envelope([], errors={"token": "Error/Missing application key."}))
    with pytest.raises(ProviderError, match="formato"):
        check_errors(["no es un diccionario"])


def test_parse_fixtures_uses_90_minute_score():
    k = NOW - timedelta(days=3)
    data = _envelope([
        _fixture(1, "Club America", "Guadalajara Chivas", k, "FT", ft=(2, 1)),
        _fixture(2, "Tigres UANL", "Monterrey", k, "AET", ft=(1, 1), goals=(2, 1)),  # gana en prórroga
        _fixture(3, "Toluca", "Necaxa", NOW + timedelta(days=2), "NS"),
        _fixture(4, "Puebla", "Atlas", NOW + timedelta(days=1), "PST"),
    ])
    evs = {e.source_event_id: e for e in parse_fixtures(data, "mex_liga_mx")}
    assert (evs["1"].home, evs["1"].away, evs["1"].home_score, evs["1"].status) == (
        "Club America", "Guadalajara", 2, "finished")
    assert (evs["2"].home, evs["2"].home_score, evs["2"].away_score) == ("Tigres", 1, 1)
    assert evs["3"].status == "scheduled" and evs["3"].home_score is None
    assert evs["4"].status == "postponed"
    assert all(e.kickoff_utc.tzinfo is not None for e in evs.values())


def test_parse_odds_maps_markets_and_skips_unknown_fixtures():
    ev = NormalizedEvent(sport="football", competition_key="mex_liga_mx", competition_name="Liga MX",
                         kickoff_utc=NOW + timedelta(days=1), home="Toluca", away="Necaxa", source="api-football",
                         source_event_id="3")
    data = _envelope([
        {"fixture": {"id": 3}, "update": NOW.isoformat(), "bookmakers": [{"id": 8, "name": "Bet365", "bets": [
            {"id": 1, "name": "Match Winner", "values": [{"value": "Home", "odd": "1.90"},
                                                         {"value": "Draw", "odd": "3.50"},
                                                         {"value": "Away", "odd": "4.20"}]},
            {"id": 5, "name": "Goals Over/Under", "values": [{"value": "Over 2.5", "odd": "1.95"},
                                                             {"value": "Under 2.5", "odd": "1.85"}]},
            {"id": 8, "name": "Both Teams Score", "values": [{"value": "Yes", "odd": "1.80"},
                                                             {"value": "No", "odd": "1.0"}]},  # inválida
            {"id": 99, "name": "Otro", "values": [{"value": "X", "odd": "2.0"}]},
        ]}]},
        {"fixture": {"id": 999}, "update": NOW.isoformat(), "bookmakers": []},
    ])
    odds, unknown = parse_odds(data, "mex_liga_mx", {"3": ev})
    got = {(o.market, o.selection, o.line): o.odds_decimal for o in odds}
    assert got == {("1x2", "home", None): 1.90, ("1x2", "draw", None): 3.50, ("1x2", "away", None): 4.20,
                   ("totals", "over", 2.5): 1.95, ("totals", "under", 2.5): 1.85, ("btts", "yes", None): 1.80}
    assert unknown == 1
    assert all(o.home == "Toluca" and o.captured_at == NOW for o in odds)


# ----------------------------------------------------------------- API-Football: cliente
def test_api_football_not_configured():
    with pytest.raises(ProviderNotConfigured):
        ApiFootballProvider(Settings(api_football_key="")).fetch_results("mex_liga_mx", "2024")


def test_api_football_fetch_odds_flow(tmp_path, db_engine):
    seen = []
    kickoff = NOW + timedelta(days=2)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, dict(req.url.params)))
        assert req.headers["x-apisports-key"] == "clave-prueba"
        headers = {"x-ratelimit-requests-remaining": "95"}
        if req.url.path == "/leagues":
            return httpx.Response(200, headers=headers, json=_envelope(
                [{"seasons": [{"year": 2025, "current": False}, {"year": 2026, "current": True}]}]))
        if req.url.path == "/fixtures":
            return httpx.Response(200, headers=headers, json=_envelope([_fixture(7, "Toluca", "Necaxa", kickoff)]))
        if req.url.path == "/odds":
            page = int(req.url.params["page"])
            item = {"fixture": {"id": 7}, "update": NOW.isoformat(), "bookmakers": [{"name": f"Casa {page}", "bets": [
                {"id": 1, "values": [{"value": "Home", "odd": "2.0"}, {"value": "Draw", "odd": "3.4"},
                                     {"value": "Away", "odd": "3.6"}]}]}]}
            return httpx.Response(200, headers=headers, json=_envelope([item], paging={"current": page, "total": 2}))
        return httpx.Response(404)

    batch = _af(tmp_path, handler).fetch_odds("mex_liga_mx")
    assert [p for p, _ in seen] == ["/leagues", "/fixtures", "/odds", "/odds"]
    assert seen[1][1]["league"] == "262" and seen[1][1]["season"] == "2026"
    assert len(batch.events) == 1 and len(batch.odds) == 6
    assert {o.bookmaker for o in batch.odds} == {"Casa 1", "Casa 2"}


def test_api_football_plan_error_is_not_cached(tmp_path, db_engine):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=_envelope([], errors={"plan": "Free plans do not have access to this season"}))

    p = _af(tmp_path, handler)
    for _ in range(2):
        with pytest.raises(PlanRestrictedError):
            p.fetch_results("mex_liga_mx", "2026")
    assert calls["n"] == 2  # el error no quedó guardado en caché


def test_api_football_results_with_explicit_season(tmp_path, db_engine):
    def handler(req):
        assert req.url.params["season"] == "2023" and req.url.params["status"] == "FT-AET-PEN"
        return httpx.Response(200, json=_envelope([_fixture(1, "Leon", "Pachuca", NOW - timedelta(days=400), "FT",
                                                            ft=(0, 0), season=2023)]))

    batch = _af(tmp_path, handler).fetch_results("mex_liga_mx", "2023")
    assert batch.events[0].season == "2023" and batch.events[0].status == "finished"


def test_api_football_unknown_competition(tmp_path):
    with pytest.raises(ProviderError, match="id de liga"):
        _af(tmp_path, lambda r: httpx.Response(200)).fetch_results("liga_inventada", "2024")


# ----------------------------------------------------------------- The Odds API: calendario
def test_odds_api_events_become_fixtures(tmp_path, db_engine):
    ko = (NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    payload = [{"id": "abc", "commence_time": ko, "home_team": "Club America", "away_team": "Pumas UNAM"},
               {"id": "old", "commence_time": "2020-01-01T00:00:00Z", "home_team": "A", "away_team": "B"}]
    assert [e.home for e in parse_events(payload, "mex_liga_mx")] == ["Club America", "A"]

    def handler(req):
        assert req.url.path == "/v4/sports/soccer_mexico_ligamx/events"
        return httpx.Response(200, headers={"x-requests-last": "0", "x-requests-remaining": "500"}, json=payload)

    p = TheOddsApiProvider(Settings(the_odds_api_key="k", cache_dir=tmp_path), transport=httpx.MockTransport(handler))
    batch = p.fetch_fixtures("mex_liga_mx")
    assert [(e.home, e.away, e.status) for e in batch.events] == [("Club America", "UNAM", "scheduled")]


# ----------------------------------------------------------------- walk-forward con solo cuotas de cierre
def _poisson_league(seed=3, teams_n=12, seasons=3):
    rng = np.random.default_rng(seed)
    names = [f"Equipo {i}" for i in range(teams_n)]
    att = rng.normal(0, 0.25, teams_n)
    dfn = rng.normal(0, 0.2, teams_n)
    rows, odds, eid = [], [], 0
    start = pd.Timestamp("2023-01-07", tz="UTC")
    for season in range(seasons):
        for rnd, (i, j) in enumerate((i, j) for i in range(teams_n) for j in range(teams_n) if i != j):
            k = start + pd.Timedelta(days=season * 365 + rnd * 2)
            lh, la = np.exp(0.25 + att[i] - dfn[j]), np.exp(att[j] - dfn[i])
            hg, ag = rng.poisson(lh), rng.poisson(la)
            rows.append({"event_id": eid, "kickoff": k, "home": names[i], "away": names[j],
                         "home_goals": hg, "away_goals": ag})
            ph = sum(np.exp(-lh) * lh**x / math.factorial(x) * sum(
                np.exp(-la) * la**y / math.factorial(y) for y in range(x)) for x in range(1, 11))
            pd_ = sum((np.exp(-lh) * lh**x / math.factorial(x)) * (np.exp(-la) * la**x / math.factorial(x))
                      for x in range(11))
            pa = 1 - ph - pd_
            for sel, p in (("home", ph), ("draw", pd_), ("away", pa)):
                odds.append({"event_id": eid, "bookmaker": "Pinnacle", "market": "1x2", "line": None, "selection": sel,
                             "odds": 1 / (p * 1.04), "captured_at": k, "is_closing": True,
                             "source": "csv:football-data.co.uk", "is_synthetic": False})
            eid += 1
    return pd.DataFrame(rows), pd.DataFrame(odds)


def test_walkforward_uses_closing_odds_when_no_preclose():
    matches, odds = _poisson_league()
    res = wf.run(matches, odds)
    assert res["status"] == "ok"
    assert res["market_reference"]["cierre"] == res["n_eval"] and res["market_reference"]["pre_cierre"] == 0
    assert "vs_baseline_mercado" in res["1x2"]
    assert "CIERRE" in res["betting"]["note"]
    assert "clv_mean" not in res["betting"]  # apostar al cierre no tiene CLV


# ----------------------------------------------------------------- revisión y fusión de equipos
def _ev(home, away, kickoff, source, status="finished", score=(1, 0), comp="test_fusion"):
    return NormalizedEvent(sport="football", competition_key=comp, competition_name="Liga de prueba",
                           kickoff_utc=kickoff, home=home, away=away, source=source, status=status,
                           home_score=score[0] if status == "finished" else None,
                           away_score=score[1] if status == "finished" else None)


def test_review_and_merge_teams(session):
    base = datetime(2025, 1, 4, 18, tzinfo=UTC)
    hist = [_ev(h, a, base + timedelta(days=7 * i), "csv:x")
            for i, (h, a) in enumerate([("Ficticio Norte", "Ficticio Sur"), ("Ficticio Sur", "Ficticio Este"),
                                        ("Ficticio Este", "Ficticio Norte"), ("Ficticio Norte", "Ficticio Este")])]
    Ingestor(session).ingest(ProviderBatch(source="csv:x", fetched_at=NOW, events=hist))
    dup_kick = base + timedelta(days=21, hours=2)  # mismo partido que el 4º, otra fuente, otra hora
    api = [_ev("Ficticio N.", "Ficticio Este", dup_kick, "api"),
           _ev("Ficticio N.", "Ficticio Sur", NOW + timedelta(days=3), "api", status="scheduled")]
    odds = [NormalizedOdds(sport="football", competition_key="test_fusion", kickoff_utc=dup_kick, home="Ficticio N.",
                           away="Ficticio Este", bookmaker="Casa", market="1x2", selection="home", odds_decimal=2.1,
                           captured_at=dup_kick - timedelta(hours=1), source="api")]
    Ingestor(session).ingest(ProviderBatch(source="api", fetched_at=NOW, events=api, odds=odds))

    r = teams.review(session, "test_fusion", min_matches=2)
    row = next(t for t in r["teams"] if t["name"] == "Ficticio N.")
    assert row["needs_review"] and row["suggestions"][0]["name"] == "Ficticio Norte"
    target = row["suggestions"][0]["team_id"]

    rep = teams.merge(session, row["team_id"], target)
    assert rep["events_merged"] == 1 and rep["events_moved"] == 1 and rep["odds_moved"] == 1
    assert session.get(Team, row["team_id"]) is None
    norte_events = session.scalars(select(Event).where(Event.home_team_id == target)).all()
    assert len(norte_events) == 3  # 2 históricos + el programado; el duplicado se unió
    merged = next(e for e in norte_events if abs(e.kickoff_utc.replace(tzinfo=UTC) - dup_kick) < timedelta(hours=3))
    assert merged.kickoff_utc.replace(tzinfo=UTC) == dup_kick  # la hora exacta de la API sustituye a la del CSV
    assert session.scalar(select(OddsSnapshot).where(OddsSnapshot.event_id == merged.id)) is not None

    # el alias queda guardado: una nueva sincronización ya no crea el equipo duplicado
    Ingestor(session).ingest(ProviderBatch(source="api", fetched_at=NOW, events=[
        _ev("Ficticio N.", "Ficticio Este", NOW + timedelta(days=10), "api", status="scheduled")]))
    assert session.scalar(select(Team).where(Team.name == "Ficticio N.")) is None


def test_merge_rejects_invalid(session):
    from app.db.models import Sport
    from app.ingestion.store import seed_sports

    seed_sports(session)
    football = session.scalar(select(Sport).where(Sport.key == "football"))
    a = Team(sport_id=football.id, canonical_key="x_rechazo_a", name="Rechazo A")
    session.add(a)
    session.flush()
    with pytest.raises(teams.TeamMergeError):
        teams.merge(session, a.id, a.id)
    with pytest.raises(teams.TeamMergeError):
        teams.merge(session, a.id, 99999999)


# ----------------------------------------------------------------- API y CLI
def test_teams_endpoints(db_engine, demo_loaded):
    from app.main import app

    with TestClient(app) as c:
        comps = c.get("/api/settings").json()["competitions"]
        r = c.get("/api/teams/review", params={"competition": comps[0]["key"]})
        assert r.status_code == 200 and r.json()["teams"]
        assert c.get("/api/teams/review", params={"competition": "no_existe"}).status_code == 404
        assert c.post("/api/teams/merge", json={"from_team_id": 99999998, "into_team_id": 99999999}).status_code == 409
        assert c.post("/api/teams/merge", json={"from_team_id": 0, "into_team_id": 1}).status_code == 422


def test_cli_import_review_and_validate(tmp_path, capsys, db_engine):
    from app.cli import main

    csv = tmp_path / "liga.csv"
    lines = ["date,competition,home,away,home_goals,away_goals"]
    lines += [f"2025-0{1 + i % 9}-1{i % 9},cli_liga,Cli Uno,Cli Dos,{i % 3},{i % 2}" for i in range(6)]
    csv.write_text("\n".join(lines))
    assert main(["importar", str(csv)]) == 0
    assert "Eventos nuevos: 6" in capsys.readouterr().out
    assert main(["equipos", "--competicion", "cli_liga"]) == 0
    assert "Cli Uno" in capsys.readouterr().out
    assert main(["validar", "--competicion", "cli_liga"]) == 0
    assert "Sin modelo" in capsys.readouterr().out


def test_cli_refuses_other_hosts():
    from app.cli import _read_source

    with pytest.raises(SystemExit, match=r"football-data\.co\.uk"):
        _read_source("https://example.com/datos.csv")
    with pytest.raises(SystemExit):
        _read_source("http://www.football-data.co.uk/new/MEX.csv")
