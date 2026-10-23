"""Importador CSV para históricos de fútbol.

Formatos soportados:
1. football-data.co.uk, formato principal (columnas Div, Date, HomeTeam, AwayTeam, FTHG, FTAG...).
2. football-data.co.uk, formato "new" (Country, League, Season, Date, Home, Away, HG, AG...),
   usado por Liga MX, MLS y otras ligas.
3. Formato genérico QuantSport (date, competition, home, away, home_goals, away_goals,
   y opcionalmente odds_home, odds_draw, odds_away, bookmaker, odds_captured_at).

Sobre los timestamps de cuotas: football-data.co.uk no publica la hora exacta de captura de
las cuotas "pre-cierre". Se asigna kickoff - 1 día como aproximación y se marca la fuente con
el sufijo ``:ts_aprox``. Las cuotas de cierre (columnas con "C") se fechan en el kickoff.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from app.providers.base import (
    Capability,
    DataProvider,
    NormalizedEvent,
    NormalizedOdds,
    ProviderBatch,
    ProviderInfo,
    ProviderKind,
)
from app.providers.normalize import COMPETITIONS, canonical_team_name, competition_by_code

SOURCE = "csv:football-data.co.uk"
GENERIC_SOURCE = "csv:generico"

BOOKMAKER_PREFIXES = {
    "B365": "Bet365", "PS": "Pinnacle", "BW": "Bet&Win", "IW": "Interwetten", "WH": "William Hill",
    "VC": "VC Bet", "1XB": "1XBet", "BFE": "Betfair Exchange", "BF": "Betfair", "BFD": "Betfred",
    "BMGM": "BetMGM", "BV": "BetVictor", "CL": "Coral", "LB": "Ladbrokes", "Max": "Máximo mercado",
    "Avg": "Promedio mercado",
}
OU_PREFIXES = {"B365": "Bet365", "P": "Pinnacle", "Max": "Máximo mercado", "Avg": "Promedio mercado", "BFE": "Betfair Exchange"}
AH_PREFIXES = {"B365": "Bet365", "P": "Pinnacle", "Max": "Máximo mercado", "Avg": "Promedio mercado", "BFE": "Betfair Exchange"}


class CsvValidationError(ValueError):
    pass


def _parse_date(date_s: str, time_s: str | None) -> datetime:
    date_s = str(date_s).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(date_s, fmt)
            break
        except ValueError:
            continue
    else:
        raise CsvValidationError(f"Fecha no reconocida: {date_s!r}")
    if time_s and isinstance(time_s, str) and re.match(r"^\d{1,2}:\d{2}$", time_s.strip()):
        hh, mm = time_s.strip().split(":")
        d = d.replace(hour=int(hh), minute=int(mm))
    else:
        d = d.replace(hour=12)  # hora desconocida: mediodía, se documenta
    # football-data.co.uk publica horas en hora del Reino Unido. Se aproxima a UTC sin DST.
    return d.replace(tzinfo=UTC)


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _int(v) -> int | None:
    f = _num(v)
    return None if f is None else int(f)


def detect_format(columns: list[str]) -> str:
    cols = set(columns)
    if {"Div", "HomeTeam", "AwayTeam"} <= cols:
        return "fdcouk_main"
    if {"Country", "League", "Home", "Away"} <= cols:
        return "fdcouk_new"
    if {"date", "competition", "home", "away"} <= cols:
        return "generic"
    raise CsvValidationError(
        "Formato CSV no reconocido. Columnas esperadas: football-data.co.uk (Div/HomeTeam/AwayTeam), "
        "football-data.co.uk 'new' (Country/League/Home/Away) o genérico (date/competition/home/away)."
    )


def parse_csv(content: bytes | str, filename: str = "upload.csv", file_mtime: datetime | None = None) -> ProviderBatch:
    text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    df = pd.read_csv(io.StringIO(text), dtype=str).dropna(how="all")
    df.columns = [c.strip() for c in df.columns]
    fmt = detect_format(list(df.columns))
    batch = ProviderBatch(source=SOURCE if fmt != "generic" else GENERIC_SOURCE, fetched_at=datetime.now(UTC))
    seen: set[tuple] = set()
    for i, row in df.iterrows():
        try:
            ev, odds = _parse_row(row, fmt, file_mtime)
        except CsvValidationError as exc:
            batch.warnings.append(f"{filename} fila {i + 2}: {exc}")
            continue
        key = (ev.competition_key, ev.kickoff_utc.date(), ev.home, ev.away)
        if key in seen:
            batch.warnings.append(f"{filename} fila {i + 2}: partido duplicado omitido ({ev.home} - {ev.away})")
            continue
        seen.add(key)
        batch.events.append(ev)
        batch.odds.extend(odds)
    return batch


def _parse_row(row: pd.Series, fmt: str, file_mtime: datetime | None) -> tuple[NormalizedEvent, list[NormalizedOdds]]:
    if fmt == "fdcouk_main":
        comp = competition_by_code("fdcouk", row["Div"])
        if not comp:
            comp = f"fdcouk_{str(row['Div']).lower()}"
        name = COMPETITIONS.get(comp, {}).get("name", f"Div {row['Div']}")
        home, away = row["HomeTeam"], row["AwayTeam"]
        hg, ag = _int(row.get("FTHG")), _int(row.get("FTAG"))
        hht, aht = _int(row.get("HTHG")), _int(row.get("HTAG"))
        season = ""
        source = SOURCE
    elif fmt == "fdcouk_new":
        comp = competition_by_code("fdcouk_new", (row["Country"], row["League"]))
        if not comp:
            comp = f"fdcouk_{str(row['Country']).lower()}_{str(row['League']).lower()}".replace(" ", "_")
        name = COMPETITIONS.get(comp, {}).get("name", str(row["League"]))
        home, away = row["Home"], row["Away"]
        hg, ag = _int(row.get("HG")), _int(row.get("AG"))
        hht = aht = None
        season = str(row.get("Season", "") or "")
        source = SOURCE
    else:
        comp = str(row["competition"]).strip()
        name = COMPETITIONS.get(comp, {}).get("name", comp)
        home, away = row["home"], row["away"]
        hg, ag = _int(row.get("home_goals")), _int(row.get("away_goals"))
        hht = aht = None
        season = str(row.get("season", "") or "")
        source = GENERIC_SOURCE

    if not isinstance(home, str) or not isinstance(away, str) or not home.strip() or not away.strip():
        raise CsvValidationError("equipos vacíos")
    if (hg is None) != (ag is None):
        raise CsvValidationError("marcador incompleto")
    if hg is not None and (hg < 0 or ag < 0 or hg > 30 or ag > 30):
        raise CsvValidationError("marcador fuera de rango")

    date_col = "Date" if fmt != "generic" else "date"
    time_col = "Time" if fmt != "generic" else "time"
    kickoff = _parse_date(row[date_col], row.get(time_col))
    home_c, away_c = canonical_team_name(home), canonical_team_name(away)
    finished = hg is not None
    ev = NormalizedEvent(
        sport="football", competition_key=comp, competition_name=name,
        country=COMPETITIONS.get(comp, {}).get("country", ""), kickoff_utc=kickoff, home=home_c, away=away_c,
        season=season, status="finished" if finished else "scheduled", home_score=hg, away_score=ag,
        home_score_ht=hht, away_score_ht=aht, source=source,
    )
    stats = {}
    for side, prefix in (("home", "H"), ("away", "A")):
        s = {}
        for stat, col in (("shots", "S"), ("shots_on_target", "ST"), ("corners", "C"), ("fouls", "F"),
                          ("yellow_cards", "Y"), ("red_cards", "R")):
            v = _num(row.get(prefix + col))
            if v is not None:
                s[stat] = v
        if s:
            stats[side] = s
    ev.stats = stats

    odds: list[NormalizedOdds] = []
    pre_ts = kickoff - timedelta(days=1)
    if not finished and file_mtime is not None:
        pre_ts = file_mtime  # fixtures: la mejor referencia disponible es la fecha del archivo
    base = dict(sport="football", competition_key=comp, kickoff_utc=kickoff, home=home_c, away=away_c)

    if fmt == "generic":
        vals = [_num(row.get(c)) for c in ("odds_home", "odds_draw", "odds_away")]
        if all(v and v > 1 for v in vals):
            ts_raw = row.get("odds_captured_at")
            ts = pd.Timestamp(ts_raw).tz_localize("UTC").to_pydatetime() if isinstance(ts_raw, str) and ts_raw else pre_ts
            for sel, v in zip(("home", "draw", "away"), vals, strict=True):
                odds.append(NormalizedOdds(
                    **base, bookmaker=str(row.get("bookmaker") or "desconocido"), market="1x2", selection=sel,
                    odds_decimal=float(v), captured_at=ts, source=GENERIC_SOURCE,
                ))
        return ev, odds

    for prefix, bname in BOOKMAKER_PREFIXES.items():
        for closing in (False, True):
            c = "C" if closing else ""
            cols = [f"{prefix}{c}H", f"{prefix}{c}D", f"{prefix}{c}A"]
            vals = [_num(row.get(col)) for col in cols]
            if all(v and v > 1 for v in vals):
                for sel, v in zip(("home", "draw", "away"), vals, strict=True):
                    odds.append(NormalizedOdds(
                        **base, bookmaker=bname, market="1x2", selection=sel, odds_decimal=float(v),
                        captured_at=kickoff if closing else pre_ts, is_closing=closing,
                        source=SOURCE if closing else f"{SOURCE}:ts_aprox",
                    ))
    for prefix, bname in OU_PREFIXES.items():
        for closing in (False, True):
            c = "C" if closing else ""
            over, under = _num(row.get(f"{prefix}{c}>2.5")), _num(row.get(f"{prefix}{c}<2.5"))
            if over and under and over > 1 and under > 1:
                for sel, v in (("over", over), ("under", under)):
                    odds.append(NormalizedOdds(
                        **base, bookmaker=bname, market="totals", line=2.5, selection=sel, odds_decimal=v,
                        captured_at=kickoff if closing else pre_ts, is_closing=closing,
                        source=SOURCE if closing else f"{SOURCE}:ts_aprox",
                    ))
    for closing in (False, True):
        line = _num(row.get("AHCh" if closing else "AHh"))
        if line is None or abs(line * 4 - round(line * 4)) > 1e-9:
            continue
        for prefix, bname in AH_PREFIXES.items():
            c = "C" if closing else ""
            h, a = _num(row.get(f"{prefix}{c}AHH")), _num(row.get(f"{prefix}{c}AHA"))
            if h and a and h > 1 and a > 1:
                odds.append(NormalizedOdds(
                    **base, bookmaker=bname, market="ah", line=line, selection="home", odds_decimal=h,
                    captured_at=kickoff if closing else pre_ts, is_closing=closing,
                    source=SOURCE if closing else f"{SOURCE}:ts_aprox",
                ))
                odds.append(NormalizedOdds(
                    **base, bookmaker=bname, market="ah", line=-line, selection="away", odds_decimal=a,
                    captured_at=kickoff if closing else pre_ts, is_closing=closing,
                    source=SOURCE if closing else f"{SOURCE}:ts_aprox",
                ))
    return ev, odds


class CsvProvider(DataProvider):
    """Lee los CSV colocados en data/imports/ (o subidos desde el dashboard)."""

    info = ProviderInfo(
        key="csv",
        name="Importación CSV (football-data.co.uk / genérico)",
        kind=ProviderKind.DATASET,
        sports=["football"],
        capabilities=[Capability.RESULTS, Capability.HISTORICAL, Capability.HISTORICAL_ODDS,
                      Capability.CLOSING_ODDS, Capability.STATISTICS],
        configured=True,
        docs_url="https://www.football-data.co.uk/data.php",
        cost_note="0 MXN (descarga manual de CSV)",
        coverage_note="Ligas europeas principales; Liga MX y MLS en formato 'new' (solo cuotas de cierre).",
        license_note="Uso según las condiciones de football-data.co.uk; atribuir la fuente.",
    )

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def files(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(p for p in self.directory.glob("*.csv") if p.is_file())

    def load_all(self) -> ProviderBatch:
        batch = ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC))
        for p in self.files():
            mtime = datetime.fromtimestamp(p.stat().st_mtime, UTC)
            sub = parse_csv(p.read_bytes(), p.name, mtime)
            batch.events.extend(sub.events)
            batch.odds.extend(sub.odds)
            batch.warnings.extend(sub.warnings)
        return batch
