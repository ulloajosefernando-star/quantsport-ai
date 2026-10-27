"""NBA: conector balldontlie, cuotas de The Odds API, modelo de puntos, validación y mercados.

Todo con datos SINTÉTICOS generados aquí y HTTP simulado (httpx.MockTransport).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import ValueThresholds, get_settings
from app.db.models import Competition, Event
from app.ingestion.store import Ingestor
from app.markets import registry
from app.providers.balldontlie import BallDontLieProvider, parse_games
from app.providers.base import NormalizedEvent, NormalizedOdds, ProviderBatch, ProviderNotConfigured
from app.providers.the_odds_api import parse_odds_response
from app.sports.nba import model as nm
from app.sports.nba.service import NbaMarketModel, gate, walk_forward

TEAMS = [f"Sim {c}" for c in "ABCDEFGHIJKLMNOPQRST"]


def simulate(n_days: int = 400, per_day: int = 6, seed: int = 3, start: str = "2024-10-20"):
    rng = np.random.default_rng(seed)
    att, dfn = rng.normal(0, 3.5, len(TEAMS)), rng.normal(0, 3.5, len(TEAMS))
    rows = []
    t0 = pd.Timestamp(start, tz="UTC") + pd.Timedelta(hours=23)
    for d in range(n_days):
        for _ in range(per_day):
            h, a = rng.choice(len(TEAMS), 2, replace=False)
            hp = round(113 + 1.6 + att[h] - dfn[a] + rng.normal(0, 11))
            ap = round(113 - 1.6 + att[a] - dfn[h] + rng.normal(0, 11))
            if hp == ap:
                hp += 1
            rows.append((len(rows) + 1, t0 + pd.Timedelta(days=d), TEAMS[h], TEAMS[a], hp, ap))
    return pd.DataFrame(rows, columns=["event_id", "kickoff", "home", "away", "home_pts", "away_pts"]), att, dfn


# ------------------------------------------------------------------ conector
def _game(i, home="Los Angeles Lakers", away="LA Clippers", status="Final", hs=110, vs=104, dt="2025-01-10T03:30:00Z",
          state="final"):
    return {"id": i, "date": "2025-01-09", "datetime": dt, "season": 2024, "status": status, "status_state": state,
            "home_team_score": hs, "visitor_team_score": vs, "postseason": False,
            "home_team": {"full_name": home}, "visitor_team": {"full_name": away}}


def test_parse_games_statuses_and_names():
    evs = parse_games([
        _game(1), _game(2, status="7:00 pm ET", hs=0, vs=0, state="scheduled", dt="2026-11-01T23:00:00Z"),
        _game(3, hs=0, vs=0),  # final sin marcador: se descarta
        {**_game(4), "datetime": None, "date": "2019-12-25"},
    ])
    assert [e.source_event_id for e in evs] == ["1", "2", "4"]
    e = evs[0]
    assert (e.sport, e.competition_key, e.status, e.home_score, e.away_score) == ("nba", "nba", "finished", 110, 104)
    assert e.away == "Los Angeles Clippers" and e.season == "2024-25"  # alias LA Clippers → nombre de The Odds API
    assert evs[1].status == "scheduled" and evs[1].home_score is None
    assert evs[2].kickoff_utc == datetime(2019, 12, 26, 0, 0, tzinfo=UTC)  # 19:00 hora del Este


def test_balldontlie_paginates_by_season_type_with_auth_header(tmp_path, db_engine):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.headers["Authorization"] == "clave-test"
        st, cursor = req.url.params["season_type"], req.url.params.get("cursor")
        if st == "regular" and not cursor:
            return httpx.Response(200, json={"data": [_game(1), _game(2)], "meta": {"next_cursor": 77}})
        if st == "regular":
            return httpx.Response(200, json={"data": [_game(3)], "meta": {}})
        if st == "playoffs":
            return httpx.Response(200, json={"data": [_game(4), _game(1)], "meta": {"next_cursor": None}})
        return httpx.Response(200, json={"data": [], "meta": {}})

    s = get_settings().model_copy(update={"balldontlie_api_key": "clave-test", "cache_dir": tmp_path})
    p = BallDontLieProvider(s, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    b = p.fetch_results("nba", "2024")
    assert sorted(e.source_event_id for e in b.events) == ["1", "2", "3", "4"]  # sin duplicados
    assert {r.url.params["season_type"] for r in seen} == {"regular", "ist", "playin", "playoffs"}
    assert all(r.url.params["seasons[]"] == "2024" and r.url.params["per_page"] == "100" for r in seen)
    assert "preseason" not in {r.url.params["season_type"] for r in seen}
    with pytest.raises(ProviderNotConfigured):
        _ = BallDontLieProvider(get_settings()).client


def test_the_odds_api_nba_markets():
    data = [{"id": "x", "commence_time": "2026-11-02T00:00:00Z", "home_team": "Boston Celtics",
             "away_team": "LA Clippers", "bookmakers": [{"title": "Pinnacle", "markets": [
                 {"key": "h2h", "last_update": "2026-11-01T12:00:00Z",
                  "outcomes": [{"name": "Boston Celtics", "price": 1.5}, {"name": "LA Clippers", "price": 2.7}]},
                 {"key": "spreads", "last_update": "2026-11-01T12:00:00Z",
                  "outcomes": [{"name": "Boston Celtics", "price": 1.91, "point": -6.5},
                               {"name": "LA Clippers", "price": 1.91, "point": 6.5}]},
                 {"key": "totals", "last_update": "2026-11-01T12:00:00Z",
                  "outcomes": [{"name": "Over", "price": 1.9, "point": 224.5},
                               {"name": "Under", "price": 1.9, "point": 224.5}]}]}]}]
    odds = parse_odds_response(data, "nba")
    got = {(o.market, o.selection, o.line) for o in odds}
    assert got == {("moneyline", "home", None), ("moneyline", "away", None), ("spread", "home", -6.5),
                   ("spread", "away", 6.5), ("totals", "over", 224.5), ("totals", "under", 224.5)}
    assert all(o.sport == "nba" and o.away == "Los Angeles Clippers" for o in odds)
    for o in odds:  # todas son válidas para el registro de mercados de NBA
        registry.validate("nba", o.market, o.selection, o.line, "ft")


# ------------------------------------------------------------------ modelo
def test_model_recovers_ratings_and_home_advantage():
    df, att, dfn = simulate()
    m = nm.fit(df, df["kickoff"].max() + pd.Timedelta(days=1))
    order = [m.teams.index(t) for t in TEAMS]
    assert np.corrcoef(att, m.attack[order])[0, 1] > 0.85
    assert np.corrcoef(dfn, m.defense[order])[0, 1] > 0.85
    assert 1.5 < m.home_adv < 5.0 and 10 < m.sd_margin < 20
    pr = m.predict(TEAMS[0], TEAMS[1])
    assert pr.se_margin > 0 and pr.mu_total == pytest.approx(pr.mu_home + pr.mu_away)
    k, p = nm.margin_distribution(pr.mu_margin, pr.sd_margin)
    assert 0 not in k and p.sum() == pytest.approx(1)
    assert nm.p_home_win(5.0, 12.0)[0] > nm.p_home_win(0.0, 12.0)[0] > nm.p_home_win(-5.0, 12.0)[0]
    assert nm.p_home_win(0.0, 12.0)[0] == pytest.approx(0.5, abs=1e-9)  # sin empates: simétrico


def test_fit_uses_only_past_games():
    df, *_ = simulate(n_days=60)
    cut = df["kickoff"].iloc[len(df) // 2]
    m = nm.fit(df, cut)
    assert m.n_games == int((df["kickoff"] < cut).sum())
    with pytest.raises(nm.InsufficientDataError):
        nm.fit(df.head(50), cut)


def _market_odds(df: pd.DataFrame, att_noise: float = 0.0) -> pd.DataFrame:
    """Cuotas de cierre sintéticas a partir de la probabilidad real de cada partido (con margen)."""
    rows = []
    for g in df.itertuples():
        p = float(np.clip(0.5 + (g.home_pts - g.away_pts) / 80 + att_noise, 0.05, 0.95))
        for sel, q in (("home", p), ("away", 1 - p)):
            rows.append({"event_id": g.event_id, "bookmaker": "Sim", "market": "moneyline", "line": None,
                         "selection": sel, "odds": 1 / (q * 1.04), "captured_at": g.kickoff, "is_closing": True,
                         "source": "sim", "is_synthetic": True})
    return pd.DataFrame(rows)


def test_walk_forward_beats_naive_baseline_and_gate_needs_market():
    df, *_ = simulate()
    wf = walk_forward(df, pd.DataFrame(), nm.NbaConfig(min_team_games=5), n_boot=100)
    assert wf["status"] == "ok" and wf["n_eval"] > 500
    sec = wf["moneyline"]
    assert sec["model"]["log_loss"] < sec["baseline_frecuencias"]["log_loss"]
    assert sec["vs_baseline_frecuencias"]["high"] < 0 and 6 < sec["margin_mae"] < 14
    assert "baseline_mercado" not in sec
    first_eval = min(wf["_oos"])  # nunca se predicen los primeros partidos (sin historia previa)
    assert first_eval > df["event_id"].min() and len(wf["_oos"]) == wf["n_eval"]
    g = gate({k: v for k, v in wf.items() if k != "_oos"}, ValueThresholds())
    assert g.status == "experimental" and any("cuotas guardadas" in r for r in g.reasons)
    assert g.market_status("spread", -4.5) == "experimental"


def test_gate_blocks_when_market_is_better():
    df, *_ = simulate()
    # Un "mercado" que conoce el resultado es imbatible: el modelo debe quedar bloqueado
    wf = walk_forward(df, _market_odds(df), nm.NbaConfig(min_team_games=5), n_boot=100)
    assert wf["moneyline"]["baseline_mercado"]["n"] > 300
    assert gate(wf, ValueThresholds()).status == "blocked"


# ------------------------------------------------------------------ mercados
def test_market_model_probabilities_and_push():
    pred = nm.NbaPrediction("A", "B", mu_home=115.0, mu_away=110.0, sd_margin=12.0, sd_total=18.0, sd_team=11.0,
                            se_margin=1.0, se_total=1.5)
    mm = NbaMarketModel(pred)
    ml = registry.get("nba", "moneyline")
    ph = mm.selection_model(ml, "home", None).dist_fn(2.0).p_full_win
    pa = mm.selection_model(ml, "away", None).dist_fn(2.0).p_full_win
    assert ph + pa == pytest.approx(1) and ph > 0.6
    sp = registry.get("nba", "spread")
    d = mm.selection_model(sp, "home", -5.0).dist_fn(1.91)  # línea entera: puede haber devolución
    assert d.p_push > 0.02 and not mm.selection_model(sp, "home", -5.0).binary
    half = mm.selection_model(sp, "home", -4.5)
    assert half.binary and half.dist_fn(1.91).p_push == 0
    assert half.p_samples is not None and half.p_samples.shape == (200,)
    tot = registry.get("nba", "totals")
    over = mm.selection_model(tot, "over", 224.5).dist_fn(1.9).p_full_win
    assert over == pytest.approx(0.5 + (225 - 224.5) / 18 * 0.4, abs=0.03)
    tt = mm.selection_model(registry.get("nba", "team_totals"), "home_over", 114.5).dist_fn(1.9).p_full_win
    assert 0.5 < tt < 0.56
    assert mm.support(registry.get("nba", "period_result"), "q1", False)
    assert mm.support(ml, "ft", True)  # en vivo: sin modelo
    assert mm.market_status("moneyline", None) == "experimental"


# ------------------------------------------------------------------ integración
@pytest.fixture(scope="module")
def nba_loaded(db_engine):
    from app.db.session import session_scope

    df, *_ = simulate(n_days=300, seed=5, start=str((datetime.now(UTC) - timedelta(days=301)).date()))
    batch = ProviderBatch(source="sim-nba", fetched_at=datetime.now(UTC))
    for g in df.itertuples():
        batch.events.append(NormalizedEvent(
            sport="nba", competition_key="nba", competition_name="NBA", kickoff_utc=g.kickoff.to_pydatetime(),
            home=g.home, away=g.away, source="sim-nba", status="finished", home_score=g.home_pts,
            away_score=g.away_pts, is_synthetic=True, source_event_id=str(g.event_id)))
    kick = datetime.now(UTC) + timedelta(days=1)
    batch.events.append(NormalizedEvent(sport="nba", competition_key="nba", competition_name="NBA", kickoff_utc=kick,
                                        home=TEAMS[0], away=TEAMS[1], source="sim-nba", is_synthetic=True))
    now = datetime.now(UTC)
    for m, sel, line, o in (("moneyline", "home", None, 1.7), ("moneyline", "away", None, 2.2),
                            ("spread", "home", -3.5, 1.91), ("spread", "away", 3.5, 1.91),
                            ("totals", "over", 226.5, 1.9), ("totals", "under", 226.5, 1.9)):
        batch.odds.append(NormalizedOdds(sport="nba", competition_key="nba", kickoff_utc=kick, home=TEAMS[0],
                                         away=TEAMS[1], bookmaker="Sim", market=m, selection=sel, line=line,
                                         odds_decimal=o, captured_at=now, source="sim-nba", is_synthetic=True))
    with session_scope() as s:
        Ingestor(s).ingest(batch)
    return kick


@pytest.fixture()
def client(db_engine, demo_loaded, nba_loaded):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_nba_board_results_and_performance(client, session):
    board = client.get("/api/markets/board", params={"sport": "nba"}).json()
    rows = [s | {"mk": m["market"]["key"]} for m in board["markets"] for s in m["selections"]]
    assert {r["mk"] for r in rows} >= {"moneyline", "spread", "totals"}
    ml = [r for r in rows if r["mk"] == "moneyline"]
    assert all(r["p_model"] is not None and r["ev"] is not None for r in ml)
    assert sum(r["p_model"] for r in ml) == pytest.approx(1, abs=1e-6)
    assert all(r["category"] not in ("candidato_valor",) for r in rows)  # experimental: nunca candidato

    res = client.get("/api/results", params={"competition": "nba"}).json()
    assert res["competition"]["sport"] == "nba" and res["selections"] == ["home", "away"]
    assert res["summary"]["draw"] == 0 and res["summary"]["over_2_5"] is None
    withm = [r for r in res["items"] if r["model_1x2"]]
    assert withm and all(len(r["model_1x2"]) == 2 and r["model_margin"] is not None for r in withm)
    assert res["summary"]["model_vs_market"]["n_model"] > 0

    perf = client.get("/api/performance").json()
    nba = perf["nba"][0]
    assert nba["model_status"] == "experimental" and nba["walkforward"]["status"] == "ok"
    assert "_oos" not in json.dumps(perf)
    assert all(c["competition_key"] != "nba" for c in perf["competitions"])  # fuera del bloque de fútbol
    assert session.scalar(select(Competition).where(Competition.key == "nba")).sport.key == "nba"
    assert session.scalar(select(Event).join(Competition).where(Competition.key == "nba", Event.status == "scheduled"))


# ------------------------------------------------------------------ programador
def test_scheduler_nba_daily_free_and_one_odds_capture_per_day(db_engine, session, tmp_path):
    from app.scheduler import tasks

    calls = []

    def bdl(req: httpx.Request) -> httpx.Response:
        calls.append(("bdl", str(req.url)))
        return httpx.Response(200, json={"data": [_game(900001, home="Sim A", away="Sim B")], "meta": {}})

    def odds_api(req: httpx.Request) -> httpx.Response:
        calls.append(("odds", req.url.path))
        if req.url.path == "/v4/sports":
            return httpx.Response(200, json=[{"key": "basketball_nba", "active": True}])
        return httpx.Response(200, json=[], headers={"x-requests-last": "3", "x-requests-remaining": "400"})

    s = get_settings().model_copy(update={
        "data_mode": "real", "scheduler_competitions": "nba", "balldontlie_api_key": "k", "the_odds_api_key": "k",
        "cache_dir": tmp_path, "scheduler_prekickoff_minutes": 45})
    nba = BallDontLieProvider(s, transport=httpx.MockTransport(bdl), sleep=lambda _: None)
    from app.providers.the_odds_api import TheOddsApiProvider

    oa = TheOddsApiProvider(s, transport=httpx.MockTransport(odds_api))
    out = tasks.task_daily(session, s, provider=oa, nba=nba)
    assert "nba:partidos" in out and isinstance(out["nba:partidos"], dict)
    assert not [c for c in calls if c[0] == "odds"]  # la diaria de NBA no gasta créditos
    assert "nba" not in str(tasks.task_odds(session, s, provider=oa))  # NBA no entra en las cuotas de horario

    now = datetime.now(UTC)
    kicks = [now + timedelta(minutes=40), now + timedelta(minutes=95)]
    for i, k in enumerate(kicks):
        Ingestor(session).ingest(ProviderBatch(source="sim", fetched_at=now, events=[NormalizedEvent(
            sport="nba", competition_key="nba", competition_name="NBA", kickoff_utc=k, home=TEAMS[2 + 2 * i],
            away=TEAMS[3 + 2 * i], source="sim")]))
    session.commit()
    tasks.task_prekickoff(session, s, now=now, provider=oa)
    tasks.task_prekickoff(session, s, now=now + timedelta(minutes=55), provider=oa)  # segundo partido del día
    odds_calls = [c for c in calls if c == ("odds", "/v4/sports/basketball_nba/odds")]
    assert len(odds_calls) == 1  # una sola captura al día
    assert tasks.odds_call_cost(s, "nba") == 3 and tasks.monthly_plan(session, s)["nba"] == 90
