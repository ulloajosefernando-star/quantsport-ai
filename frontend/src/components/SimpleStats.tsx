import { useApi } from '../hooks/useApi'
import { Card, Empty, Loading } from './ui'
import { num, pct } from '../lib/format'

interface FormRow { date: string; opponent: string; venue: 'home' | 'away'; score: string; result: 'G' | 'E' | 'P' }
interface Rec { n: number; win?: number; draw?: number; loss?: number; gf?: number; ga?: number; over?: number; btts?: number; clean_sheet?: number; failed_to_score?: number }
interface Backtest {
  status: string
  n: number
  '1x2'?: { accuracy: number; log_loss: number; market?: { n: number; accuracy: number; log_loss: number; method_accuracy: number; method_log_loss: number } }
  over_2_5?: { accuracy: number }
  btts?: { accuracy: number }
}
interface Stats {
  home: string
  away: string
  n_matches: number
  reason?: string
  league?: { home: number; draw: number; away: number; over: number; btts: number; n: number }
  form?: { home: FormRow[]; away: FormRow[] }
  form_summary?: { home: Rec; away: Rec }
  venue?: { home: Rec; away: Rec }
  h2h?: { date: string; home: string; away: string; score: string }[]
  probabilities?: { '1x2': { home: number; draw: number; away: number }; over_2_5: number; btts: number; goals_home: number; goals_away: number }
  market_1x2?: { home: number; draw: number; away: number }
  low_sample?: boolean
  params?: { n_form: number; n_venue: number }
  backtest?: Backtest
}

const CHIP: Record<string, string> = {
  G: 'bg-emerald-500/20 text-emerald-200', E: 'bg-ink-700 text-ink-200', P: 'bg-rose-500/20 text-rose-200',
}

