#!/usr/bin/env sh
# Грибница — запуск одной командой (macOS / Linux): ./run.sh   (порт можно задать: ./run.sh 8001)
set -e
cd "$(dirname "$0")"
PORT="${1:-8000}"
PY="$(command -v python3 || command -v python)"
[ -z "$PY" ] && { echo "Нужен Python 3.9+: https://www.python.org/downloads/"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt
python -m mycelium.pipeline
# если порт занят — берём следующий свободный
while python -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1', $PORT)) == 0 else 1)"; do
  echo "Порт $PORT занят, пробую $((PORT+1))"; PORT=$((PORT+1)); done
python -m mycelium.serve --port "$PORT"
