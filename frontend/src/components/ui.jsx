import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { kickoff, odds as fmtOdds } from '../lib/format'

/* ------------------------------------------------------------------ Logos */
const PALETTE = ['#1F2A44', '#2A2140', '#1E3A35', '#3A2A1E', '#1E2F3F', '#33202A']

export function TeamLogo({ team, size = 28 }) {
  const [broken, setBroken] = useState(false)
  const name = team?.name || ''
  const initials = (team?.short || name.slice(0, 3)).toUpperCase().slice(0, 3)
  const color = PALETTE[[...name].reduce((a, c) => a + c.charCodeAt(0), 0) % PALETTE.length]
  if (team?.logo && !broken) {
    return (
      <img
        src={team.logo}
        alt={name}
        width={size}
        height={size}
        loading="lazy"
        onError={() => setBroken(true)}
        className="object-contain shrink-0"
        style={{ width: size, height: size }}
      />
    )
  }
  return (
    <div
      className="shrink-0 rounded-full grid place-items-center text-[9px] font-semibold tracking-wide text-fg-dim"
      style={{ width: size, height: size, background: color }}
      title={name}
    >
      {initials}
    </div>
  )
}

export function LeagueMark({ league, logo, size = 16 }) {
  const [broken, setBroken] = useState(false)
  return (
    <span className="inline-flex items-center gap-1.5 min-w-0">
      {logo && !broken ? (
        <img src={logo} alt="" width={size} height={size} onError={() => setBroken(true)} className="object-contain opacity-90" style={{ width: size, height: size }} />
      ) : (
        <span className="w-1.5 h-1.5 rounded-full bg-fg-mute" />
      )}
      <span className="truncate">{league}</span>
    </span>
  )
}

/* ------------------------------------------------------------------ Estado */
export function StatusChip({ status, clock, start, compact = false }) {
  if (status === 'live') {
    return (
      <span className="inline-flex items-center gap-1.5 text-live text-[11px] font-semibold tracking-wide whitespace-nowrap">
        <span className="w-1.5 h-1.5 rounded-full bg-live live-dot shrink-0" />
        {!compact && 'EN VIVO'}
        {clock ? <span className="num font-medium">{compact ? clock : `· ${clock}`}</span> : null}
      </span>
    )
  }
  if (status === 'final') return <span className="text-[11px] text-fg-mute tracking-wide whitespace-nowrap">FINAL</span>
  return <span className="text-[11px] text-info tracking-wide num whitespace-nowrap">{kickoff(start)}</span>
}

/* ------------------------------------------------------------------ Cuota con destello */
export function LiveOdds({ value, trend, className = '' }) {
  const prev = useRef(value)
  const [flash, setFlash] = useState('')
  useEffect(() => {
    if (prev.current != null && value != null && value !== prev.current) {
      setFlash(value > prev.current ? 'flash-up' : 'flash-down')
      const t = setTimeout(() => setFlash(''), 1200)
      prev.current = value
      return () => clearTimeout(t)
    }
    prev.current = value
  }, [value])
  return (
    <span className={`num inline-flex items-center gap-1 px-1 -mx-1 ${flash} ${className}`}>
      {fmtOdds(value)}
      {trend === 'up' && <span className="text-ev text-[10px]">▲</span>}
      {trend === 'down' && <span className="text-live/80 text-[10px]">▼</span>}
    </span>
  )
}

export function Flash({ value, children, className = '' }) {
  const prev = useRef(value)
  const [on, setOn] = useState(false)
  useEffect(() => {
    if (prev.current !== value) {
      prev.current = value
      setOn(true)
      const t = setTimeout(() => setOn(false), 1200)
      return () => clearTimeout(t)
    }
  }, [value])
  return <span className={`${on ? 'flash' : ''} ${className}`}>{children}</span>
}

/* ------------------------------------------------------------------ Píldoras */
export function Pill({ active, onClick, children, count, tone = 'default' }) {
  const activeCls = {
    default: 'bg-fg text-ink border-fg',
    ev: 'bg-ev text-ink border-ev',
    live: 'bg-live text-white border-live',
  }[tone]
  return (
    <button
      onClick={onClick}
      className={`pill ${active ? activeCls : 'bg-transparent text-fg-dim border-ink-line hover:text-fg hover:border-ink-soft'}`}
    >
      {children}
      {count != null && (
        <span className={`num text-[11px] ${active ? 'opacity-70' : 'text-fg-mute'}`}>{count}</span>
      )}
    </button>
  )
}

/* ------------------------------------------------------------------ Toasts */
const ToastCtx = createContext(() => {})

export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const push = useCallback((text, tone = 'ok') => {
    const id = Math.random().toString(36).slice(2)
    setItems((xs) => [...xs, { id, text, tone }])
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 3200)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex flex-col items-center gap-2 pointer-events-none">
        {items.map((t) => (
          <div
            key={t.id}
            className={`fade-up pointer-events-auto rounded-xl border px-4 py-2.5 text-sm shadow-card bg-ink-raised ${
              t.tone === 'error' ? 'border-live/40 text-fg' : 'border-ink-line text-fg'
            }`}
          >
            <span className={`mr-2 inline-block w-1.5 h-1.5 rounded-full align-middle ${t.tone === 'error' ? 'bg-live' : 'bg-ev'}`} />
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export const useToast = () => useContext(ToastCtx)

/* ------------------------------------------------------------------ Vacío */
export function Empty({ title, text }) {
  return (
    <div className="card px-6 py-14 text-center">
      <div className="mx-auto mb-4 w-10 h-10 rounded-full border border-ink-line grid place-items-center">
        <span className="w-1.5 h-1.5 rounded-full bg-fg-mute live-dot" />
      </div>
      <div className="text-fg text-[15px] font-medium">{title}</div>
      {text && <div className="mt-1.5 text-sm text-fg-mute max-w-md mx-auto">{text}</div>}
    </div>
  )
}
