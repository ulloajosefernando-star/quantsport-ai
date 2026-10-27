import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import { api } from '../services/api'
import type { Betting, CompetitionPerf, MarketEval } from '../types/api'
import { Button, Card, Empty, ErrorBox, Loading, ModelBadge, Stat, SyntheticTag } from '../components/ui'
import { ReliabilityChart } from '../charts/charts'
import { localTime, num, pct, signedPct } from '../lib/format'

interface Resp {
  competitions: CompetitionPerf[]
  nba?: { competition: string; competition_key: string; model_status: string; error?: string | null; gate: { market: string; check: string; passed: boolean; detail: string }[]; walkforward: Record<string, any> }[]
  tracked: { registered: number; settled: number; note?: string; warning?: string; actionable?: Betting; probability_quality?: Record<string, number>; by_category?: Record<string, { n: number; roi: number }>; by_model_version?: Record<string, { n: number; roi: number }> }
  model_versions: { id: number; competition_key: string; name: string; version: string; fingerprint: string; trained_at: string; train_period: [string, string]; validation_period: string; status: string; status_reason: string; is_synthetic: boolean }[]
}

const MARKETS: { key: '1x2' | 'totals_2.5' | 'btts'; label: string }[] = [
  { key: '1x2', label: '1X2' }, { key: 'totals_2.5', label: 'Over/Under 2.5' }, { key: 'btts', label: 'Ambos anotan' },
]

function MarketBlock({ label, m, status }: { label: string; m: MarketEval; status?: string }) {
  const vm = m.vs_baseline_mercado
  return (
    <div className="rounded-lg border border-ink-700 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-sm font-medium">{label}</h4>
        {status && <ModelBadge status={status} />}
      </div>
      <table className="w-full text-xs">
        <thead><tr className="border-b border-ink-700"><th className="py-1"></th><th className="py-1 text-right">Brier</th><th className="py-1 text-right">Log Loss</th></tr></thead>
        <tbody>
          <tr className="border-b border-ink-800"><td className="py-1">Modelo</td><td className="num py-1 text-right">{num(m.model.brier as number, 4)}</td><td className="num py-1 text-right">{num(m.model.log_loss as number, 4)}</td></tr>
          <tr className="border-b border-ink-800"><td className="py-1">Baseline frecuencias</td><td className="num py-1 text-right">{num(m.baseline_frecuencias.brier, 4)}</td><td className="num py-1 text-right">{num(m.baseline_frecuencias.log_loss, 4)}</td></tr>
          {m.baseline_mercado && <tr className="border-b border-ink-800"><td className="py-1">Mercado sin margen</td><td className="num py-1 text-right">{num(m.baseline_mercado.brier, 4)}</td><td className="num py-1 text-right">{num(m.baseline_mercado.log_loss, 4)}</td></tr>}
        </tbody>
      </table>
      <div className="mt-2 text-[11px] text-ink-400">
        ECE {num(m.model.ece as number, 3)}{m.model.auc != null && <> · AUC {num(m.model.auc as number, 3)}</>}
        {vm && <> · Δ log loss vs mercado {vm.diff > 0 ? '+' : ''}{num(vm.diff, 4)} (IC90 {num(vm.low, 4)} a {num(vm.high, 4)})</>}
      </div>
      <ReliabilityChart bins={m.reliability} height={200} />
      {m.reliability_note && <p className="text-[11px] text-ink-400">{m.reliability_note}</p>}
    </div>
  )
}

