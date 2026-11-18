"""Doinstaluje závislosti z requirements.txt, jen když se soubor od poslední instalace změnil.

Volají ho spouštěcí skripty run.sh a run-windows.bat Pythonem z .venv, takže
pip instaluje do virtuálního prostředí. Nejdřív ověří, že .venv má dost nový
Python. Otisk (SHA-256) requirements.txt poslední úspěšné instalace je
v .venv/.requirements.sha256.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
STAMP = PROJECT_ROOT / ".venv" / ".requirements.sha256"
# Nejstarší podporovaný Python (pandas-ta vyžaduje 3.12); stejné minimum hledá run-windows.bat
MIN_PYTHON = (3, 12)


def main() -> int:
    """Ověří verzi Pythonu, porovná otisk, případně spustí pip install; vrací 0 = v pořádku."""
    # Stará .venv se starším Pythonem by skončila nesrozumitelnou chybou pipu
    if sys.version_info < MIN_PYTHON:
        found = ".".join(map(str, sys.version_info[:2]))
        needed = ".".join(map(str, MIN_PYTHON))
        print(f"Virtuální prostředí .venv má Python {found}, TickView potřebuje {needed} nebo novější.")
        print("Nainstalujte novější Python, smažte složku .venv a spusťte skript znovu.")
        return 1

    current_hash = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()

    # Otisk se shoduje – závislosti jsou aktuální
    if STAMP.exists() and STAMP.read_text().strip() == current_hash:
        return 0

    print("Instaluji závislosti…", flush=True)
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    if result.returncode != 0:
        return result.returncode

    # Otisk až po úspěšné instalaci, jinak se instalace příště zkusí znovu
    STAMP.write_text(current_hash + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
