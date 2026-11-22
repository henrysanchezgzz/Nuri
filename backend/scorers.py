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


CROSS_MIN = 1.15        # o que Bet365 pague ≥ 15 % más que DraftKings por el mismo jugador


def dk_prob(o: float) -> float:
    """Probabilidad aproximada sin comisión de un momio de 'anota' de DraftKings. La comisión por jugador
    crece en los momios largos (6 % en los favoritos, hasta 20 % en los muy largos)."""
    m = min(0.20, 0.06 + 0.025 * max(0.0, o - 1.5))
    return 1.0 / (o * (1 + m))


def dk_names(feed, eid: str) -> dict:
    """{nombre normalizado: {"name", "odds"}} con el 'anota' de DraftKings del partido."""
    src = getattr(feed, "props_src", None)
    dk = (getattr(src, "scorers", None) or {}).get(eid) or {}
    from espn_facts import norm_person
    out = {}
    for aid, d in dk.items():
        nm = src.name_of(aid) if src else None
        if nm and d.get("any"):
            out[norm_person(nm)] = {"name": nm, "odds": d["any"]}
    return out


def _match(dkn: dict, name: str):
    from espn_facts import find_scorer_odds
    try:
        return find_scorer_odds(dkn, name)
    except Exception:
        return None


def fgs_probs(b: dict, lam: float | None) -> dict:
    """Probabilidad de anotar según el 'primer goleador' de Bet365 (método de potencia)."""
    fgs = b.get("fgs") or {}
    if not lam or len(fgs) < 10:
        return {}
    keys = list(fgs.keys())
    q_raw = [1.0 / fgs[k]["odds"] for k in keys]
    target = (1 - math.exp(-lam)) * (1 - OWN_GOALS)
    if sum(q_raw) <= target:
        return {}
    q_fair = dict(zip(keys, power_devig(q_raw, target)))
    return {k: 1 - math.exp(-(lam * q / (1 - math.exp(-lam)))) for k, q in q_fair.items()}


def consistent(b: dict) -> bool:
    """¿El 'anota' y el 'primer goleador' de Bet365 que publica ESPN cuadran entre sí? En un mercado sano el
    primer goleador paga entre ~1.8 y ~4.5 veces lo que paga 'anota' para casi todos. Si no, los datos vienen
    cruzados o viejos y no se usan (más vale no dar un pick que darlo con datos malos)."""
    anyt, fgs = b.get("scorers") or {}, b.get("fgs") or {}
    rs = [fgs[k]["odds"] / anyt[k]["odds"] for k in anyt if k in fgs and anyt[k].get("odds") and fgs[k].get("odds")]
    if len(rs) < MIN_PLAYERS:
        return False
    ok = sum(1 for r in rs if 1.8 <= r <= 4.5)
    return ok / len(rs) >= 0.8


def candidates(feed, now: float) -> list[dict]:
    out = []
    for eid, b in (getattr(feed, "b365", None) or {}).items():
        e = (feed.raw or {}).get(eid) or {}
        if e.get("sport") != "football" or e.get("status") != "pre":
            continue
        if not (20 * 60 <= (e.get("start_ts") or 0) - now <= 30 * 3600):
            continue
        anyt = b.get("scorers") or {}
        if len(anyt) < MIN_PLAYERS or not consistent(b):
            continue
        lam = total_lambda(e, b)
        if lam and not (1.2 <= lam <= 4.8):
            continue
        p_f = fgs_probs(b, lam)
        # patrón del partido con el primer goleador: si es raro, no se usa
        rf = sorted(p_f[k] * anyt[k]["odds"] for k in anyt if k in p_f and anyt[k].get("odds"))
        med = rf[len(rf) // 2] if len(rf) >= MIN_PLAYERS else None
        if med is not None and not (0.65 <= med <= 1.02):
            p_f, med = {}, None
        # segunda opinión: DraftKings
        dkn = dk_names(feed, eid)
        o_dk = {k: _match(dkn, anyt[k]["name"]) for k in anyt} if dkn else {}
        rx = sorted(anyt[k]["odds"] / o_dk[k] for k in anyt if o_dk.get(k) and anyt[k].get("odds"))
        med_x = rx[len(rx) // 2] if len(rx) >= MIN_PLAYERS else None
        if not p_f and not med_x:
            continue
        inj = ((getattr(feed, "dossier", {}) or {}).get(eid) or {}).get("_inj_status") or {}
        inj_n = {norm(n): s for n, s in inj.items()}
        for k, a in anyt.items():
            o = a.get("odds")
            if not o or not (1.8 <= o <= 5.5):
                continue
            pf = p_f.get(k)
            od = o_dk.get(k) if med_x else None
            pd = dk_prob(od) if od else None
            if pf is None and pd is None:
                continue
            if pf is not None and pd is not None and abs(pf - pd) > 0.10:
                continue                          # las dos opiniones no cuadran: mejor no
            p = pf if pd is None else pd if pf is None else (pf + pd) / 2
            ev_ = p * o - 1
            rel = (pf * o / med) if (pf is not None and med) else None
            cross = (o / od) if od else None
            pattern = (rel is not None and rel >= MIN_REL) or (cross is not None and cross >= CROSS_MIN
                                                               and cross / med_x >= 1.08)
            if ev_ < MIN_EV or not pattern or p < 0.17:
                continue
            if inj_n.get(norm(a["name"])) in ("out", "doubtful"):
                continue
            conf = (50 + min(15.0, (ev_ - MIN_EV) * 150) + (min(10.0, (rel - MIN_REL) * 60) if rel and rel >= MIN_REL else 0)
                    + (min(10.0, (cross - CROSS_MIN) * 60) if cross and cross >= CROSS_MIN else 0)
                    + (4 if p >= 0.3 else 0) + (5 if pf is not None and pd is not None and abs(pf - pd) <= 0.04 else 0))
            out.append({"event_id": eid, "player": a["name"], "key": k, "odds": o, "p": round(p, 4), "ev": round(ev_, 4),
                        "rel": round(rel, 3) if rel else None, "cross": round(cross, 3) if cross else None,
                        "dk_odds": od, "lam": round(lam, 2) if lam else None, "conf_score": round(min(conf, 79.0), 1),
                        "ev_raw": e})
    out.sort(key=lambda c: -c["conf_score"])
    return out


def fair_scorers(feed, eid: str, b365: dict | None = None, top: int = 4) -> list[tuple]:
    """Precio justo de 'anota en el partido' (los más probables de cada lado): [(jugador, prob)]."""
    dkn = dk_names(feed, eid)
    if len(dkn) < MIN_PLAYERS:
        return []
    e = (getattr(feed, "raw", {}) or {}).get(eid) or {}
    p_f = fgs_probs(b365 or {}, total_lambda(e, b365)) if b365 else {}
    rows = []
    for k, d in dkn.items():
        p = dk_prob(d["odds"])
        if p_f:
            pf = p_f.get(k)   # mismas claves (nombre normalizado) que el primer goleador de Bet365
            if pf is not None and abs(pf - p) <= 0.10:
                p = (p + pf) / 2
        rows.append((d["name"], p))
    rows.sort(key=lambda r: -r[1])
    return [r for r in rows if r[1] >= 0.15][: top * 2]


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
