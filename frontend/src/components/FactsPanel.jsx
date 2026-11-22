import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { ago } from '../lib/format'
import { TeamLogo } from './ui'

/* Datos reales del partido (ESPN): forma, tabla, promedios, bajas, líderes, cara a cara. */

const cache = new Map() // id → { ts, data }

const RES = {
  G: { cls: 'bg-ev text-ink', title: 'Ganó' },
  E: { cls: 'bg-fg-mute/70 text-ink', title: 'Empató' },
  P: { cls: 'bg-live/85 text-white', title: 'Perdió' },
  '?': { cls: 'bg-ink-soft text-fg-dim', title: '' },
}

const INJ = {
  out: 'text-live',
  suspended: 'text-live',
  doubtful: 'text-stale',
  questionable: 'text-stale',
  probable: 'text-fg-dim',
  active: 'text-fg-dim',
}

export function FormChips({ form, size = 'md' }) {
  if (!form?.length) return <span className="text-[11px] text-fg-mute">—</span>
  const box = size === 'sm' ? 'w-4 h-4 text-[9px]' : 'w-5 h-5 text-[10px]'
  return (
    <span className="inline-flex gap-1">
      {form.map((g, i) => {
        const r = typeof g === 'string' ? g : g.r
        const meta = RES[r] || RES['?']
        const tip = typeof g === 'string' ? meta.title : `${meta.title} ${g.own ?? '?'}-${g.opp ?? '?'} ${g.home ? 'vs' : 'en'} ${g.vs_name || g.vs} · ${g.date}`
        return (
          <span key={i} title={tip} className={`${box} rounded-[5px] grid place-items-center font-semibold ${meta.cls}`}>{r}</span>
        )
      })}
    </span>
  )
}

/** Hasta 3 datos clave en una línea cada uno (vienen ya con el partido). */
export function FactBullets({ facts, max = 3 }) {
  const list = facts?.bullets?.slice(0, max) || []
  if (!list.length) return null
  return (
    <ul className="mt-3 space-y-1">
      {list.map((b, i) => (
        <li key={i} className="flex gap-2 text-[12px] leading-snug text-fg-dim">
          <span className="mt-[6px] w-1 h-1 rounded-full bg-info shrink-0" />
          <span>{b}</span>
        </li>
      ))}
    </ul>
  )
}

function standing(t, sport, side) {
  const parts = []
  if (t.rank) parts.push(sport === 'football' ? `${t.rank}.º de ${t.of || '?'}` : `${t.rank}.º ${t.group ? `en ${t.group}` : ''}`.trim())
  if (t.points != null) parts.push(`${t.points} pts`)
  if (t.record) parts.push(sport === 'football' ? `${t.record} (G-E-P)` : t.record)
  if (t.split) parts.push(`${t.split} ${side === 'home' ? 'en casa' : 'de visita'}`)
  return parts
}

function Section({ title, children }) {
  return (
    <section>
      <div className="label mb-2">{title}</div>
      {children}
    </section>
  )
}

