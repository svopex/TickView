"""Barvy, styl grafu, šablony nastavení (Template) a oblíbené nástroje kreslení.

Šablona je pojmenovaná sada hodnot jednoho dialogu Settings (graf, typ
indikátoru, typ kresby). Šablony se ukládají jako JSON do tabulky nastavení
pod klíčem „templates.<rozsah>“; tento modul obsahuje jen čisté funkce
(kontrola hodnot, úpravy slovníku šablon), ukládání řeší UI přes cache.
"""

import json
import re

from tickview.i18n import N_, tr

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
# okrajů a knotů pro růst / pokles a obarvení podle předchozího závěru
# (objem je indikátor Volume)
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
DRAWING_LINE_WIDTHS = {1: "1 px", 1.5: N_("1,5 px (výchozí)"), 2: "2 px", 3: "3 px", 4: "4 px"}
DRAWING_LINE_STYLES = {"solid": N_("Plná"), "dashed": N_("Přerušovaná"), "dotted": N_("Tečkovaná")}


def clamp_font_size(value) -> int:
    """Velikost písma textové kresby v rozsahu 8–72 px (neplatná hodnota = výchozí)."""
    try:
        return max(8, min(72, int(value or DEFAULT_FONT_SIZE)))
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE


# Kresby pozic (Long / Short position) a jejich výchozí parametry výpočtu:
# velikost účtu, riziko (v % účtu nebo částkou) a velikost lotu
POSITION_KINDS = ("long", "short")
POSITION_DEFAULTS: dict = {"accountSize": 10000.0, "risk": 1.0, "riskUnit": "percent", "lotSize": 1.0}
# Jednotky rizika pozice s popisky pro dialog Settings
POSITION_RISK_UNITS = {"percent": N_("% účtu"), "amount": N_("Částka")}


