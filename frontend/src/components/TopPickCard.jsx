import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { mxn, odds as fmtOdds, pct, signedPct } from '../lib/format'
import FactsPanel, { FactBullets, FormChips } from './FactsPanel'
import { StatusChip, TeamLogo, useToast } from './ui'

const TAG = {
  'Momio real': 'bg-ev-soft text-ev',
  'Línea movida': 'bg-stale-soft text-stale',
  'En vivo': 'bg-live-soft text-live',
  'La más segura': 'bg-ev-soft text-ev',
  'Buena opción': 'bg-ink-raised text-fg-dim',
  Prop: 'bg-info-soft text-info',
  'Hándicap / total': 'bg-ink-raised text-fg-dim',
}

const BOOK_LABEL = { 'Caliente.mx': 'Caliente' }
const bookName = (b) => BOOK_LABEL[b] || b

function kelly(o, w, l, cfg, vault) {
  const b = o - 1
  if (b <= 0 || w + l <= 0) return 0
  const full = (b * w - l) / (b * (w + l))
  if (full <= 0) return 0
  const f = Math.min((cfg.kelly_fraction ?? 0.25) * full, cfg.kelly_cap ?? 0.06)
  const stake = Math.round(Math.min((vault?.balance ?? 500) * f, Math.max(vault?.available ?? 500, 0)))
  return stake > 0 ? Math.max(stake, cfg.min_stake ?? 10) : 0
}

