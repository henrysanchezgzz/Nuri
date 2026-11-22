import { useEffect, useMemo, useState } from 'react'
import FactsPanel from '../components/FactsPanel'
import { Empty, Flash, LeagueMark, LiveOdds, Pill, StatusChip, TeamLogo } from '../components/ui'
import { SPORTS, kickoff, odds as fmtOdds, pct, signedPct } from '../lib/format'

function MainOdds({ main }) {
  if (!main) return <div className="text-[11px] text-fg-mute">Sin línea</div>
  return (
    <div className="flex gap-1.5">
      {main.selections.map((s) => (
        <div key={s.selection} className="w-[62px] rounded-lg border border-ink-line bg-ink/60 px-2 py-1.5 text-center" title={s.book ? `${s.book === 'Caliente.mx' ? 'Caliente' : s.book} (momio real)` : 'Momio justo (probabilidad real)'}>
          <div className="text-[10px] text-fg-mute">{s.label}{!s.odds && s.fair ? ' · justo' : ''}</div>
          {s.odds ? (
            <LiveOdds value={s.odds} trend={s.trend} className="text-[13px] text-fg font-medium justify-center" />
          ) : s.fair ? (
            <LiveOdds value={s.fair} className="text-[13px] text-fg-dim font-medium justify-center" />
          ) : (
            <div className="text-[13px] text-fg-mute">—</div>
          )}
        </div>
      ))}
    </div>
  )
}

function Row({ ev, onOpen, active }) {
  const live = ev.status === 'live'
  return (
    <button
      onClick={() => onOpen(ev.id)}
      className={`w-full text-left px-4 sm:px-5 py-3.5 flex items-center gap-4 transition-colors ${active ? 'bg-ink-raised' : 'hover:bg-ink-raised/60'}`}
    >
      <div className="w-[96px] shrink-0">
        <StatusChip status={ev.status} clock={ev.clock} start={ev.start_ts} compact />
      </div>
      <div className="flex-1 min-w-0 space-y-1.5">
        {['home', 'away'].map((side) => (
          <div key={side} className="flex items-center gap-2.5">
            <TeamLogo team={ev[side]} size={20} />
            <span className="truncate text-[14px] text-fg">{ev[side].name}</span>
            {ev.score && (
              <Flash value={ev.score[side]} className="ml-auto num text-[15px] font-semibold text-fg px-1.5">
                {ev.score[side]}
              </Flash>
            )}
          </div>
        ))}
      </div>
      <div className="hidden md:block shrink-0"><MainOdds main={ev.main} /></div>
      <div className="w-14 shrink-0 text-right">
        {ev.picks_count > 0 ? (
          <span className="inline-flex items-center rounded-full bg-ev-soft text-ev px-2 py-0.5 text-[11px] font-medium num">{ev.picks_count} +EV</span>
        ) : live ? (
          <span className="text-[11px] text-fg-mute">—</span>
        ) : null}
      </div>
    </button>
  )
}

function StatBar({ s }) {
  const h = Number(s.home) || 0
  const a = Number(s.away) || 0
  const tot = h + a || 1
  const fmt = (v) => (s.fmt === 'pct' ? `${v}%` : s.fmt === 'dec' ? Number(v).toFixed(2) : v)
  return (
    <div>
      <div className="flex justify-between text-[13px]">
        <span className="num text-fg">{fmt(s.home)}</span>
        <span className="text-fg-mute text-[12px]">{s.label}</span>
        <span className="num text-fg">{fmt(s.away)}</span>
      </div>
      <div className="mt-1.5 flex gap-1 h-1">
        <div className="flex-1 flex justify-end bg-ink-line/60 rounded-full overflow-hidden">
          <div className="h-full bg-fg/80 rounded-full transition-all duration-700" style={{ width: `${(h / tot) * 100}%` }} />
        </div>
        <div className="flex-1 bg-ink-line/60 rounded-full overflow-hidden">
          <div className="h-full bg-info/80 rounded-full transition-all duration-700" style={{ width: `${(a / tot) * 100}%` }} />
        </div>
      </div>
    </div>
  )
}

const TL_COLOR = { score: 'bg-ev', red: 'bg-live', yellow: 'bg-stale', injury: 'bg-live', news: 'bg-info', end: 'bg-fg-mute' }

