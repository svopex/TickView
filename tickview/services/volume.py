"""Živý objem pro IBKR forex, kovy a CFD (data MIDPOINT bez objemu) ze zdroje cTrader.

Historii doplňuje objemem logic/cache.fetch_history (enrich_volume); tento
modul doplňuje objem do svíček živého streamu grafu: na pozadí odebírá
živý bar cTrader a objem přiřadí ke svíčce IBKR podle času (logic/volume).
"""

import asyncio
import contextlib
import logging
from dataclasses import replace
from typing import AsyncIterator, Optional

from tickview.logic.cache import volume_source
from tickview.logic.volume import VOLUME_TIMEFRAMES, candle_volume
from tickview.providers.base import TIMEFRAME_MS, Candle, DataProvider, StreamItem, StreamStatus, utc_now_ms

log = logging.getLogger(__name__)

# Kolik období grafu zpět se drží bary zdroje objemu (starší živá svíčka už nepotřebuje)
KEEP_PERIODS = 2


class LiveVolume:
    """Objem živých svíček z živého baru zdroje objemu (odběr na pozadí).

    Bary ({čas otevření: objem}) se doplní historií aktuálního období
    (4h se skládá z hodinových) a průběžně živým barem; starší než
    KEEP_PERIODS období se zahazují.

    Parametry:
        source: zdroj objemu (cTrader).
        symbol: symbol IBKR (symbol u brokera se dohledá na pozadí, aby
            nedostupný cTrader nezdržel start živého streamu).
        timeframe: timeframe grafu.
    """

    def __init__(self, source: DataProvider, symbol: str, timeframe: str) -> None:
        self._source = source
        self._symbol = symbol
        self._timeframe = timeframe
        self._bars: dict[int, float] = {}
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    def _store(self, bar: Candle) -> None:
        # Nový / aktualizovaný bar a zahození barů starších než KEEP_PERIODS období
        self._bars[bar.open_time] = bar.volume
        limit = bar.open_time - KEEP_PERIODS * TIMEFRAME_MS[self._timeframe]
        for time in [time for time in self._bars if time < limit]:
            del self._bars[time]

    async def _run(self) -> None:
        """Dohledá symbol u brokera, doplní bary aktuálního období z historie a čte živý bar."""
        volume_timeframe = VOLUME_TIMEFRAMES[self._timeframe]
        try:
            name = await self._source.volume_symbol(self._symbol)
            if name is None:
                return
            now_ms = utc_now_ms()
            start = now_ms - KEEP_PERIODS * TIMEFRAME_MS[self._timeframe]
            for bar in await self._source.history(name, volume_timeframe, start, now_ms):
                self._store(bar)
            async for item in self._source.stream(name, volume_timeframe):
                if not isinstance(item, StreamStatus):
                    self._store(item)
        except Exception as exc:
            log.warning("Živý objem z %s pro %s: %s", self._source.id, self._symbol, exc)

    def apply(self, candle: Candle) -> Candle:
        """Svíčka s objemem z barů zdroje objemu (beze změny, když bary období nejsou známé)."""
        volume = candle_volume(candle.open_time, self._timeframe, self._bars)
        return candle if volume is None else replace(candle, volume=volume)


async def with_live_volume(
    provider: DataProvider, symbol: str, timeframe: str, stream: AsyncIterator[StreamItem]
) -> AsyncIterator[StreamItem]:
    """Živý stream zdroje se svíčkami doplněnými o objem ze zdroje objemu.

    Instrument s vlastním objemem (nebo bez zapnutého cTrader) prochází beze
    změny; odběr objemu skončí spolu se streamem.
    """
    source = volume_source(provider, symbol, timeframe)
    live = LiveVolume(source, symbol, timeframe) if source is not None else None
    if live is not None:
        live.start()
    try:
        async for item in stream:
            yield live.apply(item) if live is not None and not isinstance(item, StreamStatus) else item
    finally:
        if live is not None:
            await live.stop()