function BettingBlock({ b, kind }: { b: Betting; kind?: string }) {
  if (!b || b.n_bets === 0) return <p className="text-sm text-ink-400">{b?.note ?? 'Sin apuestas simuladas.'}</p>
  return (
    <div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Apuestas simuladas" value={b.n_bets} hint={`${b.wins} ganadas / ${b.losses} perdidas`} />
        <Stat label="ROI (= yield, stake plano)" value={signedPct(b.roi)} hint={b.roi_ci90 ? `IC90 ${signedPct(b.roi_ci90[0])} a ${signedPct(b.roi_ci90[1])}` : ''}
          tone={b.roi_ci90 && b.roi_ci90[0] > 0 ? 'good' : 'default'} />
        <Stat label="Drawdown máximo" value={`${num(b.max_drawdown_units, 1)} u`} hint={`Racha perdedora más larga: ${b.longest_losing_streak}`} />
        <Stat label="CLV medio" value={signedPct(b.clv_mean)} hint={`${b.clv_n ?? 0} con cuota de cierre`} tone={(b.clv_mean ?? 0) > 0 ? 'good' : 'warn'} />
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2 text-xs">
        <div><div className="mb-1 text-ink-400">Por mercado</div>{Object.entries(b.by_market ?? {}).map(([k, v]) => <div key={k} className="num">{k}: {v.n} apuestas · ROI {signedPct(v.roi)}</div>)}</div>
        <div><div className="mb-1 text-ink-400">Por rango de cuota</div>{Object.entries(b.by_odds_range ?? {}).map(([k, v]) => <div key={k} className="num">{k}: {v.n} · ROI {signedPct(v.roi)}</div>)}</div>
      </div>
      <p className={`mt-3 text-xs ${kind === 'sintetica' ? 'text-fuchsia-300' : 'text-ink-400'}`}>{b.note}</p>
      <p className="mt-1 text-xs text-ink-400">Un ROI histórico positivo no demuestra rentabilidad futura: revisa el intervalo, la muestra y el periodo.</p>
    </div>
  )
}

