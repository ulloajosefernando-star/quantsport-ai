"""Ratings Elo de Club Elo (clubelo.com) como SEGUNDA REFERENCIA, no como entrada del modelo.

* Formato CSV de la API de Club Elo: ``Rank,Club,Country,Level,Elo,From,To`` (una fila por club y rango
  de fechas). Los nombres van sin espacios ("ManCity", "RealMadrid"), así que se separan antes de normalizar.
* Cobertura: clubes europeos. No hay ratings de Liga MX.
* Importación MANUAL: el robots.txt de clubelo.com no permite descargas automáticas, así que la plataforma
  no lo llama por sí misma. Descarga el CSV en tu navegador (p. ej. http://api.clubelo.com/2026-10-23) y
  súbelo en Fuentes → Importar CSV.
* Solo se guardan ratings de equipos que ya existen en la base: un nombre sin coincidencia se reporta, no se
  crea un equipo nuevo.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Sport, Team, TeamAlias, TeamRating
from app.ingestion.store import seed_sports
from app.providers.normalize import slug, team_key

SOURCE = "clubelo"
HEADER = ["Rank", "Club", "Country", "Level", "Elo", "From", "To"]


class ClubEloFormatError(ValueError):
    pass


def is_clubelo_csv(columns: list[str]) -> bool:
    return [c.strip() for c in columns][:7] == HEADER


def split_camel(name: str) -> str:
    """'ManCity' -> 'Man City', 'RealMadrid' -> 'Real Madrid'; respeta nombres que ya tienen espacios."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name.strip())


def parse_csv(content: bytes | str) -> pd.DataFrame:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    df = pd.read_csv(io.StringIO(text))
    if not is_clubelo_csv(list(df.columns)):
        raise ClubEloFormatError(f"Se esperaban las columnas {','.join(HEADER)}")
    df = df.dropna(subset=["Club", "Elo", "From"])
    df["Elo"] = pd.to_numeric(df["Elo"], errors="coerce")
    df = df[df["Elo"].between(500, 3000)]
    df["From"] = pd.to_datetime(df["From"], errors="coerce", utc=True)
    df["To"] = pd.to_datetime(df["To"], errors="coerce", utc=True)
    return df.dropna(subset=["From"])


def import_ratings(s: Session, content: bytes | str) -> dict:
    df = parse_csv(content)
    seed_sports(s)
    football = s.scalar(select(Sport).where(Sport.key == "football"))
    teams = {t.canonical_key: t.id for t in s.scalars(select(Team).where(Team.sport_id == football.id,
                                                                          Team.is_synthetic.is_(False)))}
    aliases = {slug(a): tid for a, tid in s.execute(
        select(TeamAlias.alias, TeamAlias.team_id).join(Team, Team.id == TeamAlias.team_id)
        .where(Team.sport_id == football.id, Team.is_synthetic.is_(False)))}
    existing = {(r.team_id, as_naive(r.valid_from)) for r in s.scalars(select(TeamRating).where(TeamRating.source == SOURCE))}
    inserted = duplicates = 0
    unmatched: set[str] = set()
    now = datetime.now(UTC)
    for row in df.itertuples(index=False):
        name = split_camel(str(row.Club))
        tid = teams.get(team_key(name)) or aliases.get(slug(name)) or teams.get(team_key(str(row.Club)))
        if tid is None:
            unmatched.add(str(row.Club))
            continue
        frm = row.From.to_pydatetime()
        if (tid, as_naive(frm)) in existing:
            duplicates += 1
            continue
        existing.add((tid, as_naive(frm)))
        to = row.To.to_pydatetime() if not pd.isna(row.To) else None
        s.add(TeamRating(team_id=tid, source=SOURCE, rating=float(row.Elo), valid_from=frm, valid_to=to,
                         imported_at=now))
        inserted += 1
    s.flush()
    return {"format": "clubelo", "ratings_inserted": inserted, "ratings_duplicates": duplicates,
            "unmatched_clubs": len(unmatched), "unmatched_examples": sorted(unmatched)[:15]}


def as_naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def rating_at(s: Session, team_id: int, when: datetime) -> float | None:
    """Último rating de Club Elo vigente en ``when`` (o el más reciente anterior)."""
    r = s.scalar(select(TeamRating).where(TeamRating.team_id == team_id, TeamRating.source == SOURCE,
                                          TeamRating.valid_from <= when)
                 .order_by(TeamRating.valid_from.desc()).limit(1))
    return float(r.rating) if r else None
