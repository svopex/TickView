"""Instance indikátorů v grafu a jejich výpočet do payloadu pro JS graf.

Instance je slovník {"id", "kind", "params", "visible", "color"?} – ukládá
se do nastavení, rozložení a šablon. Instance indikátoru, který se
nenačetl (např. chyba v souboru vlastního indikátoru), se nezahazuje:
zůstane v legendě s poznámkou a s původními parametry, takže po opravě
indikátoru nastavení nezmizí.
"""

import json
import logging
import math
import random
import uuid
from typing import Optional

import pandas as pd

from tickview.indicators.api import (
    DOWN_COLOR,
    LINE_DASHED,
    LINE_DOTTED,
    LINE_SOLID,
    PALETTE,
    UP_COLOR,
    Context,
    Indicator,
    Line,
    Result,
)
from tickview.indicators.registry import get_indicator
from tickview.logic.styles import (
    PLOT_TYPES,
    PLOT_WIDTHS,
    normalize_color,
    normalize_indicator_style,
    normalize_visibility,
    timeframe_visible,
)
from tickview.providers.base import TIMEFRAME_MS, Candle

logger = logging.getLogger(__name__)

# Nejmenší počet svíček historie pro výpočet mimo graf (scanner, alert na signál)
MIN_HISTORY_BARS = 300

# Kolik posledních bodů každé série posílá živý přepočet (předchozí svíčka se
# mohla uzavřít, poslední je rozpracovaná; starší hodnoty se nemění)
TAIL_POINTS = 3

# Styl čáry v API (LINE_*) <-> styl výstupu v záložce Style
LINE_STYLE_NAMES = {LINE_SOLID: "solid", LINE_DOTTED: "dotted", LINE_DASHED: "dashed"}
LINE_STYLE_VALUES = {name: value for value, name in LINE_STYLE_NAMES.items()}
# Druh série Lightweight Charts podle druhu vykreslení (ostatní druhy = "line");
# JS podle něj sérii vytvoří
PLOT_SERIES = {"histogram": "histogram", "columns": "histogram", "area": "area", "areabr": "area"}
# Druhy, které mezery bez hodnoty přemostí: lineárně (čára, plocha), nebo
# předchozí hodnotou (schodovitá čára); schodovité čáry a druhy s body
LINEAR_BRIDGED_PLOT_TYPES = ("line", "area")
STEP_BRIDGED_PLOT_TYPES = ("step", "stepdiamond")
STEP_PLOT_TYPES = ("step", "stepbr", "stepdiamond")
POINT_PLOT_TYPES = ("circles", "stepdiamond")
# Počet umělých svíček, nad kterými se zjišťují výstupy indikátoru pro záložku Style
SAMPLE_BARS = 1200


# ---------- instance ----------


def indicator_color(spec: dict, index: int) -> str:
    """Barva instance indikátoru: vlastní, jinak z palety podle pořadí."""
    return spec.get("color") or PALETTE[index % len(PALETTE)]


def indicator_title(spec: dict, style: Optional[dict] = None) -> str:
    """Krátký název instance pro legendu (např. "SMA 20"); nenačtený indikátor = jeho id.

    Bez „Inputs in status line“ (záložka Style) jen název bez hodnot vstupů.

    Parametry:
        spec: instance indikátoru.
        style: už normalizovaný styl instance (None = spočítá se ze spec).
    """
    indicator = get_indicator(spec["kind"])
    if indicator is None:
        return f"{spec['kind']} (nenačten)"
    style = style or normalize_indicator_style(spec.get("style"))
    return indicator.legend_title(spec["params"], with_inputs=style["inputs_in_status"])


def normalize_params(kind: str, values: dict) -> dict:
    """Ověří parametry instance podle schématu indikátoru.

    Parametry:
        kind: id indikátoru.
        values: hodnoty parametrů {název: hodnota nebo None}.

    Vrací normalizované parametry; neznámý indikátor nebo neplatná hodnota
    vyhodí ValueError s českou zprávou pro uživatele.
    """
    indicator = get_indicator(kind)
    if indicator is None:
        raise ValueError(f"Neznámý indikátor: {kind}")
    return indicator.normalize_params(values)


