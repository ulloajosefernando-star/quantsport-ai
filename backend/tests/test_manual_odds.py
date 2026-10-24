"""Captura manual de cuotas de cualquier casa o fuente: se guardan y se evalúan, nada más."""

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
                    json={"bookmaker": "Casa X", "market": "1x2", "selection": "home", "odds": "+150",
                          "format": "american"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["bookmaker"] == "Casa X" and d["odds_decimal"] == pytest.approx(2.5)
    assert d["conversions"]["fractional"] == "3/2" and d["format"] == "american"
    a = d["evaluation"]
    assert a["quote"]["bookmaker"] == "Casa X" and a["quote"]["odds_decimal"] == pytest.approx(2.5)
    assert a["quote"]["original_value"] == "+150" and a["implied_probability"] == pytest.approx(0.4)
    assert a["data_quality"]["is_synthetic"] is True and a["ev"] is not None and a["min_odds"] is not None
    assert d["margin"]["available"] is False  # solo se capturó una selección
    snap = session.scalar(select(OddsSnapshot).where(OddsSnapshot.event_id == ev.id,
                                                     OddsSnapshot.source == MANUAL_SOURCE))
    assert snap.bookmaker == "Casa X" and snap.is_closing is False and snap.is_synthetic is True
    assert snap.original_format == "american" and snap.period == "ft"
    # Aparece en el detalle como una casa más, marcada con su origen
    rows = client.get(f"/api/events/{ev.id}").json()["odds_table"]
    assert any(x["bookmaker"] == "Casa X" and x["source"] == MANUAL_SOURCE for x in rows)


def test_manual_totals_needs_line(client, session):
    ev = next_event(session)
    url = f"/api/events/{ev.id}/manual-odds"
    base = {"market": "totals", "selection": "over", "odds": "1.95"}
    assert client.post(url, json=base).status_code == 400
    assert client.post(url, json={**base, "line": 2.3}).status_code == 400
    r = client.post(url, json={**base, "line": 2.5, "bookmaker": "Mi casa <script>"})
    assert r.status_code == 200 and r.json()["bookmaker"] == "Mi casa script"
    assert r.json()["evaluation"]["selection"]["label"].endswith("2.5")


@pytest.mark.parametrize("body", [
    {"market": "1x2", "selection": "over", "odds": "2.0"},          # selección de otro mercado
    {"market": "1x2", "selection": "home", "odds": "1.0"},          # cuota imposible
    {"market": "1x2", "selection": "home", "odds": "abc"},
    {"market": "1x2", "selection": "home", "odds": "50", "format": "american"},  # americana entre -100 y +100
    {"market": "1x2", "selection": "home", "odds": "120%"},
    {"market": "1x2", "selection": "home", "odds": "2.0", "bookmaker": "<>"},
    {"market": "corners_totals", "selection": "over", "odds": "2.0", "line": 9.3},  # línea fuera de paso
    {"market": "next_goal", "selection": "home", "odds": "2.0"},    # mercado en vivo
    {"market": "moneyline", "selection": "home", "odds": "2.0"},    # mercado de otro deporte
    {"market": "1x2", "selection": "home", "odds": "3/0"},          # división entre cero
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
    rows.append({"event_id": 1, "bookmaker": "Captura", "market": "1x2", "line": None, "selection": "home",
                 "odds": 9.0, "captured_at": now, "is_closing": False, "source": MANUAL_SOURCE})
    df = pd.DataFrame(rows)
    assert average_odds(df, "1x2", None, False)[1]["home"] == pytest.approx(2.0)
    assert sum(consensus_fair(df, "1x2", None, False)[1].values()) == pytest.approx(1.0)


def test_manual_full_market_gets_margin_and_unsupported_market_explains(client, session):
    ev = next_event(session)
    url = f"/api/events/{ev.id}/manual-odds"
    for sel, o in (("home", "2.10"), ("draw", "3.40")):
        client.post(url, json={"bookmaker": "Fuente Y", "market": "1x2", "selection": sel, "odds": o})
    r = client.post(url, json={"bookmaker": "Fuente Y", "market": "1x2", "selection": "away", "odds": "18/5"})
    d = r.json()
    assert d["margin"]["available"] and d["margin"]["overround"] == pytest.approx(1 / 2.1 + 1 / 3.4 + 1 / 4.6 - 1)
    assert d["evaluation"]["p_no_vig"] is not None
    r = client.post(url, json={"bookmaker": "Fuente Y", "market": "corners_totals", "selection": "over",
                               "line": 9.5, "odds": "1.90"})
    ev_row = r.json()["evaluation"]
    assert ev_row["ev"] is None and ev_row["category"] == "datos_insuficientes"
    assert "córners" in ev_row["reasons"][0]
