"""Cesty a základní konfigurace aplikace TickView."""

import json
from pathlib import Path

# Kořen projektu (složka o úroveň výš nad balíkem tickview)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Runtime data nejsou verzovány (viz .gitignore): cache DB i konfigurace s klíči
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "tickview.db"

# Lokální konfigurační soubor pro API klíče (např. Alpaca v kroku 7).
# Nikdy se necommituje – klíče v kódu ani v repozitáři nesmí být.
CONFIG_FILE = DATA_DIR / "config.local.json"


def ensure_dirs() -> None:
    """Vytvoří runtime složky, pokud neexistují."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Načte lokální konfiguraci (API klíče); chybí-li soubor, vrátí prázdný slovník."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except ValueError:
            # Poškozený JSON: považujeme za prázdnou konfiguraci
            return {}
    return {}


def save_config(config: dict) -> None:
    """Uloží lokální konfiguraci (API klíče) na disk."""
    ensure_dirs()
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
