"""Pantalla de resultados pasados y panel servido por el backend."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Competition, Event
from app.providers.demo import COMPETITION_KEY

DEMO = {"competition": COMPETITION_KEY}


@pytest.fixture()
def client(db_engine, demo_loaded):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_results_lists_finished_matches_with_market_and_oos_model(client, session):
    d = client.get("/api/results", params=DEMO).json()
    finished = session.scalars(select(Event).join(Competition).where(
        Event.status == "finished", Competition.key == COMPETITION_KEY)).all()
    assert d["competition"]["is_synthetic"] is True and d["total"] == len(finished)
    assert len(d["items"]) == 50 and d["teams"]
    kicks = [r["kickoff_utc"] for r in d["items"]]
    assert kicks == sorted(kicks, reverse=True)  # lo más reciente primero
    r = d["items"][0]
    assert r["outcome"] == ("home" if r["home_score"] > r["away_score"] else
                            "draw" if r["home_score"] == r["away_score"] else "away")
    assert sum(r["market_1x2"]) == pytest.approx(1, abs=1e-3)
    assert sum(r["model_1x2"]) == pytest.approx(1, abs=1e-3)
    assert r["model_hit"] == (r["model_pick"] == r["outcome"])
    s = d["summary"]
    assert s["home_win"] + s["draw"] + s["away_win"] == pytest.approx(1)
    # El modelo solo predice partidos con historia previa (walk-forward): nunca todos
    assert 0 < s["model_vs_market"]["n_model"] < s["n_matches"]
    assert s["model_vs_market"]["model_log_loss"] > 0


def test_results_filters_by_team_dates_and_pages(client):
    base = client.get("/api/results", params=DEMO).json()
    team = base["teams"][0]
    d = client.get("/api/results", params={**DEMO, "team": team}).json()
    assert d["total"] > 0 and all(team in (r["home"], r["away"]) for r in d["items"])
    t = d["summary"]["team"]
    assert t["won"] + t["drawn"] + t["lost"] == d["total"]
    first = base["items"][0]["kickoff_local"][:10]
    d = client.get("/api/results", params={**DEMO, "date_from": first, "date_to": first}).json()
    assert d["total"] >= 1 and all(r["kickoff_local"].startswith(first) for r in d["items"])
    p2 = client.get("/api/results", params={**DEMO, "page": 2}).json()
    assert p2["items"][0]["event_id"] not in {r["event_id"] for r in base["items"]}
    assert client.get("/api/results", params={"competition": "no_existe"}).json()["total"] == 0


def test_results_include_settled_predictions(client, session):
    from datetime import UTC, datetime

    from app.core.runtime_settings import get_thresholds
    from app.db.models import Prediction, Settlement
    from app.sports.football.service import football_service

    ev = session.scalars(select(Event).join(Competition).where(Event.status == "finished", Competition.key == COMPETITION_KEY)
                         .order_by(Event.kickoff_utc.desc())).first()
    st = football_service.state(session, ev.competition, get_thresholds(session))
    p = Prediction(event_id=ev.id, model_version_id=st.model_version_id, market="1x2", selection="home",
                   p_model=0.5, odds=2.2, bookmaker="Casa X", odds_captured_at=datetime.now(UTC), ev=0.1,
                   category="valor", closing_odds=2.0, is_synthetic=True)
    session.add(p)
    session.flush()
    won = ev.home_score > ev.away_score
    session.add(Settlement(prediction_id=p.id, state="gana" if won else "pierde", net_return=1.2 if won else -1.0))
    session.commit()
    try:
        d = client.get("/api/results", params=DEMO).json()
        row = next(r for r in d["items"] if r["event_id"] == ev.id)
        pr = row["predictions"][0]
        assert pr["label"] == ev.home_team.name and pr["clv"] == pytest.approx(0.1)
        assert pr["state"] == ("gana" if won else "pierde")
        assert d["summary"]["predictions"]["n"] >= 1
    finally:
        session.delete(session.scalar(select(Settlement).where(Settlement.prediction_id == p.id)))
        session.flush()
        session.delete(p)
        session.commit()


def test_panel_served_from_backend_without_leaking_files(tmp_path, monkeypatch):
    from app import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>panel</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secreto.txt").write_text("no")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    with TestClient(main.create_app()) as c:
        assert c.get("/resultados").text == "<html>panel</html>"
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/..%2Fsecreto.txt").text == "<html>panel</html>"
        assert c.get("/api/no-existe").status_code == 404
        assert c.get("/api/health").json()["status"] == "ok"
