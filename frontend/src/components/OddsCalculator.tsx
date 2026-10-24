import { useState } from 'react'
import type { Conversion } from '../types/markets'
import { api, qs } from '../services/api'
import { num, pct, signedPct } from '../lib/format'
import { Button, Card, Select } from './ui'

const input = 'rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100'
const FORMAT_OPTIONS = [
  { value: 'auto', label: 'Detectar' }, { value: 'decimal', label: 'Decimal' }, { value: 'american', label: 'Americano' },
  { value: 'fractional', label: 'Fraccionario' }, { value: 'percent', label: 'Porcentaje' },
]

/** Convierte una cuota escrita a mano y, si das tu probabilidad, calcula EV, cuota justa y stake. */
export function OddsCalculator() {
  const [value, setValue] = useState('')
  const [fmt, setFmt] = useState('auto')
  const [p, setP] = useState('')
  const [push, setPush] = useState('')
  const [res, setRes] = useState<Conversion | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    setErr(null)
    const pNum = p.trim() ? Number(p) / 100 : undefined
    const pushNum = push.trim() ? Number(push) / 100 : undefined
    if ((pNum !== undefined && !(pNum > 0 && pNum < 1)) || (pushNum !== undefined && !(pushNum >= 0 && pushNum < 1))) {
      setErr('Las probabilidades van en porcentaje, entre 0 y 100.')
      return
    }
    try {
      setRes(await api.get<Conversion>(`/api/tools/convert${qs({ value: value.trim(), fmt, p: pNum, p_push: pushNum })}`))
    } catch (e) {
      setRes(null)
      setErr((e as Error).message)
    }
  }
  const e = res?.evaluation
  return (
    <Card title="Calculadora de cuotas" subtitle="Escribe una cuota en cualquier formato. Si das tu probabilidad, calcula el EV.">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-400">Cuota
          <input value={value} onChange={(x) => setValue(x.target.value)} placeholder="2.50, +150, 3/2 o 40%" className={`w-40 ${input}`} />
        </label>
        <Select label="Formato" value={fmt} onChange={setFmt} options={FORMAT_OPTIONS} />
        <label className="flex flex-col gap-1 text-xs text-ink-400">Tu probabilidad (%)
          <input value={p} onChange={(x) => setP(x.target.value)} inputMode="decimal" placeholder="45" className={`w-24 ${input}`} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-400" title="Para Draw No Bet, hándicaps o totales enteros">Devolución (%)
          <input value={push} onChange={(x) => setPush(x.target.value)} inputMode="decimal" placeholder="0" className={`w-20 ${input}`} />
        </label>
        <Button variant="primary" onClick={run} disabled={!value.trim()}>Calcular</Button>
      </div>
      {err && <p className="mt-3 text-xs text-rose-300">{err}</p>}
      {res && (
        <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div className="rounded-md border border-ink-700 p-3">
            <div className="mb-1 text-xs text-ink-400">Conversión (formato detectado: {res.format_detected})</div>
            <div className="num grid grid-cols-2 gap-1">
              <span>Decimal <b>{res.display.decimal}</b></span><span>Americano <b>{res.display.american}</b></span>
              <span>Fraccionario <b>{res.display.fractional}</b></span><span>Porcentaje <b>{res.display.percent}</b></span>
            </div>
            <div className="mt-1 text-xs text-ink-300">Probabilidad implícita (con margen): {pct(res.implied_probability, 2)}</div>
          </div>
          {e && (
            <div className="rounded-md border border-ink-700 p-3">
              <div className="mb-1 text-xs text-ink-400">Con tu probabilidad {pct(e.p)}{e.p_push ? ` y devolución ${pct(e.p_push)}` : ''}</div>
              <div className="num grid grid-cols-2 gap-1">
                <span>EV <b className={e.ev > 0 ? 'text-emerald-300' : 'text-rose-300'}>{signedPct(e.ev)}</b></span>
                <span>Cuota justa <b>{num(e.fair_odds)}</b></span>
                <span>Cuota mínima (EV {pct(e.target_ev, 0)}) <b>{num(e.min_odds_for_target)}</b></span>
                <span>Diferencia vs implícita <b>{signedPct(e.edge_vs_implied)}</b></span>
                <span className="col-span-2">Stake sugerido (Kelly fraccionado, con tope) <b>{pct(e.stake.capped_fraction, 2)}</b> del bank</span>
              </div>
              <p className="mt-1 text-[11px] text-ink-400">El EV depende de que tu probabilidad sea correcta. No es una garantía.</p>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
