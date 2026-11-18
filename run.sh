#!/usr/bin/env bash
# Spuštění TickView na macOS/Linux: najde podporovaný Python (3.12–3.13),
# připraví .venv, doinstaluje závislosti a předá případné parametry do main.py
# (např. ./run.sh --port 8090 --open).

set -euo pipefail

# Pracujeme vždy v kořenu projektu, i když skript spustíme odjinud
cd "$(dirname "$0")"

# Podporovanou verzi ověřuje tools/python_supported.py (tam je i rozsah verzí)
supported() {
    "$1" tools/python_supported.py >/dev/null 2>&1
}

# .venv s nepodporovaným Pythonem (např. 3.14, když je to výchozí python3) se vytvoří znovu
if [ -x .venv/bin/python ] && ! supported .venv/bin/python; then
    echo "Virtuální prostředí .venv má nepodporovanou verzi Pythonu, vytvářím ho znovu…"
    rm -rf .venv
fi

# Virtuální prostředí vytvoříme při prvním spuštění; konkrétní verze mají
# přednost před python3, který může ukazovat na příliš novou verzi
if [ ! -x .venv/bin/python ]; then
    python=""
    for candidate in python3.13 python3.12 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && supported "$candidate"; then
            python="$candidate"
            break
        fi
    done
    if [ -z "$python" ]; then
        echo "Nenašel jsem Python 3.12 nebo 3.13 (Python 3.14 TickView zatím nepodporuje)."
        echo "macOS: brew install python@3.13, nebo instalátor z https://www.python.org/downloads/"
        echo "Linux: nainstalujte python3.13 nebo python3.12 (a balíček venv) z vaší distribuce."
        exit 1
    fi
    echo "Vytvářím virtuální prostředí .venv ($("$python" -V))…"
    "$python" -m venv .venv
fi

# Závislosti se doinstalují jen při změně requirements.txt (společné s run-windows.bat)
.venv/bin/python tools/ensure_requirements.py

# Spuštění aplikace; všechny argumenty skriptu jdou přímo do main.py
exec .venv/bin/python main.py "$@"
