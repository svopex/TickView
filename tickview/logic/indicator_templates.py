"""Šablony indikátorů (Indicator templates jako v TradingView).

Šablona je pojmenovaná sada indikátorů grafu (typ, parametry, barva,
viditelnost), volitelně se symbolem a timeframem. Použití šablony nahradí
indikátory aktivního grafu. Oblíbené šablony (hvězdička) mají tlačítko
v horní liště, nabídka ukazuje naposledy použité. Ukládají se jako JSON
seznam do nastavení pod klíčem INDICATOR_TEMPLATES_KEY; modul obsahuje
jen čisté funkce.
"""

import json
import uuid

from tickview.i18n import tr
from tickview.indicators.compute import indicator_title, normalize_indicators
from tickview.logic.styles import TIMEFRAMES, clean_name

# Klíč nastavení se seznamem šablon indikátorů
INDICATOR_TEMPLATES_KEY = "indicator_templates"
# Kolik naposledy použitých šablon ukazuje nabídka
RECENT_TEMPLATES = 8


def normalize_indicator_template(data) -> dict | None:
    """Zkontroluje jednu šablonu; neplatnou (bez názvu) vrátí jako None.

    Parametry:
        data: {"name", "indicators": [...], "symbol"?, "timeframe"?, "favorite"?, "used_at"?}.
    """
    if not isinstance(data, dict):
        return None
    name = clean_name(data.get("name"))
    if not name:
        return None
    symbol = data.get("symbol")
    timeframe = data.get("timeframe")
    used_at = data.get("used_at")
    return {
        "name": name,
        "indicators": normalize_indicators(data.get("indicators")),
        # Symbol „zdroj:symbol“ a timeframe jen při zapnutém „Remember symbol / interval“
        "symbol": symbol if isinstance(symbol, str) and ":" in symbol else None,
        "timeframe": timeframe if timeframe in TIMEFRAMES else None,
        "favorite": bool(data.get("favorite")),
        "used_at": int(used_at) if isinstance(used_at, (int, float)) and not isinstance(used_at, bool) else 0,
    }


def load_indicator_templates(raw: str | None) -> list[dict]:
    """Načte šablony z JSON nastavení seřazené podle názvu; poškozené vynechá."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    templates = [template for item in (data if isinstance(data, list) else []) if (template := normalize_indicator_template(item))]
    return sorted(templates, key=lambda template: template["name"].lower())


def find_indicator_template(templates: list[dict], name: str) -> dict | None:
    """Šablona daného názvu (bez ohledu na velikost písmen), nebo None."""
    wanted = (name or "").lower()
    return next((template for template in templates if template["name"].lower() == wanted), None)


def save_indicator_template(
    templates: list[dict],
    name: str,
    indicators: list[dict],
    symbol: str | None = None,
    timeframe: str | None = None,
    now_ms: int = 0,
) -> list[dict]:
    """Vrátí nový seznam šablon s uloženou (stejný název = přepsanou) šablonou.

    Přepsaná šablona si ponechá hvězdičku (oblíbenou).

    Parametry:
        templates: dosavadní šablony.
        name: název (očistí se přes clean_name; neplatný vyhodí ValueError).
        indicators: aktivní indikátory grafu.
        symbol, timeframe: zapamatovaný symbol a timeframe (None = nepamatovat).
        now_ms: čas uložení (řazení naposledy použitých).
    """
    clean = clean_name(name)
    if not clean:
        raise ValueError(tr("Neplatný název šablony."))
    previous = find_indicator_template(templates, clean)
    template = normalize_indicator_template(
        {
            "name": clean,
            "indicators": indicators,
            "symbol": symbol,
            "timeframe": timeframe,
            "favorite": bool(previous and previous["favorite"]),
            "used_at": now_ms,
        }
    )
    result = [item for item in templates if item["name"].lower() != clean.lower()] + [template]
    return sorted(result, key=lambda item: item["name"].lower())


def delete_indicator_template(templates: list[dict], name: str) -> list[dict]:
    """Vrátí nový seznam šablon bez šablony daného názvu."""
    return [template for template in templates if template["name"].lower() != (name or "").lower()]


def update_indicator_template(templates: list[dict], name: str, **changes) -> list[dict]:
    """Vrátí nový seznam šablon se změněnými poli šablony (favorite, used_at)."""
    return [
        normalize_indicator_template({**template, **changes}) if template["name"].lower() == (name or "").lower() else template
        for template in templates
    ]


def recent_indicator_templates(templates: list[dict], limit: int = RECENT_TEMPLATES) -> list[dict]:
    """Naposledy použité (nebo uložené) šablony, nejnovější první."""
    return sorted(templates, key=lambda template: -template["used_at"])[:limit]


def favorite_indicator_templates(templates: list[dict]) -> list[dict]:
    """Oblíbené šablony (tlačítka v horní liště) v abecedním pořadí."""
    return [template for template in templates if template["favorite"]]


def template_indicators(template: dict) -> list[dict]:
    """Indikátory šablony jako nové instance (nová id, aby se nepletly s jinými grafy)."""
    return [{**spec, "id": uuid.uuid4().hex[:8]} for spec in template["indicators"]]


def describe_indicator_template(template: dict) -> str:
    """Popis šablony pro nabídku: názvy indikátorů, případně symbol a timeframe."""
    parts = [", ".join(indicator_title(spec) for spec in template["indicators"]) or tr("Bez indikátorů")]
    if template.get("symbol"):
        parts.append(template["symbol"].partition(":")[2])
    if template.get("timeframe"):
        parts.append(template["timeframe"])
    return " · ".join(parts)
