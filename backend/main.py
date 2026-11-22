"""
NURI · 누리 — main.py
=====================
Servidor FastAPI: REST + WebSocket /ws/live que empuja el radar completo cada 5 s.

    python main.py          → http://127.0.0.1:8000  (sirve también el frontend compilado)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import database as db  # noqa: E402
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from mx_feeds import DemoFeed, LogoResolver, build_feed  # noqa: E402
from oddsio import DEFAULT_BOOKS, MX_CHOICES, OddsIoSource  # noqa: E402
from notify import Notifier  # noqa: E402
from pickbook import MX_TZ, PickBook  # noqa: E402
from quant_engine import MANUAL_BOOKS, EngineConfig, NuriEngine, clv_of  # noqa: E402
from version import APP_VERSION  # noqa: E402

TICK = float(os.getenv("NURI_TICK_SECONDS", "5"))
PORT = int(os.getenv("NURI_APP_PORT") or 8765)
IDLE_MINUTES = float(os.getenv("NURI_IDLE_MINUTES", "20"))
BACKEND_DIR = Path(__file__).resolve().parent
HOST = os.getenv("NURI_HOST", "127.0.0.1")


def _cfg() -> EngineConfig:
    return EngineConfig(
        mc_iterations=int(os.getenv("MC_ITERATIONS", "25000")),
        ev_threshold=float(os.getenv("EV_THRESHOLD", "0.045")),
        kelly_fraction=float(os.getenv("KELLY_FRACTION", "0.25")),
        kelly_cap=float(os.getenv("KELLY_CAP", "0.06")),
        max_credible_ev=float(os.getenv("MAX_CREDIBLE_EV", "0.25")),
        min_stake=int(os.getenv("MIN_STAKE_MXN", "10")),
    )


class State:
    feed = None
    engine: NuriEngine | None = None
    logos: LogoResolver | None = None
    snapshot: dict | None = None
    snapshot_json: str = ""
    clients: set[WebSocket] = set()
    tick = 0
    last_client_ts = time.time()
    lock = asyncio.Lock()
    pickbook: PickBook | None = None
    result: dict = {}
    notifier: Notifier | None = None


S = State()


def _picks_enabled() -> bool:
    """Los picks propios (y su autoevaluación) sólo corren con datos reales, nunca con el simulador."""
    return S.pickbook is not None and hasattr(S.feed, "fetch_result") and getattr(S.feed, "name", "") == "live"


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def dumps(obj) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------------------ ciclo en vivo
def _demo_cycle(now: float, bankroll: dict, taken: set[str]):
    S.feed.step(now)
    events = S.feed.events(now)
    return events, S.engine.analyze(events, bankroll["balance"], bankroll["available"], taken, now)


def _update_clv(events: list[dict]) -> None:
    status = {e["id"]: e["status"] for e in events}
    for b in db.pending_bets():
        if b["clv_frozen"]:
            continue
        ev_status = status.get(b["event_id"])
        fair = S.engine.fair_cache.get((b["event_id"], b["market_key"], b["selection"]))
        if ev_status is None or ev_status == "final":
            if b["closing_odds"]:
                db.update_clv(b["id"], b["closing_odds"], b["clv"], freeze=True)
            continue
        if b["status_at_take"] == "pre" and ev_status == "live":
            # CLV clásico: la línea de cierre es la última antes del inicio.
            if b["closing_odds"]:
                db.update_clv(b["id"], b["closing_odds"], b["clv"], freeze=True)
            continue
        if not fair:
            continue  # mercado suspendido momentáneamente
        db.update_clv(b["id"], fair["fair"], clv_of(b["odds"], fair["fair"]))


async def compute_and_broadcast() -> None:
    async with S.lock:
        now = time.time()
        bankroll = db.bankroll_state()
        taken = db.pending_pick_ids()
        if isinstance(S.feed, DemoFeed):
            events, result = await asyncio.to_thread(_demo_cycle, now, bankroll, taken)
        else:
            events = S.feed.events()
            result = await asyncio.to_thread(S.engine.analyze, events, bankroll["balance"],
                                             bankroll["available"], taken, now)
        await asyncio.to_thread(_update_clv, events)
        S.tick += 1
        S.result = result
        sources = S.feed.sources() if hasattr(S.feed, "sources") else {"Simulador": {"ok": True, "detail": S.feed.status["detail"]}}
        nuri = None
        if _picks_enabled():
            try:
                await asyncio.to_thread(S.pickbook.update, result, S.feed, now)
                nuri = await asyncio.to_thread(S.pickbook.view, S.feed, now)
            except Exception as exc:  # el registro de picks nunca debe tumbar el ciclo
                print(f"[picks] error: {exc!r}")
        scan_pre = sum(1 for e in events if e.get("status") == "pre" and 0 < (e.get("start_ts") or 0) - now < 48 * 3600)
        S.snapshot = {
            "type": "snapshot", "ts": now, "tick": S.tick, "tick_seconds": TICK, "version": APP_VERSION,
            "mode": S.feed.name, "sources": sources, "oddsio": _oddsio_state(),
            "scan": {"upcoming": scan_pre, "live": sum(1 for e in events if e.get("status") == "live"),
                     "total": len(events)},
            "nuri": nuri,
            "notify": S.notifier.status() if S.notifier else {"enabled": False},
        }
        S.snapshot_json = dumps(S.snapshot)
        if S.tick % 12 == 1:
            _write_status(sources, result)
    await broadcast(S.snapshot_json)


def _oddsio_state() -> dict:
    src = getattr(S.feed, "oddsio", None)
    if not src:
        return {"configured": False}
    st = src.source_status()
    return {"configured": True, "ok": bool(st.get("ok")), "detail": st.get("detail"), "books": st.get("books"),
            "error": st.get("error"), "remaining": st.get("remaining")}


def _write_status(sources: dict, result: dict) -> None:
    """estado.json: resumen legible del funcionamiento (para soporte)."""
    try:
        evs = result.get("events", [])
        (BACKEND_DIR / "estado.json").write_text(dumps({
            "version": APP_VERSION, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": S.feed.name,
            "sources": sources, "events": len(evs), "live": sum(1 for e in evs if e["status"] == "live"),
            "board": len(result.get("board", [])), "picks": len(result.get("picks", [])),
            "clients": len(S.clients),
            "sample": [{k: b.get(k) for k in ("league", "status", "market_label", "selection_label", "p", "fair", "move")}
                       for b in result.get("board", [])[:6]],
        }), encoding="utf-8")
    except Exception as exc:
        print(f"[NURI] estado.json: {exc!r}")


async def broadcast(text: str) -> None:
    dead = []
    for ws in list(S.clients):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        S.clients.discard(ws)


def _exit_soon(delay: float = 0.5) -> None:
    import threading

    threading.Timer(delay, lambda: os._exit(0)).start()


def _disk_version() -> str | None:
    try:
        txt = (BACKEND_DIR / "version.py").read_text(encoding="utf-8")
        return txt.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None


def _self_update() -> None:
    """Hay archivos nuevos en disco: arrancamos el lanzador nuevo (sin abrir otra ventana) y salimos.
    La ventana abierta se reconecta sola y recarga la interfaz nueva."""
    import subprocess
    import sys

    print(f"[NURI] actualización detectada {APP_VERSION} → {_disk_version()}")
    flags = (0x00000008 | 0x08000000) if os.name == "nt" else 0
    subprocess.Popen([sys.executable, str(BACKEND_DIR / "nuri_app.py"), "--quiet"], cwd=str(BACKEND_DIR),
                     creationflags=flags, close_fds=True)
    _exit_soon(0.8)


async def idle_watch() -> None:
    """Apaga el servidor cuando nadie lo usa (ventana cerrada) para no gastar batería, y se
    actualiza solo cuando llegan archivos nuevos."""
    updating = False
    while True:
        await asyncio.sleep(30)
        dv = _disk_version()
        if dv and dv != APP_VERSION and not updating:
            updating = True
            _self_update()
            continue
        if S.clients:
            S.last_client_ts = time.time()
        elif S.notifier:
            pass  # avisos al celular activos: NURI se queda trabajando en segundo plano
        elif time.time() - S.last_client_ts > IDLE_MINUTES * 60:
            print("[NURI] sin uso: apagando")
            _exit_soon(0.1)


async def ticker() -> None:
    while True:
        t0 = time.time()
        try:
            await compute_and_broadcast()
        except Exception as exc:
            print(f"[tick] error: {exc!r}")
        # en segundo plano (sin ventana abierta) basta revisar cada 20 s: gasta menos batería
        tick = TICK if (S.clients or not S.notifier) else max(TICK, 20.0)
        await asyncio.sleep(max(tick - (time.time() - t0), 0.2))


async def settle_loop() -> None:
    """Califica solos los picks de NURI cuando terminan sus partidos (cada 2 min)."""
    await asyncio.sleep(20)
    while True:
        try:
            if _picks_enabled():
                n = await S.pickbook.settle(S.feed, time.time())
                if n:
                    asyncio.create_task(compute_and_broadcast())
                await asyncio.to_thread(S.pickbook.weekly_check, time.time())
        except Exception as exc:
            print(f"[picks] calificación: {exc!r}")
        await asyncio.sleep(120)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    S.engine = NuriEngine(_cfg())
    S.logos = LogoResolver(db, os.getenv("THESPORTSDB_KEY", "3")) if os.getenv("LOGOS", "on").lower() != "off" else None
    S.feed = build_feed(S.logos)
    key = (os.getenv("ODDSIO_KEY") or db.get_setting("oddsio_key") or "").strip()
    if key and hasattr(S.feed, "configure_oddsio"):
        S.feed.configure_oddsio(key)
    print(f"[NURI] modo {S.feed.name.upper()} · tick {TICK:g}s · http://{HOST}:{PORT}")
    S.pickbook = PickBook()
    tasks = [asyncio.create_task(ticker()), asyncio.create_task(S.feed.run()), asyncio.create_task(idle_watch()),
             asyncio.create_task(settle_loop())]
    topic = (os.getenv("NTFY_TOPIC") or db.get_setting("ntfy_topic") or "").strip()
    if topic:
        _start_notifier(topic)
    if S.logos:
        tasks.append(asyncio.create_task(S.logos.run()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="NURI · 누리 Live Quant Radar", lifespan=lifespan)


@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    """La página principal nunca se guarda en caché: cada versión nueva se ve al instante."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------------------ modelos REST
class ValidateIn(BaseModel):
    event_id: str
    market_key: str
    selection: str
    odds: float = Field(gt=1.0, lt=1000)
    book: str = "BetVIP"


