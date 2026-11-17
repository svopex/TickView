"""Rozložení více grafů v okně (Layout setup jako v TradingView) a synchronizace mezi nimi.

Rozložení určuje počet grafů a jejich uspořádání (vzhled řeší CSS třída
„layout-<id>“ v style.css). Uložená konfigurace drží rozložení, volby
synchronizace, aktivní graf a stav každého grafu (symbol, timeframe, typ
grafu, indikátory). Modul obsahuje jen čisté funkce – ukládání řeší UI.
"""

import json

from tickview.logic.indicators import normalize_indicators
from tickview.logic.styles import TIMEFRAMES, normalize_chart_type

# Rozložení po řádcích nabídky podle počtu grafů: (počet, [(id, popis)]);
# id je zároveň přípona CSS třídy layout-<id>
LAYOUT_SECTIONS: list[tuple[int, list[tuple[str, str]]]] = [
    (1, [("1", "1 graf")]),
    (2, [("2v", "2 vedle sebe"), ("2h", "2 pod sebou")]),
    (
        3,
        [
            ("3v", "3 vedle sebe"),
            ("3h", "3 pod sebou"),
            ("3l", "1 vlevo, 2 vpravo"),
            ("3r", "2 vlevo, 1 vpravo"),
            ("3t", "1 nahoře, 2 dole"),
            ("3b", "2 nahoře, 1 dole"),
        ],
    ),
    (
        4,
        [
            ("4", "2 × 2"),
            ("4v", "4 vedle sebe"),
            ("4h", "4 pod sebou"),
            ("4l", "1 vlevo, 3 vpravo"),
            ("4r", "3 vlevo, 1 vpravo"),
            ("4t", "1 nahoře, 3 dole"),
            ("4b", "3 nahoře, 1 dole"),
        ],
    ),
    (5, [("5t", "2 nahoře, 3 dole"), ("5b", "3 nahoře, 2 dole"), ("5l", "1 vlevo, 4 vpravo"), ("5v", "5 vedle sebe")]),
    (6, [("6", "3 × 2"), ("6r", "2 × 3"), ("6v", "6 vedle sebe")]),
    (7, [("7", "3 nahoře, 4 dole")]),
    (8, [("8", "4 × 2"), ("8r", "2 × 4")]),
    (9, [("9", "3 × 3")]),
    (10, [("10", "5 × 2")]),
    (12, [("12", "4 × 3"), ("12r", "3 × 4")]),
    (14, [("14", "7 × 2")]),
    (16, [("16", "4 × 4")]),
]
# Rozložení → počet grafů
LAYOUTS: dict[str, int] = {layout_id: count for count, items in LAYOUT_SECTIONS for layout_id, _ in items}
DEFAULT_LAYOUT = "1"

# Volby synchronizace (jako „Sync in layout“ v TradingView) s popisky a nápovědou
SYNC_OPTIONS: dict[str, tuple[str, str]] = {
    "symbol": ("Symbol", "Změna symbolu v aktivním grafu se projeví ve všech grafech rozložení."),
    "interval": ("Interval", "Změna timeframu v aktivním grafu se projeví ve všech grafech rozložení."),
    "crosshair": ("Crosshair", "Crosshair se ukazuje ve stejném čase ve všech grafech."),
    "time": ("Time", "Posun grafu v čase posune ostatní grafy na stejné místo (pravý okraj)."),
    "date_range": ("Date range", "Ostatní grafy převezmou celý viditelný časový rozsah (posun i přiblížení)."),
}
DEFAULT_SYNC: dict[str, bool] = {key: False for key in SYNC_OPTIONS}

# Výchozí symbol a timeframe grafu bez uložené konfigurace
DEFAULT_SYMBOL = "binance:BTCUSDT"
DEFAULT_TIMEFRAME = "1h"


