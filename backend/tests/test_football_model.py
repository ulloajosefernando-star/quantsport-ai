import numpy as np
import pandas as pd
import pytest

from app.calibration.metrics import brier_score, expected_calibration_error, log_loss, reliability_curve
from app.probability.monte_carlo import simulate_bankroll, simulate_scores
from app.sports.football import dixon_coles as dc
from app.sports.football.scores import (
    handicap_distribution,
    probs_1x2,
    probs_btts,
    probs_totals,
    score_matrix,
    totals_distribution,
)


def synthetic_league(n_teams=12, rounds=2, seed=3):
    """Liga SINTÉTICA con fuerzas conocidas (solo para tests)."""
    rng = np.random.default_rng(seed)
    att, dfn = rng.normal(0, 0.3, n_teams), rng.normal(0, 0.3, n_teams)
    teams = [f"Equipo {chr(65 + i)}" for i in range(n_teams)]
    rows, t0 = [], pd.Timestamp("2025-01-01", tz="UTC")
    k = 0
    for _ in range(rounds):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                lam = np.exp(0.1 + 0.25 + att[i] - dfn[j])
                mu = np.exp(0.1 + att[j] - dfn[i])
                rows.append({"kickoff": t0 + pd.Timedelta(days=k // 6 * 4), "home": teams[i], "away": teams[j],
                             "home_goals": rng.poisson(lam), "away_goals": rng.poisson(mu), "event_id": k})
                k += 1
    return pd.DataFrame(rows), dict(zip(teams, att, strict=True)), dict(zip(teams, dfn, strict=True))


# ------------------------------------------------------------------ matriz
def test_matrix_mass_and_markets():
    sm = score_matrix(1.6, 1.1)
    assert sm.matrix.sum() + sm.tail_mass == pytest.approx(1.0)
    assert sm.tail_mass < 1e-5
    p = probs_1x2(sm)
    assert sum(p.values()) == pytest.approx(1.0)
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        t = probs_totals(sm, line)
        assert t["over"] + t["under"] == pytest.approx(1.0)
    assert probs_totals(sm, 0.5)["over"] > probs_totals(sm, 4.5)["over"]
    b = probs_btts(sm)
    # Con independencia (rho=0): P(BTTS) = (1-e^-λ)(1-e^-μ)
    assert b["yes"] == pytest.approx((1 - np.exp(-1.6)) * (1 - np.exp(-1.1)), abs=1e-6)


def test_dixon_coles_correction_changes_low_scores():
    base = score_matrix(1.2, 1.0, 0.0)
    dcm = score_matrix(1.2, 1.0, -0.1)
    assert dcm.matrix[0, 0] > base.matrix[0, 0]  # rho<0 aumenta 0-0 y 1-1
    assert dcm.matrix[1, 1] > base.matrix[1, 1]
    assert dcm.matrix[1, 0] < base.matrix[1, 0]
    assert dcm.matrix.sum() + dcm.tail_mass == pytest.approx(1.0, abs=1e-9)


def test_rho_out_of_bounds_rejected():
    with pytest.raises(ValueError):
        score_matrix(3.0, 3.0, 0.5)


def test_handicap_and_dnb_distributions_sum_to_one():
    sm = score_matrix(1.5, 1.0, -0.05)
    for line in (-1.0, -0.75, -0.5, -0.25, 0.0, 0.25):
        d = handicap_distribution(sm, "home", line, 1.95)
        assert sum(s.probability for s in d.states) == pytest.approx(1.0)
    dnb = handicap_distribution(sm, "home", 0.0, 1.8)
    p = probs_1x2(sm)
    assert dnb.p_push == pytest.approx(p["draw"])
    assert dnb.expected_value == pytest.approx(p["home"] * 0.8 - p["away"])


def test_totals_distribution_binary_line_matches_probs():
    sm = score_matrix(1.4, 1.2)
    d = totals_distribution(sm, "over", 2.5, 2.0)
    assert d.expected_value == pytest.approx(probs_totals(sm, 2.5)["over"] * 2 - 1)


def test_monte_carlo_matches_analytic():
    sm = score_matrix(1.5, 1.1, -0.05)
    sims = simulate_scores(sm, n=200_000, seed=1)
    p_home_mc = np.mean(sims[:, 0] > sims[:, 1])
    assert p_home_mc == pytest.approx(probs_1x2(sm)["home"], abs=0.005)
    # reproducible
    assert np.array_equal(simulate_scores(sm, 1000, seed=5), simulate_scores(sm, 1000, seed=5))


def test_bankroll_simulation_uncertainty_increases_risk():
    a = simulate_bankroll(0.55, 2.0, 0.02, n_bets=300, n_paths=2000, seed=1)
    b = simulate_bankroll(0.55, 2.0, 0.02, n_bets=300, n_paths=2000, seed=1, p_sd=0.05)
    assert b.prob_loss > a.prob_loss
    assert 0 <= a.max_drawdown_median <= 1


# ------------------------------------------------------------------ ajuste
def test_fit_recovers_strengths_and_home_advantage():
    df, att, dfn = synthetic_league()
    m = dc.fit(df, df["kickoff"].max() + pd.Timedelta(days=1), dc.DixonColesConfig(min_matches=50))
    assert m.converged
    est_att = np.array([m.attack[m.index[t]] for t in att])
    est_def = np.array([m.defence[m.index[t]] for t in dfn])
    assert np.corrcoef(est_att, list(att.values()))[0, 1] > 0.8
    assert np.corrcoef(est_def, list(dfn.values()))[0, 1] > 0.7
    assert 1.05 < np.exp(m.home_adv) < 1.6
    assert m.cov is not None
    samples = m.sample_predictions("Equipo A", "Equipo B", lambda sm: probs_1x2(sm)["home"], n=100)
    assert samples.std() > 0


def test_fit_uses_only_past_matches():
    df, _, _ = synthetic_league()
    cutoff = df["kickoff"].iloc[len(df) // 2]
    m = dc.fit(df, cutoff, dc.DixonColesConfig(min_matches=50), compute_uncertainty=False)
    assert pd.Timestamp(m.train_end) < cutoff
    assert m.n_matches == int((df["kickoff"] < cutoff).sum())


def test_insufficient_data():
    df, _, _ = synthetic_league()
    with pytest.raises(dc.InsufficientDataError):
        dc.fit(df.head(20), df["kickoff"].max(), dc.DixonColesConfig(min_matches=100))


def test_unknown_team():
    df, _, _ = synthetic_league()
    m = dc.fit(df, df["kickoff"].max() + pd.Timedelta(days=1), dc.DixonColesConfig(min_matches=50),
               compute_uncertainty=False)
    with pytest.raises(dc.UnknownTeamError):
        m.predict("Equipo Inexistente", "Equipo A")


# ------------------------------------------------------------------ calibración
def test_metrics_known_values():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert brier_score([0.5], [1]) == pytest.approx(0.25)
    assert log_loss([0.5], [1]) == pytest.approx(np.log(2))
    multi = np.array([[0.5, 0.3, 0.2]])
    assert brier_score(multi, [0]) == pytest.approx(0.25 + 0.09 + 0.04)
    assert log_loss(multi, [2]) == pytest.approx(-np.log(0.2))


def test_ece_perfect_vs_miscalibrated():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 20000)
    y = rng.random(20000) < p
    assert expected_calibration_error(p, y) < 0.02
    assert expected_calibration_error(np.clip(p + 0.15, 0, 1), y) > 0.1
    bins = reliability_curve(p, y)
    assert all(b.ci_low <= b.observed_rate <= b.ci_high for b in bins)