export default function Performance() {
  const { data, error, loading, reload } = useApi<Resp>('/api/performance')
  const [msg, setMsg] = useState<string | null>(null)
  const act = async (path: string) => {
    try {
      const r = await api.post<Record<string, number>>(path)
      setMsg(Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(' · '))
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  if (loading && !data) return <Loading label="Ejecutando validación walk-forward…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data) return null
  const t = data.tracked
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Rendimiento y validación</h1>

      <Card title="Pronósticos registrados (seguimiento real)" subtitle="Snapshots guardados antes del inicio y liquidados con el resultado final"
        actions={<><Button onClick={() => act('/api/predictions/snapshot')}>Registrar snapshot</Button><Button onClick={() => act('/api/predictions/settle')}>Liquidar</Button></>}>
        {msg && <p className="mb-3 text-xs text-emerald-300">{msg}</p>}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Registrados" value={t.registered} />
          <Stat label="Liquidados" value={t.settled} />
          <Stat label="Brier (binarios)" value={num(t.probability_quality?.brier, 4)} hint={t.probability_quality ? `n=${t.probability_quality.n}` : ''} />
          <Stat label="Log Loss (binarios)" value={num(t.probability_quality?.log_loss, 4)} />
        </div>
        {t.actionable && t.actionable.n_bets > 0 && <div className="mt-4"><BettingBlock b={t.actionable} /></div>}
        <p className="mt-3 text-xs text-ink-400">{t.note}</p>
        {t.warning && <p className="mt-1 text-xs text-fuchsia-300">{t.warning}</p>}
      </Card>

      {data.competitions.length === 0 && <Empty>No hay competiciones con datos.</Empty>}
      {data.competitions.map((c) => {
        const w = c.walkforward
        return (
          <Card key={c.competition_key}
            title={<span className="flex items-center gap-2">{c.competition} {c.is_synthetic && <SyntheticTag />}</span>}
            subtitle={w.status === 'ok' ? `Walk-forward: ${w.n_eval} predicciones fuera de muestra, ${w.refits} reentrenamientos, test ${localTime(w.test_period?.[0])} → ${localTime(w.test_period?.[1])}. ${w.leakage_check}` : (w.reason ?? c.error ?? '')}
            actions={<span className="text-xs">1X2: <ModelBadge status={c.model_status} /></span>}>
            {w.status !== 'ok' ? <Empty>{w.reason ?? c.error ?? 'Validación no disponible'}</Empty> : (
              <div className="space-y-5">
                <div className="grid gap-4 lg:grid-cols-3">
                  {MARKETS.map((mk) => w[mk.key] && (
                    <MarketBlock key={mk.key} label={mk.label} m={w[mk.key] as MarketEval}
                      status={c.gate.find((g) => g.market === mk.key) ? (c.gate.filter((g) => g.market === mk.key).every((g) => g.passed) ? 'production' : undefined) : undefined} />
                  ))}
                </div>
                <div>
                  <h3 className="mb-2 text-sm font-medium">Controles de calidad por mercado</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <tbody>
                        {c.gate.map((g, i) => (
                          <tr key={i} className="border-b border-ink-800">
                            <td className="py-1 pr-3 font-mono">{g.market}</td>
                            <td className="py-1 pr-3">{g.check}</td>
                            <td className={`py-1 pr-3 ${g.passed ? 'text-emerald-300' : 'text-rose-300'}`}>{g.passed ? 'Supera' : 'No supera'}</td>
                            <td className="py-1 text-ink-300">{g.detail}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div>
                  <h3 className="mb-2 text-sm font-medium">Simulación económica (cuota media pre-cierre, stake plano 1 u)</h3>
                  {w.betting && <BettingBlock b={w.betting} kind={w.odds_kind} />}
                </div>
              </div>
            )}
          </Card>
        )
      })}

      {(data.nba ?? []).map((c) => {
        const w = c.walkforward
        const ml = w.moneyline as (MarketEval & { margin_mae?: number; total_mae?: number }) | undefined
        return (
          <Card key={c.competition_key} title={`${c.competition} (baloncesto)`}
            subtitle={w.status === 'ok' ? `Walk-forward: ${w.n_eval} partidos fuera de muestra, reentreno cada ${w.refit_days} días, test ${localTime(w.test_period?.[0])} → ${localTime(w.test_period?.[1])}. Con cuotas guardadas: ${w.market_reference?.con_cuotas ?? 0}.` : (w.reason ?? c.error ?? '')}
            actions={<span className="text-xs">Ganador: <ModelBadge status={c.model_status} /></span>}>
            {w.status !== 'ok' || !ml ? <Empty>{w.reason ?? c.error ?? 'Validación no disponible'}</Empty> : (
              <div className="space-y-4">
                <div className="grid gap-4 lg:grid-cols-2">
                  <MarketBlock label="Ganador (moneyline)" m={ml} />
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <Stat label="Acierto del modelo" value={pct(ml.model.accuracy as number, 1)} />
                      <Stat label="Error medio diferencia" value={`${num(ml.margin_mae, 1)} pts`} hint="Hándicap: |predicho − real|" />
                      <Stat label="Error medio total" value={`${num(ml.total_mae, 1)} pts`} hint="Totales: |predicho − real|" />
                    </div>
                    <table className="w-full text-xs">
                      <tbody>
                        {c.gate.map((g, i) => (
                          <tr key={i} className="border-b border-ink-800">
                            <td className="py-1 pr-3">{g.check}</td>
                            <td className={`py-1 pr-3 ${g.passed ? 'text-emerald-300' : 'text-rose-300'}`}>{g.passed ? 'Supera' : 'No supera'}</td>
                            <td className="py-1 text-ink-300">{g.detail}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="text-xs text-ink-400">Hándicap y totales siguen experimentales: no hay cuotas históricas gratuitas para validarlos contra el mercado; se compararán con las cuotas que guarde el programador.</p>
                  </div>
                </div>
              </div>
            )}
          </Card>
        )
      })}

      <Card title="Versiones de modelo">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-ink-700"><th className="py-1.5">Competición</th><th className="py-1.5">Modelo</th><th className="py-1.5">Huella</th><th className="py-1.5">Entrenado</th><th className="py-1.5">Periodo validación</th><th className="py-1.5">Estado 1X2</th></tr></thead>
            <tbody>
              {data.model_versions.map((m) => (
                <tr key={m.id} className="border-b border-ink-800" title={m.status_reason}>
                  <td className="py-1.5">{m.competition_key}</td>
                  <td className="py-1.5 font-mono">{m.name} v{m.version}</td>
                  <td className="py-1.5 font-mono">{m.fingerprint}</td>
                  <td className="py-1.5">{localTime(m.trained_at)}</td>
                  <td className="py-1.5">{m.validation_period ? m.validation_period.split(' → ').map((d) => d.slice(0, 10)).join(' → ') : '—'}</td>
                  <td className="py-1.5"><ModelBadge status={m.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-ink-400">Rendimiento por versión (pronósticos liquidados): {Object.entries(t.by_model_version ?? {}).map(([k, v]) => `#${k}: ${v.n} · ROI ${pct(v.roi)}`).join(' | ') || 'sin datos aún'}</p>
      </Card>
    </div>
  )
}
