"""Programador automático (Fase 7): tareas, presupuesto, idempotencia, liquidación con CLV y API.

Todo el HTTP se simula; ninguna prueba llama a proveedores reales.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Competition, Event, ModelVersion, OddsSnapshot, Prediction, Settlement
from app.ingestion.download import DownloadError, download, fdcouk_url
from app.ingestion.store import Ingestor, as_utc
from app.providers.base import NormalizedEvent, ProviderBatch
from app.providers.http import credits_used
from app.providers.the_odds_api import TheOddsApiProvider, parse_scores
from app.reporting.predictions import closing_odds_for, settle_pending
from app.scheduler import runner, tasks

NOW = datetime.now(UTC).replace(microsecond=0)
ISO = "%Y-%m-%dT%H:%M:%SZ"


def real_settings(tmp_path, **kw) -> Settings:
    base = {"data_mode": "real", "the_odds_api_key": "clave-prueba", "cache_dir": tmp_path / "cache",
            "imports_dir": tmp_path / "imports", "the_odds_api_regions": "eu",
            "the_odds_api_monthly_credits": 100000, "scheduler_credit_reserve": 0,
            "scheduler_competitions": "mex_liga_mx"}
    return Settings(**(base | kw))


class FakeOddsApi:
    """Responde /sports, /events, /odds y /scores como The Odds API y cuenta las llamadas."""

    def __init__(self, kickoff: datetime, home: str = "Club Prueba Norte", away: str = "Club Prueba Sur") -> None:
        self.calls: list[str] = []
        self.kickoff, self.home, self.away = kickoff, home, away

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(path)
        hdr = {"x-requests-remaining": "400", "x-requests-used": "100", "x-requests-last": "2"}
        ev = {"id": "ev1", "sport_key": "soccer_mexico_ligamx", "commence_time": self.kickoff.strftime(ISO),
              "home_team": self.home, "away_team": self.away}
        if path.endswith("/sports"):
            return httpx.Response(200, json=[{"key": "soccer_mexico_ligamx", "active": True}], headers=hdr)
        if path.endswith("/events"):
            return httpx.Response(200, json=[ev], headers=hdr)
        if path.endswith("/odds"):
            book = {"key": "pinnacle", "title": "Pinnacle", "last_update": NOW.strftime(ISO), "markets": [
                {"key": "h2h", "last_update": NOW.strftime(ISO), "outcomes": [
                    {"name": self.home, "price": 2.1}, {"name": self.away, "price": 3.5}, {"name": "Draw", "price": 3.3}]}]}
            return httpx.Response(200, json=[ev | {"bookmakers": [book]}], headers=hdr)
        if path.endswith("/scores"):
            done = ev | {"id": "ev0", "commence_time": (NOW - timedelta(days=1)).strftime(ISO), "completed": True,
                         "scores": [{"name": self.home, "score": "2"}, {"name": self.away, "score": "0"}]}
            live = ev | {"completed": False, "scores": None}
            return httpx.Response(200, json=[done, live], headers=hdr)
        return httpx.Response(404, json={"message": "no"})


def provider(settings: Settings, fake: FakeOddsApi) -> TheOddsApiProvider:
    return TheOddsApiProvider(settings, transport=httpx.MockTransport(fake))


# ----------------------------------------------------------------- utilidades
def test_fdcouk_urls_and_download_allowlist(tmp_path):
    assert fdcouk_url("mex_liga_mx") == "https://www.football-data.co.uk/new/MEX.csv"
    assert fdcouk_url("eng_premier_league", datetime(2026, 10, 24).date()).endswith("/mmz4281/2627/E0.csv")
    assert fdcouk_url("eng_premier_league", datetime(2027, 3, 1).date()).endswith("/mmz4281/2627/E0.csv")
    for bad in ("http://www.football-data.co.uk/new/MEX.csv", "https://example.com/x.csv"):
        with pytest.raises(DownloadError):
            download(bad)


def test_parse_scores_only_completed():
    fake = FakeOddsApi(NOW + timedelta(days=2))
    data = fake(httpx.Request("GET", "https://api.the-odds-api.com/v4/sports/x/scores")).json()
    evs = parse_scores(data, "mex_liga_mx")
    assert len(evs) == 1 and evs[0].status == "finished" and (evs[0].home_score, evs[0].away_score) == (2, 0)


def test_run_keys_and_schedule_build(tmp_path):
    s = real_settings(tmp_path, scheduler_odds_times="09:00,17:30", scheduler_prekickoff_minutes=45)
    t = datetime(2026, 10, 24, 15, 5, tzinfo=UTC)  # 09:05 en CDMX
    assert runner.run_key("diaria", s, t) == "2026-10-24"
    assert runner.run_key("cuotas", s, t) == "2026-10-24T09"
    assert runner.run_key("previa", s, t).startswith("2026-10-24T09:05")
    assert runner.run_key("cuotas", s, t, manual=True).startswith("manual:")
    sched = runner.build(s)
    assert sorted(j.id for j in sched.get_jobs()) == ["cuotas_09:00", "cuotas_17:30", "diaria", "previa"]
    assert [j.id for j in runner.build(real_settings(tmp_path, scheduler_prekickoff_minutes=0)).get_jobs()
            if j.id == "previa"] == []
    with pytest.raises(ValueError):
        runner.build(real_settings(tmp_path, scheduler_daily_time="25:00"))


def test_scheduler_never_starts_in_demo_or_when_disabled(tmp_path):
    assert runner.start(Settings(data_mode="demo", scheduler_enabled=True)) is False
    assert runner.start(real_settings(tmp_path, scheduler_enabled=False)) is False
    assert not runner.is_running()
    with pytest.raises(tasks.NotRealModeError):
        tasks.task_odds(None, Settings(data_mode="demo"))


def test_monthly_plan_fits_free_plan(session, tmp_path):
    plan = tasks.monthly_plan(session, real_settings(tmp_path, the_odds_api_monthly_credits=500,
                                                     scheduler_credit_reserve=50))
    # 1 liga · 2 sincronizaciones/día · 2 créditos = 120; resultados 60; previas ≈ 12 horarios/semana · 2 créditos
    assert plan["odds_call_cost"] == 2 and plan["odds"] == 120 and plan["scores"] == 60
    assert plan["total"] == plan["odds"] + plan["scores"] + plan["prekickoff"] and plan["fits"]
    wide = tasks.monthly_plan(session, real_settings(tmp_path, the_odds_api_regions="eu,uk,us",
                                                     the_odds_api_monthly_credits=500, scheduler_credit_reserve=50))
    assert wide["odds_call_cost"] == 6 and not wide["fits"]


# ----------------------------------------------------------------- tareas
def test_task_daily_imports_csv_fixtures_scores_and_settles(session, tmp_path, monkeypatch, db_engine):
    csv = ("Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA\n"
           "Mexico,Liga MX,2026/2027,10/10/2026,02:00,Club Diario Uno,Club Diario Dos,1,1,D,2.0,3.3,3.9,2.0,3.2,3.8\n")
    monkeypatch.setattr(tasks, "download", lambda url: (csv.encode(), "MEX.csv"))
    fake = FakeOddsApi(NOW + timedelta(days=3), "Club Diario Tres", "Club Diario Cuatro")
    st = real_settings(tmp_path)
    out = tasks.task_daily(session, st, provider=provider(st, fake))
    assert out["mex_liga_mx:csv"]["events_created"] == 1
    assert out["mex_liga_mx:calendario"]["events_created"] == 1
    assert isinstance(out["mex_liga_mx:resultados"], dict)
    assert "settled" in out["liquidacion"] and "inserted" in out["pronosticos"]
    assert any(c.endswith("/scores") for c in fake.calls) and not any(c.endswith("/odds") for c in fake.calls)
    finished = session.scalar(select(Event).where(Event.status == "finished", Event.home_score == 2,
                                                  Event.source == tasks.ODDS_API))
    assert finished is not None


def test_task_daily_without_key_does_only_free_steps(session, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "download", lambda url: (_ for _ in ()).throw(httpx.ConnectError("sin red")))
    out = tasks.task_daily(session, real_settings(tmp_path, the_odds_api_key=""))
    assert out["mex_liga_mx:csv"].startswith("error:")  # un paso que falla no detiene el resto
    assert "mex_liga_mx:calendario" not in out and "settled" in out["liquidacion"]


def test_task_odds_respects_budget(session, tmp_path):
    fake = FakeOddsApi(NOW + timedelta(days=2), "Club Cuota Uno", "Club Cuota Dos")
    used = credits_used(tasks.ODDS_API, "month")
    tight = real_settings(tmp_path, the_odds_api_monthly_credits=int(used) + 1)
    out = tasks.task_odds(session, tight, provider=provider(tight, fake))
    assert out["mex_liga_mx:cuotas"].startswith("omitido por presupuesto") and fake.calls == []
    roomy = real_settings(tmp_path)
    out = tasks.task_odds(session, roomy, provider=provider(roomy, fake))
    assert out["mex_liga_mx:cuotas"]["odds_inserted"] == 3 and any(c.endswith("/odds") for c in fake.calls)
    out = tasks.task_odds(session, real_settings(tmp_path, the_odds_api_key=""))
    assert out["mex_liga_mx:cuotas"] == "omitido: falta THE_ODDS_API_KEY"


def test_task_prekickoff_once_per_kickoff(session, tmp_path):
    kick = (NOW + timedelta(minutes=40)).replace(second=0)
    Ingestor(session).ingest(ProviderBatch(source="the_odds_api", fetched_at=NOW, events=[NormalizedEvent(
        sport="football", competition_key="mex_liga_mx", competition_name="Liga MX", kickoff_utc=kick,
        home="Club Previa Uno", away="Club Previa Dos", source="the_odds_api")]))
    session.commit()
    fake = FakeOddsApi(kick, "Club Previa Uno", "Club Previa Dos")
    st = real_settings(tmp_path, scheduler_prekickoff_minutes=45)
    out = tasks.task_prekickoff(session, st, NOW, provider=provider(st, fake))
    key = f"mex_liga_mx:{as_utc(kick):%Y-%m-%dT%H:%M}"
    assert out[key]["status"] == "ok" and sum(c.endswith("/odds") for c in fake.calls) == 1
    again = tasks.task_prekickoff(session, st, NOW + timedelta(minutes=5), provider=provider(st, fake))
    assert again[key]["status"] == "skipped" and sum(c.endswith("/odds") for c in fake.calls) == 1
    later = tasks.task_prekickoff(session, st, NOW + timedelta(hours=3), provider=provider(st, fake))
    assert later == {"estado": "sin partidos en la ventana"}


# ----------------------------------------------------------------- liquidación y CLV
def _prediction(session, ev: Event, market: str, selection: str, line, odds: float, captured: datetime) -> Prediction:
    mv = session.scalar(select(ModelVersion.id).limit(1))
    if mv is None:
        from app.core.runtime_settings import get_thresholds
        from app.sports.football.service import football_service

        mv = football_service.state(session, ev.competition, get_thresholds(session)).model_version_id
    p = Prediction(event_id=ev.id, model_version_id=mv, market=market, line=line, selection=selection, p_model=0.5,
                   odds=odds, bookmaker="Pinnacle", odds_captured_at=captured, category="en_observacion")
    session.add(p)
    session.flush()
    return p


def test_settlement_generic_markets_and_clv(session, demo_loaded):
    comp = session.scalar(select(Competition).where(Competition.is_synthetic.is_(True)))
    k = NOW - timedelta(hours=3)
    Ingestor(session).ingest(ProviderBatch(source="csv:x", fetched_at=NOW, events=[NormalizedEvent(
        sport="football", competition_key=comp.key, competition_name=comp.name, kickoff_utc=k,
        home="Club Clv Uno", away="Club Clv Dos", source="csv:x", status="finished", home_score=1, away_score=1)]))
    ev = session.scalar(select(Event).join(Event.home_team).where(Event.kickoff_utc == k))
    captured = k - timedelta(hours=10)
    dnb = _prediction(session, ev, "dnb", "home", None, 2.0, captured)
    h2h = _prediction(session, ev, "1x2", "draw", None, 3.4, captured)
    tt = _prediction(session, ev, "team_totals", "away_over", 0.5, 1.6, captured)
    # Pinnacle movió el empate de 3.40 a 3.10 antes del inicio: CLV = 3.40/3.10 - 1
    session.add_all([
        OddsSnapshot(event_id=ev.id, bookmaker="Pinnacle", market="1x2", selection="draw", odds_decimal=3.10,
                     captured_at=k - timedelta(minutes=40), source="the_odds_api"),
        OddsSnapshot(event_id=ev.id, bookmaker="Pinnacle", market="1x2", selection="draw", odds_decimal=2.90,
                     captured_at=k + timedelta(minutes=30), source="the_odds_api"),  # en vivo: no cuenta
    ])
    session.flush()
    assert closing_odds_for(session, h2h, ev) == pytest.approx(3.10)
    assert closing_odds_for(session, dnb, ev) is None  # sin captura posterior ni cierre del CSV
    out = settle_pending(session)
    assert out["errors"] == [] and out["with_clv"] >= 1
    states = {p.market: session.scalar(select(Settlement.state).where(Settlement.prediction_id == p.id))
              for p in (dnb, h2h, tt)}
    assert states == {"dnb": "push", "1x2": "gana", "team_totals": "gana"}
    session.refresh(h2h)
    assert h2h.closing_odds == pytest.approx(3.10) and h2h.odds / h2h.closing_odds - 1 > 0


# ----------------------------------------------------------------- API
def test_scheduler_api_in_demo(db_engine):
    from app.main import app

    with TestClient(app) as c:
        st = c.get("/api/scheduler").json()
        assert st["enabled"] is False and st["running"] is False and "demo" in st["reason"].lower()
        assert {t["key"] for t in st["tasks"]} == {"diaria", "cuotas", "previa"} and "plan" in st
        assert c.post("/api/scheduler/run/cuotas").status_code == 400
        assert c.post("/api/scheduler/run/borrar_todo").status_code == 404
