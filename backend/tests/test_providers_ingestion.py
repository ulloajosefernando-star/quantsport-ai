from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.backtesting import football_walkforward as wf
from app.core.config import Settings
from app.db.models import Event, OddsSnapshot, ScheduledJob
from app.ingestion.jobs import import_csv, run_job
from app.ingestion.store import Ingestor
from app.providers.base import ProviderError, ProviderNotConfigured, QuotaExceeded
from app.providers.csv_footballdata import CsvValidationError, detect_format, parse_csv
from app.providers.football_data_org import parse_matches
from app.providers.http import ProviderHttpClient, QuotaPolicy
from app.providers.normalize import canonical_team_name, team_key
from app.providers.the_odds_api import TheOddsApiProvider, parse_odds_response

# CSV SINTÉTICO con columnas reales del formato football-data.co.uk (valores inventados para test)
FDCOUK_MAIN = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HS,AS,B365H,B365D,B365A,PSH,PSD,PSA,B365>2.5,B365<2.5,AHh,B365AHH,B365AHA,PSCH,PSCD,PSCA
E0,16/08/2025,20:00,Man United,Arsenal,1,2,A,10,12,2.60,3.40,2.70,2.65,3.45,2.75,1.80,2.00,0.00,1.95,1.95,2.50,3.50,2.90
E0,17/08/2025,14:00,Wolves,Brighton,0,0,D,8,9,2.90,3.30,2.45,2.95,3.35,2.50,1.95,1.85,0.25,1.90,2.00,,,
E0,17/08/2025,14:00,Wolves,Brighton,0,0,D,8,9,2.90,3.30,2.45,2.95,3.35,2.50,1.95,1.85,0.25,1.90,2.00,,,
E0,99/99/2025,14:00,X,Y,1,1,D,,,,,,,,,,,,,,,,
"""
FDCOUK_NEW = """Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA
Mexico,Liga MX,2025/2026,18/07/2025,02:00,Club America,Pumas UNAM,2,1,H,1.80,3.70,4.40,1.78,3.60,4.30
"""
GENERIC = """date,competition,home,away,home_goals,away_goals,odds_home,odds_draw,odds_away,bookmaker
2025-09-01,mi_liga,Equipo Uno,Equipo Dos,1,0,2.0,3.2,3.9,Casa Propia
"""


def test_detect_format():
    assert detect_format(["Div", "HomeTeam", "AwayTeam"]) == "fdcouk_main"
    assert detect_format(["Country", "League", "Home", "Away"]) == "fdcouk_new"
    with pytest.raises(CsvValidationError):
        detect_format(["a", "b"])


def test_parse_main_format():
    b = parse_csv(FDCOUK_MAIN, "E0.csv")
    assert len(b.events) == 2  # duplicado y fecha inválida descartados
    assert any("duplicado" in w for w in b.warnings)
    assert any("Fecha no reconocida" in w for w in b.warnings)
    ev = b.events[0]
    assert ev.competition_key == "eng_premier_league"
    assert ev.home == "Manchester United"  # alias normalizado
    assert ev.home_score == 1 and ev.away_score == 2
    assert ev.stats["home"]["shots"] == 10
    o1x2 = [o for o in b.odds if o.market == "1x2" and o.home == "Manchester United"]
    assert {o.bookmaker for o in o1x2} == {"Bet365", "Pinnacle"}
    closing = [o for o in o1x2 if o.is_closing]
    assert len(closing) == 3 and all(o.captured_at == ev.kickoff_utc for o in closing)
    pre = [o for o in o1x2 if not o.is_closing]
    assert all(o.source.endswith(":ts_aprox") for o in pre)
    ah = [o for o in b.odds if (o.market == "ah" and o.home == "Brighton") or (o.away == "Brighton" and o.market == "ah")]
    assert {(o.selection, o.line) for o in ah} == {("home", 0.25), ("away", -0.25)}


def test_parse_new_and_generic_formats():
    b = parse_csv(FDCOUK_NEW, "MEX.csv")
    assert b.events[0].competition_key == "mex_liga_mx"
    assert b.events[0].home == "Club America"
    assert b.events[0].away == "UNAM"
    assert all(o.is_closing for o in b.odds)
    g = parse_csv(GENERIC, "propio.csv")
    assert g.events[0].competition_key == "mi_liga"
    assert len(g.odds) == 3


def test_team_normalization():
    assert canonical_team_name("Manchester United FC") == "Manchester United"
    assert team_key("Atlético Madrid") == team_key("Ath Madrid")
    assert team_key("  Club   América ") == team_key("Club America")


def test_ingestion_idempotent_and_snapshots_immutable(session):
    first = import_csv(session, FDCOUK_MAIN.encode(), "E0.csv")
    assert first["events_created"] == 2
    n_odds = session.scalar(select(func.count(OddsSnapshot.id)))
    again = import_csv(session, FDCOUK_MAIN.encode(), "E0.csv")
    assert again["events_created"] == 0 and again["odds_inserted"] == 0
    assert session.scalar(select(func.count(OddsSnapshot.id))) == n_odds
    # Una cuota nueva para el mismo evento se AÑADE, no sobrescribe
    modified = FDCOUK_MAIN.replace("2.60,3.40,2.70", "2.55,3.40,2.80", 1).replace("16/08/2025,20:00", "16/08/2025,20:00")
    b = parse_csv(modified, "E0.csv")
    for o in b.odds:
        o.captured_at = o.captured_at + timedelta(hours=3)
    rep = Ingestor(session).ingest(b)
    session.commit()
    assert rep.odds_inserted > 0
    assert session.scalar(select(func.count(OddsSnapshot.id))) > n_odds


def test_cross_source_match_within_window(session):
    import_csv(session, FDCOUK_NEW.encode(), "MEX.csv")
    # misma fecha ±36 h desde otra fuente => mismo evento
    data = {"matches": [{"id": 1, "utcDate": "2025-07-18T03:05:00Z", "status": "FINISHED",
                         "homeTeam": {"name": "América"}, "awayTeam": {"name": "Pumas"},
                         "score": {"fullTime": {"home": 2, "away": 1}, "halfTime": {"home": 1, "away": 0}}}]}
    events = parse_matches(data, "mex_liga_mx")
    from app.providers.base import ProviderBatch

    rep = Ingestor(session).ingest(ProviderBatch("football-data.org", datetime.now(UTC), events=events))
    session.commit()
    assert rep.events_created == 0


# ------------------------------------------------------------------ HTTP / cuotas de API
def _client(tmp_path, handler, quota=None, per_minute=0):
    sleeps = []
    c = ProviderHttpClient("prov-test", "https://example.test", tmp_path, per_minute=per_minute, quota=quota,
                           transport=httpx.MockTransport(handler), sleep=sleeps.append, backoff_base=2.0)
    return c, sleeps


def test_retry_with_exponential_backoff(tmp_path, db_engine):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    c, sleeps = _client(tmp_path, handler)
    data, _meta = c.get_json("/x", cache_ttl=0)
    assert data == {"ok": True} and calls["n"] == 3
    assert sleeps == [2.0, 4.0]


def test_non_retryable_error(tmp_path, db_engine):
    c, _ = _client(tmp_path, lambda req: httpx.Response(401, text="no"))
    with pytest.raises(ProviderError):
        c.get_json("/x", cache_ttl=0)


def test_cache_avoids_second_request(tmp_path, db_engine):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=[1])

    c, _ = _client(tmp_path, handler)
    c.get_json("/y", {"apiKey": "secreto"}, cache_ttl=60)
    _, meta = c.get_json("/y", {"apiKey": "secreto"}, cache_ttl=60)
    assert calls["n"] == 1 and meta["from_cache"]
    assert all("secreto" not in p.read_text() for p in (tmp_path / "prov-test").glob("*.json"))


def test_quota_blocks_requests(tmp_path, db_engine):
    c, _ = _client(tmp_path, lambda req: httpx.Response(200, json={}), quota=QuotaPolicy(limit=2, period="month"))
    c.provider = "prov-quota"
    c.get_json("/a", cache_ttl=0, cost=2)
    with pytest.raises(QuotaExceeded):
        c.get_json("/b", cache_ttl=0, cost=1)


def test_rate_limit_spacing(tmp_path, db_engine):
    c, sleeps = _client(tmp_path, lambda req: httpx.Response(200, json={}), per_minute=60)
    c.get_json("/a", cache_ttl=0)
    c.get_json("/b", cache_ttl=0)
    assert sleeps and 0 < sleeps[-1] <= 1.0


def test_odds_api_not_configured():
    p = TheOddsApiProvider(Settings(the_odds_api_key=""))
    assert not p.info.configured
    with pytest.raises(ProviderNotConfigured):
        p.fetch_odds("eng_premier_league")


def test_parse_odds_api_response():
    data = [{
        "id": "abc", "sport_key": "soccer_epl", "commence_time": "2026-10-25T14:00:00Z",
        "home_team": "Arsenal", "away_team": "Chelsea",
        "bookmakers": [{"key": "pinnacle", "title": "Pinnacle", "markets": [
            {"key": "h2h", "last_update": "2026-10-23T10:00:00Z", "outcomes": [
                {"name": "Arsenal", "price": 2.1}, {"name": "Chelsea", "price": 3.5}, {"name": "Draw", "price": 3.4}]},
            {"key": "totals", "last_update": "2026-10-23T10:00:00Z", "outcomes": [
                {"name": "Over", "price": 1.9, "point": 2.5}, {"name": "Under", "price": 1.95, "point": 2.5}]},
            {"key": "spreads", "outcomes": [{"name": "Arsenal", "price": 1.9, "point": -0.5}]},  # sin timestamp
        ]}],
    }]
    out = parse_odds_response(data, "eng_premier_league")
    assert {(o.market, o.selection) for o in out} == {("1x2", "home"), ("1x2", "away"), ("1x2", "draw"),
                                                     ("totals", "over"), ("totals", "under")}
    assert all(o.captured_at.tzinfo is not None for o in out)


def test_odds_api_inactive_sport_costs_nothing(tmp_path, db_engine):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/v4/sports":
            return httpx.Response(200, json=[{"key": "soccer_epl", "active": False}])
        return httpx.Response(200, json=[])

    p = TheOddsApiProvider(Settings(the_odds_api_key="k", cache_dir=tmp_path), transport=httpx.MockTransport(handler))
    b = p.fetch_odds("eng_premier_league")
    assert b.odds == [] and b.warnings
    assert "/v4/sports/soccer_epl/odds" not in seen


# ------------------------------------------------------------------ tareas
def test_run_job_idempotent(session):
    calls = []
    r1 = run_job(session, "tarea_test", "2026-10-23", lambda: calls.append(1) or "hecho")
    r2 = run_job(session, "tarea_test", "2026-10-23", lambda: calls.append(1) or "hecho")
    assert r1["status"] == "ok" and r2["status"] == "skipped" and len(calls) == 1


def test_run_job_retries_after_failure(session):
    def boom():
        raise RuntimeError("fallo")

    assert run_job(session, "tarea_fallo", "k", boom)["status"] == "error"
    assert run_job(session, "tarea_fallo", "k", lambda: "ok")["status"] == "ok"
    job = session.scalar(select(ScheduledJob).where(ScheduledJob.job_name == "tarea_fallo"))
    assert job.attempts == 2


# ------------------------------------------------------------------ backtesting
def test_walkforward_no_leakage_and_metrics(demo_loaded, session):
    from app.db.models import Competition
    from app.sports.football.data import finished, matches_frame, odds_frame

    comp = session.scalar(select(Competition).where(Competition.is_synthetic.is_(True)))
    df = finished(matches_frame(session, comp.id))
    odds = odds_frame(session, df["event_id"].tolist())
    res = wf.run(df, odds)
    assert res["status"] == "ok"
    assert res["n_eval"] > 300
    assert res["odds_kind"] == "sintetica"
    assert "hipotético" in res["betting"]["note"]
    for key in ("1x2", "totals_2.5", "btts"):
        assert {"brier", "log_loss", "ece"} <= set(res[key]["model"])
        assert "vs_baseline_mercado" in res[key]
    test_start = pd.Timestamp(res["test_period"][0])
    assert test_start > df["kickoff"].min()


def test_walkforward_detects_leakage(monkeypatch):
    df = pd.DataFrame({"kickoff": pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC"),
                       "home": ["A", "B"] * 200, "away": ["B", "A"] * 200, "home_goals": 1, "away_goals": 0,
                       "event_id": range(400)})
    original = pd.DataFrame.__getitem__
    tampered = {"done": False}

    def leaky_getitem(self, key):
        out = original(self, key)
        # simula un bug que mete un partido futuro en el entrenamiento
        if isinstance(key, pd.Series) and key.dtype == bool and not tampered["done"] and len(out) > 100:
            tampered["done"] = True
            return pd.concat([out, original(self, "kickoff").to_frame().tail(1).assign(
                home="A", away="B", home_goals=1, away_goals=0, event_id=-1)])
        return out

    monkeypatch.setattr(pd.DataFrame, "__getitem__", leaky_getitem)
    with pytest.raises(wf.LeakageError):
        wf.run(df, pd.DataFrame())


def test_events_table_has_no_duplicates(session, demo_loaded):
    dupes = session.execute(
        select(Event.competition_id, Event.home_team_id, Event.away_team_id, Event.kickoff_utc, func.count())
        .group_by(Event.competition_id, Event.home_team_id, Event.away_team_id, Event.kickoff_utc)
        .having(func.count() > 1)
    ).all()
    assert dupes == []
