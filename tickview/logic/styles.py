"""Barvy, styl grafu, šablony nastavení (Template) a oblíbené nástroje kreslení.

Šablona je pojmenovaná sada hodnot jednoho dialogu Settings (graf, typ
indikátoru, typ kresby). Šablony se ukládají jako JSON do tabulky nastavení
pod klíčem „templates.<rozsah>“; tento modul obsahuje jen čisté funkce
(kontrola hodnot, úpravy slovníku šablon), ukládání řeší UI přes cache.
"""

import json
import re

# Barva ve tvaru rgb(...) / rgba(...) – průhlednost je volitelná
_RGBA_PATTERN = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(\d*\.?\d+)\s*)?\)$",
    re.IGNORECASE,
)
# Barva ve tvaru #rrggbb
_HEX_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")

# Nejdelší povolený název šablony nebo seznamu symbolů (zobrazuje se v nabídce)
MAX_NAME_LENGTH = 40

# Výchozí styl svíček (dialog Settings grafu, „Apply defaults“): barvy těla,
# okrajů a knotů pro růst / pokles a obarvení podle předchozího závěru.
# Objem do stylu nepatří – drží ho samostatné nastavení show_volume
CHART_DEFAULTS: dict = {
    "color_by_prev_close": False,
    "body": True,
    "body_up": "#26a69a",
    "body_down": "#ef5350",
    "borders": True,
    "border_up": "#26a69a",
    "border_down": "#ef5350",
    "wick": True,
    "wick_up": "#26a69a",
    "wick_down": "#ef5350",
}

# Tovární styl kreseb (nová kresba bez výchozí šablony, „Apply defaults“)
DEFAULT_DRAWING_COLOR = "#2962ff"
DEFAULT_LINE_WIDTH = 1.5
DEFAULT_FONT_SIZE = 13
# Povolené šířky (px) a styly čar kreseb s popisky pro dialog Settings kresby
DRAWING_LINE_WIDTHS = {1: "1 px", 1.5: "1,5 px (výchozí)", 2: "2 px", 3: "3 px", 4: "4 px"}
DRAWING_LINE_STYLES = {"solid": "Plná", "dashed": "Přerušovaná", "dotted": "Tečkovaná"}


def clamp_font_size(value) -> int:
    """Velikost písma textové kresby v rozsahu 8–72 px (neplatná hodnota = výchozí)."""
    try:
        return max(8, min(72, int(value or DEFAULT_FONT_SIZE)))
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE


def normalize_drawing_style(values) -> dict:
    """Styl kresby (šablona, dialog, výchozí styl nových kreseb) s doplněnými výchozími hodnotami.

    Parametry:
        values: {"color", "options": {"width", "lineStyle", "fontSize"}} – může být neúplný.

    Vrací {"color", "options"} s platnou barvou, šířkou, stylem čáry a písmem.
    """
    source = values if isinstance(values, dict) else {}
    options = source.get("options") if isinstance(source.get("options"), dict) else {}
    width = options.get("width")
    line_style = options.get("lineStyle")
    return {
        "color": normalize_color(source.get("color")) or DEFAULT_DRAWING_COLOR,
        "options": {
            "width": width if width in DRAWING_LINE_WIDTHS else DEFAULT_LINE_WIDTH,
            "lineStyle": line_style if line_style in DRAWING_LINE_STYLES else "solid",
            "fontSize": clamp_font_size(options.get("fontSize")),
        },
    }


# Typy grafu jako v TradingView, po sekcích nabídky: (id, popisek, ikona)
CHART_TYPE_SECTIONS: list[list[tuple[str, str, str]]] = [
    [("bars", "Bars", "chart_bars"), ("candles", "Candles", "chart_candles"), ("hollow", "Hollow candles", "chart_hollow")],
    [("line", "Line", "chart_line"), ("line_markers", "Line with markers", "chart_line_markers"), ("step", "Step line", "chart_step")],
    [("area", "Area", "chart_area"), ("baseline", "Baseline", "chart_baseline")],
    [("columns", "Columns", "chart_columns"), ("highlow", "High-low", "chart_highlow")],
    [("heikin", "Heikin Ashi", "chart_heikin")],
]
# Typ grafu → (popisek, ikona)
CHART_TYPES: dict[str, tuple[str, str]] = {
    type_id: (label, icon) for section in CHART_TYPE_SECTIONS for type_id, label, icon in section
}
DEFAULT_CHART_TYPE = "candles"


def normalize_chart_type(value) -> str:
    """Vrátí platný typ grafu; neznámá hodnota (starší nastavení) = svíčky."""
    return value if value in CHART_TYPES else DEFAULT_CHART_TYPE


# Klíče stylu grafu, které nesou barvu (ostatní jsou přepínače)
CHART_COLOR_KEYS = ("body_up", "body_down", "border_up", "border_down", "wick_up", "wick_down")


