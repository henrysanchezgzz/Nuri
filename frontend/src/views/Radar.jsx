import { useCallback, useMemo, useState } from 'react'
import BoardCard from '../components/BoardCard'
import OddsSettings from '../components/OddsSettings'
import TopPickCard from '../components/TopPickCard'
import PickCard from '../components/PickCard'
import SurebetCalculator from '../components/SurebetCalculator'
import SurebetCard from '../components/SurebetCard'
import { Empty, Pill } from '../components/ui'
import { SPORTS, ago, isToday, mxn, withinDays } from '../lib/format'

const FILTERS = [
  { key: 'live', label: 'En Vivo (In-Play)', tone: 'live' },
  { key: 'today', label: 'Mejores de Hoy', tone: 'default' },
  { key: 'moved', label: 'Línea movida', tone: 'default' },
  { key: 'safe', label: 'Alta probabilidad', tone: 'ev' },
  { key: 'week', label: 'Radar de la Semana', tone: 'default' },
  { key: 'sure', label: 'Surebets', tone: 'default' },
]

const CONF_RANK = { alta: 0, media: 1, baja: 2 }

function filterRows(rows, key, evById) {
  const status = (r) => evById[r.event_id]?.status || r.status
  if (key === 'live') return rows.filter((r) => status(r) === 'live')
  if (key === 'today') return rows.filter((r) => status(r) === 'live' || (status(r) === 'pre' && isToday(r.start_ts)))
  if (key === 'moved') return rows.filter((r) => (r.move ?? 0) >= 0.03 && status(r) === 'pre')
  if (key === 'safe') return rows.filter((r) => r.p >= 0.75 && r.confidence !== 'baja')
  if (key === 'week') return rows.filter((r) => status(r) === 'pre' && withinDays(r.start_ts, 7))
  return []
}

function filterPicks(list, key) {
  if (key === 'live') return list.filter((p) => p.status === 'live')
  if (key === 'today') return list.filter((p) => p.status === 'live' || isToday(p.start_ts))
  if (key === 'moved') return list.filter((p) => p.stale)
  if (key === 'safe') return list.filter((p) => p.p_real >= 0.75)
  if (key === 'week') return list.filter((p) => p.status === 'pre' && withinDays(p.start_ts, 7))
  return []
}