class BankrollIn(BaseModel):
    amount: float = Field(gt=0, lt=100_000_000)


class BetIn(BaseModel):
    pick_id: str | None = None
    group_id: str | None = None
    event_id: str
    market_key: str
    selection: str
    book: str
    odds: float = Field(gt=1.0, lt=1000)
    stake: float = Field(gt=0)


class SettleIn(BaseModel):
    result: str  # won | lost | push | pending


class OddsIoKeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=200)


class OddsIoBooksIn(BaseModel):
    books: list[str]


# ------------------------------------------------------------------------------ endpoints
@app.get("/api/health")
async def health():
    return {"ok": True, "version": APP_VERSION, "mode": S.feed.name if S.feed else None, "tick": S.tick,
            "clients": len(S.clients)}


@app.post("/api/shutdown")
async def shutdown(request: Request):
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Sólo desde esta computadora")
    _exit_soon(0.4)
    return {"ok": True}


@app.get("/api/snapshot")
async def snapshot():
    if not S.snapshot:
        await compute_and_broadcast()
    return S.snapshot


class NotifyIn(BaseModel):
    topic: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")


def _start_notifier(topic: str) -> None:
    if S.notifier:
        S.notifier.topic = topic
    else:
        S.notifier = Notifier(topic, os.getenv("NTFY_SERVER") or "https://ntfy.sh")
        asyncio.create_task(S.notifier.run())
    if S.pickbook:
        S.pickbook.notifier = S.notifier
    print(f"[NURI] avisos al celular activos (ntfy · {topic}) · modo segundo plano")


