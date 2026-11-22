import { odds as fmtOdds, pct, signedPct } from '../lib/format'
import { TeamLogo } from '../components/ui'

/* NURI v3: sólo los mejores picks y su autoevaluación semanal. */

const RESULT = {
  won: { icon: '✓', cls: 'text-ev', label: 'Ganó' },
  half_won: { icon: '½', cls: 'text-ev', label: 'Medio ganado' },
  lost: { icon: '✗', cls: 'text-live', label: 'Perdió' },
  half_lost: { icon: '½', cls: 'text-live', label: 'Medio perdido' },
  push: { icon: '=', cls: 'text-fg-dim', label: 'Devuelta' },
  void: { icon: '·', cls: 'text-fg-mute', label: 'Anulada' },
}

const DIAS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb']

function when(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const hm = d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', hour12: false })
  const sameDay = d.toDateString() === now.toDateString()
  const tomorrow = new Date(now.getTime() + 86400000).toDateString() === d.toDateString()
  if (sameDay) return `Hoy ${hm}`
  if (tomorrow) return `Mañana ${hm}`
  return `${DIAS[d.getDay()]} ${d.getDate()} · ${hm}`
}

function units(u) {
  if (u === null || u === undefined) return '—'
  return `${u > 0 ? '+' : ''}${u.toFixed(2)} u`
}

