@echo off
rem Грибница — запуск одной командой (Windows): двойной клик по run.bat или run.bat 8001
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" set PORT=8000
where py >nul 2>nul && (set PY=py) || (set PY=python)
if not exist .venv (%PY% -m venv .venv || (echo Нужен Python 3.9+: https://www.python.org/downloads/ & pause & exit /b 1))
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt || (pause & exit /b 1)
python -m mycelium.pipeline || (pause & exit /b 1)
echo Если порт %PORT% занят, запустите: run.bat 8001
python -m mycelium.serve --port %PORT%
pause
