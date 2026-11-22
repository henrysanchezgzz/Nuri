const TZ = 'America/Mexico_City'

const mxnFmt = new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN', maximumFractionDigits: 0 })
const mxn2Fmt = new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN', minimumFractionDigits: 2, maximumFractionDigits: 2 })

export const mxn = (v) => (v == null || Number.isNaN(v) ? '—' : mxnFmt.format(Math.round(v)))
export const mxn2 = (v) => (v == null || Number.isNaN(v) ? '—' : mxn2Fmt.format(v))
export const odds = (v) => (v == null ? '—' : Number(v).toFixed(2))
export const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}\u00A0%`)
export const signedPct = (v, d = 1) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(d)}\u00A0%`)

const dayKey = (d) => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d)

export function isToday(ts) {
  if (!ts) return false
  return dayKey(new Date(ts * 1000)) === dayKey(new Date())
}

export function withinDays(ts, days) {
  const now = Date.now() / 1000
  return ts >= now - 3 * 3600 && ts <= now + days * 86400
}

export function kickoff(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const time = new Intl.DateTimeFormat('es-MX', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false }).format(d)
  if (isToday(ts)) return `Hoy ${time}`
  const tomorrow = new Date(Date.now() + 86400000)
  if (dayKey(d) === dayKey(tomorrow)) return `Mañana ${time}`
  const day = new Intl.DateTimeFormat('es-MX', { timeZone: TZ, weekday: 'short', day: 'numeric' }).format(d)
  return `${day.replace('.', '')} · ${time}`
}

export function dateTime(ts) {
  if (!ts) return ''
  return new Intl.DateTimeFormat('es-MX', { timeZone: TZ, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
    .format(new Date(ts * 1000)).replace('.', '')
}

export function ago(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (s < 60) return `hace ${s} s`
  if (s < 3600) return `hace ${Math.round(s / 60)} min`
  return `hace ${Math.round(s / 3600)} h`
}

export const SPORTS = [
  { key: 'all', label: 'Todos' },
  { key: 'football', label: 'Fútbol' },
  { key: 'basketball', label: 'NBA' },
  { key: 'americanfootball', label: 'NFL' },
  { key: 'hockey', label: 'NHL' },
  { key: 'baseball', label: 'MLB' },
]

export const sportLabel = (k) => SPORTS.find((s) => s.key === k)?.label ?? k
