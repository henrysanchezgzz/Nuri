"""
NURI · 누리 — export.py
=======================
Arma lo que ve la app (picks, autoevaluación y explorador de mercados) y lleva el historial de
líneas que usa el modelo para detectar casas que no han ajustado.
"""
from __future__ import annotations

import time

from pickbook import PICK_MARKETS, book_name

MARKET_ORDER = {"1x2": 0, "ml": 0, "dc": 1, "spread": 2, "ah": 2, "total": 3, "btts": 4, "total_1h": 5, "prop": 6}
HIST_KEEP_S = 30 * 3600
HIST_STEP_S = 600           # un punto cada 10 min como máximo
CREDIBLE_EV = 0.25          # con línea de mercado propia (igual que EngineConfig.max_credible_ev)
CREDIBLE_EV_MODEL = 0.12    # cuando la probabilidad viene sólo del modelo (EngineConfig.max_model_ev)


def record_history(hist: dict, rows_index: dict, now: float) -> dict:
    """Guarda, por selección, la probabilidad justa y los momios reales a lo largo del tiempo."""
    for rid, r in rows_index.items():
        if r["status"] != "pre" or r["market_type"] not in PICK_MARKETS:
            continue
        if not (0 < (r.get("start_ts") or 0) - now <= 48 * 3600):
            continue
        if not r.get("quotes") and r.get("move") is None:
            continue
        series = hist.setdefault(rid, [])
        if series and now - series[-1][0] < HIST_STEP_S - 60:   # tolerancia: el cron de GitHub no es exacto
            continue
        series.append([round(now), round(r["p"], 4), {q["book"]: q["odds"] for q in r.get("quotes") or []}])
        if len(series) > 200:
            del series[: len(series) - 200]
    for rid in [k for k, v in hist.items() if not v or now - v[-1][0] > HIST_KEEP_S]:
        hist.pop(rid, None)
    for v in hist.values():
        while len(v) > 2 and now - v[0][0] > HIST_KEEP_S:
            v.pop(0)
    return hist


def markets_view(result: dict, feed, now: float, horizon_h: float = 36) -> list[dict]:
    """Todos los mercados que NURI analiza, partido por partido: probabilidad real, momio justo y el
    mejor momio real disponible (con su ventaja)."""
    rows = result.get("rows_index") or {}
    picks = set()
    for p in getattr(feed, "_issued_ids", set()):
        picks.add(p)
    by_ev: dict[str, list] = {}
    for r in rows.values():
        if r["status"] == "pre" and not (0 < (r.get("start_ts") or 0) - now <= horizon_h * 3600):
            continue
        if r["status"] not in ("pre", "live"):
            continue
        by_ev.setdefault(r["event_id"], []).append(r)
    out = []
    for eid, rs in by_ev.items():
        e = (feed.raw.get(eid) if hasattr(feed, "raw") else None) or {}
        mk: dict[str, dict] = {}
        props = 0
        for r in sorted(rs, key=lambda x: (MARKET_ORDER.get(x["market_type"], 9), x.get("line") or 0)):
            if r["market_type"] == "prop":
                if r["market_key"] not in mk:
                    props += 1
                    if props > 8:
                        continue
            m = mk.setdefault(r["market_key"], {"key": r["market_key"], "label": r["market_label"],
                                                "type": r["market_type"], "line": r.get("line"), "sels": []})
            q = (r.get("quotes") or [None])[0]
            best = None
            if q:
                # una "ventaja" enorme casi siempre es error del modelo o una línea distinta: no se presume
                cap = CREDIBLE_EV if r.get("confidence") == "alta" else CREDIBLE_EV_MODEL
                ok = q["ev"] <= cap
                best = {"book": book_name(q["book"]), "odds": q["odds"], "ev": round(q["ev"], 4) if ok else None}
                if not ok:
                    best["warn"] = True
            m["sels"].append({
                "label": r["selection_label"], "p": round(r["p"], 3),
                "fair": round(r["fair"], 2) if r.get("fair") else None,
                "best": best,
                "ref": r.get("ref_odds"), "move": round(r["move"], 3) if r.get("move") is not None else None,
                "pick": r["id"] in picks,
            })
        if not mk:
            continue
        markets = sorted(mk.values(), key=lambda m: (MARKET_ORDER.get(m["type"], 9), m["line"] or 0))
        value = max((s["best"]["ev"] for m in markets for s in m["sels"] if s["best"] and s["best"]["ev"] is not None),
                    default=None)
        out.append({
            "id": eid, "league": rs[0]["league"], "sport": rs[0]["sport"], "status": rs[0]["status"],
            "start_ts": rs[0].get("start_ts"),
            "home": (e.get("home") or {}).get("name"), "away": (e.get("away") or {}).get("name"),
            "home_logo": (e.get("home") or {}).get("logo"), "away_logo": (e.get("away") or {}).get("logo"),
            "score": e.get("score"), "clock": e.get("clock"),
            "n": sum(len(m["sels"]) for m in markets), "best_ev": value, "markets": markets,
        })
    out.sort(key=lambda x: (x["status"] != "live", x["start_ts"] or 0))
    return out


def build_data(pb, result: dict, feed, now: float, version: str, sources: dict | None = None,
               next_minutes: int | None = None) -> dict:
    view = pb.view(feed, now)
    issued = {p["id"] for p in view["active"]}
    feed._issued_ids = issued
    markets = markets_view(result, feed, now)
    rows = result.get("rows_index") or {}
    return {
        "v": 1,
        "meta": {
            "generated": now, "version": version,
            "events": len({r["event_id"] for r in rows.values()}),
            "markets": len(rows),
            "upcoming": sum(1 for m in markets if m["status"] == "pre"),
            "live": sum(1 for m in markets if m["status"] == "live"),
            "next_minutes": next_minutes,
            "sources": {k: {"ok": bool(v.get("ok")), "detail": v.get("detail")} for k, v in (sources or {}).items()},
        },
        "nuri": view,
        "markets": markets,
    }


def now_ts() -> float:
    return time.time()
