import Logo from './Logo'

const TABS = [
  { key: 'radar', label: 'Radar' },
  { key: 'live', label: 'En Vivo' },
  { key: 'vault', label: 'Mi Bóveda' },
]

export default function TopBar({ view, onChange, connected, mode, tickSeconds, lastTick, liveCount }) {
  return (
    <header className="sticky top-0 z-30 bg-ink/85 backdrop-blur-md border-b border-ink-line">
      <div className="max-w-[1240px] mx-auto px-4 sm:px-5 h-16 flex items-center justify-between gap-3 sm:gap-6">
        <div className="hidden md:block"><Logo /></div>
        <div className="md:hidden flex items-center gap-2 shrink-0">
          <Logo compact />
          <span className={`min-[420px]:hidden w-2 h-2 rounded-full ${connected ? 'bg-ev live-dot' : 'bg-live'}`} title={connected ? 'LIVE' : 'Sin señal'} />
        </div>

        <nav className="flex items-center gap-0.5 sm:gap-1 rounded-full border border-ink-line p-1 bg-ink-card/60 min-w-0">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => onChange(t.key)}
              className={`h-8 px-3 sm:px-4 rounded-full text-[13px] font-medium whitespace-nowrap transition-colors inline-flex items-center gap-2 ${
                view === t.key ? 'bg-fg text-ink' : 'text-fg-dim hover:text-fg'
              }`}
            >
              {t.label}
              {t.key === 'live' && liveCount > 0 && (
                <span className={`num text-[11px] ${view === t.key ? 'text-live' : 'text-live/80'}`}>{liveCount}</span>
              )}
            </button>
          ))}
        </nav>

        <div className="hidden min-[420px]:flex items-center gap-2.5 shrink-0">
          {mode === 'demo' && (
            <span className="hidden sm:inline-flex items-center gap-1.5 rounded-full border border-ink-line px-2.5 h-7 text-[11px] text-fg-dim" title="Cuotas y partidos simulados. Agrega ODDS_API_KEY en .env para datos reales.">
              <span className="w-1.5 h-1.5 rounded-full bg-stale" /> DEMO · simulado
            </span>
          )}
          <span
            className={`relative overflow-hidden inline-flex items-center gap-1.5 rounded-full border px-2.5 h-7 text-[11px] font-medium num ${
              connected ? 'border-ev/30 text-ev' : 'border-live/30 text-live'
            }`}
            title={connected ? 'Conectado por WebSocket' : 'Reconectando…'}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-ev live-dot' : 'bg-live'}`} />
            <span className="whitespace-nowrap">{connected ? `LIVE · ${tickSeconds || 5}s` : 'SIN SEÑAL'}</span>
            {connected && lastTick && (
              <span
                key={lastTick}
                className="tick-bar absolute bottom-0 left-0 h-px w-full bg-ev/50"
                style={{ animationDuration: `${(tickSeconds || 5) * 1000}ms` }}
              />
            )}
          </span>
        </div>
      </div>
    </header>
  )
}
