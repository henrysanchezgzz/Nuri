import { useMemo, useState } from 'react'
import { dateTime, mxn2 } from '../lib/format'

/** Línea minimalista de crecimiento de banca (SVG puro, sin librerías). */
export default function BankrollChart({ history, initial }) {
  const [hover, setHover] = useState(null)
  const W = 720
  const H = 180
  const P = { l: 8, r: 8, t: 16, b: 22 }

  const pts = useMemo(() => {
    const data = history?.length ? history : [{ ts: Date.now() / 1000, balance: initial }]
    const series = data.length === 1 ? [data[0], { ...data[0], ts: data[0].ts + 1 }] : data
    const vals = series.map((d) => d.balance)
    let lo = Math.min(...vals, initial)
    let hi = Math.max(...vals, initial)
    if (hi - lo < 1) { hi += 1; lo -= 1 }
    const pad = (hi - lo) * 0.15
    lo -= pad
    hi += pad
    const x = (i) => P.l + (i / (series.length - 1)) * (W - P.l - P.r)
    const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b)
    return { series, x, y, baseY: y(initial) }
  }, [history, initial])

  const { series, x, y, baseY } = pts
  const last = series[series.length - 1]
  const up = last.balance >= initial
  const color = up ? '#10B981' : '#EF4444'
  const line = series.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d.balance).toFixed(1)}`).join(' ')
  const area = `${line} L${x(series.length - 1).toFixed(1)},${H - P.b} L${x(0).toFixed(1)},${H - P.b} Z`

  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const rx = ((e.clientX - rect.left) / rect.width) * W
    let best = 0
    series.forEach((_, i) => { if (Math.abs(x(i) - rx) < Math.abs(x(best) - rx)) best = i })
    setHover(best)
  }

  const h = hover != null ? series[hover] : null

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[180px]" preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="bkFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.18" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1={P.l} x2={W - P.r} y1={baseY} y2={baseY} stroke="#2A3142" strokeDasharray="3 5" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <path d={area} fill="url(#bkFill)" />
        <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        {h && (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={P.t} y2={H - P.b} stroke="#2A3142" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            <circle cx={x(hover)} cy={y(h.balance)} r="3.5" fill={color} />
          </>
        )}
      </svg>
      <div className="flex justify-between text-[11px] text-fg-mute num -mt-1">
        <span>{dateTime(series[0].ts)}</span>
        <span>Inicial {mxn2(initial)}</span>
        <span>{dateTime(last.ts)}</span>
      </div>
      {h && (
        <div className="absolute top-0 right-0 rounded-lg border border-ink-line bg-ink-raised px-2.5 py-1.5 text-[12px]">
          <span className="num text-fg">{mxn2(h.balance)}</span>
          <span className="text-fg-mute ml-2">{dateTime(h.ts)}</span>
        </div>
      )}
    </div>
  )
}
