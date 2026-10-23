import { useRef, useState } from 'react'
import { useApi } from '../hooks/useApi'
import { api } from '../services/api'
import type { ProviderRow } from '../types/api'
import { Button, Card, ErrorBox, Loading, SyntheticTag } from '../components/ui'
import { ago, localTime, pct } from '../lib/format'

interface Resp {
  providers: ProviderRow[]
  usage: { provider: string; period: string; credits_used: number; limit: number | null; calls: number; errors: number; last_call: string | null; pct_used: number | null }[]
  coverage: { competition: string; key: string; is_synthetic: boolean; finished: number; scheduled: number; odds_snapshots: number; last_sync: string | null }[]
  recent_calls: { time: string; provider: string; endpoint: string; status: string; http_status: number | null; credits: number; error: string }[]
  csv_files: string[]
}

const KIND: Record<string, string> = { real: 'API real', dataset: 'Dataset', demo: 'Demo sintético', pending: 'Pendiente' }

export default function Sources() {
  const { data, error, loading, reload } = useApi<Resp>('/api/sources')
  const fileRef = useRef<HTMLInputElement>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const upload = async () => {
    const f = fileRef.current?.files?.[0]
    if (!f) return
    setBusy(true)
    try {
      const r = await api.upload<{ events_created: number; events_updated: number; odds_inserted: number; odds_duplicates: number; warnings: string[] }>('/api/imports/csv', f)
      setMsg(`Importado: ${r.events_created} eventos nuevos, ${r.events_updated} actualizados, ${r.odds_inserted} cuotas (${r.odds_duplicates} duplicadas). ${r.warnings.slice(0, 3).join(' ')}`)
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const refreshDemo = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ odds_inserted: number }>('/api/demo/refresh-odds')
      setMsg(`Cuotas demo actualizadas: ${r.odds_inserted} nuevos snapshots.`)
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data) return null
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Fuentes de datos</h1>

      <Card title="Proveedores" subtitle="Ningún proveedor se activa ni se contrata automáticamente">
        <div className="grid gap-3 md:grid-cols-2">
          {data.providers.map((p) => (
            <div key={p.key} className="rounded-lg border border-ink-700 p-3 text-sm">
              <div className="flex items-start justify-between gap-2">
                <div className="font-medium">{p.name}</div>
                <span className={`text-xs ${p.kind === 'pending' ? 'text-ink-400' : p.configured ? 'text-emerald-300' : 'text-amber-300'}`}>
                  {KIND[p.kind]} · {p.kind === 'pending' ? 'no implementado' : p.configured ? 'configurado' : 'falta clave en .env'}
                </span>
              </div>
              <div className="mt-1 text-xs text-ink-400">Deportes: {p.sports.join(', ')} · Capacidades: {p.capabilities.join(', ') || '—'}</div>
              {p.cost_note && <div className="mt-1 text-xs">Coste: {p.cost_note}</div>}
              {p.coverage_note && <div className="mt-0.5 text-xs text-ink-300">{p.coverage_note}</div>}
              {p.quota_note && <div className="mt-0.5 text-xs text-ink-300">{p.quota_note}</div>}
              {p.license_note && <div className="mt-0.5 text-xs text-ink-400">{p.license_note}</div>}
              {p.docs_url && <a href={p.docs_url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-xs text-emerald-300 hover:underline">Documentación</a>}
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Importar CSV" subtitle="football-data.co.uk (formato principal y 'new': Liga MX, MLS) o formato genérico">
          <div className="flex flex-wrap items-center gap-3">
            <input ref={fileRef} type="file" accept=".csv" className="text-xs text-ink-300 file:mr-3 file:rounded-md file:border-0 file:bg-ink-800 file:px-3 file:py-1.5 file:text-ink-100" />
            <Button variant="primary" onClick={upload} disabled={busy}>Importar</Button>
            <Button onClick={refreshDemo} disabled={busy}>Actualizar cuotas demo</Button>
          </div>
          {msg && <p className="mt-3 text-xs text-emerald-300">{msg}</p>}
          <p className="mt-3 text-xs text-ink-400">
            Formato genérico: <code>date, competition, home, away, home_goals, away_goals[, odds_home, odds_draw, odds_away, bookmaker, odds_captured_at]</code>.
            Las cuotas pre-cierre de football-data.co.uk no traen hora de captura: se marcan como timestamp aproximado.
          </p>
          {data.csv_files.length > 0 && <p className="mt-2 text-xs text-ink-400">Archivos en data/imports: {data.csv_files.join(', ')}</p>}
        </Card>
        <Card title="Consumo de APIs (periodo actual)">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-ink-700"><th className="py-1.5">Proveedor</th><th className="py-1.5 text-right">Créditos</th><th className="py-1.5 text-right">Llamadas</th><th className="py-1.5 text-right">Errores</th><th className="py-1.5">Última</th></tr></thead>
            <tbody>
              {data.usage.map((u) => (
                <tr key={u.provider} className="border-b border-ink-800">
                  <td className="py-1.5">{u.provider}<div className="text-[11px] text-ink-400">por {u.period === 'month' ? 'mes' : 'día'}</div></td>
                  <td className="num py-1.5 text-right">{u.credits_used}{u.limit ? ` / ${u.limit}` : ''}<div className="text-[11px] text-ink-400">{u.pct_used !== null ? pct(u.pct_used, 0) : ''}</div></td>
                  <td className="num py-1.5 text-right">{u.calls}</td>
                  <td className={`num py-1.5 text-right ${u.errors ? 'text-rose-300' : ''}`}>{u.errors}</td>
                  <td className="py-1.5 text-xs text-ink-400">{ago(u.last_call)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <Card title="Cobertura y última sincronización">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-ink-700"><th className="py-1.5">Competición</th><th className="py-1.5 text-right">Finalizados</th><th className="py-1.5 text-right">Programados</th><th className="py-1.5 text-right">Snapshots de cuota</th><th className="py-1.5">Última sincronización</th></tr></thead>
          <tbody>
            {data.coverage.map((c) => (
              <tr key={c.key} className="border-b border-ink-800">
                <td className="py-1.5"><span className="mr-2">{c.competition}</span>{c.is_synthetic && <SyntheticTag />}</td>
                <td className="num py-1.5 text-right">{c.finished}</td>
                <td className="num py-1.5 text-right">{c.scheduled}</td>
                <td className="num py-1.5 text-right">{c.odds_snapshots}</td>
                <td className="py-1.5 text-xs text-ink-400">{localTime(c.last_sync)} ({ago(c.last_sync)})</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Llamadas recientes y errores">
        {data.recent_calls.length === 0 ? <p className="text-sm text-ink-400">Sin llamadas a APIs externas todavía.</p> : (
          <table className="w-full text-xs">
            <tbody>
              {data.recent_calls.map((r, i) => (
                <tr key={i} className="border-b border-ink-800">
                  <td className="py-1 pr-3">{localTime(r.time)}</td><td className="py-1 pr-3">{r.provider}</td>
                  <td className="py-1 pr-3 font-mono">{r.endpoint}</td>
                  <td className={`py-1 pr-3 ${r.status === 'error' ? 'text-rose-300' : 'text-ink-300'}`}>{r.status} {r.http_status ?? ''}</td>
                  <td className="py-1 text-ink-400">{r.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
