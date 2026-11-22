import { useState } from 'react'
import { api } from '../lib/api'
import { mxn, odds as fmtOdds, pct, signedPct } from '../lib/format'
import { LeagueMark, LiveOdds, StatusChip, TeamLogo, useToast } from './ui'

function Teams({ pick }) {
  const live = pick.status === 'live' && pick.score
  return (
    <div className="flex items-center gap-2.5 min-w-0">
      <TeamLogo team={pick.home} size={22} />
      <span className="truncate text-[13px] text-fg-dim">{pick.home.name}</span>
      {live ? (
        <span className="num text-[13px] text-fg font-semibold px-1 whitespace-nowrap shrink-0">
          {pick.score.home}–{pick.score.away}
        </span>
      ) : (
        <span className="text-fg-mute text-xs px-0.5">vs</span>
      )}
      <span className="truncate text-[13px] text-fg-dim">{pick.away.name}</span>
      <TeamLogo team={pick.away} size={22} />
    </div>
  )
}

function ManualCheck({ pick, books }) {
  const [book, setBook] = useState('BetVIP')
  const [value, setValue] = useState('')
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const check = async () => {
    const o = parseFloat(String(value).replace(',', '.'))
    if (!(o > 1)) return toast('Escribe una cuota decimal mayor a 1.00', 'error')
    setBusy(true)
    try {
      const r = await api.validate({ event_id: pick.event_id, market_key: pick.market_key, selection: pick.selection, odds: o, book })
      setRes(r)
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const take = async () => {
    if (!res?.ok || !res.stake) return
    try {
      await api.takeBet({ pick_id: `${pick.event_id}|${pick.market_key}|${pick.selection}|${book}`, event_id: pick.event_id, market_key: pick.market_key, selection: pick.selection, book, odds: res.odds, stake: res.stake })
      toast(`Registrada en ${book} @ ${fmtOdds(res.odds)}`)
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  return (
    <div className="mt-3 rounded-xl border border-ink-line bg-ink/60 p-3 fade-up">
      <div className="label mb-2">Validar cuota manual</div>
      <div className="flex gap-2">
        <select value={book} onChange={(e) => setBook(e.target.value)} className="field w-32 pr-2">
          {books.map((b) => (
            <option key={b} value={b}>{b}</option>
          ))}
        </select>
        <input
          inputMode="decimal"
          placeholder="2.10"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && check()}
          className="field flex-1 num min-w-0"
        />
        <button onClick={check} disabled={busy} className="btn-ghost h-10 px-3">Validar</button>
      </div>
      {res && (
        <div className="mt-2.5 text-[13px] flex items-center justify-between gap-3">
          {res.ok ? (
            <>
              <span className={res.ev >= 0.045 ? 'text-ev' : res.ev > 0 ? 'text-fg-dim' : 'text-fg-mute'}>
                <span className="num font-semibold">{signedPct(res.ev)}</span> · {res.verdict}
                {res.stake > 0 && <span className="text-fg-dim"> · {mxn(res.stake)}</span>}
              </span>
              {res.ev > 0 && res.stake > 0 && (
                <button onClick={take} className="text-fg text-xs underline underline-offset-4 decoration-ink-soft hover:decoration-fg">Tomar</button>
              )}
            </>
          ) : (
            <span className="text-fg-mute">{res.message}</span>
          )}
        </div>
      )}
      <div className="mt-2 text-[11px] text-fg-mute">Justa: <span className="num">{fmtOdds(pick.fair_odds)}</span> — cualquier cuota arriba de ésta tiene valor.</div>
    </div>
  )
}

export default function PickCard({ pick, books, minStake, onTaken }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [taken, setTaken] = useState(false)
  const toast = useToast()
  const isTaken = pick.taken || taken
  const stake = pick.below_min ? minStake : pick.stake

  const takeBet = async () => {
    if (!stake) return
    setBusy(true)
    try {
      await api.takeBet({ pick_id: pick.id, event_id: pick.event_id, market_key: pick.market_key, selection: pick.selection, book: pick.book, odds: pick.odds, stake })
      setTaken(true)
      toast(`Apuesta registrada · ${pick.book} @ ${fmtOdds(pick.odds)} · ${mxn(stake)}`)
      onTaken?.()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className={`card p-5 fade-up flex flex-col min-w-0 ${pick.stale ? 'ring-1 ring-stale/30' : ''}`}>
      {/* Liga + estado */}
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 text-[12px] text-fg-mute">
        <LeagueMark league={pick.league} logo={pick.league_logo} />
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {pick.stale && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-stale-soft text-stale px-2 py-0.5 text-[11px] font-medium">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M6 0 1.5 5.6H4.6L4 10l4.5-5.6H5.4z" fill="currentColor" /></svg>
              Línea dormida · {pick.stale.lag_s} s
            </span>
          )}
          <StatusChip status={pick.status} clock={pick.clock} start={pick.start_ts} />
        </div>
      </header>

      <div className="mt-3"><Teams pick={pick} /></div>

      {/* Mercado y selección */}
      <div className="mt-4">
        <div className="text-[12px] text-fg-mute">{pick.market_label}</div>
        <div className="mt-0.5 text-[19px] font-semibold tracking-tight text-fg">{pick.selection_label}</div>
      </div>

      {/* Casa / cuota / ventaja */}
      <div className="mt-4 flex items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="label">Casa en México</div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-[15px] text-fg font-medium">{pick.book}</span>
            <span className="text-fg-mute">@</span>
            <LiveOdds value={pick.odds} trend={pick.trend} className="text-[18px] text-fg font-semibold" />
          </div>
          {pick.others?.length > 0 && (
            <div className="mt-1 text-[11px] text-fg-mute num truncate">
              {pick.others.slice(0, 3).map((o) => `${o.book} ${fmtOdds(o.odds)}`).join(' · ')}
            </div>
          )}
        </div>
        <div className="text-right shrink-0">
          <div className="label whitespace-nowrap">Ventaja real</div>
          <div className="num mt-1 text-[22px] font-semibold text-ev leading-none whitespace-nowrap">{signedPct(pick.ev)}</div>
        </div>
      </div>

      {/* Monto */}
      <div className="mt-4 rounded-xl bg-ink/70 border border-ink-line px-4 py-3 flex items-center justify-between">
        <div>
          <div className="label">Apostar</div>
          <div className="num text-[20px] text-fg font-semibold mt-0.5">{mxn(stake)} <span className="text-[12px] text-fg-mute font-normal">MXN</span></div>
        </div>
        <div className="text-right text-[11px] text-fg-mute leading-relaxed">
          <div>Prob. real <span className="num text-fg-dim">{pct(pick.p_real)}</span></div>
          <div>Kelly ¼ <span className="num text-fg-dim">{pct(pick.kelly_fraction, 2)}</span></div>
        </div>
      </div>
      {pick.below_min && (
        <div className="mt-2 text-[11px] text-fg-mute">Kelly sugiere {mxn(pick.stake)}; se usa el mínimo de la casa ({mxn(minStake)}).</div>
      )}

      {/* Por qué */}
      <p className="mt-4 text-[13px] leading-relaxed text-fg-dim">{pick.reason}</p>
      <div className="mt-1.5 text-[11px] text-fg-mute">{pick.source}</div>

      {open && <ManualCheck pick={pick} books={books} />}

      <footer className="mt-5 pt-4 border-t border-ink-line flex items-center justify-between gap-3 mt-auto">
        <button onClick={() => setOpen((v) => !v)} className="text-[12px] text-fg-dim hover:text-fg inline-flex items-center gap-1.5">
          Validar cuota manual
          <svg width="10" height="10" viewBox="0 0 10 10" className={`transition-transform ${open ? 'rotate-180' : ''}`}><path d="M1 3l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>
        </button>
        {isTaken ? (
          <span className="btn h-10 border border-ev/30 text-ev bg-ev-soft cursor-default">Tomada ✓</span>
        ) : (
          <button onClick={takeBet} disabled={busy || !stake} className="btn-primary">
            {stake ? 'Tomar apuesta' : 'Sin saldo libre'}
          </button>
        )}
      </footer>
    </article>
  )
}
