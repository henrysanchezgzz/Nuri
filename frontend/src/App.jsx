import { useCallback, useEffect, useState } from 'react'
import OddsSettings from './components/OddsSettings'
import { ToastProvider, useToast } from './components/ui'
import { api } from './lib/api'
import { useLiveSocket } from './hooks/useLiveSocket'
import { ago } from './lib/format'
import Picks from './views/Picks'

function PhoneButton({ notify }) {
  const toast = useToast()
  if (!notify?.enabled) return null
  const test = async () => {
    try {
      await api.notifyTest()
      toast('Listo: revisa tu cel (app ntfy)')
    } catch (e) { toast(e.message, 'error') }
  }
  return (
    <button onClick={test} title={`Tema de ntfy: ${notify.topic}`} className="inline-flex items-center gap-1.5 hover:text-fg">
      <span className={`w-1.5 h-1.5 rounded-full ${notify.error ? 'bg-stale' : 'bg-ev'}`} />
      Picks al cel: activos · probar
    </button>
  )
}

function PowerButton() {
  const [off, setOff] = useState(false)
  const shutdown = async () => {
    if (!window.confirm('¿Apagar NURI? Tus picks y su evaluación quedan guardados.')) return
    try { await fetch('/api/shutdown', { method: 'POST' }) } catch { /* ya apagado */ }
    setOff(true)
    setTimeout(() => window.close(), 600)
  }
  if (off) return <span className="text-fg-dim">NURI apagado · ya puedes cerrar esta ventana</span>
  return (
    <button onClick={shutdown} className="inline-flex items-center gap-1.5 text-fg-mute hover:text-live transition-colors">
      <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1v4.5M3.2 3a4 4 0 1 0 5.6 0" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
      Apagar NURI
    </button>
  )
}

export default function App() {
  const { snapshot, connected } = useLiveSocket()
  const [settings, setSettings] = useState(false)
  const close = useCallback(() => setSettings(false), [])
  const n = snapshot?.nuri?.active?.length || 0
  const io = snapshot?.oddsio || {}
  const ioOk = io.configured && io.ok && (io.books || []).length > 0

  useEffect(() => {
    document.title = n ? `(${n}) NURI · picks de valor` : 'NURI · 누리'
  }, [n])

  return (
    <ToastProvider>
      <div className="min-h-screen">
        <header className="border-b border-ink-line">
          <div className="max-w-[1080px] mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <img src="/nuri.svg" alt="" className="w-8 h-8 rounded-lg" />
              <div className="leading-tight">
                <div className="text-[14px] font-semibold tracking-[0.2em] text-fg">NURI</div>
                <div className="text-[10px] tracking-[0.18em] text-fg-mute">누리 · PICKS DE VALOR</div>
              </div>
            </div>
            <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] num ${connected ? 'border-ev/30 text-ev' : 'border-ink-line text-fg-mute'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-ev' : 'bg-fg-mute'}`} />
              {connected ? 'EN LÍNEA' : 'CONECTANDO'}
            </span>
          </div>
        </header>

        <main className="max-w-[1080px] mx-auto px-4 sm:px-6 py-8">
          <Picks snapshot={snapshot} />
        </main>

        <footer className="max-w-[1080px] mx-auto px-4 sm:px-6 pb-10 pt-4 border-t border-ink-line/60">
          <div className="flex flex-wrap items-center justify-between gap-3 text-[11px] text-fg-mute">
            <span>
              Momios: Bet365 (vía ESPN){ioOk ? ` + ${io.books.map((b) => (b === 'Caliente.mx' ? 'Caliente' : b)).join(' + ')}` : ''} · línea de mercado y estadísticas: ESPN · actualizado {ago(snapshot?.ts)}
            </span>
            <div className="flex items-center gap-4">
              <PhoneButton notify={snapshot?.notify} />
              <button onClick={() => setSettings(true)} className="hover:text-fg underline underline-offset-4 decoration-ink-soft">
                {ioOk ? 'Momios de Caliente/Codere: conectados' : 'Conectar Caliente y Codere'}
              </button>
              <PowerButton />
            </div>
          </div>
          <p className="mt-3 text-[11px] text-fg-mute/80">NURI calcula probabilidades, no garantiza resultados. Revisa el momio en tu casa antes de apostar y apuesta sólo lo que puedas perder · +18</p>
        </footer>
        <OddsSettings open={settings} onClose={close} state={io} />
      </div>
    </ToastProvider>
  )
}
