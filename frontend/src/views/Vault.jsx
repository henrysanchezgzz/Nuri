import { useCallback, useEffect, useState } from 'react'
import BankrollChart from '../components/BankrollChart'
import { useToast } from '../components/ui'
import { api } from '../lib/api'
import { dateTime, mxn, mxn2, odds as fmtOdds, signedPct } from '../lib/format'

function Stat({ label, value, sub, tone }) {
  return (
    <div className="card p-5">
      <div className="label">{label}</div>
      <div className={`num mt-2 text-[26px] font-semibold tracking-tight ${tone === 'pos' ? 'text-ev' : tone === 'neg' ? 'text-live' : 'text-fg'}`}>{value}</div>
      {sub && <div className="mt-1 text-[12px] text-fg-mute num">{sub}</div>}
    </div>
  )
}

const RESULT = {
  won: { label: 'Ganada', cls: 'text-ev bg-ev-soft border-ev/30' },
  lost: { label: 'Perdida', cls: 'text-live bg-live-soft border-live/30' },
  push: { label: 'Push', cls: 'text-fg-dim bg-ink-raised border-ink-soft' },
}

export default function Vault({ snapshot }) {
  const [data, setData] = useState(null)
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const vaultKey = JSON.stringify(snapshot?.vault ?? {})

  const load = useCallback(async () => {
    try {
      const d = await api.vault()
      setData(d)
      setAmount((a) => (a === '' ? String(Math.round(d.state.initial)) : a))
    } catch (e) {
      toast(e.message, 'error')
    }
  }, [toast])

  useEffect(() => { load() }, [load, vaultKey])

  const saveBankroll = async () => {
    const v = Number(String(amount).replace(/[^0-9.]/g, ''))
    if (!(v > 0)) return toast('Escribe un monto válido en MXN', 'error')
    setBusy(true)
    try {
      setData(await api.setBankroll(v))
      toast(`Bankroll inicial fijado en ${mxn(v)}`)
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const settle = async (id, result) => {
    try {
      setData(await api.settle(id, result))
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const remove = async (id) => {
    try {
      setData(await api.remove(id))
      toast('Apuesta eliminada')
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const s = data?.state ?? snapshot?.vault
  const bets = data?.bets ?? []

  return (
    <section className="space-y-6">
      <div className="card p-5 flex flex-col sm:flex-row sm:items-center gap-4 justify-between">
        <div>
          <div className="text-[15px] text-fg font-medium">Bankroll inicial</div>
          <div className="text-[12px] text-fg-mute mt-0.5">Los montos Kelly (¼, tope 6 %) se recalculan al instante con tu saldo real.</div>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-fg-mute text-sm">$</span>
            <input
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && saveBankroll()}
              className="field num w-36 pl-6"
            />
          </div>
          <span className="text-fg-mute text-sm">MXN</span>
          <button onClick={saveBankroll} disabled={busy} className="btn-primary">Guardar</button>
        </div>
      </div>

      {s && (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
          <Stat label="Bankroll total" value={mxn2(s.balance)} sub={`Inicial ${mxn(s.initial)}`} />
          <Stat label="Dinero en juego" value={mxn2(s.in_play)} sub={`${s.pending} apuesta${s.pending === 1 ? '' : 's'} abierta${s.pending === 1 ? '' : 's'}`} />
          <Stat label="Dinero libre" value={mxn2(s.available)} sub="Disponible para nuevos picks" />
          <Stat
            label="Ganancia neta"
            value={`${s.profit >= 0 ? '+' : ''}${mxn2(s.profit)}`}
            sub={`ROI ${signedPct(s.roi)} · ${s.won}G ${s.lost}P ${s.push}N`}
            tone={s.profit > 0 ? 'pos' : s.profit < 0 ? 'neg' : undefined}
          />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
        <div className="card p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="label">Crecimiento de banca</div>
            {s && <div className="num text-[12px] text-fg-dim">{mxn2(s.balance)}</div>}
          </div>
          <BankrollChart history={data?.history} initial={s?.initial ?? 500} />
        </div>
        <div className="card p-5 flex flex-col justify-between">
          <div>
            <div className="label">CLV promedio</div>
            <div className={`num mt-2 text-[30px] font-semibold ${s?.clv_avg > 0 ? 'text-ev' : s?.clv_avg < 0 ? 'text-live' : 'text-fg'}`}>
              {s?.clv_avg == null ? '—' : signedPct(s.clv_avg, 2)}
            </div>
            <div className="mt-1 text-[12px] text-fg-mute">{s?.clv_count ?? 0} apuestas auditadas</div>
          </div>
          <p className="mt-4 text-[12px] leading-relaxed text-fg-mute">
            Compara tu cuota contra la línea justa de cierre (línea de mercado sin margen). Un CLV positivo sostenido es la mejor señal de que la ventaja es real, aunque una racha salga mal.
          </p>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-ink-line flex items-center justify-between">
          <div className="label">Historial</div>
          <div className="text-[12px] text-fg-mute">{bets.length} registros</div>
        </div>
        {bets.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-fg-mute">Todavía no tomas apuestas. Usa “Tomar apuesta” en el Radar.</div>
        ) : (
          <div className="divide-y divide-ink-line">
            {bets.map((b) => (
              <div key={b.id} className="px-5 py-3.5 grid gap-3 md:grid-cols-[1fr_auto] items-center">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-[11px] text-fg-mute">
                    <span className="num">{dateTime(b.created_at)}</span>
                    <span>·</span>
                    <span className="truncate">{b.league}</span>
                    {b.mode === 'demo' && <span className="rounded px-1 border border-ink-line">demo</span>}
                  </div>
                  <div className="mt-1 text-[14px] text-fg truncate">
                    {b.selection_label} <span className="text-fg-mute">· {b.market_label}</span>
                  </div>
                  <div className="mt-0.5 text-[12px] text-fg-dim truncate">
                    {b.event_name} · {b.book} @ <span className="num">{fmtOdds(b.odds)}</span> · <span className="num">{mxn(b.stake)}</span>
                    <span className="text-fg-mute"> · EV {signedPct(b.ev)}</span>
                    {b.clv != null && <span className={b.clv >= 0 ? 'text-ev' : 'text-live'}> · CLV {signedPct(b.clv, 2)}</span>}
                  </div>
                </div>
                <div className="flex items-center gap-2 md:justify-end">
                  {b.result === 'pending' ? (
                    <>
                      <button onClick={() => settle(b.id, 'won')} className="pill border-ink-line text-fg-dim hover:text-ev hover:border-ev/40">Ganada</button>
                      <button onClick={() => settle(b.id, 'lost')} className="pill border-ink-line text-fg-dim hover:text-live hover:border-live/40">Perdida</button>
                      <button onClick={() => settle(b.id, 'push')} className="pill border-ink-line text-fg-dim hover:text-fg hover:border-ink-soft">Push</button>
                    </>
                  ) : (
                    <>
                      <span className={`pill cursor-default ${RESULT[b.result].cls}`}>
                        {RESULT[b.result].label}
                        <span className="num">{b.profit >= 0 ? '+' : ''}{mxn2(b.profit)}</span>
                      </span>
                      <button onClick={() => settle(b.id, 'pending')} className="text-[11px] text-fg-mute hover:text-fg-dim px-1">Deshacer</button>
                    </>
                  )}
                  <button onClick={() => remove(b.id)} className="w-8 h-8 rounded-full grid place-items-center text-fg-mute hover:text-live hover:bg-ink-raised" aria-label="Eliminar" title="Eliminar">
                    <svg width="11" height="11" viewBox="0 0 12 12"><path d="M1 1l10 10M11 1 1 11" stroke="currentColor" strokeWidth="1.5" /></svg>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
