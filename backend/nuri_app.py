"""
NURI · 누리 — nuri_app.py
=========================
Lanzador de escritorio. Lo abre el ícono "NURI" del escritorio con pythonw.exe
(sin ventana negra):

  1. Si NURI ya está abierto en esta versión → sólo abre la ventana.
  2. Si hay una versión vieja corriendo → la apaga y arranca la nueva.
  3. Arranca el servidor en segundo plano y abre NURI como ventana de app
     (Brave → Chrome → Edge → navegador predeterminado).
El servidor se apaga solo cuando cierras la ventana y pasan unos minutos sin uso.

Desde la 3.2 NURI corre solo en la nube: este lanzador sólo abre la app (GitHub Pages).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
LOG = BASE / "nuri.log"


def _ensure_streams() -> None:
    """pythonw no tiene consola: mandamos todo a nuri.log."""
    if sys.stdout is None or sys.stderr is None or "pythonw" in Path(sys.executable).name.lower():
        try:
            if LOG.exists() and LOG.stat().st_size > 2_000_000:
                LOG.unlink()
        except OSError:
            pass
        f = open(LOG, "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f


_ensure_streams()
os.chdir(BASE)
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from version import APP_VERSION  # noqa: E402

PORT = int(os.getenv("NURI_APP_PORT") or 8765)
URL = f"http://127.0.0.1:{PORT}"
# NURI ya corre solo en la nube (GitHub Actions) y la app vive en GitHub Pages: el ícono de la laptop
# sólo abre esa app. Con NURI_LOCAL=1 en .env vuelve el modo viejo (servidor y motor en esta laptop).
CLOUD_URL = os.getenv("NURI_CLOUD_URL") or "https://henrysanchezgzz.github.io/Nuri/"
LOCAL_MODE = (os.getenv("NURI_LOCAL") or "").strip().lower() in ("1", "si", "sí", "true", "yes")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DETACHED = 0x00000008 if os.name == "nt" else 0


def health() -> dict | None:
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def shutdown_running() -> None:
    try:
        req = urllib.request.Request(URL + "/api/shutdown", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass
    for _ in range(40):
        if health() is None:
            return
        time.sleep(0.25)


def find_browser() -> str | None:
    env = os.environ
    cands = []
    for root in (env.get("PROGRAMFILES"), env.get("PROGRAMFILES(X86)"), env.get("LOCALAPPDATA")):
        if not root:
            continue
        cands += [Path(root) / "BraveSoftware/Brave-Browser/Application/brave.exe",
                  Path(root) / "Google/Chrome/Application/chrome.exe",
                  Path(root) / "Microsoft/Edge/Application/msedge.exe"]
    for c in cands:
        if c.exists():
            return str(c)
    return None


def open_window(url: str = URL, size: str = "1360,900") -> None:
    b = find_browser()
    if b:
        try:
            subprocess.Popen([b, f"--app={url}", f"--window-size={size}"], creationflags=DETACHED | NO_WINDOW,
                             close_fds=True)
            return
        except Exception as exc:
            print(f"[NURI] no se pudo abrir {b}: {exc!r}")
    webbrowser.open(url)


QUIET = "--quiet" in sys.argv  # reinicio por actualización: la ventana ya está abierta


def _open_when_ready() -> None:
    for _ in range(120):
        if health():
            if not QUIET:
                open_window()
            return
        time.sleep(0.5)
    print("[NURI] el servidor no respondió a tiempo")


def ensure_shortcuts() -> None:
    """Crea (o repara) los accesos directos de NURI en el Escritorio y en el menú Inicio."""
    if os.name != "nt":
        return
    marker = BASE / ".atajos_ok"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == APP_VERSION:
        return
    target = str(Path(sys.executable).with_name("pythonw.exe"))
    script = str(BASE / "nuri_app.py")
    icon = str(BASE / "nuri.ico")
    ps = (
        "$w = New-Object -ComObject WScript.Shell;"
        "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {"
        "  $s = $w.CreateShortcut((Join-Path $d 'NURI.lnk'));"
        f"  $s.TargetPath = '{target}'; $s.Arguments = '\"{script}\"';"
        f"  $s.WorkingDirectory = '{BASE}'; $s.IconLocation = '{icon},0';"
        "  $s.Description = 'NURI - Live Quant Radar'; $s.Save() }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       creationflags=NO_WINDOW, timeout=30, check=False, capture_output=True)
        marker.write_text(APP_VERSION, encoding="utf-8")
    except Exception as exc:
        print(f"[NURI] no se pudieron crear los accesos directos: {exc!r}")


def ensure_autostart() -> None:
    """Con NURI_AUTOSTART=1 en .env, NURI arranca solo (sin ventana) al iniciar Windows para seguir
    buscando picks y mandarlos al celular. Sin esa opción, se quita el acceso de inicio."""
    if os.name != "nt":
        return
    want = (os.getenv("NURI_AUTOSTART") or "").strip().lower() in ("1", "si", "sí", "true", "yes")
    target = str(Path(sys.executable).with_name("pythonw.exe"))
    script = str(BASE / "nuri_app.py")
    icon = str(BASE / "nuri.ico")
    if want:
        ps = ("$w = New-Object -ComObject WScript.Shell;"
              "$s = $w.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'NURI (segundo plano).lnk'));"
              f"$s.TargetPath = '{target}'; $s.Arguments = '\"{script}\" --quiet';"
              f"$s.WorkingDirectory = '{BASE}'; $s.IconLocation = '{icon},0';"
              "$s.Description = 'NURI - busca picks en segundo plano'; $s.Save()")
    else:
        ps = ("$f = Join-Path ([Environment]::GetFolderPath('Startup')) 'NURI (segundo plano).lnk';"
              "if (Test-Path $f) { Remove-Item $f }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       creationflags=NO_WINDOW, timeout=30, check=False, capture_output=True)
    except Exception as exc:
        print(f"[NURI] arranque automático: {exc!r}")


def main() -> None:
    print(f"\n[NURI] {time.strftime('%Y-%m-%d %H:%M:%S')} · lanzador v{APP_VERSION} · {URL}")
    if not LOCAL_MODE:
        # modo nube: apaga el servidor local si sigue prendido y abre la app de la nube
        if health():
            print("[NURI] NURI ya corre en la nube: apagando el servidor local")
            shutdown_running()
        if not QUIET:
            open_window(CLOUD_URL, "460,940")
        return
    threading.Thread(target=ensure_shortcuts, daemon=True).start()
    threading.Thread(target=ensure_autostart, daemon=True).start()
    h = health()
    if h and h.get("version") == APP_VERSION:
        if not QUIET:
            open_window()
        return
    if h:
        print(f"[NURI] apagando versión anterior {h.get('version')}")
        shutdown_running()
    threading.Thread(target=_open_when_ready, daemon=True).start()
    import uvicorn
    from main import app

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning", log_config=None)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # cualquier error queda en nuri.log
        import traceback

        traceback.print_exc()
        print(f"[NURI] error fatal: {exc!r}")
