"""Výpočet technických indikátorů nad svíčkami (pandas + pandas-ta).

Definice indikátorů (INDICATOR_DEFS) jsou datové: kategorie a popis pro
nabídku indikátorů, výchozí parametry pro dialog nastavení a umístění
(překryv hlavního grafu / vlastní panel). Výpočet jednotlivých typů je
v _compute_series.
"""

import json
import uuid

import pandas as pd
import pandas_ta

from tickview.logic.styles import normalize_color
from tickview.providers.base import Candle

# Barvy čar indikátorů (cyklicky podle pořadí přidání, pokud uživatel nezvolí vlastní)
PALETTE = ["#2962ff", "#ff9800", "#9c27b0", "#00bcd4", "#f7b500", "#e91e63"]

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"
# Barva signálních / doplňkových čar (MACD signál, %D, …)
SIGNAL_COLOR = "#ff6d00"

# Kategorie nabídky indikátorů (pořadí = pořadí v levém sloupci dialogu)
CATEGORIES = {
    "ma": "Klouzavé průměry",
    "bands": "Pásma a kanály",
    "trend": "Trend",
    "osc": "Oscilátory",
    "volatility": "Volatilita",
    "volume": "Objem",
}

# Definice indikátorů: zkratka (title), celý název (name), kategorie, popis,
# výchozí parametry a zda se kreslí do vlastního panelu (pane)
INDICATOR_DEFS = {
    "sma": {"title": "SMA", "name": "Jednoduchý klouzavý průměr", "category": "ma", "pane": False,
            "desc": "Aritmetický průměr závěrečných cen za periodu.", "params": {"length": 20}},
    "ema": {"title": "EMA", "name": "Exponenciální klouzavý průměr", "category": "ma", "pane": False,
            "desc": "Průměr s větší váhou novějších cen.", "params": {"length": 20}},
    "wma": {"title": "WMA", "name": "Vážený klouzavý průměr", "category": "ma", "pane": False,
            "desc": "Lineárně vážený průměr závěrečných cen.", "params": {"length": 20}},
    "hma": {"title": "HMA", "name": "Hullův klouzavý průměr", "category": "ma", "pane": False,
            "desc": "Rychlý a vyhlazený průměr s malým zpožděním.", "params": {"length": 9}},
    "vwma": {"title": "VWMA", "name": "Objemem vážený klouzavý průměr", "category": "ma", "pane": False,
             "desc": "Průměr cen vážený objemem obchodů.", "params": {"length": 20}},
    "vwap": {"title": "VWAP", "name": "Objemem vážená průměrná cena", "category": "volume", "pane": False,
             "desc": "Průměrná cena vážená objemem od začátku dne (UTC).", "params": {}},
    "bb": {"title": "BB", "name": "Bollinger Bands", "category": "bands", "pane": False,
           "desc": "Klouzavý průměr ± násobek směrodatné odchylky.", "params": {"length": 20, "std": 2.0}},
    "kc": {"title": "KC", "name": "Keltnerovy kanály", "category": "bands", "pane": False,
           "desc": "EMA ± násobek ATR.", "params": {"length": 20, "mult": 2.0}},
    "donchian": {"title": "DC", "name": "Donchianovy kanály", "category": "bands", "pane": False,
                 "desc": "Nejvyšší maximum a nejnižší minimum za periodu.", "params": {"length": 20}},
    "ichimoku": {"title": "Ichimoku", "name": "Ichimoku Kinko Hyo", "category": "trend", "pane": False,
                 "desc": "Tenkan, Kijun, Senkou A/B a Chikou span.",
                 "params": {"tenkan": 9, "kijun": 26, "senkou": 52}},
    "psar": {"title": "SAR", "name": "Parabolic SAR", "category": "trend", "pane": False,
             "desc": "Body stop-and-reverse pod / nad cenou.", "params": {"af": 0.02, "max_af": 0.2}},
    "supertrend": {"title": "Supertrend", "name": "Supertrend", "category": "trend", "pane": False,
                   "desc": "Trendová linie z ATR; zelená = růst, červená = pokles.",
                   "params": {"length": 10, "mult": 3.0}},
    "adx": {"title": "ADX", "name": "Average Directional Index", "category": "trend", "pane": True,
            "desc": "Síla trendu (ADX) a směrové indikátory +DI / −DI.", "params": {"length": 14}},
    "rsi": {"title": "RSI", "name": "Relative Strength Index", "category": "osc", "pane": True,
            "desc": "Oscilátor 0–100, překoupeno nad 70, přeprodáno pod 30.", "params": {"length": 14}},
    "macd": {"title": "MACD", "name": "MACD", "category": "osc", "pane": True,
             "desc": "Rozdíl dvou EMA, signální linie a histogram.",
             "params": {"fast": 12, "slow": 26, "signal": 9}},
    "stoch": {"title": "Stoch", "name": "Stochastický oscilátor", "category": "osc", "pane": True,
              "desc": "Poloha závěru v rozpětí periody (%K, %D).", "params": {"k": 14, "d": 3, "smooth": 3}},
    "stochrsi": {"title": "Stoch RSI", "name": "Stochastické RSI", "category": "osc", "pane": True,
                 "desc": "Stochastický oscilátor aplikovaný na RSI.",
                 "params": {"length": 14, "rsi_length": 14, "k": 3, "d": 3}},
    "cci": {"title": "CCI", "name": "Commodity Channel Index", "category": "osc", "pane": True,
            "desc": "Odchylka ceny od průměru; hranice ±100.", "params": {"length": 20}},
    "willr": {"title": "%R", "name": "Williams %R", "category": "osc", "pane": True,
              "desc": "Oscilátor −100–0, překoupeno nad −20.", "params": {"length": 14}},
    "mom": {"title": "MOM", "name": "Momentum", "category": "osc", "pane": True,
            "desc": "Rozdíl závěru a závěru před N svíčkami.", "params": {"length": 10}},
    "roc": {"title": "ROC", "name": "Rate of Change", "category": "osc", "pane": True,
            "desc": "Procentní změna ceny za periodu.", "params": {"length": 9}},
    "ao": {"title": "AO", "name": "Awesome Oscillator", "category": "osc", "pane": True,
           "desc": "Rozdíl SMA mediánové ceny (5 a 34).", "params": {"fast": 5, "slow": 34}},
    "trix": {"title": "TRIX", "name": "TRIX", "category": "osc", "pane": True,
             "desc": "Změna trojitě vyhlazené EMA a signální linie.", "params": {"length": 18, "signal": 9}},
    "tsi": {"title": "TSI", "name": "True Strength Index", "category": "osc", "pane": True,
            "desc": "Dvakrát vyhlazené momentum a signální linie.",
            "params": {"fast": 13, "slow": 25, "signal": 13}},
    "atr": {"title": "ATR", "name": "Average True Range", "category": "volatility", "pane": True,
            "desc": "Průměrné skutečné rozpětí svíček.", "params": {"length": 14}},
    "obv": {"title": "OBV", "name": "On Balance Volume", "category": "volume", "pane": True,
            "desc": "Kumulativní objem podle směru svíček.", "params": {}},
    "mfi": {"title": "MFI", "name": "Money Flow Index", "category": "volume", "pane": True,
            "desc": "RSI vážené objemem; hranice 20 / 80.", "params": {"length": 14}},
    "cmf": {"title": "CMF", "name": "Chaikin Money Flow", "category": "volume", "pane": True,
            "desc": "Tok peněz za periodu (−1 až 1).", "params": {"length": 20}},
}

