"""Conversión de formatos de cuota y probabilidades implícitas.

Internamente la plataforma trabaja SIEMPRE con cuotas decimales.

Formatos soportados (todos se normalizan a decimal):
    decimal       2.50
    americano     +150 -> 1 + 150/100 = 2.50 ; -200 -> 1 + 100/200 = 1.50
    fraccionario  3/2  -> 3/2 + 1 = 2.50
    porcentaje    40%  -> 100/40 = 2.50   (probabilidad implícita expresada como porcentaje)

Definiciones:
    probabilidad implícita bruta  p = 1 / d
    americano positivo            p = 100 / (momio + 100)
    americano negativo            p = |momio| / (|momio| + 100)
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
    if american != american or abs(american) == float("inf") or -100 < american < 100:
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


def american_implied_probability(american: float) -> float:
    """+150 -> 100/250 = 0.40 ; -200 -> 200/300 = 0.6667 (incluye el margen del operador)."""
    if -100 < american < 100:
        raise InvalidOddsError(f"Cuota americana inválida: {american}")
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def percent_to_decimal(value: float | str) -> float:
    """'40%' o 40 -> 2.50. El porcentaje es la probabilidad implícita (0 < p < 100)."""
    text = str(value).strip().rstrip("%").strip()
    try:
        pct = float(text)
    except ValueError as exc:
        raise InvalidOddsError(f"Porcentaje inválido: {value!r}") from exc
    if not 0.0 < pct < 100.0 or pct != pct:
        raise InvalidOddsError(f"Porcentaje fuera de rango: {value!r}. Debe estar entre 0 y 100 (sin incluirlos)")
    return 100.0 / pct


def decimal_to_percent(d: float) -> float:
    return 100.0 / validate_decimal(d)


def decimal_to_fractional(d: float, max_denominator: int = 100) -> str:
    d = validate_decimal(d)
    frac = Fraction(d - 1.0).limit_denominator(max_denominator)
    return f"{frac.numerator}/{frac.denominator}"


FORMATS = ("decimal", "american", "fractional", "percent")


def detect_format(value: float | str) -> str:
    """Adivina el formato de una cuota escrita a mano: '3/2', '40%', '+150'/'-200' o decimal."""
    text = str(value).strip()
    if "/" in text:
        return "fractional"
    if text.endswith("%"):
        return "percent"
    if text.startswith(("+", "-")):
        return "american"
    try:
        num = float(text)
    except ValueError as exc:
        raise InvalidOddsError(f"Cuota no reconocida: {value!r}") from exc
    return "american" if abs(num) >= 100 else "decimal"


def to_decimal(value: float | str, fmt: str = "decimal") -> float:
    """Convierte cualquier formato a decimal validado. ``fmt='auto'`` detecta el formato."""
    fmt = fmt.lower()
    if fmt == "auto":
        fmt = detect_format(value)
    try:
        if fmt == "decimal":
            return validate_decimal(float(value))
        if fmt == "american":
            return validate_decimal(american_to_decimal(float(str(value).strip())))
        if fmt == "fractional":
            return validate_decimal(fractional_to_decimal(str(value)))
        if fmt == "percent":
            return validate_decimal(percent_to_decimal(value))
    except (ZeroDivisionError, TypeError) as exc:
        raise InvalidOddsError(f"Cuota inválida: {value!r}") from exc
    except InvalidOddsError:
        raise
    except ValueError as exc:
        raise InvalidOddsError(f"Cuota inválida para formato {fmt}: {value!r}") from exc
    raise ValueError(f"Formato de cuota desconocido: {fmt}")


def format_odds(d: float, fmt: str = "decimal") -> str:
    """Texto para mostrar una cuota decimal en el formato elegido."""
    d = validate_decimal(d)
    if fmt == "decimal":
        return f"{d:.2f}"
    if fmt == "american":
        a = decimal_to_american(d)
        return f"{a:+.0f}"
    if fmt == "fractional":
        return decimal_to_fractional(d)
    if fmt == "percent":
        return f"{decimal_to_percent(d):.2f}%"
    raise ValueError(f"Formato de cuota desconocido: {fmt}")


def convert_all(d: float) -> dict:
    """Una cuota decimal en todos los formatos, más su probabilidad implícita."""
    d = validate_decimal(d)
    return {"decimal": d, "american": decimal_to_american(d), "fractional": decimal_to_fractional(d),
            "percent": decimal_to_percent(d), "implied_probability": 1.0 / d,
            "display": {f: format_odds(d, f) for f in FORMATS}}


def implied_probability(d: float) -> float:
    """Probabilidad implícita bruta (incluye el margen del operador)."""
    return 1.0 / validate_decimal(d)


def fair_odds(p: float) -> float:
    """Cuota justa para una probabilidad estimada p."""
    return 1.0 / validate_probability(p)
