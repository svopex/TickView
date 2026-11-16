"""Výpočet technických indikátorů nad svíčkami (pandas + pandas-ta)."""

import json
import uuid

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

# Horní mez period (delší periody nedávají nad načtenou historií smysl)
MAX_LENGTH = 1000


def normalize_params(kind: str, values: dict) -> dict:
    """Ověří a převede parametry indikátoru z dialogu nebo z uloženého nastavení.

    Celočíselné parametry (perioda, EMA MACD) musí být celá čísla 1–MAX_LENGTH,
    počet směrodatných odchylek kladné číslo; u MACD musí být rychlá EMA kratší
    než pomalá. Chybějící hodnota se nahradí výchozí.

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
    if kind == "macd" and params["fast"] >= params["slow"]:
        raise ValueError("Rychlá EMA musí být kratší než pomalá.")
    return params


def load_saved_indicators(raw: str | None) -> list[dict]:
    """Načte aktivní indikátory uložené v nastavení (JSON); neplatné položky vynechá.

    Parametry:
        raw: JSON seznam [{"id", "kind", "params"}] nebo None.
    """
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    indicators: list[dict] = []
    for item in items if isinstance(items, list) else []:
        try:
            params = normalize_params(item["kind"], item.get("params") or {})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        indicators.append({"id": str(item.get("id") or uuid.uuid4().hex[:8]), "kind": item["kind"], "params": params})
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


def _line(name: str, color: str, df: pd.DataFrame, values) -> dict:
    """Připraví jednu overlay čáru pro hlavní graf."""
    return {"name": name, "color": color, "type": "line", "data": _points(df["time"], values)}


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

    overlays = čáry přes hlavní graf (SMA, EMA, Bollinger Bands),
    panes = samostatné panely pod grafem (RSI, MACD) s úrovněmi (30/70, 0).
    Indikátor, pro který je málo svíček (pandas-ta vrátí None), se vynechá.

    Parametry:
        indicators: aktivní indikátory [{"id", "kind", "params"}].
        candles: svíčky vzestupně podle času.
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

        # pandas-ta vrací None, když je svíček méně než perioda indikátoru;
        # takový indikátor přeskočíme, místo aby spadlo celé načtení grafu
        if kind == "sma":
            length = int(params["length"])
            values = pandas_ta.sma(close, length=length)
            if values is not None:
                overlays.append(_line(f"SMA {length}", color, df, values))
        elif kind == "ema":
            length = int(params["length"])
            values = pandas_ta.ema(close, length=length)
            if values is not None:
                overlays.append(_line(f"EMA {length}", color, df, values))
        elif kind == "bb":
            length = int(params["length"])
            bb = pandas_ta.bbands(close, length=length, std=float(params["std"]))
            if bb is None:
                continue
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
            if values is None:
                continue
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
            if macd is None:
                continue
            macd_column = _find_column(macd, "MACD_")
            hist_column = _find_column(macd, "MACDh")
            signal_column = _find_column(macd, "MACDs")
            # Histogram obarvený podle znaménka (prázdné body zůstanou bez barvy)
            hist_points = [
                {**point, "color": UP_COLOR if point["value"] >= 0 else DOWN_COLOR}
                if "value" in point
                else point
                for point in _points(df["time"], macd[hist_column])
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
