from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.core.config import ValueThresholds
from app.probability.settlement import binary_distribution
from app.ranking.ranker import RankingFilters, rank
from app.value_engine.engine import Offer, SelectionInput, assess

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def selection(p=0.55, odds=2.0, age_min=5, p_sd=0.01, fair=0.50, offers=True):
    rng = np.random.default_rng(0)
    ps = np.clip(rng.normal(p, p_sd, 400), 0, 1)
    offs = [Offer("Operador X", odds, NOW - timedelta(minutes=age_min), "test", True)] if offers else []
    return SelectionInput(
        market="1x2", selection="home", line=None, label="Local", offers=offs, p_market_fair=fair,
        dist_fn=lambda d: binary_distribution(p, d), ev_samples_fn=lambda d: ps * d - 1,
    ), ps


def run(sel_ps, status="production", issues=None, th=None):
    sel, ps = sel_ps
    return assess(sel, th or ValueThresholds(), status, issues or [], 0.8, now=NOW, p_samples=ps)


def test_candidate_when_everything_passes():
    a = run(selection())
    assert a.category == "candidato_valor"
    assert a.ev == pytest.approx(0.55 * 2 - 1)
    assert a.fair_odds == pytest.approx(1 / 0.55)
    assert a.edge == pytest.approx(0.05)
    assert a.ev_ci90[0] > 0
    assert a.min_odds == pytest.approx((1 + ValueThresholds().target_ev) / 0.55)


def test_stale_odds():
    a = run(selection(age_min=500))
    assert a.category == "cuota_desactualizada"


def test_no_offers_is_analysis_only():
    a = run(selection(offers=False))
    assert a.category == "datos_insuficientes"


def test_negative_ev_is_no_value():
    a = run(selection(p=0.45))
    assert a.category == "sin_valor"


def test_unvalidated_model_never_candidate():
    a = run(selection(), status="experimental")
    assert a.category == "ventaja_no_confirmada"
    a = run(selection(), status="blocked")
    assert a.category == "ventaja_no_confirmada"


def test_high_uncertainty_rejected_despite_positive_ev():
    a = run(selection(p=0.53, p_sd=0.08))  # EV puntual +6% pero muy incierto
    assert a.ev > 0
    assert a.category in ("ventaja_no_confirmada", "en_observacion")
    assert a.category != "candidato_valor"


def test_data_issues_block():
    a = run(selection(), issues=["Datos desactualizados"])
    assert a.category == "datos_insuficientes"


def test_odds_out_of_range():
    th = ValueThresholds(max_odds=1.8)
    a = run(selection(), th=th)
    assert a.category == "sin_valor"


def test_ranking_orders_by_category_then_quality():
    rows = [
        {"category": "en_observacion", "quality_score": 0.9, "market": "1x2", "ev": 0.2, "odds": 2.0},
        {"category": "candidato_valor", "quality_score": 0.5, "market": "1x2", "ev": 0.05, "odds": 2.0},
        {"category": "candidato_valor", "quality_score": 0.7, "market": "btts", "ev": 0.04, "odds": 1.9},
        {"category": "sin_valor", "quality_score": 0.0, "market": "1x2", "ev": -0.1, "odds": 2.0},
    ]
    out = rank([dict(r) for r in rows], RankingFilters())
    assert [r["category"] for r in out] == ["candidato_valor", "candidato_valor", "en_observacion"]
    assert out[0]["quality_score"] == 0.7  # no se ordena solo por EV
    assert len(rank([dict(r) for r in rows], RankingFilters(market="btts"))) == 1
    assert len(rank([dict(r) for r in rows], RankingFilters(include_no_value=True))) == 4
