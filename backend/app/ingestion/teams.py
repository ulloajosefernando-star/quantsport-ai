"""Revisión y fusión de equipos entre fuentes.

Cada fuente escribe los nombres a su manera ("Club America", "America", "América"). Si un
nombre no tiene alias registrado se crea un equipo nuevo sin historial, y el modelo no puede
analizar sus partidos. Este módulo:

* detecta equipos con partidos programados pero sin historial suficiente y sugiere con qué
  equipo con historial podrían coincidir (similitud de nombres; la decisión es del usuario);
* fusiona dos equipos: mueve partidos, alias y cuotas, y une partidos duplicados que dos
  fuentes registraron por separado. El alias queda guardado, así que la próxima sincronización
  ya reconoce el nombre.
"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    Competition,
    Event,
    EventStatistic,
    OddsSnapshot,
    Player,
    Prediction,
    PredictionFeature,
    Settlement,
    Team,
    TeamAlias,
    TeamRating,
)
from app.ingestion.store import MATCH_WINDOW, as_utc
from app.providers.normalize import known_aliases, slug

MIN_SUGGESTION_SIMILARITY = 0.5


class TeamMergeError(ValueError):
    pass


def _similarity(a: str, b: str) -> float:
    sa, sb = slug(a), slug(b)
    if not sa or not sb:
        return 0.0
    ratio = SequenceMatcher(None, sa, sb).ratio()
    ta, tb = set(sa.split("_")), set(sb.split("_"))
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return round(max(ratio, overlap), 3)


def review(s: Session, competition_key: str, min_matches: int) -> dict:
    comp = s.scalar(select(Competition).where(Competition.key == competition_key))
    if comp is None:
        raise TeamMergeError(f"Competición desconocida: {competition_key}")
    finished: dict[int, int] = defaultdict(int)
    scheduled: dict[int, int] = defaultdict(int)
    rows = s.execute(select(Event.home_team_id, Event.away_team_id, Event.status)
                     .where(Event.competition_id == comp.id)).all()
    for home_id, away_id, status in rows:
        target = finished if status == "finished" else scheduled if status == "scheduled" else None
        if target is not None:
            target[home_id] += 1
            target[away_id] += 1
    team_ids = set(finished) | set(scheduled)
    teams = {t.id: t for t in s.scalars(select(Team).where(Team.id.in_(team_ids)))}
    aliases: dict[int, list[str]] = defaultdict(list)
    names: dict[int, set[str]] = {tid: {t.name, *known_aliases(t.name)} for tid, t in teams.items()}
    for team_id, source, alias in s.execute(select(TeamAlias.team_id, TeamAlias.source, TeamAlias.alias)
                                            .where(TeamAlias.team_id.in_(team_ids))):
        aliases[team_id].append(f"{alias} ({source})")
        names[team_id].add(alias)
    with_history = [t for tid, t in teams.items() if finished[tid] >= min_matches]
    out = []
    for tid, t in sorted(teams.items(), key=lambda kv: kv[1].name):
        needs_review = scheduled[tid] > 0 and finished[tid] < min_matches
        suggestions = []
        if needs_review:
            # se compara contra el nombre y todos los alias conocidos del candidato
            scored = sorted(((max(_similarity(n, m) for n in names[tid] for m in names[h.id]), h)
                             for h in with_history if h.id != tid), key=lambda x: x[0], reverse=True)
            suggestions = [{"team_id": h.id, "name": h.name, "similarity": sc, "finished": finished[h.id]}
                           for sc, h in scored[:3] if sc >= MIN_SUGGESTION_SIMILARITY]
        out.append({"team_id": tid, "name": t.name, "finished": finished[tid], "scheduled": scheduled[tid],
                    "aliases": sorted(aliases[tid]), "needs_review": needs_review, "suggestions": suggestions})
    return {"competition": comp.name, "competition_key": comp.key, "min_matches": min_matches, "teams": out,
            "pending_review": sum(1 for r in out if r["needs_review"])}


def _merge_duplicate_event(s: Session, dup: Event, keep: Event) -> dict:
    """Une ``dup`` en ``keep`` (mismo partido registrado por dos fuentes) y borra ``dup``."""
    moved = {"odds": 0, "odds_duplicates": 0, "predictions_dropped": 0}
    if keep.status != "finished" and dup.status == "finished":
        keep.status, keep.home_score, keep.away_score = "finished", dup.home_score, dup.away_score
        keep.home_score_ht, keep.away_score_ht = dup.home_score_ht, dup.away_score_ht
    elif dup.status in ("postponed", "cancelled") and keep.status == "scheduled":
        keep.status = dup.status
    if keep.source.startswith("csv") and not dup.source.startswith("csv"):
        keep.kickoff_utc = dup.kickoff_utc  # la API tiene la hora exacta
    existing = {(o.bookmaker, o.market, o.period, o.participant, o.line, o.selection, as_utc(o.captured_at), o.source)
                for o in s.scalars(select(OddsSnapshot).where(OddsSnapshot.event_id == keep.id))}
    for o in list(s.scalars(select(OddsSnapshot).where(OddsSnapshot.event_id == dup.id))):
        key = (o.bookmaker, o.market, o.period, o.participant, o.line, o.selection, as_utc(o.captured_at), o.source)
        if key in existing:
            s.delete(o)
            moved["odds_duplicates"] += 1
        else:
            o.event_id = keep.id
            existing.add(key)
            moved["odds"] += 1
    stats = {(st.team_side, st.stat, st.source)
             for st in s.scalars(select(EventStatistic).where(EventStatistic.event_id == keep.id))}
    for st in list(s.scalars(select(EventStatistic).where(EventStatistic.event_id == dup.id))):
        if (st.team_side, st.stat, st.source) in stats:
            s.delete(st)
        else:
            st.event_id = keep.id
    kept_preds = {(p.model_version_id, p.market, p.selection, p.line)
                  for p in s.scalars(select(Prediction).where(Prediction.event_id == keep.id))}
    for p in list(s.scalars(select(Prediction).where(Prediction.event_id == dup.id))):
        if (p.model_version_id, p.market, p.selection, p.line) in kept_preds:
            s.execute(delete(Settlement).where(Settlement.prediction_id == p.id))
            s.execute(delete(PredictionFeature).where(PredictionFeature.prediction_id == p.id))
            s.delete(p)
            moved["predictions_dropped"] += 1
        else:
            p.event_id = keep.id
    s.flush()
    s.delete(dup)
    return moved


def merge(s: Session, from_team_id: int, into_team_id: int) -> dict:
    if from_team_id == into_team_id:
        raise TeamMergeError("No se puede fusionar un equipo consigo mismo")
    src, dst = s.get(Team, from_team_id), s.get(Team, into_team_id)
    if src is None or dst is None:
        raise TeamMergeError("Equipo no encontrado")
    if src.sport_id != dst.sport_id:
        raise TeamMergeError("Los equipos son de deportes distintos")
    if src.is_synthetic != dst.is_synthetic:
        raise TeamMergeError("No se mezclan equipos sintéticos con reales")
    report = {"from": src.name, "into": dst.name, "events_moved": 0, "events_merged": 0, "odds_moved": 0,
              "predictions_dropped": 0}

    events = list(s.scalars(select(Event).where(or_(Event.home_team_id == src.id, Event.away_team_id == src.id))))
    for ev in events:
        new_home = dst.id if ev.home_team_id == src.id else ev.home_team_id
        new_away = dst.id if ev.away_team_id == src.id else ev.away_team_id
        if new_home == new_away:
            raise TeamMergeError(f"{src.name} y {dst.name} se enfrentan entre sí: no pueden ser el mismo equipo")
        twins = [t for t in s.scalars(select(Event).where(
            Event.competition_id == ev.competition_id, Event.home_team_id == new_home,
            Event.away_team_id == new_away, Event.id != ev.id))
            if abs(as_utc(t.kickoff_utc) - as_utc(ev.kickoff_utc)) <= MATCH_WINDOW]
        if twins:
            keep = min(twins, key=lambda t: abs(as_utc(t.kickoff_utc) - as_utc(ev.kickoff_utc)))
            moved = _merge_duplicate_event(s, ev, keep)
            report["events_merged"] += 1
            report["odds_moved"] += moved["odds"]
            report["predictions_dropped"] += moved["predictions_dropped"]
        else:
            ev.home_team_id, ev.away_team_id = new_home, new_away
            report["events_moved"] += 1
        s.flush()

    known = {(a.source, a.alias) for a in s.scalars(select(TeamAlias).where(TeamAlias.team_id == dst.id))}
    for a in list(s.scalars(select(TeamAlias).where(TeamAlias.team_id == src.id))):
        if (a.source, a.alias) in known:
            s.delete(a)
        else:
            a.team_id = dst.id
            known.add((a.source, a.alias))
    if ("manual", src.name) not in known:
        s.add(TeamAlias(team_id=dst.id, source="manual", alias=src.name))
    s.execute(update(Player).where(Player.team_id == src.id).values(team_id=dst.id))
    kept = {(r.source, r.valid_from) for r in s.scalars(select(TeamRating).where(TeamRating.team_id == dst.id))}
    for r in list(s.scalars(select(TeamRating).where(TeamRating.team_id == src.id))):
        if (r.source, r.valid_from) in kept:
            s.delete(r)
        else:
            r.team_id = dst.id
    s.flush()
    remaining = s.scalar(select(func.count(Event.id)).where(
        or_(Event.home_team_id == src.id, Event.away_team_id == src.id)))
    if remaining:
        raise TeamMergeError("Quedaron partidos sin mover; fusión cancelada")
    s.delete(src)
    s.flush()
    return report
