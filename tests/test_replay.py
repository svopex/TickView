"""Testy enginu replaye: plnění příkazů, stopy a cíle, náklady, převod měn, krok zpět, statistiky."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tickview.logic.cache import KlineCache, replay_drawing_source
from tickview.logic.replay import (
    CurrencyConversion,
    ReplayEngine,
    bucket_start,
    clean_time_of_day,
    commands_until,
    equity_curve,
    merge_into,
    normalize_settings,
    plan_conversion,
    position_size,
    round_to_step,
    simulate,
    time_of_day_stopper,
    trade_stats,
    trades_csv,
)
from tickview.providers.base import Candle

MINUTE = 60_000


def bar(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    """Minutová svíčka s pořadím index (čas = index minut)."""
    return Candle(index * MINUTE, open_, high, low, close, 1.0)


def settings(**overrides) -> dict:
    """Nastavení session pro testy: tick 0.01, lot = 1 jednotka, bez nákladů."""
    return normalize_settings({"initial_balance": 1000, "tick": 0.01, "contract_size": 1, **overrides})


class SettingsDefaultsTests(unittest.TestCase):
    def test_futures_defaults(self):
        # Futures: velikost kontraktu od zdroje a celé kontrakty; uložená hodnota má přednost
        futures = normalize_settings({}, "futures", contract_size=50)
        self.assertEqual((futures["contract_size"], futures["lot_step"]), (50, 1.0))
        self.assertEqual(normalize_settings({"contract_size": 5}, "futures", 50)["contract_size"], 5)
        forex = normalize_settings({}, "forex")
        self.assertEqual((forex["contract_size"], forex["lot_step"]), (100_000.0, 0.01))


class EngineFillTests(unittest.TestCase):
    def test_market_buy_fills_at_close_and_hits_target(self):
        engine = ReplayEngine(settings())
        engine.process(bar(0, 100, 101, 99, 100))
        engine.apply({"op": "order", "side": "buy", "type": "market", "qty": 2, "sl": 98, "tp": 103})
        trade = engine.trades[1]
        self.assertEqual(trade.entry_price, 100)
        self.assertEqual(trade.risk, 4)
        engine.process(bar(1, 100, 103.5, 99.5, 103))
        self.assertTrue(trade.closed)
        self.assertEqual(trade.exits[0].reason, "tp")
        self.assertAlmostEqual(trade.realized, 6)
        self.assertAlmostEqual(trade.r_multiple, 1.5)
        self.assertAlmostEqual(engine.balance, 1006)

    def test_stop_and_target_in_same_bar_takes_stop(self):
        engine = ReplayEngine(settings())
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "buy", "type": "market", "qty": 1, "sl": 99, "tp": 101})
        engine.process(bar(1, 100, 102, 98, 100))
        trade = engine.trades[1]
        self.assertEqual(trade.exits[0].reason, "sl")
        self.assertTrue(trade.ambiguous)

    def test_buy_limit_fills_at_level_and_gap_fills_at_open(self):
        engine = ReplayEngine(settings())
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "buy", "type": "limit", "qty": 1, "price": 99})
        engine.apply({"op": "order", "side": "buy", "type": "limit", "qty": 1, "price": 99.5})
        engine.process(bar(1, 99.8, 99.9, 98.9, 99))
        self.assertEqual(engine.trades[1].entry_price, 99)
        self.assertEqual(engine.trades[2].entry_price, 99.5)
        # Gap pod limit: plní se za otevření (lepší cenou)
        engine.apply({"op": "order", "side": "buy", "type": "limit", "qty": 1, "price": 98.5})
        engine.process(bar(2, 97, 97.5, 96, 97))
        self.assertEqual(engine.trades[3].entry_price, 97)

    def test_sell_stop_gap_fills_worse_with_slippage(self):
        engine = ReplayEngine(settings(slippage_ticks=2))
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "sell", "type": "stop", "qty": 1, "price": 99})
        engine.process(bar(1, 98, 98.5, 97.5, 98))
        self.assertAlmostEqual(engine.trades[1].entry_price, 97.98)

    def test_spread_and_commission(self):
        engine = ReplayEngine(settings(spread_ticks=10, commission_per_lot=0.5))
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "buy", "type": "market", "qty": 1})
        trade = engine.trades[1]
        # Nákup na asku (bid + 0.10), komise za vstup
        self.assertAlmostEqual(trade.entry_price, 100.1)
        self.assertAlmostEqual(engine.balance, 999.5)
        engine.process(bar(1, 101, 101, 101, 101))
        engine.apply({"op": "close", "id": 1})
        self.assertAlmostEqual(trade.realized, 0.9 - 1.0)
        self.assertAlmostEqual(engine.balance, 1000 - 0.1)

    def test_short_stop_uses_ask(self):
        engine = ReplayEngine(settings(spread_ticks=10))
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "sell", "type": "market", "qty": 1, "sl": 101})
        # High 100.95 + spread 0.10 = ask 101.05 ≥ stop
        engine.process(bar(1, 100, 100.95, 99.5, 100))
        self.assertTrue(engine.trades[1].closed)

    def test_entry_bar_ignores_target_but_stops(self):
        engine = ReplayEngine(settings())
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "buy", "type": "limit", "qty": 1, "price": 99, "tp": 101})
        engine.process(bar(1, 100, 101.5, 98.5, 100))
        self.assertFalse(engine.trades[1].closed)
        engine.apply({"op": "order", "side": "buy", "type": "limit", "qty": 1, "price": 99, "sl": 98})
        engine.process(bar(2, 100, 100, 97, 99))
        self.assertTrue(engine.trades[2].closed)
        self.assertTrue(engine.trades[2].ambiguous)

    def test_partial_close_modify_cancel_and_expiry(self):
        engine = ReplayEngine(settings())
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "buy", "type": "market", "qty": 2, "sl": 95})
        engine.apply({"op": "close", "id": 1, "qty": 0.5})
        trade = engine.trades[1]
        self.assertEqual(trade.open_qty, 1.5)
        self.assertEqual(trade.exits[0].reason, "partial")
        engine.apply({"op": "modify", "id": 1, "sl": 100})
        self.assertEqual(trade.sl, 100)
        engine.apply({"op": "order", "side": "sell", "type": "limit", "qty": 1, "price": 110, "expires": 3 * MINUTE})
        engine.apply({"op": "order", "side": "sell", "type": "limit", "qty": 1, "price": 111})
        engine.apply({"op": "cancel", "id": 3})
        engine.process(bar(1, 100, 100, 100, 100))
        engine.process(bar(3, 100, 100, 100, 100))
        self.assertEqual(engine.orders[2].status, "expired")
        self.assertEqual(engine.orders[3].status, "cancelled")

    def test_invalid_order_is_rejected(self):
        engine = ReplayEngine(settings())
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "buy", "type": "limit", "qty": 1})
        self.assertEqual(engine.pop_events()[0].type, "rejected")
        self.assertFalse(engine.orders)

    def test_excursions(self):
        engine = ReplayEngine(settings())
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "buy", "type": "market", "qty": 1})
        engine.process(bar(1, 100, 104, 97, 101))
        trade = engine.trades[1]
        self.assertEqual(trade.mfe, 4)
        self.assertEqual(trade.mae, 3)
        self.assertAlmostEqual(engine.open_pnl(), 1)
        self.assertAlmostEqual(engine.equity(), 1001)


class ConversionTests(unittest.TestCase):
    def test_plans(self):
        self.assertEqual(plan_conversion(None, "USDT", "USD", 0).mode, "one")
        self.assertEqual(plan_conversion("USD", "JPY", "USD", 0).mode, "inverse")
        self.assertEqual(plan_conversion("EUR", "USD", "EUR", 0).mode, "inverse")
        self.assertEqual(plan_conversion("EUR", "USD", "CZK", 0).mode, "table")
        self.assertEqual(plan_conversion(None, "USD", "CZK", 25).mode, "fixed")
        pence = plan_conversion(None, "GBp", "GBP", 0)
        self.assertAlmostEqual(pence.rate(0, 1), 0.01)

    def test_inverse_rate_converts_pnl(self):
        # USDJPY na dolarovém účtu: zisk 1 JPY na jednotku při ceně 150 = 1/150 USD
        engine = ReplayEngine(settings(tick=0.001, contract_size=100_000), plan_conversion("USD", "JPY", "USD", 0))
        engine.process(bar(0, 150, 150, 150, 150))
        engine.apply({"op": "order", "side": "buy", "type": "market", "qty": 1})
        engine.process(bar(1, 151, 151, 151, 151))
        engine.apply({"op": "close", "id": 1})
        self.assertAlmostEqual(engine.trades[1].realized, 100_000 / 151)

    def test_table_rate_uses_last_known_day(self):
        conversion = CurrencyConversion("table", times=[0, 10, 20], rates=[1.0, 2.0, 3.0])
        self.assertEqual(conversion.rate(15, 0), 2.0)
        self.assertEqual(conversion.rate(-5, 0), 1.0)

    def test_position_size_by_risk(self):
        values = settings(contract_size=100_000, lot_step=0.01, tick=0.00001)
        # 1 % z 10 000 = 100; stop 50 pipů (0.005) × 100 000 = 500 na lot → 0.2 lotu
        self.assertAlmostEqual(position_size("risk_pct", 1, 1.1, 1.095, values, 10_000, 1.0), 0.2)
        self.assertAlmostEqual(position_size("risk_amount", 100, 1.1, 1.095, values, 10_000, 1.0), 0.2)
        self.assertEqual(position_size("risk_pct", 1, 1.1, None, values, 10_000, 1.0), 0)
        self.assertEqual(position_size("lots", 0.237, 1.1, None, values, 10_000, 1.0), 0.23)
        self.assertEqual(round_to_step(0.3, 0.1), 0.3)


class SimulationTests(unittest.TestCase):
    def test_simulate_matches_live_and_rewind_drops_commands(self):
        candles = [bar(i, 100 + i, 100.5 + i, 99.5 + i, 100 + i) for i in range(6)]
        commands = [
            {"t": 1 * MINUTE, "op": "order", "side": "buy", "type": "market", "qty": 1},
            {"t": 3 * MINUTE, "op": "close", "id": 1},
            {"t": 4 * MINUTE, "op": "order", "side": "sell", "type": "market", "qty": 1},
        ]
        engine = simulate(settings(), CurrencyConversion(), candles, commands)
        self.assertAlmostEqual(engine.trades[1].realized, 2)
        self.assertFalse(engine.trades[2].closed)
        kept = commands_until(commands, 2 * MINUTE)
        rewound = simulate(settings(), CurrencyConversion(), candles[:3], kept)
        self.assertEqual(len(rewound.trades), 1)
        self.assertFalse(rewound.trades[1].closed)

    def test_stats_curve_and_csv(self):
        engine = ReplayEngine(settings())
        engine.process(bar(0, 100, 100, 100, 100))
        engine.apply({"op": "order", "side": "buy", "type": "market", "qty": 1, "sl": 99})
        engine.process(bar(1, 102, 102, 102, 102))
        engine.apply({"op": "close", "id": 1})
        engine.apply({"op": "order", "side": "buy", "type": "market", "qty": 1, "sl": 101})
        engine.process(bar(2, 101, 101, 100.5, 100.5))
        trades = list(engine.trades.values())
        stats = trade_stats(trades, 1000)
        self.assertEqual(stats["count"], 2)
        self.assertAlmostEqual(stats["win_rate"], 50)
        self.assertAlmostEqual(stats["net"], 1)
        self.assertAlmostEqual(stats["profit_factor"], 2)
        self.assertAlmostEqual(stats["max_drawdown"], 1)
        self.assertAlmostEqual(stats["avg_r"], 0.5)
        self.assertEqual(equity_curve(trades, 1000, 0)[-1], (2 * MINUTE, 1001))
        csv = trades_csv(trades, {"1": {"tag": "ENG", "note": "a;b"}}, "USD", str)
        self.assertIn("ENG;a,b", csv)
        self.assertEqual(len(csv.strip().splitlines()), 3)


class BucketTests(unittest.TestCase):
    def test_bucket_alignment_and_merge(self):
        hour = 60 * MINUTE
        anchor = 30 * MINUTE
        self.assertEqual(bucket_start(95 * MINUTE, anchor, hour), 90 * MINUTE)
        self.assertEqual(bucket_start(20 * MINUTE, anchor, hour), -30 * MINUTE)
        first = merge_into(None, bar(90, 1, 2, 0.5, 1.5), 90 * MINUTE)
        merged = merge_into(first, bar(91, 1.5, 3, 1, 2), 90 * MINUTE)
        self.assertEqual((merged.open, merged.high, merged.low, merged.close, merged.volume), (1, 3, 0.5, 2, 2))


class SessionStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = KlineCache(Path(self.tmp.name) / "test.db")
        await self.cache.init()

    async def asyncTearDown(self):
        await self.cache.close()
        self.tmp.cleanup()

    async def test_session_indicators_and_drawings_are_separate(self):
        cache = self.cache
        first = await cache.create_replay_session("A", "binance", "BTCUSDT", "1m", 0, settings())
        second = await cache.create_replay_session("B", "binance", "BTCUSDT", "1m", 0, settings())
        indicators = {"0": [{"id": "x", "kind": "sma", "params": {"length": 20}}]}
        await cache.update_replay_session(first, indicators=indicators)
        self.assertEqual((await cache.get_replay_session(first))["indicators"], indicators)
        self.assertEqual((await cache.get_replay_session(second))["indicators"], {})
        # Kresby session mají vlastní zdroj a smažou se se session
        await cache.add_drawing(replay_drawing_source(first), "BTCUSDT", "1m", "hline", [{"time": 0, "price": 1}])
        self.assertEqual(await cache.load_drawings("binance", "BTCUSDT"), [])
        await cache.delete_replay_session(first)
        self.assertEqual(await cache.load_drawings(replay_drawing_source(first), "BTCUSDT"), [])


def local_ms(*parts: int) -> int:
    """Místní datum a čas (rok, měsíc, den, hodina, minuta) → ms UTC."""
    return int(datetime(*parts).timestamp() * 1000)


def run_jump(start_ms: int, bars: list[int], time_of_day: str) -> int | None:
    """Skok na čas dne nad svíčkami (ms): otevření svíčky, před kterou skončí (None = konec dat)."""
    stop = time_of_day_stopper(start_ms, time_of_day)
    return next((bar_ms for bar_ms in bars if bar_ms > start_ms and stop(bar_ms)), None)


def hourly(start: tuple, end: tuple) -> list[int]:
    """Hodinové svíčky (ms) od start do end včetně (místní čas)."""
    first, last = local_ms(*start), local_ms(*end)
    return list(range(first, last + 1, 60 * MINUTE))


class TimeOfDayJumpTests(unittest.TestCase):
    def test_clean_time_of_day(self):
        # Pole type=time vrací HH:MM (příp. s vteřinami); neplatný čas = None
        self.assertEqual(clean_time_of_day("9:00"), "09:00")
        self.assertEqual(clean_time_of_day("09:30:00"), "09:30")
        self.assertIsNone(clean_time_of_day("24:00"))
        self.assertIsNone(clean_time_of_day(""))
        self.assertEqual(normalize_settings({})["jump_time"], "09:00")
        self.assertEqual(normalize_settings({"jump_time": "x"})["jump_time"], "09:00")

    def test_today_or_next_day(self):
        # Před 9:00 = dnes, v 9:00 a po ní = zítra
        bars = hourly((2024, 3, 5, 0, 0), (2024, 3, 6, 23, 0))
        self.assertEqual(run_jump(local_ms(2024, 3, 5, 7, 0), bars, "09:00"), local_ms(2024, 3, 5, 9, 0))
        self.assertEqual(run_jump(local_ms(2024, 3, 5, 9, 0), bars, "09:00"), local_ms(2024, 3, 6, 9, 0))

    def test_jump_next_day(self):
        # Krypto 24/7: skok končí před svíčkou 9:00 dalšího dne (i o víkendu)
        bars = hourly((2024, 3, 8, 0, 0), (2024, 3, 11, 23, 0))
        self.assertEqual(run_jump(local_ms(2024, 3, 8, 12, 0), bars, "09:00"), local_ms(2024, 3, 9, 9, 0))

    def test_forex_weekend_skips_to_monday(self):
        # Forex: pátek do 22:00, neděle od 23:00 – skok z pátku jde na pondělí 9:00
        bars = hourly((2024, 3, 8, 0, 0), (2024, 3, 8, 22, 0)) + hourly((2024, 3, 10, 23, 0), (2024, 3, 11, 23, 0))
        self.assertEqual(run_jump(local_ms(2024, 3, 8, 12, 0), bars, "09:00"), local_ms(2024, 3, 11, 9, 0))

    def test_late_open_stops_at_first_bar(self):
        # Akcie otevírají až 15:30: skok na 9:00 skončí před první svíčkou dne (i po víkendu)
        friday = hourly((2024, 3, 8, 15, 30), (2024, 3, 8, 21, 30))
        monday = hourly((2024, 3, 11, 15, 30), (2024, 3, 11, 21, 30))
        self.assertEqual(run_jump(local_ms(2024, 3, 8, 16, 30), friday + monday, "09:00"), local_ms(2024, 3, 11, 15, 30))
        self.assertIsNone(run_jump(local_ms(2024, 3, 11, 16, 30), friday + monday, "09:00"))

if __name__ == "__main__":
    unittest.main()
