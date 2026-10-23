import { Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import type { Category, Overview } from '../types/api'
import { Card, CategoryBadge, Disclaimer, Empty, ErrorBox, Loading, ModelBadge, Stat, SyntheticTag } from '../components/ui'
import { ProbBar } from '../charts/charts'
import { CATEGORY_META, ago, localTime, num, signedPct } from '../lib/format'

const SPORT_STATUS: Record<string, { label: string; cls: string }> = {
  active: { label: 'Activo', cls: 'text-emerald-300' },
  partial: { label: 'Parcial', cls: 'text-amber-300' },
  disabled: { label: 'Deshabilitado', cls: 'text-ink-400' },
}

export default function Home() {
  const { data, error, loading, reload } = useApi<Overview>('/api/overview')
  if (loading && !data) return <Loading label="Analizando la cartelera…" />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data) return null
  const c = data.category_counts
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Resumen de la cartelera</h1>
          <p className="text-sm text-ink-400">
            Cuotas actualizadas {ago(data.last_odds_update)} · Eventos sincronizados {ago(data.last_event_update)}
          </p>
        </div>
        <Disclaimer />
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Eventos próximos (10 días)" value={data.n_upcoming} hint={`${data.events_today.length} hoy`} />
        <Stat label="Candidatos de valor" value={c.candidato_valor} tone={c.candidato_valor ? 'good' : 'default'} />
        <Stat label="En observación" value={c.en_observacion} tone={c.en_observacion ? 'warn' : 'default'} />
        <Stat label="Ventaja no confirmada" value={c.ventaja_no_confirmada} />
        <Stat label="Cuotas desactualizadas" value={c.cuota_desactualizada} tone={c.cuota_desactualizada ? 'bad' : 'default'} />
        <Stat label="Modelos disponibles" value={data.models_available} hint="Por competición" />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Ranking de oportunidades"
          subtitle="Ordenado por categoría y calidad (frescura, calibración, EV, incertidumbre, robustez); no solo por EV."
          actions={<Link to="/oportunidades" className="text-xs text-emerald-300 hover:underline">Ver todas →</Link>}>
          {data.ranking.length === 0 ? (
            <Empty>Ninguna selección supera los controles. Recomendación del sistema: NO BET.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-ink-700">
                    <th className="py-2 pr-3">Evento</th>
                    <th className="py-2 pr-3">Mercado</th>
                    <th className="py-2 pr-3 text-right">Cuota</th>
                    <th className="py-2 pr-3 text-right">EV</th>
                    <th className="py-2 pr-3 text-right">P(EV&gt;0)</th>
                    <th className="py-2">Decisión</th>
                  </tr>
                </thead>
                <tbody>
                  {data.ranking.map((r, i) => (
                    <tr key={i} className="border-b border-ink-800 hover:bg-ink-850">
                      <td className="py-2 pr-3">
                        <Link to={`/eventos/${r.event_id}`} className="hover:text-emerald-300">{r.event}</Link>
                        <div className="text-xs text-ink-400">{r.competition} · {localTime(r.kickoff_utc)}</div>
                      </td>
                      <td className="py-2 pr-3">{r.label}</td>
                      <td className="num py-2 pr-3 text-right">{num(r.odds)}<div className="text-[11px] text-ink-400">{r.bookmaker}</div></td>
                      <td className="num py-2 pr-3 text-right">{signedPct(r.ev)}</td>
                      <td className="num py-2 pr-3 text-right">{r.prob_ev_positive === null ? '—' : `${Math.round(r.prob_ev_positive * 100)}%`}</td>
                      <td className="py-2"><CategoryBadge category={r.category} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="space-y-6">
          <Card title="Estado por deporte">
            <ul className="space-y-2 text-sm">
              {data.sports.map((s) => (
                <li key={s.key} className="flex items-start justify-between gap-3">
                  <div>
                    <div>{s.name}</div>
                    <div className="text-xs text-ink-400">{s.reason}</div>
                  </div>
                  <span className={`text-xs font-medium ${SPORT_STATUS[s.status]?.cls}`}>{SPORT_STATUS[s.status]?.label}</span>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Fuentes de datos" actions={<Link to="/fuentes" className="text-xs text-emerald-300 hover:underline">Detalle →</Link>}>
            <ul className="space-y-1.5 text-sm">
              {data.providers.map((p) => (
                <li key={p.key} className="flex items-center justify-between gap-2">
                  <span className="truncate">{p.name}</span>
                  <span className={`text-xs ${p.kind === 'pending' ? 'text-ink-400' : p.configured ? 'text-emerald-300' : 'text-amber-300'}`}>
                    {p.kind === 'pending' ? 'Pendiente' : p.kind === 'demo' ? 'Demo' : p.configured ? 'Configurado' : 'Sin clave'}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Próximos eventos">
          {data.upcoming.length === 0 ? <Empty>No hay eventos próximos. Importa calendarios o configura un proveedor.</Empty> : (
            <ul className="divide-y divide-ink-800">
              {data.upcoming.map((e) => (
                <li key={e.event_id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <Link to={`/eventos/${e.event_id}`} className="text-sm hover:text-emerald-300">{e.home} vs {e.away}</Link>
                    <div className="flex items-center gap-2 text-xs text-ink-400">
                      {e.competition} · {localTime(e.kickoff_utc)} {e.is_synthetic && <SyntheticTag />}
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    <ProbBar p={e.p1x2} compact />
                    <CategoryBadge category={e.summary_category} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Alertas de calidad y frescura">
          {data.alerts.length === 0 ? <p className="text-sm text-ink-400">Sin alertas.</p> : (
            <ul className="space-y-2 text-sm">
              {data.alerts.map((a, i) => (
                <li key={i} className="rounded-md bg-amber-500/10 px-3 py-2 text-amber-200">
                  <div className="text-xs text-amber-300/80">{a.event}</div>{a.issue}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-4 border-t border-ink-700 pt-3 text-xs text-ink-400">
            Leyenda:{' '}
            {(Object.keys(CATEGORY_META) as Category[]).map((k) => (
              <span key={k} className="mr-2 inline-block"><CategoryBadge category={k} /></span>
            ))}
          </div>
          <div className="mt-2 text-xs text-ink-400">Estados de modelo: <ModelBadge status="production" />, <ModelBadge status="experimental" />, <ModelBadge status="blocked" /></div>
        </Card>
      </div>
    </div>
  )
}
