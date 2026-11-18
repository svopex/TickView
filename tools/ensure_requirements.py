"""Doinstaluje závislosti z requirements.txt, jen když se soubor od poslední instalace změnil.

Volají ho spouštěcí skripty run.sh a run-windows.bat Pythonem z .venv, takže
pip instaluje do virtuálního prostředí. Otisk (SHA-256) požadavků poslední
úspěšné instalace je v .venv/.requirements.sha256.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
STAMP = PROJECT_ROOT / ".venv" / ".requirements.sha256"


def requirements_hash() -> str:
    """Otisk požadavků bez komentářů a prázdných řádků, aby úprava komentáře nespustila pip."""
    requirements = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        # Část za # je komentář; prázdné řádky se vynechají
        requirement = line.split("#", 1)[0].strip()
        if requirement:
            requirements.append(requirement)
    return hashlib.sha256("\n".join(requirements).encode("utf-8")).hexdigest()


def main() -> int:
    """Porovná otisk, případně spustí pip install; vrací návratový kód pipu (0 = v pořádku)."""
    current_hash = requirements_hash()

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
