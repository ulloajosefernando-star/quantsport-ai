import { useParams, Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import type { Assessment, Category } from '../types/api'
import { Card, CategoryBadge, Empty, ErrorBox, Loading, ModelBadge, SyntheticTag } from '../components/ui'
import { OddsHistoryChart, ProbBar, ScoreHeatmap } from '../charts/charts'
import { MARKET_LABEL, SELECTION_LABEL, localTime, num, pct, signedPct } from '../lib/format'

interface EventAnalysis {
  event_id: number
  competition: string
  home: string
  away: string
  kickoff_utc: string
  kickoff_local: string
  status: string
  is_synthetic: boolean
  source: string
  model: (Record<string, unknown> & { version: string; fingerprint: string; n_matches: number; effective_n: number; rho: number; rho_estimated: boolean; home_advantage: number; train_period: [string, string]; status: string; market_status: Record<string, string>; gate: { market: string; check: string; passed: boolean; detail: string }[] }) | null
  model_status: string
  probabilities: {
    lambda_home: number
    lambda_away: number
    tail_mass: number
    '1x2': { home: number; draw: number; away: number }
    double_chance: Record<string, number>
    totals: Record<string, { over: number; under: number }>
    btts: { yes: number; no: number }
    team_totals: { home: Record<string, { over: number; under: number }>; away: Record<string, { over: number; under: number }> }
    top_scores: { home: number; away: number; p: number }[]
    intervals_90: Record<string, [number, number]>
    xg_intervals_90: { home: [number, number]; away: [number, number] }
  } | null
  assessments: Assessment[]
  factors: { factor: string; home: number | null; away: number | null; detail: string }[]
  limitations: string[]
  data_quality: { last_result_used: string | null; issues: string[]; event_fetched_at: string | null }
  odds_table: { bookmaker: string; market: string; line: number | null; selection: string; odds: number; captured_at: string; source: string }[]
  odds_history: { captured_at: string; bookmaker: string; selection: string; odds: number }[]
  margin_analysis: { bookmaker: string; overround: number; methods: Record<string, Record<string, number>>; max_disagreement: number }[]
  summary_category: Category
}

export default function EventDetail() {
  const { id } = useParams()
  const { data, error, loading, reload } = useApi<EventAnalysis>(`/api/events/${id}`)
  if (loading && !data) return <Loading label="Analizando evento…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data) return null
  const p = data.probabilities
  const iv = p?.intervals_90 ?? {}
  return (
    <div className="space-y-6">
      <div>
        <Link to="/cartelera" className="text-xs text-ink-400 hover:text-ink-100">← Cartelera</Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">{data.home} <span className="text-ink-400">vs</span> {data.away}</h1>
          {data.is_synthetic && <SyntheticTag />}
          <CategoryBadge category={data.summary_category} />
        </div>
        <p className="mt-1 text-sm text-ink-400">
          {data.competition} · {localTime(data.kickoff_utc)} (CDMX) · fuente {data.source} · modelo 1X2 <ModelBadge status={data.model_status} />
        </p>
      </div>

      {data.data_quality.issues.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
          {data.data_quality.issues.map((x, i) => <div key={i}>{x}</div>)}
        </div>
      )}

      {!p ? <Empty>Sin probabilidades: datos insuficientes para este partido.</Empty> : (
        <>
          <div className="grid gap-6 lg:grid-cols-3">
            <Card title="Probabilidades 1X2" subtitle="Intervalo del 90% por incertidumbre de parámetros">
              <ProbBar p={p['1x2']} />
              <table className="mt-4 w-full text-sm">
                <tbody>
                  {(['home', 'draw', 'away'] as const).map((s) => (
                    <tr key={s} className="border-b border-ink-800">
                      <td className="py-1.5">{SELECTION_LABEL[s]}</td>
                      <td className="num py-1.5 text-right">{pct(p['1x2'][s])}</td>
                      <td className="num py-1.5 text-right text-xs text-ink-400">{iv[`1x2:${s}`] ? `${pct(iv[`1x2:${s}`][0])} – ${pct(iv[`1x2:${s}`][1])}` : ''}</td>
                      <td className="num py-1.5 text-right text-xs text-ink-400">justa {num(1 / p['1x2'][s])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                {Object.entries(p.double_chance).map(([k, v]) => (
                  <div key={k} className="rounded-md bg-ink-850 py-1.5"><div className="text-ink-400">{k}</div><div className="num">{pct(v)}</div></div>
                ))}
              </div>
            </Card>
            <Card title="Goles esperados" subtitle={`Masa fuera de la matriz 0–10: ${p.tail_mass.toExponential(1)}`}>
              <div className="grid grid-cols-2 gap-3 text-center">
                <div className="rounded-lg bg-ink-850 p-3">
                  <div className="text-xs text-ink-400">{data.home}</div>
                  <div className="num text-2xl font-semibold">{num(p.lambda_home)}</div>
                  <div className="num text-[11px] text-ink-400">IC90 {num(p.xg_intervals_90.home[0])}–{num(p.xg_intervals_90.home[1])}</div>
                </div>
                <div className="rounded-lg bg-ink-850 p-3">
                  <div className="text-xs text-ink-400">{data.away}</div>
                  <div className="num text-2xl font-semibold">{num(p.lambda_away)}</div>
                  <div className="num text-[11px] text-ink-400">IC90 {num(p.xg_intervals_90.away[0])}–{num(p.xg_intervals_90.away[1])}</div>
                </div>
              </div>
              <p className="mt-3 text-xs text-ink-400">Goles esperados del modelo Dixon-Coles (no son xG de tiros: no hay datos de xG verificados en Fase 1).</p>
              <div className="mt-3 rounded-md bg-ink-850 p-2 text-sm">
                Ambos anotan: <span className="num font-medium">{pct(p.btts.yes)}</span>
                <span className="num text-xs text-ink-400"> (IC90 {iv['btts:yes'] ? `${pct(iv['btts:yes'][0])}–${pct(iv['btts:yes'][1])}` : '—'})</span>
              </div>
            </Card>
            <Card title="Over / Under">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-ink-700"><th className="py-1">Línea</th><th className="py-1 text-right">Más</th><th className="py-1 text-right">Menos</th><th className="py-1 text-right">Justa (más)</th></tr></thead>
                <tbody>
                  {Object.entries(p.totals).map(([line, v]) => (
                    <tr key={line} className="border-b border-ink-800">
                      <td className="num py-1.5">{line}</td>
                      <td className="num py-1.5 text-right">{pct(v.over)}</td>
                      <td className="num py-1.5 text-right">{pct(v.under)}</td>
                      <td className="num py-1.5 text-right text-ink-400">{num(1 / v.over)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-3 text-xs text-ink-400">
                Goles por equipo (más de 0.5 / 1.5): {data.home} {pct(p.team_totals.home['0.5'].over, 0)} / {pct(p.team_totals.home['1.5'].over, 0)} · {data.away} {pct(p.team_totals.away['0.5'].over, 0)} / {pct(p.team_totals.away['1.5'].over, 0)}
              </div>
            </Card>
          </div>

          <Card title="Marcadores más probables"><ScoreHeatmap scores={p.top_scores} /></Card>

          <Card title="Evaluación de valor por selección" subtitle="Compara la probabilidad del modelo con la mejor cuota fresca disponible">
            {data.assessments.length === 0 ? <Empty>Sin cuotas: solo análisis deportivo, no oportunidad ejecutable.</Empty> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-ink-700">
                      <th className="py-2 pr-3">Selección</th><th className="py-2 pr-3 text-right">Mejor cuota</th>
                      <th className="py-2 pr-3 text-right">P modelo</th><th className="py-2 pr-3 text-right">P mercado sin margen</th>
                      <th className="py-2 pr-3 text-right">Cuota justa</th><th className="py-2 pr-3 text-right">Edge</th>
                      <th className="py-2 pr-3 text-right">EV (IC90)</th><th className="py-2 pr-3">Validación</th><th className="py-2">Decisión</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.assessments.map((a, i) => (
                      <tr key={i} className="border-b border-ink-800 align-top" title={a.reasons.join(' ')}>
                        <td className="py-2 pr-3">{a.label}<div className="text-[11px] text-ink-400">{MARKET_LABEL[a.market] ?? a.market}</div></td>
                        <td className="num py-2 pr-3 text-right">{num(a.odds)}<div className="text-[11px] text-ink-400">{a.bookmaker ?? '—'}</div></td>
                        <td className="num py-2 pr-3 text-right">{pct(a.p_model)}{a.p_push !== null && <div className="text-[11px] text-ink-400">push {pct(a.p_push)}</div>}</td>
                        <td className="num py-2 pr-3 text-right">{pct(a.p_market_fair)}</td>
                        <td className="num py-2 pr-3 text-right">{num(a.fair_odds)}</td>
                        <td className="num py-2 pr-3 text-right">{signedPct(a.edge)}</td>
                        <td className="num py-2 pr-3 text-right">{signedPct(a.ev)}<div className="text-[11px] text-ink-400">{a.ev_ci90 ? `${signedPct(a.ev_ci90[0], 0)} a ${signedPct(a.ev_ci90[1], 0)}` : ''}</div></td>
                        <td className="py-2 pr-3"><ModelBadge status={a.model_status} /></td>
                        <td className="py-2"><CategoryBadge category={a.category} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Factores estadísticos">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-ink-700"><th className="py-1.5">Factor</th><th className="py-1.5 text-right">{data.home}</th><th className="py-1.5 text-right">{data.away}</th></tr></thead>
            <tbody>
              {data.factors.map((f) => (
                <tr key={f.factor} className="border-b border-ink-800" title={f.detail}>
                  <td className="py-1.5">{f.factor}<div className="text-[11px] text-ink-400">{f.detail}</div></td>
                  <td className="num py-1.5 text-right">{f.home ?? '—'}</td>
                  <td className="num py-1.5 text-right">{f.away ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Modelo y limitaciones">
          {data.model && (
            <div className="mb-3 grid grid-cols-2 gap-2 text-xs text-ink-300">
              <div>Modelo: <span className="font-mono">{String(data.model.name)} v{data.model.version}</span></div>
              <div>Huella: <span className="font-mono">{data.model.fingerprint}</span></div>
              <div>Partidos: {data.model.n_matches} (efectivos {data.model.effective_n})</div>
              <div>ρ Dixon-Coles: {num(data.model.rho, 3)} {data.model.rho_estimated ? '' : '(fijo, poca muestra)'}</div>
              <div>Localía: ×{num(data.model.home_advantage)}</div>
              <div>Entrenado hasta: {localTime(data.model.train_period[1])}</div>
              <div className="col-span-2">Validación por mercado: {Object.entries(data.model.market_status ?? {}).map(([k, v]) => <span key={k} className="mr-3">{k}: <ModelBadge status={v} /></span>)}</div>
            </div>
          )}
          <ul className="list-disc space-y-1.5 pl-4 text-sm text-ink-300">{data.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
          <p className="mt-3 text-xs text-ink-400">Factores que cambiarían la evaluación: alineaciones confirmadas, lesiones verificadas, movimientos de cuota, nuevos resultados.</p>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Historial de cuotas 1X2" subtitle="Snapshots inmutables por operador"><OddsHistoryChart history={data.odds_history} /></Card>
        <Card title="Margen por operador (1X2)" subtitle="Comparación de métodos de eliminación del margen">
          {data.margin_analysis.length === 0 ? <p className="text-sm text-ink-400">Sin mercado 1X2 completo.</p> : (
            <table className="w-full text-sm">
              <thead><tr className="border-b border-ink-700"><th className="py-1.5">Operador</th><th className="py-1.5 text-right">Overround</th><th className="py-1.5 text-right">L prop.</th><th className="py-1.5 text-right">L potencia</th><th className="py-1.5 text-right">L Shin</th></tr></thead>
              <tbody>
                {data.margin_analysis.map((m) => (
                  <tr key={m.bookmaker} className="border-b border-ink-800">
                    <td className="py-1.5">{m.bookmaker}</td>
                    <td className="num py-1.5 text-right">{pct(m.overround, 2)}</td>
                    <td className="num py-1.5 text-right">{pct(m.methods.proportional.home)}</td>
                    <td className="num py-1.5 text-right">{pct(m.methods.power.home)}</td>
                    <td className="num py-1.5 text-right">{pct(m.methods.shin.home)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="mt-2 text-xs text-ink-400">Eliminar el margen no revela la probabilidad verdadera; cada método supone un reparto distinto.</p>
        </Card>
      </div>
    </div>
  )
}
