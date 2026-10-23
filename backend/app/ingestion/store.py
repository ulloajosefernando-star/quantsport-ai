"""Persistencia idempotente de lotes normalizados de proveedores.

* Equipos: se resuelven por clave canónica y se registra cada alias por fuente.
* Eventos: se emparejan entre fuentes por competición + equipos + kickoff (±36 h, porque
  algunas fuentes no publican la hora). Si el lote trae un resultado, se actualiza.
* Cuotas: inserción sin sobrescritura; los duplicados exactos se omiten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Competition, Event, EventStatistic, OddsSnapshot, Sport, Team, TeamAlias
from app.providers.base import NormalizedEvent, NormalizedOdds, ProviderBatch
from app.providers.normalize import team_key

MATCH_WINDOW = timedelta(hours=36)

SPORTS = {
    "football": ("Fútbol", "partial", "Fase 1: modelo Dixon-Coles operativo; fuentes reales dependen de configuración."),
    "nba": ("NBA", "disabled", "Sin fuente de datos configurada ni modelo validado (Fase 2-3)."),
    "nfl": ("NFL", "disabled", "Sin fuente de datos configurada ni modelo validado (Fase 2-3)."),
    "mlb": ("MLB", "disabled", "Sin fuente de datos configurada ni modelo validado (Fase 2-3)."),
    "tennis": ("Tenis", "disabled", "Sin fuente de datos configurada ni modelo validado (Fase 2-3)."),
}


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite devuelve datetimes sin zona: se interpretan como UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def seed_sports(s: Session) -> None:
    for key, (name, status, reason) in SPORTS.items():
        sp = s.scalar(select(Sport).where(Sport.key == key))
        if sp is None:
            s.add(Sport(key=key, name=name, status=status, status_reason=reason))
    s.flush()


@dataclass
class IngestReport:
    source: str
    events_created: int = 0
    events_updated: int = 0
    events_unchanged: int = 0
    odds_inserted: int = 0
    odds_duplicates: int = 0
    odds_unmatched: int = 0
    warnings: list[str] = field(default_factory=list)


class Ingestor:
    def __init__(self, session: Session) -> None:
        self.s = session
        seed_sports(session)
        self._sports: dict[str, Sport] = {sp.key: sp for sp in session.scalars(select(Sport))}
        self._comps: dict[str, Competition] = {}
        self._teams: dict[tuple[int, str], Team] = {}
        self._events: dict[tuple[int, int, int], list[Event]] = {}

    # ------------------------------------------------------------ lookups
    def competition(self, sport: str, key: str, name: str, country: str, synthetic: bool) -> Competition:
        if key in self._comps:
            return self._comps[key]
        sp = self._sports[sport]
        comp = self.s.scalar(select(Competition).where(Competition.sport_id == sp.id, Competition.key == key))
        if comp is None:
            comp = Competition(sport_id=sp.id, key=key, name=name, country=country, is_synthetic=synthetic)
            self.s.add(comp)
            self.s.flush()
        self._comps[key] = comp
        return comp

    def team(self, sport: str, name: str, source: str, synthetic: bool) -> Team:
        sp = self._sports[sport]
        ck = team_key(name)
        cache_key = (sp.id, ck)
        team = self._teams.get(cache_key)
        if team is None:
            team = self.s.scalar(select(Team).where(Team.sport_id == sp.id, Team.canonical_key == ck))
            if team is None:
                team = Team(sport_id=sp.id, canonical_key=ck, name=name, is_synthetic=synthetic)
                self.s.add(team)
                self.s.flush()
            self._teams[cache_key] = team
            alias_exists = self.s.scalar(
                select(TeamAlias.id).where(TeamAlias.team_id == team.id, TeamAlias.source == source,
                                           TeamAlias.alias == name)
            )
            if not alias_exists:
                self.s.add(TeamAlias(team_id=team.id, source=source, alias=name))
        return team

    def _candidates(self, comp_id: int, home_id: int, away_id: int) -> list[Event]:
        key = (comp_id, home_id, away_id)
        if key not in self._events:
            self._events[key] = list(
                self.s.scalars(
                    select(Event).where(Event.competition_id == comp_id, Event.home_team_id == home_id,
                                        Event.away_team_id == away_id)
                )
            )
        return self._events[key]

    def find_event(self, comp_id: int, home_id: int, away_id: int, kickoff: datetime) -> Event | None:
        best, best_gap = None, MATCH_WINDOW
        for ev in self._candidates(comp_id, home_id, away_id):
            gap = abs(as_utc(ev.kickoff_utc) - kickoff)
            if gap <= best_gap:
                best, best_gap = ev, gap
        return best

    # ------------------------------------------------------------ ingest
    def ingest(self, batch: ProviderBatch) -> IngestReport:
        rep = IngestReport(source=batch.source, warnings=list(batch.warnings))
        for ne in batch.events:
            self._ingest_event(ne, rep)
        self.s.flush()
        self._ingest_odds(batch.odds, rep)
        self.s.flush()
        return rep

    def _ingest_event(self, ne: NormalizedEvent, rep: IngestReport) -> None:
        comp = self.competition(ne.sport, ne.competition_key, ne.competition_name, ne.country, ne.is_synthetic)
        home = self.team(ne.sport, ne.home, ne.source, ne.is_synthetic)
        away = self.team(ne.sport, ne.away, ne.source, ne.is_synthetic)
        ev = self.find_event(comp.id, home.id, away.id, ne.kickoff_utc)
        if ev is None:
            ev = Event(
                sport_id=self._sports[ne.sport].id, competition_id=comp.id, season_label=ne.season,
                kickoff_utc=ne.kickoff_utc, home_team_id=home.id, away_team_id=away.id, status=ne.status,
                home_score=ne.home_score, away_score=ne.away_score, home_score_ht=ne.home_score_ht,
                away_score_ht=ne.away_score_ht, source=ne.source, source_event_id=ne.source_event_id,
                is_synthetic=ne.is_synthetic, fetched_at=datetime.now(UTC),
            )
            self.s.add(ev)
            self.s.flush()
            self._candidates(comp.id, home.id, away.id).append(ev)
            rep.events_created += 1
        else:
            changed = False
            if ne.status == "finished" and (ev.home_score != ne.home_score or ev.away_score != ne.away_score
                                            or ev.status != "finished"):
                if ev.status == "finished" and ev.home_score is not None and ev.source != ne.source:
                    rep.warnings.append(
                        f"Resultado discrepante {ne.home}-{ne.away}: {ev.source}={ev.home_score}-{ev.away_score}, "
                        f"{ne.source}={ne.home_score}-{ne.away_score}. Se conserva el primero."
                    )
                else:
                    ev.home_score, ev.away_score, ev.status = ne.home_score, ne.away_score, "finished"
                    ev.home_score_ht, ev.away_score_ht = ne.home_score_ht, ne.away_score_ht
                    changed = True
            elif ne.status in ("postponed", "cancelled") and ev.status != ne.status:
                ev.status = ne.status
                changed = True
            if (ev.source.startswith("csv") and not ne.source.startswith("csv")
                    and as_utc(ev.kickoff_utc) != ne.kickoff_utc):
                # una API con hora exacta mejora el kickoff de un CSV sin hora
                ev.kickoff_utc = ne.kickoff_utc
                changed = True
            if changed:
                ev.fetched_at = datetime.now(UTC)
                rep.events_updated += 1
            else:
                rep.events_unchanged += 1
        for side, stats in ne.stats.items():
            for stat, value in stats.items():
                exists = self.s.scalar(select(EventStatistic.id).where(
                    EventStatistic.event_id == ev.id, EventStatistic.team_side == side,
                    EventStatistic.stat == stat, EventStatistic.source == ne.source))
                if not exists:
                    self.s.add(EventStatistic(event_id=ev.id, team_side=side, stat=stat, value=value, source=ne.source))

    def _ingest_odds(self, odds: list[NormalizedOdds], rep: IngestReport) -> None:
        existing: dict[int, set[tuple]] = {}
        invalid = 0
        for o in odds:
            if not (o.odds_decimal > 1.0) or o.odds_decimal != o.odds_decimal:
                invalid += 1
                continue
            comp = self._comps.get(o.competition_key) or self.s.scalar(
                select(Competition).where(Competition.key == o.competition_key))
            if comp is None:
                rep.odds_unmatched += 1
                continue
            self._comps[o.competition_key] = comp
            home = self.team(o.sport, o.home, o.source, o.is_synthetic)
            away = self.team(o.sport, o.away, o.source, o.is_synthetic)
            ev = self.find_event(comp.id, home.id, away.id, o.kickoff_utc)
            if ev is None:
                rep.odds_unmatched += 1
                continue
            if ev.id not in existing:
                existing[ev.id] = {
                    (r.bookmaker, r.market, r.line, r.selection, as_utc(r.captured_at), r.source)
                    for r in self.s.scalars(select(OddsSnapshot).where(OddsSnapshot.event_id == ev.id))
                }
            key = (o.bookmaker, o.market, o.line, o.selection, o.captured_at.astimezone(UTC), o.source)
            if key in existing[ev.id]:
                rep.odds_duplicates += 1
                continue
            existing[ev.id].add(key)
            self.s.add(OddsSnapshot(
                event_id=ev.id, bookmaker=o.bookmaker, market=o.market, line=o.line, selection=o.selection,
                odds_decimal=o.odds_decimal, captured_at=o.captured_at, is_closing=o.is_closing, source=o.source,
                is_synthetic=o.is_synthetic,
            ))
            rep.odds_inserted += 1
        if invalid:
            rep.warnings.append(f"{invalid} cuotas inválidas (≤ 1.0) descartadas.")
        if rep.odds_unmatched:
            rep.warnings.append(f"{rep.odds_unmatched} cuotas sin evento emparejado (no se guardaron).")