# České popisky parametrů v dialogu
PARAM_LABELS = {
    "length": "Délka (perioda)",
    "std": "Počet směrodatných odchylek",
    "mult": "Násobek",
    "fast": "Rychlá perioda",
    "slow": "Pomalá perioda",
    "signal": "Signální perioda",
    "k": "%K perioda",
    "d": "%D vyhlazení",
    "smooth": "Vyhlazení %K",
    "rsi_length": "Délka RSI",
    "af": "Krok zrychlení (AF)",
    "max_af": "Maximální AF",
    "tenkan": "Tenkan (konverzní linie)",
    "kijun": "Kijun (základní linie)",
    "senkou": "Senkou B (perioda)",
}

# Horní mez period (delší periody nedávají nad načtenou historií smysl)
MAX_LENGTH = 1000


def indicator_color(spec: dict, index: int) -> str:
    """Barva instance indikátoru: vlastní, jinak z palety podle pořadí."""
    return spec.get("color") or PALETTE[index % len(PALETTE)]


def indicator_title(spec: dict) -> str:
    """Krátký název instance indikátoru pro legendu (např. "SMA 20", "BB 20 2")."""
    values = " ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in spec["params"].values())
    return (INDICATOR_DEFS[spec["kind"]]["title"] + " " + values).strip()


