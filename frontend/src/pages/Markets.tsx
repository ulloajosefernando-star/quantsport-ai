import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import type { Board, EvaluatedSelection, Registry } from '../types/markets'
import { Card, CategoryBadge, Empty, ErrorBox, Loading, ModelBadge, Select, SyntheticTag } from '../components/ui'
import { OddsCalculator } from '../components/OddsCalculator'
import { ODDS_FORMATS, formatOdds, useOddsFormat, type OddsFormat } from '../lib/odds'
import { localTime, pct, signedPct } from '../lib/format'
import { qs } from '../services/api'

const input = 'w-20 rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100'
const METHOD_LABEL: Record<string, string> = { proportional: 'Proporcional', power: 'Potencia', shin: 'Shin' }
const UNC_CLS: Record<string, string> = {
  baja: 'text-emerald-300', media: 'text-amber-300', alta: 'text-rose-300', desconocida: 'text-ink-400',
}

function NumFilter({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-400">{label}
      <input value={value} onChange={(e) => onChange(e.target.value.replace(/[^\d.-]/g, ''))} inputMode="decimal" placeholder={placeholder} className={input} />
    </label>
  )
}

const asNum = (v: string, scale = 1) => (v.trim() === '' || Number.isNaN(Number(v)) ? undefined : Number(v) / scale)

