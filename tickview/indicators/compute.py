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
import uuid
from typing import Optional

import pandas as pd

from tickview.indicators.api import (
    DOWN_COLOR,
    PALETTE,
    UP_COLOR,
    Context,
    Indicator,
    Result,
)
from tickview.indicators.registry import get_indicator
from tickview.logic.styles import normalize_color
from tickview.providers.base import TIMEFRAME_MS, Candle

logger = logging.getLogger(__name__)

# Kolik posledních bodů každé série posílá živý přepočet (předchozí svíčka se
# mohla uzavřít, poslední je rozpracovaná; starší hodnoty se nemění)
TAIL_POINTS = 3


# ---------- instance ----------


def indicator_color(spec: dict, index: int) -> str:
    """Barva instance indikátoru: vlastní, jinak z palety podle pořadí."""
    return spec.get("color") or PALETTE[index % len(PALETTE)]


def indicator_title(spec: dict) -> str:
    """Krátký název instance pro legendu (např. "SMA 20"); nenačtený indikátor = jeho id."""
    indicator = get_indicator(spec["kind"])
    if indicator is None:
        return f"{spec['kind']} (nenačten)"
    return indicator.legend_title(spec["params"])


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


def normalize_indicators(items) -> list[dict]:
    """Zkontroluje seznam instancí (rozložení, šablona, nastavení).

    Položka bez typu se vynechá. Instance nenačteného indikátoru se zachová
    s původními parametry; instance s neplatnými parametry dostane výchozí
    (změněné schéma indikátoru nesmí instanci zahodit).

    Parametry:
        items: seznam [{"id", "kind", "params", "visible"?, "color"?}] (jiný typ = prázdný seznam).
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


def _series_payload(line, color: str, times) -> dict:
    """Jedna série pro JS graf: čára (barva instance, není-li vlastní) nebo histogram podle znaménka."""
    points = _points(times, line.values)
    if line.type == "histogram":
        # Histogram obarvený podle znaménka (prázdné body zůstanou bez barvy)
        points = [
            {**point, "color": UP_COLOR if point["value"] >= 0 else DOWN_COLOR} if "value" in point else point
            for point in points
        ]
        series_color = line.color
    else:
        series_color = line.color or color
    return {
        "name": line.name,
        "color": series_color,
        "type": line.type,
        "options": line.options,
        "data": points,
    }


def _finite(*values: float) -> bool:
    return all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)


def compute_indicators(indicators: list[dict], candles: list[Candle], ctx: Optional[Context] = None) -> dict:
    """Spočítá aktivní instance indikátorů; vrací payload pro JS graf.

    overlays = čáry přes hlavní graf, panes = samostatné panely s úrovněmi,
    segments / markers = úsečky a značky indikátorů v hlavním grafu,
    groups = jedna položka na instanci pro legendu (název, viditelnost,
    panel). Instance bez dat, nenačteného indikátoru nebo s chybou výpočtu
    v legendě zůstane, jen bez čar.

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
        title = indicator_title(spec)
        group = {"id": spec["id"], "title": title, "visible": spec.get("visible", True) is not False, "pane": None}
        groups.append(group)
        if indicator is None or not candles:
            continue
        try:
            result = indicator.compute(df, spec["params"], ctx)
        except Exception:
            # Chyba výpočtu jedné instance (degenerovaná data, chyba v indikátoru)
            # nesmí shodit načtení grafu – instance zůstane v legendě bez čar
            logger.exception("Výpočet indikátoru %s selhal", spec["kind"])
            group["title"] = f"{title} (chyba výpočtu)"
            continue
        payloads = [_series_payload(line, color, df["time"]) for line in result.lines]
        if payloads and not indicator.overlay:
            pane_id = f"pane-{spec['id']}"
            group["pane"] = pane_id
            panes.append({"paneId": pane_id, "group": spec["id"], "kind": spec["kind"], "title": title,
                          "series": payloads, "levels": list(result.levels)})
        else:
            overlays.extend({**payload, "group": spec["id"]} for payload in payloads)
        # Úsečky a značky (jen s platnými souřadnicemi)
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
