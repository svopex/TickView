"""Vestavěné indikátory (pandas + pandas-ta) deklarované přes rozhraní indikátorů.

Každý indikátor je jedna deklarace: popis pro nabídku, schéma parametrů a
funkce výpočtu, která vrací (čáry, úrovně). Klíče indikátorů a názvy
parametrů se ukládají do nastavení, rozložení a šablon – neměnit. Texty pro
uživatele (názvy, popisy, popisky, názvy čar) jsou označené N_ a překládají
se až při zobrazení; název čáry je zároveň klíčem jejího vzhledu (záložka Style).
"""

from typing import Callable, Optional

import numpy as np
import pandas as pd
import pandas_ta

from tickview.indicators.api import (
    DOWN_COLOR,
    LINE_DASHED,
    SIGNAL_COLOR,
    UP_COLOR,
    N_,
    Context,
    Indicator,
    Line,
    Param,
    Result,
    tr,
    with_alpha,
)

# Funkce výpočtu vestavěného indikátoru: (svíčky, parametry) -> (čáry, úrovně)
ComputeFn = Callable[[pd.DataFrame, dict], tuple[list[Line], list[float]]]


class BuiltinIndicator(Indicator):
    """Vestavěný indikátor složený z deklarace (bez vlastní třídy pro každý typ).

    Parametry:
        id, title, name, category, description, overlay, params: viz Indicator.
        fn: výpočet (svíčky, parametry) -> (čáry, úrovně).
        validate: volitelná kontrola vztahů mezi parametry.
    """

    def __init__(
        self,
        id: str,
        title: str,
        name: str,
        category: str,
        description: str,
        params: list[Param],
        fn: ComputeFn,
        *,
        overlay: bool = True,
        validate: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.id = id
        self.title = title
        self.name = name
        self.category = category
        self.description = description
        self.params = params
        self.overlay = overlay
        self._fn = fn
        self._validate = validate

    def validate(self, params: dict) -> None:
        if self._validate:
            self._validate(params)

    def compute(self, df: pd.DataFrame, params: dict, ctx: Context) -> Result:
        lines, levels = self._fn(df, params)
        return Result(lines=lines, levels=levels)


# ---------- společné parametry ----------


def _length(default: int) -> Param:
    return Param.int("length", default, N_("Délka (perioda)"))


def _fast(default: int) -> Param:
    return Param.int("fast", default, N_("Rychlá perioda"))


def _slow(default: int) -> Param:
    return Param.int("slow", default, N_("Pomalá perioda"))


def _signal(default: int) -> Param:
    return Param.int("signal", default, N_("Signální perioda"))


def _positive(name: str, default: float, label: str, step: float = 0.1) -> Param:
    # Kladný násobek / koeficient (0 vyloučena, nejvýš 10)
    return Param.float(name, default, label, step=step, exclusive_min=True)


def _fast_below_slow(params: dict) -> None:
    # Dvojice rychlá/pomalá perioda (MACD, AO, TSI) musí dávat smysl
    if params["fast"] >= params["slow"]:
        raise ValueError(tr("Rychlá perioda musí být kratší než pomalá."))


# ---------- pomocné funkce výpočtu ----------

# Volby série: přerušovaná čára
DASHED = {"lineStyle": LINE_DASHED}


def _single(name: str, values, levels: Optional[list] = None, histogram: bool = False):
    """Jednosériový indikátor; pandas-ta při krátké historii vrací None (= bez čar)."""
    if values is None:
        return [], levels or []
    return [Line(name, values, None, "histogram" if histogram else "line")], levels or []


def _find_column(frame: pd.DataFrame, prefix: str):
    """Sloupec pandas-ta podle prefixu (názvy obsahují parametry, např. BBL_20_2.0_2.0)."""
    for column in frame.columns:
        if column.startswith(prefix):
            return column
    return None


def _columns(frame, specs: list[tuple]) -> list[Line]:
    """Série z víceřádkového výstupu pandas-ta podle prefixů sloupců.

    Parametry:
        frame: DataFrame z pandas-ta (nebo None při krátké historii).
        specs: [(prefix, název, barva[, typ[, volby]])] – barva None = barva
            instance, výchozí typ "line" bez voleb (typy viz Line.type).
    """
    if frame is None:
        return []
    lines = []
    for spec in specs:
        # Chybějící typ a volby doplníme výchozími (spec má 3–5 položek)
        prefix, name, color, kind, options = (*spec, *("line", {})[len(spec) - 3:])
        column = _find_column(frame, prefix)
        if column is not None:
            lines.append(Line(name, frame[column], color, kind, dict(options)))
    return lines


def _bands(frame, upper: str, middle: str, lower: str):
    """Pásma (horní / střed / dolní) – Bollinger, Keltner, Donchian."""
    return _columns(frame, [
        (upper, N_("Horní"), None),
        (middle, N_("Střed"), SIGNAL_COLOR),
        (lower, N_("Dolní"), None),
    ]), []


# Objem (Volume) jako v TradingView: poloprůhledné sloupce podle směru
# svíčky na vlastní ose v dolní pětině hlavního grafu
VOLUME_UP_COLOR = with_alpha(UP_COLOR, 0.5)
VOLUME_DOWN_COLOR = with_alpha(DOWN_COLOR, 0.5)
VOLUME_OPTIONS = {
    "priceScaleId": "volume",
    "priceFormat": {"type": "volume"},
    "scaleMargins": {"top": 0.82, "bottom": 0},
}


def _volume(df: pd.DataFrame, params: dict):
    """Sloupce objemu obarvené podle směru svíčky; bez objemu (forex
    MIDPOINT, indexy) prázdné body, takže se nic nevykreslí."""
    values = df["volume"].where(df["volume"] > 0)
    colors = np.where(df["close"] >= df["open"], VOLUME_UP_COLOR, VOLUME_DOWN_COLOR).tolist()
    return [Line(N_("Objem"), values, None, "columns", dict(VOLUME_OPTIONS), colors)], []


def _vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP s resetem na začátku každého dne (UTC) z typické ceny a objemu."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df["time"] // 86400
    volume = df["volume"]
    cumulative_pv = (typical * volume).groupby(day).cumsum()
    cumulative_volume = volume.groupby(day).cumsum()
    # Den bez objemu (např. index) by dělil nulou → NaN (prázdný bod)
    return cumulative_pv / cumulative_volume.where(cumulative_volume > 0)


def _psar(df: pd.DataFrame, p: dict):
    # Body nad/pod cenou sloučíme do jedné série vykreslené jen jako tečky
    frame = pandas_ta.psar(df["high"], df["low"], df["close"], af0=p["af"], af=p["af"], max_af=p["max_af"])
    if frame is None:
        return [], []
    values = frame[_find_column(frame, "PSARl")].fillna(frame[_find_column(frame, "PSARs")])
    return [Line("SAR", values, None, "circles")], []


def _ichimoku(df: pd.DataFrame, p: dict):
    result = pandas_ta.ichimoku(df["high"], df["low"], df["close"], tenkan=p["tenkan"], kijun=p["kijun"],
                                senkou=p["senkou"])
    frame = result[0] if isinstance(result, tuple) else result
    return _columns(frame, [
        ("ITS", "Tenkan", "#2962ff"), ("IKS", "Kijun", "#b71c1c"),
        ("ISA", "Senkou A", "#43a047"), ("ISB", "Senkou B", "#e53935"),
        ("ICS", "Chikou", "#8e24aa"),
    ]), []


# Zkratky sloupců svíček ve výpočtech
def _hlc(df: pd.DataFrame):
    return df["high"], df["low"], df["close"]


# ---------- deklarace ----------

BUILTIN_INDICATORS: list[Indicator] = [
    # Klouzavé průměry
    BuiltinIndicator(
        "sma", "SMA", N_("Jednoduchý klouzavý průměr"), "ma", N_("Aritmetický průměr závěrečných cen za periodu."),
        [_length(20)], lambda df, p: _single("SMA", pandas_ta.sma(df["close"], length=p["length"])),
    ),
    BuiltinIndicator(
        "ema", "EMA", N_("Exponenciální klouzavý průměr"), "ma", N_("Průměr s větší váhou novějších cen."),
        [_length(20)], lambda df, p: _single("EMA", pandas_ta.ema(df["close"], length=p["length"])),
    ),
    BuiltinIndicator(
        "wma", "WMA", N_("Vážený klouzavý průměr"), "ma", N_("Lineárně vážený průměr závěrečných cen."),
        [_length(20)], lambda df, p: _single("WMA", pandas_ta.wma(df["close"], length=p["length"])),
    ),
    BuiltinIndicator(
        "hma", "HMA", N_("Hullův klouzavý průměr"), "ma", N_("Rychlý a vyhlazený průměr s malým zpožděním."),
        [_length(9)], lambda df, p: _single("HMA", pandas_ta.hma(df["close"], length=p["length"])),
    ),
    BuiltinIndicator(
        "vwma", "VWMA", N_("Objemem vážený klouzavý průměr"), "ma", N_("Průměr cen vážený objemem obchodů."),
        [_length(20)], lambda df, p: _single("VWMA", pandas_ta.vwma(df["close"], df["volume"], length=p["length"])),
    ),
    BuiltinIndicator(
        "volume", "Vol", N_("Objem (Volume)"), "volume",
        N_("Objem svíček jako sloupce v dolní části grafu (barva podle směru svíčky)."), [], _volume,
    ),
    BuiltinIndicator(
        "vwap", "VWAP", N_("Objemem vážená průměrná cena"), "volume",
        N_("Průměrná cena vážená objemem od začátku dne (UTC)."), [], lambda df, p: _single("VWAP", _vwap(df)),
    ),
    # Pásma a kanály
    BuiltinIndicator(
        "bb", "BB", "Bollinger Bands", "bands", N_("Klouzavý průměr ± násobek směrodatné odchylky."),
        [_length(20), _positive("std", 2.0, N_("Počet směrodatných odchylek"))],
        lambda df, p: _bands(pandas_ta.bbands(df["close"], length=p["length"], std=p["std"]), "BBU", "BBM", "BBL"),
    ),
    BuiltinIndicator(
        "kc", "KC", N_("Keltnerovy kanály"), "bands", N_("EMA ± násobek ATR."),
        [_length(20), _positive("mult", 2.0, N_("Násobek"))],
        lambda df, p: _bands(pandas_ta.kc(*_hlc(df), length=p["length"], scalar=p["mult"]), "KCU", "KCB", "KCL"),
    ),
    BuiltinIndicator(
        "donchian", "DC", N_("Donchianovy kanály"), "bands", N_("Nejvyšší maximum a nejnižší minimum za periodu."),
        [_length(20)],
        lambda df, p: _bands(
            pandas_ta.donchian(df["high"], df["low"], lower_length=p["length"], upper_length=p["length"]),
            "DCU", "DCM", "DCL",
        ),
    ),
    # Trend
    BuiltinIndicator(
        "ichimoku", "Ichimoku", "Ichimoku Kinko Hyo", "trend", N_("Tenkan, Kijun, Senkou A/B a Chikou span."),
        [Param.int("tenkan", 9, N_("Tenkan (konverzní linie)")), Param.int("kijun", 26, N_("Kijun (základní linie)")),
         Param.int("senkou", 52, N_("Senkou B (perioda)"))],
        _ichimoku,
    ),
    BuiltinIndicator(
        "psar", "SAR", "Parabolic SAR", "trend", N_("Body stop-and-reverse pod / nad cenou."),
        [_positive("af", 0.02, N_("Krok zrychlení (AF)"), 0.01), _positive("max_af", 0.2, N_("Maximální AF"), 0.01)],
        _psar,
    ),
    BuiltinIndicator(
        "supertrend", "Supertrend", "Supertrend", "trend", N_("Trendová linie z ATR; zelená = růst, červená = pokles."),
        [_length(10), _positive("mult", 3.0, N_("Násobek"))],
        # Rostoucí a klesající větev zvlášť (každá svou barvou, čára přerušená
        # tam, kde větev nemá hodnotu)
        lambda df, p: (_columns(pandas_ta.supertrend(*_hlc(df), length=p["length"], multiplier=p["mult"]), [
            ("SUPERTl", N_("Růst"), UP_COLOR, "linebr"), ("SUPERTs", N_("Pokles"), DOWN_COLOR, "linebr"),
        ]), []),
    ),
    BuiltinIndicator(
        "adx", "ADX", "Average Directional Index", "trend", N_("Síla trendu (ADX) a směrové indikátory +DI / −DI."),
        [_length(14)],
        lambda df, p: (_columns(pandas_ta.adx(*_hlc(df), length=p["length"]), [
            ("ADX_", "ADX", None), ("DMP", "+DI", UP_COLOR),
            ("DMN", "−DI", DOWN_COLOR),
        ]), [25]),
        overlay=False,
    ),
    # Oscilátory
    BuiltinIndicator(
        "rsi", "RSI", "Relative Strength Index", "osc", N_("Oscilátor 0–100, překoupeno nad 70, přeprodáno pod 30."),
        [_length(14)], lambda df, p: _single("RSI", pandas_ta.rsi(df["close"], length=p["length"]), [30, 70]),
        overlay=False,
    ),
    BuiltinIndicator(
        "macd", "MACD", "MACD", "osc", N_("Rozdíl dvou EMA, signální linie a histogram."),
        [_fast(12), _slow(26), _signal(9)],
        lambda df, p: (_columns(pandas_ta.macd(df["close"], fast=p["fast"], slow=p["slow"], signal=p["signal"]), [
            ("MACDh", "Histogram", None, "histogram", {}),
            ("MACD_", "MACD", None),
            ("MACDs", N_("Signál"), SIGNAL_COLOR),
        ]), [0]),
        overlay=False, validate=_fast_below_slow,
    ),
    BuiltinIndicator(
        "stoch", "Stoch", N_("Stochastický oscilátor"), "osc", N_("Poloha závěru v rozpětí periody (%K, %D)."),
        [Param.int("k", 14, N_("%K perioda")), Param.int("d", 3, N_("%D vyhlazení")),
         Param.int("smooth", 3, N_("Vyhlazení %K"))],
        lambda df, p: (_columns(pandas_ta.stoch(*_hlc(df), k=p["k"], d=p["d"], smooth_k=p["smooth"]), [
            ("STOCHk", "%K", None), ("STOCHd", "%D", SIGNAL_COLOR),
        ]), [20, 80]),
        overlay=False,
    ),
    BuiltinIndicator(
        "stochrsi", "Stoch RSI", N_("Stochastické RSI"), "osc", N_("Stochastický oscilátor aplikovaný na RSI."),
        [_length(14), Param.int("rsi_length", 14, N_("Délka RSI")), Param.int("k", 3, N_("%K perioda")),
         Param.int("d", 3, N_("%D vyhlazení"))],
        lambda df, p: (_columns(
            pandas_ta.stochrsi(df["close"], length=p["length"], rsi_length=p["rsi_length"], k=p["k"], d=p["d"]),
            [("STOCHRSIk", "%K", None), ("STOCHRSId", "%D", SIGNAL_COLOR)],
        ), [20, 80]),
        overlay=False,
    ),
    BuiltinIndicator(
        "cci", "CCI", "Commodity Channel Index", "osc", N_("Odchylka ceny od průměru; hranice ±100."),
        [_length(20)], lambda df, p: _single("CCI", pandas_ta.cci(*_hlc(df), length=p["length"]), [-100, 100]),
        overlay=False,
    ),
    BuiltinIndicator(
        "willr", "%R", "Williams %R", "osc", N_("Oscilátor −100–0, překoupeno nad −20."),
        [_length(14)], lambda df, p: _single("%R", pandas_ta.willr(*_hlc(df), length=p["length"]), [-80, -20]),
        overlay=False,
    ),
    BuiltinIndicator(
        "mom", "MOM", "Momentum", "osc", N_("Rozdíl závěru a závěru před N svíčkami."),
        [_length(10)], lambda df, p: _single("MOM", pandas_ta.mom(df["close"], length=p["length"]), [0]),
        overlay=False,
    ),
    BuiltinIndicator(
        "roc", "ROC", "Rate of Change", "osc", N_("Procentní změna ceny za periodu."),
        [_length(9)], lambda df, p: _single("ROC", pandas_ta.roc(df["close"], length=p["length"]), [0]),
        overlay=False,
    ),
    BuiltinIndicator(
        "ao", "AO", "Awesome Oscillator", "osc", N_("Rozdíl SMA mediánové ceny (5 a 34)."),
        [_fast(5), _slow(34)],
        lambda df, p: _single("AO", pandas_ta.ao(df["high"], df["low"], fast=p["fast"], slow=p["slow"]), [0],
                              histogram=True),
        overlay=False, validate=_fast_below_slow,
    ),
    BuiltinIndicator(
        "trix", "TRIX", "TRIX", "osc", N_("Změna trojitě vyhlazené EMA a signální linie."),
        [_length(18), _signal(9)],
        lambda df, p: (_columns(pandas_ta.trix(df["close"], length=p["length"], signal=p["signal"]), [
            ("TRIX_", "TRIX", None), ("TRIXs", N_("Signál"), SIGNAL_COLOR),
        ]), [0]),
        overlay=False,
    ),
    BuiltinIndicator(
        "tsi", "TSI", "True Strength Index", "osc", N_("Dvakrát vyhlazené momentum a signální linie."),
        [_fast(13), _slow(25), _signal(13)],
        lambda df, p: (_columns(pandas_ta.tsi(df["close"], fast=p["fast"], slow=p["slow"], signal=p["signal"]), [
            ("TSI_", "TSI", None), ("TSIs", N_("Signál"), SIGNAL_COLOR, "line", DASHED),
        ]), [0]),
        overlay=False, validate=_fast_below_slow,
    ),
    # Volatilita
    BuiltinIndicator(
        "atr", "ATR", "Average True Range", "volatility", N_("Průměrné skutečné rozpětí svíček."),
        [_length(14)], lambda df, p: _single("ATR", pandas_ta.atr(*_hlc(df), length=p["length"])),
        overlay=False,
    ),
    # Objem
    BuiltinIndicator(
        "obv", "OBV", "On Balance Volume", "volume", N_("Kumulativní objem podle směru svíček."),
        [], lambda df, p: _single("OBV", pandas_ta.obv(df["close"], df["volume"])),
        overlay=False,
    ),
    BuiltinIndicator(
        "mfi", "MFI", "Money Flow Index", "volume", N_("RSI vážené objemem; hranice 20 / 80."),
        [_length(14)],
        lambda df, p: _single("MFI", pandas_ta.mfi(*_hlc(df), df["volume"], length=p["length"]), [20, 80]),
        overlay=False,
    ),
    BuiltinIndicator(
        "cmf", "CMF", "Chaikin Money Flow", "volume", N_("Tok peněz za periodu (−1 až 1)."),
        [_length(20)],
        lambda df, p: _single("CMF", pandas_ta.cmf(*_hlc(df), df["volume"], length=p["length"]), [0]),
        overlay=False,
    ),
]
