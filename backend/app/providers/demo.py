"""Proveedor DEMO: datos 100% SINTÉTICOS para probar la plataforma sin coste.

* Equipos y operadores ficticios ("Demo ..."), para que no se confundan con datos reales.
* Resultados generados con un proceso Dixon-Coles con fuerzas conocidas (semilla fija).
* Cuotas sintéticas: probabilidades de un "mercado" que subestima la localía (sesgo conocido)
  + ruido + margen. Sirven para ejercitar el detector de valor y el control de calidad;
  NO representan el comportamiento de mercados reales.
* Todo registro sale con is_synthetic=True y source="demo:sintetico".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from app.providers.base import (
    Capability,
    DataProvider,
    NormalizedEvent,
    NormalizedOdds,
    ProviderBatch,
    ProviderInfo,
    ProviderKind,
)
from app.sports.football.scores import (
    handicap_distribution,
    probs_1x2,
    probs_btts,
    probs_totals,
    score_matrix,
)

SOURCE = "demo:sintetico"
COMPETITION_KEY = "demo_liga_sintetica"
COMPETITION_NAME = "Liga Demo (SINTÉTICA)"
TEAMS = [
    "Demo Atlético Norte", "Demo Real Costa", "Demo Unión Sierra", "Demo Deportivo Valle",
    "Demo Racing Puerto", "Demo Sporting Lago", "Demo Club Llanura", "Demo Inter Bahía",
    "Demo Olímpico Río", "Demo Celta Meseta", "Demo Estrella Sur", "Demo Juventud Este",
    "Demo Rayo Oeste", "Demo Tigres Isla", "Demo Pumas Cumbre", "Demo Halcones Delta",
]
BOOKMAKERS = ["Operador Demo A", "Operador Demo B", "Operador Demo C"]
TRUE_HOME_ADV = 0.25
TRUE_MU = 0.10
TRUE_RHO = -0.06
N_SEASONS = 3
# Sesgo deliberado del mercado sintético: los operadores demo ignoran la
# ventaja de localía real (ver MARKET_HOME_ADV_SHRINK). Sirve para comprobar que el pipeline detecta un sesgo conocido;
# los mercados reales no tienen por qué comportarse así.
MARKET_HOME_ADV_SHRINK = 0.0


class DemoProvider(DataProvider):
    info = ProviderInfo(
        key="demo",
        name="Demo sintético",
        kind=ProviderKind.DEMO,
        sports=["football"],
        capabilities=[
            Capability.RESULTS, Capability.FIXTURES, Capability.HISTORICAL,
            Capability.ODDS, Capability.HISTORICAL_ODDS, Capability.CLOSING_ODDS,
        ],
        configured=True,
        cost_note="0 MXN",
        coverage_note="Una liga ficticia de 16 equipos, 3 temporadas + próximos 10 días.",
        license_note="Datos generados localmente. NO son estadísticas reales.",
    )

    def __init__(self, seed: int, now: datetime | None = None) -> None:
        self.seed = seed
        self.now = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
        rng = np.random.default_rng(seed)
        self.attack = rng.normal(0, 0.28, len(TEAMS))
        self.defence = rng.normal(0, 0.24, len(TEAMS))
        self._rng = rng
        self._schedule_cache: list[dict] | None = None

    @property
    def _schedule(self) -> list[dict]:
        if self._schedule_cache is None:
            self._schedule_cache = self._build_schedule(self._rng)
        return self._schedule_cache

    # ------------------------------------------------------------ generation
    def _lambdas(self, h: int, a: int, home_adv: float = TRUE_HOME_ADV) -> tuple[float, float]:
        lam = float(np.exp(TRUE_MU + home_adv + self.attack[h] - self.defence[a]))
        mu = float(np.exp(TRUE_MU + self.attack[a] - self.defence[h]))
        return lam, mu

    def _build_schedule(self, rng: np.random.Generator) -> list[dict]:
        """Dos temporadas de ida y vuelta (480 partidos) + jornadas futuras."""
        n = len(TEAMS)
        rounds = []
        teams = list(range(n))
        for _ in range(n - 1):  # round-robin (método del círculo)
            pairs = [(teams[i], teams[n - 1 - i]) for i in range(n // 2)]
            rounds.append(pairs)
            teams = [teams[0], teams[-1], *teams[1:-1]]
        season_rounds = rounds + [[(a, h) for h, a in r] for r in rounds]

        total_rounds = N_SEASONS * len(season_rounds) + 3  # temporadas jugadas + 3 jornadas futuras
        past_rounds = N_SEASONS * len(season_rounds)
        # La última jornada jugada termina hace 2 días; las futuras empiezan en 1-2 días.
        start = self.now - timedelta(days=2 + 7 * (past_rounds - 1))
        schedule = []
        for r in range(total_rounds):
            pairs = season_rounds[r % len(season_rounds)]
            base = start + timedelta(days=7 * r)
            if r >= past_rounds:
                base = self.now + timedelta(days=1 + 3 * (r - past_rounds))
            season = f"Demo {min(r // len(season_rounds), N_SEASONS - 1) + 1}"
            for j, (h, a) in enumerate(pairs):
                kickoff = base.replace(hour=17 + (j % 4))
                lam, mu = self._lambdas(h, a)
                sm = score_matrix(lam, mu, TRUE_RHO)
                finished = r < past_rounds
                hg = ag = None
                if finished:
                    flat = sm.matrix.ravel() / sm.matrix.sum()
                    idx = rng.choice(flat.size, p=flat)
                    hg, ag = (int(v) for v in np.unravel_index(idx, sm.matrix.shape))
                schedule.append(
                    {"round": r, "season": season, "kickoff": kickoff, "h": h, "a": a, "hg": hg, "ag": ag, "sm": sm}
                )
        return schedule

    def _event(self, m: dict) -> NormalizedEvent:
        return NormalizedEvent(
            sport="football",
            competition_key=COMPETITION_KEY,
            competition_name=COMPETITION_NAME,
            country="Ficticio",
            kickoff_utc=m["kickoff"],
            home=TEAMS[m["h"]],
            away=TEAMS[m["a"]],
            season=m["season"],
            status="finished" if m["hg"] is not None else "scheduled",
            home_score=m["hg"],
            away_score=m["ag"],
            source=SOURCE,
            source_event_id=f"demo-{m['round']}-{m['h']}-{m['a']}",
            is_synthetic=True,
        )

    def _noisy(self, probs: list[float], margin: float, rng: np.random.Generator, noise: float) -> list[float]:
        logits = np.log(np.asarray(probs)) + rng.normal(0, noise, len(probs))
        p = np.exp(logits) / np.exp(logits).sum()
        return [max(1.01, round(float(1.0 / (pi * (1 + margin))), 2)) for pi in p]

    def _odds_for(self, m: dict, captured_at: datetime, closing: bool, salt: int = 0) -> list[NormalizedOdds]:
        rng = np.random.default_rng(hash((self.seed, m["round"], m["h"], m["a"], closing, salt)) % 2**32)
        # El "mercado" sintético subestima la localía a propósito (sesgo conocido y documentado)
        lam, mu = self._lambdas(m["h"], m["a"], home_adv=TRUE_HOME_ADV * MARKET_HOME_ADV_SHRINK)
        sm = score_matrix(lam, mu, TRUE_RHO)
        p1x2 = probs_1x2(sm)
        pou = probs_totals(sm, 2.5)
        pbtts = probs_btts(sm)
        out: list[NormalizedOdds] = []
        base = dict(
            sport="football", competition_key=COMPETITION_KEY, kickoff_utc=m["kickoff"],
            home=TEAMS[m["h"]], away=TEAMS[m["a"]], source=SOURCE, is_synthetic=True,
            is_closing=closing, captured_at=captured_at,
        )
        noise = 0.04 if closing else 0.08
        for b, bookmaker in enumerate(BOOKMAKERS):
            margin = 0.045 + 0.01 * b
            o = self._noisy([p1x2["home"], p1x2["draw"], p1x2["away"]], margin, rng, noise)
            for sel, d in zip(("home", "draw", "away"), o, strict=True):
                out.append(NormalizedOdds(bookmaker=bookmaker, market="1x2", selection=sel, odds_decimal=d, **base))
            o = self._noisy([pou["over"], pou["under"]], margin, rng, noise)
            for sel, d in zip(("over", "under"), o, strict=True):
                out.append(
                    NormalizedOdds(bookmaker=bookmaker, market="totals", line=2.5, selection=sel, odds_decimal=d, **base)
                )
            o = self._noisy([pbtts["yes"], pbtts["no"]], margin, rng, noise)
            for sel, d in zip(("yes", "no"), o, strict=True):
                out.append(NormalizedOdds(bookmaker=bookmaker, market="btts", selection=sel, odds_decimal=d, **base))
            # Hándicap asiático en la línea más equilibrada para el local
            line = self._balanced_ah_line(sm)
            ph = handicap_distribution(sm, "home", line, 2.0)
            pa = handicap_distribution(sm, "away", -line, 2.0)
            # probabilidad efectiva de "ganar" excluyendo push para fijar precios simétricos
            eff_h = (ph.expected_value + 1) / 2
            eff_a = (pa.expected_value + 1) / 2
            o = self._noisy([eff_h, eff_a], margin, rng, noise)
            out.append(NormalizedOdds(bookmaker=bookmaker, market="ah", line=line, selection="home", odds_decimal=o[0], **base))
            out.append(NormalizedOdds(bookmaker=bookmaker, market="ah", line=-line, selection="away", odds_decimal=o[1], **base))
        return out

    @staticmethod
    def _balanced_ah_line(sm) -> float:
        best, best_gap = 0.0, 9.0
        for q in range(-10, 11):
            line = q * 0.25
            ev = handicap_distribution(sm, "home", line, 2.0).expected_value
            if abs(ev) < best_gap:
                best, best_gap = line, abs(ev)
        return best

    # ------------------------------------------------------------ interface
    def fetch_competitions(self) -> list[dict]:
        return [{"key": COMPETITION_KEY, "name": COMPETITION_NAME, "is_synthetic": True}]

    def fetch_results(self, competition_key: str = COMPETITION_KEY, season: str | None = None) -> ProviderBatch:
        batch = ProviderBatch(source=SOURCE, fetched_at=self.now)
        for m in self._schedule:
            if m["hg"] is None:
                continue
            batch.events.append(self._event(m))
            batch.odds.extend(self._odds_for(m, m["kickoff"] - timedelta(days=1), closing=False))
            batch.odds.extend(self._odds_for(m, m["kickoff"] - timedelta(minutes=5), closing=True))
        batch.warnings.append("Datos SINTÉTICOS de demostración: no usar para decisiones reales.")
        return batch

    def fetch_fixtures(self, competition_key: str = COMPETITION_KEY, days_ahead: int = 10) -> ProviderBatch:
        batch = ProviderBatch(source=SOURCE, fetched_at=self.now)
        for m in self._schedule:
            if m["hg"] is None and m["kickoff"] <= self.now + timedelta(days=days_ahead):
                batch.events.append(self._event(m))
        return batch

    def fetch_odds(self, competition_key: str = COMPETITION_KEY) -> ProviderBatch:
        batch = ProviderBatch(source=SOURCE, fetched_at=datetime.now(UTC))
        captured = datetime.now(UTC) - timedelta(minutes=10)
        for m in self._schedule:
            if m["hg"] is None:
                # Las cuotas "se mueven" cada 30 min para poblar el historial de cuotas.
                batch.odds.extend(self._odds_for(m, captured, closing=False, salt=int(captured.timestamp() // 1800)))
        return batch
