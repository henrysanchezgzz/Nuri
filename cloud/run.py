"""
NURI · 누리 — cloud/run.py
==========================
Una corrida completa de NURI en GitHub Actions (cada 15 min), sin depender de tu laptop:

  1. abre el estado cifrado (historial de picks, líneas, avisos pendientes)
  2. baja de ESPN marcadores, líneas y momios reales de Bet365
  3. analiza todos los mercados (Shin + Monte Carlo + modelos por deporte)
  4. emite los picks que pasan el filtro, califica los que ya terminaron, resumen del lunes
  5. manda los avisos al celular (ntfy)
  6. guarda estado y datos cifrados para la app

Variables de entorno (secretos del repositorio):
  NURI_CODE     código que cifra todo (es el mismo que pide la app del celular)
  NTFY_TOPIC    tema de ntfy para los avisos
  ODDSIO_KEY    (opcional) clave de odds-api.io para Caliente / Codere
  NURI_APP_URL  (opcional) dirección de la app; al tocar un aviso se abre
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv("NURI_OUT") or ROOT / "_out")
WORK = Path(os.getenv("NURI_WORK") or ROOT / "_work")
WORK.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
os.environ["NURI_DB"] = str(WORK / "nuri.sqlite3")
os.environ.setdefault("NURI_CONFIRM_S", "300")  # el pick debe sostenerse en dos corridas seguidas (~8 min)
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "cloud"))

import httpx  # noqa: E402

from crypto import decrypt_bytes, encrypt_bytes, encrypt_json  # noqa: E402

CODE = (os.getenv("NURI_CODE") or "").strip()
TOPIC = (os.getenv("NTFY_TOPIC") or "").strip()
STATUS: dict = {"ok": False, "started": time.time(), "steps": []}


def _clean(text: str) -> str:
    """Nada secreto llega al archivo público de estado."""
    for s in (CODE, TOPIC, os.getenv("ODDSIO_KEY") or ""):
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return text[:600]


def step(msg: str) -> None:
    print(msg, flush=True)
    STATUS["steps"].append(_clean(msg))


def load_state(path: Path) -> dict:
    """Descifra state.enc: la base SQLite de picks y el JSON con historial / avisos pendientes."""
    if not path.exists() or path.stat().st_size < 20:
        step("[estado] primera corrida: sin estado previo")
        return {}
    try:
        raw = decrypt_bytes(json.loads(path.read_text()), CODE)
    except Exception as exc:
        raise SystemExit(f"[estado] no se pudo descifrar ({type(exc).__name__}): ¿cambió NURI_CODE?") from exc
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if "nuri.sqlite3" in z.namelist():
            (WORK / "nuri.sqlite3").write_bytes(z.read("nuri.sqlite3"))
        state = json.loads(z.read("state.json")) if "state.json" in z.namelist() else {}
    step(f"[estado] abierto · última corrida hace {(time.time() - (state.get('last_run') or time.time())) / 60:.0f} min")
    return state


def save_state(path: Path, state: dict) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if (WORK / "nuri.sqlite3").exists():
            z.write(WORK / "nuri.sqlite3", "nuri.sqlite3")
        z.writestr("state.json", json.dumps(state, separators=(",", ":")))
    path.write_text(json.dumps(encrypt_bytes(buf.getvalue(), CODE)))


async def main() -> None:
    t0 = time.time()
    if not CODE:
        raise SystemExit("Falta el secreto NURI_CODE (Settings → Secrets and variables → Actions)")
    state = load_state(Path(os.getenv("NURI_STATE_IN") or ROOT / "state.enc"))

    import database as db
    db.init_db()
    from export import build_data, record_history
    from mx_feeds import EspnFeed
    from notify import Notifier
    from pickbook import PickBook
    from quant_engine import EngineConfig, NuriEngine
    from version import APP_VERSION
    STATUS["version"] = APP_VERSION

    feed = EspnFeed()
    stats = await feed.run_once()
    step(f"[ESPN] {stats['events']} partidos · {stats['summaries']} resúmenes · {stats['ok']} ok / {stats['err']} errores")
    STATUS["espn"] = stats

    # Props de jugadores: líneas reales de DraftKings (NBA y NFL) y nombres de las plantillas
    from props import PropsSource
    ps = PropsSource(state.get("props"))
    try:
        await ps.refresh(feed)
        n_gl = await ps.enrich(feed)      # historial partido por partido de los candidatos
        if n_gl:
            step(f"[props] historial bajado de {n_gl} jugadores")
    except Exception as exc:
        step(f"[props] {type(exc).__name__}: {exc}")
    feed.props_src = ps
    feed.src["Props de jugadores · DraftKings"] = {"ok": ps.status["ok"], "detail": ps.status["detail"],
                                                   "last": time.time()}
    step(f"[props] {ps.status['detail']}")
    STATUS["props"] = {k: ps.status.get(k) for k in ("events", "props", "errors")}

    # Caliente / Codere (opcional): una pasada del cliente de odds-api.io
    if os.getenv("ODDSIO_KEY"):
        src = feed.configure_oddsio(os.getenv("ODDSIO_KEY"))
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                for _ in range(2):
                    await src._cycle(client, feed)
            step(f"[odds-api.io] {src.source_status().get('detail')}")
        except Exception as exc:
            step(f"[odds-api.io] {type(exc).__name__}")

    now = time.time()
    events = feed.events()
    engine = NuriEngine(EngineConfig())
    result = engine.analyze(events, 1000.0, 1000.0, set(), now)
    step(f"[motor] {len(events)} partidos · {len(result['rows_index'])} selecciones analizadas")
    STATUS["analyzed"] = {"events": len(events), "selections": len(result["rows_index"])}

    pb = PickBook()
    pb.hist = record_history(state.get("hist") or {}, result["rows_index"], now)
    pb.seen = {k: float(v) for k, v in (state.get("seen") or {}).items()}
    notifier = None
    if TOPIC:
        notifier = Notifier(TOPIC, os.getenv("NTFY_SERVER") or "https://ntfy.sh")
        notifier.click = os.getenv("NURI_APP_URL") or None
        notifier.load(state.get("outbox") or [])
        pb.notifier = notifier
    else:
        step("[avisos] sin NTFY_TOPIC: no se mandan avisos al celular")

    try:
        import scorers as S
        b_ev = [eid for eid, b in (feed.b365 or {}).items() if (b or {}).get("fgs") and (b or {}).get("scorers")]
        step(f"[goleadores] partidos con primer goleador y anota de Bet365: {len(b_ev)} · candidatos {len(S.candidates(feed, now))}")
    except Exception as exc:
        step(f"[goleadores] {type(exc).__name__}")
    pb.update(result, feed, now)
    settled = await pb.settle(feed, now)
    pb.weekly_check(now)
    step(f"[picks] activos {len(db.pending_nuri_picks())} · calificados ahora {settled} · confirmando {pb.confirming}"
         f" · props candidatos {len(ps.candidates(feed, now))}")

    if notifier:
        async with httpx.AsyncClient(timeout=12.0) as client:
            await notifier.flush(client)
        step(f"[avisos] enviados {notifier.sent} · pendientes {len(notifier.dump())}")

    try:   # diagnóstico cifrado (sólo para revisar el motor; la app no lo usa)
        (OUT / "diag.enc").write_text(encrypt_json(diagnose(pb, result, feed, ps, now), CODE))
    except Exception as exc:
        step(f"[diag] {type(exc).__name__}: {exc}")

    data = build_data(pb, result, feed, now, APP_VERSION, feed.sources(), next_minutes=15)
    (OUT / "data.enc").write_text(encrypt_json(data, CODE))
    keep = {k: v for k, v in pb.seen.items() if now - v < 6 * 3600}
    save_state(OUT / "state.enc", {"hist": pb.hist, "seen": keep, "outbox": notifier.dump() if notifier else [],
                                   "props": ps.dump(), "last_run": now, "version": APP_VERSION})
    STATUS["ok"] = True
    step(f"[listo] {time.time() - t0:.1f} s · datos {len((OUT / 'data.enc').read_text()) // 1024} KB")


def diagnose(pb, result, feed, ps, now: float) -> dict:
    """Qué vio el motor en esta corrida: candidatos de props, de momio real / línea movida y lo ya emitido."""
    import database as db
    def game(e):
        return f"{(e.get('home') or {}).get('name')} vs {(e.get('away') or {}).get('name')}"
    out: dict = {"ts": now, "props": [], "cands": [], "picks": []}
    for c in ps.candidates(feed, now, {}, min_p=0.50)[:80]:
        e = c["ev_raw"]
        out["props"].append({"lg": e.get("league"), "game": game(e), "eid": c["event_id"], "player": c["player"],
                             "stat": c["label"], "side": c["side"], "line": c["line"], "now": c["now_line"],
                             "open": c["open_line"], "kind": c["kind"], "p": round(c["p"], 3),
                             "conf": c["conf_score"], "min_odds": c["min_odds"],
                             "start_h": round((e.get("start_ts", now) - now) / 3600, 1),
                             "form": (c.get("form") or {}).get("hr")})
    for c in pb.candidates(result, feed, now)[:60]:
        r, e = c["row"], c["ev_raw"]
        out["cands"].append({"tier": c["tier"], "lg": r.get("league"), "game": game(e), "eid": r.get("event_id"),
                             "mkt": r.get("market_label"), "sel": r.get("selection_label"), "p": round(r["p"], 3),
                             "ev": round(c.get("ev") or 0, 4), "odds": c.get("odds"), "book": c.get("book"),
                             "conf": c.get("conf_score"), "mv": round(c.get("mv") or 0, 3),
                             "start_h": round((r.get("start_ts", now) - now) / 3600, 1),
                             "seen_min": round((now - pb.seen.get(r["id"], now)) / 60, 1)})
    try:   # goleadores: Bet365 contra DraftKings y contra el primer goleador (para calibrar)
        import scorers as S
        out["scorers"] = []
        for eid, b in list((feed.b365 or {}).items())[:14]:
            e = feed.raw.get(eid) or {}
            anyt = (b or {}).get("scorers") or {}
            if not anyt:
                continue
            lam = S.total_lambda(e, b)
            pf = S.fgs_probs(b, lam)
            dkn = S.dk_names(feed, eid)
            rows = []
            for k, a in sorted(anyt.items(), key=lambda kv: kv[1]["odds"])[:14]:
                od = S._match(dkn, a["name"]) if dkn else None
                rows.append([a["name"], a["odds"], od, round(pf[k], 3) if k in pf else None,
                             round(S.dk_prob(od), 3) if od else None])
            out["scorers"].append({"game": game(e), "lam": round(lam, 2) if lam else None, "n_dk": len(dkn), "rows": rows})
        out["scorer_cands"] = [{k: c.get(k) for k in ("player", "odds", "p", "ev", "rel", "cross", "dk_odds", "conf_score")}
                               for c in S.candidates(feed, now)]
        out["dk_scorer_events"] = {eid: len(v) for eid, v in (getattr(ps, "scorers", {}) or {}).items()}
    except Exception as exc:
        out["scorers_err"] = repr(exc)
    for p in db.list_nuri_picks(since_ts=now - 3 * 86400):
        out["picks"].append({k: p.get(k) for k in ("id", "tier", "league", "market_label", "selection_label", "conf",
                                                    "conf_score", "stake_pct", "result", "start_ts", "created_at",
                                                    "odds", "min_odds", "close_p", "p")})
    return out


if __name__ == "__main__":
    code = 0
    try:
        asyncio.run(main())
    except SystemExit as exc:
        step(str(exc))
        code = 1
    except Exception:
        step("[error] " + traceback.format_exc(limit=6))
        code = 1
    STATUS["finished"] = time.time()
    (OUT / "status.json").write_text(json.dumps(STATUS, ensure_ascii=False, indent=1))
    sys.exit(code)
