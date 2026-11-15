#!/usr/bin/env bash
# Spuštění TickView na macOS/Linux: připraví .venv, doinstaluje závislosti
# a předá případné parametry do main.py (např. ./run.sh --port 8090 --open).

set -euo pipefail

# Pracujeme vždy v kořenu projektu, i když skript spustíme odjinud
cd "$(dirname "$0")"

# Virtuální prostředí vytvoříme při prvním spuštění
if [ ! -x .venv/bin/python ]; then
    echo "Vytvářím virtuální prostředí .venv…"
    python3 -m venv .venv
fi

# Otisk requirements.txt – závislosti instalujeme jen při jejich změně
requirements_hash() {
    .venv/bin/python - <<'PY'
import hashlib
from pathlib import Path

print(hashlib.sha256(Path("requirements.txt").read_bytes()).hexdigest())
PY
}

stamp=".venv/.requirements.sha256"
current_hash="$(requirements_hash)"
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$current_hash" ]; then
    echo "Instaluji závislosti…"
    .venv/bin/python -m pip install -r requirements.txt
    printf '%s\n' "$current_hash" > "$stamp"
fi

# Spuštění aplikace; všechny argumenty skriptu jdou přímo do main.py
exec .venv/bin/python main.py "$@"
