"""
NURI · 누리 — scorers.py
========================
"Anota en el partido" con momios REALES de Bet365 (apostable en México).

¿Cómo sabe NURI cuánto vale de verdad?
  1. Los goles esperados del partido salen de la línea de DraftKings sin comisión (Poisson).
  2. El mercado de "primer goleador" de Bet365 dice qué parte de esos goles le toca a cada jugador
     (se le quita la comisión con el método de potencia, que castiga más a los momios largos).
  3. Con eso sale la probabilidad justa de que anote: 1 − e^(−goles esperados del jugador).
  4. Sólo es pick si el momio de "anota" de Bet365 paga claramente más que lo justo Y se sale del
     patrón del resto de los jugadores del mismo partido (así no confundimos la comisión con valor).
Se califica con la alineación y los goles oficiales de ESPN.
"""
from __future__ import annotations

import math
import re
import unicodedata

REF_BOOK = "DraftKings (ref.)"
OWN_GOALS = 0.03
MIN_EV = 0.06
MIN_REL = 1.15          # tiene que pagar ≥ 15 % más que el patrón del resto del partido
MIN_PLAYERS = 6


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def poisson_over(lam: float, line: float) -> float:
    k = int(math.floor(line))            # más de 2.5 = 3 o más
    term, cdf = math.exp(-lam), 0.0
    for i in range(k + 1):
        if i:
            term *= lam / i
        cdf += term
    return 1.0 - cdf


def lam_from_total(line: float, p_over: float) -> float:
    lo, hi = 0.2, 7.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if poisson_over(mid, line) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def total_lambda(ev: dict, b365: dict | None) -> float | None:
    """Goles esperados: línea de DraftKings sin comisión (o la de Bet365 si no hay)."""
    best = None
    for m in (ev.get("markets") or {}).values():
        if m.get("type") != "total" or m.get("line") is None or abs(m["line"] % 1 - 0.5) > 1e-6:
            continue
        sel = m.get("selections") or {}
        o, u = (sel.get("over") or {}).get(REF_BOOK), (sel.get("under") or {}).get(REF_BOOK)
        if o and u and o > 1 and u > 1:
            po = (1 / o) / (1 / o + 1 / u)
            cand = (abs(po - 0.5), m["line"], po)      # la línea más pareja es la más informativa
            best = min(best, cand) if best else cand
    if best:
        return lam_from_total(best[1], best[2])
    t = (b365 or {}).get("total")
    if t and t.get("over") and t.get("under") and abs(t["line"] % 1 - 0.5) < 1e-6:
        po = (1 / t["over"]) / (1 / t["over"] + 1 / t["under"])
        return lam_from_total(t["line"], po)
    return None


def power_devig(qs: list[float], target: float) -> list[float]:
    lo, hi = 0.5, 6.0
    for _ in range(60):
        k = (lo + hi) / 2
        s = sum(q ** k for q in qs)
        if s > target:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    return [q ** k for q in qs]


def candidates(feed, now: float) -> list[dict]:
    out = []
    for eid, b in (getattr(feed, "b365", None) or {}).items():
        e = (feed.raw or {}).get(eid) or {}
        if e.get("sport") != "football" or e.get("status") != "pre":
            continue
        if not (20 * 60 <= (e.get("start_ts") or 0) - now <= 30 * 3600):
            continue
        anyt, fgs = b.get("scorers") or {}, b.get("fgs") or {}
        common = [k for k in anyt if k in fgs and anyt[k].get("odds") and fgs[k].get("odds")]
        if len(common) < MIN_PLAYERS or len(fgs) < 10:
            continue
        lam = total_lambda(e, b)
        if not lam or not (1.2 <= lam <= 4.8):
            continue
        keys = list(fgs.keys())
        q_raw = [1.0 / fgs[k]["odds"] for k in keys]
        target = (1 - math.exp(-lam)) * (1 - OWN_GOALS)
        if sum(q_raw) <= target:
            continue
        q_fair = dict(zip(keys, power_devig(q_raw, target)))
        rows = []
        for k in common:
            mu = lam * q_fair[k] / (1 - math.exp(-lam))
            p = 1 - math.exp(-mu)
            o = anyt[k]["odds"]
            rows.append((k, p, o, p * o))
        rhos = sorted(r[3] for r in rows)
        med = rhos[len(rhos) // 2]
        if not (0.65 <= med <= 1.02):           # si el patrón del partido es raro, mejor no
            continue
        inj = ((getattr(feed, "dossier", {}) or {}).get(eid) or {}).get("_inj_status") or {}
        inj_n = {norm(n): s for n, s in inj.items()}
        for k, p, o, rho in rows:
            ev_ = rho - 1
            rel = rho / med
            if ev_ < MIN_EV or rel < MIN_REL or not (1.8 <= o <= 5.5) or p < 0.17:
                continue
            name = anyt[k]["name"]
            if inj_n.get(norm(name)) in ("out", "doubtful"):
                continue
            conf = 50 + min(15.0, (ev_ - MIN_EV) * 150) + min(10.0, (rel - MIN_REL) * 60) + (4 if p >= 0.3 else 0)
            out.append({"event_id": eid, "player": name, "key": k, "odds": o, "p": round(p, 4), "ev": round(ev_, 4),
                        "rel": round(rel, 3), "lam": round(lam, 2), "conf_score": round(min(conf, 79.0), 1), "ev_raw": e})
    out.sort(key=lambda c: -c["conf_score"])
    return out


def grade(p: dict, summary: dict) -> tuple[str, float] | None:
    """Ganó si anotó (sin contar autogoles). Si no jugó, se anula."""
    target = norm(p.get("market_label") or "")
    if not target:
        return "void", 0.0
    last = target.split()[-1] if target.split() else ""
    found = None
    for team in summary.get("rosters") or []:
        for pl in team.get("roster") or []:
            n = norm(((pl.get("athlete") or {}).get("displayName")) or "")
            if n == target or (last and n.split() and n.split()[-1] == last and n[:1] == target[:1]):
                found = pl
                break
        if found:
            break
    if not found:
        return ("void", 0.0) if summary.get("rosters") else None   # no estuvo en la convocatoria: se anula
    stats = {s.get("name"): s.get("value") for s in found.get("stats") or []}
    played = (stats.get("appearances") or 0) > 0 or found.get("starter") or found.get("subbedIn")
    if not played:
        return "void", 0.0
    goals = stats.get("totalGoals") or 0          # ESPN no cuenta aquí los autogoles
    o = float(p["odds"])
    return ("won", round(o - 1.0, 4)) if goals >= 1 else ("lost", -1.0)
