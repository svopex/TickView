"""Testy objemu z jiného zdroje (spuštění: python -m unittest)."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tickview.logic.cache import KlineCache, fetch_history
from tickview.logic.volume import candle_volume, merge_volume, volume_fetch_range
from tickview import providers
from tickview.providers.base import Candle, DataProvider, StreamStatus
from tickview.providers.ctrader import ctrader_candidates, resolve_ctrader_symbol
from tickview.services.volume import with_live_volume

HOUR = 3_600_000
DAY = 24 * HOUR


def _candle(open_time: int, volume: float = 0.0) -> Candle:
    # Svíčka s libovolnými cenami (testy řeší jen čas a objem)
    return Candle(open_time, 1.0, 1.0, 1.0, 1.0, volume)


class SymbolTest(unittest.TestCase):
    """Mapování symbolů IBKR na názvy u cTrader brokera."""

    def test_candidates(self) -> None:
        # Forexový pár se odvodí ze symbolu, CFD a kovy z tabulky, akcie nemají kandidáta
        self.assertEqual(ctrader_candidates("EUR.USD"), ["EURUSD"])
        self.assertIn("NAS100", ctrader_candidates("IBUST100"))
        self.assertEqual(ctrader_candidates("AAPL"), [])

    def test_resolve(self) -> None:
        # Shoda názvu, broker s příponou, ruční přiřazení a chybějící instrument
        available = ["EURUSD", "NAS100", "US500.cash", "GER40"]
        self.assertEqual(resolve_ctrader_symbol("EUR.USD", available), "EURUSD")
        self.assertEqual(resolve_ctrader_symbol("IBUST100", available), "NAS100")
        self.assertEqual(resolve_ctrader_symbol("IBUS500", available), "US500.cash")
        self.assertEqual(resolve_ctrader_symbol("IBDE40", available), "GER40")
        self.assertEqual(resolve_ctrader_symbol("IBUST100", available, {"IBUST100": "nas100"}), "NAS100")
        self.assertIsNone(resolve_ctrader_symbol("IBHK50", available))


class MergeTest(unittest.TestCase):
    """Přiřazení barů objemu ke svíčkám IBKR."""

    def test_intraday_sum(self) -> None:
        # 4h svíčky ze 4 hodinových barů; bar mimo svíčky se ignoruje
        candles = [_candle(0), _candle(4 * HOUR)]
        bars = [_candle(h * HOUR, 10 + h) for h in range(8)] + [_candle(9 * HOUR, 99)]
        merged = merge_volume(candles, bars, "4h")
        self.assertEqual([c.volume for c in merged], [10 + 11 + 12 + 13, 14 + 15 + 16 + 17])

    def test_daily_next_candle(self) -> None:
        # Denní bar brokera otevřený večer patří k následující svíčce IBKR (obchodní den)
        tuesday = 10 * DAY
        candles = [_candle(tuesday), _candle(tuesday + DAY)]
        bars = [_candle(tuesday - 3 * HOUR, 500), _candle(tuesday + DAY - 3 * HOUR, 700)]
        self.assertEqual([c.volume for c in merge_volume(candles, bars, "1D")], [500, 700])
        # Interval barů začíná o den dřív než první svíčka
        self.assertEqual(volume_fetch_range(candles, "1D"), (tuesday - DAY, tuesday + DAY))

    def test_missing_keeps_volume(self) -> None:
        # Svíčka bez barů si nechá původní objem
        candles = [_candle(0, 5), _candle(HOUR, 6)]
        merged = merge_volume(candles, [_candle(0, 42)], "1h")
        self.assertEqual([c.volume for c in merged], [42, 6])

    def test_live_candle_volume(self) -> None:
        # Živá 4h svíčka sečte hodinové bary období, denní bere bar z předchozího večera
        bars = {0: 1.0, HOUR: 2.0, 4 * HOUR: 100.0}
        self.assertEqual(candle_volume(0, "4h", bars), 3.0)
        self.assertIsNone(candle_volume(8 * HOUR, "4h", bars))
        self.assertEqual(candle_volume(DAY, "1D", {DAY - 3 * HOUR: 9.0}), 9.0)


class _FakeProvider(DataProvider):
    """Zdroj s pevnými svíčkami: IBKR bez objemu u forexu / cTrader s objemem a živým barem."""

    def __init__(self, provider_id: str, candles: list[Candle], live: tuple[Candle, ...] = ()) -> None:
        self.id = provider_id
        self.timeframes = {"1h": "1h"}
        self._candles = candles
        self._live = live

    def lacks_volume(self, symbol: str) -> bool:
        # Jako IBKR: forexový pár objem nemá
        return self.id == "ibkr" and "." in symbol

    async def volume_symbol(self, ib_symbol: str):
        # Jako cTrader: EUR.USD → EURUSD
        return ib_symbol.replace(".", "")

    async def search(self, query, limit=30):
        return []

    async def history(self, symbol, timeframe, start_ms, end_ms):
        return [c for c in self._candles if start_ms <= c.open_time <= end_ms]

    async def stream(self, symbol, timeframe):
        # Živé bary, pak čekání (stream končí zrušením)
        for candle in self._live:
            yield candle
        await asyncio.Event().wait()


class EnrichTest(unittest.TestCase):
    """fetch_history doplní IBKR forexu objem z cTrader."""

    def test_fetch_history_adds_volume(self) -> None:
        async def scenario():
            ibkr = _FakeProvider("ibkr", [_candle(h * HOUR) for h in range(3)])
            source = _FakeProvider("ctrader", [_candle(h * HOUR, 100 + h) for h in range(3)])
            with tempfile.TemporaryDirectory() as folder:
                cache = KlineCache(Path(folder) / "test.db")
                await cache.init()
                with mock.patch.object(providers, "PROVIDERS", [ibkr, source]):
                    forex = await fetch_history(ibkr, cache, "EUR.USD", "1h", 0, 2 * HOUR)
                    stock = await fetch_history(ibkr, cache, "AAPL", "1h", 0, 2 * HOUR)
                await cache.close()
            return forex, stock

        forex, stock = asyncio.run(scenario())
        self.assertEqual([c.volume for c in forex], [100, 101, 102])
        # Akcie (data TRADES s vlastním objemem) se nedoplňují
        self.assertEqual([c.volume for c in stock], [0, 0, 0])

    def test_live_stream_volume(self) -> None:
        # Živá svíčka IBKR dostane objem z živého baru cTrader; stavová zpráva projde beze změny
        async def ibkr_stream():
            await asyncio.sleep(0.05)
            yield StreamStatus(True, "ok")
            yield _candle(HOUR)

        async def scenario():
            ibkr = _FakeProvider("ibkr", [])
            source = _FakeProvider("ctrader", [], live=(_candle(HOUR, 77),))
            with mock.patch.object(providers, "PROVIDERS", [ibkr, source]):
                return [item async for item in with_live_volume(ibkr, "EUR.USD", "1h", ibkr_stream())]

        items = asyncio.run(scenario())
        self.assertIsInstance(items[0], StreamStatus)
        self.assertEqual(items[1].volume, 77)


if __name__ == "__main__":
    unittest.main()