export default function FactsPanel({ eventId, ev, sport }) {
  const [data, setData] = useState(() => cache.get(eventId)?.data || null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    const hit = cache.get(eventId)
    if (hit && Date.now() - hit.ts < 4 * 60 * 1000) {
      setData(hit.data)
      return undefined
    }
    api.facts(eventId)
      .then((d) => {
        cache.set(eventId, { ts: Date.now(), data: d })
        if (alive) { setData(d); setErr('') }
      })
      .catch((e) => alive && setErr(e.message))
    return () => { alive = false }
  }, [eventId])

  if (err && !data) return <div className="text-[12px] text-fg-mute">{err}</div>
  if (!data) return <div className="text-[12px] text-fg-mute">Cargando datos reales…</div>

  const sp = sport || data.sport
  const teams = [['home', data.home], ['away', data.away]]
  const ok = (x) => x !== undefined && x !== null && x !== ''

  return (
    <div className="space-y-5 text-[12.5px]">
      <Section title="Cómo llegan (últimos 5)">
        <div className="space-y-2.5">
          {teams.map(([side, t]) => (
            <div key={side} className="flex items-center gap-2.5 min-w-0">
              <TeamLogo team={{ name: t.name, short: t.short, logo: t.logo || ev?.[side]?.logo }} size={20} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-fg">{t.name}</div>
                <div className="text-[11px] text-fg-mute truncate">{standing(t, sp, side).join(' · ') || '—'}</div>
              </div>
              <FormChips form={t.form} />
            </div>
          ))}
        </div>
        {teams.some(([, t]) => t.last10 || t.streak) && (
          <div className="mt-2 text-[11px] text-fg-mute">
            {teams.filter(([, t]) => t.last10 || t.streak).map(([s, t]) => `${t.short || t.name}: ${t.last10 ? `${t.last10} en sus últimos 10` : ''}${t.streak ? ` · racha ${t.streak.replace('W', 'G').replace('L', 'P')}` : ''}`).join('   ·   ')}
          </div>
        )}
      </Section>

      {data.season?.length > 0 && (
        <Section title="Promedios de la temporada">
          <div className="rounded-xl border border-ink-line overflow-hidden">
            <div className="grid grid-cols-[1fr_auto_1fr] px-3 py-1.5 text-[11px] text-fg-mute bg-ink/60">
              <span className="truncate">{data.home.short || data.home.name}</span><span />
              <span className="truncate text-right">{data.away.short || data.away.name}</span>
            </div>
            {data.season.map((r) => (
              <div key={r.label} className="grid grid-cols-[1fr_auto_1fr] items-center px-3 py-1.5 border-t border-ink-line/70">
                <span className={`num ${r.better === 'home' ? 'text-ev font-semibold' : 'text-fg'}`}>{r.home}</span>
                <span className="text-[11px] text-fg-mute text-center px-2">{r.label}</span>
                <span className={`num text-right ${r.better === 'away' ? 'text-ev font-semibold' : 'text-fg'}`}>{r.away}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {data.predictor && (
        <Section title="Predictor de ESPN">
          <div className="flex items-center gap-3">
            <span className="num text-fg w-12">{Math.round(data.predictor.home)}%</span>
            <div className="flex-1 flex h-1.5 rounded-full overflow-hidden bg-ink-line">
              <div className="bg-fg/80" style={{ width: `${data.predictor.home}%` }} />
              <div className="bg-info/80" style={{ width: `${data.predictor.away}%` }} />
            </div>
            <span className="num text-fg w-12 text-right">{Math.round(data.predictor.away)}%</span>
          </div>
          <div className="mt-1 text-[11px] text-fg-mute">Modelo propio de ESPN; NURI lo muestra como segunda opinión.</div>
        </Section>
      )}

      {teams.some(([, t]) => t.injuries?.length) && (
        <Section title="Bajas y dudas">
          <div className="grid gap-3 sm:grid-cols-2">
            {teams.map(([side, t]) => (
              <div key={side}>
                <div className="text-[11px] text-fg-mute mb-1">{t.name}</div>
                {t.injuries?.length ? (
                  <ul className="space-y-0.5">
                    {t.injuries.slice(0, 6).map((i) => (
                      <li key={i.name} className="flex gap-1.5">
                        <span className={`shrink-0 ${INJ[i.code] || 'text-fg-dim'}`}>{i.status}</span>
                        <span className="text-fg truncate">{i.name}{i.pos ? <span className="text-fg-mute"> · {i.pos}</span> : null}</span>
                        {i.part && <span className="text-fg-mute truncate">({i.part})</span>}
                      </li>
                    ))}
                  </ul>
                ) : <div className="text-fg-mute">Sin bajas reportadas</div>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {teams.some(([, t]) => t.leaders?.length) && (
        <Section title="Líderes de cada equipo">
          <div className="grid gap-3 sm:grid-cols-2">
            {teams.map(([side, t]) => (
              <ul key={side} className="space-y-0.5">
                <li className="text-[11px] text-fg-mute">{t.name}</li>
                {(t.leaders || []).map((l) => (
                  <li key={l.cat} className="min-w-0 leading-snug pb-0.5">
                    <div className="truncate"><span className="text-fg-mute">{l.cat} · </span><span className="text-fg">{l.name}</span></div>
                    <div className="num text-fg-dim text-[11.5px]">{l.val}</div>
                  </li>
                ))}
              </ul>
            ))}
          </div>
        </Section>
      )}

      {teams.some(([, t]) => t.goalie) && (
        <Section title="Porteros">
          {teams.filter(([, t]) => t.goalie).map(([side, t]) => (
            <div key={side} className="flex gap-2"><span className="text-fg">{t.goalie.name}</span><span className="text-fg-mute">· {t.short} · {ok(t.goalie.gaa) ? `${t.goalie.gaa} goles en contra` : ''}{ok(t.goalie.sv) ? ` · ${t.goalie.sv} efectividad` : ''}</span></div>
          ))}
        </Section>
      )}

      {data.h2h && (
        <Section title={data.h2h.title}>
          <div className="text-fg mb-1">{data.h2h.summary}</div>
          <ul className="space-y-0.5 text-[11.5px] text-fg-dim">
            {data.h2h.games.map((g, i) => (
              <li key={i} className="num">{g.date} · {g.local} {g.score} {g.visita}</li>
            ))}
          </ul>
        </Section>
      )}

      {teams.some(([, t]) => t.lineup?.length) && (
        <Section title="Alineaciones confirmadas">
          <div className="grid gap-3 sm:grid-cols-2 text-[11.5px]">
            {teams.map(([side, t]) => (
              <div key={side}>
                <div className="text-fg-mute mb-0.5">{t.name}</div>
                <div className="text-fg-dim leading-relaxed">{(t.lineup || []).join(', ') || '—'}</div>
              </div>
            ))}
          </div>
        </Section>
      )}

      <div className="text-[10.5px] text-fg-mute">Fuente: ESPN · actualizado {ago(data.updated)}</div>
    </div>
  )
}
