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

# Závislosti se doinstalují jen při změně requirements.txt (společné s run-windows.bat)
.venv/bin/python tools/ensure_requirements.py

# Spuštění aplikace; všechny argumenty skriptu jdou přímo do main.py
exec .venv/bin/python main.py "$@"