export function AllPicks({ snapshot }) {
  const [filter, setFilter] = useState('today')
  const [sport, setSport] = useState('all')
  const [limit, setLimit] = useState(30)
  const [hideInfo, setHideInfo] = useState(() => {
    try { return localStorage.getItem('nuri.info2') === '1' } catch { return false }
  })
  const board = snapshot?.board ?? []
  const picks = snapshot?.picks ?? []
  const surebets = snapshot?.surebets ?? []
  const cfg = snapshot?.config ?? {}
  const vault = snapshot?.vault
  const books = cfg.manual_books || ['Caliente.mx', 'Codere', 'Stake', 'Bet365', 'Betway', 'Novibet', 'BetVIP']
  const evById = useMemo(() => Object.fromEntries((snapshot?.events ?? []).map((e) => [e.id, e])), [snapshot])

  const bySport = (xs) => (sport === 'all' ? xs : xs.filter((x) => x.sport === sport))

  const counts = useMemo(() => {
    const c = {}
    FILTERS.forEach((f) => {
      c[f.key] = f.key === 'sure'
        ? bySport(surebets).length
        : bySport(filterRows(board, f.key, evById)).length + bySport(filterPicks(picks, f.key)).length
    })
    return c
  }, [board, picks, surebets, sport, evById]) // eslint-disable-line react-hooks/exhaustive-deps

  const sportsAvailable = useMemo(() => {
    const s = new Set([...board, ...picks].map((p) => p.sport))
    return SPORTS.filter((x) => x.key === 'all' || s.has(x.key))
  }, [board, picks])

  let rows = bySport(filterRows(board, filter, evById))
  rows = [...rows].sort((a, b) => {
    if (filter === 'moved') return (b.move ?? 0) - (a.move ?? 0)
    if (filter === 'week') return a.start_ts - b.start_ts || b.p - a.p
    if (filter === 'safe') return b.p - a.p
    const ma = (a.move ?? 0) >= 0.03 ? 0 : 1
    const mb = (b.move ?? 0) >= 0.03 ? 0 : 1
    return ma - mb || CONF_RANK[a.confidence] - CONF_RANK[b.confidence] || b.p - a.p
  })
  const pickList = bySport(filterPicks(picks, filter))
  const shown = rows.slice(0, limit)

  return (
    <section>
      {snapshot?.mode === 'demo' && (
        <div className="mb-6 rounded-2xl border border-ink-line bg-ink-card/50 px-4 py-3 text-[13px] text-fg-dim flex items-start gap-3">
          <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-stale shrink-0" />
          <span>Modo demo: partidos y cuotas simulados. No apuestes dinero real con estos picks.</span>
        </div>
      )}
      {snapshot?.mode === 'live' && !hideInfo && (
        <div className="mb-6 rounded-2xl border border-ink-line bg-ink-card/50 px-4 py-3 text-[13px] text-fg-dim flex items-start gap-3">
          <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-ev shrink-0" />
          <span className="flex-1">
            Partidos y líneas reales, actualizados solos. Cada tarjeta te dice la probabilidad real y
            <span className="text-fg"> desde qué momio conviene</span>. Abre tu casa, mira el momio y toca el que veas para registrar la apuesta con el monto exacto.
          </span>
          <button onClick={() => { setHideInfo(true); try { localStorage.setItem('nuri.info2', '1') } catch { /* nada */ } }} className="text-fg-mute hover:text-fg text-[12px]">Entendido</button>
        </div>
      )}

      <div className="flex flex-col gap-3">
        <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
          {FILTERS.map((f) => (
            <Pill key={f.key} active={filter === f.key} onClick={() => { setFilter(f.key); setLimit(30) }} count={f.key === 'sure' ? undefined : counts[f.key]} tone={f.tone}>
              {f.key === 'live' && <span className={`w-1.5 h-1.5 rounded-full ${filter === 'live' ? 'bg-white' : 'bg-live live-dot'}`} />}
              {f.label}
            </Pill>
          ))}
        </div>
        {filter !== 'sure' && (
          <div className="flex gap-1.5 overflow-x-auto -mx-1 px-1">
            {sportsAvailable.map((s) => (
              <button
                key={s.key}
                onClick={() => setSport(s.key)}
                className={`h-7 px-3 rounded-full text-[12px] transition-colors ${sport === s.key ? 'bg-ink-raised text-fg border border-ink-soft' : 'text-fg-mute hover:text-fg-dim border border-transparent'}`}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {filter === 'sure' ? (
        <div className="mt-6 space-y-6">
          {surebets.length > 0 && (
            <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
              {surebets.map((s) => <SurebetCard key={s.id} sure={s} />)}
            </div>
          )}
          <SurebetCalculator books={books} />
        </div>
      ) : (
        <>
          <div className="mt-5 mb-4 flex items-center justify-between text-[12px] text-fg-mute">
            <span>{rows.length + pickList.length} apuestas · actualizado {ago(snapshot?.ts)}</span>
            {vault && <span className="hidden sm:inline">Libre <span className="num text-fg-dim">{mxn(vault.available)}</span> de <span className="num text-fg-dim">{mxn(vault.balance)}</span></span>}
          </div>

          {!snapshot ? (
            <Empty title="Conectando con el radar…" text="Cargando partidos y líneas en vivo." />
          ) : rows.length + pickList.length === 0 ? (
            <Empty
              title={filter === 'live' ? 'No hay partidos en vivo ahora' : filter === 'moved' ? 'Ninguna línea se ha movido fuerte' : 'Nada en esta vista por ahora'}
              text={filter === 'live' ? 'En cuanto empiece un partido de tus ligas aparece aquí con probabilidades en vivo.' : `El radar se actualiza solo cada ${snapshot.tick_seconds || 5} s.`}
            />
          ) : (
            <>
              {pickList.length > 0 && (
                <div className="mb-8">
                  <div className="label mb-3">Con momio de casa mexicana (+EV)</div>
                  <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
                    {pickList.map((p) => <PickCard key={p.id} pick={p} books={books} minStake={cfg.min_stake || 10} />)}
                  </div>
                </div>
              )}
              <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
                {shown.map((r) => <BoardCard key={r.id} row={r} ev={evById[r.event_id]} books={books} cfg={cfg} vault={vault} />)}
              </div>
              {rows.length > limit && (
                <div className="mt-6 flex justify-center">
                  <button onClick={() => setLimit((l) => l + 30)} className="btn-ghost">Ver más ({rows.length - limit})</button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}


const SECTION_ORDER = ['real', 'valor', 'props', 'lineas', 'seguras', 'vivo']
const SECTION_INFO = {
  real: { title: 'Momio real con valor', desc: 'Comparado contra el momio que de verdad paga la casa hoy (Bet365, y Caliente/Codere si los conectas). La ventaja ya está calculada con ese momio.' },
  valor: { title: 'Valor: el mercado se movió', desc: 'El dinero fuerte movió la línea y las casas mexicanas suelen tardar en ajustar. Aquí suele estar el valor real, también en underdogs.' },
  props: { title: 'Props de jugadores', desc: 'Con números reales de la temporada: puntos/rebotes/asistencias (NBA), yardas (NFL) y goleadores (fútbol, con el momio de Bet365 cuando existe).' },
  lineas: { title: 'Hándicaps y totales', desc: 'Más/menos y hándicaps con la mejor probabilidad del mercado.' },
  seguras: { title: 'Las más seguras', desc: 'Ganan 7 u 8 de cada 10. Pagan poco, pero son las más firmes.' },
  vivo: { title: 'En vivo ahora', desc: 'Recalculadas con el marcador y el reloj reales.' },
}

const MODES = [
  { key: 'best', label: 'Mejores picks' },
  { key: 'live', label: 'En vivo' },
  { key: 'all', label: 'Ver todos' },
]

/** Franja de estado: de dónde salen los momios reales y botón para conectar Caliente/Codere. */
function RealOddsStrip({ snapshot, onOpen }) {
  const src = snapshot?.sources || {}
  const b365 = src['Bet365 · momios reales']
  const facts = src['Estadísticas reales (ESPN)']
  const io = snapshot?.oddsio || {}
  const ioOk = io.configured && io.ok && (io.books || []).length > 0
  return (
    <div className="mb-6 rounded-2xl border border-ink-line bg-ink-card/50 px-4 py-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12.5px]">
      <span className="inline-flex items-center gap-2 text-fg-dim">
        <span className={`w-1.5 h-1.5 rounded-full ${b365?.ok ? 'bg-ev' : 'bg-fg-mute'}`} />
        Bet365 real: <span className="text-fg">{b365?.detail || '—'}</span>
      </span>
      <span className="inline-flex items-center gap-2 text-fg-dim">
        <span className={`w-1.5 h-1.5 rounded-full ${facts?.ok ? 'bg-ev' : 'bg-fg-mute'}`} />
        Estadísticas: <span className="text-fg">{facts?.detail || 'cargando…'}</span>
      </span>
      <span className="inline-flex items-center gap-2 text-fg-dim">
        <span className={`w-1.5 h-1.5 rounded-full ${ioOk ? 'bg-ev' : io.configured ? 'bg-stale' : 'bg-fg-mute'}`} />
        Caliente / Codere: <span className="text-fg">{ioOk ? (io.detail || 'conectado') : io.configured ? (io.detail || 'revisando…') : 'sin conectar'}</span>
      </span>
      <button onClick={onOpen} className="ml-auto btn-ghost h-8 px-3 text-[12px]">{ioOk ? 'Ajustes de momios' : 'Conectar Caliente y Codere'}</button>
    </div>
  )
}

/** Vista principal: NURI ya eligió por ti las mejores opciones de hoy. */
export default function Radar({ snapshot }) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const closeSettings = useCallback(() => setSettingsOpen(false), [])
  const [mode, setMode] = useState(() => {
    try { return localStorage.getItem('nuri.mode') || 'best' } catch { return 'best' }
  })
  const pick = (m) => { setMode(m); try { localStorage.setItem('nuri.mode', m) } catch { /* nada */ } }
  const top = snapshot?.top ?? []
  const events = snapshot?.events ?? []
  const evById = useMemo(() => Object.fromEntries(events.map((e) => [e.id, e])), [events])
  const cfg = snapshot?.config ?? {}
  const vault = snapshot?.vault
  const books = cfg.manual_books || ['Caliente.mx', 'Codere', 'Stake', 'Bet365', 'Betway', 'Novibet', 'BetVIP']
  const liveRows = (snapshot?.board ?? []).filter((r) => evById[r.event_id]?.status === 'live').sort((a, b) => b.p - a.p)
  const nGames = events.filter((e) => e.status !== 'final').length

  return (
    <section>
      <div className="flex items-end justify-between gap-4 flex-wrap mb-5">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-fg">
            {mode === 'best' ? 'Tus mejores picks' : mode === 'live' ? 'En vivo ahora' : 'Todas las apuestas'}
          </h1>
          <p className="mt-1 text-[13px] text-fg-mute">
            {mode === 'best'
              ? `NURI revisó ${nGames} partidos y eligió estos. Abre tu casa, busca el pick y apuéstalo si paga el momio indicado o más.`
              : mode === 'live' ? 'Probabilidades que se recalculan con el marcador y el reloj reales.' : 'El tablero completo con filtros.'}
          </p>
        </div>
        <div className="flex rounded-full border border-ink-line p-1 bg-ink-card/60">
          {MODES.map((m) => (
            <button key={m.key} onClick={() => pick(m.key)} className={`h-8 px-4 rounded-full text-[13px] font-medium whitespace-nowrap ${mode === m.key ? 'bg-fg text-ink' : 'text-fg-dim hover:text-fg'}`}>
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {snapshot?.mode === 'live' && <RealOddsStrip snapshot={snapshot} onOpen={() => setSettingsOpen(true)} />}
      <OddsSettings open={settingsOpen} onClose={closeSettings} state={snapshot?.oddsio} />

      {mode === 'all' && <AllPicks snapshot={snapshot} />}

      {mode === 'best' && (!snapshot ? (
        <Empty title="Buscando los mejores picks…" text="Cargando partidos y líneas en vivo." />
      ) : top.length === 0 ? (
        <Empty title="Todavía no hay picks claros" text={`NURI está revisando ${nGames} partidos. En cuanto haya una opción buena aparece aquí sola.`} />
      ) : (
        <div className="space-y-10">
          {SECTION_ORDER.filter((k) => top.some((p) => p.section === k)).map((k) => (
            <div key={k}>
              <div className="mb-3">
                <h2 className="text-[16px] font-semibold text-fg">{SECTION_INFO[k].title}</h2>
                <p className="text-[12.5px] text-fg-mute mt-0.5">{SECTION_INFO[k].desc}</p>
              </div>
              <div className="grid gap-4 grid-cols-1 md:grid-cols-2">
                {top.filter((p) => p.section === k).map((p) => <TopPickCard key={p.id} pick={p} ev={evById[p.event_id]} books={books} cfg={cfg} vault={vault} />)}
              </div>
            </div>
          ))}
        </div>
      ))}

      {mode === 'live' && (liveRows.length === 0 ? (
        <Empty title="No hay partidos en vivo ahora" text="Cuando empiece uno de tus ligas aparece aquí con probabilidades en vivo." />
      ) : (
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
          {liveRows.slice(0, 24).map((r) => <BoardCard key={r.id} row={r} ev={evById[r.event_id]} books={books} cfg={cfg} vault={vault} />)}
        </div>
      ))}

      <p className="mt-8 text-[11px] text-fg-mute">
        Las probabilidades salen de la línea de mercado real (sin margen) y del modelo de NURI; las estadísticas, de ESPN; los momios
        reales, de Bet365 (vía ESPN) y de Caliente/Codere si los conectas. Ningún pick es seguro al 100 %: una ventaja real en
        apuestas es de 2–8 %, por eso NURI sólo te recomienda apostar montos chicos (Kelly ¼). Revisa el momio en tu app antes de apostar.
      </p>
    </section>
  )
}
