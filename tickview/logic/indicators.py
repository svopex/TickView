"""Výpočet technických indikátorů nad svíčkami (pandas + pandas-ta)."""

import pandas as pd
import pandas_ta

from tickview.providers.base import Candle

# Barvy čar indikátorů (cyklicky podle pořadí přidání)
PALETTE = ["#2962ff", "#ff9800", "#9c27b0", "#00bcd4", "#f7b500", "#e91e63"]

# Definice indikátorů: zobrazený název + výchozí parametry pro dialog
INDICATOR_DEFS = {
    "sma": {"title": "SMA", "params": {"length": 20}},
    "ema": {"title": "EMA", "params": {"length": 20}},
    "bb": {"title": "Bollinger Bands", "params": {"length": 20, "std": 2.0}},
    "rsi": {"title": "RSI", "params": {"length": 14}},
    "macd": {"title": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}},
}

# České popisky parametrů v dialogu
PARAM_LABELS = {
    "length": "Délka (perioda)",
    "std": "Počet směrodatných odchylek",
    "fast": "Rychlá EMA",
    "slow": "Pomalá EMA",
    "signal": "Signální EMA",
}

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"


def _points(times, values) -> list[dict]:
    """Spojí časy s hodnotami; NaN zahodí (Lightweight Charts je neumí)."""
    points = []
    for time, value in zip(times, values):
        if pd.isna(value):
            continue
        points.append({"time": int(time), "value": float(value)})
    return points


def _line(name: str, color: str, df: pd.DataFrame, values) -> dict:
    """Připraví jednu overlay čáru pro hlavní graf."""
    return {"name": name, "color": color, "type": "line", "data": _points(df["time"], values)}


def _find_column(frame: pd.DataFrame, prefix: str):
    """Najde sloupec pandas-ta podle prefixu (názvy obsahují parametry, např. BBL_20_2.0_2.0)."""
    for column in frame.columns:
        if column.startswith(prefix):
            return column
    return None


def compute_indicators(indicators: list[dict], candles: list[Candle]) -> dict:
    """Spočítá aktivní indikátory; vrací payload pro JS graf.

    overlays = čáry přes hlavní graf (SMA, EMA, Bollinger Bands),
    panes = samostatné panely pod grafem (RSI, MACD) s úrovněmi (30/70, 0).
    """
    if not candles:
        return {"overlays": [], "panes": []}
    df = pd.DataFrame(
        {
            "time": [candle.open_time // 1000 for candle in candles],
            "close": [candle.close for candle in candles],
        }
    )
    close = df["close"]
    overlays: list[dict] = []
    panes: list[dict] = []
    color_index = 0

    for spec in indicators:
        kind = spec["kind"]
        params = spec["params"]
        color = PALETTE[color_index % len(PALETTE)]
        color_index += 1

        if kind == "sma":
            length = int(params["length"])
            overlays.append(_line(f"SMA {length}", color, df, pandas_ta.sma(close, length=length)))
        elif kind == "ema":
            length = int(params["length"])
            overlays.append(_line(f"EMA {length}", color, df, pandas_ta.ema(close, length=length)))
        elif kind == "bb":
            length = int(params["length"])
            bb = pandas_ta.bbands(close, length=length, std=float(params["std"]))
            # Tři pásma jednou barvou; názvy sloupců pandas-ta obsahují parametry
            for prefix, name in (
                ("BBL", f"BB dolní {length}"),
                ("BBM", f"BB střed {length}"),
                ("BBU", f"BB horní {length}"),
            ):
                column = _find_column(bb, prefix)
                if column is not None:
                    overlays.append(_line(name, color, df, bb[column]))
        elif kind == "rsi":
            length = int(params["length"])
            values = pandas_ta.rsi(close, length=length)
            panes.append(
                {
                    "paneId": f"pane-{spec['id']}",
                    "kind": "rsi",
                    "title": f"RSI ({length})",
                    "series": [
                        {
                            "name": "RSI",
                            "color": color,
                            "type": "line",
                            "data": _points(df["time"], values),
                        }
                    ],
                    "levels": [30, 70],
                }
            )
        elif kind == "macd":
            fast = int(params["fast"])
            slow = int(params["slow"])
            signal = int(params["signal"])
            macd = pandas_ta.macd(close, fast=fast, slow=slow, signal=signal)
            macd_column = _find_column(macd, "MACD_")
            hist_column = _find_column(macd, "MACDh")
            signal_column = _find_column(macd, "MACDs")
            hist_points = [
                {
                    "time": int(time),
                    "value": float(value),
                    "color": UP_COLOR if value >= 0 else DOWN_COLOR,
                }
                for time, value in zip(df["time"], macd[hist_column])
                if not pd.isna(value)
            ]
            panes.append(
                {
                    "paneId": f"pane-{spec['id']}",
                    "kind": "macd",
                    "title": f"MACD ({fast}, {slow}, {signal})",
                    "series": [
                        {"name": "Histogram", "type": "histogram", "color": None, "data": hist_points},
                        {
                            "name": "MACD",
                            "color": color,
                            "type": "line",
                            "data": _points(df["time"], macd[macd_column]),
                        },
                        {
                            "name": "Signál",
                            "color": "#ff9800",
                            "type": "line",
                            "data": _points(df["time"], macd[signal_column]),
                        },
                    ],
                    "levels": [0],
                }
            )

    return {"overlays": overlays, "panes": panes}
