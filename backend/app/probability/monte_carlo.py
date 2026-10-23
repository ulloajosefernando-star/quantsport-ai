"""Simulación Monte Carlo reproducible (semilla y número de simulaciones configurables).

Monte Carlo no aporta información nueva: reproduce el modelo que simula, con sus defectos.
Se usa para mercados derivados sin solución cerrada y para riesgo de banca, y se contrasta
con la solución analítica cuando existe.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.sports.football.scores import ScoreMatrix


def simulate_scores(sm: ScoreMatrix, n: int = 100_000, seed: int = 42) -> np.ndarray:
    """Devuelve un array (n, 2) de marcadores simulados desde la matriz del modelo."""
    rng = np.random.default_rng(seed)
    flat = (sm.matrix / sm.matrix.sum()).ravel()
    idx = rng.choice(flat.size, size=n, p=flat)
    h, a = np.unravel_index(idx, sm.matrix.shape)
    return np.column_stack([h, a])


@dataclass(frozen=True)
class BankrollSimulation:
    final_median: float
    final_p05: float
    final_p95: float
    prob_loss: float
    max_drawdown_median: float
    max_drawdown_p95: float
    longest_losing_streak_p95: int


def simulate_bankroll(
    p: float, odds: float, stake_fraction: float, n_bets: int = 500, n_paths: int = 5_000,
    bankroll: float = 1.0, seed: int = 42, p_sd: float = 0.0,
) -> BankrollSimulation:
    """Trayectorias de banca con apuestas independientes de igual p y cuota.

    ``p_sd`` añade incertidumbre sobre la probabilidad verdadera (se muestrea una p por
    trayectoria): con ventaja incierta, el riesgo real es mayor que con p conocida.
    """
    rng = np.random.default_rng(seed)
    p_true = np.clip(rng.normal(p, p_sd, size=(n_paths, 1)), 0.0, 1.0) if p_sd > 0 else np.full((n_paths, 1), p)
    wins = rng.random((n_paths, n_bets)) < p_true
    growth = np.where(wins, 1 + stake_fraction * (odds - 1), 1 - stake_fraction)
    paths = bankroll * np.cumprod(growth, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.full((n_paths, 1), bankroll), paths], axis=1), axis=1)[:, 1:]
    dd = np.max(1 - paths / peak, axis=1)
    streaks = np.zeros(n_paths, dtype=int)
    cur = np.zeros(n_paths, dtype=int)
    for j in range(n_bets):
        cur = np.where(~wins[:, j], cur + 1, 0)
        streaks = np.maximum(streaks, cur)
    final = paths[:, -1]
    return BankrollSimulation(
        final_median=float(np.median(final)), final_p05=float(np.quantile(final, 0.05)),
        final_p95=float(np.quantile(final, 0.95)), prob_loss=float(np.mean(final < bankroll)),
        max_drawdown_median=float(np.median(dd)), max_drawdown_p95=float(np.quantile(dd, 0.95)),
        longest_losing_streak_p95=int(np.quantile(streaks, 0.95)),
    )
