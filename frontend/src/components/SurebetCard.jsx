import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { mxn, mxn2, odds as fmtOdds, pct } from '../lib/format'
import { LeagueMark, StatusChip, TeamLogo, useToast } from './ui'

export default function SurebetCard({ sure, onTaken }) {
  const [total, setTotal] = useState(sure.suggested_total || 100)
  const [busy, setBusy] = useState(false)
  const [taken, setTaken] = useState(false)
  const toast = useToast()

  useEffect(() => {
    if (!busy && !taken) setTotal((t) => t || sure.suggested_total)
  }, [sure.suggested_total, busy, taken])

  const legs = sure.legs.map((l) => ({ ...l, stakeNow: Math.round(total * l.weight) }))
  const payout = Math.min(...legs.map((l) => l.stakeNow * l.odds))
  const spent = legs.reduce((a, l) => a + l.stakeNow, 0)
  const profit = payout - spent

  const takeAll = async () => {
    setBusy(true)
    try {
      for (const l of legs) {
        await api.takeBet({ group_id: sure.id, event_id: sure.event_id, market_key: sure.market_key, selection: l.selection, book: l.book, odds: l.odds, stake: l.stakeNow })
      }
      setTaken(true)
      toast(`Surebet registrada · ganancia asegurada ${mxn2(profit)}`)
      onTaken?.()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const done = sure.taken || taken

  return (
    <article className="card p-5 fade-up min-w-0">
      <header className="flex items-center justify-between text-[12px] text-fg-mute">
        <LeagueMark league={sure.league} logo={sure.league_logo} />
        <StatusChip status={sure.status} clock={sure.clock} start={sure.start_ts} />
      </header>
      <div className="mt-3 flex items-center gap-2.5 text-[13px] text-fg-dim">
        <TeamLogo team={sure.home} size={22} />
        <span className="truncate">{sure.home.name}</span>
        <span className="text-fg-mute text-xs">vs</span>
        <span className="truncate">{sure.away.name}</span>
        <TeamLogo team={sure.away} size={22} />
      </div>

      <div className="mt-4 flex items-end justify-between">
        <div>
          <div className="text-[12px] text-fg-mute">{sure.market_label}</div>
          <div className="mt-0.5 text-[17px] font-semibold text-fg">Surebet · {sure.legs.length} patas</div>
        </div>
        <div className="text-right shrink-0">
          <div className="label whitespace-nowrap">Ganancia segura</div>
          <div className="num mt-1 text-[22px] font-semibold text-ev leading-none whitespace-nowrap">+{pct(sure.profit_pct, 2)}</div>
        </div>
      </div>

      <div className="mt-4 divide-y divide-ink-line rounded-xl border border-ink-line overflow-hidden">
        {legs.map((l) => (
          <div key={l.selection} className="flex items-center justify-between px-4 py-2.5 bg-ink/60 text-[13px]">
            <div className="min-w-0">
              <div className="text-fg truncate">{l.selection_label}</div>
              <div className="text-fg-mute text-[11px]">{l.book} @ <span className="num">{fmtOdds(l.odds)}</span></div>
            </div>
            <div className="num text-fg font-medium">{mxn(l.stakeNow)}</div>
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="label shrink-0">Total</label>
        <input
          type="number"
          min="10"
          value={total}
          onChange={(e) => setTotal(Math.max(0, Number(e.target.value) || 0))}
          className="field num w-28"
        />
        <div className="text-[12px] text-fg-mute">
          Cobras mínimo <span className="num text-fg-dim">{mxn2(payout)}</span> · ganas <span className="num text-ev">{mxn2(profit)}</span>
        </div>
      </div>

      <p className="mt-4 text-[13px] leading-relaxed text-fg-dim">{sure.reason}</p>
      <p className="mt-1 text-[11px] text-fg-mute">Coloca todas las patas casi al mismo tiempo: si una cuota cambia antes, el arbitraje se rompe.</p>

      <footer className="mt-5 pt-4 border-t border-ink-line flex justify-end">
        {done ? (
          <span className="btn h-10 border border-ev/30 text-ev bg-ev-soft cursor-default">Tomada ✓</span>
        ) : (
          <button onClick={takeAll} disabled={busy || total < 10} className="btn-primary">Tomar surebet</button>
        )}
      </footer>
    </article>
  )
}