def normalize_pane(data, fallback: dict | None = None) -> dict:
    """Stav jednoho grafu rozložení s doplněnými výchozími hodnotami.

    Parametry:
        data: {"symbol": "zdroj:symbol", "timeframe", "chart_type", "indicators": [...]}
            – může být neúplný nebo poškozený.
        fallback: hodnoty, které se použijí místo chybějících (typicky aktivní
            graf při přidání nového grafu do rozložení).
    """
    source = data if isinstance(data, dict) else {}
    base = fallback or {}
    symbol = source.get("symbol")
    if not (isinstance(symbol, str) and ":" in symbol):
        symbol = base.get("symbol") or DEFAULT_SYMBOL
    timeframe = source.get("timeframe")
    if timeframe not in TIMEFRAMES:
        timeframe = base.get("timeframe") if base.get("timeframe") in TIMEFRAMES else DEFAULT_TIMEFRAME
    chart_type = normalize_chart_type(source.get("chart_type") or base.get("chart_type"))
    # Indikátory projdou stejnou kontrolou jako uložené indikátory jednoho grafu
    raw_indicators = source.get("indicators") if isinstance(source.get("indicators"), list) else base.get("indicators")
    indicators = normalize_indicators(raw_indicators)
    return {"symbol": symbol, "timeframe": timeframe, "chart_type": chart_type, "indicators": indicators}


def normalize_layout(data, legacy: dict | None = None) -> dict:
    """Konfigurace rozložení s platným id, synchronizací, aktivním grafem a grafy.

    Počet grafů vždy odpovídá rozložení: chybějící se doplní kopií prvního
    grafu, přebývající se zahodí.

    Parametry:
        data: uložená konfigurace {"layout", "sync", "active", "panes"} (může být None).
        legacy: stav jediného grafu ze starších nastavení (poslední symbol,
            timeframe, typ grafu, indikátory) pro první spuštění s rozložením.
    """
    source = data if isinstance(data, dict) else {}
    layout_id = source.get("layout") if source.get("layout") in LAYOUTS else DEFAULT_LAYOUT
    count = LAYOUTS[layout_id]
    raw_sync = source.get("sync") if isinstance(source.get("sync"), dict) else {}
    sync = {key: bool(raw_sync.get(key, default)) for key, default in DEFAULT_SYNC.items()}
    raw_panes = source.get("panes") if isinstance(source.get("panes"), list) else []
    first = normalize_pane(raw_panes[0] if raw_panes else legacy)
    panes = [first] + [normalize_pane(raw_panes[index] if index < len(raw_panes) else None, first) for index in range(1, count)]
    active = source.get("active")
    # bool je podtřída int – True/False jako index nepřijmeme
    if not isinstance(active, int) or isinstance(active, bool) or not 0 <= active < count:
        active = 0
    return {"layout": layout_id, "sync": sync, "active": active, "panes": panes}


def load_layout(raw: str | None, legacy: dict | None = None) -> dict:
    """Načte konfiguraci rozložení z JSON nastavení (viz normalize_layout)."""
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    return normalize_layout(data, legacy)


def resize_panes(panes: list[dict], layout_id: str, active: int) -> list[dict]:
    """Grafy pro nové rozložení: stávající zůstanou, nové převezmou stav aktivního grafu.

    Parametry:
        panes: dosavadní stavy grafů.
        layout_id: nové rozložení (neznámé = jeden graf).
        active: index aktivního grafu (vzor pro nově přidané grafy).
    """
    count = LAYOUTS.get(layout_id, 1)
    template = panes[active] if 0 <= active < len(panes) else (panes[0] if panes else normalize_pane(None))
    kept = [dict(pane) for pane in panes[:count]]
    # Nový graf má symbol, timeframe a typ aktivního grafu, ale bez indikátorů
    # (jako v TradingView – indikátory patří ke konkrétnímu grafu)
    extra = {"symbol": template["symbol"], "timeframe": template["timeframe"], "chart_type": template["chart_type"], "indicators": []}
    return kept + [dict(extra) for _ in range(count - len(kept))]
