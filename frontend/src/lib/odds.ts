import { useState } from 'react'

export type OddsFormat = 'decimal' | 'american' | 'fractional' | 'percent'

export const ODDS_FORMATS: { value: OddsFormat; label: string }[] = [
  { value: 'decimal', label: 'Decimal (2.50)' },
  { value: 'american', label: 'Americano (+150)' },
  { value: 'fractional', label: 'Fraccionario (3/2)' },
  { value: 'percent', label: 'Porcentaje (40%)' },
]

// Fracción más cercana con denominador ≤ maxDen (fracciones continuas), igual que el backend
function toFraction(x: number, maxDen = 100): [number, number] {
  let [h0, h1, k0, k1] = [0, 1, 1, 0]
  let v = x
  for (let i = 0; i < 64; i++) {
    const a = Math.floor(v)
    const k2 = a * k1 + k0
    if (k2 > maxDen) break
    ;[h0, h1] = [h1, a * h1 + h0]
    ;[k0, k1] = [k1, k2]
    if (Math.abs(v - a) < 1e-12) break
    v = 1 / (v - a)
  }
  return [h1, k1]
}

/** Muestra una cuota decimal en el formato elegido. La plataforma siempre guarda decimal. */
export function formatOdds(d: number | null | undefined, fmt: OddsFormat): string {
  if (d === null || d === undefined || !Number.isFinite(d) || d <= 1) return '—'
  switch (fmt) {
    case 'american':
      return d >= 2 ? `+${Math.round((d - 1) * 100)}` : `${Math.round(-100 / (d - 1))}`
    case 'fractional': {
      const [n, k] = toFraction(d - 1)
      return `${n}/${k}`
    }
    case 'percent':
      return `${(100 / d).toFixed(2)}%`
    default:
      return d.toFixed(2)
  }
}

const KEY = 'qs_odds_format'

/** Formato de visualización elegido por el usuario (se recuerda en este navegador). */
export function useOddsFormat(): [OddsFormat, (f: OddsFormat) => void] {
  const [fmt, setFmt] = useState<OddsFormat>(() => {
    try {
      const v = localStorage.getItem(KEY)
      return ODDS_FORMATS.some((f) => f.value === v) ? (v as OddsFormat) : 'decimal'
    } catch {
      return 'decimal'
    }
  })
  const set = (f: OddsFormat) => {
    setFmt(f)
    try {
      localStorage.setItem(KEY, f)
    } catch {
      /* sin almacenamiento: solo esta sesión */
    }
  }
  return [fmt, set]
}