def _positive_number(value, default: float) -> float:
    """Kladné konečné číslo z dialogu / šablony; jinak výchozí hodnota."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if 0 < number < 1e15 else default


def normalize_position_options(options) -> dict:
    """Parametry pozice (Long / Short) s doplněnými výchozími hodnotami.

    Riziko v procentech je omezené na 100 % účtu.

    Parametry:
        options: {"accountSize", "risk", "riskUnit", "lotSize"} – může být neúplný.
    """
    source = options if isinstance(options, dict) else {}
    unit = source.get("riskUnit") if source.get("riskUnit") in POSITION_RISK_UNITS else POSITION_DEFAULTS["riskUnit"]
    risk = _positive_number(source.get("risk"), POSITION_DEFAULTS["risk"])
    if unit == "percent":
        risk = min(risk, 100.0)
    return {
        "accountSize": _positive_number(source.get("accountSize"), POSITION_DEFAULTS["accountSize"]),
        "risk": risk,
        "riskUnit": unit,
        "lotSize": _positive_number(source.get("lotSize"), POSITION_DEFAULTS["lotSize"]),
    }


# Velikosti písma textové kresby v nabídce dialogu (jako v TradingView)
TEXT_FONT_SIZES = [10, 11, 12, 13, 14, 16, 20, 24, 28, 32, 40]
# Kresby s volitelným textovým popiskem (záložka Text v Settings jako
# v TradingView): čáry, kanál a pitchfork kreslí popisek podél čáry,
# obdélník uvnitř, Fibonacci a štětec nad / pod ohraničením. Text má popis
# sám, pozice mají vlastní popisky s výpočtem a sticker je emoji
LABEL_KINDS = (
    "hline", "hray", "vline", "trend", "ray", "extended", "arrow", "cross",
    "channel", "pitchfork", "fib", "fibext", "rect", "brush",
)
# Svislé zarovnání popisku (nad čárou / na čáře / pod čárou, u obdélníku
# nahoře / uprostřed / dole uvnitř) a vodorovné (začátek / střed / konec)
LABEL_V_ALIGNS = {"top": "Top", "middle": "Middle", "bottom": "Bottom"}
LABEL_H_ALIGNS = {"left": "Left", "center": "Center", "right": "Right"}
# Výchozí barvy pozadí a rámečku textové kresby (zapínají se v dialogu)
DEFAULT_TEXT_BACKGROUND = "rgba(41, 98, 255, 0.25)"
DEFAULT_TEXT_BORDER = "#787b86"


def normalize_text_options(options) -> dict:
    """Vzhled textové kresby: tučné, kurzíva, pozadí, rámeček a zalomení řádků.

    Parametry:
        options: {"bold", "italic", "background", "backgroundColor", "border",
            "borderColor", "wrap"} – může být neúplný.
    """
    source = options if isinstance(options, dict) else {}
    return {
        "bold": bool(source.get("bold")),
        "italic": bool(source.get("italic")),
        "background": bool(source.get("background")),
        "backgroundColor": normalize_color(source.get("backgroundColor")) or DEFAULT_TEXT_BACKGROUND,
        "border": bool(source.get("border")),
        "borderColor": normalize_color(source.get("borderColor")) or DEFAULT_TEXT_BORDER,
        "wrap": bool(source.get("wrap")),
    }


def normalize_label_options(options, line_color: str) -> dict:
    """Vzhled textového popisku čáry nebo obdélníku: barva, tučné, kurzíva a zarovnání.

    Parametry:
        options: {"textColor", "bold", "italic", "textVAlign", "textHAlign"} – může být neúplný.
        line_color: barva kresby – výchozí barva popisku.
    """
    source = options if isinstance(options, dict) else {}
    v_align = source.get("textVAlign")
    h_align = source.get("textHAlign")
    return {
        "textColor": normalize_color(source.get("textColor")) or line_color,
        "bold": bool(source.get("bold")),
        "italic": bool(source.get("italic")),
        "textVAlign": v_align if v_align in LABEL_V_ALIGNS else "top",
        "textHAlign": h_align if h_align in LABEL_H_ALIGNS else "center",
    }


# Viditelnost kreseb a indikátorů podle timeframu (záložka Visibility jako
# v TradingView): jednotka → (popisek, nejmenší a největší hodnota rozsahu;
# None = jednotka jen se zaškrtávátkem). Řádky jsou všechny jako v TradingView,
# i když aplikace tickové, sekundové, měsíční a range timeframy (zatím) nemá
VISIBILITY_UNITS: dict[str, tuple[str, int | None, int | None]] = {
    "ticks": ("Ticks", None, None),
    "seconds": ("Seconds", 1, 59),
    "minutes": ("Minutes", 1, 59),
    "hours": ("Hours", 1, 24),
    "days": ("Days", 1, 366),
    "weeks": ("Weeks", 1, 52),
    "months": ("Months", 1, 12),
    "ranges": ("Ranges", None, None),
}
# Přípona timeframu → jednotka viditelnosti (30s, 1m, 4h, 1D, 1W, 3M)
_TIMEFRAME_SUFFIX_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "D": "days", "W": "weeks", "M": "months"}


def timeframe_unit(timeframe: str) -> tuple[str, int] | None:
    """Jednotka viditelnosti a počet jednotek timeframu ("15m" → ("minutes", 15)); neznámý = None."""
    unit = _TIMEFRAME_SUFFIX_UNITS.get(timeframe[-1:]) if isinstance(timeframe, str) else None
    count = timeframe[:-1]
    if unit is None or not count.isdigit():
        return None
    return unit, int(count)


def _bounded_int(value, low: int, high: int, default: int) -> int:
    """Celé číslo omezené na rozsah low–high; neplatná hodnota = default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def normalize_visibility(data) -> dict:
    """Viditelnost kresby po jednotkách timeframu s doplněnými výchozími hodnotami.

    Výchozí je kresba viditelná na všech timeframech (celý rozsah každé
    jednotky). Prohozené meze rozsahu se srovnají.

    Parametry:
        data: {jednotka: {"enabled", "from", "to"}} – může být neúplný nebo None.
    """
    source = data if isinstance(data, dict) else {}
    result = {}
    for unit, (_, low, high) in VISIBILITY_UNITS.items():
        item = source.get(unit) if isinstance(source.get(unit), dict) else {}
        enabled = item.get("enabled", True) is not False
        # Jednotka bez rozsahu (Ticks, Ranges) má jen zaškrtnutí
        if low is None:
            result[unit] = {"enabled": enabled}
            continue
        start = _bounded_int(item.get("from"), low, high, low)
        end = _bounded_int(item.get("to"), low, high, high)
        result[unit] = {
            "enabled": enabled,
            "from": min(start, end),
            "to": max(start, end),
        }
    return result