function Drawer({ ev, picks, rows, alerts, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!ev) return null
  const model = ev.main?.selections?.filter((s) => s.p_model != null) ?? []
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <aside className="slide-in relative w-full max-w-[460px] h-full overflow-y-auto bg-ink-card border-l border-ink-line">
        <div className="sticky top-0 bg-ink-card/95 backdrop-blur px-6 pt-5 pb-4 border-b border-ink-line z-10">
          <div className="flex items-center justify-between text-[12px] text-fg-mute">
            <LeagueMark league={ev.league} logo={ev.league_logo} />
            <button onClick={onClose} className="w-8 h-8 -mr-2 rounded-full grid place-items-center hover:bg-ink-raised text-fg-dim" aria-label="Cerrar">
              <svg width="12" height="12" viewBox="0 0 12 12"><path d="M1 1l10 10M11 1 1 11" stroke="currentColor" strokeWidth="1.5" /></svg>
            </button>
          </div>
          <div className="mt-5 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
            <div className="flex flex-col items-center gap-2 text-center min-w-0">
              <TeamLogo team={ev.home} size={44} />
              <span className="text-[13px] text-fg truncate w-full">{ev.home.name}</span>
            </div>
            <div className="text-center">
              {ev.score ? (
                <div className="num text-[32px] font-semibold text-fg tracking-tight">
                  <Flash value={ev.score.home}>{ev.score.home}</Flash>
                  <span className="text-fg-mute mx-2">–</span>
                  <Flash value={ev.score.away}>{ev.score.away}</Flash>
                </div>
              ) : (
                <div className="num text-[15px] text-fg-dim">{kickoff(ev.start_ts)}</div>
              )}
              <div className="mt-1"><StatusChip status={ev.status} clock={ev.clock} start={ev.start_ts} /></div>
            </div>
            <div className="flex flex-col items-center gap-2 text-center min-w-0">
              <TeamLogo team={ev.away} size={44} />
              <span className="text-[13px] text-fg truncate w-full">{ev.away.name}</span>
            </div>
          </div>
        </div>

        <div className="px-6 py-5 space-y-7">
          {ev.main && (
            <section>
              <div className="label mb-2.5">{ev.main.selections.some((s) => s.odds) ? 'Mejor momio real' : 'Momio justo (sin margen)'}</div>
              <div className="grid grid-cols-3 gap-2">
                {ev.main.selections.map((s) => (
                  <div key={s.selection} className="rounded-xl border border-ink-line bg-ink/60 px-3 py-2.5">
                    <div className="text-[11px] text-fg-mute truncate">{s.label} · {s.book ? (s.book === 'Caliente.mx' ? 'Caliente' : s.book) : (s.p_model != null ? pct(s.p_model, 0) : '—')}</div>
                    <LiveOdds value={s.odds || s.fair} trend={s.trend} className="text-[17px] text-fg font-semibold" />
                  </div>
                ))}
              </div>
              {model.length > 0 && (
                <div className="mt-2 text-[11px] text-fg-mute">
                  Prob. modelo: {model.map((s) => `${s.label} ${pct(s.p_model)}`).join(' · ')}
                  {ev.model?.method ? ` — ${ev.model.method}` : ''}
                </div>
              )}
            </section>
          )}

          <section>
            <div className="label mb-2.5">Datos reales del partido</div>
            <FactsPanel key={ev.id} eventId={ev.id} ev={ev} sport={ev.sport} />
          </section>

          {rows.length > 0 && (
            <section>
              <div className="label mb-2.5">Probabilidades de este partido</div>
              <div className="space-y-2">
                {rows.map((r) => (
                  <div key={r.id} className={`rounded-xl border px-3.5 py-3 ${(r.move ?? 0) >= 0.03 ? 'border-stale/30 bg-stale-soft' : 'border-ink-line bg-ink/60'}`}>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-[13px] text-fg truncate">{r.selection_label} <span className="text-fg-mute">· {r.market_label}</span></span>
                      <span className="num text-fg text-[13px] font-semibold shrink-0">{pct(r.p, 0)}</span>
                    </div>
                    <div className="mt-1 text-[12px] text-fg-dim">
                      Vale la pena desde <span className="num text-ev">{fmtOdds(r.min_odds)}</span>{(r.move ?? 0) >= 0.03 ? ` · línea movida ${signedPct(r.move)}` : ''}
                      {r.best_book && <> · {r.best_book === 'Caliente.mx' ? 'Caliente' : r.best_book} paga <span className="num text-fg">{fmtOdds(r.best_odds)}</span> <span className={`num ${r.real_value ? 'text-ev' : 'text-fg-mute'}`}>{signedPct(r.best_ev, 0)}</span></>}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {picks.length > 0 && (
            <section>
              <div className="label mb-2.5">Alertas in-play activas</div>
              <div className="space-y-2">
                {picks.map((p) => (
                  <div key={p.id} className={`rounded-xl border px-3.5 py-3 ${p.stale ? 'border-stale/30 bg-stale-soft' : 'border-ink-line bg-ink/60'}`}>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-[13px] text-fg truncate">{p.selection_label} <span className="text-fg-mute">· {p.market_label}</span></span>
                      <span className="num text-ev text-[13px] font-semibold shrink-0">{signedPct(p.ev)}</span>
                    </div>
                    <div className="mt-1 text-[12px] text-fg-dim">{p.book} @ <span className="num">{fmtOdds(p.odds)}</span>{p.stale ? ` · línea dormida ${p.stale.lag_s} s` : ''}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {ev.stats?.length > 0 && (
            <section>
              <div className="label mb-3">Estadísticas en vivo</div>
              <div className="space-y-4">{ev.stats.map((s) => <StatBar key={s.label} s={s} />)}</div>
            </section>
          )}

          {alerts.length > 0 && (
            <section>
              <div className="label mb-2.5">Movimientos de línea</div>
              <ul className="space-y-1.5 text-[12px] text-fg-dim">
                {alerts.map((a, i) => (
                  <li key={i} className="flex gap-2"><span className={`mt-1.5 w-1 h-1 rounded-full ${a.type === 'stale' ? 'bg-stale' : 'bg-info'}`} />{a.text}</li>
                ))}
              </ul>
            </section>
          )}

          {ev.timeline?.length > 0 && (
            <section>
              <div className="label mb-3">Minuto a minuto</div>
              <ol className="relative border-l border-ink-line ml-1.5 space-y-3">
                {[...ev.timeline].reverse().map((t, i) => (
                  <li key={i} className="pl-4 relative">
                    <span className={`absolute -left-[4.5px] top-1.5 w-2 h-2 rounded-full ${TL_COLOR[t.type] || 'bg-fg-mute'}`} />
                    <span className="num text-[11px] text-fg-mute mr-2">{t.t}</span>
                    <span className="text-[13px] text-fg-dim">{t.text}</span>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>
      </aside>
    </div>
  )
}

export default function Live({ snapshot }) {
  const [sport, setSport] = useState('all')
  const [openId, setOpenId] = useState(null)
  const events = snapshot?.events ?? []

  const filtered = useMemo(() => (sport === 'all' ? events : events.filter((e) => e.sport === sport)), [events, sport])
  const live = filtered.filter((e) => e.status === 'live')
  const upcoming = filtered.filter((e) => e.status === 'pre').slice(0, 30)
  const finals = filtered.filter((e) => e.status === 'final')
  const counts = useMemo(() => {
    const c = {}
    events.forEach((e) => { if (e.status === 'live') c[e.sport] = (c[e.sport] || 0) + 1 })
    return c
  }, [events])
  const open = events.find((e) => e.id === openId)
  const openPicks = (snapshot?.picks ?? []).filter((p) => p.event_id === openId)
  const openRows = (snapshot?.board ?? []).filter((r) => r.event_id === openId)
  const openAlerts = (snapshot?.alerts ?? []).filter((a) => a.event_id === openId).slice(0, 6)
  const sportsAvailable = SPORTS.filter((s) => s.key === 'all' || events.some((e) => e.sport === s.key))

  // Función (no componente) para que React conserve el estado de cada fila entre ticks.
  const renderGroup = (title, items) =>
    items.length > 0 && (
      <div className="mb-6" key={title.split(' ')[0]}>
        <div className="label mb-2.5 px-1">{title}</div>
        <div className="card overflow-hidden divide-y divide-ink-line">
          {groupByLeague(items).map(([league, evs]) => (
            <div key={league}>
              <div className="px-5 pt-3 pb-1 text-[11px] text-fg-mute"><LeagueMark league={league} logo={evs[0].league_logo} size={14} /></div>
              <div className="divide-y divide-ink-line/60">
                {evs.map((ev) => <Row key={ev.id} ev={ev} onOpen={setOpenId} active={ev.id === openId} />)}
              </div>
            </div>
          ))}
        </div>
      </div>
    )

  return (
    <section>
      <div className="flex gap-2 overflow-x-auto pb-1 mb-6 -mx-1 px-1">
        {sportsAvailable.map((s) => (
          <Pill key={s.key} active={sport === s.key} onClick={() => setSport(s.key)} count={s.key === 'all' ? events.filter((e) => e.status === 'live').length : counts[s.key] || 0}>
            {s.label}
          </Pill>
        ))}
      </div>
      {!snapshot ? (
        <Empty title="Conectando…" text="Cargando marcadores en vivo." />
      ) : filtered.length === 0 ? (
        <Empty title="Sin partidos" text="No hay eventos para este deporte en este momento." />
      ) : (
        <>
          {renderGroup(`En vivo · ${live.length}`, live)}
          {renderGroup('Próximos', upcoming)}
          {renderGroup('Finalizados', finals)}
        </>
      )}
      <Drawer ev={open} picks={openPicks} rows={openRows} alerts={openAlerts} onClose={() => setOpenId(null)} />
    </section>
  )
}

function groupByLeague(items) {
  const m = new Map()
  items.forEach((e) => {
    if (!m.has(e.league)) m.set(e.league, [])
    m.get(e.league).push(e)
  })
  return [...m.entries()]
}
