import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import { qs } from '../services/api'
import type { Category, RankedItem } from '../types/api'
import { Card, CategoryBadge, Disclaimer, Empty, ErrorBox, Loading, ModelBadge, NumberInput, Select, SyntheticTag } from '../components/ui'
import { CATEGORY_META, MARKET_LABEL, localTime, num, pct, signedPct } from '../lib/format'

interface Resp { items: RankedItem[]; thresholds: Record<string, number>; disclaimer: string }
type SortKey = 'rank' | 'ev' | 'edge' | 'uncertainty' | 'quality'

export default function Opportunities() {
  const [market, setMarket] = useState('')
  const [category, setCategory] = useState('')
  const [minEv, setMinEv] = useState('')
  const [minEdge, setMinEdge] = useState('')
  const [minOdds, setMinOdds] = useState('')
  const [maxOdds, setMaxOdds] = useState('')
  const [includeNoValue, setIncludeNoValue] = useState(false)
  const [sort, setSort] = useState<SortKey>('rank')
  const [open, setOpen] = useState<number | null>(null)
  const path = `/api/opportunities${qs({
    market, category: category ? [category] : undefined, min_ev: minEv ? Number(minEv) / 100 : undefined,
    min_edge: minEdge ? Number(minEdge) / 100 : undefined, min_odds: minOdds, max_odds: maxOdds, include_no_value: includeNoValue,
  })}`
  const { data, error, loading, reload } = useApi<Resp>(path)

  const items = useMemo(() => {
    const arr = [...(data?.items ?? [])]
    const width = (r: RankedItem) => (r.ev_ci90 ? r.ev_ci90[1] - r.ev_ci90[0] : 9)
    const by: Record<SortKey, (a: RankedItem, b: RankedItem) => number> = {
      rank: (a, b) => a.rank - b.rank,
      ev: (a, b) => (b.ev ?? -9) - (a.ev ?? -9),
      edge: (a, b) => (b.edge ?? -9) - (a.edge ?? -9),
      uncertainty: (a, b) => width(a) - width(b),
      quality: (a, b) => b.quality_score - a.quality_score,
    }
    return arr.sort(by[sort])
  }, [data, sort])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-xl font-semibold">Oportunidades</h1>
        <Disclaimer />
      </div>
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Mercado" value={market} onChange={setMarket}
            options={[{ value: '', label: 'Todos' }, ...Object.entries(MARKET_LABEL).map(([v, l]) => ({ value: v, label: l }))]} />
          <Select label="Categoría" value={category} onChange={setCategory}
            options={[{ value: '', label: 'Todas' }, ...(Object.keys(CATEGORY_META) as Category[]).map((k) => ({ value: k, label: CATEGORY_META[k].label }))]} />
          <NumberInput label="EV mínimo (%)" value={minEv} onChange={setMinEv} step={0.5} />
          <NumberInput label="Edge mínimo (%)" value={minEdge} onChange={setMinEdge} step={0.5} />
          <NumberInput label="Cuota mín." value={minOdds} onChange={setMinOdds} />
          <NumberInput label="Cuota máx." value={maxOdds} onChange={setMaxOdds} />
          <Select label="Ordenar por" value={sort} onChange={(v) => setSort(v as SortKey)} options={[
            { value: 'rank', label: 'Ranking del sistema' }, { value: 'ev', label: 'EV' }, { value: 'edge', label: 'Edge' },
            { value: 'uncertainty', label: 'Menor incertidumbre' }, { value: 'quality', label: 'Calidad' }]} />
          <label className="flex items-center gap-2 pb-1.5 text-xs text-ink-300">
            <input type="checkbox" checked={includeNoValue} onChange={(e) => setIncludeNoValue(e.target.checked)} />
            Incluir "sin valor"
          </label>
        </div>
      </Card>

      {loading && !data ? <Loading label="Calculando ranking…" /> : error ? <ErrorBox error={error} onRetry={reload} /> : items.length === 0 ? (
        <Empty>Ninguna selección cumple los filtros. Sin evidencia suficiente: NO BET.</Empty>
      ) : (
        <Card subtitle="Haz clic en una fila para ver explicación, riesgos, definiciones y la cuota mínima aceptable.">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-700">
                  <th className="py-2 pr-2">#</th>
                  <th className="py-2 pr-3">Evento / mercado</th>
                  <th className="py-2 pr-3 text-right">Cuota</th>
                  <th className="py-2 pr-3 text-right">P modelo</th>
                  <th className="py-2 pr-3 text-right">P mercado</th>
                  <th className="py-2 pr-3 text-right">Cuota justa</th>
                  <th className="py-2 pr-3 text-right">Edge</th>
                  <th className="py-2 pr-3 text-right">EV (IC90)</th>
                  <th className="py-2 pr-3 text-right">Cuota mín.</th>
                  <th className="py-2">Estado</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <Fragment key={`${r.event_id}-${r.market}-${r.selection}-${r.line}`}>
                    <tr onClick={() => setOpen(open === r.rank ? null : r.rank)} className="cursor-pointer border-b border-ink-800 align-top hover:bg-ink-850">
                      <td className="num py-2.5 pr-2 text-ink-400">{r.rank}</td>
                      <td className="py-2.5 pr-3">
                        <div className="font-medium">{r.label}</div>
                        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-400">
                          <Link to={`/eventos/${r.event_id}`} onClick={(e) => e.stopPropagation()} className="hover:text-emerald-300">{r.event}</Link>
                          · {r.competition} · {localTime(r.kickoff_utc)} {r.is_synthetic && <SyntheticTag />}
                        </div>
                      </td>
                      <td className="num py-2.5 pr-3 text-right">{num(r.odds)}<div className="text-[11px] text-ink-400">{r.bookmaker}</div></td>
                      <td className="num py-2.5 pr-3 text-right">{pct(r.p_model)}</td>
                      <td className="num py-2.5 pr-3 text-right">{pct(r.p_market_fair)}</td>
                      <td className="num py-2.5 pr-3 text-right">{num(r.fair_odds)}</td>
                      <td className="num py-2.5 pr-3 text-right">{signedPct(r.edge)}</td>
                      <td className="num py-2.5 pr-3 text-right">
                        {signedPct(r.ev)}
                        <div className="text-[11px] text-ink-400">{r.ev_ci90 ? `${signedPct(r.ev_ci90[0], 0)} a ${signedPct(r.ev_ci90[1], 0)}` : '—'}</div>
                      </td>
                      <td className="num py-2.5 pr-3 text-right">{num(r.min_odds)}</td>
                      <td className="py-2.5"><CategoryBadge category={r.category} /><div className="mt-1"><ModelBadge status={r.model_status} /></div></td>
                    </tr>
                    {open === r.rank && (
                      <tr className="border-b border-ink-800 bg-ink-850/60">
                        <td colSpan={10} className="px-4 py-4">
                          <div className="grid gap-4 text-sm md:grid-cols-3">
                            <div>
                              <h3 className="mb-1 text-xs font-semibold uppercase text-ink-400">Explicación</h3>
                              <ul className="list-disc space-y-1 pl-4">{r.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
                              <p className="mt-2 text-xs text-ink-400">
                                P(EV&gt;0) = {r.prob_ev_positive === null ? '—' : pct(r.prob_ev_positive, 0)} · IC90 de p: {r.p_model_ci90 ? `${pct(r.p_model_ci90[0])} – ${pct(r.p_model_ci90[1])}` : '—'}
                                {r.p_push !== null && <> · P(push) = {pct(r.p_push)}</>}
                              </p>
                            </div>
                            <div>
                              <h3 className="mb-1 text-xs font-semibold uppercase text-ink-400">Riesgos</h3>
                              <ul className="list-disc space-y-1 pl-4 text-amber-200/90">{r.risks.length ? r.risks.map((x, i) => <li key={i}>{x}</li>) : <li>—</li>}</ul>
                              <p className="mt-2 text-xs text-ink-400">
                                Kelly completo (referencia): {pct(r.kelly_full)} · Kelly fraccionado con límite: {pct(r.kelly_suggested_fraction)} de la banca. No es una orden de apuesta.
                              </p>
                            </div>
                            <div>
                              <h3 className="mb-1 text-xs font-semibold uppercase text-ink-400">Definiciones y trazabilidad</h3>
                              <ul className="space-y-1 font-mono text-[11px] text-ink-300">{Object.values(r.definitions).map((d, i) => <li key={i}>{d}</li>)}</ul>
                              <p className="mt-2 text-xs text-ink-400">
                                Cuota capturada {localTime(r.odds_captured_at)} ({num(r.odds_age_minutes, 0)} min) · fuente {r.odds_source} · {r.n_offers} ofertas ·
                                modelo v{r.model_version} ({r.model_fingerprint}) · muestra {r.sample_size} partidos
                              </p>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
