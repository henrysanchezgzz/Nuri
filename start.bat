@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
title NURI - Live Quant Radar

echo.
echo   NURI - LIVE QUANT RADAR
echo   ----------------------------------
echo.

rem ---- Python 3.10+ ----------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [NURI] No encontre Python. Instala Python 3.11 o 3.12 desde https://www.python.org/downloads/
  echo        y marca la casilla "Add python.exe to PATH". Luego vuelve a abrir start.bat
  pause
  exit /b 1
)

rem ---- Entorno virtual + dependencias ---------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [NURI] Creando entorno virtual .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [NURI] No se pudo crear el entorno virtual.
    pause
    exit /b 1
  )
)
set "VPY=%~dp0.venv\Scripts\python.exe"

if not exist ".venv\deps_ok.txt" (
  echo [NURI] Instalando dependencias ^(solo la primera vez, 1-3 min^) ...
  "%VPY%" -m pip install --upgrade pip
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [NURI] Fallo la instalacion de dependencias. Revisa tu conexion a internet.
    pause
    exit /b 1
  )
  echo ok> ".venv\deps_ok.txt"
)

rem ---- Configuracion --------------------------------------------------------
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [NURI] Se creo .env ^(modo DEMO hasta que agregues ODDS_API_KEY^)
)

rem ---- Interfaz (ya viene compilada; solo se compila si falta) --------------
if not exist "frontend\dist\index.html" (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [NURI] Falta frontend\dist y no hay Node.js. Instala Node LTS desde https://nodejs.org
    pause
    exit /b 1
  )
  echo [NURI] Compilando interfaz ...
  pushd frontend
  call npm install
  call npm run build
  popd
)

rem ---- Arranque (sin ventana negra; abre NURI como app) -----------------
echo [NURI] Abriendo NURI ...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0backend\nuri_app.py"
exit /b 0
