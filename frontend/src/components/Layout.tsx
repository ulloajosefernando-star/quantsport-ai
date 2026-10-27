import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useApi } from '../hooks/useApi'

const NAV = [
  { to: '/', label: 'Inicio', icon: 'M3 11l9-7 9 7v9a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1z' },
  { to: '/cartelera', label: 'Cartelera', icon: 'M4 5h16M4 12h16M4 19h10' },
  { to: '/oportunidades', label: 'Oportunidades', icon: 'M4 18l5-6 4 3 7-9' },
  { to: '/resultados', label: 'Resultados', icon: 'M9 5h10M9 12h10M9 19h10M4 5l1 1 2-2M4 12l1 1 2-2M4 19l1 1 2-2' },
  { to: '/rendimiento', label: 'Rendimiento', icon: 'M5 20V10M12 20V4M19 20v-7' },
  { to: '/fuentes', label: 'Fuentes', icon: 'M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7' },
  { to: '/mercados', label: 'Cuotas y mercados', icon: 'M4 6h16M4 12h10M4 18h6m10-4l-3 3-2-2' },
  { to: '/herramientas', label: 'Calculadora', icon: 'M7 3h10a1 1 0 011 1v16a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1zm2 4h6M9 12h2m2 0h2m-6 4h2m2 0h2' },
  { to: '/configuracion', label: 'Configuración', icon: 'M12 15a3 3 0 100-6 3 3 0 000 6zm7.4-3a7.4 7.4 0 00-.1-1.2l2-1.6-2-3.4-2.4 1a7.6 7.6 0 00-2-1.2L14.5 3h-5l-.4 2.6a7.6 7.6 0 00-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 000 2.4l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 002 1.2l.4 2.6h5l.4-2.6a7.6 7.6 0 002-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z' },
]

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  )
}

export default function Layout() {
  const { data } = useApi<{ status: string; data_mode: string }>('/api/health')
  const [open, setOpen] = useState(false)
  return (
    <div className="flex min-h-full">
      <aside className={`fixed inset-y-0 left-0 z-30 w-60 shrink-0 border-r border-ink-700/70 bg-ink-900 transition-transform lg:static lg:translate-x-0 ${open ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex items-center gap-2 px-5 py-5">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <div>
            <div className="text-sm font-semibold tracking-tight">QuantSport AI</div>
            <div className="text-[11px] text-ink-400">Análisis cuantitativo</div>
          </div>
        </div>
        <nav className="flex flex-col gap-0.5 px-3">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'} onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition ${isActive ? 'bg-ink-800 text-ink-100' : 'text-ink-300 hover:bg-ink-850 hover:text-ink-100'}`
              }>
              <Icon d={n.icon} />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="absolute bottom-0 w-full border-t border-ink-700/60 px-5 py-4 text-[11px] text-ink-400">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${data?.status === 'ok' ? 'bg-emerald-400' : 'bg-rose-400'}`} />
            API {data?.status === 'ok' ? 'conectada' : 'sin conexión'}
          </div>
          <div className="mt-1">Modo: {data?.data_mode === 'demo' ? 'DEMO (sintético)' : data?.data_mode ?? '—'}</div>
          <div className="mt-1">Zona horaria: CDMX</div>
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-20 bg-black/50 lg:hidden" onClick={() => setOpen(false)} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-3 border-b border-ink-700/60 px-4 py-3 lg:hidden">
          <button onClick={() => setOpen(true)} className="rounded-md p-1.5 text-ink-300 ring-1 ring-ink-700" aria-label="Abrir menú">
            <Icon d="M4 6h16M4 12h16M4 18h16" />
          </button>
          <span className="text-sm font-semibold">QuantSport AI</span>
        </div>
        {data?.data_mode === 'demo' && (
          <div className="border-b border-fuchsia-500/30 bg-fuchsia-500/10 px-4 py-2 text-xs text-fuchsia-200">
            <strong>Modo DEMO:</strong> los equipos, resultados y cuotas son SINTÉTICOS y sirven solo para probar la plataforma.
            No representan partidos ni mercados reales. Para datos reales usa <code>DATA_MODE=real</code> e importa CSV o configura proveedores.
          </div>
        )}
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
