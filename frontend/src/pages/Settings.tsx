import { useEffect, useState } from 'react'
import { useApi } from '../hooks/useApi'
import { api, getAdminToken, setAdminToken } from '../services/api'
import { Button, Card, ErrorBox, Loading, SyntheticTag } from '../components/ui'

interface Resp {
  data_mode: string
  timezone: string
  thresholds: Record<string, number>
  sports: { key: string; name: string; status: string; enabled: boolean; reason: string }[]
  competitions: { key: string; name: string; enabled: boolean; is_synthetic: boolean }[]
  telegram: { configured: boolean; allowed_chats: number; max_messages_per_day: number }
  providers: { key: string; name: string; configured: boolean; kind: string }[]
  budget: { budget_mxn: number; usd_mxn: number; eur_mxn: number; items: { category: string; option: string; monthly_mxn: number; status: string; notes: string }[]; scenarios: Record<string, number>; within_budget: Record<string, boolean> }
  admin_token_required: boolean
  schedules: string
}

const TH_LABELS: Record<string, string> = {
  min_ev: 'EV mínimo (decimal)', min_edge: 'Edge mínimo', min_prob_ev_positive: 'P(EV>0) mínima', target_ev: 'EV objetivo (cuota mínima)',
  min_odds: 'Cuota mínima', max_odds: 'Cuota máxima', max_odds_age_minutes: 'Antigüedad máx. cuota (min)', max_data_age_days: 'Antigüedad máx. datos (días)',
  min_eval_sample: 'Muestra mínima de validación', max_ece: 'ECE máximo', max_logloss_gap_vs_market: 'Tolerancia Δ log loss vs mercado',
  min_team_matches: 'Partidos mínimos por equipo', kelly_fraction: 'Fracción de Kelly', max_stake_fraction: 'Exposición máx. por selección',
  max_event_exposure: 'Exposición máx. por evento',
}
const SPORT_STATUS: Record<string, string> = { partial: 'Parcial', disabled: 'Deshabilitado', complete: 'Completo' }
const SCENARIOS: Record<string, string> = { local: 'Modo local', vps_minimo: 'VPS mínimo', vps_con_api_football_pro: 'VPS + API-Football Pro' }

