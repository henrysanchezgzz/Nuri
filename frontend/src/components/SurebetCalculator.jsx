import { useMemo, useState } from 'react'
import { mxn, mxn2, pct } from '../lib/format'

/** Calculadora de surebet: escribe el mejor momio de cada resultado (de casas distintas). */
export default function SurebetCalculator({ books }) {
  const [ways, setWays] = useState(2)
  const [total, setTotal] = useState(100)
  const [legs, setLegs] = useState([
    { name: 'Resultado 1', book: books[0] || 'Caliente.mx', odds: '' },
    { name: 'Resultado 2', book: books[1] || 'Codere', odds: '' },
    { name: 'Empate', book: books[2] || 'Stake', odds: '' },
  ])

  const active = legs.slice(0, ways)
  const calc = useMemo(() => {
    const os = active.map((l) => parseFloat(String(l.odds).replace(',', '.')))
    if (os.some((o) => !(o > 1))) return null
    const inv = os.reduce((a, o) => a + 1 / o, 0)
    const stakes = os.map((o) => Math.round((total * (1 / o)) / inv))
    const payout = Math.min(...stakes.map((s, i) => s * os[i]))
    const spent = stakes.reduce((a, b) => a + b, 0)
    return { inv, stakes, payout, profit: payout - spent, ok: inv < 1 }
  }, [active, total])

  const set = (i, k, v) => setLegs((xs) => xs.map((x, j) => (j === i ? { ...x, [k]: v } : x)))

  return (
    <div className="card p-5 max-w-xl">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[15px] text-fg font-medium">Calculadora de surebet</div>
          <div className="text-[12px] text-fg-mute mt-0.5">Escribe el mejor momio de cada resultado, cada uno en una casa distinta.</div>
        </div>
        <div className="flex rounded-full border border-ink-line p-0.5">
          {[2, 3].map((n) => (
            <button key={n} onClick={() => setWays(n)} className={`h-7 px-3 rounded-full text-[12px] ${ways === n ? 'bg-fg text-ink' : 'text-fg-dim'}`}>{n} vías</button>
          ))}
        </div>
      </div>
      <div className="mt-4 space-y-2">
        {active.map((l, i) => (
          <div key={i} className="flex gap-2">
            <input value={l.name} onChange={(e) => set(i, 'name', e.target.value)} className="field flex-1 min-w-0 text-[13px]" />
            <select value={l.book} onChange={(e) => set(i, 'book', e.target.value)} className="field w-[118px] text-[13px]">
              {books.map((b) => <option key={b}>{b}</option>)}
            </select>
            <input inputMode="decimal" placeholder="2.10" value={l.odds} onChange={(e) => set(i, 'odds', e.target.value)} className="field num w-20" />
          </div>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <span className="label">Total a repartir</span>
        <input type="number" min="10" value={total} onChange={(e) => setTotal(Math.max(0, Number(e.target.value) || 0))} className="field num w-28" />
        <span className="text-fg-mute text-sm">MXN</span>
      </div>
      {calc && (
        <div className={`mt-4 rounded-xl border px-4 py-3 ${calc.ok ? 'border-ev/30 bg-ev-soft' : 'border-ink-line bg-ink/60'}`}>
          {calc.ok ? (
            <>
              <div className="text-[13px] text-ev font-medium">Surebet real: ganas {pct(1 / calc.inv - 1, 2)} pase lo que pase</div>
              <div className="mt-2 space-y-1 text-[13px]">
                {active.map((l, i) => (
                  <div key={i} className="flex justify-between text-fg-dim"><span>{l.name} · {l.book}</span><span className="num text-fg">{mxn(calc.stakes[i])}</span></div>
                ))}
              </div>
              <div className="mt-2 text-[12px] text-fg-mute">Cobras mínimo <span className="num text-fg-dim">{mxn2(calc.payout)}</span> · ganancia <span className="num text-ev">{mxn2(calc.profit)}</span></div>
            </>
          ) : (
            <div className="text-[13px] text-fg-dim">No hay surebet: Σ 1/momio = <span className="num">{calc.inv.toFixed(3)}</span> (necesita ser menor a 1.000).</div>
          )}
        </div>
      )}
    </div>
  )
}
