"""Control de calidad de modelos, POR MERCADO.

Un mercado solo puede emitir "candidatos de valor" si su validación fuera de muestra:
1. tiene muestra suficiente,
2. está calibrada (ECE ≤ umbral),
3. supera al baseline de frecuencias de la liga (IC90 del Δ log loss < 0),
4. no es peor que el mercado sin margen (Δ log loss ≤ tolerancia configurable).
Si falta evidencia (muestra o cuotas) el estado es "experimental"; si la evidencia es
contraria, "blocked". Los mercados que no se evalúan (p. ej. hándicap asiático en Fase 1)
quedan "experimental".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import ValueThresholds

PRODUCTION = "production"
EXPERIMENTAL = "experimental"
BLOCKED = "blocked"
VALIDATED_MARKETS = {"1x2": "1x2", "totals_2.5": "totals_2.5", "btts": "btts"}


@dataclass
class GateResult:
    status: str
    checks: list[dict] = field(default_factory=list)
    markets: dict[str, dict] = field(default_factory=dict)

    @property
    def reasons(self) -> list[str]:
        return [c["detail"] for c in self.checks if not c["passed"]]

    def market_status(self, market: str, line: float | None) -> str:
        key = "totals_2.5" if market == "totals" and line is not None and abs(line - 2.5) < 1e-9 else market
        return self.markets.get(key, {}).get("status", EXPERIMENTAL)


def _evaluate_section(name: str, sec: dict, n: int, th: ValueThresholds) -> tuple[str, list[dict]]:
    checks: list[dict] = []

    def add(check: str, passed: bool, detail: str) -> None:
        checks.append({"market": name, "check": check, "passed": bool(passed), "detail": detail})

    add("muestra", n >= th.min_eval_sample, f"{n} predicciones fuera de muestra (mínimo {th.min_eval_sample})")
    ece = sec["model"]["ece"]
    add("calibracion", ece <= th.max_ece, f"ECE = {ece:.3f} (máximo {th.max_ece:.3f})")
    vb = sec["vs_baseline_frecuencias"]
    add("supera_baseline_frecuencias", vb["high"] < 0,
        f"Δ log loss vs frecuencias = {vb['diff']:+.4f} (IC90 [{vb['low']:+.4f}, {vb['high']:+.4f}])")
    if "vs_baseline_mercado" in sec:
        vm = sec["vs_baseline_mercado"]
        add("no_peor_que_mercado", vm["diff"] <= th.max_logloss_gap_vs_market,
            f"Δ log loss vs mercado sin margen = {vm['diff']:+.4f} (IC90 [{vm['low']:+.4f}, {vm['high']:+.4f}]; "
            f"tolerancia {th.max_logloss_gap_vs_market:+.4f})")
    else:
        add("no_peor_que_mercado", False, "Sin cuotas históricas para comparar con el mercado")

    if all(c["passed"] for c in checks):
        return PRODUCTION, checks
    if n < th.min_eval_sample or "vs_baseline_mercado" not in sec:
        return EXPERIMENTAL, checks
    return BLOCKED, checks


def evaluate(wf: dict, th: ValueThresholds) -> GateResult:
    if wf.get("status") != "ok":
        detail = wf.get("reason", "Backtest no disponible")
        return GateResult(EXPERIMENTAL, [{"market": "todos", "check": "muestra", "passed": False, "detail": detail}])
    n = wf["n_eval"]
    markets: dict[str, dict] = {}
    all_checks: list[dict] = []
    for key in VALIDATED_MARKETS:
        if key not in wf:
            continue
        status, checks = _evaluate_section(key, wf[key], n, th)
        markets[key] = {"status": status, "checks": checks}
        all_checks.extend(checks)
    overall = markets.get("1x2", {}).get("status", EXPERIMENTAL)
    return GateResult(overall, all_checks, markets)
