"""Captura manual de cuotas vistas en otra casa (p. ej. Playdoit): se guardan y se evalúan, nada más."""

from datetime import UTC, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Event, OddsSnapshot
from app.sports.football.data import MANUAL_SOURCE, average_odds, consensus_fair


@pytest.fixture()
def client(db_engine, demo_loaded):
    from app.main import app

    with TestClient(app) as c:
        yield c


def next_event(session) -> Event:
    return session.scalar(select(Event).where(Event.status == "scheduled", Event.kickoff_utc > datetime.now(UTC))
                          .order_by(Event.kickoff_utc))


def test_manual_odds_saved_and_assessed(client, session):
    ev = next_event(session)
    r = client.post(f"/api/events/{ev.id}/manual-odds",
                    json={"bookmaker": "Playdoit", "market": "1x2", "selection": "home", "odds": "+150",
                          "format": "american"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["bookmaker"] == "Playdoit" and d["odds_decimal"] == pytest.approx(2.5)
    a = d["assessment"]
    assert a["bookmaker"] == "Playdoit" and a["odds"] == pytest.approx(2.5) and a["n_offers"] == 1
    assert a["is_synthetic"] is True and "ev" in a and "min_odds" in a
    snap = session.scalar(select(OddsSnapshot).where(OddsSnapshot.event_id == ev.id,
                                                     OddsSnapshot.source == MANUAL_SOURCE))
    assert snap.bookmaker == "Playdoit" and snap.is_closing is False and snap.is_synthetic is True
    # Aparece en el detalle como una casa más, marcada con su origen
    rows = client.get(f"/api/events/{ev.id}").json()["odds_table"]
    assert any(x["bookmaker"] == "Playdoit" and x["source"] == MANUAL_SOURCE for x in rows)


def test_manual_totals_needs_line(client, session):
    ev = next_event(session)
    url = f"/api/events/{ev.id}/manual-odds"
    base = {"market": "totals", "selection": "over", "odds": "1.95"}
    assert client.post(url, json=base).status_code == 400
    assert client.post(url, json={**base, "line": 2.3}).status_code == 400
    r = client.post(url, json={**base, "line": 2.5, "bookmaker": "Mi casa <script>"})
    assert r.status_code == 200 and r.json()["bookmaker"] == "Mi casa script"
    assert r.json()["assessment"]["line"] == 2.5


@pytest.mark.parametrize("body", [
    {"market": "1x2", "selection": "over", "odds": "2.0"},          # selección de otro mercado
    {"market": "1x2", "selection": "home", "odds": "1.0"},          # cuota imposible
    {"market": "1x2", "selection": "home", "odds": "abc"},
    {"market": "1x2", "selection": "home", "odds": "50", "format": "american"},  # americana entre -100 y +100
    {"market": "1x2", "selection": "home", "odds": "2.0", "bookmaker": "<>"},
])
def test_manual_odds_rejects_bad_input(client, session, body):
    ev = next_event(session)
    assert client.post(f"/api/events/{ev.id}/manual-odds", json=body).status_code in (400, 422)


def test_manual_odds_rejects_finished_or_missing_event(client, session):
    done = session.scalar(select(Event).where(Event.status == "finished"))
    body = {"market": "1x2", "selection": "home", "odds": "2.0"}
    assert client.post(f"/api/events/{done.id}/manual-odds", json=body).status_code == 400
    assert client.post("/api/events/99999999/manual-odds", json=body).status_code == 404


def test_manual_odds_excluded_from_market_consensus():
    now = pd.Timestamp(datetime.now(UTC))
    rows = [{"event_id": 1, "bookmaker": "A", "market": "1x2", "line": None, "selection": s, "odds": o,
             "captured_at": now, "is_closing": False, "source": "the_odds_api"}
            for s, o in (("home", 2.0), ("draw", 3.4), ("away", 3.8))]
    rows.append({"event_id": 1, "bookmaker": "Playdoit", "market": "1x2", "line": None, "selection": "home",
                 "odds": 9.0, "captured_at": now, "is_closing": False, "source": MANUAL_SOURCE})
    df = pd.DataFrame(rows)
    assert average_odds(df, "1x2", None, False)[1]["home"] == pytest.approx(2.0)
    assert sum(consensus_fair(df, "1x2", None, False)[1].values()) == pytest.approx(1.0)
