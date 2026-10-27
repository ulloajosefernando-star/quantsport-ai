"""Mejoras del modelo de fútbol: localía por equipo, torneos cortos, Liguilla, empates y mercado.

Todos los datos son SINTÉTICOS (generados aquí con parámetros conocidos); ninguna prueba usa APIs reales.
"""

import numpy as np
import pandas as pd

from app.backtesting import football_experiments as fx
from app.backtesting import football_walkforward as wf
from app.calibration import quality_gate
from app.core.config import ValueThresholds
from app.sports.football import adjust
from app.sports.football import dixon_coles as dc
from app.sports.football.scores import probs_1x2, probs_btts, probs_totals, score_matrix


def league(n_teams=10, rounds=6, seed=5, altitude=0.35, draw_boost=0.0, two_leg=False):
    """Liga SINTÉTICA: el equipo A juega en altura (más localía) y, opcionalmente, hay más empates de los
    que da Poisson y cruces de ida y vuelta al final de cada torneo."""
    rng = np.random.default_rng(seed)
    att, dfn = rng.normal(0, 0.25, n_teams), rng.normal(0, 0.25, n_teams)
    teams = [f"Equipo {chr(65 + i)}" for i in range(n_teams)]
    rows, k = [], 0
    t = pd.Timestamp("2021-01-10", tz="UTC")

    def play(i, j, when, extra=0.0):
        nonlocal k
        lam = np.exp(0.15 + 0.25 + (altitude if i == 0 else 0.0) + extra + att[i] - dfn[j])
        mu = np.exp(0.15 + att[j] - dfn[i])
        sm = score_matrix(lam, mu).matrix.copy()
        sm[np.eye(len(sm), dtype=bool)] *= np.exp(draw_boost)
        sm /= sm.sum()
        cell = rng.choice(sm.size, p=sm.ravel())
        rows.append({"event_id": k, "kickoff": when, "home": teams[i], "away": teams[j],
                     "home_goals": cell // len(sm), "away_goals": cell % len(sm), "status": "finished",
                     "truth": [float(sm[np.tril_indices(len(sm), -1)].sum()), float(np.trace(sm)),
                               float(sm[np.triu_indices(len(sm), 1)].sum())]})
        k += 1

    for _ in range(rounds):
        for i in range(n_teams):
            for j in range(n_teams):
                if i != j:
                    play(i, j, t)
                    t += pd.Timedelta(hours=40)
        if two_leg:  # "Liguilla": ida y vuelta a 3 días; el local de la vuelta tiene menos ventaja
            for i, j in ((1, 2), (3, 4)):
                play(i, j, t, extra=0.0)
                play(j, i, t + pd.Timedelta(days=3), extra=-0.2)
            t += pd.Timedelta(days=5)
    return pd.DataFrame(rows), teams


def test_team_home_advantage_finds_altitude():
    df, teams = league()
    base = dc.fit(df, df["kickoff"].max() + pd.Timedelta(days=1), dc.DixonColesConfig(min_matches=50),
                  compute_uncertainty=False)
    m = dc.fit(df, df["kickoff"].max() + pd.Timedelta(days=1),
               dc.DixonColesConfig(min_matches=50, team_home=True), compute_uncertainty=False)
    assert m.home_dev is not None and np.argmax(m.home_dev) == teams.index("Equipo A")
    assert m.home_advantage("Equipo A") > base.home_advantage("Equipo A")
    assert m.metadata()["team_home_advantage"]["Equipo A"] > 1.3
    # la predicción del local en altura sube respecto al modelo con una sola localía
    assert m.expected_goals("Equipo A", "Equipo B")[0] > base.expected_goals("Equipo A", "Equipo B")[0]
    # el muestreo con incertidumbre funciona con los parámetros nuevos
    m2 = dc.fit(df.tail(300), df["kickoff"].max() + pd.Timedelta(days=1),
                dc.DixonColesConfig(min_matches=50, team_home=True, knockout=True))
    ps = m2.sample_predictions("Equipo A", "Equipo B", lambda sm: probs_1x2(sm)["home"], n=20)
    assert ps.shape == (20,) and np.all((ps > 0) & (ps < 1))