def normalize_params(kind: str, values: dict) -> dict:
    """Ověří a převede parametry indikátoru z dialogu nebo z uloženého nastavení.

    Celočíselné parametry (periody) musí být celá čísla 1–MAX_LENGTH,
    desetinné parametry (násobky, AF) kladná čísla nejvýš 10; rychlá perioda
    musí být kratší než pomalá. Chybějící hodnota se nahradí výchozí.

    Parametry:
        kind: typ indikátoru (klíč INDICATOR_DEFS).
        values: hodnoty parametrů {název: číslo nebo None}.

    Vrací normalizované parametry; při neplatné hodnotě vyhodí ValueError
    s českou zprávou pro uživatele.
    """
    if kind not in INDICATOR_DEFS:
        raise ValueError(f"Neznámý indikátor: {kind}")
    params: dict = {}
    for name, default in INDICATOR_DEFS[kind]["params"].items():
        value = values.get(name)
        if value is None:
            value = default
        label = PARAM_LABELS.get(name, name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: zadejte číslo.") from None
        if isinstance(default, int):
            # Perioda musí být celé kladné číslo
            if not number.is_integer() or not 1 <= number <= MAX_LENGTH:
                raise ValueError(f"{label}: zadejte celé číslo 1–{MAX_LENGTH}.")
            params[name] = int(number)
        else:
            if not 0 < number <= 10:
                raise ValueError(f"{label}: zadejte kladné číslo nejvýš 10.")
            params[name] = number
    # Dvojice rychlá/pomalá perioda (MACD, AO, TSI) musí dávat smysl
    if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
        raise ValueError("Rychlá perioda musí být kratší než pomalá.")
    return params


def new_indicator(kind: str, params: dict | None = None) -> dict:
    """Vytvoří novou instanci indikátoru s výchozími (nebo zadanými) parametry."""
    return {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "params": normalize_params(kind, params or {}),
        "visible": True,
    }


def load_saved_indicators(raw: str | None) -> list[dict]:
    """Načte aktivní indikátory uložené v nastavení (JSON); neplatné položky vynechá.

    Parametry:
        raw: JSON seznam [{"id", "kind", "params", "visible"?, "color"?}] nebo None.
    """
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    return normalize_indicators(items)


def normalize_indicators(items) -> list[dict]:
    """Zkontroluje seznam indikátorů (rozložení, šablona, nastavení); neplatné položky vynechá.

    Parametry:
        items: seznam [{"id", "kind", "params", "visible"?, "color"?}] (jiný typ = prázdný seznam).
    """
    indicators: list[dict] = []
    for item in items if isinstance(items, list) else []:
        try:
            params = normalize_params(item["kind"], item.get("params") or {})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        spec = {
            "id": str(item.get("id") or uuid.uuid4().hex[:8]),
            "kind": item["kind"],
            "params": params,
            "visible": item.get("visible", True) is not False,
        }
        # Vlastní barva jen ve tvaru #rrggbb (jinak výchozí z palety)
        if color := normalize_color(item.get("color")):
            spec["color"] = color
        indicators.append(spec)
    return indicators


def _points(times, values) -> list[dict]:
    """Spojí časy s hodnotami; NaN nahradí prázdným bodem jen s časem.

    Lightweight Charts NaN neumí; prázdný bod (whitespace) ale zachová stejný
    počet bodů jako mají svíčky, takže panely indikátorů sedí na hlavní graf
    i při synchronizaci podle logického indexu.

    Parametry:
        times: časy svíček v sekundách UTC.
        values: hodnoty indikátoru (stejná délka jako times).
    """
    return [
        {"time": int(time)} if pd.isna(value) else {"time": int(time), "value": float(value)}
        for time, value in zip(times, values)
    ]


def _find_column(frame: pd.DataFrame, prefix: str):
    """Najde sloupec pandas-ta podle prefixu (názvy obsahují parametry, např. BBL_20_2.0_2.0).

    Parametry:
        frame: výstup pandas-ta s více sloupci.
        prefix: začátek hledaného názvu sloupce.
    """
    for column in frame.columns:
        if column.startswith(prefix):
            return column
    return None


def _columns(frame, specs: list[tuple]) -> list[tuple]:
    """Vybere sloupce víceřádkového výstupu pandas-ta podle prefixů.

    Parametry:
        frame: DataFrame z pandas-ta (nebo None při krátké historii).
        specs: [(prefix, název série, barva, extra)] – extra jsou volby série.

    Vrací [(název, hodnoty, barva, extra)] jen pro nalezené sloupce.
    """
    if frame is None:
        return []
    result = []
    for prefix, name, color, extra in specs:
        column = _find_column(frame, prefix)
        if column is not None:
            result.append((name, frame[column], color, extra))
    return result


def _vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP s resetem na začátku každého dne (UTC) z typické ceny a objemu."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df["time"] // 86400
    volume = df["volume"]
    cumulative_pv = (typical * volume).groupby(day).cumsum()
    cumulative_volume = volume.groupby(day).cumsum()
    # Den bez objemu (např. index) by dělil nulou → NaN (prázdný bod)
    return cumulative_pv / cumulative_volume.where(cumulative_volume > 0)


def _compute_series(kind: str, p: dict, df: pd.DataFrame, color: str):
    """Spočítá série jednoho indikátoru.

    Parametry:
        kind: typ indikátoru.
        p: normalizované parametry.
        df: svíčky (sloupce time, open, high, low, close, volume).
        color: hlavní barva indikátoru.

    Vrací (série, úrovně): série = [(název, hodnoty, barva, extra)], kde extra
    je slovník voleb (type="histogram", options pro LWC, colors=histogram
    podle znaménka); úrovně = vodorovné linky panelu. Prázdné série = málo dat.
    """
    h, l, c, v = df["high"], df["low"], df["close"], df["volume"]
    dashed = {"options": {"lineStyle": 2}}
    histogram = {"type": "histogram"}

    def single(name, values, levels=None, extra=None):
        # Jednosériový indikátor; pandas-ta při krátké historii vrací None
        series_color = None if extra is histogram else color
        return ([(name, values, series_color, extra or {})] if values is not None else []), levels or []

    def bands(frame, prefix_upper, prefix_middle, prefix_lower):
        # Pásma (horní / střed / dolní) – Bollinger, Keltner, Donchian
        return _columns(frame, [
            (prefix_upper, "Horní", color, {}),
            (prefix_middle, "Střed", SIGNAL_COLOR, {}),
            (prefix_lower, "Dolní", color, {}),
        ]), []

    if kind == "sma":
        return single("SMA", pandas_ta.sma(c, length=p["length"]))
    if kind == "ema":
        return single("EMA", pandas_ta.ema(c, length=p["length"]))
    if kind == "wma":
        return single("WMA", pandas_ta.wma(c, length=p["length"]))
    if kind == "hma":
        return single("HMA", pandas_ta.hma(c, length=p["length"]))
    if kind == "vwma":
        return single("VWMA", pandas_ta.vwma(c, v, length=p["length"]))
    if kind == "vwap":
        return single("VWAP", _vwap(df))
    if kind == "bb":
        return bands(pandas_ta.bbands(c, length=p["length"], std=p["std"]), "BBU", "BBM", "BBL")
    if kind == "kc":
        return bands(pandas_ta.kc(h, l, c, length=p["length"], scalar=p["mult"]), "KCU", "KCB", "KCL")
    if kind == "donchian":
        frame = pandas_ta.donchian(h, l, lower_length=p["length"], upper_length=p["length"])
        return bands(frame, "DCU", "DCM", "DCL")
    if kind == "ichimoku":
        result = pandas_ta.ichimoku(h, l, c, tenkan=p["tenkan"], kijun=p["kijun"], senkou=p["senkou"])
        frame = result[0] if isinstance(result, tuple) else result
        return _columns(frame, [
            ("ITS", "Tenkan", "#2962ff", {}), ("IKS", "Kijun", "#b71c1c", {}),
            ("ISA", "Senkou A", "#43a047", {}), ("ISB", "Senkou B", "#e53935", {}),
            ("ICS", "Chikou", "#8e24aa", {}),
        ]), []
    if kind == "psar":
        frame = pandas_ta.psar(h, l, c, af0=p["af"], af=p["af"], max_af=p["max_af"])
        if frame is None:
            return [], []
        # Body nad/pod cenou sloučíme do jedné série vykreslené jen jako tečky
        long_col = _find_column(frame, "PSARl")
        short_col = _find_column(frame, "PSARs")
        values = frame[long_col].fillna(frame[short_col])
        dots = {"options": {"lineVisible": False, "pointMarkersVisible": True, "pointMarkersRadius": 1.5}}
        return [("SAR", values, color, dots)], []
    if kind == "supertrend":
        frame = pandas_ta.supertrend(h, l, c, length=p["length"], multiplier=p["mult"])
        # Rostoucí a klesající větev zvlášť (každá svou barvou, mezery = whitespace)
        return _columns(frame, [
            ("SUPERTl", "Růst", UP_COLOR, {}), ("SUPERTs", "Pokles", DOWN_COLOR, {}),
        ]), []
    if kind == "adx":
        frame = pandas_ta.adx(h, l, c, length=p["length"])
        return _columns(frame, [
            ("ADX_", "ADX", color, {}), ("DMP", "+DI", UP_COLOR, {}), ("DMN", "−DI", DOWN_COLOR, {}),
        ]), [25]
    if kind == "rsi":
        return single("RSI", pandas_ta.rsi(c, length=p["length"]), [30, 70])
    if kind == "macd":
        frame = pandas_ta.macd(c, fast=p["fast"], slow=p["slow"], signal=p["signal"])
        return _columns(frame, [
            ("MACDh", "Histogram", None, histogram),
            ("MACD_", "MACD", color, {}),
            ("MACDs", "Signál", SIGNAL_COLOR, {}),
        ]), [0]
    if kind == "stoch":
        frame = pandas_ta.stoch(h, l, c, k=p["k"], d=p["d"], smooth_k=p["smooth"])
        return _columns(frame, [("STOCHk", "%K", color, {}), ("STOCHd", "%D", SIGNAL_COLOR, {})]), [20, 80]
    if kind == "stochrsi":
        frame = pandas_ta.stochrsi(c, length=p["length"], rsi_length=p["rsi_length"], k=p["k"], d=p["d"])
        return _columns(frame, [
            ("STOCHRSIk", "%K", color, {}), ("STOCHRSId", "%D", SIGNAL_COLOR, {}),
        ]), [20, 80]
    if kind == "cci":
        return single("CCI", pandas_ta.cci(h, l, c, length=p["length"]), [-100, 100])
    if kind == "willr":
        return single("%R", pandas_ta.willr(h, l, c, length=p["length"]), [-80, -20])
    if kind == "mom":
        return single("MOM", pandas_ta.mom(c, length=p["length"]), [0])
    if kind == "roc":
        return single("ROC", pandas_ta.roc(c, length=p["length"]), [0])
    if kind == "ao":
        return single("AO", pandas_ta.ao(h, l, fast=p["fast"], slow=p["slow"]), [0], histogram)
    if kind == "trix":
        frame = pandas_ta.trix(c, length=p["length"], signal=p["signal"])
        return _columns(frame, [("TRIX_", "TRIX", color, {}), ("TRIXs", "Signál", SIGNAL_COLOR, {})]), [0]
    if kind == "tsi":
        frame = pandas_ta.tsi(c, fast=p["fast"], slow=p["slow"], signal=p["signal"])
        return _columns(frame, [("TSI_", "TSI", color, {}), ("TSIs", "Signál", SIGNAL_COLOR, dashed)]), [0]
    if kind == "atr":
        return single("ATR", pandas_ta.atr(h, l, c, length=p["length"]))
    if kind == "obv":
        return single("OBV", pandas_ta.obv(c, v))
    if kind == "mfi":
        return single("MFI", pandas_ta.mfi(h, l, c, v, length=p["length"]), [20, 80])
    if kind == "cmf":
        return single("CMF", pandas_ta.cmf(h, l, c, v, length=p["length"]), [0])
    return [], []


def _series_payload(name: str, values, color, extra: dict, times) -> dict:
    """Připraví jednu sérii pro JS graf (čára nebo histogram obarvený podle znaménka)."""
    points = _points(times, values)
    if extra.get("type") == "histogram":
        # Histogram obarvený podle znaménka (prázdné body zůstanou bez barvy)
        points = [
            {**point, "color": UP_COLOR if point["value"] >= 0 else DOWN_COLOR} if "value" in point else point
            for point in points
        ]
    return {
        "name": name,
        "color": color,
        "type": extra.get("type", "line"),
        "options": extra.get("options") or {},
        "data": points,
    }


# Kolik posledních bodů každé série posílá živý přepočet (předchozí svíčka se
# mohla uzavřít, poslední je rozpracovaná; starší hodnoty se nemění)
TAIL_POINTS = 3


def tail_payload(payload: dict, points: int = TAIL_POINTS) -> dict:
    """Zkrátí payload indikátorů na poslední body sérií (příznak "tail" pro JS graf).

    Parametry:
        payload: výstup compute_indicators.
        points: kolik posledních bodů ponechat v každé sérii.
    """
    return {
        "tail": True,
        "overlays": [{**overlay, "data": overlay["data"][-points:]} for overlay in payload["overlays"]],
        "panes": [
            {**pane, "series": [{**series, "data": series["data"][-points:]} for series in pane["series"]]}
            for pane in payload["panes"]
        ],
    }


def compute_indicators(indicators: list[dict], candles: list[Candle]) -> dict:
    """Spočítá aktivní indikátory; vrací payload pro JS graf.

    overlays = čáry přes hlavní graf (průměry, pásma, trend), panes = samostatné
    panely pod grafem (oscilátory, objem…) s úrovněmi. groups = jedna položka
    na instanci indikátoru pro legendu (název, viditelnost, panel); indikátor
    s nedostatkem dat (pandas-ta vrátí None) v legendě zůstane, jen bez čar.

    Parametry:
        indicators: aktivní indikátory [{"id", "kind", "params", "visible"?, "color"?}].
        candles: svíčky vzestupně podle času.
    """
    overlays: list[dict] = []
    panes: list[dict] = []
    groups: list[dict] = []
    df = pd.DataFrame(
        {
            "time": [candle.open_time // 1000 for candle in candles],
            "high": [candle.high for candle in candles],
            "low": [candle.low for candle in candles],
            "close": [candle.close for candle in candles],
            "volume": [candle.volume for candle in candles],
        }
    )

    for index, spec in enumerate(indicators):
        kind = spec["kind"]
        definition = INDICATOR_DEFS[kind]
        color = indicator_color(spec, index)
        title = indicator_title(spec)
        group = {"id": spec["id"], "title": title, "visible": spec.get("visible", True) is not False, "pane": None}
        groups.append(group)
        if not candles:
            continue
        try:
            series, levels = _compute_series(kind, spec["params"], df, color)
        except Exception:
            # Chyba výpočtu jednoho indikátoru (např. degenerovaná data) nesmí
            # shodit načtení grafu – indikátor zůstane v legendě bez čar
            series, levels = [], []
        if not series:
            continue
        payloads = [_series_payload(name, values, c, extra, df["time"]) for name, values, c, extra in series]
        if definition["pane"]:
            pane_id = f"pane-{spec['id']}"
            group["pane"] = pane_id
            panes.append({"paneId": pane_id, "group": spec["id"], "kind": kind, "title": title,
                          "series": payloads, "levels": levels})
        else:
            for payload in payloads:
                overlays.append({**payload, "group": spec["id"]})

    return {"overlays": overlays, "panes": panes, "groups": groups}
