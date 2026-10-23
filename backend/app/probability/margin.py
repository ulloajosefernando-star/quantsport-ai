"""Eliminación del margen (overround) de un mercado completo.

IMPORTANTE: eliminar el margen produce una *estimación* de la probabilidad que el
mercado asigna a cada resultado. No revela la probabilidad verdadera. Cada método
supone una forma distinta de repartir el margen entre resultados (por ejemplo, el
sesgo favorito/longshot), y sus resultados pueden diferir.

Requiere un mercado con resultados mutuamente excluyentes y exhaustivos
(p. ej. 1X2, Over/Under de la misma línea, BTTS Sí/No).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq

from app.probability.odds import validate_decimal

METHODS = ("proportional", "power", "shin")


def _inverse(odds: Sequence[float]) -> np.ndarray:
    if len(odds) < 2:
        raise ValueError("Un mercado necesita al menos 2 resultados")
    return np.array([1.0 / validate_decimal(d) for d in odds], dtype=float)


def overround(odds: Sequence[float]) -> float:
    """overround = suma(1/d_i) - 1."""
    return float(_inverse(odds).sum() - 1.0)


def proportional(odds: Sequence[float]) -> list[float]:
    """p_i = (1/d_i) / suma(1/d_j). Reparte el margen proporcionalmente."""
    q = _inverse(odds)
    return (q / q.sum()).tolist()


def power(odds: Sequence[float]) -> list[float]:
    """Método de potencia: p_i = (1/d_i)^k con k tal que suma(p_i) = 1.

    Asigna proporcionalmente más margen a las selecciones con cuota alta.
    """
    q = _inverse(odds)
    total = q.sum()
    if abs(total - 1.0) < 1e-12:
        return q.tolist()
    if total < 1.0:
        # Mercado con overround negativo (posible al combinar mejores cuotas de varios
        # operadores). k < 1 eleva las probabilidades.
        lo, hi = 1e-6, 1.0
    else:
        lo, hi = 1.0, 50.0
    k = brentq(lambda k: float(np.sum(q**k)) - 1.0, lo, hi, xtol=1e-12)
    p = q**k
    return (p / p.sum()).tolist()


def shin(odds: Sequence[float]) -> list[float]:
    """Método de Shin (1993): modela el margen como protección ante apostadores informados.

    p_i = (sqrt(z^2 + 4(1-z) q_i^2 / S) - z) / (2(1-z)), con z tal que suma(p_i)=1.
    """
    q = _inverse(odds)
    s = q.sum()
    if s <= 1.0:
        return (q / s).tolist()

    def total(z: float) -> float:
        p = (np.sqrt(z**2 + 4 * (1 - z) * q**2 / s) - z) / (2 * (1 - z))
        return float(p.sum()) - 1.0

    z = brentq(total, 0.0, 0.999, xtol=1e-12)
    p = (np.sqrt(z**2 + 4 * (1 - z) * q**2 / s) - z) / (2 * (1 - z))
    return (p / p.sum()).tolist()


def remove_margin(odds: Sequence[float], method: str = "proportional") -> list[float]:
    if method == "proportional":
        return proportional(odds)
    if method == "power":
        return power(odds)
    if method == "shin":
        return shin(odds)
    raise ValueError(f"Método de eliminación de margen desconocido: {method}")


@dataclass(frozen=True)
class MarginComparison:
    overround: float
    raw_implied: list[float]
    by_method: dict[str, list[float]]
    max_disagreement: float
    note: str = (
        "Las probabilidades sin margen son estimaciones del consenso del mercado; "
        "no equivalen a la probabilidad verdadera."
    )


def compare_methods(odds: Sequence[float]) -> MarginComparison:
    raw = _inverse(odds).tolist()
    by_method = {m: remove_margin(odds, m) for m in METHODS}
    arr = np.array(list(by_method.values()))
    disagreement = float((arr.max(axis=0) - arr.min(axis=0)).max())
    return MarginComparison(
        overround=overround(odds),
        raw_implied=raw,
        by_method=by_method,
        max_disagreement=disagreement,
    )