def test_two_leg_detection_and_knockout_adjustment():
    df, _ = league(rounds=6, two_leg=True, altitude=0.0)
    flags = dc.flag_two_leg(df)
    assert flags.sum() == 6 * 4  # 2 cruces de ida y vuelta por torneo
    m = dc.fit(df, df["kickoff"].max() + pd.Timedelta(days=1), dc.DixonColesConfig(min_matches=50, knockout=True),
               compute_uncertainty=False)
    assert m.metadata()["knockout_home_adjustment"] < 1.0
    lam_ko, _ = m.expected_goals("Equipo B", "Equipo C", knockout=True)
    lam, _ = m.expected_goals("Equipo B", "Equipo C")
    assert lam_ko < lam


def test_tournament_decay_downweights_previous_tournaments():
    df, _ = league()
    as_of = df["kickoff"].max() + pd.Timedelta(days=1)
    a = dc.fit(df, as_of, dc.DixonColesConfig(min_matches=50), compute_uncertainty=False)
    b = dc.fit(df, as_of, dc.DixonColesConfig(min_matches=50, tournament_decay=0.6), compute_uncertainty=False)
    assert b.effective_n < a.effective_n
    ids = dc.tournament_id(pd.Series(pd.to_datetime(["2024-06-30", "2024-07-01", "2025-01-02"], utc=True)))
    assert list(np.diff(ids)) == [1, 1]


def test_adjust_matrix_is_coherent():
    sm = score_matrix(1.4, 1.1, -0.05)
    adj = adjust.Adjustment(draw_delta=0.2, model_weight=0.4, market_weight=0.8, n_blend=500)
    m3 = np.array([0.40, 0.30, 0.30])
    out = adjust.adjust_matrix(sm, adj, m3)
    p = probs_1x2(sm)
    q = adjust.class_probs(np.array([p["home"], p["draw"], p["away"]]), adj, m3)
    p2 = probs_1x2(out)
    assert np.allclose([p2["home"], p2["draw"], p2["away"]], q, atol=1e-6)
    assert abs(out.matrix.sum() - sm.matrix.sum()) < 1e-9
    # los mercados derivados salen de la misma matriz reescalada
    split = adjust.class_split(sm)
    raw = split["p"] / split["p"].sum()
    assert abs(adjust.derived(raw, q, split["over"])[0] - probs_totals(out, 2.5)["over"]) < 1e-6
    assert abs(adjust.derived(raw, q, split["btts"])[0] - probs_btts(out)["yes"]) < 1e-6
    # sin cuotas solo se aplica el ajuste de empates
    only_draw = probs_1x2(adjust.adjust_matrix(sm, adj, None))
    assert only_draw["draw"] > p["draw"]


def test_draw_fit_recovers_missing_draws():
    df, _ = league(rounds=10, draw_boost=0.3, altitude=0.0)
    out = wf.run(df, pd.DataFrame(), dc.DixonColesConfig(min_matches=50, min_team_matches=5),
                 wf.WalkForwardConfig(min_train=200, refit_days=30, bootstrap=50, draw_adjust=True))
    a = out["adjustment"]
    assert a["n_draw"] >= adjust.MIN_ROWS_DRAW and a["draw_delta"] > 0.1
    assert out["1x2"]["model"]["log_loss"] < out["1x2"]["model_raw"]["log_loss"]


def test_blend_weights_follow_the_better_source():
    rng = np.random.default_rng(1)
    n = 1500
    truth = rng.dirichlet([6, 3, 4], n)
    y = np.array([rng.choice(3, p=t) for t in truth])
    noisy = truth * np.exp(rng.normal(0, 0.5, (n, 3)))
    noisy /= noisy.sum(axis=1, keepdims=True)
    a = adjust.fit(noisy, truth, y, draw=False, blend=True)  # mercado = verdad, modelo con ruido
    assert a.market_weight > a.model_weight
    b = adjust.fit(truth, noisy, y, draw=False, blend=True)  # al revés
    assert b.model_weight > b.market_weight
    few = adjust.fit(truth[:100], noisy[:100], y[:100], draw=False, blend=True)
    assert not few.blends  # con pocas cuotas no se combina


