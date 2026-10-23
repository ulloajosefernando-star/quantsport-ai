"""Reglas genéricas de liquidación por estados.

Cada apuesta se describe como una distribución sobre estados de liquidación con su
retorno neto por unidad apostada (ganancia positiva, pérdida -1, push 0, etc.).
Así el EV se calcula sobre TODOS los estados posibles, sin aplicar la fórmula binaria
a mercados con devolución (Draw No Bet, hándicaps asiáticos, totales asiáticos).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.probability.odds import validate_decimal

TOL = 1e-9


@dataclass(frozen=True)
class SettlementState:
    name: str  # "gana", "medio_gana", "push", "medio_pierde", "pierde"
    probability: float
    net_return: float  # por unidad apostada


@dataclass(frozen=True)
class SettlementDistribution:
    states: tuple[SettlementState, ...]
    definition: str = field(default="")

    def __post_init__(self) -> None:
        total = sum(s.probability for s in self.states)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Las probabilidades de los estados deben sumar 1 (suma={total:.8f})")
        if any(s.probability < -TOL for s in self.states):
            raise ValueError("Probabilidad negativa en un estado de liquidación")

    @property
    def expected_value(self) -> float:
        return sum(s.probability * s.net_return for s in self.states)

    @property
    def variance(self) -> float:
        mu = self.expected_value
        return sum(s.probability * (s.net_return - mu) ** 2 for s in self.states)

    @property
    def p_full_win(self) -> float:
        return sum(s.probability for s in self.states if s.name == "gana")

    @property
    def p_push(self) -> float:
        return sum(s.probability for s in self.states if s.name == "push")


def _single_line_return(margin: float, odds: float) -> tuple[str, float]:
    """margin = resultado ajustado por la línea desde la perspectiva de la selección."""
    if margin > TOL:
        return "gana", odds - 1.0
    if margin < -TOL:
        return "pierde", -1.0
    return "push", 0.0


def is_quarter_line(line: float) -> bool:
    return abs((line * 4) % 2 - 1) < TOL  # x.25 o x.75


def validate_line(line: float) -> float:
    if abs(line * 4 - round(line * 4)) > TOL:
        raise ValueError(f"Línea inválida {line}: debe ser múltiplo de 0.25")
    return float(line)


def handicap_return(diff: int, line: float, odds: float) -> tuple[str, float]:
    """Retorno neto por unidad de un hándicap asiático.

    diff: goles de la selección - goles del rival (marcador final del periodo liquidado).
    line: hándicap aplicado a la selección (p. ej. -0.75, +0.25, 0).
    Las líneas de cuarto (x.25/x.75) dividen la apuesta en dos mitades a línea±0.25.
    """
    odds = validate_decimal(odds)
    line = validate_line(line)
    if is_quarter_line(line):
        a_name, a = _single_line_return(diff + line - 0.25, odds)
        b_name, b = _single_line_return(diff + line + 0.25, odds)
        ret = 0.5 * a + 0.5 * b
        names = {a_name, b_name}
        if names == {"gana"}:
            return "gana", ret
        if names == {"pierde"}:
            return "pierde", ret
        if names == {"gana", "push"}:
            return "medio_gana", ret
        if names == {"pierde", "push"}:
            return "medio_pierde", ret
        raise AssertionError("Combinación de cuarto de línea imposible")
    return _single_line_return(diff + line, odds)


def total_return(total: int, line: float, odds: float, side: str) -> tuple[str, float]:
    """Over/Under (incluye totales asiáticos .25/.75). side: 'over' | 'under'."""
    if side not in ("over", "under"):
        raise ValueError("side debe ser 'over' o 'under'")
    # Over L equivale a hándicap -L sobre el total; under L equivale a +L sobre -total.
    if side == "over":
        return handicap_return(total, -line, odds)
    return handicap_return(-total, line, odds)


def aggregate(outcomes: Iterable[tuple[float, str, float]], definition: str = "") -> SettlementDistribution:
    """Agrupa (probabilidad, estado, retorno) en una distribución de liquidación."""
    acc: dict[tuple[str, float], float] = {}
    for prob, name, ret in outcomes:
        key = (name, round(ret, 12))
        acc[key] = acc.get(key, 0.0) + prob
    states = tuple(
        SettlementState(name=name, probability=prob, net_return=ret)
        for (name, ret), prob in sorted(acc.items(), key=lambda kv: -kv[0][1])
        if prob > 0
    )
    return SettlementDistribution(states=states, definition=definition)


def binary_distribution(p: float, odds: float, definition: str = "") -> SettlementDistribution:
    odds = validate_decimal(odds)
    return aggregate([(p, "gana", odds - 1.0), (1.0 - p, "pierde", -1.0)], definition)
