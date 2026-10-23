"""Conversión de formatos de cuota y probabilidades implícitas.

Internamente la plataforma trabaja SIEMPRE con cuotas decimales.

Definiciones:
    probabilidad implícita bruta  p = 1 / d
    cuota justa                    d_justa = 1 / p
"""

from __future__ import annotations

from fractions import Fraction

MIN_DECIMAL_ODDS = 1.0


class InvalidOddsError(ValueError):
    """Cuota fuera de dominio (por ejemplo, decimal <= 1 o americana entre -100 y 100)."""


def validate_decimal(d: float) -> float:
    if d is None or not (d > MIN_DECIMAL_ODDS) or d != d or d == float("inf"):
        raise InvalidOddsError(f"Cuota decimal inválida: {d!r}. Debe ser > 1.0")
    return float(d)


def validate_probability(p: float, *, allow_bounds: bool = False) -> float:
    if p is None or p != p:
        raise ValueError("Probabilidad inválida: NaN")
    if allow_bounds:
        ok = 0.0 <= p <= 1.0
    else:
        ok = 0.0 < p < 1.0
    if not ok:
        raise ValueError(f"Probabilidad fuera de rango: {p!r}")
    return float(p)


def american_to_decimal(american: float) -> float:
    """+150 -> 2.50 ; -200 -> 1.50. Valores en (-100, 100) no existen en formato americano."""
    if -100 < american < 100:
        raise InvalidOddsError(f"Cuota americana inválida: {american}")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(d: float) -> float:
    d = validate_decimal(d)
    if d >= 2.0:
        return round((d - 1.0) * 100.0, 6)
    return round(-100.0 / (d - 1.0), 6)


def fractional_to_decimal(fractional: str | Fraction | tuple[int, int]) -> float:
    """'5/2' -> 3.5 ; (1, 4) -> 1.25."""
    if isinstance(fractional, tuple):
        num, den = fractional
        frac = Fraction(num, den)
    elif isinstance(fractional, Fraction):
        frac = fractional
    else:
        text = str(fractional).strip()
        if "/" not in text:
            raise InvalidOddsError(f"Cuota fraccionaria inválida: {fractional!r}")
        num_s, den_s = text.split("/", 1)
        frac = Fraction(int(num_s), int(den_s))
    if frac <= 0:
        raise InvalidOddsError(f"Cuota fraccionaria inválida: {fractional!r}")
    return float(1 + frac)


def decimal_to_fractional(d: float, max_denominator: int = 100) -> str:
    d = validate_decimal(d)
    frac = Fraction(d - 1.0).limit_denominator(max_denominator)
    return f"{frac.numerator}/{frac.denominator}"


def to_decimal(value: float | str, fmt: str = "decimal") -> float:
    fmt = fmt.lower()
    if fmt == "decimal":
        return validate_decimal(float(value))
    if fmt == "american":
        return american_to_decimal(float(value))
    if fmt == "fractional":
        return fractional_to_decimal(str(value))
    raise ValueError(f"Formato de cuota desconocido: {fmt}")


def implied_probability(d: float) -> float:
    """Probabilidad implícita bruta (incluye el margen del operador)."""
    return 1.0 / validate_decimal(d)


def fair_odds(p: float) -> float:
    """Cuota justa para una probabilidad estimada p."""
    return 1.0 / validate_probability(p)