def timeframe_visible(visibility, timeframe: str) -> bool:
    """Je kresba / indikátor s danou viditelností vidět na timeframu? (záložka Visibility)

    Parametry:
        visibility: {jednotka: {"enabled", "from", "to"}} (jiný typ = viditelný všude).
        timeframe: timeframe grafu, např. "5m" (neznámý = viditelný).
    """
    unit = timeframe_unit(timeframe)
    if unit is None or not isinstance(visibility, dict):
        return True
    rule = normalize_visibility(visibility)[unit[0]]
    return rule["enabled"] and rule["from"] <= unit[1] <= rule["to"]


def drawing_visible(options, timeframe: str) -> bool:
    """Má se kresba zobrazit na daném timeframu? (záložka Visibility v Settings kresby)

    Parametry:
        options: volby kresby (může obsahovat "visibility"; bez ní je viditelná všude).
        timeframe: timeframe grafu, např. "5m" (neznámý = viditelná).
    """
    return timeframe_visible(options.get("visibility") if isinstance(options, dict) else None, timeframe)


# Záložka Style instance indikátoru jako v TradingView – přepínače: grafické
# objekty (Lines = úsečky, Tables = tabulka scanneru), Output values (popisky
# hodnot na cenové ose, hodnoty v legendě) a vstupy v legendě (status line)
INDICATOR_STYLE_DEFAULTS: dict[str, bool] = {
    "lines": True,
    "tables": True,
    "labels_on_scale": True,
    "values_in_status": True,
    "inputs_in_status": True,
}

# Druhy vykreslení výstupu (Plot type v TradingView) – klíč -> popisek nabídky.
# „…br“ = s přerušením v místech bez hodnoty, ostatní čáry mezery přemostí
PLOT_TYPES: dict[str, str] = {
    "line": "Line",
    "linebr": "Line with breaks",
    "step": "Step line",
    "stepbr": "Step line with breaks",
    "stepdiamond": "Step line with diamonds",
    "histogram": "Histogram",
    "area": "Area",
    "areabr": "Area with breaks",
    "columns": "Columns",
    "circles": "Circles",
}
# Tloušťky čáry výstupu (Thickness) a styly čáry (Line style) jako v TradingView
PLOT_WIDTHS = (1, 2, 3, 4)
PLOT_LINE_STYLES = tuple(DRAWING_LINE_STYLES)
# Přesnost hodnot (Precision): None = výchozí podle instrumentu, jinak počet desetinných míst
PLOT_PRECISIONS = tuple(range(9))


def normalize_plot_style(data) -> dict:
    """Změny stylu jednoho výstupu indikátoru (čáry) proti jeho výchozímu vzhledu.

    Ukládají se jen změněné klíče, výchozí hodnoty určuje až výstup indikátoru
    (barva, typ, tloušťka); neplatné hodnoty se vynechají.

    Parametry:
        data: {"visible"?, "color"?, "width"?, "lineStyle"?, "type"?, "priceLine"?}
            (jiný typ = žádné změny).
    """
    source = data if isinstance(data, dict) else {}
    style: dict = {}
    # Přepínače: zobrazení výstupu a cenová linka u poslední hodnoty
    for key in ("visible", "priceLine"):
        if isinstance(source.get(key), bool):
            style[key] = source[key]
    if color := normalize_color(source.get("color")):
        style["color"] = color
    # Výčtové hodnoty jen z povolených voleb (bool je podtřída int – vyloučíme)
    width = source.get("width")
    if width in PLOT_WIDTHS and not isinstance(width, bool):
        style["width"] = width
    if source.get("lineStyle") in PLOT_LINE_STYLES:
        style["lineStyle"] = source["lineStyle"]
    if source.get("type") in PLOT_TYPES:
        style["type"] = source["type"]
    return style


