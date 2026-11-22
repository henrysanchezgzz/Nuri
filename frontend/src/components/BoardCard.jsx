import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { mxn, odds as fmtOdds, pct, signedPct } from '../lib/format'
import FactsPanel, { FactBullets } from './FactsPanel'
import { LeagueMark, StatusChip, TeamLogo, useToast } from './ui'

const bookName = (b) => (b === 'Caliente.mx' ? 'Caliente' : b)

const CONF = {
  alta: { label: 'Confianza alta', cls: 'text-ev border-ev/30' },
  media: { label: 'Confianza media', cls: 'text-fg-dim border-ink-soft' },
  baja: { label: 'Confianza baja', cls: 'text-stale border-stale/30' },
}

/** Kelly fraccionado (¼, tope 6 %) para un momio dado; mismo cálculo que el backend. */
function kelly(o, w, l, cfg, vault) {
  const b = o - 1
  if (b <= 0 || w + l <= 0) return { ev: -1, stake: 0 }
  const ev = w * b - l
  const full = (b * w - l) / (b * (w + l))
  if (full <= 0) return { ev, stake: 0 }
  const f = Math.min((cfg.kelly_fraction ?? 0.25) * full, cfg.kelly_cap ?? 0.06)
  const stake = Math.round(Math.min((vault?.balance ?? 500) * f, Math.max(vault?.available ?? 500, 0)))
  return { ev, stake: stake > 0 ? Math.max(stake, cfg.min_stake ?? 10) : 0 }
}

function ladder(minOdds) {
  if (!minOdds) return []
  const o1 = Math.ceil(minOdds * 100) / 100
  const steps = minOdds < 1.5 ? [0, 0.03, 0.06, 0.1] : minOdds < 2.5 ? [0, 0.05, 0.1, 0.2] : [0, 0.1, 0.25, 0.5]
  return steps.map((s) => Math.round((o1 + s) * 100) / 100)
}

