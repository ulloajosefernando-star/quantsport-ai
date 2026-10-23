import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import { qs } from '../services/api'
import type { EventRow } from '../types/api'
import { Card, CategoryBadge, Empty, ErrorBox, Loading, ModelBadge, Select, SyntheticTag } from '../components/ui'
import { ProbBar } from '../charts/charts'
import { MARKET_LABEL, ago, localTime, num } from '../lib/format'

interface Resp { events: EventRow[]; competitions: { key: string; name: string; is_synthetic: boolean }[] }

export default function Fixtures() {
  const [sport, setSport] = useState('')
  const [competition, setCompetition] = useState('')
  const [days, setDays] = useState('10')
  const [market, setMarket] = useState('')
  const [bookmaker, setBookmaker] = useState('')
  const [date, setDate] = useState('')
  const { data, error, loading, reload } = useApi<Resp>(`/api/events${qs({ sport, competition, days })}`)

  const events = useMemo(() => {
    let ev = data?.events ?? []
    if (market) ev = ev.filter((e) => e.markets.includes(market))
    if (bookmaker) ev = ev.filter((e) => e.bookmakers.includes(bookmaker))
    if (date) ev = ev.filter((e) => e.kickoff_local.startsWith(date))
    return ev
  }, [data, market, bookmaker, date])
  const books = Array.from(new Set((data?.events ?? []).flatMap((e) => e.bookmakers))).sort()
  const dates = Array.from(new Set((data?.events ?? []).map((e) => e.kickoff_local.slice(0, 10)))).sort()

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Cartelera</h1>
      <Card>
        <div className="flex flex-wrap gap-3">
          <Select label="Deporte" value={sport} onChange={setSport} options={[
            { value: '', label: 'Todos' }, { value: 'football', label: 'Fútbol' }, { value: 'nba', label: 'NBA (deshabilitado)' },
            { value: 'nfl', label: 'NFL (deshabilitado)' }, { value: 'mlb', label: 'MLB (deshabilitado)' }, { value: 'tennis', label: 'Tenis (deshabilitado)' },
          ]} />
          <Select label="Competición" value={competition} onChange={setCompetition}
            options={[{ value: '', label: 'Todas' }, ...(data?.competitions ?? []).map((c) => ({ value: c.key, label: c.name }))]} />
          <Select label="Fecha" value={date} onChange={setDate} options={[{ value: '', label: 'Todas' }, ...dates.map((d) => ({ value: d, label: d }))]} />
          <Select label="Horizonte" value={days} onChange={setDays} options={[
            { value: '3', label: '3 días' }, { value: '7', label: '7 días' }, { value: '10', label: '10 días' }, { value: '30', label: '30 días' }]} />
          <Select label="Mercado con cuotas" value={market} onChange={setMarket}
            options={[{ value: '', label: 'Cualquiera' }, ...Object.entries(MARKET_LABEL).map(([v, l]) => ({ value: v, label: l }))]} />
          <Select label="Operador" value={bookmaker} onChange={setBookmaker} options={[{ value: '', label: 'Todos' }, ...books.map((b) => ({ value: b, label: b }))]} />
        </div>
      </Card>
      {loading && !data ? <Loading /> : error ? <ErrorBox error={error} onRetry={reload} /> : events.length === 0 ? (
        <Empty>No hay eventos con estos filtros.</Empty>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-700">
                  <th className="py-2 pr-3">Hora (CDMX)</th>
                  <th className="py-2 pr-3">Partido</th>
                  <th className="py-2 pr-3">Probabilidades del modelo</th>
                  <th className="py-2 pr-3 text-right">Mejor 1 / X / 2</th>
                  <th className="py-2 pr-3">Alineaciones</th>
                  <th className="py-2 pr-3">Cuotas</th>
                  <th className="py-2">Resumen</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.event_id} className="border-b border-ink-800 align-top hover:bg-ink-850">
                    <td className="num py-2.5 pr-3 whitespace-nowrap">{localTime(e.kickoff_utc)}</td>
                    <td className="py-2.5 pr-3">
                      <Link to={`/eventos/${e.event_id}`} className="font-medium hover:text-emerald-300">{e.home} vs {e.away}</Link>
                      <div className="flex items-center gap-2 text-xs text-ink-400">{e.competition} {e.is_synthetic && <SyntheticTag />}</div>
                      <div className="text-xs text-ink-400">Modelo: <ModelBadge status={e.model_status} /></div>
                      {e.data_issues.map((d, i) => <div key={i} className="text-xs text-amber-300">{d}</div>)}
                    </td>
                    <td className="py-2.5 pr-3"><ProbBar p={e.p1x2} compact /></td>
                    <td className="num py-2.5 pr-3 text-right whitespace-nowrap">
                      {['home', 'draw', 'away'].map((s) => num(e.best_1x2[s]?.odds)).join(' / ')}
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-ink-400">{e.lineups_status}</td>
                    <td className="py-2.5 pr-3 text-xs text-ink-400">{e.bookmakers.length} operadores<br />{ago(e.last_odds_update)}</td>
                    <td className="py-2.5"><CategoryBadge category={e.summary_category} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
