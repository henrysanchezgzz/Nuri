import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useToast } from './ui'

const LABEL = { Caliente: 'Caliente', 'Codere MX': 'Codere', Bet365: 'Bet365', Stake: 'Stake', Betway: 'Betway', Novibet: 'Novibet', 'Betano MX': 'Betano', 'Winpot MX': 'Winpot', Strendus: 'Strendus', Playdoit: 'Playdoit', 'RushBet MX': 'RushBet' }

/** Ventana para conectar los momios reales de Caliente y Codere (odds-api.io). */
export default function OddsSettings({ open, onClose, state }) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [choices, setChoices] = useState([])
  const [picked, setPicked] = useState(['Caliente', 'Codere MX'])
  const toast = useToast()
  const configured = state?.configured
  const books = state?.books || []
  const needBooks = configured && !books.length

  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    api.settings().then((s) => { setChoices(s.choices || []); if (s.default_books) setPicked(s.default_books) }).catch(() => {})
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const save = async () => {
    setBusy(true); setMsg('')
    try {
      const r = await api.setOddsKey(key.trim())
      setKey('')
      setMsg(r.books?.length ? `Listo: ${r.books.join(', ')}. Los momios aparecen en 1–2 minutos.` : 'Clave válida. Ahora elige tus 2 casas abajo.')
      toast('Clave guardada en esta computadora')
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }
  const saveBooks = async () => {
    setBusy(true); setMsg('')
    try {
      const r = await api.setOddsBooks(picked)
      setMsg(`Casas activas: ${r.books.join(', ')}`)
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }
  const remove = async () => {
    if (!window.confirm('¿Quitar la clave? NURI vuelve a sólo Bet365 + datos de ESPN.')) return
    await api.removeOddsKey().catch(() => {})
    setMsg('Clave quitada.')
  }
  const toggle = (b) => setPicked((xs) => (xs.includes(b) ? xs.filter((x) => x !== b) : [...xs, b]))

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-[520px] max-h-[92vh] overflow-y-auto card p-6 fade-up">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-[17px] font-semibold text-fg">Momios reales de tus casas</h2>
            <p className="mt-1 text-[12.5px] text-fg-mute">Así NURI compara cada pick contra el momio que de verdad paga tu casa.</p>
          </div>
          <button onClick={onClose} className="w-8 h-8 -mr-2 rounded-full grid place-items-center hover:bg-ink-raised text-fg-dim" aria-label="Cerrar">
            <svg width="12" height="12" viewBox="0 0 12 12"><path d="M1 1l10 10M11 1 1 11" stroke="currentColor" strokeWidth="1.5" /></svg>
          </button>
        </div>

        <div className="mt-5 space-y-3 text-[13px]">
          <div className="rounded-xl border border-ev/25 bg-ev-soft px-4 py-3">
            <div className="text-fg font-medium">Bet365 · ya activo, gratis</div>
            <div className="text-fg-dim text-[12.5px] mt-0.5">Momios reales de fútbol (1X2, total de goles y goleador) en Premier, Serie A, Bundesliga, Ligue 1, MLS, Champions y más. Liga MX no viene en esta fuente.</div>
          </div>

          <div className={`rounded-xl border px-4 py-3 ${configured && books.length ? 'border-ev/25 bg-ev-soft' : 'border-ink-line bg-ink/60'}`}>
            <div className="text-fg font-medium">Caliente y Codere · {configured && books.length ? 'conectado' : 'necesita tu clave'}</div>
            <div className="text-fg-dim text-[12.5px] mt-0.5 leading-relaxed">
              Caliente y Codere no tienen una API pública. El único servicio autorizado que hoy los publica es
              {' '}<span className="text-fg">odds-api.io</span>: plan Solo, unos <span className="text-fg">US$65 al mes</span> por 2 casas
              (pre-partido y en vivo, todos los deportes). No tiene versión gratis para cuentas nuevas.
              {' '}Caliente ahí no incluye props de jugadores.
            </div>
            {configured && (
              <div className="mt-2 text-[12px] text-fg-mute">Estado: {state?.detail || '—'}{state?.remaining != null ? ` · ${state.remaining} consultas libres esta hora` : ''}</div>
            )}
          </div>
        </div>

        <ol className="mt-5 space-y-2 text-[12.5px] text-fg-dim list-decimal pl-5">
          <li>Entra a <a className="text-info underline underline-offset-4" href="https://odds-api.io/pricing" target="_blank" rel="noreferrer">odds-api.io/pricing</a>, elige <span className="text-fg">Solo</span> y paga tú (NURI no toca tu tarjeta).</li>
          <li>Te llega la clave por correo. Cópiala.</li>
          <li>Pégala aquí. NURI la guarda sólo en esta computadora.</li>
        </ol>

        <div className="mt-4 flex gap-2">
          <input value={key} onChange={(e) => setKey(e.target.value)} placeholder={configured ? 'Pegar una clave nueva' : 'Pega aquí tu clave de odds-api.io'} className="field flex-1 min-w-0 num" autoComplete="off" spellCheck={false} />
          <button onClick={save} disabled={busy || key.trim().length < 8} className="btn-primary h-10 px-4">{busy ? 'Probando…' : 'Guardar'}</button>
        </div>

        {(needBooks || configured) && choices.length > 0 && (
          <div className="mt-5">
            <div className="label mb-2">{needBooks ? 'Elige tus casas (tu plan incluye 2)' : 'Casas del plan'}</div>
            <div className="flex flex-wrap gap-2">
              {choices.map((b) => (
                <button key={b} onClick={() => toggle(b)} className={`pill ${picked.includes(b) ? 'bg-fg text-ink border-fg' : 'text-fg-dim border-ink-line hover:text-fg'}`}>{LABEL[b] || b}</button>
              ))}
            </div>
            <div className="mt-2 flex items-center gap-3">
              <button onClick={saveBooks} disabled={busy || !picked.length} className="btn-ghost h-9">Activar estas casas</button>
              <span className="text-[11px] text-fg-mute">odds-api.io sólo deja cambiar la selección cada 12 h.</span>
            </div>
          </div>
        )}

        {msg && <div className="mt-4 text-[12.5px] text-fg">{msg}</div>}

        <div className="mt-6 pt-4 border-t border-ink-line flex items-center justify-between text-[11.5px] text-fg-mute">
          <span>Sin clave, NURI sigue con Bet365 + líneas públicas + estadísticas de ESPN.</span>
          {configured && <button onClick={remove} className="hover:text-live">Quitar clave</button>}
        </div>
      </div>
    </div>
  )
}
