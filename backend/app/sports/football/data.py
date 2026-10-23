"""Acceso a datos de fútbol en formato tabular para modelos y backtesting."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.db.models import Event, OddsSnapshot, Team
from app.probability.margin import proportional


def matches_frame(s: Session, competition_id: int) -> pd.DataFrame:
    home, away = aliased(Team), aliased(Team)
    rows = s.execute(
        select(Event.id, Event.kickoff_utc, home.name, away.name, Event.home_score, Event.away_score,
               Event.status, Event.updated_at, Event.is_synthetic)
        .join(home, Event.home_team_id == home.id)
        .join(away, Event.away_team_id == away.id)
        .where(Event.competition_id == competition_id)
    ).all()
    df = pd.DataFrame(rows, columns=["event_id", "kickoff", "home", "away", "home_goals", "away_goals",
                                     "status", "updated_at", "is_synthetic"])
    if df.empty:
        return df
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    return df.sort_values("kickoff").reset_index(drop=True)


def finished(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[(df["status"] == "finished") & df["home_goals"].notna()].copy()


def odds_frame(s: Session, event_ids: list[int]) -> pd.DataFrame:
    if not event_ids:
        return pd.DataFrame(columns=["event_id", "bookmaker", "market", "line", "selection", "odds",
                                     "captured_at", "is_closing", "source", "is_synthetic"])
    rows = []
    for chunk in np.array_split(np.array(event_ids), max(1, len(event_ids) // 500)):
        rows.extend(s.execute(
            select(OddsSnapshot.event_id, OddsSnapshot.bookmaker, OddsSnapshot.market, OddsSnapshot.line,
                   OddsSnapshot.selection, OddsSnapshot.odds_decimal, OddsSnapshot.captured_at,
                   OddsSnapshot.is_closing, OddsSnapshot.source, OddsSnapshot.is_synthetic)
            .where(OddsSnapshot.event_id.in_([int(x) for x in chunk]))
        ).all())
    df = pd.DataFrame(rows, columns=["event_id", "bookmaker", "market", "line", "selection", "odds",
                                     "captured_at", "is_closing", "source", "is_synthetic"])
    if not df.empty:
        df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True)
    return df


# Pseudo-operador agregado del CSV: mezcla operadores distintos, no se usa para consenso.
EXCLUDED_FROM_CONSENSUS = {"Máximo mercado"}

MARKET_SELECTIONS = {"1x2": ("home", "draw", "away"), "totals": ("over", "under"), "btts": ("yes", "no")}


def consensus_fair(odds: pd.DataFrame, market: str, line: float | None, closing: bool) -> dict[int, dict[str, float]]:
    """Probabilidad de mercado sin margen (proporcional) promediada entre operadores.

    Usa, por operador, el snapshot más reciente que tenga el mercado completo.
    Devuelve {event_id: {selection: p}}.
    """
    sels = MARKET_SELECTIONS[market]
    sub = odds[(odds["market"] == market) & (odds["is_closing"] == closing)
               & ~odds["bookmaker"].isin(EXCLUDED_FROM_CONSENSUS)]
    if line is not None:
        sub = sub[np.isclose(sub["line"].astype(float), line)]
    out: dict[int, dict[str, float]] = {}
    if sub.empty:
        return out
    sub = sub.sort_values("captured_at").groupby(["event_id", "bookmaker", "selection"]).tail(1)
    for ev_id, g in sub.groupby("event_id"):
        fairs = []
        for _, gb in g.groupby("bookmaker"):
            m = dict(zip(gb["selection"], gb["odds"], strict=True))
            if all(s in m for s in sels):
                fairs.append(proportional([m[s] for s in sels]))
        if fairs:
            avg = np.mean(fairs, axis=0)
            out[int(ev_id)] = dict(zip(sels, (float(x) for x in avg), strict=True))
    return out


def average_odds(odds: pd.DataFrame, market: str, line: float | None, closing: bool) -> dict[int, dict[str, float]]:
    """Cuota media entre operadores (más conservadora que la mejor cuota)."""
    sub = odds[(odds["market"] == market) & (odds["is_closing"] == closing)
               & ~odds["bookmaker"].isin(EXCLUDED_FROM_CONSENSUS)]
    if line is not None:
        sub = sub[np.isclose(sub["line"].astype(float), line)]
    if sub.empty:
        return {}
    sub = sub.sort_values("captured_at").groupby(["event_id", "bookmaker", "selection"]).tail(1)
    agg = sub.groupby(["event_id", "selection"])["odds"].mean()
    out: dict[int, dict[str, float]] = {}
    for (ev_id, sel), v in agg.items():
        out.setdefault(int(ev_id), {})[sel] = float(v)
    return out
