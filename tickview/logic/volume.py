"""Objem z jiného zdroje pro svíčky bez objemu (IBKR forex, kovy a CFD – data MIDPOINT).

IBKR u forexu, spotových kovů a CFD posílá jen kotace (MIDPOINT) bez objemu.
Objem (tick volume jako v TradingView) se bere z cTrader Open API a přiřadí
se ke svíčkám IBKR podle času. Modul obsahuje jen čisté funkce přiřazení
objemu (mapování symbolů je u zdroje cTrader), aby šel testovat bez sítě.
"""

import bisect
from dataclasses import replace
from typing import Optional

from tickview.providers.base import TIMEFRAME_MS, Candle

# Timeframe barů zdroje objemu pro timeframe grafu: 4h se skládá z hodinových
# (4h svíčky IBKR po víkendu nezačínají na stejné hodině jako u brokera)
VOLUME_TIMEFRAMES = {
    "1m": "1m",
    "2m": "2m",
    "5m": "5m",
    "10m": "10m",
    "15m": "15m",
    "1h": "1h",
    "4h": "1h",
    "1D": "1D",
    "1W": "1W",
}

# Denní a týdenní bary brokera otevírají večer před obchodním dnem (17:00
# New York), IBKR svíčky datuje obchodním dnem (týdenní koncem týdne – pátkem);
# bar objemu proto patří k nejbližší následující svíčce IBKR (do jednoho období)
NEXT_CANDLE_TIMEFRAMES = {"1D", "1W"}

def volume_fetch_range(candles: list[Candle], timeframe: str) -> tuple[int, int]:
    """Interval barů zdroje objemu (UTC ms, včetně) pokrývající svíčky grafu.

    Denní a týdenní bary brokera začínají až o období dřív než svíčka IBKR,
    ke které patří (viz NEXT_CANDLE_TIMEFRAMES).
    """
    step = TIMEFRAME_MS[timeframe]
    start = candles[0].open_time
    end = candles[-1].open_time + step - 1
    if timeframe in NEXT_CANDLE_TIMEFRAMES:
        start -= step
        end = candles[-1].open_time
    return start, end


def _candle_index(opens: list[int], bar_time: int, timeframe: str) -> Optional[int]:
    """Index svíčky (podle seřazených časů otevření), ke které patří bar objemu.

    Intraday: svíčka, která bar obsahuje (poslední otevřená nejpozději v čase
    baru, do délky období). Denní a týdenní: nejbližší svíčka otevřená
    v čase baru nebo po něm, do délky období.
    """
    step = TIMEFRAME_MS[timeframe]
    if timeframe in NEXT_CANDLE_TIMEFRAMES:
        index = bisect.bisect_left(opens, bar_time)
        if index < len(opens) and opens[index] - bar_time < step:
            return index
        return None
    index = bisect.bisect_right(opens, bar_time) - 1
    if index >= 0 and bar_time - opens[index] < step:
        return index
    return None


def merge_volume(candles: list[Candle], volume_bars: list[Candle], timeframe: str) -> list[Candle]:
    """Svíčky s objemem z barů jiného zdroje (součet barů, které ke svíčce patří).

    Svíčky, ke kterým žádný bar nepatří (broker data nemá), si nechají
    původní objem; vstupní seznam se nemění.

    Parametry:
        candles: svíčky grafu seřazené podle času.
        volume_bars: bary zdroje objemu (timeframe podle VOLUME_TIMEFRAMES).
        timeframe: timeframe svíček grafu.
    """
    if not candles or not volume_bars:
        return candles
    opens = [candle.open_time for candle in candles]
    totals: dict[int, float] = {}
    for bar in volume_bars:
        index = _candle_index(opens, bar.open_time, timeframe)
        if index is not None:
            totals[index] = totals.get(index, 0.0) + bar.volume
    if not totals:
        return candles
    return [replace(candle, volume=totals[i]) if i in totals else candle for i, candle in enumerate(candles)]


def candle_volume(open_time: int, timeframe: str, bars: dict[int, float]) -> Optional[float]:
    """Objem živé svíčky ze známých barů zdroje objemu ({čas otevření: objem}).

    Intraday sečte bary otevřené v období svíčky, u denních a týdenních bary
    otevřené v období před ní (viz NEXT_CANDLE_TIMEFRAMES); None = žádný bar.
    """
    step = TIMEFRAME_MS[timeframe]
    if timeframe in NEXT_CANDLE_TIMEFRAMES:
        values = [volume for time, volume in bars.items() if open_time - step < time <= open_time]
    else:
        values = [volume for time, volume in bars.items() if open_time <= time < open_time + step]
    return sum(values) if values else None