def new_indicator(kind: str, params: Optional[dict] = None) -> dict:
    """Vytvoří novou instanci indikátoru s výchozími (nebo zadanými) parametry."""
    return {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "params": normalize_params(kind, params or {}),
        "visible": True,
    }


def load_saved_indicators(raw: Optional[str]) -> list[dict]:
    """Načte instance uložené v nastavení (JSON); poškozené položky vynechá."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    return normalize_indicators(items)


def instance_params(kind: str, raw) -> dict:
    """Parametry uložené instance (indikátor v grafu, scanner) bez vyhazování chyb.

    Nenačtený indikátor si parametry ponechá beze změny (po opravě se znovu
    ověří), neplatné parametry se nahradí výchozími – změněné schéma
    indikátoru nesmí uložené nastavení zahodit.
    """
    params = raw if isinstance(raw, dict) else {}
    indicator = get_indicator(kind)
    if indicator is None:
        return dict(params)
    try:
        return indicator.normalize_params(params)
    except ValueError:
        return indicator.defaults()


def instance_options(source: dict) -> dict:
    """Volby záložek Style (grafické objekty, vstupy v legendě) a Visibility (timeframy) instance.

    Vrací jen volby změněné proti výchozímu stavu (vše zapnuto / viditelné
    všude) – výchozí se do rozložení a šablon neukládají.

    Parametry:
        source: instance nebo hodnoty dialogu s klíči "style"? a "visibility"?.
    """
    options = {}
    for key, normalize in (("style", normalize_indicator_style), ("visibility", normalize_visibility)):
        value = normalize(source.get(key))
        if value != normalize(None):
            options[key] = value
    return options


def normalize_indicators(items) -> list[dict]:
    """Zkontroluje seznam instancí (rozložení, šablona, nastavení).

    Položka bez typu se vynechá. Instance nenačteného indikátoru se zachová
    s původními parametry; instance s neplatnými parametry dostane výchozí
    (změněné schéma indikátoru nesmí instanci zahodit).

    Parametry:
        items: seznam [{"id", "kind", "params", "visible"?, "color"?, "style"?, "visibility"?}]
            (jiný typ = prázdný seznam).
    """
    indicators: list[dict] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str) or not item["kind"]:
            continue
        spec = {
            "id": str(item.get("id") or uuid.uuid4().hex[:8]),
            "kind": item["kind"],
            "params": instance_params(item["kind"], item.get("params")),
            "visible": item.get("visible", True) is not False,
        }
        spec.update(instance_options(item))
        # Vlastní barva jen ve tvaru #rrggbb (jinak výchozí z palety)
        if color := normalize_color(item.get("color")):
            spec["color"] = color
        indicators.append(spec)
    return indicators


# ---------- kontext a svíčky ----------


def detect_tick(candles: list[Candle]) -> float:
    """Nejmenší změna ceny odhadnutá z dat (90. percentil počtu desetinných míst OHLC, 2–8).

    Stejný postup jako přesnost cen v JS grafu: forex 0.00001, akcie 0.01.
    """
    counts = []
    for candle in candles[-300:]:
        for value in (candle.open, candle.high, candle.low, candle.close):
            text = f"{value:.8f}".rstrip("0")
            dot = text.find(".")
            counts.append(0 if dot < 0 else len(text) - dot - 1)
    if not counts:
        return 0.01
    counts.sort()
    decimals = min(8, max(2, counts[int((len(counts) - 1) * 0.9)]))
    return 10.0 ** -decimals


def make_context(
    source: str,
    symbol: str,
    timeframe: str,
    candles: list[Candle],
    *,
    kind: str = "",
    exchange_timezone: str = "UTC",
    tick: Optional[float] = None,
    live: bool = False,
    signal_bars: Optional[int] = None,
) -> Context:
    """Sestaví Context výpočtu z údajů o instrumentu a svíček (tick None = odhad z dat)."""
    return Context(
        symbol=symbol,
        source=source,
        timeframe=timeframe,
        step_ms=TIMEFRAME_MS.get(timeframe, 0),
        kind=kind,
        tick=tick or detect_tick(candles),
        exchange_timezone=exchange_timezone,
        live=live,
        signal_bars=signal_bars,
    )


def context_for(provider, symbol: str, timeframe: str, candles: list[Candle], *, live: bool = False,
                signal_bars: Optional[int] = None) -> Context:
    """Context výpočtu pro symbol zdroje: druh instrumentu, tick a pásmo burzy podává zdroj.

    Parametry:
        provider: zdroj dat (DataProvider) nebo None (neznámý zdroj = prázdné údaje).
        symbol, timeframe, candles: co se počítá.
        live, signal_bars: viz Context.
    """
    if provider is None:
        return make_context("", symbol, timeframe, candles, live=live, signal_bars=signal_bars)
    return make_context(
        provider.id, symbol, timeframe, candles,
        kind=provider.symbol_kind(symbol),
        exchange_timezone=provider.exchange_timezone(symbol),
        tick=provider.price_tick(symbol, candles[-1].close) if candles else None,
        live=live,
        signal_bars=signal_bars,
    )


def candles_frame(candles: list[Candle]) -> pd.DataFrame:
    """Svíčky jako DataFrame pro indikátory (time v sekundách UTC, OHLCV)."""
    return pd.DataFrame(
        {
            "time": [candle.open_time // 1000 for candle in candles],
            "open": [candle.open for candle in candles],
            "high": [candle.high for candle in candles],
            "low": [candle.low for candle in candles],
            "close": [candle.close for candle in candles],
            "volume": [candle.volume for candle in candles],
        }
    )


def history_bars(indicator: Indicator, params: dict) -> int:
    """Kolik svíček historie stáhnout pro výpočet mimo graf (aspoň MIN_HISTORY_BARS)."""
    return max(indicator.required_bars(params), MIN_HISTORY_BARS)


def run_indicator(indicator: Indicator, params: dict, candles: list[Candle], ctx: Context) -> Result:
    """Spočítá jeden indikátor nad svíčkami (scanner, testy); chyby propouští volajícímu."""
    return indicator.compute(candles_frame(candles), params, ctx)


# ---------- payload pro JS graf ----------


def _points(times, values) -> list[dict]:
    """Spojí časy s hodnotami; NaN nahradí prázdným bodem jen s časem.

    Lightweight Charts NaN neumí; prázdný bod (whitespace) ale zachová stejný
    počet bodů jako mají svíčky, takže panely indikátorů sedí na hlavní graf
    i při synchronizaci podle logického indexu.
    """
    return [
        {"time": int(time)} if pd.isna(value) else {"time": int(time), "value": float(value)}
        for time, value in zip(times, values)
    ]


def plot_defaults(line: Line, color: str) -> dict:
    """Výchozí vzhled výstupu indikátoru (řádek záložky Style) podle jeho deklarace.

    Parametry:
        line: výstup indikátoru (Line).
        color: barva instance (pro výstup bez vlastní barvy).

    Vrací {"visible", "color", "width", "lineStyle", "type", "priceLine"};
    histogram bez vlastní barvy má color None (barví se podle znaménka).
    """
    options = line.options or {}
    plot_type = line.type if line.type in PLOT_TYPES else "line"
    width = options.get("lineWidth", 1)
    return {
        "visible": True,
        "color": line.color if PLOT_SERIES.get(plot_type) == "histogram" else line.color or color,
        "width": width if width in PLOT_WIDTHS else 1,
        "lineStyle": LINE_STYLE_NAMES.get(options.get("lineStyle", LINE_SOLID), "solid"),
        "type": plot_type,
        "priceLine": False,
    }


def resolve_plot(line: Line, color: str, changes: Optional[dict]) -> dict:
    """Vzhled výstupu: výchozí podle deklarace (plot_defaults) + změny ze záložky Style.

    Parametry:
        line: výstup indikátoru.
        color: barva instance.
        changes: uložené změny výstupu (normalize_plot_style) nebo None.
    """
    return {**plot_defaults(line, color), **(changes or {})}


def _plot_values(values, plot_type: str):
    """Hodnoty výstupu podle druhu vykreslení: mezery uvnitř řady přemostí čára a plocha
    (lineárně) i schodovitá čára (předchozí hodnotou); začátek a konec bez hodnot zůstane prázdný.
    """
    if plot_type not in LINEAR_BRIDGED_PLOT_TYPES + STEP_BRIDGED_PLOT_TYPES:
        return values
    series = pd.Series(values, dtype="float64")
    # Řada bez mezer (běžný případ při živém přepočtu) se nepřepočítává
    if not series.isna().any():
        return series
    if plot_type in LINEAR_BRIDGED_PLOT_TYPES:
        return series.interpolate(limit_area="inside")
    # Předchozí hodnota jen do poslední platné hodnoty (konec řady zůstane prázdný)
    return series.ffill().where(series.bfill().notna())


def _series_payload(line: Line, plot: dict, instance_color: str, times, labels_on_scale: bool) -> dict:
    """Jedna série pro JS graf podle vzhledu výstupu (záložka Style).

    Parametry:
        line: výstup indikátoru.
        plot: vzhled výstupu (plot_defaults + změny uživatele).
        instance_color: barva instance (výstup bez barvy, který není histogram).
        times: časy svíček v s UTC.
        labels_on_scale: popisek poslední hodnoty na cenové ose (Labels on price scale).

    Vrací {"name", "color", "type", "series", "visible", "options", "data"};
    series je druh série Lightweight Charts ("line", "area", "histogram"),
    options její volby pro daný druh vykreslení.
    """
    plot_type = plot["type"]
    series_kind = PLOT_SERIES.get(plot_type, "line")
    bars = series_kind == "histogram"
    color = plot["color"] or (None if bars else instance_color)
    points = _points(times, _plot_values(line.values, plot_type))
    options: dict = {"priceLineVisible": plot["priceLine"], "lastValueVisible": labels_on_scale}
    line_style = LINE_STYLE_VALUES[plot["lineStyle"]]
    if bars:
        if color:
            options["color"] = color
        else:
            # Histogram obarvený podle znaménka (prázdné body zůstanou bez barvy)
            points = [
                {**point, "color": UP_COLOR if point["value"] >= 0 else DOWN_COLOR} if "value" in point else point
                for point in points
            ]
    elif series_kind == "area":
        # Plocha: čára s výplní k dolnímu okraji (výplň z barvy čáry dopočítá JS graf)
        options.update(lineColor=color, lineWidth=plot["width"], lineStyle=line_style)
    else:
        # Čáry a body: volby z deklarace výstupu, nad nimi vzhled ze záložky Style –
        # schodovitá čára (lineType 1), body bez čáry, kosočtverce schodovité čáry
        # jako body na čáře (Lightweight Charts kreslí body kulaté)
        options.update(line.options or {})
        options.update(
            color=color,
            lineWidth=plot["width"],
            lineStyle=line_style,
            lineType=1 if plot_type in STEP_PLOT_TYPES else 0,
            lineVisible=plot_type != "circles",
            pointMarkersVisible=plot_type in POINT_PLOT_TYPES,
            pointMarkersRadius=plot["width"] + (0.5 if plot_type == "circles" else 1.5),
        )
    return {
        "name": line.name,
        "color": color,
        "type": plot_type,
        "series": series_kind,
        "visible": plot["visible"],
        "options": options,
        "data": points,
    }


def sample_lines(indicator: Indicator, params: dict) -> list[Line]:
    """Výstupy indikátoru pro záložku Style – výpočet nad umělými svíčkami.

    Záložka Style potřebuje znát výstupy (názvy, barvy, druhy) i na timeframu,
    kde se indikátor nepočítá; chyba výpočtu = žádné výstupy.

    Parametry:
        indicator: indikátor.
        params: parametry instance (počet svíček se řídí i required_bars).
    """
    count = max(SAMPLE_BARS, indicator.required_bars(params) + 100)
    # Náhodná procházka s pevným semínkem (stejné výstupy při každém otevření)
    rng = random.Random(7)
    price = 100.0
    candles = []
    for index in range(count):
        open_price = price
        price = max(1.0, price * (1 + rng.gauss(0, 0.01)))
        high = max(open_price, price) * (1 + abs(rng.gauss(0, 0.003)))
        low = min(open_price, price) * (1 - abs(rng.gauss(0, 0.003)))
        candles.append(Candle(index * 3_600_000, open_price, high, low, price, rng.uniform(100, 1000)))
    try:
        return list(indicator.compute(candles_frame(candles), params, make_context("", "", "1h", candles)).lines)
    except Exception:
        logger.exception("Výstupy indikátoru %s pro záložku Style nelze zjistit", indicator.id)
        return []


def _finite(*values: float) -> bool:
    return all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)


def compute_indicators(indicators: list[dict], candles: list[Candle], ctx: Optional[Context] = None) -> dict:
    """Spočítá aktivní instance indikátorů; vrací payload pro JS graf.

    overlays = čáry přes hlavní graf, panes = samostatné panely s úrovněmi,
    segments / markers = úsečky a značky indikátorů v hlavním grafu,
    groups = jedna položka na instanci pro legendu (název, viditelnost,
    panel, signals = lze nastavit alert na signál). Instance bez dat, nenačteného indikátoru, s chybou výpočtu
    nebo skrytá na timeframu grafu (záložka Visibility, ctx.timeframe) v legendě zůstane, jen bez čar;
    úsečky se vynechají při vypnutých Lines (záložka Style).

    Parametry:
        indicators: instance [{"id", "kind", "params", "visible"?, "color"?}].
        candles: svíčky vzestupně podle času.
        ctx: informace o instrumentu (None = prázdný kontext).
    """
    overlays: list[dict] = []
    panes: list[dict] = []
    groups: list[dict] = []
    segments: list[dict] = []
    markers: list[dict] = []
    ctx = ctx or Context()
    df = candles_frame(candles)

    for index, spec in enumerate(indicators):
        indicator = get_indicator(spec["kind"])
        color = indicator_color(spec, index)
        style = normalize_indicator_style(spec.get("style"))
        title = indicator_title(spec, style)
        # Skupina pro legendu: přesnost a zobrazení hodnot podle Output values (záložka Style)
        group = {"id": spec["id"], "title": title, "visible": spec.get("visible", True) is not False, "pane": None,
                 "signals": bool(indicator and indicator.has_signals), "precision": style["precision"],
                 "values": style["values_in_status"]}
        groups.append(group)
        if indicator is None or not candles or not timeframe_visible(spec.get("visibility"), ctx.timeframe):
            continue
        try:
            result = indicator.compute(df, spec["params"], ctx)
        except Exception:
            # Chyba výpočtu jedné instance (degenerovaná data, chyba v indikátoru)
            # nesmí shodit načtení grafu – instance zůstane v legendě bez čar
            logger.exception("Výpočet indikátoru %s selhal", spec["kind"])
            group["title"] = f"{title} (chyba výpočtu)"
            continue
        # Vzhled každého výstupu: výchozí podle deklarace + změny ze záložky Style
        payloads = [
            _series_payload(line, resolve_plot(line, color, style["plots"].get(line.name)), color, df["time"],
                            style["labels_on_scale"])
            for line in result.lines
        ]
        if payloads and not indicator.overlay:
            pane_id = f"pane-{spec['id']}"
            group["pane"] = pane_id
            panes.append({"paneId": pane_id, "group": spec["id"], "kind": spec["kind"], "title": title,
                          "series": payloads, "levels": list(result.levels), "precision": style["precision"]})
        else:
            overlays.extend({**payload, "group": spec["id"]} for payload in payloads)
        # Úsečky jen se zapnutými Lines (záložka Style) a s platnými souřadnicemi
        if style["lines"]:
            segments.extend(
                {"group": spec["id"], "time1": int(seg.time1), "price1": float(seg.price1),
                 "time2": int(seg.time2), "price2": float(seg.price2),
                 "color": seg.color, "width": seg.width, "style": seg.style}
                for seg in result.segments
                if _finite(seg.time1, seg.price1, seg.time2, seg.price2)
            )
        markers.extend(
            {"group": spec["id"], "time": int(marker.time), "position": marker.position,
             "shape": marker.shape, "color": marker.color, "text": marker.text}
            for marker in result.markers
        )

    return {"overlays": overlays, "panes": panes, "groups": groups, "segments": segments, "markers": markers}


def tail_payload(payload: dict, points: int = TAIL_POINTS) -> dict:
    """Zkrátí payload na poslední body sérií (příznak "tail" pro JS graf).

    Úsečky a značky se posílají celé – po každém přepočtu se mohou změnit
    i starší (nový signál, posun indexů) a je jich málo.
    """
    return {
        "tail": True,
        "overlays": [{**overlay, "data": overlay["data"][-points:]} for overlay in payload["overlays"]],
        "panes": [
            {**pane, "series": [{**series, "data": series["data"][-points:]} for series in pane["series"]]}
            for pane in payload["panes"]
        ],
        "segments": payload["segments"],
        "markers": payload["markers"],
    }