function Form({ rows }: { rows: FormRow[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {rows.map((r, i) => (
        <span key={i} title={`${r.date} · ${r.venue === 'home' ? 'vs' : 'en'} ${r.opponent} · ${r.score}`}
          className={`inline-flex h-6 w-6 items-center justify-center rounded text-xs font-semibold ${CHIP[r.result]}`}>{r.result}</span>
      ))}
    </div>
  )
}

function RecordTable({ home, away, labelHome, labelAway }: { home: Rec; away: Rec; labelHome: string; labelAway: string }) {
  const rows: [string, (r: Rec) => string][] = [
    ['Partidos', (r) => String(r.n)],
    ['Ganó', (r) => pct(r.win, 0)],
    ['Empató', (r) => pct(r.draw, 0)],
    ['Perdió', (r) => pct(r.loss, 0)],
    ['Goles a favor por partido', (r) => num(r.gf, 2)],
    ['Goles en contra por partido', (r) => num(r.ga, 2)],
    ['Más de 2.5 goles', (r) => pct(r.over, 0)],
    ['Ambos anotan', (r) => pct(r.btts, 0)],
    ['Portería a cero', (r) => pct(r.clean_sheet, 0)],
  ]
  return (
    <table className="w-full text-sm">
      <thead><tr className="border-b border-ink-700 text-xs text-ink-400"><th className="py-1.5 text-left font-medium"></th>
        <th className="py-1.5 text-right font-medium">{labelHome}</th><th className="py-1.5 text-right font-medium">{labelAway}</th></tr></thead>
      <tbody>
        {rows.map(([label, fn]) => (
          <tr key={label} className="border-b border-ink-800">
            <td className="py-1.5 text-ink-300">{label}</td>
            <td className="num py-1.5 text-right">{home.n ? fn(home) : '—'}</td>
            <td className="num py-1.5 text-right">{away.n ? fn(away) : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function SimpleStats({ eventId }: { eventId: number }) {
  const { data, error, loading } = useApi<Stats>(`/api/events/${eventId}/stats`)
  if (loading && !data) return <Loading label="Calculando estadísticas…" />
  if (error || !data) return null
  if (data.reason || !data.probabilities || !data.venue || !data.form) {
    return <Card title="Estadística con resultados pasados"><Empty>{data.reason ?? 'Sin datos suficientes.'}</Empty></Card>
  }
  const p = data.probabilities
  const m = data.market_1x2
  const bt = data.backtest
  const btm = bt?.['1x2']?.market
  const nv = data.params?.n_venue ?? 15
  const sels = [['home', `Gana ${data.home}`], ['draw', 'Empate'], ['away', `Gana ${data.away}`]] as const
  return (
    <div className="space-y-4">
      <Card title="Probabilidad según resultados pasados"
        subtitle={`Promedio de cómo le va a ${data.home} en casa y a ${data.away} de visita (últimos ${nv} partidos de cada uno). Sin modelo: solo conteo de resultados.`}>
        <div className="grid gap-3 sm:grid-cols-3">
          {sels.map(([k, label]) => (
            <div key={k} className="rounded-lg bg-ink-850 p-3 text-center">
              <div className="text-xs text-ink-400">{label}</div>
              <div className="num text-2xl font-semibold">{pct(p['1x2'][k], 0)}</div>
              <div className="num text-[11px] text-ink-400">
                cuota justa {num(1 / p['1x2'][k], 2)}{m && <> · mercado {pct(m[k], 0)}</>}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-3 text-sm">
          <div className="rounded-md bg-ink-850 p-2">Más de 2.5 goles: <span className="num font-medium">{pct(p.over_2_5, 0)}</span></div>
          <div className="rounded-md bg-ink-850 p-2">Ambos anotan: <span className="num font-medium">{pct(p.btts, 0)}</span></div>
          <div className="rounded-md bg-ink-850 p-2">Goles esperados: <span className="num font-medium">{num(p.goals_home, 1)} – {num(p.goals_away, 1)}</span></div>
        </div>
        {data.low_sample && <p className="mt-2 text-xs text-amber-300">Uno de los equipos tiene pocos partidos en esa condición: la cifra se acerca a la media de la liga.</p>}
        {bt?.status === 'ok' && bt['1x2'] && (
          <p className="mt-3 text-xs text-ink-400">
            ¿Qué tan bien funciona este método? En {bt.n} partidos pasados de esta liga, calculado cada vez solo con partidos anteriores,
            el resultado más probable acertó el <span className="num text-ink-200">{pct(bt['1x2'].accuracy, 1)}</span> de las veces
            {btm && <> (el favorito de las casas acertó el <span className="num text-ink-200">{pct(btm.accuracy, 1)}</span>)</>}.
            Más de 2.5 acertó {pct(bt.over_2_5?.accuracy, 0)} y ambos anotan {pct(bt.btts?.accuracy, 0)}.
            {btm && btm.method_log_loss > btm.log_loss && ' Las casas fueron más precisas: estas cifras sirven para entender el partido, no para apostar.'}
          </p>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Local en casa y visitante fuera" subtitle={`Últimos ${nv} partidos de cada uno en esa condición`}>
          <RecordTable home={data.venue.home} away={data.venue.away} labelHome={`${data.home} (casa)`} labelAway={`${data.away} (fuera)`} />
        </Card>
        <Card title="Forma reciente" subtitle={`Últimos ${data.params?.n_form ?? 10} partidos, el más reciente primero (G gana, E empata, P pierde)`}>
          <div className="space-y-3 text-sm">
            <div><div className="mb-1 text-ink-300">{data.home}</div><Form rows={data.form.home} /></div>
            <div><div className="mb-1 text-ink-300">{data.away}</div><Form rows={data.form.away} /></div>
          </div>
          <h3 className="mb-1 mt-4 text-sm font-medium">Cara a cara</h3>
          {!data.h2h?.length ? <p className="text-xs text-ink-400">No se han enfrentado en los datos disponibles.</p> : (
            <table className="w-full text-xs">
              <tbody>
                {data.h2h.map((h, i) => (
                  <tr key={i} className="border-b border-ink-800">
                    <td className="whitespace-nowrap py-1 pr-2 text-ink-400">{h.date}</td><td className="py-1">{h.home}</td>
                    <td className="num whitespace-nowrap px-2 py-1 text-center">{h.score}</td><td className="py-1 text-right">{h.away}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  )
}
