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

from tickview.i18n import N_, tr
from tickview.indicators.api import API_VERSION, CATEGORIES, Indicator
from tickview.indicators.builtin import BUILTIN_INDICATORS
from tickview.indicators.cot import COT_INDICATORS
from tickview.indicators.tpo import TPO_INDICATORS

logger = logging.getLogger(__name__)

# Adresář vlastních indikátorů a balíček, pod kterým se importují
CUSTOM_DIR = Path(__file__).parent / "custom"
CUSTOM_PACKAGE = "tickview.indicators.custom"

# Chyba načtení: šablona zprávy (označená N_) a hodnoty zástupných symbolů – zpráva
# se přeloží až při zobrazení (registr se plní jednou za běh, jazyk se může změnit)
LoadError = tuple[str, dict]

# Načtené indikátory (id -> indikátor) a chyby načtení (adresář -> chyba)
_registry: dict[str, Indicator] = {}
_errors: dict[str, LoadError] = {}
_loaded = False


def _format_error(error: LoadError, translate: bool = True) -> str:
    """Text chyby načtení ze šablony a hodnot.

    Parametry:
        error: (šablona, hodnoty zástupných symbolů).
        translate: přeložit do jazyka rozhraní (False = česky do logu).
    """
    template, values = error
    return (tr(template) if translate else template).format(**values)


def _check(indicator, origin: str) -> Optional[LoadError]:
    """Ověří načtený indikátor; vrátí chybu (šablona, hodnoty), nebo None (indikátor je v pořádku).

    Parametry:
        indicator: objekt z INDICATOR.
        origin: odkud pochází (pro zprávu).
    """
    if not isinstance(indicator, Indicator):
        return N_("{origin}: INDICATOR musí být instance Indicator."), {"origin": origin}
    if indicator.api_version != API_VERSION:
        return (N_("{origin}: indikátor je pro rozhraní verze {version}, TickView má {api_version}."),
                {"origin": origin, "version": indicator.api_version, "api_version": API_VERSION})
    if not indicator.id:
        return N_("{origin}: indikátor nemá id."), {"origin": origin}
    if indicator.id in _registry:
        return N_("{origin}: id {id} už používá jiný indikátor."), {"origin": origin, "id": indicator.id}
    if indicator.category not in CATEGORIES:
        return N_("{origin}: neznámá kategorie {category}."), {"origin": origin, "category": indicator.category}
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
            _errors[directory.name] = (N_("{origin}: chyba importu – {error}"), {"origin": origin, "error": exc})
            logger.exception("Indikátor %s se nepodařilo načíst", origin)
            continue
        indicator = getattr(module, "INDICATOR", None)
        if error := _check(indicator, origin):
            _errors[directory.name] = error
            logger.error("Indikátor %s odmítnut: %s", origin, _format_error(error, translate=False))
            continue
        _registry[indicator.id] = indicator


def _ensure_loaded() -> None:
    # Registr se plní líně při prvním použití (import modulu nic nenačítá)
    global _loaded
    if _loaded:
        return
    _loaded = True
    # Vestavěné indikátory (pandas-ta), TPO a indikátory COT reportu, pak vlastní
    for indicator in BUILTIN_INDICATORS + TPO_INDICATORS + COT_INDICATORS:
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
    """Chyby načtení vlastních indikátorů (adresář -> zpráva v jazyce rozhraní) pro stavovou lištu."""
    _ensure_loaded()
    return {directory: _format_error(error) for directory, error in _errors.items()}
