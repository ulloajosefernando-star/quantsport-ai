"""Estructura normalizada de cuotas: evento → mercado → selección → cuota.

Cada nivel tiene un identificador estable (hash de sus campos naturales), así la misma
cuota llegada por dos caminos produce el mismo id y no se duplica:

    evento     deporte | competición | inicio (UTC, minuto) | local | visitante   (claves canónicas de equipo)
    mercado    evento | mercado | periodo | jugador | línea
    selección  mercado | selección
    cuota      selección | operador | fuente | momento de captura

En la base, la cuota es una fila de ``odds_snapshots`` (inmutable); este módulo la presenta
separando los cuatro niveles.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, OddsSnapshot
from app.ingestion.store import as_utc
from app.markets import registry


def stable_id(*parts: object) -> str:
    raw = "|".join("" if p is None else (f"{p:g}" if isinstance(p, float) else str(p)) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EventRef:
    id: int
    stable_id: str
    sport: str
    competition_key: str
    competition: str
    home: str
    away: str
    kickoff_utc: datetime
    status: str
    is_synthetic: bool


@dataclass(frozen=True)
class MarketRef:
    stable_id: str
    event_id: str
    key: str
    label: str
    period: str
    line: float | None
    participant: str | None
    is_live: bool
    status: str
    settlement_note: str


@dataclass(frozen=True)
class SelectionRef:
    stable_id: str
    market_id: str
    selection: str
    label: str


@dataclass(frozen=True)
class Quote:
    stable_id: str
    selection_id: str
    bookmaker: str
    odds_decimal: float
    implied_probability: float
    original_format: str | None
    original_value: str | None
    source: str
    updated_at: datetime
    is_synthetic: bool


@dataclass
class MarketBook:
    """Todas las selecciones de un mercado (misma línea, periodo y jugador) de UN operador."""

    event: EventRef
    market: MarketRef
    definition: registry.MarketDef
    bookmaker: str
    entries: list[tuple[SelectionRef, Quote]] = field(default_factory=list)


def event_ref(ev: Event) -> EventRef:
    comp = ev.competition
    sport = comp.sport.key if comp is not None and comp.sport is not None else "football"
    kickoff = as_utc(ev.kickoff_utc)
    return EventRef(
        id=ev.id, sport=sport, competition_key=comp.key, competition=comp.name,
        stable_id=stable_id(sport, comp.key, kickoff.strftime("%Y-%m-%dT%H:%M"), ev.home_team.canonical_key,
                            ev.away_team.canonical_key),
        home=ev.home_team.name, away=ev.away_team.name, kickoff_utc=kickoff, status=ev.status,
        is_synthetic=bool(ev.is_synthetic or comp.is_synthetic))


def selection_label(m: registry.MarketDef, selection: str, line: float | None, home: str, away: str) -> str:
    base = registry.SELECTION_LABELS.get(selection, selection)
    base = base.replace("Local", home).replace("Visitante", away)
    if m.settlement in ("handicap",) and line is not None:
        return f"{base} {line:+g}"
    if line is not None:
        return f"{base} {line:g}"
    return base


def load_books(s: Session, events: list[Event], market: str | None = None, bookmaker: str | None = None,
               include_live: bool = False) -> tuple[list[MarketBook], list[str]]:
    """Última cuota (no de cierre) de cada selección, agrupada por evento/mercado/operador.

    Devuelve también avisos de mercados que no están en el registro (no se evalúan).
    """
    if not events:
        return [], []
    refs = {ev.id: event_ref(ev) for ev in events}
    q = select(OddsSnapshot).where(OddsSnapshot.event_id.in_(list(refs)), OddsSnapshot.is_closing.is_(False))
    if market:
        q = q.where(OddsSnapshot.market == market)
    if bookmaker:
        q = q.where(OddsSnapshot.bookmaker == bookmaker)
    if not include_live:
        q = q.where(OddsSnapshot.is_live.is_(False))
    latest: dict[tuple, OddsSnapshot] = {}
    for o in s.scalars(q.order_by(OddsSnapshot.captured_at)):
        latest[(o.event_id, o.bookmaker, o.market, o.period, o.participant, o.line, o.selection, o.is_live)] = o
    books: dict[tuple, MarketBook] = {}
    warnings: set[str] = set()
    for o in latest.values():
        ev = refs[o.event_id]
        try:
            mdef = registry.get(ev.sport, o.market)
        except registry.MarketValidationError:
            warnings.add(f"Mercado '{o.market}' no registrado para {ev.sport}: no se evalúa.")
            continue
        if o.bookmaker == "Máximo mercado":
            continue  # pseudo-operador agregado del CSV
        mkey = (o.event_id, o.bookmaker, o.market, o.period, o.participant, o.line, o.is_live)
        book = books.get(mkey)
        if book is None:
            mid = stable_id(ev.stable_id, o.market, o.period, o.participant, o.line)
            mref = MarketRef(stable_id=mid, event_id=ev.stable_id, key=o.market, label=mdef.label, period=o.period,
                             line=o.line, participant=o.participant, is_live=o.is_live, status=o.market_status,
                             settlement_note=mdef.settlement_note)
            book = books[mkey] = MarketBook(ev, mref, mdef, o.bookmaker)
        sid = stable_id(book.market.stable_id, o.selection)
        sref = SelectionRef(sid, book.market.stable_id, o.selection,
                            selection_label(mdef, o.selection, o.line, ev.home, ev.away))
        captured = as_utc(o.captured_at)
        book.entries.append((sref, Quote(
            stable_id=stable_id(sid, o.bookmaker, o.source, captured.isoformat()), selection_id=sid,
            bookmaker=o.bookmaker, odds_decimal=o.odds_decimal, implied_probability=1.0 / o.odds_decimal,
            original_format=o.original_format, original_value=o.original_value, source=o.source,
            updated_at=captured, is_synthetic=o.is_synthetic)))
    out = sorted(books.values(), key=lambda b: (b.event.kickoff_utc, b.event.id, b.market.key, b.market.period,
                                                b.market.participant or "", b.market.line or 0, b.bookmaker))
    for b in out:
        sels = b.definition.selections or ()
        order = {sel: i for i, sel in enumerate(sels)}
        b.entries.sort(key=lambda e: (order.get(e[0].selection, 99), e[0].selection))
    return out, sorted(warnings)


def as_dict(obj: object) -> dict:
    d = asdict(obj)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d
