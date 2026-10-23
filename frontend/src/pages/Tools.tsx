import { useState } from 'react'
import { api, qs } from '../services/api'
import { Button, Card, ErrorBox, Select } from '../components/ui'
import { num, pct, signedPct } from '../lib/format'

interface Result {
  decimal: number[]
  american: number[]
  fractional: string[]
  implied_raw: number[]
  overround?: number
  fair_by_method?: Record<string, number[]>
  method_disagreement?: number
  note?: string
  definitions: Record<string, string>
  first_selection?: { ev: number; fair_odds: number; min_odds_for_target: number; edge_vs_proportional: number | null; kelly_full: number; stake: { capped_fraction: number; stake_amount: number; warning: string } }
}

export default function Tools() {
  const [odds, setOdds] = useState('2.10, 3.40, 3.60')
  const [fmt, setFmt] = useState('decimal')
  const [p, setP] = useState('52')
  const [target, setTarget] = useState('2')
  const [bankroll, setBankroll] = useState('1000')
  const [kf, setKf] = useState('0.25')
  const [res, setRes] = useState<Result | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const calc = async () => {
    setErr(null)
    try {
      const list = odds.split(',').map((x) => x.trim()).filter(Boolean)
      setRes(await api.get<Result>(`/api/tools/odds${qs({ odds: list, fmt, p_model: p ? Number(p) / 100 : undefined, target_ev: Number(target) / 100, bankroll, kelly_fraction: kf })}`))
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  const input = (label: string, v: string, set: (s: string) => void, w = 'w-28') => (
    <label className="flex flex-col gap-1 text-xs text-ink-400">{label}
      <input value={v} onChange={(e) => set(e.target.value)} className={`${w} rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100`} />
    </label>
  )
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Calculadora de cuotas, margen, EV y Kelly</h1>
      <Card subtitle="Introduce TODAS las cuotas del mercado (p. ej. 1, X, 2). La probabilidad del modelo se aplica a la primera selección.">
        <div className="flex flex-wrap items-end gap-3">
          {input('Cuotas (separadas por coma)', odds, setOdds, 'w-56')}
          <Select label="Formato" value={fmt} onChange={setFmt} options={[{ value: 'decimal', label: 'Decimal' }, { value: 'american', label: 'Americana' }, { value: 'fractional', label: 'Fraccionaria' }]} />
          {input('P modelo 1ª selección (%)', p, setP)}
          {input('EV objetivo (%)', target, setTarget)}
          {input('Banca', bankroll, setBankroll)}
          {input('Fracción Kelly', kf, setKf)}
          <Button variant="primary" onClick={calc}>Calcular</Button>
        </div>
      </Card>
      {err && <ErrorBox error={err} />}
      {res && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Conversión y margen">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-ink-700"><th className="py-1">#</th><th className="py-1 text-right">Decimal</th><th className="py-1 text-right">Americana</th><th className="py-1 text-right">Fracc.</th><th className="py-1 text-right">Implícita</th><th className="py-1 text-right">Prop.</th><th className="py-1 text-right">Potencia</th><th className="py-1 text-right">Shin</th></tr></thead>
              <tbody>
                {res.decimal.map((d, i) => (
                  <tr key={i} className="border-b border-ink-800">
                    <td className="py-1">{i + 1}</td><td className="num py-1 text-right">{num(d, 3)}</td>
                    <td className="num py-1 text-right">{res.american[i] > 0 ? '+' : ''}{num(res.american[i], 0)}</td>
                    <td className="num py-1 text-right">{res.fractional[i]}</td><td className="num py-1 text-right">{pct(res.implied_raw[i])}</td>
                    <td className="num py-1 text-right">{pct(res.fair_by_method?.proportional[i])}</td>
                    <td className="num py-1 text-right">{pct(res.fair_by_method?.power[i])}</td>
                    <td className="num py-1 text-right">{pct(res.fair_by_method?.shin[i])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-sm">Overround: <span className="num font-medium">{pct(res.overround, 2)}</span> · Discrepancia máx. entre métodos: {pct(res.method_disagreement, 2)}</p>
            <p className="mt-1 text-xs text-ink-400">{res.note}</p>
          </Card>
          {res.first_selection && (
            <Card title="Primera selección">
              <dl className="grid grid-cols-2 gap-y-2 text-sm">
                <dt className="text-ink-400">EV por unidad</dt><dd className="num">{signedPct(res.first_selection.ev)}</dd>
                <dt className="text-ink-400">Cuota justa</dt><dd className="num">{num(res.first_selection.fair_odds)}</dd>
                <dt className="text-ink-400">Cuota mínima para EV objetivo</dt><dd className="num">{num(res.first_selection.min_odds_for_target)}</dd>
                <dt className="text-ink-400">Edge vs mercado (proporcional)</dt><dd className="num">{signedPct(res.first_selection.edge_vs_proportional)}</dd>
                <dt className="text-ink-400">Kelly completo (referencia)</dt><dd className="num">{pct(res.first_selection.kelly_full)}</dd>
                <dt className="text-ink-400">Kelly fraccionado con límite</dt><dd className="num">{pct(res.first_selection.stake.capped_fraction)} = {num(res.first_selection.stake.stake_amount)}</dd>
              </dl>
              <p className="mt-3 text-xs text-amber-200/90">{res.first_selection.stake.warning}</p>
              <ul className="mt-3 space-y-1 font-mono text-[11px] text-ink-400">{Object.values(res.definitions).map((d, i) => <li key={i}>{d}</li>)}</ul>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
