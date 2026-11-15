"""Cesty a konfigurace aplikace TickView.

Konfigurace je v YAML souboru config.yaml v kořenu projektu (šablona je
config.example.yaml – tu udržujeme aktuální, uživatel si ji zkopíruje na
config.yaml a doplní hodnoty). Obsah config.yaml se necommituje (může obsahovat
API klíče).
"""

import json
from pathlib import Path

import yaml

# Kořen projektu (složka o úroveň výš nad balíkem tickview)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Runtime data (cache databáze) nejsou verzovány
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "tickview.db"

# Konfigurace v kořenu projektu (config.yaml) a její šablona (config.example.yaml)
CONFIG_FILE = PROJECT_ROOT / "config.yaml"
EXAMPLE_CONFIG_FILE = PROJECT_ROOT / "config.example.yaml"


def ensure_dirs() -> None:
    """Vytvoří runtime složky, pokud neexistují."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Načte config.yaml; chybí-li, vrátí prázdný slovník (výchozí hodnoty)."""
    if CONFIG_FILE.exists():
        try:
            return yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            # Poškozený YAML: považujeme za prázdnou konfiguraci
            return {}
    return {}


def save_config(config: dict) -> None:
    """Uloží konfiguraci do config.yaml (používá se při ukládání klíčů z UI)."""
    CONFIG_FILE.write_text(
        yaml.dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def load_json(path: Path) -> dict:
    """Pomocná funkce: načte JSON soubor; chybí-li nebo je poškozený, vrátí {}."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except ValueError:
            return {}
    return {}