def normalize_indicator_style(data) -> dict:
    """Záložka Style instance indikátoru s výchozími hodnotami.

    Parametry:
        data: {"lines", "tables", "labels_on_scale", "values_in_status",
            "inputs_in_status", "precision", "plots": {název výstupu: změny}}
            – může být neúplný nebo None.

    Vrací přepínače INDICATOR_STYLE_DEFAULTS (výchozí zapnuto), "precision"
    (None = výchozí, jinak 0–8) a "plots" jen s výstupy, které mají nějakou změnu.
    """
    source = data if isinstance(data, dict) else {}
    style: dict = {key: source.get(key, default) is not False for key, default in INDICATOR_STYLE_DEFAULTS.items()}
    precision = source.get("precision")
    style["precision"] = precision if precision in PLOT_PRECISIONS and not isinstance(precision, bool) else None
    plots = source.get("plots") if isinstance(source.get("plots"), dict) else {}
    # Výstupy bez platné změny se vynechají (výchozí vzhled se neukládá)
    style["plots"] = {
        name: plot_style
        for name, value in plots.items()
        if isinstance(name, str) and (plot_style := normalize_plot_style(value))
    }
    return style


# Fixed range volume profile (FRVP jako v TradingView): rozvržení řádků
# (počet řádků / ticků na řádek), druh objemu, umístění histogramu v boxu
FRVP_KIND = "frvp"
FRVP_ROWS_LAYOUTS = {"count": "Number Of Rows", "ticks": "Ticks Per Row"}
FRVP_VOLUME_MODES = {"updown": "Up/Down", "total": "Total", "delta": "Delta"}
FRVP_PLACEMENTS = {"left": "Left", "right": "Right"}
# Nejvyšší počet řádků profilu (i u Ticks Per Row – výpočet a kreslení
# probíhají při každém pohybu grafu)
FRVP_MAX_ROWS = 1000
# Čáry profilu: úrovně VAH, VAL, POC a vyvíjející se POC a Value Area;
# každá má zobrazení, barvu, tloušťku a styl čáry
FRVP_LINES = ("vah", "val", "poc", "devPoc", "devVa")
FRVP_LINE_DEFAULTS: dict[str, dict] = {
    "vah": {"visible": False, "color": "#2962ff", "width": 1, "lineStyle": "solid"},
    "val": {"visible": False, "color": "#2962ff", "width": 1, "lineStyle": "solid"},
    "poc": {"visible": True, "color": "#ff0000", "width": 1, "lineStyle": "solid"},
    "devPoc": {"visible": False, "color": "#ff0000", "width": 1, "lineStyle": "solid"},
    "devVa": {"visible": False, "color": "#2962ff", "width": 1, "lineStyle": "solid"},
}
# Tovární hodnoty Inputs a Style (barvy histogramu jako ve výchozím FRVP
# TradingView: růst modrý, pokles žlutý, Value Area sytější)
FRVP_DEFAULTS: dict = {
    "rowsLayout": "ticks",
    "rowSize": 20,
    "volumeMode": "updown",
    "valueArea": 70,
    "extendRight": False,
    "profile": True,
    "values": False,
    "valuesColor": "#787b86",
    "profileWidth": 30,
    "placement": "left",
    "upColor": "rgba(21, 146, 230, 0.3)",
    "downColor": "rgba(251, 193, 35, 0.3)",
    "vaUpColor": "rgba(21, 146, 230, 0.7)",
    "vaDownColor": "rgba(251, 193, 35, 0.7)",
    "boxColor": "rgba(41, 98, 255, 0.06)",
}


def _normalize_frvp_line(value, default: dict) -> dict:
    """Čára profilu (VAH, POC…): zobrazení, barva, tloušťka a styl s výchozími hodnotami.

    Parametry:
        value: {"visible", "color", "width", "lineStyle"} – může být neúplný.
        default: výchozí hodnoty čáry (FRVP_LINE_DEFAULTS).
    """
    # Kontrola barvy, tloušťky a stylu jako u výstupů indikátoru; neplatné klíče = výchozí
    return {**default, **{key: item for key, item in normalize_plot_style(value).items() if key in default}}


