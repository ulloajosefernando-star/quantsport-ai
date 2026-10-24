"""Registro de tipos de mercado por deporte.

Cada mercado declara sus selecciones válidas, si necesita línea (y con qué paso), el periodo,
la regla de liquidación y si sus selecciones son mutuamente excluyentes y exhaustivas (solo
entonces se puede calcular el margen del operador).

Añadir un mercado o un deporte = añadir una entrada aquí (y, si hace falta, una regla en
``app.markets.settlement``). El núcleo no cambia.

Los mercados registrados son categorías que la plataforma sabe representar y liquidar. No
significa que todas las fuentes los ofrezcan ni que exista un modelo para todos: eso lo dice
``app.markets.models``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

TOL = 1e-9

SPORT_LABELS = {"football": "Fútbol", "nba": "Baloncesto (NBA)", "nfl": "Fútbol americano (NFL)",
                "mlb": "Béisbol (MLB)", "tennis": "Tenis"}
SELECTION_LABELS = {
    "home": "Local", "draw": "Empate", "away": "Visitante", "over": "Más de", "under": "Menos de",
    "yes": "Sí", "no": "No", "none": "Ninguno", "1X": "Local o empate", "X2": "Empate o visitante",
    "12": "Local o visitante", "home_over": "Local más de", "home_under": "Local menos de",
    "away_over": "Visitante más de", "away_under": "Visitante menos de",
}
HT_FT = tuple(f"{a}/{b}" for a in ("1", "X", "2") for b in ("1", "X", "2"))
TEAM_TOTAL = ("home_over", "home_under", "away_over", "away_under")


class MarketValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MarketDef:
    key: str
    sport: str
    label: str
    group: str
    settlement: str  # regla en app.markets.settlement.RULES
    selections: tuple[str, ...] | None = None  # None = selección libre que cumple selection_pattern
    selection_pattern: str | None = None
    line: str = "none"  # none | required | optional
    line_step: float | None = None  # múltiplo permitido de la línea (0.25 hándicap asiático, 0.5 totales, 1 europeo)
    exhaustive: bool = True  # selecciones excluyentes y exhaustivas -> se puede calcular el margen
    periods: tuple[str, ...] = ("ft",)
    participant: bool = False  # props de jugador: requiere nombre del jugador
    live_only: bool = False
    needs: tuple[str, ...] = ()  # datos adicionales necesarios (córners, estadísticas de jugador...)
    settlement_note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["selection_labels"] = {s: SELECTION_LABELS.get(s, s) for s in (self.selections or ())}
        return d


FT_GOALS = "Tiempo reglamentario (90 min + descuento); no cuenta prórroga ni penales."
PLAYER_VOID = "Si el jugador no participa, la apuesta se anula (push) según la regla habitual; confirma la regla de tu casa."
WITH_OT = "Incluye prórroga/tiempo extra salvo que el mercado diga lo contrario."

def _fb(key: str, label: str, group: str, settlement: str, **kw) -> MarketDef:
    kw.setdefault("settlement_note", FT_GOALS)
    return MarketDef(key=key, sport="football", label=label, group=group, settlement=settlement, **kw)


_MARKETS: list[MarketDef] = [
    # ---------------------------------------------------------------- fútbol
    _fb("1x2", "1X2", "Resultado", "winner_3way", selections=("home", "draw", "away")),
    _fb("double_chance", "Doble oportunidad", "Resultado", "double_chance", selections=("1X", "X2", "12"),
        exhaustive=False),
    _fb("dnb", "Empate no válido (Draw No Bet)", "Resultado", "handicap", selections=("home", "away"),
        settlement_note=FT_GOALS + " Si hay empate se devuelve la apuesta."),
    _fb("european_handicap", "Hándicap europeo", "Hándicap", "winner_3way", selections=("home", "draw", "away"),
        line="required", line_step=1.0,
        settlement_note=FT_GOALS + " La línea se suma a los goles del local; el empate con hándicap es una selección."),
    _fb("ah", "Hándicap asiático", "Hándicap", "handicap", selections=("home", "away"), line="required",
        line_step=0.25, settlement_note=FT_GOALS + " Líneas .25/.75 dividen la apuesta en dos mitades."),
    _fb("totals", "Más/menos goles", "Goles", "total", selections=("over", "under"), line="required",
        line_step=0.25, settlement_note=FT_GOALS + " Líneas enteras devuelven en caso de igualdad."),
    _fb("btts", "Ambos equipos anotan", "Goles", "btts", selections=("yes", "no")),
    _fb("correct_score", "Resultado exacto", "Goles", "correct_score", selection_pattern=r"^\d{1,2}-\d{1,2}$",
        exhaustive=False),
    _fb("ht_result", "Resultado al descanso", "Descanso", "winner_3way", selections=("home", "draw", "away"),
        periods=("ht",), settlement_note="Marcador al final del primer tiempo."),
    _fb("ht_ft", "Descanso/final", "Descanso", "ht_ft", selections=HT_FT,
        settlement_note="Combina el resultado al descanso y el final (90 min)."),
    _fb("team_totals", "Goles por equipo", "Goles", "team_total", selections=TEAM_TOTAL, line="required",
        line_step=0.25, exhaustive=False),
    _fb("corners_totals", "Córners: más/menos", "Córners", "total", selections=("over", "under"), line="required",
        line_step=0.5, needs=("corners",)),
    _fb("cards_totals", "Tarjetas: más/menos", "Tarjetas", "total", selections=("over", "under"), line="required",
        line_step=0.5, needs=("cards",),
        settlement_note="Conteo según la regla de la fuente (a veces amarilla=1, roja=2)."),
    _fb("first_team_to_score", "Primer equipo en marcar", "Goles", "first_to_score",
        selections=("home", "away", "none")),
    _fb("next_goal", "Próximo gol", "Goles", "first_to_score", selections=("home", "away", "none"), live_only=True,
        settlement_note="Mercado en vivo: se liquida con el siguiente gol desde el momento de la apuesta."),
]


def _us(sport: str, key: str, label: str, group: str, settlement: str, note: str = WITH_OT, **kw) -> MarketDef:
    return MarketDef(key=key, sport=sport, label=label, group=group, settlement=settlement, settlement_note=note, **kw)


def _props(sport: str, stats: dict[str, str], group: str = "Props de jugador") -> list[MarketDef]:
    return [_us(sport, f"player_{k}", v, group, "player_total", PLAYER_VOID, selections=("over", "under"),
                line="required", line_step=0.5, participant=True, needs=("player_stats",)) for k, v in stats.items()]


_SIDES = ("home", "away")
_OU = ("over", "under")
for sp in ("nba", "nfl", "mlb"):
    _MARKETS += [
        _us(sp, "moneyline", "Ganador (moneyline)", "Resultado", "winner_2way", selections=_SIDES),
        _us(sp, "spread" if sp != "mlb" else "run_line",
            "Hándicap (spread)" if sp != "mlb" else "Run line", "Hándicap", "handicap", selections=_SIDES,
            line="required", line_step=0.5),
        _us(sp, "totals", "Total de puntos" if sp != "mlb" else "Total de carreras", "Totales", "total",
            selections=_OU, line="required", line_step=0.5),
        _us(sp, "team_totals", "Totales por equipo", "Totales", "team_total", selections=TEAM_TOTAL,
            line="required", line_step=0.5, exhaustive=False),
    ]
_MARKETS += [
    _us("nba", "period_result", "Resultado por cuarto o mitad", "Periodos", "winner_3way",
        selections=("home", "draw", "away"), periods=("q1", "q2", "q3", "q4", "h1", "h2"),
        note="Solo el periodo indicado; el empate es una selección."),
    _us("nba", "ht_winner", "Ganador al descanso", "Periodos", "winner_3way",
        selections=("home", "draw", "away"), periods=("h1",), note="Marcador al descanso."),
    *_props("nba", {"points": "Puntos", "rebounds": "Rebotes", "assists": "Asistencias", "steals": "Robos",
                           "blocks": "Bloqueos", "threes": "Triples", "pra": "Puntos + rebotes + asistencias",
                           "pr": "Puntos + rebotes", "pa": "Puntos + asistencias", "ra": "Rebotes + asistencias"}),
    _us("nba", "player_double_double", "Doble-doble", "Props de jugador", "yes_no", PLAYER_VOID,
        selections=("yes", "no"), participant=True, needs=("player_stats",)),
    _us("nba", "player_triple_double", "Triple-doble", "Props de jugador", "yes_no", PLAYER_VOID,
        selections=("yes", "no"), participant=True, needs=("player_stats",)),
    _us("nfl", "ht_winner", "Ganador al descanso", "Periodos", "winner_3way",
        selections=("home", "draw", "away"), periods=("h1",), note="Marcador al descanso."),
    _us("nfl", "period_spread", "Hándicap por periodo", "Periodos", "handicap", selections=_SIDES,
        line="required", line_step=0.5, periods=("q1", "q2", "q3", "q4", "h1", "h2"), note="Solo el periodo indicado."),
    _us("nfl", "period_totals", "Total por periodo", "Periodos", "total", selections=_OU,
        line="required", line_step=0.5, periods=("q1", "q2", "q3", "q4", "h1", "h2"), note="Solo el periodo indicado."),
    _us("nfl", "total_touchdowns", "Total de touchdowns", "Totales", "total", selections=_OU,
        line="required", line_step=0.5, needs=("touchdowns",)),
    *_props("nfl", {"passing_yards": "Yardas por pase", "rushing_yards": "Yardas por carrera",
                                  "receiving_yards": "Yardas por recepción", "touchdowns": "Touchdowns",
                                  "interceptions": "Intercepciones lanzadas"}),
    _us("nfl", "player_anytime_td", "Anota touchdown en el partido", "Props de jugador", "yes_no",
        PLAYER_VOID, selections=("yes", "no"), participant=True, needs=("player_stats",)),
    _us("mlb", "first5_moneyline", "Ganador primeras 5 entradas", "Primeras entradas", "winner_3way",
        selections=("home", "draw", "away"), periods=("f5",),
        note="Solo las primeras 5 entradas; algunas casas devuelven el empate (revisa la regla)."),
    _us("mlb", "first5_totals", "Total primeras 5 entradas", "Primeras entradas", "total", selections=_OU,
        line="required", line_step=0.5, periods=("f5",), note="Solo las primeras 5 entradas."),
    *_props("mlb", {"pitcher_strikeouts": "Ponches del lanzador", "pitcher_outs": "Outs del lanzador",
                         "pitcher_earned_runs": "Carreras permitidas", "batter_hits": "Hits",
                         "batter_total_bases": "Bases totales", "batter_rbis": "Carreras impulsadas",
                         "batter_home_runs": "Home runs"}),
    # ---------------------------------------------------------------- tenis
    _us("tennis", "match_winner", "Ganador del partido", "Resultado", "winner_2way", selections=_SIDES,
        note="Si un jugador se retira, la liquidación depende de la regla de la fuente (a menudo se anula)."),
    _us("tennis", "set_winner", "Ganador del set", "Sets", "winner_2way", selections=_SIDES,
        periods=("set1", "set2", "set3", "set4", "set5"), note="Solo el set indicado."),
    _us("tennis", "first_set_winner", "Ganador del primer set", "Sets", "winner_2way", selections=_SIDES,
        periods=("set1",), note="Solo el primer set."),
    _us("tennis", "set_handicap", "Hándicap de sets", "Hándicap", "handicap", selections=_SIDES, line="required",
        line_step=0.5, note="Diferencia de sets ganados."),
    _us("tennis", "game_handicap", "Hándicap de juegos", "Hándicap", "handicap", selections=_SIDES,
        line="required", line_step=0.5, note="Diferencia de juegos ganados en el partido."),
    _us("tennis", "total_games", "Total de juegos", "Totales", "total", selections=_OU, line="required",
        line_step=0.5, note="Juegos totales del partido."),
    _us("tennis", "total_sets", "Total de sets", "Totales", "total", selections=_OU, line="required",
        line_step=0.5, note="Sets totales del partido."),
    _us("tennis", "correct_score_sets", "Marcador exacto en sets", "Sets", "correct_score",
        selection_pattern=r"^[0-3]-[0-3]$", exhaustive=False, note="Sets ganados por cada jugador (local-visitante)."),
    *_props("tennis", {"aces": "Aces", "double_faults": "Dobles faltas"}),
]

REGISTRY: dict[tuple[str, str], MarketDef] = {}
for _m in _MARKETS:
    if (_m.sport, _m.key) in REGISTRY:
        raise RuntimeError(f"Mercado duplicado: {_m.sport}/{_m.key}")
    REGISTRY[(_m.sport, _m.key)] = _m


def get(sport: str, key: str) -> MarketDef:
    try:
        return REGISTRY[(sport, key)]
    except KeyError as exc:
        raise MarketValidationError(f"Mercado '{key}' no registrado para {SPORT_LABELS.get(sport, sport)}") from exc


def for_sport(sport: str) -> list[MarketDef]:
    return [m for m in _MARKETS if m.sport == sport]


def sports() -> list[str]:
    return list(SPORT_LABELS)


@dataclass(frozen=True)
class ValidatedSelection:
    market: MarketDef
    selection: str
    line: float | None
    period: str
    participant: str | None


def validate(sport: str, market: str, selection: str, line: float | None = None, period: str | None = None,
             participant: str | None = None) -> ValidatedSelection:
    """Comprueba que la combinación mercado/selección/línea/periodo es válida. Lanza MarketValidationError."""
    m = get(sport, market)
    if m.selections is not None and selection not in m.selections:
        raise MarketValidationError(
            f"Selección '{selection}' no válida para {m.label}. Válidas: {', '.join(m.selections)}")
    if m.selections is None and not re.match(m.selection_pattern or ".+", selection or ""):
        raise MarketValidationError(f"Selección '{selection}' no válida para {m.label}")
    if m.line == "none":
        if line is not None:
            raise MarketValidationError(f"{m.label} no lleva línea")
    elif m.line == "required":
        if line is None:
            raise MarketValidationError(f"{m.label} necesita una línea (p. ej. 2.5)")
        if line != line or abs(line) > 1000:
            raise MarketValidationError(f"Línea inválida: {line}")
        if m.line_step and abs(line / m.line_step - round(line / m.line_step)) > TOL:
            raise MarketValidationError(f"Línea {line:g} inválida para {m.label}: debe ser múltiplo de {m.line_step:g}")
    if m.settlement in ("total", "team_total", "player_total") and line is not None and line < 0:
        raise MarketValidationError("Un total no puede tener línea negativa")
    p = period or m.periods[0]
    if p not in m.periods:
        raise MarketValidationError(f"Periodo '{p}' no válido para {m.label}. Válidos: {', '.join(m.periods)}")
    if m.participant and not (participant and participant.strip()):
        raise MarketValidationError(f"{m.label} necesita el nombre del jugador")
    if not m.participant and participant:
        raise MarketValidationError(f"{m.label} no lleva jugador")
    return ValidatedSelection(m, selection, line if m.line != "none" else None, p,
                              participant.strip() if participant else None)
