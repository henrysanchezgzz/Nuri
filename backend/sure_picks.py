"""
NURI · sure_picks.py — PICKS DE ANÁLISIS (tier "S")
====================================================
Picks de alta probabilidad hechos con análisis deportivo, no con cazar cuotas:
  · el mercado (DraftKings sin comisión) ya da como favorito ese resultado, y
  · las estadísticas reales lo confirman (forma, diferencia de goles/puntos, bajas, modelo de ESPN,
    enfrentamientos, local/visita; en jugadores: cuántas veces pasó la línea, su promedio y sus minutos).
La probabilidad final mezcla las dos cosas. Sólo sale si queda ≥ 66 % y paga 1.40 o más.
"""
from __future__ import annotations

MIN_ODDS = 1.40
MAX_ODDS = 2.10
MIN_P = 0.66
MARKETS = ("1x2", "ml", "dc", "spread", "ah", "total", "btts")


def level(score: float) -> str:
    return "lock" if score >= 80 else "alta" if score >= 68 else "media" if score >= 56 else "baja"


def team_candidates(result: dict, feed, now: float, stats_agreement, min_lead: float, horizon: float) -> list[dict]:
    rows = result.get("rows_index") or {}
    out = []
    for rid, r in rows.items():
        if r.get("status") != "pre" or r.get("market_type") not in MARKETS:
            continue
        st = r.get("start_ts") or 0
        if not (min_lead <= st - now <= horizon):
            continue
        odds = r.get("ref_odds")
        q = (r.get("quotes") or [None])[0]
        book = None
        if q and q.get("odds") and q["odds"] >= (odds or 0) - 0.02:       # momio real (Bet365) si existe
            odds, book = q["odds"], q.get("book")
        if not odds or not (MIN_ODDS <= odds <= MAX_ODDS):
            continue
        pm = r.get("p") or 0
        if pm < 0.58 or r.get("confidence") == "baja":
            continue
        d = (getattr(feed, "dossier", {}) or {}).get(r["event_id"])
        if not d:
            continue                                    # sin estadísticas no hay análisis
        fam = r["sport"]
        ml = rows.get(f"{r['event_id']}|{'1x2' if fam == 'football' else 'ml'}|{r['selection']}")
        ml_p = ml["p"] if ml else (pm if r["market_type"] in ("1x2", "ml") else None)
        ss, notes = stats_agreement(d, fam, r, ml_p)
        if ss < 0.30 or len(notes) < 2:
            continue                                    # las estadísticas tienen que respaldarlo claramente
        p = min(0.92, pm + 0.07 * ss)
        if p < MIN_P or p * odds < 0.99:
            continue
        score = 50 + (p - MIN_P) * 160 + ss * 14 + min(3, len(notes) - 2) * 2
        out.append({"row": r, "p": round(p, 4), "p_mkt": pm, "ss": ss, "notes": notes, "odds": round(odds, 2),
                    "book": book, "conf_score": round(min(score, 92), 1)})
    out.sort(key=lambda c: -c["conf_score"])
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
        for pr in prs.values():
            po = pr.get("p_over")
            if po is None:
                continue
            games = src.games_of(pr["athlete"], e.get("_league"))
            if not games:
                continue
            name = src.name_of(pr["athlete"], e.get("_league"))
            if not name:
                continue
            for i, side in enumerate(("over", "under")):
                odds = (pr.get("o_now") or [None, None])[i]
                pm = po if side == "over" else 1 - po
                if not odds or not (MIN_ODDS <= odds <= MAX_ODDS) or pm < 0.52:
                    continue
                f = P.form_of(games, pr["stat"], pr["line"], side)
                if not f or f["n10"] < 7:
                    continue
                vals = P.gl_values(games, pr["stat"])
                avg5 = sum(vals[:5]) / min(5, len(vals)) if vals else None
                if avg5 is None or (side == "over" and avg5 <= pr["line"]) or (side == "under" and avg5 >= pr["line"]):
                    continue
                hr = (f["hits"] + 1) / (f["n10"] + 2)
                if f["hits"] / f["n10"] < 0.7:
                    continue                           # que la haya pasado en al menos 7 de 10
                p = min(0.90, 0.6 * pm + 0.4 * hr)
                if p < MIN_P or p * odds < 0.99:
                    continue
                score = 50 + (p - MIN_P) * 160 + (f["hits"] / f["n10"] - 0.7) * 40
                out.append({"event_id": eid, "pr": pr, "player": name, "side": side, "odds": round(odds, 2),
                            "p": round(p, 4), "hits": f["hits"], "n": f["n10"], "avg5": avg5,
                            "conf_score": round(min(score, 90), 1), "ev_raw": e})
    out.sort(key=lambda c: -c["conf_score"])
    return out