def normalize_frvp_options(options) -> dict:
    """Volby FRVP (záložky Inputs a Style) s doplněnými výchozími hodnotami.

    Velikost řádku je 1–FRVP_MAX_ROWS (počet řádků nebo ticků na řádek),
    Value Area 1–100 % objemu a šířka histogramu 1–100 % šířky boxu.

    Parametry:
        options: volby kresby (klíče FRVP_DEFAULTS a čáry FRVP_LINES) – může být neúplný.
    """
    source = options if isinstance(options, dict) else {}
    defaults = FRVP_DEFAULTS

    def choice(key: str, allowed: dict) -> str:
        # Výčtová hodnota jen z povolených voleb
        return source.get(key) if source.get(key) in allowed else defaults[key]

    def color(key: str) -> str:
        return normalize_color(source.get(key)) or defaults[key]

    result = {
        "rowsLayout": choice("rowsLayout", FRVP_ROWS_LAYOUTS),
        "rowSize": _bounded_int(source.get("rowSize"), 1, FRVP_MAX_ROWS, defaults["rowSize"]),
        "volumeMode": choice("volumeMode", FRVP_VOLUME_MODES),
        "valueArea": _bounded_int(source.get("valueArea"), 1, 100, defaults["valueArea"]),
        "extendRight": bool(source.get("extendRight")),
        "profile": source.get("profile", True) is not False,
        "values": bool(source.get("values")),
        "valuesColor": color("valuesColor"),
        "profileWidth": _bounded_int(source.get("profileWidth"), 1, 100, defaults["profileWidth"]),
        "placement": choice("placement", FRVP_PLACEMENTS),
        "upColor": color("upColor"),
        "downColor": color("downColor"),
        "vaUpColor": color("vaUpColor"),
        "vaDownColor": color("vaDownColor"),
        "boxColor": color("boxColor"),
    }
    for name in FRVP_LINES:
        result[name] = _normalize_frvp_line(source.get(name), FRVP_LINE_DEFAULTS[name])
    return result


def normalize_drawing_style(values, kind: str | None = None) -> dict:
    """Styl kresby (šablona, dialog, výchozí styl nových kreseb) s doplněnými výchozími hodnotami.

    Parametry:
        values: {"color", "options": {"width", "lineStyle", "fontSize", "visibility", …}}
            – může být neúplný.
        kind: druh kresby; pozice (POSITION_KINDS) mají v options navíc
            parametry výpočtu (velikost účtu, riziko, lot), text vzhled písma,
            pozadí a rámečku, čáry a obdélník vzhled popisku (LABEL_KINDS),
            obdélník prodloužení doleva / doprava, FRVP volby profilu
            (normalize_frvp_options).

    Vrací {"color", "options"} s platnou barvou, šířkou, stylem čáry, písmem
    a viditelností podle timeframu.
    """
    source = values if isinstance(values, dict) else {}
    options = source.get("options") if isinstance(source.get("options"), dict) else {}
    width = options.get("width")
    line_style = options.get("lineStyle")
    style = {
        "color": normalize_color(source.get("color")) or DEFAULT_DRAWING_COLOR,
        "options": {
            "width": width if width in DRAWING_LINE_WIDTHS else DEFAULT_LINE_WIDTH,
            "lineStyle": line_style if line_style in DRAWING_LINE_STYLES else "solid",
            "fontSize": clamp_font_size(options.get("fontSize")),
            "visibility": normalize_visibility(options.get("visibility")),
        },
    }
    if kind in POSITION_KINDS:
        style["options"].update(normalize_position_options(options))
    if kind == "text":
        style["options"].update(normalize_text_options(options))
    if kind in LABEL_KINDS:
        style["options"].update(normalize_label_options(options, style["color"]))
    # Obdélník: prodloužení doleva / doprava až k okraji grafu (Extend left / right)
    if kind == "rect":
        style["options"]["extendLeft"] = bool(options.get("extendLeft"))
        style["options"]["extendRight"] = bool(options.get("extendRight"))
    if kind == FRVP_KIND:
        style["options"].update(normalize_frvp_options(options))
    return style


