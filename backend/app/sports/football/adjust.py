"""Ajustes posteriores al modelo de goles: calibración de empates y combinación con el mercado.

Los dos se aplican como un reescalado por clase (local, empate, visitante) de la matriz de marcadores: cada
celda se multiplica por el factor de su clase. Así todos los mercados derivados (totales, ambos anotan,
hándicaps, resultado exacto) siguen siendo coherentes con el 1X2 ajustado.

* Empates: factor e^δ sobre la clase empate. Dixon-Coles suele quedarse corto en empates; δ se estima con
  predicciones fuera de muestra anteriores, nunca con el partido que se predice.
* Mercado: agregación logarítmica q ∝ p_modelo^a · p_mercado^b sobre el 1X2. Si el modelo no aporta nada,
  a → 0 y la probabilidad final es la del mercado (sin valor que apostar). Los pesos también se estiman con
  predicciones anteriores. En la validación con Liga MX la referencia es el cierre; en vivo se usa el
  consenso sin margen de las cuotas disponibles antes del partido.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from app.sports.football.scores import ScoreMatrix

MIN_ROWS_DRAW = 300
MIN_ROWS_BLEND = 300
EPS = 1e-9


@dataclass(frozen=True)
class Adjustment:
    draw_delta: float = 0.0
    model_weight: float = 1.0
    market_weight: float = 0.0
    n_draw: int = 0
    n_blend: int = 0

    @property
    def blends(self) -> bool:
        return self.n_blend > 0

    def to_dict(self) -> dict:
        return asdict(self) | {"draw_factor": round(float(np.exp(self.draw_delta)), 4)}


IDENTITY = Adjustment()


def class_probs(p3: np.ndarray, adj: Adjustment, m3: np.ndarray | None = None) -> np.ndarray:
    """1X2 ajustado. ``p3`` y ``m3`` pueden ser (3,) o (n, 3); ``m3`` con NaN = sin mercado."""
    p = np.atleast_2d(np.asarray(p3, dtype=float)) * np.array([1.0, np.exp(adj.draw_delta), 1.0])
    p = p / p.sum(axis=1, keepdims=True)
    if adj.blends and m3 is not None:
        m = np.atleast_2d(np.asarray(m3, dtype=float))
        ok = np.isfinite(m).all(axis=1)
        if ok.any():
            q = np.clip(p[ok], EPS, 1) ** adj.model_weight * np.clip(m[ok], EPS, 1) ** adj.market_weight
            p[ok] = q / q.sum(axis=1, keepdims=True)
    return p if np.ndim(p3) == 2 else p[0]


def _masks(n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    d = np.subtract.outer(np.arange(n), np.arange(n))
    return d > 0, d == 0, d < 0


def adjust_matrix(sm: ScoreMatrix, adj: Adjustment, m3: np.ndarray | None = None) -> ScoreMatrix:
    if adj == IDENTITY or (adj.draw_delta == 0 and not (adj.blends and m3 is not None)):
        return sm
    masks = _masks(sm.matrix.shape[0])
    mass = np.array([sm.matrix[k].sum() for k in masks])
    total = mass.sum()
    p3 = mass / total
    q3 = class_probs(p3, adj, m3)
    out = sm.matrix.copy()
    for k, msk in enumerate(masks):
        out[msk] *= q3[k] / max(p3[k], EPS)
    return ScoreMatrix(matrix=out, lambda_home=sm.lambda_home, lambda_away=sm.lambda_away, rho=sm.rho,
                       tail_mass=sm.tail_mass)


def class_split(sm: ScoreMatrix, line: float = 2.5) -> dict[str, np.ndarray]:
    """Masa por clase (L, E, V) total, con más de ``line`` goles y con ambos anotando."""
    m = sm.matrix
    n = m.shape[0]
    tot = np.add.outer(np.arange(n), np.arange(n))
    both = np.outer(np.arange(n) > 0, np.arange(n) > 0)
    masks = _masks(n)
    return {"p": np.array([m[k].sum() for k in masks]),
            "over": np.array([m[k & (tot > line)].sum() for k in masks]),
            "btts": np.array([m[k & both].sum() for k in masks])}


def derived(p3: np.ndarray, q3: np.ndarray, part: np.ndarray) -> np.ndarray:
    """Probabilidad de un suceso tras reescalar clases: Σ masa_suceso_clase · q/p."""
    f = q3 / np.clip(p3, EPS, None)
    return (np.atleast_2d(part) * np.atleast_2d(f)).sum(axis=1) / (np.atleast_2d(p3) * np.atleast_2d(f)).sum(axis=1)


def _ll(P: np.ndarray, y: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], EPS, 1))))


def fit_draw(P: np.ndarray, y: np.ndarray) -> float:
    if len(y) < MIN_ROWS_DRAW:
        return 0.0
    r = minimize_scalar(lambda d: _ll(class_probs(P, Adjustment(draw_delta=d)), y), bounds=(-0.5, 0.5),
                        method="bounded")
    return float(r.x)


def fit(P: np.ndarray, M: np.ndarray, y: np.ndarray, draw: bool, blend: bool) -> Adjustment:
    """Estima los ajustes con predicciones fuera de muestra ``P`` (n×3), mercado ``M`` (n×3, NaN si no hay) y
    resultados ``y`` (0 local, 1 empate, 2 visitante)."""
    delta = fit_draw(P, y) if draw else 0.0
    n_draw = len(y) if draw and len(y) >= MIN_ROWS_DRAW else 0
    if not blend:
        return Adjustment(draw_delta=delta, n_draw=n_draw)
    ok = np.isfinite(M).all(axis=1)
    if ok.sum() < MIN_ROWS_BLEND:
        return Adjustment(draw_delta=delta, n_draw=n_draw)
    Pd = class_probs(P[ok], Adjustment(draw_delta=delta))
    Mo, yo = M[ok], y[ok]

    def loss(w: np.ndarray) -> float:
        return _ll(class_probs(Pd, Adjustment(model_weight=w[0], market_weight=w[1], n_blend=1), Mo), yo)

    r = minimize(loss, np.array([0.5, 0.5]), method="L-BFGS-B", bounds=[(0.0, 1.5), (0.0, 1.5)])
    return Adjustment(draw_delta=delta, model_weight=float(r.x[0]), market_weight=float(r.x[1]),
                      n_draw=n_draw, n_blend=int(ok.sum()))
