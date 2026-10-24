import { useState } from 'react'
import type { EvaluatedSelection, Registry } from '../types/markets'
import { api } from '../services/api'
import { useApi } from '../hooks/useApi'
import { num, pct, signedPct } from '../lib/format'
import { formatOdds, useOddsFormat } from '../lib/odds'
import { Button, Card, CategoryBadge, ModelBadge, Select } from './ui'

interface ManualResult {
  bookmaker: string
  odds_decimal: number
  format: string
  conversions: { display: Record<string, string> }
  evaluation: EvaluatedSelection
  margin: { available: boolean; overround?: number; note: string }
  model_note: string | null
  best_other_odds: number | null
  disclaimer: string
}

const input = 'rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100'
const FORMATS = [
  { value: 'auto', label: 'Detectar' }, { value: 'decimal', label: 'Decimal (2.50)' }, { value: 'american', label: 'Americano (+150)' },
  { value: 'fractional', label: 'Fraccionario (3/2)' }, { value: 'percent', label: 'Porcentaje (40%)' },
]

export function ManualOddsCard({ eventId, home, away, onSaved }: { eventId: number; home: string; away: string; onSaved: () => void }) {
  const reg = useApi<Registry>('/api/markets/registry?sport=football')
  const markets = (reg.data?.sports[0]?.markets ?? []).filter((m) => !m.live_only)
  const [bookmaker, setBookmaker] = useState('Manual')
  const [market, setMarket] = useState('1x2')
  const [selection, setSelection] = useState('home')
  const [line, setLine] = useState('2.5')
  const [odds, setOdds] = useState('')
  const [format, setFormat] = useState('auto')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [res, setRes] = useState<ManualResult | null>(null)
  const [fmt] = useOddsFormat()
  const def = markets.find((m) => m.key === market)
  const label = (s: string) => (def?.selection_labels[s] ?? s).replace('Local', home).replace('Visitante', away)

  const changeMarket = (k: string) => {
    setMarket(k)
    const d = markets.find((m) => m.key === k)
    setSelection(d?.selections?.[0] ?? '1-0')
    if (d?.line === 'required') setLine(d.key === 'european_handicap' ? '-1' : d.key === 'ah' ? '-0.5' : '2.5')
  }

  const submit = async () => {
    setBusy(true)
    setErr(null)
    setRes(null)
    try {
      const r = await api.post<ManualResult>(`/api/events/${eventId}/manual-odds`, {
        bookmaker, market, selection: selection.trim(), odds, format,
        line: def?.line === 'required' ? Number(line) : null,
      })
      setRes(r)
      onSaved()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const a = res?.evaluation
  return (
    <Card title="Evaluar una cuota de cualquier casa o fuente"
      subtitle="Escribe la cuota que ves en cualquier formato. Solo se guarda y se calcula; la plataforma no se conecta a ninguna casa ni apuesta.">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-400">Casa o fuente
          <input value={bookmaker} maxLength={40} onChange={(e) => setBookmaker(e.target.value)} className={`w-32 ${input}`} />
        </label>
        <Select label="Mercado" value={market} onChange={changeMarket}
          options={markets.map((m) => ({ value: m.key, label: `${m.label}${m.has_model ? '' : ' (sin modelo)'}` }))} />
        {def?.selections ? (
          <Select label="Selección" value={selection} onChange={setSelection} options={def.selections.map((s) => ({ value: s, label: label(s) }))} />
        ) : (
          <label className="flex flex-col gap-1 text-xs text-ink-400">Marcador (local-visitante)
            <input value={selection} onChange={(e) => setSelection(e.target.value)} placeholder="2-1" className={`w-20 ${input}`} />
          </label>
        )}
        {def?.line === 'required' && (
          <label className="flex flex-col gap-1 text-xs text-ink-400">Línea
            <input value={line} onChange={(e) => setLine(e.target.value)} inputMode="decimal" className={`w-16 ${input}`} />
          </label>
        )}
        <Select label="Formato" value={format} onChange={setFormat} options={FORMATS} />
        <label className="flex flex-col gap-1 text-xs text-ink-400">Cuota
          <input value={odds} onChange={(e) => setOdds(e.target.value)} placeholder="2.50 / +150 / 3/2 / 40%" className={`w-36 ${input}`} />
        </label>
        <Button variant="primary" onClick={submit} disabled={busy || !odds.trim()}>{busy ? 'Calculando…' : 'Evaluar'}</Button>
      </div>
      {def && <p className="mt-2 text-[11px] text-ink-400">{def.settlement_note}{def.model_reason ? ` · ${def.model_reason}` : ''}</p>}
      {err && <p className="mt-3 text-xs text-rose-300">{err}</p>}
      {res && a && (
        <div className="mt-4 rounded-md border border-ink-700 p-3 text-sm">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="font-medium">{res.bookmaker} · {a.selection.label} · {formatOdds(res.odds_decimal, fmt)}</span>
            <span className="text-xs text-ink-400">({Object.values(res.conversions.display).join(' · ')})</span>
            <CategoryBadge category={a.category} />
            {a.model_status !== 'sin_modelo' && <ModelBadge status={a.model_status} />}
          </div>
          <div className="num grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-ink-300 sm:grid-cols-4">
            <div>Implícita <span className="text-ink-100">{pct(a.implied_probability)}</span></div>
            <div>Sin margen <span className="text-ink-100">{pct(a.p_no_vig)}</span></div>
            <div>Prob. modelo <span className="text-ink-100">{pct(a.p_model)}</span></div>
            <div>Cuota justa <span className="text-ink-100">{formatOdds(a.fair_odds, fmt)}</span></div>
            <div>Cuota mínima <span className="text-ink-100">{formatOdds(a.min_odds, fmt)}</span></div>
            <div>EV <span className="text-ink-100">{signedPct(a.ev)}</span></div>
            <div>Incertidumbre <span className="text-ink-100">{a.uncertainty}</span></div>
            <div>Stake sugerido <span className="text-ink-100">{a.kelly_suggested_fraction != null ? pct(a.kelly_suggested_fraction, 2) : '—'}</span></div>
            <div>Mejor otra fuente <span className="text-ink-100">{res.best_other_odds != null ? formatOdds(res.best_other_odds, fmt) : '—'}</span></div>
            <div>Margen <span className="text-ink-100">{res.margin.available ? pct(res.margin.overround, 2) : '—'}</span></div>
          </div>
          {!res.margin.available && <p className="mt-1 text-[11px] text-ink-400">{res.margin.note}</p>}
          {[...a.reasons, ...a.risks].length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-ink-400">{[...a.reasons, ...a.risks].map((r, i) => <li key={i}>{r}</li>)}</ul>}
          <p className="mt-2 text-[11px] text-ink-400">{res.disclaimer} {a.ev === null ? '' : `EV con ${num(res.odds_decimal)}.`}</p>
        </div>
      )}
    </Card>
  )
}