def normalize_position_points(
    kind: str, entry, target, stop, points: list[dict], decimals: int | None = None
) -> list[dict]:
    """Body pozice s cenami z dialogu Settings; špatné pořadí cen vyhodí ValueError.

    Long musí mít stop < vstup < cíl, short cíl < vstup < stop. Časy bodů
    (začátek a konec pozice) zůstanou z původní kresby.

    Parametry:
        kind: "long" nebo "short".
        entry, target, stop: ceny vstupu, cíle a stopu.
        points: původní body kresby [vstup, cíl, stop].
        decimals: přesnost cen instrumentu – ceny se na ni zaokrouhlí (None = beze změny).
    """
    try:
        entry, target, stop = float(entry), float(target), float(stop)
    except (TypeError, ValueError):
        raise ValueError(tr("Zadejte ceny vstupu, cíle a stopu.")) from None
    if decimals is not None:
        entry, target, stop = (round(price, decimals) for price in (entry, target, stop))
    if kind == "long" and not stop < entry < target:
        raise ValueError(tr("Long pozice musí mít stop pod vstupem a cíl nad ním."))
    if kind == "short" and not target < entry < stop:
        raise ValueError(tr("Short pozice musí mít cíl pod vstupem a stop nad ním."))
    if len(points) < 3:
        raise ValueError(tr("Neplatná pozice."))
    return [
        {"time": int(points[0]["time"]), "price": entry},
        {"time": int(points[1]["time"]), "price": target},
        {"time": int(points[2]["time"]), "price": stop},
    ]


# Motiv vzhledu aplikace a grafů: tmavý, světlý, nebo Auto podle systému
# (prefers-color-scheme prohlížeče)
THEME_MODES = ("dark", "light", "auto")
DEFAULT_THEME = "auto"


def normalize_theme(value) -> str:
    """Platný motiv vzhledu (THEME_MODES); neznámá hodnota = Auto (podle systému)."""
    return value if value in THEME_MODES else DEFAULT_THEME


def next_theme(value) -> str:
    """Další motiv v pořadí tmavý → světlý → Auto → tmavý (tlačítko motivu v liště)."""
    return THEME_MODES[(THEME_MODES.index(normalize_theme(value)) + 1) % len(THEME_MODES)]


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
# Výchozí oblíbené typy grafu (tlačítka v horní liště)
DEFAULT_FAVORITE_CHART_TYPES = ["candles"]

# Timeframy po sekcích nabídky (jako v TradingView): (nadpis, [(timeframe, popisek)])
TIMEFRAME_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (N_("Minuty"), [("1m", N_("1 minuta")), ("2m", N_("2 minuty")), ("5m", N_("5 minut")), ("10m", N_("10 minut")),
                    ("15m", N_("15 minut"))]),
    (N_("Hodiny"), [("1h", N_("1 hodina")), ("4h", N_("4 hodiny"))]),
    (N_("Dny"), [("1D", N_("1 den")), ("1W", N_("1 týden"))]),
]
# Všechny timeframy v pořadí nabídky
TIMEFRAMES: list[str] = [timeframe for _, items in TIMEFRAME_SECTIONS for timeframe, _ in items]


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


def normalize_favorites(values, allowed: list[str] | None) -> list[str]:
    """Zkontroluje oblíbené položky (nástroje kreslení, typy grafu, timeframy, indikátory).

    Neznámé a duplicitní hodnoty vynechá; pořadí odpovídá pořadí v allowed
    (pořadí lišty / nabídky), stejně jako se oblíbené zobrazují.

    Parametry:
        values: seznam hodnot, např. ["trend", "hline"] (jiný typ = prázdný seznam).
        allowed: všechny povolené hodnoty v pořadí zobrazení; None = neznámé
            hodnoty se ponechají (seznam se může měnit, např. dočasně nenačtený
            vlastní indikátor) a seřadí se abecedně.
    """
    if not isinstance(values, list):
        return []
    wanted = {item for item in values if isinstance(item, str)}
    if allowed is None:
        return sorted(wanted)
    return [value for value in allowed if value in wanted]


def load_favorites(raw: str | None, allowed: list[str] | None, default: list[str] | None = None) -> list[str]:
    """Načte oblíbené položky z JSON nastavení (viz normalize_favorites).

    Parametry:
        raw: JSON seznam z nastavení, nebo None (dosud neuloženo).
        allowed: povolené hodnoty v pořadí zobrazení (None = viz normalize_favorites).
        default: oblíbené, když nastavení ještě neexistuje.
    """
    try:
        values = default if raw is None else json.loads(raw)
    except ValueError:
        values = default
    return normalize_favorites(values, allowed)


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
