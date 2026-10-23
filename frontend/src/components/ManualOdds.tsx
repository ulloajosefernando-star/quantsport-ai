import { useState } from 'react'
import type { Assessment } from '../types/api'
import { api } from '../services/api'
import { num, pct, signedPct } from '../lib/format'
import { Button, Card, CategoryBadge, ModelBadge, Select } from './ui'

interface ManualResult {
  bookmaker: string
  odds_decimal: number
  american: string
  assessment: Assessment | null
  best_other_odds: number | null
  disclaimer: string
}

// Mercado + selección en una sola lista para que el formulario sea corto
const PICKS: { value: string; label: string; market: string; selection: string; line?: boolean }[] = [
  { value: '1x2:home', label: '1X2 · Local', market: '1x2', selection: 'home' },
  { value: '1x2:draw', label: '1X2 · Empate', market: '1x2', selection: 'draw' },
  { value: '1x2:away', label: '1X2 · Visitante', market: '1x2', selection: 'away' },
  { value: 'totals:over', label: 'Goles · Más de', market: 'totals', selection: 'over', line: true },
  { value: 'totals:under', label: 'Goles · Menos de', market: 'totals', selection: 'under', line: true },
  { value: 'btts:yes', label: 'Ambos anotan · Sí', market: 'btts', selection: 'yes' },
  { value: 'btts:no', label: 'Ambos anotan · No', market: 'btts', selection: 'no' },
]

const input = 'rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100'

export function ManualOddsCard({ eventId, home, away, onSaved }: { eventId: number; home: string; away: string; onSaved: () => void }) {
  const [bookmaker, setBookmaker] = useState('Playdoit')
  const [pick, setPick] = useState('1x2:home')
  const [line, setLine] = useState('2.5')
  const [odds, setOdds] = useState('')
  const [format, setFormat] = useState('decimal')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [res, setRes] = useState<ManualResult | null>(null)
  const chosen = PICKS.find((p) => p.value === pick)!

  const submit = async () => {
    setBusy(true)
    setErr(null)
    setRes(null)
    try {
      const r = await api.post<ManualResult>(`/api/events/${eventId}/manual-odds`, {
        bookmaker, market: chosen.market, selection: chosen.selection, odds, format,
        line: chosen.line ? Number(line) : null,
      })
      setRes(r)
      onSaved()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const a = res?.assessment
  return (
    <Card title="Evaluar una cuota que viste en otra casa"
      subtitle="Escribe la cuota tal como la ves (p. ej. en Playdoit). Solo se guarda y se calcula; la plataforma no se conecta a la casa ni apuesta.">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-400">Casa
          <input value={bookmaker} maxLength={40} onChange={(e) => setBookmaker(e.target.value)} className={`w-32 ${input}`} />
        </label>
        <Select label="Selección" value={pick} onChange={setPick}
          options={PICKS.map((p) => ({ value: p.value, label: p.label.replace('Local', home).replace('Visitante', away) }))} />
        {chosen.line && (
          <label className="flex flex-col gap-1 text-xs text-ink-400">Línea
            <input value={line} onChange={(e) => setLine(e.target.value)} inputMode="decimal" className={`w-16 ${input}`} />
          </label>
        )}
        <Select label="Formato" value={format} onChange={setFormat}
          options={[{ value: 'decimal', label: 'Decimal (2.50)' }, { value: 'american', label: 'Americana (+150)' }]} />
        <label className="flex flex-col gap-1 text-xs text-ink-400">Cuota
          <input value={odds} onChange={(e) => setOdds(e.target.value)} placeholder={format === 'decimal' ? '2.50' : '+150'}
            className={`w-24 ${input}`} />
        </label>
        <Button variant="primary" onClick={submit} disabled={busy || !odds.trim()}>{busy ? 'Calculando…' : 'Evaluar'}</Button>
      </div>
      {err && <p className="mt-3 text-xs text-rose-300">{err}</p>}
      {res && (
        <div className="mt-4 rounded-md border border-ink-700 p-3 text-sm">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="font-medium">{res.bookmaker} · {num(res.odds_decimal)} ({res.american})</span>
            {a && <CategoryBadge category={a.category} />}
            {a && <ModelBadge status={a.model_status} />}
          </div>
          {!a ? <p className="text-ink-400">Cuota guardada, pero el modelo no tiene probabilidad para esta selección.</p> : (
            <>
              <div className="num grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-ink-300 sm:grid-cols-4">
                <div>Prob. modelo <span className="text-ink-100">{pct(a.p_model)}</span></div>
                <div>Cuota justa <span className="text-ink-100">{num(a.fair_odds)}</span></div>
                <div>Cuota mínima <span className="text-ink-100">{num(a.min_odds)}</span></div>
                <div>EV <span className="text-ink-100">{signedPct(a.ev)}</span></div>
                <div>P(EV &gt; 0) <span className="text-ink-100">{pct(a.prob_ev_positive)}</span></div>
                <div>Stake sugerido <span className="text-ink-100">{a.kelly_suggested_fraction != null ? pct(a.kelly_suggested_fraction, 2) : '—'}</span> del bank</div>
                <div>Mejor otra casa <span className="text-ink-100">{res.best_other_odds != null ? num(res.best_other_odds) : '—'}</span></div>
              </div>
              {a.reasons.length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-ink-400">{a.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
            </>
          )}
          <p className="mt-2 text-[11px] text-ink-400">{res.disclaimer}</p>
        </div>
      )}
    </Card>
  )
}