@app.post("/api/settings/notify")
async def set_notify(body: NotifyIn):
    """Activa los avisos al celular (ntfy) con el tema indicado; se guarda en esta computadora."""
    db.set_setting("ntfy_topic", body.topic)
    _start_notifier(body.topic)
    return S.notifier.status()


@app.delete("/api/settings/notify")
async def del_notify():
    db.set_setting("ntfy_topic", "")
    S.notifier = None
    if S.pickbook:
        S.pickbook.notifier = None
    return {"enabled": False}


@app.post("/api/notify/test")
async def notify_test():
    """Manda al celular un mensaje de prueba con los picks activos."""
    if not S.notifier:
        raise HTTPException(400, "Los avisos al celular no están configurados.")
    active = ((S.snapshot or {}).get("nuri") or {}).get("active") or []
    S.notifier.test(active, MX_TZ)
    return S.notifier.status()


@app.get("/api/facts/{event_id}")
async def event_facts(event_id: str):
    """Estadísticas reales del partido (récord, forma, promedios, bajas, líderes, H2H)."""
    d = S.feed.facts_for(event_id) if hasattr(S.feed, "facts_for") else None
    if not d:
        raise HTTPException(404, "Todavía no hay datos de este partido; aparecen unos minutos después de abrir NURI.")
    return d