export default function Settings() {
  const { data, error, loading, reload } = useApi<Resp>('/api/settings')
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)
  const [token, setToken] = useState(getAdminToken())
  const [preview, setPreview] = useState<string[] | null>(null)

  useEffect(() => {
    if (data) setDraft(Object.fromEntries(Object.entries(data.thresholds).map(([k, v]) => [k, String(v)])))
  }, [data])

  const save = async () => {
    const changes = Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, Number(v)]))
    try {
      await api.put('/api/settings/thresholds', { changes })
      setMsg('Umbrales guardados. Los análisis se recalculan con los nuevos valores.')
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  const toggle = async (key: string, enabled: boolean) => {
    try {
      await api.put(`/api/settings/competitions/${key}`, { enabled })
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  const tgPreview = async () => setPreview((await api.get<{ messages: string[] }>('/api/telegram/preview')).messages)
  const tgSend = async () => {
    try {
      const r = await api.post<{ status: string; detail?: string }>('/api/telegram/send-summary')
      setMsg(`Telegram: ${r.status}${r.detail ? ` — ${r.detail}` : ''}`)
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  const resetDemo = async () => {
    try {
      await api.post('/api/demo/reset')
      setMsg('Liga demo regenerada.')
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    }
  }

  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data) return null
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Configuración</h1>
      {msg && <div className="rounded-md bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">{msg}</div>}

      <Card title="Umbrales del motor de valor" subtitle="Parámetros editables, no constantes universales. Valores decimales (0.03 = 3%)."
        actions={<Button variant="primary" onClick={save}>Guardar</Button>}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.keys(draft).map((k) => (
            <label key={k} className="flex flex-col gap-1 text-xs text-ink-400">
              {TH_LABELS[k] ?? k}
              <input value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                className="num rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100 focus:border-emerald-500 focus:outline-none" />
            </label>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Deportes">
          <ul className="space-y-2 text-sm">
            {data.sports.map((s) => (
              <li key={s.key} className="flex items-start justify-between gap-3">
                <div>{s.name}<div className="text-xs text-ink-400">{s.reason}</div></div>
                <span className="text-xs text-ink-300">{SPORT_STATUS[s.status] ?? s.status}</span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Competiciones">
          <ul className="space-y-2 text-sm">
            {data.competitions.map((c) => (
              <li key={c.key} className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2">{c.name} {c.is_synthetic && <SyntheticTag />}</span>
                <label className="flex items-center gap-2 text-xs text-ink-300">
                  <input type="checkbox" checked={c.enabled} onChange={(e) => toggle(c.key, e.target.checked)} /> Activa
                </label>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title={`Presupuesto mensual (límite ${data.budget.budget_mxn} MXN)`}
        subtitle={`Tipo de cambio manual: 1 USD = ${data.budget.usd_mxn} MXN · 1 EUR ≈ ${data.budget.eur_mxn} MXN. Verifica precios antes de contratar.`}>
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          {Object.entries(data.budget.scenarios).map(([k, v]) => (
            <div key={k} className="rounded-lg border border-ink-700 p-3">
              <div className="text-xs text-ink-400">{SCENARIOS[k] ?? k}</div>
              <div className="num text-xl font-semibold">{v.toFixed(0)} MXN/mes</div>
              <div className={`text-xs ${data.budget.within_budget[k] ? 'text-emerald-300' : 'text-rose-300'}`}>{data.budget.within_budget[k] ? 'Dentro del presupuesto' : 'Excede el presupuesto'}</div>
            </div>
          ))}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-ink-700"><th className="py-1.5">Categoría</th><th className="py-1.5">Opción</th><th className="py-1.5 text-right">MXN/mes</th><th className="py-1.5">Estado</th><th className="py-1.5">Notas</th></tr></thead>
            <tbody>
              {data.budget.items.map((i, n) => (
                <tr key={n} className="border-b border-ink-800">
                  <td className="py-1.5 text-ink-400">{i.category}</td><td className="py-1.5">{i.option}</td>
                  <td className="num py-1.5 text-right">{i.monthly_mxn.toFixed(0)}</td><td className="py-1.5 text-xs">{i.status}</td>
                  <td className="py-1.5 text-xs text-ink-400">{i.notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Telegram" subtitle={data.telegram.configured ? `Configurado · ${data.telegram.allowed_chats} chat(s) autorizados · máx. ${data.telegram.max_messages_per_day} mensajes/día` : 'Sin configurar: añade TELEGRAM_BOT_TOKEN y TELEGRAM_ALLOWED_CHAT_IDS en .env'}
          actions={<><Button onClick={tgPreview}>Vista previa</Button><Button onClick={tgSend} disabled={!data.telegram.configured}>Enviar resumen</Button></>}>
          {preview ? preview.map((m, i) => <pre key={i} className="mb-2 whitespace-pre-wrap rounded-md bg-ink-850 p-3 text-xs text-ink-200">{m}</pre>) :
            <p className="text-sm text-ink-400">Los comandos interactivos (/today, /ranking, /status…) llegan en la Fase 6.</p>}
        </Card>
        <Card title="Acceso y modo">
          <div className="space-y-3 text-sm">
            <div>Modo de datos: <strong>{data.data_mode}</strong> · Zona horaria: {data.timezone}</div>
            <div>Horarios de análisis: <span className="text-ink-400">{data.schedules}</span></div>
            <label className="flex flex-col gap-1 text-xs text-ink-400">
              Token de administración {data.admin_token_required ? '(requerido)' : '(no requerido en modo local)'}
              <div className="flex gap-2">
                <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
                  className="flex-1 rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100" />
                <Button onClick={() => { setAdminToken(token); setMsg('Token guardado en este navegador.') }}>Guardar</Button>
              </div>
            </label>
            {data.data_mode === 'demo' && <Button onClick={resetDemo}>Regenerar liga demo</Button>}
            <ul className="text-xs text-ink-400">{data.providers.map((p) => <li key={p.key}>{p.name}: {p.kind === 'pending' ? 'pendiente' : p.configured ? 'configurado' : 'sin configurar'}</li>)}</ul>
          </div>
        </Card>
      </div>
    </div>
  )
}
