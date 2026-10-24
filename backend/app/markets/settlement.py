"""Reglas de liquidación por tipo de mercado.

Cada regla recibe el resultado del periodo liquidado (``Outcome``) y devuelve el estado
(gana, medio_gana, push, medio_pierde, pierde) y el retorno neto por unidad apostada.
El EV de cualquier mercado se calcula después como Σ P(estado) · retorno(estado)
(``app.probability.settlement``), así que los mercados con devolución o medias
liquidaciones nunca usan la fórmula binaria.

Convenciones:
    handicap   la línea se suma a la selección: home -0.5 gana si local - visitante - 0.5 > 0.
    total      over L gana si total > L; igualdad exacta = push; .25/.75 = dos mitades.
    winner_3way con línea (hándicap europeo): la línea se suma al local y el empate es selección.
    winner_2way empate (posible en NFL o cuando un periodo acaba igual) = push.
    player_total / yes_no  sin dato del jugador (no participó) = push.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.probability.odds import validate_decimal
from app.probability.settlement import handicap_return, total_return

WIN, LOSS, PUSH = "gana", "pierde", "push"


@dataclass(frozen=True)
class Outcome:
    """Resultado del periodo liquidado.

    home/away: goles, puntos, carreras, sets o juegos según el mercado.
    value: estadística de un jugador o conteo (córners, tarjetas) cuando el mercado no es por equipos.
    extra: datos adicionales (p. ej. ``ht_home``/``ht_away`` para descanso/final, ``first`` para primer gol).
    """

    home: float | None = None
    away: float | None = None
    value: float | None = None
    extra: dict = field(default_factory=dict)


class UnsupportedSettlementError(ValueError):
    pass


def _binary(won: bool, odds: float) -> tuple[str, float]:
    return (WIN, odds - 1.0) if won else (LOSS, -1.0)


def _need_score(o: Outcome) -> tuple[float, float]:
    if o.home is None or o.away is None:
        raise UnsupportedSettlementError("Faltan los marcadores para liquidar este mercado")
    return o.home, o.away


def winner_3way(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    h, a = _need_score(o)
    d = h + (line or 0.0) - a
    res = "home" if d > 0 else "away" if d < 0 else "draw"
    return _binary(res == sel, odds)


def winner_2way(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    h, a = _need_score(o)
    if h == a:
        return PUSH, 0.0
    return _binary((h > a) == (sel == "home"), odds)


def double_chance(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    h, a = _need_score(o)
    res = "1" if h > a else "2" if h < a else "X"
    return _binary(res in sel, odds)


def handicap(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    h, a = _need_score(o)
    diff = h - a if sel == "home" else a - h
    return handicap_return(diff, line or 0.0, odds)


def total(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    t = o.value if o.value is not None else sum(_need_score(o))
    return total_return(t, float(line), odds, sel)


def team_total(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    h, a = _need_score(o)
    team, side = sel.split("_", 1)
    return total_return(h if team == "home" else a, float(line), odds, side)


def btts(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    h, a = _need_score(o)
    return _binary((h > 0 and a > 0) == (sel == "yes"), odds)


def correct_score(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    h, a = _need_score(o)
    sh, sa = (int(x) for x in sel.split("-"))
    return _binary(h == sh and a == sa, odds)


def ht_ft(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    h, a = _need_score(o)
    if "ht_home" not in o.extra or "ht_away" not in o.extra:
        raise UnsupportedSettlementError("Falta el marcador al descanso")

    def r(x: float, y: float) -> str:
        return "1" if x > y else "2" if x < y else "X"

    return _binary(f"{r(o.extra['ht_home'], o.extra['ht_away'])}/{r(h, a)}" == sel, odds)


def first_to_score(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    first = o.extra.get("first")
    if first not in ("home", "away", "none"):
        raise UnsupportedSettlementError("Falta saber qué equipo marcó primero")
    return _binary(first == sel, odds)


def player_total(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    if o.value is None:
        return PUSH, 0.0  # no participó: anulada
    return total_return(o.value, float(line), odds, sel)


def yes_no(sel: str, line: float | None, odds: float, o: Outcome) -> tuple[str, float]:
    if o.value is None:
        return PUSH, 0.0
    return _binary(bool(o.value) == (sel == "yes"), odds)


Rule = Callable[[str, float | None, float, Outcome], tuple[str, float]]
RULES: dict[str, Rule] = {
    "winner_3way": winner_3way, "winner_2way": winner_2way, "double_chance": double_chance, "handicap": handicap,
    "total": total, "team_total": team_total, "btts": btts, "correct_score": correct_score, "ht_ft": ht_ft,
    "first_to_score": first_to_score, "player_total": player_total, "yes_no": yes_no,
}


def settle(rule: str, selection: str, line: float | None, odds: float, outcome: Outcome) -> tuple[str, float]:
    """Estado y retorno neto por unidad de una selección dado el resultado."""
    if rule not in RULES:
        raise UnsupportedSettlementError(f"Regla de liquidación desconocida: {rule}")
    return RULES[rule](selection, line, validate_decimal(odds), outcome)