# ------------------------------------------------------------------------------ momios reales (odds-api.io)
@app.get("/api/settings")
async def get_settings():
    return {"oddsio": _oddsio_state(), "choices": list(MX_CHOICES), "default_books": list(DEFAULT_BOOKS)}


@app.post("/api/settings/oddsio")
async def set_oddsio(body: OddsIoKeyIn):
    if not hasattr(S.feed, "configure_oddsio"):
        raise HTTPException(400, "Disponible sólo con datos reales (no en modo demo).")
    key = body.key.strip()
    probe = OddsIoSource(key)
    res = await probe.check()
    if not res["ok"]:
        raise HTTPException(400, res["detail"] or "No se pudo validar la clave.")
    db.set_setting("oddsio_key", key)
    src = S.feed.configure_oddsio(key)
    src.books = probe.books
    src.last_books = probe.last_books
    asyncio.create_task(compute_and_broadcast())
    return {"ok": True, "books": probe.books, "detail": res["detail"], "choices": list(MX_CHOICES)}


@app.post("/api/settings/oddsio/books")
async def set_oddsio_books(body: OddsIoBooksIn):
    src = getattr(S.feed, "oddsio", None)
    if not src:
        raise HTTPException(400, "Primero pega tu clave de odds-api.io.")
    res = await src.select_books(body.books)
    if not res["ok"]:
        raise HTTPException(400, res["detail"])
    return {"ok": True, "books": res["books"]}


@app.delete("/api/settings/oddsio")
async def del_oddsio():
    db.set_setting("oddsio_key", "")
    if hasattr(S.feed, "configure_oddsio"):
        S.feed.configure_oddsio(None)
    asyncio.create_task(compute_and_broadcast())
    return {"ok": True}


@app.post("/api/validate")
async def validate(body: ValidateIn):
    br = db.bankroll_state()
    res = S.engine.validate(body.event_id, body.market_key, body.selection, body.odds, br["balance"], br["available"])
    res["book"] = body.book
    res["odds"] = body.odds
    return res


@app.get("/api/vault")
async def vault():
    return {"state": db.bankroll_state(), "history": db.bankroll_history(), "bets": db.list_bets()}


