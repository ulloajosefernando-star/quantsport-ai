import math

import numpy as np
import pytest

from app.bankroll.kelly import kelly_binary, kelly_states, suggest_stake
from app.probability import ev as evm
from app.probability.margin import compare_methods, overround, power, proportional, shin
from app.probability.odds import (
    InvalidOddsError,
    american_to_decimal,
    decimal_to_american,
    decimal_to_fractional,
    fair_odds,
    fractional_to_decimal,
    implied_probability,
    to_decimal,
)
from app.probability.settlement import binary_distribution, handicap_return, total_return


# ------------------------------------------------------------------ cuotas
@pytest.mark.parametrize("american,decimal", [(150, 2.5), (-200, 1.5), (100, 2.0), (-100, 2.0), (-110, 1.909091)])
def test_american_roundtrip(american, decimal):
    assert american_to_decimal(american) == pytest.approx(decimal, abs=1e-6)
    if american not in (-100,):
        assert decimal_to_american(american_to_decimal(american)) == pytest.approx(american, abs=1e-4)


@pytest.mark.parametrize("frac,decimal", [("5/2", 3.5), ("1/4", 1.25), ("evens".replace("evens", "1/1"), 2.0)])
def test_fractional(frac, decimal):
    assert fractional_to_decimal(frac) == pytest.approx(decimal)
    assert fractional_to_decimal(decimal_to_fractional(decimal)) == pytest.approx(decimal)


@pytest.mark.parametrize("bad", [1.0, 0.5, -2, float("nan")])
def test_invalid_decimal(bad):
    with pytest.raises(InvalidOddsError):
        implied_probability(bad)


def test_invalid_american():
    with pytest.raises(InvalidOddsError):
        american_to_decimal(50)


def test_to_decimal_formats():
    assert to_decimal("+150", "american") == pytest.approx(2.5)
    assert to_decimal("5/2", "fractional") == pytest.approx(3.5)
    assert to_decimal(2.1) == 2.1


def test_implied_and_fair():
    assert implied_probability(2.0) == 0.5
    assert fair_odds(0.25) == 4.0


# ------------------------------------------------------------------ margen
def test_overround_and_proportional():
    odds = [2.10, 3.40, 3.60]
    assert overround(odds) == pytest.approx(1 / 2.1 + 1 / 3.4 + 1 / 3.6 - 1)
    p = proportional(odds)
    assert sum(p) == pytest.approx(1.0)
    assert p[0] == pytest.approx((1 / 2.1) / (1 / 2.1 + 1 / 3.4 + 1 / 3.6))


@pytest.mark.parametrize("method", [power, shin])
def test_alternative_methods_sum_to_one(method):
    odds = [1.50, 4.20, 7.00]
    p = method(odds)
    assert sum(p) == pytest.approx(1.0, abs=1e-9)
    # Ambos asignan relativamente más margen al longshot que el proporcional
    assert p[2] < proportional(odds)[2]
    assert p[0] > proportional(odds)[0]


def test_fair_market_no_margin_is_identity():
    odds = [2.0, 4.0, 4.0]
    for m in (proportional, power, shin):
        assert m(odds) == pytest.approx([0.5, 0.25, 0.25])


def test_compare_methods_reports_disagreement():
    cmp = compare_methods([1.30, 5.50, 10.0])
    assert set(cmp.by_method) == {"proportional", "power", "shin"}
    assert cmp.max_disagreement > 0
    assert "no equivalen" in cmp.note


# ------------------------------------------------------------------ EV
def test_ev_spec_example():
    # Ejemplo del enunciado: p=0.55, d=2.00 => EV = +0.10
    assert evm.ev_binary(0.55, 2.0) == pytest.approx(0.10)
    assert evm.ev_percent(0.55, 2.0) == pytest.approx(10.0)
    # forma equivalente
    assert pytest.approx(evm.ev_binary(0.55, 2.0)) == 0.55 * (2.0 - 1) - (1 - 0.55)


