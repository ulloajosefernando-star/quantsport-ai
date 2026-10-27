import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import { Button, Card, Disclaimer, Empty, ErrorBox, Loading, Select, Stat, SyntheticTag } from '../components/ui'
import { TZ, num, pct, signedPct } from '../lib/format'

type Outcome = 'home' | 'draw' | 'away'

interface PredictionRow {
  market: string
  line: number | null
  selection: string
  label: string
  p_model: number | null
  odds: number | null
  bookmaker: string
  ev: number | null
  category: string
  closing_odds: number | null
  clv: number | null
  state: string
  profit: number | null
}

interface ResultRow {
  event_id: number
  kickoff_local: string
  home: string
  away: string
  home_score: number
  away_score: number
  ht: [number, number] | null
  outcome: Outcome
  total_goals: number
  btts: boolean
  market_1x2: number[] | null
  closing_odds_1x2: number[] | null
  market_over25: number | null
  market_btts: number | null
  model_1x2: number[] | null
  model_margin?: number | null
  model_total?: number | null
  model_over25: number | null
  model_btts: number | null
  model_pick: Outcome | null
  model_hit: boolean | null
  predictions: PredictionRow[]
}

interface Summary {
  n_matches: number
  home_win?: number
  draw?: number
  away_win?: number
  avg_goals?: number
  over_2_5?: number
  btts?: number
  team?: { name: string; won: number; drawn: number; lost: number; goals_for: number; goals_against: number }
  model_vs_market?: { n_model: number; model_accuracy: number | null; n?: number; model_log_loss?: number; market_log_loss?: number; market_accuracy?: number }
  predictions?: { n: number; hit_rate?: number; roi?: number; avg_clv?: number | null; n_clv?: number }
}

interface Resp {
  competitions: { key: string; name: string; is_synthetic: boolean; sport?: string }[]
  competition: { key: string; name: string; is_synthetic: boolean; sport?: string } | null
  selections?: Outcome[]
  teams: string[]
  items: ResultRow[]
  total: number
  page: number
  page_size: number
  summary: Summary
  notes?: string[]
}

const STATE_META: Record<string, { label: string; cls: string }> = {
  gana: { label: 'Gana', cls: 'text-emerald-300' },
  medio_gana: { label: 'Medio gana', cls: 'text-emerald-300' },
  push: { label: 'Devuelta', cls: 'text-ink-300' },
  medio_pierde: { label: 'Medio pierde', cls: 'text-rose-300' },
  pierde: { label: 'Pierde', cls: 'text-rose-300' },
  anulada: { label: 'Anulada', cls: 'text-ink-400' },
  pendiente: { label: 'Pendiente', cls: 'text-amber-300' },
}

const DATE_FMT = new Intl.DateTimeFormat('es-MX', { timeZone: TZ, day: '2-digit', month: 'short', year: 'numeric' })

function Probs({ p, outcome, sels }: { p: number[] | null; outcome: Outcome; sels: Outcome[] }) {
  if (!p) return <span className="text-ink-500">—</span>
  return (
    <span className="num whitespace-nowrap">
      {p.map((x, i) => (
        <span key={i} className={sels[i] === outcome ? 'font-semibold text-ink-100' : 'text-ink-400'}>
          {i > 0 && <span className="text-ink-600"> · </span>}
          {pct(x, 0)}
        </span>
      ))}
    </span>
  )
}

