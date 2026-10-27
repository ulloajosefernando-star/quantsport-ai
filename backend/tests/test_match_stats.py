"""Estadística simple con resultados pasados (datos SINTÉTICOS de la demo)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Competition, Event
from app.providers.demo import COMPETITION_KEY
from app.reporting import match_stats as ms


@pytest.fixture()
def client(db_engine, demo_loaded):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_probabilities_average_home_and_away_records():
    lg = {"home": 0.45, "draw": 0.27, "away": 0.28, "over": 0.5, "btts": 0.5, "home_goals": 1.5, "away_goals": 1.1}
    home = {"n": 1000, "win": 0.8, "draw": 0.1, "loss": 0.1, "gf": 2.0, "ga": 0.5, "over": 0.6, "btts": 0.4}
    away = {"n": 1000, "win": 0.1, "draw": 0.3, "loss": 0.6, "gf": 0.8, "ga": 1.8, "over": 0.4, "btts": 0.5}
    p = ms.probabilities(home, away, lg)
    assert abs(p["1x2"]["home"] - 0.7) < 0.01 and abs(p["1x2"]["draw"] - 0.2) < 0.01
    assert abs(sum(p["1x2"].values()) - 1) < 1e-9 and abs(p["over_2_5"] - 0.5) < 0.01
    # sin partidos, la media de la liga
    empty = ms.probabilities({"n": 0}, {"n": 0}, lg)
    assert abs(empty["1x2"]["home"] - 0.45) < 1e-9


def test_backtest_uses_only_previous_matches():
    rng = np.random.default_rng(0)
    teams = [f"T{i}" for i in range(8)]
    rows, t, k = [], pd.Timestamp("2022-01-01", tz="UTC"), 0
    for _ in range(8):
        for h in teams:
            for a in teams:
                if h != a:
                    rows.append({"event_id": k, "kickoff": t, "home": h, "away": a,
                                 "home_goals": rng.poisson(1.5), "away_goals": rng.poisson(1.0)})
                    k += 1
                    t += pd.Timedelta(hours=30)
    df = pd.DataFrame(rows)
    out = ms.backtest(df, pd.DataFrame())
    assert out["status"] == "ok" and out["n"] > 300
    # cambiar un resultado futuro no altera la predicción de partidos anteriores
    df2 = df.copy()
    df2.loc[len(df2) - 1, "home_goals"] = 9
    assert ms.backtest(df2.iloc[:-1], pd.DataFrame())["1x2"] == ms.backtest(df.iloc[:-1], pd.DataFrame())["1x2"]


def test_event_stats_endpoint(client, session, demo_loaded):
    ev = session.scalars(select(Event).join(Competition).where(Competition.key == COMPETITION_KEY,
                                                             Event.status == "scheduled")
                         .order_by(Event.kickoff_utc)).first()
    d = client.get(f"/api/events/{ev.id}/stats").json()
    assert d["home"] == ev.home_team.name and len(d["form"]["home"]) == ms.N_FORM
    assert d["venue"]["home"]["n"] > 0 and abs(sum(d["probabilities"]["1x2"].values()) - 1) < 1e-9
    assert d["backtest"]["status"] == "ok" and "market" in d["backtest"]["1x2"]
    assert client.get("/api/events/999999/stats").status_code == 404
