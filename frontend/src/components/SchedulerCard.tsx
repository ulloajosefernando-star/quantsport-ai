import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import { api } from '../services/api'
import { localTime } from '../lib/format'
import { Button, Card, ErrorBox, Loading } from './ui'

interface SchedulerStatus {
  enabled: boolean; running: boolean; reason: string | null; timezone: string; has_odds_key: boolean
  config: { competitions: string[]; odds_times: string[]; daily_time: string; prekickoff_minutes: number; credit_reserve: number; scores: boolean }
  tasks: { key: string; label: string }[]
  next_runs: { id: string; task: string; next_run: string | null }[]
  plan: { odds: number; scores: number; prekickoff: number; nba?: number; total: number; available: number; monthly_limit: number; reserve: number; used_this_month: number; fits: boolean; odds_call_cost: number; note: string }
  recent: { job: string; run_key: string; status: string; attempts: number; started_at: string; finished_at: string | null; detail: string }[]
}

const STATUS_CLS: Record<string, string> = { ok: 'text-emerald-300', error: 'text-rose-300', running: 'text-amber-300' }

export function SchedulerCard() {
  const { data, error, loading, reload } = useApi<SchedulerStatus>('/api/scheduler')
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const run = async (task: string) => {
    setBusy(task)
    setMsg(null)
    try {
      const r = await api.post<{ status: string; detail?: string; reason?: string }>(`/api/scheduler/run/${task}`)
      setMsg(`${task}: ${r.status}${r.reason ? ` (${r.reason})` : ''}`)
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (loading && !data) return <Loading />
  if (!data) return null
  const p = data.plan
  const share = p.available > 0 ? Math.min(1, p.total / p.available) : 1
  const blocked = Boolean(data.reason?.startsWith('Modo demo'))
  return (
    <Card title="Programador automático"
      subtitle="Sincroniza cuotas y resultados, registra pronósticos antes de cada partido y los liquida con CLV. Necesita el backend encendido.">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${data.running ? 'bg-emerald-500/15 text-emerald-300' : 'bg-ink-800 text-ink-300'}`}>
          {data.running ? 'Encendido' : 'Apagado'}
        </span>
        {data.reason && <span className="text-xs text-ink-400">{data.reason}</span>}
        {!data.has_odds_key && !blocked && <span className="text-xs text-amber-200">Sin THE_ODDS_API_KEY: solo hará lo gratuito (CSV, liquidación).</span>}
      </div>

      <div className="mt-3 grid gap-3 text-xs text-ink-300 sm:grid-cols-2">
        <div>
          <div>Ligas: <b className="text-ink-100">{data.config.competitions.join(', ')}</b></div>
          <div>Cuotas a las <b className="text-ink-100">{data.config.odds_times.join(' y ')}</b> · tarea diaria a las <b className="text-ink-100">{data.config.daily_time}</b> ({data.timezone})</div>
          <div>Captura previa: <b className="text-ink-100">{data.config.prekickoff_minutes ? `${data.config.prekickoff_minutes} min antes del inicio` : 'desactivada'}</b></div>
          {data.next_runs.length > 0 && <div className="mt-1">Próximas: {data.next_runs.filter((j) => j.next_run).slice(0, 4).map((j) => `${j.task} ${localTime(j.next_run)}`).join(' · ')}</div>}
        </div>
        <div>
          <div className="flex justify-between"><span>Créditos estimados al mes (The Odds API)</span><b className={p.fits ? 'text-ink-100' : 'text-rose-300'}>{p.total} / {p.available}</b></div>
          <div className="mt-1 h-2 rounded bg-ink-800"><div className={`h-2 rounded ${p.fits ? 'bg-emerald-500' : 'bg-rose-500'}`} style={{ width: `${share * 100}%` }} /></div>
          <div className="mt-1 text-[11px] text-ink-400">
            cuotas {p.odds} · resultados {p.scores} · previas {p.prekickoff}{p.nba ? ` · NBA ${p.nba}` : ''} · reserva {p.reserve} de {p.monthly_limit} · usados este mes {Math.round(p.used_this_month)}
          </div>
          {!p.fits && <div className="mt-1 text-[11px] text-rose-300">No cabe en el plan: usa una sola región, menos horarios o desactiva la captura previa.</div>}
          <div className="mt-1 text-[11px] text-ink-400">{p.note}</div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {data.tasks.map((t) => (
          <span key={t.key} title={t.label}>
            <Button onClick={() => run(t.key)} disabled={blocked || busy !== null}>{busy === t.key ? 'Ejecutando…' : `Ejecutar ${t.key}`}</Button>
          </span>
        ))}
      </div>
      {msg && <p className="mt-2 text-xs text-emerald-300">{msg}</p>}

      {data.recent.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-ink-700 text-left text-ink-400"><th className="py-1">Tarea</th><th className="py-1">Clave</th><th className="py-1">Estado</th><th className="py-1">Terminó</th><th className="py-1">Detalle</th></tr></thead>
            <tbody>
              {data.recent.map((r) => (
                <tr key={`${r.job}-${r.run_key}`} className="border-b border-ink-800 align-top">
                  <td className="py-1 pr-2">{r.job}</td>
                  <td className="py-1 pr-2 font-mono">{r.run_key}</td>
                  <td className={`py-1 pr-2 ${STATUS_CLS[r.status] ?? ''}`}>{r.status}{r.attempts > 1 ? ` (${r.attempts} intentos)` : ''}</td>
                  <td className="py-1 pr-2">{r.finished_at ? localTime(r.finished_at) : '—'}</td>
                  <td className="max-w-md truncate py-1 text-ink-400" title={r.detail}>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
