"""Registr indikátorů: vestavěné + vlastní z adresáře tickview/indicators/custom/.

Vlastní indikátor má vlastní adresář custom/<název>/ se souborem
indicator.py, který definuje INDICATOR (instanci třídy odvozené od
Indicator). Ostatní soubory adresáře (Pine verze, poznámky, data) loader
ignoruje. Chyba importu nebo nesouhlasná verze rozhraní jednoho indikátoru
se zapíše do logu a nic dalšího neovlivní.
"""

import importlib
import logging
from pathlib import Path
from typing import Optional

from tickview.indicators.api import API_VERSION, CATEGORIES, Indicator
from tickview.indicators.builtin import BUILTIN_INDICATORS
from tickview.indicators.cot import COT_INDICATORS

logger = logging.getLogger(__name__)

# Adresář vlastních indikátorů a balíček, pod kterým se importují
CUSTOM_DIR = Path(__file__).parent / "custom"
CUSTOM_PACKAGE = "tickview.indicators.custom"

# Načtené indikátory (id -> indikátor) a chyby načtení (adresář -> zpráva)
_registry: dict[str, Indicator] = {}
_errors: dict[str, str] = {}
_loaded = False


def _check(indicator, origin: str) -> Optional[str]:
    """Ověří načtený indikátor; vrátí popis chyby, nebo None (indikátor je v pořádku).

    Parametry:
        indicator: objekt z INDICATOR.
        origin: odkud pochází (pro zprávu).
    """
    if not isinstance(indicator, Indicator):
        return f"{origin}: INDICATOR musí být instance Indicator."
    if indicator.api_version != API_VERSION:
        return f"{origin}: indikátor je pro rozhraní verze {indicator.api_version}, TickView má {API_VERSION}."
    if not indicator.id:
        return f"{origin}: indikátor nemá id."
    if indicator.id in _registry:
        return f"{origin}: id {indicator.id} už používá jiný indikátor."
    if indicator.category not in CATEGORIES:
        return f"{origin}: neznámá kategorie {indicator.category}."
    return None


def _load_custom() -> None:
    """Načte vlastní indikátory z podadresářů CUSTOM_DIR (abecedně)."""
    if not CUSTOM_DIR.is_dir():
        return
    for directory in sorted(CUSTOM_DIR.iterdir()):
        # Indikátor = adresář s indicator.py (pomocné adresáře jako __pycache__ přeskočíme)
        if not directory.is_dir() or directory.name.startswith(("_", ".")) or not (directory / "indicator.py").exists():
            continue
        origin = f"custom/{directory.name}"
        try:
            module = importlib.import_module(f"{CUSTOM_PACKAGE}.{directory.name}.indicator")
        except Exception as exc:
            _errors[directory.name] = f"{origin}: chyba importu – {exc}"
            logger.exception("Indikátor %s se nepodařilo načíst", origin)
            continue
        indicator = getattr(module, "INDICATOR", None)
        if error := _check(indicator, origin):
            _errors[directory.name] = error
            logger.error("Indikátor %s odmítnut: %s", origin, error)
            continue
        _registry[indicator.id] = indicator


def _ensure_loaded() -> None:
    # Registr se plní líně při prvním použití (import modulu nic nenačítá)
    global _loaded
    if _loaded:
        return
    _loaded = True
    # Vestavěné indikátory (pandas-ta) a indikátory COT reportu, pak vlastní
    for indicator in BUILTIN_INDICATORS + COT_INDICATORS:
        _registry[indicator.id] = indicator
    _load_custom()


def get_indicator(kind: str) -> Optional[Indicator]:
    """Indikátor podle id, nebo None (neznámý / nenačtený vlastní indikátor)."""
    _ensure_loaded()
    return _registry.get(kind)


def all_indicators() -> dict[str, Indicator]:
    """Všechny načtené indikátory (id -> indikátor)."""
    _ensure_loaded()
    return dict(_registry)


def load_errors() -> dict[str, str]:
    """Chyby načtení vlastních indikátorů (adresář -> zpráva) pro stavovou lištu."""
    _ensure_loaded()
    return dict(_errors)
