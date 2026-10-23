import type { Category } from '../types/api'

export const TZ = 'America/Mexico_City'

export const pct = (x: number | null | undefined, digits = 1): string =>
  x === null || x === undefined || Number.isNaN(x) ? '—' : `${(x * 100).toFixed(digits)}%`

export const signedPct = (x: number | null | undefined, digits = 1): string =>
  x === null || x === undefined || Number.isNaN(x) ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`

export const num = (x: number | null | undefined, digits = 2): string =>
  x === null || x === undefined || Number.isNaN(x) ? '—' : x.toFixed(digits)

export function localTime(iso: string | null | undefined, withDate = true): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return new Intl.DateTimeFormat('es-MX', {
    timeZone: TZ,
    ...(withDate ? { day: '2-digit', month: 'short' } : {}),
    hour: '2-digit',
    minute: '2-digit',
  }).format(d)
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return 'nunca'
  const mins = (Date.now() - new Date(iso).getTime()) / 60000
  if (mins < 1) return 'hace segundos'
  if (mins < 60) return `hace ${Math.round(mins)} min`
  if (mins < 60 * 24) return `hace ${Math.round(mins / 60)} h`
  return `hace ${Math.round(mins / 1440)} d`
}

export const CATEGORY_META: Record<Category, { label: string; cls: string; dot: string; order: number }> = {
  candidato_valor: { label: 'Candidato de valor', cls: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30', dot: 'bg-emerald-400', order: 0 },
  en_observacion: { label: 'En observación', cls: 'bg-amber-500/15 text-amber-300 ring-amber-500/30', dot: 'bg-amber-400', order: 1 },
  ventaja_no_confirmada: { label: 'Ventaja no confirmada', cls: 'bg-sky-500/10 text-sky-300 ring-sky-500/25', dot: 'bg-sky-400', order: 2 },
  sin_valor: { label: 'Sin valor detectable', cls: 'bg-slate-500/10 text-slate-300 ring-slate-500/25', dot: 'bg-slate-400', order: 3 },
  datos_insuficientes: { label: 'Datos insuficientes', cls: 'bg-rose-500/10 text-rose-300 ring-rose-500/25', dot: 'bg-rose-400', order: 4 },
  cuota_desactualizada: { label: 'Cuota desactualizada', cls: 'bg-orange-500/10 text-orange-300 ring-orange-500/25', dot: 'bg-orange-400', order: 5 },
}

export const MODEL_STATUS: Record<string, { label: string; cls: string }> = {
  production: { label: 'Validado', cls: 'text-emerald-300' },
  experimental: { label: 'Experimental', cls: 'text-amber-300' },
  blocked: { label: 'Bloqueado', cls: 'text-rose-300' },
  sin_modelo: { label: 'Sin modelo', cls: 'text-slate-400' },
}

export const MARKET_LABEL: Record<string, string> = {
  '1x2': '1X2',
  totals: 'Goles (O/U)',
  btts: 'Ambos anotan',
  ah: 'Hándicap asiático',
  dnb: 'Empate no válido',
}

export const SELECTION_LABEL: Record<string, string> = {
  home: 'Local',
  draw: 'Empate',
  away: 'Visitante',
  over: 'Más',
  under: 'Menos',
  yes: 'Sí',
  no: 'No',
}
