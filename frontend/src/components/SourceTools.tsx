import { useEffect, useState } from 'react'
import { api, qs } from '../services/api'
import { Button, Card, Select } from './ui'

export interface CatalogItem { key: string; name: string; country: string; providers: string[] }

const PROVIDER_LABEL: Record<string, string> = {
  api_football: 'API-Football', the_odds_api: 'The Odds API', football_data_org: 'football-data.org',
}
const KIND_LABEL: Record<string, string> = { results: 'Resultados', fixtures: 'Calendario', odds: 'Cuotas' }
// Qué puede pedir cada proveedor
const KINDS: Record<string, string[]> = {
  api_football: ['results', 'fixtures', 'odds'], the_odds_api: ['odds', 'fixtures'], football_data_org: ['results', 'fixtures'],
}

export function SyncCard({ catalog, onDone }: { catalog: CatalogItem[]; onDone: () => void }) {
  const [provider, setProvider] = useState('api_football')
  const comps = catalog.filter((c) => c.providers.includes(provider))
  const [comp, setComp] = useState('mex_liga_mx')
  const [kind, setKind] = useState('results')
  const [season, setSeason] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!comps.some((c) => c.key === comp) && comps[0]) setComp(comps[0].key)
    if (!KINDS[provider].includes(kind)) setKind(KINDS[provider][0])
  }, [provider]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.post<{ events_created: number; events_updated: number; odds_inserted: number; odds_unmatched?: number; warnings: string[] }>(
        `/api/providers/${provider}/sync${qs({ competition: comp, kind, season: season || undefined })}`)
      setMsg(`Listo: ${r.events_created} partidos nuevos, ${r.events_updated} actualizados, ${r.odds_inserted} cuotas. ${r.warnings.slice(0, 2).join(' ')}`)
      onDone()
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const showSeason = provider === 'api_football' || kind === 'results'
  return (
    <Card title="Sincronizar proveedor" subtitle="Cada solicitud descuenta de la cuota del plan; las respuestas se guardan en caché.">
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Proveedor" value={provider} onChange={setProvider}
          options={Object.keys(KINDS).map((k) => ({ value: k, label: PROVIDER_LABEL[k] }))} />
        <Select label="Competición" value={comp} onChange={setComp} options={comps.map((c) => ({ value: c.key, label: c.name }))} />
        <Select label="Datos" value={kind} onChange={setKind} options={KINDS[provider].map((k) => ({ value: k, label: KIND_LABEL[k] }))} />
        {showSeason && (
          <label className="flex flex-col gap-1 text-xs text-ink-400">Temporada (opcional)
            <input value={season} onChange={(e) => setSeason(e.target.value.replace(/\D/g, '').slice(0, 4))} placeholder="2024"
              className="w-24 rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100" />
          </label>
        )}
        <Button variant="primary" onClick={run} disabled={busy}>{busy ? 'Sincronizando…' : 'Sincronizar'}</Button>
      </div>
      {provider === 'api_football' && (
        <p className="mt-3 text-xs text-amber-200/90">
          El plan gratuito de API-Football solo da temporadas pasadas (p. ej. 2022–2024). Para la temporada actual de Liga MX usa
          el CSV de football-data.co.uk y The Odds API, o un plan de pago.
        </p>
      )}
      {msg && <p className="mt-3 text-xs text-emerald-300">{msg}</p>}
    </Card>
  )
}

interface ReviewTeam {
  team_id: number; name: string; finished: number; scheduled: number; aliases: string[]; needs_review: boolean
  suggestions: { team_id: number; name: string; similarity: number; finished: number }[]
}
interface Review { competition: string; min_matches: number; pending_review: number; teams: ReviewTeam[] }

export function TeamReviewCard({ competitions, onDone }: { competitions: { key: string; competition: string }[]; onDone: () => void }) {
  const [comp, setComp] = useState(competitions.find((c) => c.key === 'mex_liga_mx')?.key ?? competitions[0]?.key ?? '')
  const [data, setData] = useState<Review | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [all, setAll] = useState(false)

  const load = async (key: string) => {
    if (!key) return
    try {
      setData(await api.get<Review>(`/api/teams/review${qs({ competition: key })}`))
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  useEffect(() => { load(comp) }, [comp])

  const merge = async (from: ReviewTeam, into: { team_id: number; name: string }) => {
    if (!window.confirm(`¿"${from.name}" y "${into.name}" son el mismo equipo? Se moverán sus partidos y cuotas a "${into.name}".`)) return
    try {
      const r = await api.post<{ events_moved: number; events_merged: number }>('/api/teams/merge', { from_team_id: from.team_id, into_team_id: into.team_id })
      setMsg(`Fusionado: ${r.events_moved} partidos movidos, ${r.events_merged} duplicados unidos.`)
      load(comp)
      onDone()
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  if (!competitions.length) return null
  const rows = data ? data.teams.filter((t) => all || t.needs_review) : []
  return (
    <Card title="Revisión de equipos"
      subtitle="Cada fuente escribe los nombres distinto. Un equipo sin historial no se puede analizar: fusiónalo con el equipo correcto.">
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Select label="Competición" value={comp} onChange={setComp} options={competitions.map((c) => ({ value: c.key, label: c.competition }))} />
        <label className="flex items-center gap-2 text-xs text-ink-300">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Mostrar todos
        </label>
        {data && <span className="text-xs text-ink-400">{data.pending_review} por revisar (menos de {data.min_matches} resultados y con partidos próximos)</span>}
      </div>
      {msg && <p className="mb-3 text-xs text-emerald-300">{msg}</p>}
      {rows.length === 0 ? <p className="text-sm text-ink-400">Nada pendiente.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-ink-700"><th className="py-1.5">Equipo</th><th className="py-1.5 text-right">Resultados</th><th className="py-1.5 text-right">Próximos</th><th className="py-1.5">Posible coincidencia</th></tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.team_id} className="border-b border-ink-800 align-top">
                  <td className="py-1.5">{t.name}<div className="text-[11px] text-ink-400">{t.aliases.join(' · ')}</div></td>
                  <td className="num py-1.5 text-right">{t.finished}</td>
                  <td className="num py-1.5 text-right">{t.scheduled}</td>
                  <td className="py-1.5">
                    {t.suggestions.length === 0 && t.needs_review && <span className="text-xs text-ink-400">Sin sugerencias; puede ser un equipo nuevo (ascendido).</span>}
                    <div className="flex flex-wrap gap-2">
                      {t.suggestions.map((s) => (
                        <Button key={s.team_id} onClick={() => merge(t, s)}>Es {s.name} ({Math.round(s.similarity * 100)}%)</Button>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
