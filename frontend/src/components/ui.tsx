import type { ReactNode } from 'react'
import type { Category } from '../types/api'
import { CATEGORY_META, MODEL_STATUS } from '../lib/format'

export function Card({ title, subtitle, actions, children, className = '' }: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-xl border border-ink-700/70 bg-ink-900/80 ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-ink-700/60 px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Stat({ label, value, hint, tone = 'default' }: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'good' | 'warn' | 'bad'
}) {
  const color = { default: 'text-ink-100', good: 'text-emerald-300', warn: 'text-amber-300', bad: 'text-rose-300' }[tone]
  return (
    <div className="rounded-xl border border-ink-700/70 bg-ink-900/80 px-4 py-3">
      <div className="text-xs text-ink-400">{label}</div>
      <div className={`num mt-1 text-2xl font-semibold ${color}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-400">{hint}</div>}
    </div>
  )
}

export function CategoryBadge({ category }: { category: Category | null | undefined }) {
  if (!category) return <span className="text-xs text-ink-400">—</span>
  const m = CATEGORY_META[category]
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs ring-1 ${m.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
      {m.label}
    </span>
  )
}

export function ModelBadge({ status }: { status: string }) {
  const m = MODEL_STATUS[status] ?? { label: status, cls: 'text-ink-300' }
  return <span className={`text-xs font-medium ${m.cls}`}>{m.label}</span>
}

export function SyntheticTag() {
  return (
    <span className="rounded bg-fuchsia-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-fuchsia-300 ring-1 ring-fuchsia-500/30">
      Sintético
    </span>
  )
}

export function Loading({ label = 'Cargando…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 p-6 text-sm text-ink-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-emerald-400" />
      {label}
    </div>
  )
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200">
      <div className="font-medium">No se pudo cargar la información</div>
      <div className="mt-1 text-rose-300/80">{error}</div>
      {onRetry && (
        <button onClick={onRetry} className="mt-3 rounded-md bg-rose-500/20 px-3 py-1 text-xs hover:bg-rose-500/30">
          Reintentar
        </button>
      )}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-ink-700 p-6 text-center text-sm text-ink-400">{children}</div>
}

export function Button({ children, onClick, variant = 'secondary', disabled, type = 'button' }: {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'secondary' | 'ghost'
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  const cls = {
    primary: 'bg-emerald-500 text-ink-950 hover:bg-emerald-400',
    secondary: 'bg-ink-800 text-ink-100 ring-1 ring-ink-600 hover:bg-ink-700',
    ghost: 'text-ink-300 hover:bg-ink-800',
  }[variant]
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${cls}`}
    >
      {children}
    </button>
  )
}

export function Select({ value, onChange, options, label }: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  label: string
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-400">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100 focus:border-emerald-500 focus:outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function NumberInput({ value, onChange, label, step = 0.01, placeholder }: {
  value: string
  onChange: (v: string) => void
  label: string
  step?: number
  placeholder?: string
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-400">
      {label}
      <input
        type="number"
        step={step}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-28 rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100 focus:border-emerald-500 focus:outline-none"
      />
    </label>
  )
}

export function Disclaimer() {
  return (
    <p className="text-xs text-ink-400">
      Análisis probabilístico, no garantía de resultado. QuantSport AI no ejecuta apuestas ni realiza transacciones. Si no
      hay evidencia suficiente de ventaja, la recomendación es <span className="font-semibold text-ink-200">NO BET</span>.
    </p>
  )
}
