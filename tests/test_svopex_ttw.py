"""Testy indikátoru Svopex TTW (spuštění: python -m unittest)."""

import random
import unittest

from tests.ttw_reference import reference
from tickview.indicators.compute import compute_indicators, make_context, new_indicator, run_indicator
from tickview.indicators.custom.svopex_ttw.indicator import pine_range
from tickview.indicators.registry import get_indicator, load_errors
from tickview.providers.base import Candle

HOUR_MS = 3_600_000


def random_candles(seed: int, count: int = 700, volatility: float = 0.0012) -> list[Candle]:
    """Náhodná procházka 1h svíček (forexové ceny na 5 míst) s reprodukovatelným seedem."""
    rnd = random.Random(seed)
    candles, price = [], 1.1
    start = 1_700_000_000_000 - 1_700_000_000_000 % HOUR_MS
    for i in range(count):
        open_ = price
        close = open_ + rnd.gauss(0, volatility)
        high = max(open_, close) + abs(rnd.gauss(0, volatility * 0.6))
        low = min(open_, close) - abs(rnd.gauss(0, volatility * 0.6))
        candles.append(Candle(start + i * HOUR_MS, round(open_, 5), round(high, 5), round(low, 5), round(close, 5), 0))
        price = close
    return candles


# Varianty parametrů pro porovnání s doslovným přepisem Pine (změny proti výchozím)
VARIANTS = [
    {},
    {"from_extreme": False, "min_candle_length": 5},
    {"first_touches_on_wicks": False, "last_touch_on_wicks": False, "from_extreme": False},
    {"use_major_swing": False, "eng_close_beyond_previous": False, "two_color_line": True, "from_extreme": False},
    {"midnight_timezone": "UTC", "midnight_hour": 0, "bars_to_process": 200, "max_signal_length": 60},
]


class SvopexTTWTest(unittest.TestCase):
    """Převod Pine → Python: shoda s doslovným přepisem, signály, napojení na TickView."""

    def setUp(self) -> None:
        self.indicator = get_indicator("svopex_ttw")
        self.assertIsNotNone(self.indicator, load_errors())

    def test_pine_range(self) -> None:
        self.assertEqual(list(pine_range(1, 3)), [1, 2, 3])
        self.assertEqual(list(pine_range(3, 1)), [3, 2, 1])
        self.assertEqual(list(pine_range(2, 2)), [2])

    def test_matches_literal_pine_port(self) -> None:
        # Optimalizovaný převod (cache, předčasné ukončení smyček) musí dát
        # stejné úsečky i signály jako doslovný přepis Pine
        checked_segments = 0
        for seed in range(4):
            candles = random_candles(seed)
            times = [c.open_time // 1000 for c in reversed(candles)]
            for kind in ("forex", "stock"):
                for variant in VARIANTS:
                    params = self.indicator.normalize_params({**self.indicator.defaults(), **variant})
                    ctx = make_context("yahoo", "X", "1h", candles, kind=kind, exchange_timezone="UTC")
                    result = run_indicator(self.indicator, params, candles, ctx)
                    ref_segments, ref_hits = reference(candles, params, "1h", kind, ctx.tick, ctx.step_ms)
                    expected = [(times[s], round(p1, 10), times[e], round(p2, 10)) for s, p1, e, p2, _ in ref_segments]
                    actual = [(seg.time1, round(seg.price1, 10), seg.time2, round(seg.price2, 10))
                              for seg in result.segments]
                    self.assertEqual(actual, expected, f"seed {seed}, {kind}, {variant}")
                    signals = {(sig.bars_ago, 0 if sig.direction == "long" else 1) for sig in result.signals}
                    self.assertEqual(signals, set(ref_hits), f"seed {seed}, {kind}, {variant}")
                    checked_segments += len(actual)
        # Data musí signály opravdu obsahovat (jinak by test nic neověřil)
        self.assertGreater(checked_segments, 50)

    def test_signal_bars_limits_signals(self) -> None:
        # Scanner (signal_bars) dostane stejné signály posledních svíček jako plný výpočet
        params = self.indicator.defaults()
        for seed in range(6):
            candles = random_candles(seed + 10, volatility=0.002)
            full = run_indicator(self.indicator, params, candles, make_context("y", "X", "1h", candles))
            scan = run_indicator(self.indicator, params, candles, make_context("y", "X", "1h", candles, signal_bars=5))
            self.assertEqual(
                [(s.bars_ago, s.direction) for s in scan.signals],
                [(s.bars_ago, s.direction) for s in full.signals if s.bars_ago < 5],
            )

    def test_short_history_draws_nothing(self) -> None:
        # Méně svíček než perioda průměrné velikosti = nic (jako ta.sma = na v Pine)
        candles = random_candles(1, count=300)
        result = run_indicator(self.indicator, self.indicator.defaults(), candles, make_context("y", "X", "1h", candles))
        self.assertEqual((result.segments, result.signals), ([], []))

    def test_chart_payload_has_segments(self) -> None:
        candles = random_candles(2)
        spec = new_indicator("svopex_ttw")
        payload = compute_indicators([spec], candles, make_context("y", "X", "1h", candles))
        self.assertEqual(payload["groups"][0]["title"], "Svopex TTW")
        self.assertTrue(payload["segments"])
        self.assertTrue(all(segment["group"] == spec["id"] for segment in payload["segments"]))
        self.assertEqual(payload["overlays"], [])

    def test_required_bars(self) -> None:
        self.assertGreaterEqual(self.indicator.required_bars(self.indicator.defaults()), 360)
