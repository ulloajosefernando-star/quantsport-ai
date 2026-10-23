import {
  CartesianGrid,
  ComposedChart,
  ErrorBar,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { P1x2, ReliabilityBin } from '../types/api'
import { localTime, pct } from '../lib/format'

const GRID = '#1c2a44'
const AXIS = '#7f8fae'
const tooltipStyle = { background: '#0f182a', border: '1px solid #2a3b5c', borderRadius: 8, fontSize: 12 }

/** Barra apilada 1X2 (local / empate / visitante). */
export function ProbBar({ p, compact = false }: { p: P1x2 | null; compact?: boolean }) {
  if (!p) return <span className="text-xs text-ink-400">—</span>
  const parts = [
    { k: 'L', v: p.home, cls: 'bg-emerald-500/70' },
    { k: 'E', v: p.draw, cls: 'bg-slate-500/70' },
    { k: 'V', v: p.away, cls: 'bg-sky-500/70' },
  ]
  return (
    <div className={compact ? 'w-40' : 'w-full'}>
      <div className="flex h-2 overflow-hidden rounded-full bg-ink-800">
        {parts.map((x) => (
          <div key={x.k} className={x.cls} style={{ width: `${x.v * 100}%` }} title={`${x.k} ${pct(x.v)}`} />
        ))}
      </div>
      <div className="num mt-1 flex justify-between text-[11px] text-ink-300">
        {parts.map((x) => (
          <span key={x.k}>
            {x.k} {pct(x.v, 0)}
          </span>
        ))}
      </div>
    </div>
  )
}

/** Curva de calibración: probabilidad media predicha vs frecuencia observada con IC de Wilson. */
export function ReliabilityChart({ bins, height = 240 }: { bins: ReliabilityBin[]; height?: number }) {
  const data = bins.map((b) => ({
    x: b.mean_predicted,
    y: b.observed_rate,
    err: [b.observed_rate - b.ci_low, b.ci_high - b.observed_rate],
    n: b.count,
  }))
  const diag = [{ x: 0, d: 0 }, { x: 1, d: 1 }]
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
        <XAxis type="number" dataKey="x" domain={[0, 1]} tickFormatter={(v) => pct(v, 0)} stroke={AXIS} fontSize={11}
          label={{ value: 'Probabilidad predicha', position: 'insideBottom', offset: -8, fill: AXIS, fontSize: 11 }} />
        <YAxis type="number" domain={[0, 1]} tickFormatter={(v) => pct(v, 0)} stroke={AXIS} fontSize={11} width={44} />
        <Tooltip contentStyle={tooltipStyle}
          formatter={(v, name) => (name === 'Observado' ? pct(Number(v)) : String(v))}
          labelFormatter={(v) => `Predicho ${pct(Number(v))}`} />
        <Line data={diag} dataKey="d" stroke="#2a3b5c" dot={false} strokeDasharray="4 4" name="Calibración perfecta" isAnimationActive={false} />
        <Scatter data={data} dataKey="y" fill="#34d399" name="Observado">
          <ErrorBar dataKey="err" width={4} stroke="#34d399" direction="y" />
        </Scatter>
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** Historial de cuotas 1X2 por operador (snapshots inmutables). */
export function OddsHistoryChart({ history }: {
  history: { captured_at: string; bookmaker: string; selection: string; odds: number }[]
}) {
  const keys = Array.from(new Set(history.map((h) => `${h.selection}|${h.bookmaker}`)))
  const byTime = new Map<string, Record<string, number | string>>()
  for (const h of history) {
    const row = byTime.get(h.captured_at) ?? { t: h.captured_at }
    row[`${h.selection}|${h.bookmaker}`] = h.odds
    byTime.set(h.captured_at, row)
  }
  const data = Array.from(byTime.values()).sort((a, b) => String(a.t).localeCompare(String(b.t)))
  const colors: Record<string, string> = { home: '#34d399', draw: '#94a3b8', away: '#38bdf8' }
  const dashes = ['', '4 3', '1 3', '6 2']
  const books = Array.from(new Set(history.map((h) => h.bookmaker)))
  if (data.length < 2) {
    return <p className="text-xs text-ink-400">Se necesitan al menos dos capturas de cuotas para dibujar la evolución.</p>
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
        <XAxis dataKey="t" tickFormatter={(v) => localTime(v)} stroke={AXIS} fontSize={11} minTickGap={30} />
        <YAxis stroke={AXIS} fontSize={11} width={40} domain={['auto', 'auto']} />
        <Tooltip contentStyle={tooltipStyle} labelFormatter={(v) => localTime(String(v))} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {keys.map((k) => {
          const [sel, bm] = k.split('|')
          return (
            <Line key={k} type="stepAfter" dataKey={k} name={`${sel === 'home' ? 'L' : sel === 'draw' ? 'E' : 'V'} · ${bm}`}
              stroke={colors[sel] ?? '#fff'} strokeDasharray={dashes[books.indexOf(bm) % dashes.length]} dot={false}
              connectNulls isAnimationActive={false} />
          )
        })}
      </LineChart>
    </ResponsiveContainer>
  )
}

/** Mapa de calor de marcadores (0–5 x 0–5) con probabilidades del modelo. */
export function ScoreHeatmap({ scores }: { scores: { home: number; away: number; p: number }[] }) {
  const max = Math.max(...scores.map((s) => s.p), 0.0001)
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {scores.map((s) => (
        <div key={`${s.home}-${s.away}`} className="rounded-lg p-2 text-center ring-1 ring-ink-700"
          style={{ background: `rgba(52, 211, 153, ${0.08 + 0.5 * (s.p / max)})` }}>
          <div className="num text-lg font-semibold">{s.home}–{s.away}</div>
          <div className="num text-xs text-ink-300">{pct(s.p)}</div>
        </div>
      ))}
    </div>
  )
}