/** Un pick ya elegido por NURI: qué apostar, con qué momio real o desde qué momio, y cuánto. */
export default function TopPickCard({ pick, ev, books, cfg, vault }) {
  const [book, setBook] = useState(() => {
    try { return localStorage.getItem('nuri.book') || 'Caliente.mx' } catch { return 'Caliente.mx' }
  })
  const [taken, setTaken] = useState(null)
  const [showFacts, setShowFacts] = useState(false)
  const toast = useToast()
  const thr = cfg.ev_threshold ?? 0.045
  const quotes = pick.quotes || []
  const best = quotes[0]
  const bestStake = best ? kelly(best.odds, pick.w, pick.l, cfg, vault) : 0

  const levels = useMemo(() => {
    const o1 = Math.ceil(pick.min_odds * 100) / 100
    const step = pick.min_odds < 1.5 ? 0.04 : pick.min_odds < 2.5 ? 0.07 : 0.2
    return [o1, o1 + step, o1 + step * 2].map((o) => {
      const r = Math.round(o * 100) / 100
      return { o: r, stake: kelly(r, pick.w, pick.l, cfg, vault), ev: pick.w * (r - 1) - pick.l }
    })
  }, [pick, cfg, vault])
  const lo = levels[0]?.stake || 0
  const hi = levels[levels.length - 1]?.stake || 0

  const take = async (o, stake, b = book) => {
    if (!stake) return
    try { localStorage.setItem('nuri.book', b) } catch { /* nada */ }
    try {
      await api.takeBet({ pick_id: `${pick.id}|${b}`, event_id: pick.event_id, market_key: pick.market_key, selection: pick.selection, book: b, odds: o, stake })
      setTaken(`${b}@${o}`)
      toast(`Guardada en tu Bóveda · ${bookName(b)} @ ${fmtOdds(o)} · ${mxn(stake)}`)
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const home = ev?.home || { name: '' }
  const away = ev?.away || { name: '' }
  const url = best && ev?.book_urls?.[best.book]

  return (
    <article className={`card p-5 fade-up min-w-0 ${pick.real_value ? 'ring-1 ring-ev/30' : ''}`}>
      <header className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="num text-[12px] text-fg-mute">#{pick.rank}</span>
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${TAG[pick.tag] || TAG['Buena opción']}`}>{pick.tag}</span>
        </div>
        <StatusChip status={ev?.status || pick.status} clock={ev?.clock} start={pick.start_ts} />
      </header>

      <div className="mt-3 flex items-center gap-2 min-w-0 text-[13px] text-fg-dim">
        <TeamLogo team={home} size={20} />
        <span className="truncate">{home.name}</span>
        {ev?.score ? <span className="num text-fg font-semibold px-1 shrink-0">{ev.score.home}–{ev.score.away}</span> : <span className="text-fg-mute text-xs">vs</span>}
        <span className="truncate">{away.name}</span>
        <TeamLogo team={away} size={20} />
        <span className="ml-auto text-[11px] text-fg-mute shrink-0 hidden sm:inline">{pick.league}</span>
      </div>
      {ev?.facts?.form && (ev.facts.form.home || ev.facts.form.away) && (
        <div className="mt-2 flex items-center gap-3 text-[10.5px] text-fg-mute">
          <span className="inline-flex items-center gap-1.5">{home.short || 'LOC'} <FormChips form={[...(ev.facts.form.home || '')]} size="sm" /></span>
          <span className="inline-flex items-center gap-1.5">{away.short || 'VIS'} <FormChips form={[...(ev.facts.form.away || '')]} size="sm" /></span>
          <span className="hidden sm:inline">últimos 5</span>
        </div>
      )}

      <div className="mt-4 flex items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[21px] font-semibold tracking-tight text-fg leading-tight">{pick.selection_label}</div>
          <div className="mt-1 text-[12px] text-fg-mute">{pick.market_label}</div>
        </div>
        <div className="text-right shrink-0">
          <div className="num text-[20px] font-semibold text-fg leading-none">{pct(pick.p, 0)}</div>
          <div className="mt-1 text-[11px] text-fg-mute whitespace-nowrap">gana {Math.round(pick.p * 10)} de 10</div>
        </div>
      </div>

      {best ? (
        <div className={`mt-4 rounded-xl border px-4 py-3 ${best.ev >= thr ? 'bg-ev-soft border-ev/30' : 'bg-ink/70 border-ink-line'}`}>
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[13px] text-fg">
                {bookName(best.book)} paga <span className="num font-semibold text-[17px] text-fg">{fmtOdds(best.odds)}</span>
                <span className={`num ml-2 text-[13px] font-semibold ${best.ev >= thr ? 'text-ev' : best.ev >= 0 ? 'text-fg-dim' : 'text-live/80'}`}>{signedPct(best.ev, 1)}</span>
              </div>
              <div className="text-[12px] text-fg-mute mt-0.5">
                {best.ev >= thr ? `Momio real con valor · apuesta ${mxn(bestStake)}` : `No conviene a ese momio: pide ${fmtOdds(pick.min_odds)} o más`}
              </div>
            </div>
            {best.ev >= thr && bestStake > 0 && (
              <button onClick={() => take(best.odds, bestStake, best.book)} className={`btn h-9 px-3 text-[12.5px] shrink-0 ${taken === `${best.book}@${best.odds}` ? 'bg-ev text-ink' : 'bg-fg text-ink hover:bg-white'}`}>
                {taken === `${best.book}@${best.odds}` ? 'Guardada ✓' : 'Ya la aposté'}
              </button>
            )}
          </div>
          {quotes.length > 1 && (
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-fg-mute">
              {quotes.slice(1, 5).map((q) => (
                <span key={q.book} className="num">{bookName(q.book)} {fmtOdds(q.odds)} <span className={q.ev >= thr ? 'text-ev' : ''}>{signedPct(q.ev, 0)}</span></span>
              ))}
            </div>
          )}
          {url && <a href={url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-[11.5px] text-info underline underline-offset-4">Abrir el partido en {bookName(best.book)}</a>}
        </div>
      ) : (
        <div className="mt-4 rounded-xl bg-ink/70 border border-ink-line px-4 py-3 flex items-center justify-between gap-3">
          <div>
            <div className="text-[13px] text-fg">Tómala si paga <span className="num text-ev font-semibold text-[16px]">{fmtOdds(pick.min_odds)}</span> o más</div>
            <div className="text-[12px] text-fg-mute mt-0.5">Apuesta {lo === hi ? mxn(lo) : `${mxn(lo)} a ${mxn(hi)}`} según el momio</div>
          </div>
          {pick.ref_odds && <div className="text-right text-[11px] text-fg-mute shrink-0">DraftKings<br /><span className="num text-fg-dim">{fmtOdds(pick.ref_odds)}</span></div>}
        </div>
      )}

      <p className="mt-3 text-[12.5px] leading-relaxed text-fg-dim">{pick.reason}</p>
      <FactBullets facts={ev?.facts} max={3} />

      <div className="mt-3">
        <button onClick={() => setShowFacts((v) => !v)} className="text-[12px] text-info hover:text-fg underline underline-offset-4 decoration-ink-soft">
          {showFacts ? 'Ocultar estadísticas' : 'Ver estadísticas reales del partido'}
        </button>
        {showFacts && (
          <div className="mt-3 pt-3 border-t border-ink-line fade-up">
            <FactsPanel eventId={pick.event_id} ev={ev} sport={pick.sport} />
          </div>
        )}
      </div>

      {!best && (
        <div className="mt-4 pt-4 border-t border-ink-line">
          <div className="flex items-center justify-between gap-2 mb-2">
            <span className="text-[12px] text-fg-mute">¿Ya la apostaste? Toca el momio que te dieron</span>
            <select value={book} onChange={(e) => setBook(e.target.value)} className="h-7 rounded-lg bg-ink border border-ink-line px-2 text-[12px] text-fg-dim outline-none">
              {books.map((b) => <option key={b} value={b}>{bookName(b)}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {levels.map((x) => (
              <button
                key={x.o}
                onClick={() => take(x.o, x.stake)}
                disabled={!x.stake}
                className={`rounded-xl border px-2 py-2 text-center transition-colors ${taken === `${book}@${x.o}` ? 'border-ev/40 bg-ev-soft' : 'border-ink-line bg-ink/60 hover:border-ev/40'}`}
              >
                <div className="num text-[14px] text-fg font-semibold">{fmtOdds(x.o)} <span className="text-[10px] text-ev font-normal">+{Math.round(x.ev * 100)}%</span></div>
                <div className="text-[11px] text-fg-mute">{taken === `${book}@${x.o}` ? 'Guardada ✓' : mxn(x.stake)}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </article>
  )
}
