"""Métricas de evaluación probabilística.

Las probabilidades se evalúan por su calidad (Brier, Log Loss) y calibración
(¿ocurren ~60% de los eventos a los que se asigna 60%?). La exactitud es secundaria.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
from sklearn.metrics import roc_auc_score

EPS = 1e-12


def _as2d(probs: np.ndarray | list) -> np.ndarray:
    arr = np.asarray(probs, dtype=float)
    return arr[:, None] if arr.ndim == 1 else arr


def brier_score(probs: np.ndarray | list, outcomes: np.ndarray | list) -> float:
    """Multiclase: media de Σ_k (p_k - o_k)². ``outcomes`` = índice de clase o 0/1 binario."""
    p = np.asarray(probs, dtype=float)
    o = np.asarray(outcomes)
    if p.ndim == 1:
        return float(np.mean((p - o.astype(float)) ** 2))
    onehot = np.eye(p.shape[1])[o.astype(int)]
    return float(np.mean(np.sum((p - onehot) ** 2, axis=1)))


def log_loss(probs: np.ndarray | list, outcomes: np.ndarray | list) -> float:
    p = np.asarray(probs, dtype=float)
    o = np.asarray(outcomes)
    if p.ndim == 1:
        o = o.astype(float)
        return float(-np.mean(o * np.log(np.clip(p, EPS, 1)) + (1 - o) * np.log(np.clip(1 - p, EPS, 1))))
    chosen = p[np.arange(len(p)), o.astype(int)]
    return float(-np.mean(np.log(np.clip(chosen, EPS, 1))))


def accuracy(probs: np.ndarray | list, outcomes: np.ndarray | list) -> float:
    p = np.asarray(probs, dtype=float)
    o = np.asarray(outcomes)
    if p.ndim == 1:
        return float(np.mean((p >= 0.5) == o.astype(bool)))
    return float(np.mean(p.argmax(axis=1) == o.astype(int)))


@dataclass(frozen=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float
    ci_low: float
    ci_high: float


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    phat = k / n
    denom = 1 + z**2 / n
    centre = (phat + z**2 / (2 * n)) / denom
    half = z * np.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return float(max(0.0, centre - half)), float(min(1.0, centre + half))


def reliability_curve(p: np.ndarray | list, y: np.ndarray | list, n_bins: int = 10) -> list[ReliabilityBin]:
    """Curva de calibración binaria con intervalos de Wilson por intervalo de probabilidad."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, n_bins + 1)
    out = []
    for lo, hi in itertools.pairwise(edges):
        mask = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        n = int(mask.sum())
        if n == 0:
            continue
        k = int(y[mask].sum())
        ci = _wilson(k, n)
        out.append(
            ReliabilityBin(
                lower=float(lo),
                upper=float(hi),
                count=n,
                mean_predicted=float(p[mask].mean()),
                observed_rate=k / n,
                ci_low=ci[0],
                ci_high=ci[1],
            )
        )
    return out


def expected_calibration_error(p: np.ndarray | list, y: np.ndarray | list, n_bins: int = 10) -> float:
    p = np.asarray(p, dtype=float)
    bins = reliability_curve(p, y, n_bins)
    n = len(p)
    if n == 0:
        return float("nan")
    return float(sum(b.count / n * abs(b.mean_predicted - b.observed_rate) for b in bins))


def multiclass_ece(probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 10) -> float:
    """ECE promedio one-vs-rest sobre las clases."""
    probs = _as2d(probs)
    outcomes = np.asarray(outcomes).astype(int)
    return float(
        np.mean([expected_calibration_error(probs[:, c], outcomes == c, n_bins) for c in range(probs.shape[1])])
    )


def roc_auc(p: np.ndarray | list, y: np.ndarray | list) -> float | None:
    y = np.asarray(y)
    if len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, p))


def bootstrap_metric_diff(
    probs_a: np.ndarray, probs_b: np.ndarray, outcomes: np.ndarray, metric=log_loss, n: int = 500, seed: int = 11
) -> dict[str, float]:
    """Diferencia (A - B) de una métrica con intervalo bootstrap del 90%.

    Negativo => A mejor que B en métricas de pérdida.
    """
    rng = np.random.default_rng(seed)
    m = len(outcomes)
    point = metric(probs_a, outcomes) - metric(probs_b, outcomes)
    diffs = np.empty(n)
    for i in range(n):
        ix = rng.integers(0, m, m)
        diffs[i] = metric(probs_a[ix], outcomes[ix]) - metric(probs_b[ix], outcomes[ix])
    return {
        "diff": float(point),
        "low": float(np.quantile(diffs, 0.05)),
        "high": float(np.quantile(diffs, 0.95)),
        "p_a_better": float(np.mean(diffs < 0)),
    }