@app.post("/api/bankroll")
async def set_bankroll(body: BankrollIn):
    db.set_initial_bankroll(body.amount)
    asyncio.create_task(compute_and_broadcast())
    return await vault()


def _find_meta(body: BetIn) -> dict:
    snap = S.result or {}
    pick = next((p for p in snap.get("picks", []) if p["id"] == body.pick_id), None)
    if not pick:
        pick = next((b for b in snap.get("board", []) if b["event_id"] == body.event_id
                     and b["market_key"] == body.market_key and b["selection"] == body.selection), None)
    ev = next((e for e in snap.get("events", []) if e["id"] == body.event_id), None)
    sure = None
    if body.group_id:
        sure = next((s for s in snap.get("surebets", []) if s["id"] == body.group_id), None)
    return {"pick": pick, "event": ev or pick or sure, "sure": sure}


@app.post("/api/bets")
async def take_bet(body: BetIn):
    meta = _find_meta(body)
    ev = meta["event"]
    if not ev:
        raise HTTPException(404, "El evento ya no está en el radar.")
    fair = S.engine.fair_cache.get((body.event_id, body.market_key, body.selection))
    if not fair:
        raise HTTPException(409, "El mercado está suspendido o cerró. Intenta en unos segundos.")
    pick, sure = meta["pick"], meta["sure"]
    if pick:
        market_label, selection_label = pick["market_label"], pick["selection_label"]
    elif sure:
        leg = next((l for l in sure["legs"] if l["selection"] == body.selection), None)
        market_label, selection_label = sure["market_label"], (leg or {}).get("selection_label", body.selection)
    else:
        market_label, selection_label = body.market_key, body.selection
    w, l = fair["w"], fair["l"]
    bet_id = db.add_bet({
        "pick_id": body.pick_id or (f"{body.group_id}|{body.selection}" if body.group_id else None),
        "group_id": body.group_id, "event_id": body.event_id, "sport": ev.get("sport"), "league": ev.get("league"),
        "event_name": f"{ev['home']['name']} vs {ev['away']['name']}", "start_ts": ev.get("start_ts"),
        "status_at_take": ev.get("status"), "market_key": body.market_key, "market_label": market_label,
        "selection": body.selection, "selection_label": selection_label, "book": body.book,
        "odds": body.odds, "stake": round(body.stake, 2), "p_real": round(w / max(w + l, 1e-9), 4),
        "fair_odds": round(fair["fair"], 4), "ev": round(w * (body.odds - 1) - l, 4), "source": fair["source"],
        "closing_odds": round(fair["fair"], 4), "clv": round(clv_of(body.odds, fair["fair"]), 5),
        "mode": S.feed.name,
    })
    asyncio.create_task(compute_and_broadcast())
    return {"ok": True, "id": bet_id, "bet": db.get_bet(bet_id)}


@app.post("/api/bets/{bet_id}/settle")
async def settle(bet_id: int, body: SettleIn):
    try:
        bet = db.settle_bet(bet_id, body.result)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not bet:
        raise HTTPException(404, "Apuesta no encontrada")
    asyncio.create_task(compute_and_broadcast())
    return await vault()


@app.delete("/api/bets/{bet_id}")
async def delete(bet_id: int):
    if not db.delete_bet(bet_id):
        raise HTTPException(404, "Apuesta no encontrada")
    asyncio.create_task(compute_and_broadcast())
    return await vault()


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    S.clients.add(ws)
    S.last_client_ts = time.time()
    try:
        if S.snapshot_json:
            await ws.send_text(S.snapshot_json)
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(dumps({"type": "pong", "ts": time.time()}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        S.clients.discard(ws)


# ------------------------------------------------------------------------------ frontend
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {"NURI": "backend activo", "frontend": "ejecuta start.bat / start.sh para compilar la interfaz",
                "ws": "/ws/live"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=HOST, port=PORT, log_level="warning")