export default function Results() {
  const [competition, setCompetition] = useState('')
  const [team, setTeam] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<number | null>(null)

  const qs = new URLSearchParams({ page: String(page), page_size: '50' })
  if (competition) qs.set('competition', competition)
  if (team) qs.set('team', team)
  if (from) qs.set('date_from', from)
  if (to) qs.set('date_to', to)
  const { data, error, loading, reload } = useApi<Resp>(`/api/results?${qs}`)

  const reset = (fn: () => void) => {
    fn()
    setPage(1)
    setOpen(null)
  }

  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data) return <Loading />

  const s = data.summary
  const nba = data.competition?.sport === 'nba'
  const sels: Outcome[] = data.selections ?? ['home', 'draw', 'away']
  const mainLabel = nba ? 'ganador' : '1X2'
  const mvm = s.model_vs_market
  const pr = s.predictions
  const pages = Math.max(1, Math.ceil(data.total / data.page_size))
  const dateCls = 'rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100 focus:border-emerald-500 focus:outline-none'

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          Resultados {data.competition?.is_synthetic && <SyntheticTag />}
        </h1>
        <p className="text-sm text-ink-400">
          Partidos jugados: qué daba el mercado al cierre, qué dijo el modelo antes del partido y cómo terminaron los pronósticos registrados.
        </p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Competición" value={competition || data.competition?.key || ''}
            onChange={(v) => reset(() => { setCompetition(v); setTeam('') })}
            options={data.competitions.map((c) => ({ value: c.key, label: c.name }))} />
          <Select label="Equipo" value={team} onChange={(v) => reset(() => setTeam(v))}
            options={[{ value: '', label: 'Todos' }, ...data.teams.map((t) => ({ value: t, label: t }))]} />
          <label className="flex flex-col gap-1 text-xs text-ink-400">Desde
            <input type="date" value={from} onChange={(e) => reset(() => setFrom(e.target.value))} className={dateCls} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-400">Hasta
            <input type="date" value={to} onChange={(e) => reset(() => setTo(e.target.value))} className={dateCls} />
          </label>
          {(team || from || to) && <Button variant="ghost" onClick={() => reset(() => { setTeam(''); setFrom(''); setTo('') })}>Quitar filtros</Button>}
          {loading && <span className="text-xs text-ink-400">Actualizando…</span>}
        </div>
      </Card>

      {s.n_matches === 0 ? (
        <Empty>No hay partidos jugados con estos filtros. Importa resultados en Fuentes o espera a la tarea diaria del programador.</Empty>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {s.team ? (
              <Stat label={s.team.name} value={nba ? `${s.team.won}G ${s.team.lost}P` : `${s.team.won}G ${s.team.drawn}E ${s.team.lost}P`}
                hint={`${nba ? 'Puntos' : 'Goles'} ${s.team.goals_for}–${s.team.goals_against} en ${s.n_matches} partidos`} />
            ) : (
              <Stat label="Partidos" value={s.n_matches}
                hint={nba ? `Local ${pct(s.home_win, 0)} · Visita ${pct(s.away_win, 0)}`
                  : `Local ${pct(s.home_win, 0)} · Empate ${pct(s.draw, 0)} · Visita ${pct(s.away_win, 0)}`} />
            )}
            {nba ? (
              <Stat label="Puntos por partido" value={num(s.avg_goals, 1)} hint="Suma de ambos equipos, prórroga incluida" />
            ) : (
              <Stat label="Goles por partido" value={num(s.avg_goals, 2)} hint={`Más de 2.5: ${pct(s.over_2_5, 0)} · Ambos anotan: ${pct(s.btts, 0)}`} />
            )}
            <Stat label={`Acierto del modelo (${mainLabel})`} value={pct(mvm?.model_accuracy, 1)}
              hint={mvm?.market_accuracy != null
                ? `Favorito del mercado: ${pct(mvm.market_accuracy, 1)} · ${mvm.n_model} partidos`
                : `${mvm?.n_model ?? 0} partidos con predicción`} />
            {pr && pr.n > 0 ? (
              <Stat label="Pronósticos liquidados" value={signedPct(pr.roi)} tone={(pr.roi ?? 0) > 0 ? 'good' : 'bad'}
                hint={`ROI en ${pr.n} · acierto ${pct(pr.hit_rate, 0)}${pr.avg_clv != null ? ` · CLV ${signedPct(pr.avg_clv)}` : ''}`} />
            ) : (
              <Stat label="Pronósticos liquidados" value="0" hint="El programador los registra antes de cada partido." />
            )}
          </div>

          {mvm?.model_log_loss != null && (
            <p className="text-xs text-ink-400">
              Log loss {mainLabel} en {mvm.n} partidos con cuotas de cierre: modelo <span className="num text-ink-200">{num(mvm.model_log_loss, 4)}</span> vs
              mercado <span className="num text-ink-200">{num(mvm.market_log_loss, 4)}</span> (menor es mejor).{' '}
              {mvm.model_log_loss > (mvm.market_log_loss ?? 0)
                ? 'En este periodo el mercado de cierre fue más preciso que el modelo.'
                : 'En este periodo el modelo fue más preciso que el mercado de cierre; con muestras pequeñas puede ser azar.'}
            </p>
          )}

          <Card title={`${data.total} partidos`} subtitle={`Probabilidades en orden ${nba ? 'Local · Visita' : 'Local · Empate · Visita'}; en negrita, lo que pasó. Toca un partido para ver los pronósticos registrados.`}
            actions={pages > 1 && (
              <>
                <Button disabled={page <= 1} onClick={() => { setPage(page - 1); setOpen(null) }}>Anterior</Button>
                <span className="num text-xs text-ink-400">{page} / {pages}</span>
                <Button disabled={page >= pages} onClick={() => { setPage(page + 1); setOpen(null) }}>Siguiente</Button>
              </>
            )}>
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead>
                  <tr className="border-b border-ink-700 text-left text-xs text-ink-400">
                    <th className="px-4 py-2 font-medium">Fecha</th>
                    <th className="px-2 py-2 font-medium">Partido</th>
                    <th className="px-2 py-2 text-center font-medium">Marcador</th>
                    <th className="px-2 py-2 font-medium">Mercado al cierre</th>
                    <th className="px-2 py-2 font-medium">Modelo</th>
                    <th className="px-2 py-2 text-center font-medium">Modelo acertó</th>
                    <th className="px-4 py-2 text-right font-medium">Pronósticos</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => {
                    const settled = r.predictions.filter((p) => p.profit != null)
                    const profit = settled.reduce((a, p) => a + (p.profit ?? 0), 0)
                    return (
                      <Fragment key={r.event_id}>
                        <tr onClick={() => setOpen(open === r.event_id ? null : r.event_id)}
                          className="cursor-pointer border-b border-ink-800 hover:bg-ink-850">
                          <td className="whitespace-nowrap px-4 py-2 text-xs text-ink-400">{DATE_FMT.format(new Date(r.kickoff_local))}</td>
                          <td className="px-2 py-2">
                            <span className={r.outcome === 'home' ? 'font-medium' : ''}>{r.home}</span>
                            <span className="text-ink-500"> vs </span>
                            <span className={r.outcome === 'away' ? 'font-medium' : ''}>{r.away}</span>
                          </td>
                          <td className="num whitespace-nowrap px-2 py-2 text-center">
                            {r.home_score}–{r.away_score}
                            {r.ht && <span className="ml-1 text-[11px] text-ink-500">({r.ht[0]}–{r.ht[1]})</span>}
                          </td>
                          <td className="px-2 py-2 text-xs"><Probs p={r.market_1x2} outcome={r.outcome} sels={sels} /></td>
                          <td className="px-2 py-2 text-xs"><Probs p={r.model_1x2} outcome={r.outcome} sels={sels} /></td>
                          <td className="px-2 py-2 text-center text-xs">
                            {r.model_hit == null ? <span className="text-ink-500">—</span>
                              : r.model_hit ? <span className="text-emerald-300">Sí</span> : <span className="text-rose-300">No</span>}
                          </td>
                          <td className="num px-4 py-2 text-right text-xs">
                            {r.predictions.length === 0 ? <span className="text-ink-500">—</span>
                              : <span className={settled.length ? (profit >= 0 ? 'text-emerald-300' : 'text-rose-300') : 'text-amber-300'}>
                                  {r.predictions.length}{settled.length > 0 && ` · ${profit >= 0 ? '+' : ''}${num(profit, 2)} u`}
                                </span>}
                          </td>
                        </tr>
                        {open === r.event_id && (
                          <tr className="border-b border-ink-800 bg-ink-950/40">
                            <td colSpan={7} className="px-4 py-3 text-xs">
                              <div className="mb-2 flex flex-wrap gap-x-6 gap-y-1 text-ink-400">
                                <span>Cuotas medias de cierre: <span className="num text-ink-200">{r.closing_odds_1x2 ? r.closing_odds_1x2.map((o) => num(o, 2)).join(' · ') : '—'}</span></span>
                                {nba ? (
                                  <>
                                    <span>Diferencia (local − visita): real <span className="num text-ink-200">{r.home_score - r.away_score}</span>, modelo <span className="num text-ink-200">{num(r.model_margin, 1)}</span></span>
                                    <span>Puntos totales: real <span className="num text-ink-200">{r.total_goals}</span>, modelo <span className="num text-ink-200">{num(r.model_total, 1)}</span></span>
                                  </>
                                ) : (
                                  <>
                                    <span>Más de 2.5 ({r.total_goals > 2 ? 'sí' : 'no'}): mercado <span className="num text-ink-200">{pct(r.market_over25, 0)}</span>, modelo <span className="num text-ink-200">{pct(r.model_over25, 0)}</span></span>
                                    <span>Ambos anotan ({r.btts ? 'sí' : 'no'}): mercado <span className="num text-ink-200">{pct(r.market_btts, 0)}</span>, modelo <span className="num text-ink-200">{pct(r.model_btts, 0)}</span></span>
                                  </>
                                )}
                                <Link to={`/eventos/${r.event_id}`} className="text-emerald-300 hover:underline">Ver detalle</Link>
                              </div>
                              {r.predictions.length === 0 ? (
                                <p className="text-ink-500">No se registraron pronósticos para este partido.</p>
                              ) : (
                                <table className="w-full">
                                  <thead>
                                    <tr className="text-left text-ink-400">
                                      <th className="py-1 font-medium">Selección</th><th className="py-1 font-medium">Casa</th>
                                      <th className="py-1 text-right font-medium">Prob. modelo</th><th className="py-1 text-right font-medium">Cuota</th>
                                      <th className="py-1 text-right font-medium">EV</th><th className="py-1 text-right font-medium">Cierre</th>
                                      <th className="py-1 text-right font-medium">CLV</th><th className="py-1 text-right font-medium">Resultado</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {r.predictions.map((p, i) => {
                                      const st = STATE_META[p.state] ?? { label: p.state, cls: 'text-ink-300' }
                                      return (
                                        <tr key={i} className="border-t border-ink-800">
                                          <td className="py-1">{p.label}</td>
                                          <td className="py-1 text-ink-400">{p.bookmaker || '—'}</td>
                                          <td className="num py-1 text-right">{pct(p.p_model, 1)}</td>
                                          <td className="num py-1 text-right">{num(p.odds, 2)}</td>
                                          <td className="num py-1 text-right">{signedPct(p.ev)}</td>
                                          <td className="num py-1 text-right">{num(p.closing_odds, 2)}</td>
                                          <td className={`num py-1 text-right ${(p.clv ?? 0) > 0 ? 'text-emerald-300' : ''}`}>{signedPct(p.clv)}</td>
                                          <td className={`py-1 text-right ${st.cls}`}>{st.label}{p.profit != null && <span className="num"> ({p.profit >= 0 ? '+' : ''}{num(p.profit, 2)} u)</span>}</td>
                                        </tr>
                                      )
                                    })}
                                  </tbody>
                                </table>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {data.notes && (
        <ul className="list-disc space-y-1 pl-5 text-xs text-ink-400">
          {data.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
      <Disclaimer />
    </div>
  )
}
