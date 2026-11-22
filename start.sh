#!/usr/bin/env bash
# NURI · 누리 — arranque en 1 clic para macOS / Linux
set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "  NURI  ·  누리  ·  LIVE QUANT RADAR"
echo "  ----------------------------------"
echo ""

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "[NURI] No encontré Python 3.10+. Instálalo desde https://www.python.org/downloads/"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "[NURI] Creando entorno virtual .venv ..."
  "$PY" -m venv .venv
fi
VPY="$(pwd)/.venv/bin/python"

if [ ! -f ".venv/deps_ok.txt" ]; then
  echo "[NURI] Instalando dependencias (sólo la primera vez) ..."
  "$VPY" -m pip install --upgrade pip
  "$VPY" -m pip install -r requirements.txt
  echo ok > .venv/deps_ok.txt
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[NURI] Se creó .env (modo DEMO hasta que agregues ODDS_API_KEY)"
fi

if [ ! -f "frontend/dist/index.html" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "[NURI] Falta frontend/dist y no hay Node.js. Instala Node LTS desde https://nodejs.org"
    exit 1
  fi
  echo "[NURI] Compilando interfaz ..."
  (cd frontend && npm install && npm run build)
fi

echo "[NURI] Abriendo NURI ..."
cd backend
exec "$VPY" nuri_app.py
