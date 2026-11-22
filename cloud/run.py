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
  NURI_CODE   código que cifra todo (es el mismo que pide la app del celular)
  NTFY_TOPIC  tema de ntfy para los avisos
  ODDSIO_KEY  (opcional) clave de odds-api.io para Caliente / Codere
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv("NURI_OUT") or ROOT / "_out")
WORK = Path(os.getenv("NURI_WORK") or ROOT / "_work")
WORK.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
os.environ["NURI_DB"] = str(WORK / "nuri.sqlite3")
os.environ.setdefault("NURI_CONFIRM_S", "600")  # dos corridas seguidas (cada ~15 min) para confirmar
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "cloud"))

import httpx  # noqa: E402

from crypto import decrypt_bytes, encrypt_bytes, encrypt_json  # noqa: E402

CODE = (os.getenv("NURI_CODE") or "").strip()
if not CODE:
    sys.exit("Falta el secreto NURI_CODE")


def load_state(path: Path) -> dict:
    """Descifra state.enc: la base SQLite de picks y el JSON con historial / avisos pendientes."""
    if not path.exists() or path.stat().st_size < 20:
        print("[estado] primera corrida: sin estado previo")
        return {}
    try:
        raw = decrypt_bytes(json.loads(path.read_text()), CODE)
    except Exception as exc:
        sys.exit(f"[estado] no se pudo descifrar ({type(exc).__name__}); ¿cambió NURI_CODE?")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if "nuri.sqlite3" in z.namelist():
            (WORK / "nuri.sqlite3").write_bytes(z.read("nuri.sqlite3"))
        return json.loads(z.read("state.json")) if "state.json" in z.namelist() else {}


def save_state(path: Path, state: dict) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if (WORK / "nuri.sqlite3").exists():
            z.write(WORK / "nuri.sqlite3", "nuri.sqlite3")
        z.writestr("state.json", json.dumps(state, separators=(",", ":")))
    path.write_text(json.dumps(encrypt_bytes(buf.getvalue(), CODE)))


async def main() -> None:
    t0 = time.time()
    state = load_state(Path(os.getenv("NURI_STATE_IN") or ROOT / "state.enc"))

    import database as db
    db.init_db()
    from export import build_data, record_history
    from mx_feeds import EspnFeed
    from notify import Notifier
    from pickbook import PickBook
    from quant_engine import EngineConfig, NuriEngine
    from version import APP_VERSION

    feed = EspnFeed()
    if os.getenv("ODDSIO_KEY"):
        print("[odds-api.io] clave presente (Caliente / Codere)")
    stats = await feed.run_once()
    print(f"[ESPN] {stats}")

    # Caliente / Codere (opcional): una pasada del cliente de odds-api.io
    if os.getenv("ODDSIO_KEY"):
        src = feed.configure_oddsio(os.getenv("ODDSIO_KEY"))
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                for _ in range(2):
                    await src._cycle(client, feed)
        except Exception as exc:
            print(f"[odds-api.io] {exc!r}")

    now = time.time()
    events = feed.events()
    engine = NuriEngine(EngineConfig())
    result = engine.analyze(events, 1000.0, 1000.0, set(), now)
    print(f"[motor] {len(events)} partidos · {len(result['rows_index'])} selecciones analizadas")

    pb = PickBook()
    pb.hist = record_history(state.get("hist") or {}, result["rows_index"], now)
    pb.seen = {k: float(v) for k, v in (state.get("seen") or {}).items()}
    topic = (os.getenv("NTFY_TOPIC") or "").strip()
    notifier = Notifier(topic) if topic else None
    if notifier:
        notifier.load(state.get("outbox") or [])
        pb.notifier = notifier

    pb.update(result, feed, now)
    settled = await pb.settle(feed, now)
    pb.weekly_check(now)
    print(f"[picks] activos {len(db.pending_nuri_picks())} · calificados ahora {settled} · confirmando {pb.confirming}")

    if notifier:
        async with httpx.AsyncClient(timeout=12.0) as client:
            await notifier.flush(client)
        print(f"[avisos] enviados {notifier.sent} · pendientes {len(notifier.dump())}")

    data = build_data(pb, result, feed, now, APP_VERSION, feed.sources(), next_minutes=15)
    (OUT / "data.enc").write_text(encrypt_json(data, CODE))
    state = {"hist": pb.hist, "seen": pb.seen, "outbox": notifier.dump() if notifier else [],
             "last_run": now, "version": APP_VERSION}
    save_state(OUT / "state.enc", state)
    print(f"[listo] {time.time() - t0:.1f} s · datos {len((OUT / 'data.enc').read_text()) // 1024} KB")


if __name__ == "__main__":
    asyncio.run(main())