/** Tarjeta de probabilidad real: qué apostar, desde qué momio vale la pena y cuánto meter. */
export default function BoardCard({ row, ev, books, cfg, vault }) {
  const [book, setBook] = useState(() => {
    try { return localStorage.getItem('nuri.book') || 'Caliente.mx' } catch { return 'Caliente.mx' }
  })
  const [other, setOther] = useState(false)
  const [value, setValue] = useState('')
  const [takenAt, setTakenAt] = useState(null)
  const [facts, setFacts] = useState(false)
  const toast = useToast()
  const moved = (row.move ?? 0) >= 0.03
  const conf = CONF[row.confidence] || CONF.media
  const home = ev?.home || { name: '' }
  const away = ev?.away || { name: '' }
  const levels = useMemo(() => ladder(row.min_odds).map((o) => ({ o, ...kelly(o, row.w, row.l, cfg, vault) })), [row, cfg, vault])
  const custom = useMemo(() => {
    const o = parseFloat(String(value).replace(',', '.'))
    return o > 1 ? { o, ...kelly(o, row.w, row.l, cfg, vault) } : null
  }, [value, row, cfg, vault])

  const pickBook = (b) => {
    setBook(b)
    try { localStorage.setItem('nuri.book', b) } catch { /* sin almacenamiento */ }
  }

  const take = async (o, stake, b = book) => {
    if (!stake) return
    try {
      await api.takeBet({ pick_id: `${row.id}|${b}`, event_id: row.event_id, market_key: row.market_key, selection: row.selection, book: b, odds: o, stake })
      setTakenAt(o)
      toast(`Registrada en tu Bóveda · ${bookName(b)} @ ${fmtOdds(o)} · ${mxn(stake)}`)
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  return (
    <article className={`card p-5 fade-up flex flex-col min-w-0 ${moved ? 'ring-1 ring-stale/30' : ''}`}>
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 text-[12px] text-fg-mute">
        <LeagueMark league={row.league} logo={ev?.league_logo} />
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {moved && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-stale-soft text-stale px-2 py-0.5 text-[11px] font-medium">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M6 0 1.5 5.6H4.6L4 10l4.5-5.6H5.4z" fill="currentColor" /></svg>
              Línea movida {signedPct(row.move)}
            </span>
          )}
          <StatusChip status={ev?.status || row.status} clock={ev?.clock} start={row.start_ts} />
        </div>
      </header>

      <div className="mt-3 flex items-center gap-2.5 min-w-0">
        <TeamLogo team={home} size={22} />
        <span className="truncate text-[13px] text-fg-dim">{home.name}</span>
        {ev?.score ? (
          <span className="num text-[13px] text-fg font-semibold px-1 whitespace-nowrap shrink-0">{ev.score.home}–{ev.score.away}</span>
        ) : (
          <span className="text-fg-mute text-xs px-0.5">vs</span>
        )}
        <span className="truncate text-[13px] text-fg-dim">{away.name}</span>
        <TeamLogo team={away} size={22} />
      </div>

      <div className="mt-4">
        <div className="text-[12px] text-fg-mute">{row.market_label}</div>
        <div className="mt-0.5 text-[19px] font-semibold tracking-tight text-fg">{row.selection_label}</div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="rounded-xl bg-ink/70 border border-ink-line px-4 py-3">
          <div className="label">Probabilidad real</div>
          <div className="num mt-1 text-[22px] font-semibold text-fg leading-none">{pct(row.p, 0)}</div>
          <div className="mt-1.5 text-[11px] text-fg-mute">Gana {Math.round(row.p * 10)} de cada 10</div>
        </div>
        <div className="rounded-xl bg-ink/70 border border-ink-line px-4 py-3">
          <div className="label">Vale la pena desde</div>
          <div className="num mt-1 text-[22px] font-semibold text-ev leading-none">{fmtOdds(row.min_odds)}</div>
          <div className="mt-1.5 text-[11px] text-fg-mute">Momio justo <span className="num">{fmtOdds(row.fair)}</span></div>
        </div>
      </div>

      {row.quotes?.length > 0 && (
        <div className={`mt-3 rounded-xl border px-3.5 py-2.5 ${row.real_value ? 'border-ev/30 bg-ev-soft' : 'border-ink-line'}`}>
          <div className="text-[11px] text-fg-mute mb-1">Momio real hoy</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {row.quotes.slice(0, 4).map((q) => (
              <button key={q.book} onClick={() => take(q.odds, kelly(q.odds, row.w, row.l, cfg, vault).stake, q.book)} disabled={q.ev < (cfg.ev_threshold ?? 0.045)} title={q.ev >= (cfg.ev_threshold ?? 0.045) ? 'Registrar esta apuesta' : 'Sin valor a este momio'} className="num text-[13px] text-fg disabled:cursor-default">
                {bookName(q.book)} <span className="font-semibold">{fmtOdds(q.odds)}</span>{' '}
                <span className={`text-[11px] ${q.ev >= (cfg.ev_threshold ?? 0.045) ? 'text-ev' : q.ev >= 0 ? 'text-fg-mute' : 'text-live/80'}`}>{signedPct(q.ev, 0)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {row.ref_odds && (
        <div className="mt-3 flex items-center justify-between text-[12px] rounded-xl border border-ink-line px-3.5 py-2">
          <span className="text-fg-mute">Momio público (DraftKings)</span>
          <span className="num text-fg-dim">
            {fmtOdds(row.ref_odds)}{row.open_odds && row.open_odds !== row.ref_odds ? <span className="text-fg-mute"> · abrió {fmtOdds(row.open_odds)}</span> : null}
          </span>
        </div>
      )}

      <p className="mt-4 text-[13px] leading-relaxed text-fg-dim">{row.reason}</p>
      <FactBullets facts={ev?.facts} max={2} />
      <div className="mt-2 flex items-center gap-2 text-[11px] text-fg-mute">
        <span className={`rounded-full border px-2 py-0.5 ${conf.cls}`}>{conf.label}</span>
        <span className="truncate">{row.source}</span>
      </div>
      <button onClick={() => setFacts((v) => !v)} className="mt-2 self-start text-[12px] text-info hover:text-fg underline underline-offset-4 decoration-ink-soft">
        {facts ? 'Ocultar estadísticas' : 'Ver estadísticas reales'}
      </button>
      {facts && <div className="mt-3 pt-3 border-t border-ink-line fade-up"><FactsPanel eventId={row.event_id} ev={ev} sport={row.sport} /></div>}

      <div className="mt-5 pt-4 border-t border-ink-line mt-auto">
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="label">Si tu casa paga…</span>
          <select value={book} onChange={(e) => pickBook(e.target.value)} className="h-7 rounded-lg bg-ink border border-ink-line px-2 text-[12px] text-fg-dim outline-none">
            {books.map((b) => <option key={b} value={b}>{bookName(b)}</option>)}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {levels.map((x) => (
            <button
              key={x.o}
              onClick={() => take(x.o, x.stake)}
              disabled={!x.stake}
              title={`Registrar ${book} @ ${fmtOdds(x.o)} por ${mxn(x.stake)}`}
              className={`rounded-xl border px-3 py-2 text-left transition-colors ${takenAt === x.o ? 'border-ev/40 bg-ev-soft' : 'border-ink-line bg-ink/60 hover:border-ev/40'}`}
            >
              <div className="num text-[14px] text-fg font-semibold">{fmtOdds(x.o)} <span className="text-[11px] text-ev font-normal">{signedPct(x.ev, 0)}</span></div>
              <div className="text-[11px] text-fg-mute">{takenAt === x.o ? 'Tomada ✓' : <>apuesta <span className="num text-fg-dim">{mxn(x.stake)}</span></>}</div>
            </button>
          ))}
        </div>
        <div className="mt-2 text-[11px] text-fg-mute">
          Toca el momio que veas en {book} para registrarla. Si paga menos de {fmtOdds(row.min_odds)}, no tiene valor.{' '}
          <button onClick={() => setOther((v) => !v)} className="underline underline-offset-4 decoration-ink-soft hover:text-fg-dim">Otro momio</button>
        </div>
        {other && (
          <div className="mt-2 flex gap-2 fade-up">
            <input inputMode="decimal" placeholder="Momio, ej. 1.95" value={value} onChange={(e) => setValue(e.target.value)} className="field flex-1 num min-w-0" />
            <button onClick={() => custom && take(custom.o, custom.stake)} disabled={!custom || !custom.stake} className="btn-primary h-10 px-3">
              {custom ? (custom.stake ? `Tomar ${mxn(custom.stake)}` : 'Sin valor') : 'Tomar'}
            </button>
          </div>
        )}
        {other && custom && custom.ev > (cfg.max_credible_ev ?? 0.25) && (
          <div className="mt-2 text-[11px] text-stale">Una ventaja de {signedPct(custom.ev, 0)} casi siempre es error al escribir o una línea distinta. Revísalo.</div>
        )}
      </div>
    </article>
  )
}
