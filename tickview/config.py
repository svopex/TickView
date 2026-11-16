"""Cesty a konfigurace aplikace TickView.

Konfigurace je v YAML souboru config.yaml v kořenu projektu (šablona je
config.example.yaml – tu udržujeme aktuální, uživatel si ji zkopíruje na
config.yaml a doplní hodnoty). Obsah config.yaml se necommituje (může obsahovat
API klíče).
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

import yaml

# Kořen projektu (složka o úroveň výš nad balíkem tickview)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Runtime data (cache databáze) nejsou verzovány
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "tickview.db"

# Konfigurace v kořenu projektu (šablona je config.example.yaml)
CONFIG_FILE = PROJECT_ROOT / "config.yaml"


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


def update_config_section(section: str, values: dict, path: Optional[Path] = None) -> None:
    """Zapíše hodnoty do sekce config.yaml a zachová zbytek souboru včetně komentářů.

    Existující klíče sekce přepíše na místě (i s komentářem za hodnotou),
    chybějící klíče doplní na konec sekce, chybějící sekci přidá na konec
    souboru. Kdyby úprava textu nedala platný YAML se zapsanými hodnotami,
    soubor se uloží celý znovu (komentáře se pak ztratí).

    Parametry:
        section: název sekce na nejvyšší úrovni (např. "alpaca").
        values: klíče a hodnoty (řetězce/čísla) k zapsání.
        path: cesta ke konfiguraci (výchozí config.yaml v kořenu projektu).
    """
    path = path or CONFIG_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    # JSON řetězec/číslo je platný YAML skalár (uvozovky ošetří i speciální znaky)
    rendered = {key: json.dumps(value, ensure_ascii=False) for key, value in values.items()}

    # Hlavička sekce na nejvyšší úrovni, např. "alpaca:" (s volitelným komentářem)
    header = re.compile(rf"^{re.escape(section)}\s*:\s*(#.*)?$")
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{section}:")
        lines.extend(f"  {key}: {value}" for key, value in rendered.items())
    else:
        # Tělo sekce = následující prázdné nebo odsazené řádky
        end = start + 1
        last_body = start
        while end < len(lines) and (not lines[end].strip() or lines[end][:1].isspace()):
            if lines[end].strip():
                last_body = end
            end += 1
        missing = []
        for key, value in rendered.items():
            # Řádek "  klíč: hodnota  # komentář" – hodnota v uvozovkách nebo bez '#'
            key_line = re.compile(
                rf"^(\s+{re.escape(key)}\s*:)\s*(?:\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^#]*?)(\s+#.*)?$"
            )
            for index in range(start + 1, end):
                match = key_line.match(lines[index])
                if match:
                    lines[index] = f"{match.group(1)} {value}{match.group(2) or ''}"
                    break
            else:
                missing.append(f"  {key}: {value}")
        lines[last_body + 1:last_body + 1] = missing

    text = "\n".join(lines) + "\n"
    # Pojistka: výsledek musí být platný YAML a obsahovat zapsané hodnoty
    try:
        parsed = yaml.safe_load(text) or {}
        valid = all((parsed.get(section) or {}).get(key) == value for key, value in values.items())
    except yaml.YAMLError:
        valid = False
    if not valid:
        config = load_config()
        config.setdefault(section, {}).update(values)
        text = yaml.dump(config, sort_keys=False, allow_unicode=True)
    _write_private(path, text)


def _write_private(path: Path, text: str) -> None:
    """Zapíše soubor; nový soubor vytvoří s právy jen pro vlastníka (obsahuje API klíče).

    Práva 0o600 se uplatní jen při vytvoření, existujícímu souboru zůstanou.
    """
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
        file.write(text)
