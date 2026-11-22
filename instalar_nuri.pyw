"""
NURI · 누리 — instalar_nuri.pyw
===============================
Instalador y arranque sin consola. Lo abre el ícono NURI la primera vez:
  1. Crea el entorno de Python en %LOCALAPPDATA%\\NURI (fuera de OneDrive).
  2. Instala el motor (numpy, scipy, FastAPI…), sólo la primera vez.
  3. Crea los accesos directos NURI en el Escritorio y en el menú Inicio.
  4. Abre NURI.
Si ya está instalado, sólo abre NURI.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "NURI"
VENV = LOCAL / "venv"
PYW = VENV / "Scripts" / "pythonw.exe"
PY = VENV / "Scripts" / "python.exe"
MARK = LOCAL / "deps_ok.txt"
LOG = LOCAL / "instalacion.log"
REQ = APP / "requirements.txt"
NO_WINDOW = 0x08000000
DETACHED = 0x00000008


def needs_install() -> bool:
    try:
        return not (PYW.exists() and MARK.exists() and MARK.read_text(encoding="utf-8") == REQ.read_text(encoding="utf-8"))
    except OSError:
        return True


def run(cmd: list[str]) -> int:
    LOCAL.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(cmd)}\n")
        f.flush()
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, creationflags=NO_WINDOW).returncode


def make_shortcuts() -> None:
    script = APP / "backend" / "nuri_app.py"
    icon = APP / "backend" / "nuri.ico"
    ps = (
        "$w = New-Object -ComObject WScript.Shell;"
        "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {"
        "  $s = $w.CreateShortcut((Join-Path $d 'NURI.lnk'));"
        f"  $s.TargetPath = '{PYW}'; $s.Arguments = '\"{script}\"';"
        f"  $s.WorkingDirectory = '{APP / 'backend'}'; $s.IconLocation = '{icon},0';"
        "  $s.Description = 'NURI - Live Quant Radar'; $s.Save() }"
    )
    run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps])
    try:
        (APP / "backend" / ".atajos_ok").unlink(missing_ok=True)  # nuri_app los revisa otra vez
    except OSError:
        pass


def launch() -> None:
    subprocess.Popen([str(PYW), str(APP / "backend" / "nuri_app.py")], cwd=str(APP / "backend"),
                     creationflags=DETACHED | NO_WINDOW, close_fds=True)


def install(set_status) -> None:
    base_py = Path(sys.executable).with_name("python.exe")
    if not base_py.exists():
        base_py = Path(sys.executable)
    if not PY.exists():
        set_status("Preparando Python…")
        if run([str(base_py), "-m", "venv", str(VENV)]) != 0:
            raise RuntimeError("No se pudo crear el entorno de Python.")
    set_status("Instalando el motor matemático (1–3 minutos, sólo esta vez)…")
    if run([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQ)]) != 0:
        raise RuntimeError("Falló la instalación. Revisa tu conexión a internet y vuelve a abrir NURI.")
    MARK.write_text(REQ.read_text(encoding="utf-8"), encoding="utf-8")
    set_status("Creando el ícono de NURI…")
    make_shortcuts()
    set_status("Abriendo NURI…")
    launch()


def main() -> None:
    if not needs_install():
        launch()
        return
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:  # Python sin tkinter: instalamos sin ventana
        try:
            install(lambda t: None)
        except Exception as exc:
            LOCAL.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"\nERROR: {exc!r}\n")
        return

    root = tk.Tk()
    root.title("NURI · Instalando")
    root.configure(bg="#090B10")
    root.resizable(False, False)
    w, h = 440, 190
    root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    try:
        root.iconbitmap(str(APP / "backend" / "nuri.ico"))
    except Exception:
        pass
    tk.Label(root, text="NURI", fg="#E7EAF1", bg="#090B10", font=("Segoe UI Semibold", 18)).pack(pady=(22, 0))
    tk.Label(root, text="누리 · LIVE QUANT RADAR", fg="#5E6779", bg="#090B10", font=("Segoe UI", 8)).pack()
    status = tk.StringVar(value="Empezando…")
    tk.Label(root, textvariable=status, fg="#9AA3B5", bg="#090B10", font=("Segoe UI", 10), wraplength=400).pack(pady=(16, 8))
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("N.Horizontal.TProgressbar", troughcolor="#121620", background="#10B981", bordercolor="#1E2433",
                    lightcolor="#10B981", darkcolor="#10B981")
    bar = ttk.Progressbar(root, mode="indeterminate", length=360, style="N.Horizontal.TProgressbar")
    bar.pack()
    bar.start(12)

    def set_status(t: str) -> None:
        root.after(0, status.set, t)

    def worker() -> None:
        try:
            install(set_status)
            root.after(2500, root.destroy)
        except Exception as exc:
            set_status(f"{exc}\nDetalle en {LOG}")
            root.after(0, bar.stop)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