export default function Markets() {
  const reg = useApi<Registry>('/api/markets/registry')
  const [sport, setSport] = useState('football')
  const [competition, setCompetition] = useState('')
  const [eventId, setEventId] = useState('')
  const [market, setMarket] = useState('')
  const [method, setMethod] = useState('proportional')
  const [fmt, setFmt] = useOddsFormat()
  const [f, setF] = useState({ minOdds: '', maxOdds: '', minProb: '', maxProb: '', minEv: '', minEdge: '' })
  const set = (k: keyof typeof f) => (v: string) => setF((x) => ({ ...x, [k]: v }))

  const evs = useApi<{ events: { id: number; label: string; competition_key: string; kickoff_utc: string }[]; competitions: { key: string; name: string }[] }>(
    `/api/markets/events${qs({ sport, days: 30 })}`)
  useEffect(() => { setCompetition(''); setEventId(''); setMarket('') }, [sport])
  useEffect(() => { setEventId('') }, [competition])

  const path = `/api/markets/board${qs({
    sport, competition: competition || undefined, event_id: eventId || undefined, market: market || undefined,
    margin_method: method, days: 30, min_odds: asNum(f.minOdds), max_odds: asNum(f.maxOdds),
    min_prob: asNum(f.minProb, 100), max_prob: asNum(f.maxProb, 100), min_ev: asNum(f.minEv, 100), min_edge: asNum(f.minEdge, 100),
  })}`
  const board = useApi<Board>(path)
  const sportDef = reg.data?.sports.find((s) => s.key === sport)
  const events = (evs.data?.events ?? []).filter((e) => !competition || e.competition_key === competition)
  const noModel = sportDef && sportDef.markets.every((m) => !m.has_model)
  const modelCount = useMemo(() => sportDef?.markets.filter((m) => m.has_model).length ?? 0, [sportDef])

  if (reg.error) return <ErrorBox error={reg.error} onRetry={reg.reload} />
  if (!reg.data) return <Loading />
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Cuotas y mercados</h1>
        <p className="mt-1 text-sm text-ink-400">
          Compara la cuota disponible con la probabilidad sin margen y con el modelo, mercado por mercado. Solo lectura y cálculo:
          la plataforma no se conecta a casas ni apuesta.
        </p>
      </div>

      <Card title="Filtros">
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Deporte" value={sport} onChange={setSport} options={reg.data.sports.map((s) => ({ value: s.key, label: s.label }))} />
          <Select label="Competición" value={competition} onChange={setCompetition}
            options={[{ value: '', label: 'Todas' }, ...(evs.data?.competitions ?? []).map((c) => ({ value: c.key, label: c.name }))]} />
          <Select label="Evento" value={eventId} onChange={setEventId}
            options={[{ value: '', label: 'Todos los próximos' }, ...events.map((e) => ({ value: String(e.id), label: `${e.label} · ${localTime(e.kickoff_utc)}` }))]} />
          <Select label="Mercado" value={market} onChange={setMarket}
            options={[{ value: '', label: 'Todos' }, ...(sportDef?.markets ?? []).map((m) => ({ value: m.key, label: `${m.label}${m.has_model ? '' : ' (sin modelo)'}` }))]} />
          <Select label="Formato de cuota" value={fmt} onChange={(v) => setFmt(v as OddsFormat)} options={ODDS_FORMATS} />
          <Select label="Quitar margen" value={method} onChange={setMethod}
            options={reg.data.margin_methods.map((m) => ({ value: m, label: METHOD_LABEL[m] ?? m }))} />
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <NumFilter label="Cuota mín." value={f.minOdds} onChange={set('minOdds')} placeholder="1.50" />
          <NumFilter label="Cuota máx." value={f.maxOdds} onChange={set('maxOdds')} placeholder="5.00" />
          <NumFilter label="Implícita mín. %" value={f.minProb} onChange={set('minProb')} placeholder="20" />
          <NumFilter label="Implícita máx. %" value={f.maxProb} onChange={set('maxProb')} placeholder="80" />
          <NumFilter label="EV mín. %" value={f.minEv} onChange={set('minEv')} placeholder="0" />
          <NumFilter label="Edge mín. %" value={f.minEdge} onChange={set('minEdge')} placeholder="0" />
        </div>
        <p className="mt-3 text-xs text-ink-400">
          {noModel
            ? `${sportDef?.label}: todavía no hay modelo. Se muestran probabilidad implícita y margen, sin EV del modelo.`
            : `${sportDef?.label}: el modelo cubre ${modelCount} de ${sportDef?.markets.length} mercados registrados. En el resto se explica por qué no.`}
        </p>
      </Card>

      <OddsCalculator />

      <Card title="Mercados con cuota" subtitle={board.data ? `${board.data.markets.length} mercados · ${board.data.events_considered} eventos revisados` : undefined}>
        {board.error && <ErrorBox error={board.error} onRetry={board.reload} />}
        {board.loading && !board.data && <Loading label="Evaluando mercados…" />}
        {board.data && board.data.markets.length === 0 && (
          <Empty>No hay cuotas que cumplan los filtros. Sincroniza un proveedor en Fuentes o captura una cuota en el detalle del partido.</Empty>
        )}
        {board.data && board.data.markets.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-700 text-left text-xs text-ink-400">
                  <th className="py-1.5 pr-3">Selección</th><th className="py-1.5 pr-3 text-right">Cuota</th>
                  <th className="py-1.5 pr-3 text-right">Implícita</th><th className="py-1.5 pr-3 text-right">Sin margen</th>
                  <th className="py-1.5 pr-3 text-right">Modelo</th><th className="py-1.5 pr-3 text-right">Cuota justa</th>
                  <th className="py-1.5 pr-3 text-right">Edge</th><th className="py-1.5 pr-3 text-right">EV</th>
                  <th className="py-1.5 pr-3">Incertidumbre</th><th className="py-1.5">Evaluación</th>
                </tr>
              </thead>
              <tbody>
                {board.data.markets.map((m) => (
                  <Fragment key={`${m.market.stable_id}-${m.bookmaker}`}>
                    <tr className="border-b border-ink-800 bg-ink-850/60">
                      <td colSpan={10} className="py-2 text-xs">
                        <Link to={`/eventos/${m.event.id}`} className="font-medium text-ink-100 hover:underline">{m.event.home} vs {m.event.away}</Link>
                        <span className="text-ink-400"> · {m.event.competition} · {localTime(m.event.kickoff_utc)} · </span>
                        <span className="text-ink-200">{m.market.label}{m.market.line !== null ? ` ${m.market.line}` : ''}{m.market.participant ? ` · ${m.market.participant}` : ''}{m.market.period !== 'ft' ? ` · ${m.market.period}` : ''}</span>
                        <span className="text-ink-400"> · {m.bookmaker} · </span>
                        <span className={m.margin.available ? 'text-ink-200' : 'text-ink-400'} title={m.margin.note}>
                          {m.margin.available ? `margen ${pct(m.margin.overround, 2)}` : 'margen no calculable'}
                        </span>
                        {m.event.is_synthetic && <span className="ml-2"><SyntheticTag /></span>}
                        {m.model_note && <div className="mt-0.5 text-[11px] text-amber-200/80">{m.model_note}</div>}
                      </td>
                    </tr>
                    {m.selections.map((r) => <SelectionRow key={r.quote.stable_id} r={r} fmt={fmt} />)}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {board.data?.warnings.map((w) => <p key={w} className="mt-2 text-xs text-amber-200/80">{w}</p>)}
        <p className="mt-3 text-[11px] text-ink-400">
          Edge = probabilidad del modelo − probabilidad sin margen (mismo mercado, línea y periodo; no aplica con devolución).
          EV = Σ probabilidad × retorno de cada resultado posible. {board.data?.disclaimer}
        </p>
      </Card>
    </div>
  )
}

function SelectionRow({ r, fmt }: { r: EvaluatedSelection; fmt: OddsFormat }) {
  const tip = [...r.reasons, ...r.risks].join(' ')
  return (
    <tr className="border-b border-ink-800 align-top" title={tip}>
      <td className="py-1.5 pr-3">{r.selection.label}
        {r.quote.original_value && r.quote.original_format !== 'decimal' && <div className="text-[11px] text-ink-400">capturada como {r.quote.original_value}</div>}
      </td>
      <td className="num py-1.5 pr-3 text-right">{formatOdds(r.quote.odds_decimal, fmt)}
        {r.data_quality.stale && <div className="text-[11px] text-orange-300">desactualizada</div>}
      </td>
      <td className="num py-1.5 pr-3 text-right">{pct(r.implied_probability)}</td>
      <td className="num py-1.5 pr-3 text-right">{pct(r.p_no_vig)}</td>
      <td className="num py-1.5 pr-3 text-right">{pct(r.p_model)}
        {r.p_model_ci90 && <div className="text-[11px] text-ink-400">{pct(r.p_model_ci90[0], 0)}–{pct(r.p_model_ci90[1], 0)}</div>}
        {r.p_push !== null && <div className="text-[11px] text-ink-400">devolución {pct(r.p_push)}</div>}
      </td>
      <td className="num py-1.5 pr-3 text-right">{formatOdds(r.fair_odds, fmt)}
        {r.fair_odds !== null && r.quote.odds_decimal > r.fair_odds && <div className="text-[11px] text-emerald-300">sobre la justa</div>}
      </td>
      <td className="num py-1.5 pr-3 text-right">{signedPct(r.edge)}</td>
      <td className="num py-1.5 pr-3 text-right">{signedPct(r.ev)}
        {r.ev_ci90 && <div className="text-[11px] text-ink-400">{signedPct(r.ev_ci90[0], 0)} a {signedPct(r.ev_ci90[1], 0)}</div>}
      </td>
      <td className={`py-1.5 pr-3 text-xs ${UNC_CLS[r.uncertainty]}`}>{r.uncertainty}
        {r.prob_ev_positive !== null && <div className="text-[11px] text-ink-400">P(EV&gt;0) {pct(r.prob_ev_positive, 0)}</div>}
      </td>
      <td className="py-1.5"><CategoryBadge category={r.category} />
        {r.model_status !== 'sin_modelo' && <div className="mt-1"><ModelBadge status={r.model_status} /></div>}
      </td>
    </tr>
  )
}