def test_min_odds_and_edge():
    assert evm.min_odds_for_target(0.5, 0.05) == pytest.approx(2.1)
    assert evm.min_odds_for_target(0.4, 0.0) == pytest.approx(2.5)
    assert evm.edge(0.55, 0.50) == pytest.approx(0.05)


def test_min_odds_states_consistent_with_distribution():
    # DNB: p_win 0.45, p_push 0.30 => cuota justa = 1 + p_loss/p_win
    d = evm.min_odds_for_target_states(0.45, 0.30, 0.0)
    assert d == pytest.approx(1 + 0.25 / 0.45)


def test_ev_interval_from_samples():
    rng = np.random.default_rng(0)
    iv = evm.ev_interval_from_samples(rng.normal(0.55, 0.03, 5000), 2.0)
    assert iv.low < iv.point < iv.high
    assert 0.9 < iv.prob_positive <= 1.0


# ------------------------------------------------------------------ liquidaciones
@pytest.mark.parametrize("diff,line,expected_state,expected_ret", [
    (1, -0.5, "gana", 1.0), (0, -0.5, "pierde", -1.0),
    (1, -1.0, "push", 0.0), (2, -1.0, "gana", 1.0),
    (1, -0.75, "medio_gana", 0.5), (0, -0.25, "medio_pierde", -0.5),
    (0, 0.25, "medio_gana", 0.5), (-1, 0.75, "medio_pierde", -0.5),
    (0, 0.0, "push", 0.0),
])
def test_asian_handicap(diff, line, expected_state, expected_ret):
    state, ret = handicap_return(diff, line, 2.0)
    assert state == expected_state
    assert ret == pytest.approx(expected_ret)


def test_invalid_line():
    with pytest.raises(ValueError):
        handicap_return(0, 0.3, 2.0)


@pytest.mark.parametrize("total,line,side,state", [
    (3, 2.5, "over", "gana"), (2, 2.5, "over", "pierde"), (2, 2.0, "under", "push"),
    (2, 2.25, "under", "medio_gana"), (3, 2.75, "over", "medio_gana"), (2, 2.25, "over", "medio_pierde"),
])
def test_totals(total, line, side, state):
    assert total_return(total, line, 1.9, side)[0] == state


def test_binary_distribution_matches_formula():
    dist = binary_distribution(0.55, 2.0)
    assert dist.expected_value == pytest.approx(evm.ev_binary(0.55, 2.0))
    with pytest.raises(ValueError):
        from app.probability.settlement import SettlementDistribution, SettlementState

        SettlementDistribution((SettlementState("gana", 0.5, 1.0),))


# ------------------------------------------------------------------ Kelly
def test_kelly_binary():
    assert kelly_binary(0.55, 2.0) == pytest.approx(0.10)  # (1.1-1)/(1)
    assert kelly_binary(0.40, 2.0) == 0.0  # EV negativo => 0
    assert kelly_binary(0.30, 4.0) == pytest.approx((0.30 * 4 - 1) / 3)


def test_kelly_states_matches_binary():
    dist = binary_distribution(0.55, 2.0)
    assert kelly_states(dist) == pytest.approx(kelly_binary(0.55, 2.0), abs=1e-4)


def test_fractional_kelly_capped():
    st = suggest_stake(0.20, 1000.0, kelly_fraction=0.25, max_fraction_per_selection=0.02)
    assert st.fraction_used == pytest.approx(0.05)
    assert st.capped_fraction == 0.02 and st.capped_by_limit
    assert st.stake_amount == 20.0
    assert "no es una orden" in st.warning.lower()


def test_kelly_bounds():
    with pytest.raises(ValueError):
        suggest_stake(0.1, 100, kelly_fraction=1.5)
    assert math.isfinite(kelly_binary(0.99, 1.01))