def normalize_color(value) -> str | None:
    """Vrátí barvu v jednotném tvaru, jinak None (neplatný vstup z dialogu / nastavení).

    Plně krycí barva se vrací jako #rrggbb (malými písmeny), průhledná jako
    „rgba(r, g, b, a)“ s průhledností zaokrouhlenou na 2 desetinná místa –
    takový zápis přijímá CSS, canvas i Lightweight Charts.

    Parametry:
        value: barva #rrggbb nebo rgb()/rgba() (např. z výběru barev).
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _HEX_PATTERN.match(text):
        return text.lower()
    match = _RGBA_PATTERN.match(text)
    if not match:
        return None
    red, green, blue = (int(part) for part in match.group(1, 2, 3))
    if max(red, green, blue) > 255:
        return None
    alpha = float(match.group(4)) if match.group(4) is not None else 1.0
    if alpha > 1:
        return None
    alpha = round(alpha, 2)
    # Plná krytí ukládáme jako hex – kratší a stejný zápis jako výchozí barvy
    if alpha >= 1:
        return f"#{red:02x}{green:02x}{blue:02x}"
    return f"rgba({red}, {green}, {blue}, {alpha:g})"


def normalize_chart_style(values) -> dict:
    """Doplní a zkontroluje styl grafu; neznámé klíče zahodí, chybné nahradí výchozími.

    Parametry:
        values: slovník z dialogu, šablony nebo nastavení (může být neúplný).
    """
    source = values if isinstance(values, dict) else {}
    style: dict = {}
    for key, default in CHART_DEFAULTS.items():
        raw = source.get(key, default)
        if key in CHART_COLOR_KEYS:
            # Neplatná barva (ruční zápis, starší formát) → výchozí barva
            style[key] = normalize_color(raw) or default
        else:
            style[key] = bool(raw)
    return style


def load_chart_style(raw: str | None) -> dict:
    """Načte styl grafu uložený v nastavení (JSON); poškozený záznam = výchozí styl."""
    try:
        return normalize_chart_style(json.loads(raw or "{}"))
    except ValueError:
        return dict(CHART_DEFAULTS)


def templates_key(scope: str) -> str:
    """Klíč nastavení, pod kterým jsou uložené šablony daného rozsahu (graf, indikátor…)."""
    return f"templates.{scope}"


def default_template_key(scope: str) -> str:
    """Klíč nastavení s uživatelskou výchozí šablonou rozsahu („Save as default“)."""
    return f"template_default.{scope}"


def load_templates(raw: str | None) -> dict[str, dict]:
    """Načte šablony z JSON; vynechá poškozené položky a vrátí je seřazené podle názvu.

    Parametry:
        raw: JSON objekt {název: hodnoty} nebo None.
    """
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    items = {
        name: values
        for name, values in data.items()
        if isinstance(name, str) and name.strip() and isinstance(values, dict)
    }
    return dict(sorted(items.items(), key=lambda item: item[0].lower()))


def clean_name(name) -> str | None:
    """Ořízne název (šablona, seznam symbolů); prázdný nebo příliš dlouhý vrátí jako None."""
    if not isinstance(name, str):
        return None
    text = " ".join(name.split())
    if not text or len(text) > MAX_NAME_LENGTH:
        return None
    return text


def save_template(templates: dict[str, dict], name: str, values: dict) -> dict[str, dict]:
    """Vrátí nový slovník šablon s uloženou (případně přepsanou) šablonou.

    Shoda názvu se posuzuje bez ohledu na velikost písmen – „Default“
    a „default“ jsou stejná šablona (jako v TradingView).

    Parametry:
        templates: dosavadní šablony {název: hodnoty}.
        name: název šablony (už očištěný přes clean_name).
        values: hodnoty dialogu k uložení.
    """
    result = {key: value for key, value in templates.items() if key.lower() != name.lower()}
    result[name] = dict(values)
    return dict(sorted(result.items(), key=lambda item: item[0].lower()))


def delete_template(templates: dict[str, dict], name: str) -> dict[str, dict]:
    """Vrátí nový slovník šablon bez šablony daného názvu."""
    return {key: value for key, value in templates.items() if key != name}


def normalize_favorite_tools(modes, allowed: list[str]) -> list[str]:
    """Zkontroluje oblíbené nástroje (plovoucí panel) z JS nebo z nastavení.

    Neznámé a duplicitní režimy vynechá; pořadí odpovídá pořadí nástrojů
    v liště (allowed), stejně jako v plovoucím panelu.

    Parametry:
        modes: seznam režimů, např. ["trend", "hline"] (jiný typ = prázdný seznam).
        allowed: všechny režimy nástrojů v pořadí lišty.
    """
    if not isinstance(modes, list):
        return []
    wanted = {item for item in modes if isinstance(item, str)}
    return [mode for mode in allowed if mode in wanted]


def load_favorite_tools(raw: str | None, allowed: list[str]) -> list[str]:
    """Načte oblíbené nástroje z JSON nastavení (viz normalize_favorite_tools)."""
    try:
        return normalize_favorite_tools(json.loads(raw or "[]"), allowed)
    except ValueError:
        return []


def normalize_panel_position(data) -> dict | None:
    """Zkontroluje pozici plovoucího panelu {"left", "top"} v px; chybná hodnota = None (výchozí pozice)."""
    if not isinstance(data, dict):
        return None
    left, top = data.get("left"), data.get("top")
    # bool je podtřída int – True/False jako souřadnici nepřijmeme
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (left, top)):
        return None
    return {"left": max(0, int(left)), "top": max(0, int(top))}


def load_panel_position(raw: str | None) -> dict | None:
    """Načte pozici plovoucího panelu z JSON nastavení (viz normalize_panel_position)."""
    try:
        return normalize_panel_position(json.loads(raw or "null"))
    except ValueError:
        return None