function PickCard({ p }) {
  const live = p.live?.status === 'live'
  const a = p.tier === 'A'
  return (
    <article className={`card p-5 sm:p-6 fade-up ${a ? 'ring-1 ring-ev/25' : ''}`}>
      <header className="flex items-center justify-between gap-3 text-[12px]">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ${a ? 'bg-ev-soft text-ev' : 'bg-stale-soft text-stale'}`}>
            {a ? 'Momio real con valor' : 'Línea movida'}
          </span>
          <span className="text-fg-mute truncate">{p.league}</span>
        </div>
        {live ? (
          <span className="inline-flex items-center gap-1.5 text-live num whitespace-nowrap shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-live animate-pulse" />{p.live?.score ? `${p.live.score.home}–${p.live.score.away}` : 'En vivo'} {p.live?.clock || ''}</span>
        ) : (
          <span className="num text-info whitespace-nowrap shrink-0">{when(p.start_ts)}</span>
        )}
      </header>

      <div className="mt-3 flex items-center gap-2 text-[13.5px] text-fg-dim min-w-0">
        <TeamLogo team={{ name: p.home, logo: p.home_logo }} size={20} />
        <span className="truncate">{p.home}</span>
        <span className="text-fg-mute text-xs">vs</span>
        <span className="truncate">{p.away}</span>
        <TeamLogo team={{ name: p.away, logo: p.away_logo }} size={20} />
      </div>

      <div className="mt-4">
        <div className="text-[24px] font-semibold tracking-tight text-fg leading-tight">{p.selection_label}</div>
        <div className="mt-1 text-[12.5px] text-fg-mute">{p.market_label}</div>
      </div>

      <div className={`mt-4 rounded-xl border px-4 py-3.5 ${a ? 'border-ev/30 bg-ev-soft' : 'border-ink-line bg-ink/70'}`}>
        {a ? (
          <>
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[13px] text-fg">{p.book} paga</span>
              <span className="num text-[24px] font-semibold text-fg">{fmtOdds(p.odds)}</span>
            </div>
            <div className="mt-1 flex items-center justify-between text-[12px]">
              <span className="text-ev font-medium">Ventaja {signedPct(p.ev, 1)}</span>
              <span className="text-fg-mute">Si ya bajó de {fmtOdds(p.min_odds)}, no la tomes</span>
            </div>
          </>
        ) : (
          <>
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[13px] text-fg">Tómala sólo si tu casa paga</span>
              <span className="num text-[24px] font-semibold text-ev">{fmtOdds(p.odds)}+</span>
            </div>
            <div className="mt-1 text-[12px] text-fg-mute">A ese momio la ventaja es {signedPct(p.ev, 1)}. Si paga menos, no hay valor: déjala pasar.</div>
          </>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[12.5px]">
        <div className="rounded-lg border border-ink-line px-3 py-2">
          <div className="text-fg-mute text-[11px]">Probabilidad real</div>
          <div className="num text-fg font-semibold">{pct(p.p, 0)} <span className="text-fg-mute font-normal">· gana {Math.round(p.p * 10)} de 10</span></div>
        </div>
        <div className="rounded-lg border border-ink-line px-3 py-2">
          <div className="text-fg-mute text-[11px]">Cuánto apostar</div>
          <div className="num text-fg font-semibold">{p.stake_pct ? `${(p.stake_pct * 100).toFixed(1)} %` : '1 %'} <span className="text-fg-mute font-normal">de tu banco</span></div>
        </div>
      </div>

      <p className="mt-3 text-[12.5px] leading-relaxed text-fg-dim">{p.reason}</p>
    </article>
  )
}

function Tile({ label, value, sub, tone }) {
  return (
    <div className="rounded-xl border border-ink-line bg-ink/50 px-4 py-3 min-w-0">
      <div className="text-[11px] text-fg-mute">{label}</div>
      <div className={`num text-[18px] font-semibold mt-0.5 ${tone || 'text-fg'}`}>{value}</div>
      {sub && <div className="text-[11px] text-fg-mute mt-0.5 truncate">{sub}</div>}
    </div>
  )
}

function record(s) {
  if (!s || !s.settled) return '—'
  return `${s.won}-${s.lost}${s.push ? `-${s.push}` : ''}`
}

function Report({ nuri }) {
  const rep = nuri.report
  const w = rep.this_week
  const prev = rep.weeks.filter((x) => !x.current)
  const tone = (u) => (u > 0 ? 'text-ev' : u < 0 ? 'text-live' : 'text-fg')
  return (
    <section className="mt-12">
      <div className="flex items-end justify-between gap-3 mb-4">
        <div>
          <h2 className="text-[18px] font-semibold text-fg">Autoevaluación</h2>
          <p className="text-[12.5px] text-fg-mute mt-0.5">NURI califica solo cada pick con el marcador oficial. 1 unidad = lo que apuestas en un pick.</p>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
        <Tile label="Esta semana" value={record(w)} sub={`${w.picks} pick${w.picks === 1 ? '' : 's'} · ${w.pending} por jugarse`} />
        <Tile label="Unidades" value={w.settled ? units(w.units) : '—'} tone={tone(w.units)} />
        <Tile label="ROI" value={w.roi != null ? signedPct(w.roi, 1) : '—'} tone={tone(w.roi)} />
        <Tile label="Aciertos esperados" value={w.expected != null ? `${w.expected.toFixed(1)}` : '—'} sub={w.settled ? `salieron ${w.won}` : ''} />
        <Tile label="Línea de cierre (CLV)" value={w.clv != null ? `${w.clv > 0 ? '+' : ''}${(w.clv * 100).toFixed(1)} pts` : '—'} tone={w.clv > 0 ? 'text-ev' : w.clv < 0 ? 'text-live' : 'text-fg'} sub="+ = el mercado le dio la razón" />
      </div>

      <p className="mt-4 text-[13px] text-fg-dim leading-relaxed">{rep.verdict}</p>
      {nuri.adjust_note && <p className="mt-1 text-[12px] text-stale">{nuri.adjust_note}</p>}

      {nuri.settled.length > 0 && (
        <div className="mt-5 card divide-y divide-ink-line">
          {nuri.settled.map((p) => {
            const r = RESULT[p.result] || RESULT.void
            return (
              <div key={p.id} className="flex items-center gap-3 px-4 py-3 text-[12.5px]">
                <span className={`w-5 text-center text-[15px] font-semibold ${r.cls}`} title={r.label}>{r.icon}</span>
                <div className="min-w-0 flex-1">
                  <div className="text-fg truncate">{p.selection_label} <span className="text-fg-mute">· {p.market_label}</span></div>
                  <div className="text-fg-mute truncate">{p.home} {p.final_home != null ? `${p.final_home}–${p.final_away}` : 'vs'} {p.away} · {p.league}</div>
                </div>
                <span className="num text-fg-dim shrink-0">{p.book ? `${p.book} ` : ''}{fmtOdds(p.odds)}</span>
                <span className={`num w-[70px] text-right shrink-0 ${tone(p.units)}`}>{units(p.units)}</span>
              </div>
            )
          })}
        </div>
      )}

      {prev.length > 0 && (
        <div className="mt-6">
          <div className="label mb-2">Semanas anteriores</div>
          <div className="card overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-fg-mute text-[11px]">
                  <th className="text-left font-normal px-4 py-2">Semana</th>
                  <th className="text-right font-normal px-3 py-2">Picks</th>
                  <th className="text-right font-normal px-3 py-2">Récord</th>
                  <th className="text-right font-normal px-3 py-2">Unidades</th>
                  <th className="text-right font-normal px-3 py-2">ROI</th>
                  <th className="text-right font-normal px-4 py-2">CLV</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-line">
                {prev.map((x) => (
                  <tr key={x.start}>
                    <td className="px-4 py-2 text-fg">{x.label}</td>
                    <td className="px-3 py-2 text-right num text-fg-dim">{x.picks}</td>
                    <td className="px-3 py-2 text-right num text-fg-dim">{record(x)}</td>
                    <td className={`px-3 py-2 text-right num ${tone(x.units)}`}>{x.settled ? units(x.units) : '—'}</td>
                    <td className={`px-3 py-2 text-right num ${tone(x.roi)}`}>{x.roi != null ? signedPct(x.roi, 0) : '—'}</td>
                    <td className="px-4 py-2 text-right num text-fg-dim">{x.clv != null ? `${x.clv > 0 ? '+' : ''}${(x.clv * 100).toFixed(1)}` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {rep.all_time.settled > 0 && (
        <p className="mt-4 text-[12px] text-fg-mute">
          Desde que empezó: {rep.all_time.picks} picks · {record(rep.all_time)} · {units(rep.all_time.units)} · ROI {rep.all_time.roi != null ? signedPct(rep.all_time.roi, 0) : '—'}
          {rep.all_time.clv != null ? ` · CLV ${(rep.all_time.clv * 100).toFixed(1)} pts` : ''}
        </p>
      )}
    </section>
  )
}

export default function Picks({ snapshot }) {
  const nuri = snapshot?.nuri
  if (!snapshot) {
    return <div className="py-24 text-center text-fg-mute text-[13px]">Conectando con NURI…</div>
  }
  if (snapshot.mode !== 'live' || !nuri) {
    return <div className="py-24 text-center text-fg-mute text-[13px]">NURI está cargando los partidos reales… (unos segundos)</div>
  }
  const scan = snapshot.scan || {}
  const active = nuri.active || []
  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight text-fg">Picks de la semana</h1>
          <p className="mt-1 text-[13px] text-fg-mute">Semana del {nuri.week.label}. Sólo aparecen cuando hay ventaja real; si no hay, no hay.</p>
        </div>
        <div className="text-[12px] text-fg-mute text-right">
          Revisando {scan.upcoming ?? 0} partidos de las próximas 48 h{scan.live ? ` · ${scan.live} en vivo` : ''}
          {nuri.confirming > 0 && <div className="text-stale">{nuri.confirming} posible{nuri.confirming === 1 ? '' : 's'} pick{nuri.confirming === 1 ? '' : 's'} en confirmación</div>}
        </div>
      </div>

      {active.length ? (
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          {active.map((p) => <PickCard key={p.id} p={p} />)}
        </div>
      ) : (
        <div className="mt-6 card px-6 py-10 text-center">
          <div className="text-[16px] text-fg font-medium">Por ahora no hay ningún pick con suficiente valor</div>
          <p className="mt-2 text-[13px] text-fg-mute max-w-[520px] mx-auto leading-relaxed">
            NURI revisa cada partido todo el tiempo y sólo te da un pick cuando el momio paga más de lo que debería
            o cuando el mercado se movió fuerte a favor y las estadísticas lo respaldan. Mejor nada que apostar sin ventaja.
          </p>
        </div>
      )}

      <Report nuri={nuri} />
    </div>
  )
}
