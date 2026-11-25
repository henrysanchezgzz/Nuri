"""
NURI · sure_picks.py — PICKS DE ANÁLISIS (tier "S")
====================================================
Aquí pesa más el análisis de NURI que el casino: no se va con el favorito por ser favorito.
  · NURI calcula su propia probabilidad con estadísticas (model.py): 65 % su análisis + 35 % el mercado.
  · Es pick cuando esa probabilidad dice que el momio paga de más (≥ 5 % de ventaja), sea favorito o no.
  · Cuidados: momio 1.40–4.50, que el análisis no se aleje absurdamente del mercado (señal de que falta
    información, p. ej. una baja que no vemos) y datos suficientes.
"""
from __future__ import annotations

import model as M

MIN_ODDS, MAX_ODDS = 1.20, 4.50
SAFE_ODDS, SAFE_P = 1.40, 0.78   # abajo de 1.40 sólo entra si NURI lo ve en 78 % o más (modo 80 %)
W_MODEL = 0.65
MIN_EDGE = 0.05
MAX_GAP = 0.15        # si el análisis se aleja más que esto del mercado, casi siempre falta información
MARKETS = ("1x2", "ml", "dc", "spread", "ah", "total", "btts")


def level(p: float) -> str:
    """El nivel es la probabilidad: LOCK 80 %+, Alta 70 %+, Media 60 %+, Baja menos."""
    return "lock" if p >= 0.80 else "alta" if p >= 0.70 else "media" if p >= 0.60 else "baja"


def need_edge(odds: float, p: float) -> float | None:
    if odds < SAFE_ODDS:
        return 0.02 if p >= SAFE_P else None
    return MIN_EDGE


def _conf(p: float, ev: float, extra: float) -> float:
    return 46 + min(14.0, ev * 80) + (p - 0.5) * 50 + extra


def team_candidates(result: dict, feed, now: float, stats_agreement, min_lead: float, horizon: float,
                    for_against=None) -> list[dict]:
    rows = result.get("rows_index") or {}
    tps: dict = {}
    best: dict = {}
    for rid, r in rows.items():
        if r.get("status") != "pre" or r.get("market_type") not in MARKETS:
            continue
        if not (min_lead <= (r.get("start_ts") or 0) - now <= horizon):
            continue
        d = (getattr(feed, "dossier", {}) or {}).get(r["event_id"])
        if not d:
            continue
        fam = r["sport"]
        if r["event_id"] not in tps:
            tps[r["event_id"]] = M.team_probs(d, fam, for_against)
        tp = tps[r["event_id"]]
        pmod = M.prob_for(tp, r["market_type"], r["selection"], r.get("line"))
        if pmod is None:
            continue
        odds = r.get("ref_odds")
        q = (r.get("quotes") or [None])[0]
        book = None
        if q and q.get("odds") and q["odds"] >= (odds or 0) - 0.02:
            odds, book = q["odds"], q.get("book")
        if not odds or not (MIN_ODDS <= odds <= MAX_ODDS):
            continue
        pm = r.get("p") or 0
        if abs(pmod - pm) > MAX_GAP:
            continue
        p = W_MODEL * pmod + (1 - W_MODEL) * pm
        ev = p * odds - 1
        need = need_edge(odds, p)
        if need is None:
            continue
        need += 0.03 if tp.get("src", 1) < 2 else 0
        if ev < need or p < 0.30 or pmod * odds - 1 < need:
            continue
        ml = rows.get(f"{r['event_id']}|{'1x2' if fam == 'football' else 'ml'}|{r['selection']}")
        ml_p = ml["p"] if ml else (pm if r["market_type"] in ("1x2", "ml") else None)
        ss, notes = stats_agreement(d, fam, r, ml_p)
        if ss < -0.2:
            continue                                     # las demás estadísticas van en contra
        c = {"row": r, "p": round(p, 4), "p_mkt": pm, "p_model": round(pmod, 4), "ss": ss, "notes": notes,
             "odds": round(odds, 2), "book": book, "ev": round(ev, 4), "tp": tp,
             "conf_score": round(min(_conf(p, ev, ss * 10 + min(2, len(notes)) * 2), 92), 1)}
        k = r["event_id"]
        if k not in best or c["conf_score"] > best[k]["conf_score"]:
            best[k] = c
    out = sorted(best.values(), key=lambda c: -c["conf_score"])
    return out


def prop_candidates(feed, now: float, min_lead: float, horizon: float) -> list[dict]:
    import props as P
    src = getattr(feed, "props_src", None)
    if src is None:
        return []
    out = []
    for eid, prs in (src.props or {}).items():
        e = (feed.raw or {}).get(eid) or {}
        if e.get("status") != "pre" or not (min_lead <= (e.get("start_ts") or 0) - now <= horizon):
            continue
        lg = e.get("_league")
        for pr in prs.values():
            po = pr.get("p_over")
            if po is None:
                continue
            games = src.games_of(pr["athlete"], lg)
            vals = P.gl_values(games, pr["stat"]) if games else []
            if len(vals) < 8:
                continue
            name = src.name_of(pr["athlete"], lg)
            if not name:
                continue
            for i, side in enumerate(("over", "under")):
                odds = (pr.get("o_now") or [None, None])[i]
                if not odds or not (MIN_ODDS <= odds <= 3.5):
                    continue
                pm = po if side == "over" else 1 - po
                pmod = M.prop_prob(vals[:15], pr["line"], side, pr.get("dist") == "poisson")
                if pmod is None or abs(pmod - pm) > MAX_GAP:
                    continue
                p = W_MODEL * pmod + (1 - W_MODEL) * pm
                ev = p * odds - 1
                need = need_edge(odds, p)
                if need is None or ev < need or pmod * odds - 1 < need or p < 0.35:
                    continue
                last = vals[:10]
                hits = sum(1 for x in last if (x > pr["line"] if side == "over" else x < pr["line"]))
                avg5 = sum(vals[:5]) / 5
                extra = (hits / len(last) - 0.5) * 16
                out.append({"event_id": eid, "pr": pr, "player": name, "side": side, "odds": round(odds, 2),
                            "p": round(p, 4), "p_mkt": pm, "p_model": round(pmod, 4), "ev": round(ev, 4),
                            "hits": hits, "n": len(last), "avg5": avg5,
                            "conf_score": round(min(_conf(p, ev, extra), 90), 1), "ev_raw": e})
    out.sort(key=lambda c: -c["conf_score"])
    return out