def _market_odds(df: pd.DataFrame, noise: float, seed: int = 2) -> pd.DataFrame:
    """Cuotas de cierre SINTÉTICAS alrededor de la probabilidad verdadera, con 5 % de margen."""
    rng = np.random.default_rng(seed)
    rows = []
    for r in df.itertuples():
        p = np.array(r.truth) * np.exp(rng.normal(0, noise, 3))
        p /= p.sum()
        for sel, pi in zip(("home", "draw", "away"), p, strict=True):
            rows.append({"event_id": r.event_id, "bookmaker": "Media", "market": "1x2", "line": None,
                         "selection": sel, "odds": 1 / (pi * 1.05), "captured_at": r.kickoff, "is_closing": True,
                         "source": "csv", "is_synthetic": True})
    return pd.DataFrame(rows)


def test_walkforward_blend_and_gate_require_clear_improvement():
    df, _ = league(rounds=10)
    odds = _market_odds(df, noise=0.02)  # mercado casi perfecto
    out = wf.run(df, odds, dc.DixonColesConfig(min_matches=50, min_team_matches=5),
                 wf.WalkForwardConfig(min_train=200, refit_days=30, bootstrap=100, market_blend=True))
    a = out["adjustment"]
    assert a["n_blend"] >= adjust.MIN_ROWS_BLEND and a["market_weight"] > a["model_weight"]
    oos = out["_oos"]
    assert len(oos) == out["n_eval"]
    th = ValueThresholds()
    gate = quality_gate.evaluate(out, th)
    checks = {c["check"]: c for c in gate.checks if c["market"] == "1x2"}
    assert "aporta_sobre_mercado" in checks
    # con un mercado casi perfecto, combinar no lo mejora con claridad: no pasa a producción
    assert gate.markets["1x2"]["status"] != quality_gate.PRODUCTION


def test_experiments_table():
    df, _ = league(rounds=8)
    odds = _market_odds(df, noise=0.3)
    variants = [v for v in fx.VARIANTS if v.key in ("base", "localia_equipo", "ajustes_mercado")]
    rows = fx.run(df, odds, variants, base_model={"min_matches": 50, "min_team_matches": 5},
                  base_wf={"min_train": 200, "refit_days": 30, "bootstrap": 30})
    assert [r["key"] for r in rows] == ["base", "localia_equipo", "ajustes_mercado"]
    assert all(r["status"] == "ok" for r in rows)
    # la liga tiene un equipo en altura: la localía por equipo mejora el log loss
    assert rows[1]["log_loss"] < rows[0]["log_loss"]
    md = fx.markdown(rows, "Liga sintética", len(df))
    assert "Localía propia" in md and "peso mercado" in md


def test_service_applies_competition_profile(demo_loaded, session, monkeypatch):
    from sqlalchemy import select

    from app.core.runtime_settings import get_thresholds
    from app.db.models import Competition, Event
    from app.providers.demo import COMPETITION_KEY
    from app.sports.football import service as svc

    monkeypatch.setitem(svc.COMPETITION_PROFILES, COMPETITION_KEY, {
        "model": {"team_home": True, "tournament_decay": 0.8, "knockout": True},
        "wf": {"draw_adjust": True, "market_blend": True}})
    th = get_thresholds(session)
    try:
        comp = session.scalar(select(Competition).where(Competition.key == COMPETITION_KEY))
        st = svc.football_service.state(session, comp, th, force=True)
        assert st.model is not None and st.model.home_dev is not None
        assert st.walkforward["status"] == "ok" and "adjustment" in st.walkforward
        ev = session.scalars(select(Event).where(Event.competition_id == comp.id, Event.status == "scheduled")
                             .order_by(Event.kickoff_utc)).first()
        out = svc.football_service.analyze_event(session, ev, th)
        p = out["probabilities"]["1x2"]
        assert abs(p["home"] + p["draw"] + p["away"] - 1) < 1e-6
        assert any(f["factor"] == "Ventaja de localía" for f in out["factors"])
        em = svc.football_service.market_model(session, ev, th)
        assert em.model is not None
    finally:
        svc.football_service.invalidate()
