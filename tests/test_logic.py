"""Testy logické vrstvy a společných částí zdrojů (spuštění: python -m unittest)."""

import asyncio
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

import yaml
from eventkit import Event
from types import SimpleNamespace

from ib_async import BarData, BarDataList, Future

from tickview.config import update_config_section
from tickview.logic.cache import KlineCache, fetch_history, uncovered_ranges
from tickview.indicators.builtin import BUILTIN_INDICATORS
from tickview.indicators.compute import (
    compute_indicators,
    indicator_color,
    load_saved_indicators,
    move_indicator,
    new_indicator,
    normalize_params,
)
from tickview.logic.sessions import filter_session, timezone_options, utc_offset_label
from tickview.logic.watchlists import (
    FLAG_COLORS,
    build_groups,
    click_flag,
    move_section,
    move_symbol,
    next_sort,
    parse_entry,
    remove_section,
    sort_column,
    sort_watchlist,
)
from tickview.logic.styles import (
    CHART_DEFAULTS,
    clean_name,
    delete_template,
    load_chart_style,
    load_favorites,
    load_panel_position,
    load_templates,
    normalize_chart_style,
    normalize_chart_type,
    normalize_color,
    normalize_drawing_style,
    normalize_favorites,
    normalize_panel_position,
    save_template,
)
from tickview.providers.base import clean_float32_price, clip_spikes, split_symbol
from tickview.providers.base import (
    TIMEFRAME_MS,
    Candle,
    FailureLatch,
    RateLimiter,
    StreamStatus,
    aggregate_candles,
    poll_latest_candles,
    utc_now_ms,
)
from tickview.providers import provider_symbol
from tickview.providers.ibkr import IbkrProvider, forex_parts, forex_suggestions, instrument_suggestions
from tickview.providers.ibkr_futures import (
    build_chain,
    front_leg,
    future_spec,
    future_suggestions,
    leg_segments,
    roll_time_ms,
)
from tickview.providers.yahoo import YahooProvider


def make_candle(open_time: int, close: float = 1.0) -> Candle:
    """Svíčka pro testy s danou otevírací dobou (ms) a závěrečnou cenou."""
    return Candle(open_time, close, close + 1, close - 1, close, 10.0)


class FakeProvider:
    """Testovací zdroj: vrací souvislé hodinové svíčky až do současnosti.

    Atributy:
        calls: seznam požadovaných intervalů (start_ms, end_ms).
    """

    id = "fake"
    data_delay_ms = 0

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def has_quote_spikes(self, symbol: str) -> bool:
        return False

    def lacks_volume(self, symbol: str) -> bool:
        return False

    async def history(self, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Candle]:
        self.calls.append((start_ms, end_ms))
        step = TIMEFRAME_MS[timeframe]
        first = (start_ms + step - 1) // step * step
        # Cena odliší jednotlivá stažení (kontrola přepsání rozpracované svíčky)
        return [
            make_candle(open_time, close=float(len(self.calls)))
            for open_time in range(first, min(end_ms, utc_now_ms()) + 1, step)
        ]


class FetchHistoryTest(unittest.IsolatedAsyncioTestCase):
    """Skládání historie z cache a sítě."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = KlineCache(Path(self.tmp.name) / "test.db")
        await self.cache.init()

    async def asyncTearDown(self) -> None:
        await self.cache.close()
        self.tmp.cleanup()

    async def test_quote_spikes_clipped_on_read(self) -> None:
        # Zdroj s chybnými knoty z kotací: cache drží surová data, výsledek je zastřižený
        step = TIMEFRAME_MS["1h"]
        quiet = [Candle(i * step, 1.1, 1.1002, 1.0998, 1.1, 0) for i in range(9)]
        await self.cache.store("fake", "EUR.USD", "1h", quiet + [Candle(9 * step, 1.1, 1.2, 1.0998, 1.1, 0)])
        await self.cache.add_fetched_range("fake", "EUR.USD", "1h", 0, 9 * step)
        provider = FakeProvider()
        self.assertEqual((await fetch_history(provider, self.cache, "EUR.USD", "1h", 0, 9 * step))[-1].high, 1.2)
        provider.has_quote_spikes = lambda symbol: True
        self.assertEqual((await fetch_history(provider, self.cache, "EUR.USD", "1h", 0, 9 * step))[-1].high, 1.1)
        self.assertEqual(provider.calls, [])
        # Úsek začínající až svíčkou se špičkou: okolí se doplní z cache
        self.assertEqual((await fetch_history(provider, self.cache, "EUR.USD", "1h", 9 * step, 9 * step))[0].high, 1.1)

    async def test_open_candle_is_not_cached_and_is_refetched(self) -> None:
        provider = FakeProvider()
        step = TIMEFRAME_MS["1h"]
        now = utc_now_ms()
        first = await fetch_history(provider, self.cache, "X", "1h", now - 10 * step, now)
        cached = await self.cache.load("fake", "X", "1h")
        # Rozpracovaná poslední svíčka je ve výsledku, ale ne v cache
        self.assertLess(cached[-1].open_time, first[-1].open_time)
        second = await fetch_history(provider, self.cache, "X", "1h", now - 10 * step, now)
        # Druhé načtení stáhne jen konec a rozpracovanou svíčku obnoví
        self.assertEqual(len(provider.calls), 2)
        self.assertNotEqual(second[-1].close, first[-1].close)
        open_times = [candle.open_time for candle in second]
        self.assertEqual(open_times, sorted(set(open_times)))

    async def test_older_range_is_fetched_before_cached_data(self) -> None:
        provider = FakeProvider()
        step = TIMEFRAME_MS["1h"]
        now = utc_now_ms()
        await fetch_history(provider, self.cache, "X", "1h", now - 10 * step, now)
        first_cached = (await self.cache.load("fake", "X", "1h"))[0].open_time
        older = await fetch_history(
            provider, self.cache, "X", "1h", first_cached - 5 * step, first_cached - 1
        )
        self.assertEqual(len(older), 5)
        self.assertTrue(all(candle.open_time < first_cached for candle in older))
        # Navazující stažené intervaly se sloučí do jednoho
        self.assertEqual(len(await self.cache.fetched_ranges("fake", "X", "1h")), 1)

    async def test_hole_in_cache_is_refetched(self) -> None:
        provider = FakeProvider()
        step = TIMEFRAME_MS["1h"]
        base = (utc_now_ms() // step - 100) * step
        # Cache se svíčkami před dírou a po ní (např. ze starší verze aplikace)
        await self.cache.store("fake", "X", "1h", [make_candle(base + i * step) for i in range(5)])
        await self.cache.store("fake", "X", "1h", [make_candle(base + i * step) for i in range(15, 20)])
        result = await fetch_history(provider, self.cache, "X", "1h", base, base + 19 * step)
        self.assertEqual([c.open_time for c in result], [base + i * step for i in range(20)])
        # Podruhé už je interval stažený – žádný další dotaz
        await fetch_history(provider, self.cache, "X", "1h", base, base + 19 * step)
        self.assertEqual(len(provider.calls), 1)

    def test_uncovered_ranges(self) -> None:
        self.assertEqual(uncovered_ranges(0, 100, [], 10), [(0, 100)])
        self.assertEqual(uncovered_ranges(0, 100, [(20, 40), (60, 70)], 10), [(0, 19), (41, 59), (71, 100)])
        # Levý proužek kratší než bar se vynechá, celý interval pokrytý
        self.assertEqual(uncovered_ranges(15, 100, [(20, 100)], 10), [])
        self.assertEqual(uncovered_ranges(0, 100, [(-5, 200)], 10), [])


class MoveIndicatorTest(unittest.TestCase):
    """Přeřazení instancí indikátorů tažením panelu."""

    def setUp(self):
        self.specs = [{"id": "a", "kind": "rsi"}, {"id": "b", "kind": "macd", "color": "#123456"}, {"id": "c", "kind": "rsi"}]

    def ids(self, specs):
        return [spec["id"] for spec in specs]

    def test_move_before_and_to_end(self):
        self.assertEqual(self.ids(move_indicator(self.specs, "c", "a")), ["c", "a", "b"])
        self.assertEqual(self.ids(move_indicator(self.specs, "a", None)), ["b", "c", "a"])
        # Neznámý cíl = na konec
        self.assertEqual(self.ids(move_indicator(self.specs, "a", "x")), ["b", "c", "a"])

    def test_keeps_palette_colors(self):
        # Barva z palety podle původního pořadí se zafixuje, vlastní zůstane
        moved = {spec["id"]: spec for spec in move_indicator(self.specs, "c", "a")}
        self.assertEqual(moved["a"]["color"], indicator_color(self.specs[0], 0))
        self.assertEqual(moved["c"]["color"], indicator_color(self.specs[2], 2))
        self.assertEqual(moved["b"]["color"], "#123456")
        self.assertNotIn("color", self.specs[0])

    def test_noop_returns_same_list(self):
        self.assertIs(move_indicator(self.specs, "a", "b"), self.specs)
        self.assertIs(move_indicator(self.specs, "c", None), self.specs)
        self.assertIs(move_indicator(self.specs, "a", "a"), self.specs)
        self.assertIs(move_indicator(self.specs, "x", None), self.specs)


class IndicatorsTest(unittest.TestCase):
    """Výpočet a validace indikátorů."""

    SPECS = [
        {"id": "a", "kind": "sma", "params": {"length": 20}},
        {"id": "b", "kind": "bb", "params": {"length": 20, "std": 2.0}},
        {"id": "c", "kind": "rsi", "params": {"length": 14}},
        {"id": "d", "kind": "macd", "params": {"fast": 12, "slow": 26, "signal": 9}},
    ]

    def test_short_history_skips_indicators(self) -> None:
        candles = [make_candle(i * 60_000) for i in range(10)]
        payload = compute_indicators(self.SPECS, candles)
        self.assertEqual(payload["overlays"], [])
        self.assertEqual(payload["panes"], [])
        # Indikátory bez dat zůstanou v legendě (lze je nastavit / odebrat)
        self.assertEqual([group["id"] for group in payload["groups"]], ["a", "b", "c", "d"])
        self.assertTrue(all(group["pane"] is None for group in payload["groups"]))

    def test_all_indicators_compute(self) -> None:
        # Každý definovaný indikátor dá nad dostatečnou historií aspoň jednu sérii
        candles = [make_candle(i * 60_000, close=10 + (i % 11) * 0.3) for i in range(300)]
        specs = [new_indicator(indicator.id) for indicator in BUILTIN_INDICATORS]
        payload = compute_indicators(specs, candles)
        drawn = {overlay["group"] for overlay in payload["overlays"]} | {pane["group"] for pane in payload["panes"]}
        self.assertEqual(drawn, {spec["id"] for spec in specs})
        for pane in payload["panes"]:
            for series in pane["series"]:
                self.assertEqual(len(series["data"]), len(candles))

    def test_hidden_and_color_in_groups(self) -> None:
        candles = [make_candle(i * 60_000, close=1 + (i % 7) * 0.1) for i in range(100)]
        spec = {"id": "x", "kind": "sma", "params": {"length": 5}, "visible": False, "color": "#123456"}
        payload = compute_indicators([spec], candles)
        self.assertEqual(payload["groups"][0]["visible"], False)
        self.assertEqual(payload["overlays"][0]["color"], "#123456")
        self.assertEqual(payload["groups"][0]["title"], "SMA 5")

    def test_style_and_visibility(self) -> None:
        from tickview.indicators.api import Context, Result, Segment
        from tickview.indicators.registry import get_indicator

        candles = [make_candle(i * 60_000, close=1 + (i % 7) * 0.1) for i in range(100)]
        # Bez vstupů v legendě jen název; skrytý na timeframu = v legendě, ale bez čar
        spec = {"id": "x", "kind": "sma", "params": {"length": 5}, "style": {"inputs_in_status": False},
                "visibility": {"minutes": {"enabled": False}}}
        payload = compute_indicators([spec], candles, Context(timeframe="5m"))
        self.assertEqual((payload["groups"][0]["title"], payload["overlays"]), ("SMA", []))
        self.assertTrue(compute_indicators([spec], candles, Context(timeframe="1h"))["overlays"])
        # Vypnuté Lines vynechají úsečky indikátoru
        indicator = get_indicator("sma")
        original = indicator.compute
        indicator.compute = lambda df, params, ctx: Result(segments=[Segment(1, 1.0, 2, 2.0, "#fff")])
        try:
            lines_on = compute_indicators([{**spec, "visibility": None}], candles)
            lines_off = compute_indicators([{**spec, "visibility": None, "style": {"lines": False}}], candles)
        finally:
            indicator.compute = original
        self.assertEqual((len(lines_on["segments"]), len(lines_off["segments"])), (1, 0))
        # Výchozí styl a viditelnost se do instance neukládají, změněné ano
        loaded = load_saved_indicators('[{"kind": "sma", "params": {}, "style": {"lines": true}},'
                                       ' {"kind": "sma", "params": {}, "style": {"tables": false},'
                                       ' "visibility": {"days": {"enabled": false}}}]')
        self.assertNotIn("style", loaded[0])
        self.assertNotIn("visibility", loaded[0])
        self.assertFalse(loaded[1]["style"]["tables"])
        self.assertFalse(loaded[1]["visibility"]["days"]["enabled"])

    def test_plot_style_normalization(self) -> None:
        from tickview.logic.styles import normalize_indicator_style, normalize_plot_style

        # Výstup si drží jen platné změny; bool není tloušťka, neznámý druh se zahodí
        self.assertEqual(
            normalize_plot_style({"visible": False, "color": "#FF0000", "width": True, "lineStyle": "wavy",
                                  "type": "cross", "priceLine": True}),
            {"visible": False, "priceLine": True, "color": "#ff0000"},
        )
        style = normalize_indicator_style({"precision": 9, "plots": {"A": {"width": 3}, "B": {"width": 7}, 1: {}}})
        self.assertEqual((style["precision"], style["plots"]), (None, {"A": {"width": 3}}))
        self.assertTrue(style["labels_on_scale"] and style["values_in_status"])
        self.assertEqual(normalize_indicator_style({"precision": 0})["precision"], 0)

    def test_plot_style_payload(self) -> None:
        from tickview.indicators.api import Line, Result
        from tickview.indicators.compute import plot_defaults, sample_lines
        from tickview.indicators.registry import get_indicator

        candles = [make_candle(i * 60_000, close=1 + (i % 7) * 0.1) for i in range(100)]
        # Styl výstupu SMA: schodovitá čárkovaná čára, cenová linka, bez popisku na ose, přesnost 2
        spec = {"id": "x", "kind": "sma", "params": {"length": 5}, "style": {
            "precision": 2, "labels_on_scale": False, "values_in_status": False,
            "plots": {"SMA": {"type": "step", "width": 3, "lineStyle": "dashed", "color": "#123456",
                              "priceLine": True}}}}
        payload = compute_indicators([spec], candles)
        overlay = payload["overlays"][0]
        self.assertEqual((overlay["type"], overlay["color"]), ("step", "#123456"))
        self.assertEqual(
            {key: overlay["options"][key] for key in ("lineWidth", "lineStyle", "lineType", "priceLineVisible",
                                                       "lastValueVisible")},
            {"lineWidth": 3, "lineStyle": 2, "lineType": 1, "priceLineVisible": True, "lastValueVisible": False},
        )
        self.assertEqual((payload["groups"][0]["precision"], payload["groups"][0]["values"]), (2, False))
        # MACD: histogram jako sloupce jedné barvy, skrytý signál
        macd = {"id": "m", "kind": "macd", "params": {"fast": 12, "slow": 26, "signal": 9}, "style": {
            "plots": {"Histogram": {"type": "columns", "color": "#00ff00"}, "Signál": {"visible": False}}}}
        series = {item["name"]: item for item in compute_indicators([macd], candles)["panes"][0]["series"]}
        self.assertEqual((series["Histogram"]["type"], series["Histogram"]["options"]["color"]), ("columns", "#00ff00"))
        self.assertNotIn("color", series["Histogram"]["data"][-1])
        self.assertFalse(series["Signál"]["visible"])
        # Bez změn se histogram barví podle znaménka
        plain = compute_indicators([{**macd, "style": None}], candles)["panes"][0]["series"][0]
        self.assertIn("color", plain["data"][-1])
        # Mezery uvnitř řady: Line je přemostí, Line with breaks ne; začátek zůstane prázdný
        indicator = get_indicator("sma")
        original = indicator.compute
        values = [float("nan")] * 3 + [1.0, float("nan"), 3.0] + [4.0] * 94
        indicator.compute = lambda df, params, ctx: Result(lines=[Line("SMA", values)])
        try:
            line = compute_indicators([{"id": "g", "kind": "sma", "params": {}}], candles)["overlays"][0]
            broken = compute_indicators([{"id": "g", "kind": "sma", "params": {},
                                          "style": {"plots": {"SMA": {"type": "linebr"}}}}], candles)["overlays"][0]
        finally:
            indicator.compute = original
        self.assertEqual((line["data"][0], line["data"][4]["value"]), ({"time": 0}, 2.0))
        self.assertNotIn("value", broken["data"][4])
        # Výstupy pro záložku Style a jejich výchozí vzhled (SAR body, Supertrend s přerušením)
        self.assertEqual([item.name for item in sample_lines(get_indicator("macd"), {"fast": 12, "slow": 26,
                                                                                       "signal": 9})],
                         ["Histogram", "MACD", "Signál"])
        sar = sample_lines(get_indicator("psar"), {"af": 0.02, "max_af": 0.2})[0]
        self.assertEqual(plot_defaults(sar, "#2962ff")["type"], "circles")
        histogram = Line("H", [], None, "histogram")
        self.assertIsNone(plot_defaults(histogram, "#2962ff")["color"])

    def test_series_keep_candle_count(self) -> None:
        candles = [make_candle(i * 60_000, close=1 + (i % 7) * 0.1) for i in range(100)]
        payload = compute_indicators(self.SPECS, candles)
        # Panely musí mít stejný počet bodů jako svíčky (whitespace místo NaN)
        for pane in payload["panes"]:
            for series in pane["series"]:
                self.assertEqual(len(series["data"]), len(candles))
        self.assertNotIn("value", payload["panes"][0]["series"][0]["data"][0])

    def test_normalize_params(self) -> None:
        self.assertEqual(normalize_params("sma", {"length": 20.0}), {"length": 20})
        self.assertEqual(normalize_params("sma", {"length": None}), {"length": 20})
        for kind, values in [
            ("sma", {"length": 0}),
            ("sma", {"length": 2.5}),
            ("bb", {"length": 20, "std": -1}),
            ("macd", {"fast": 26, "slow": 12, "signal": 9}),
            ("ao", {"fast": 34, "slow": 5}),
        ]:
            with self.assertRaises(ValueError):
                normalize_params(kind, values)

    def test_load_saved_indicators_skips_invalid(self) -> None:
        raw = '[{"id": "x", "kind": "ema", "params": {"length": 50}}, {"params": {}}, 5]'
        self.assertEqual(
            load_saved_indicators(raw),
            [{"id": "x", "kind": "ema", "params": {"length": 50}, "visible": True}],
        )
        self.assertEqual(load_saved_indicators("not json"), [])
        # Viditelnost a platná vlastní barva se zachovají, neplatná barva ne
        raw = '[{"id": "y", "kind": "rsi", "params": {}, "visible": false, "color": "#abcdef"},' \
            ' {"id": "z", "kind": "rsi", "params": {}, "color": "red"}]'
        loaded = load_saved_indicators(raw)
        self.assertEqual(loaded[0]["visible"], False)
        self.assertEqual(loaded[0]["color"], "#abcdef")
        self.assertNotIn("color", loaded[1])


class AggregationTest(unittest.TestCase):
    """Skládání delších timeframů (2m, 10m) z kratších svíček."""

    def test_aggregate_candles(self) -> None:
        candles = [
            Candle(0, 1, 3, 0.5, 2, 10),
            Candle(60_000, 2, 4, 1.5, 3, 5),
            Candle(120_000, 3, 3.5, 2.5, 2.8, 1),
        ]
        merged = aggregate_candles(list(reversed(candles)), TIMEFRAME_MS["2m"])
        self.assertEqual([c.open_time for c in merged], [0, 120_000])
        first = merged[0]
        self.assertEqual((first.open, first.high, first.low, first.close, first.volume), (1, 4, 0.5, 3, 15))
        # Vstupní svíčky se nemění (sloučení pracuje s kopiemi)
        self.assertEqual(candles[0].volume, 10)


class SessionsTest(unittest.TestCase):
    """Filtr hlavní obchodní seance (RTH) a časová pásma."""

    NY_SESSION = ("America/New_York", "09:30", "16:00")

    def test_filter_session_intraday(self) -> None:
        # Pondělí 16. 11. 2026: 9:25, 9:30, 15:55, 16:00 New York (UTC-5)
        base = 1794839400_000  # 2026-11-16 14:30 UTC = 9:30 New York
        times = [base - 300_000, base, base + 385 * 60_000, base + 390 * 60_000]
        candles = [make_candle(t) for t in times]
        kept = [c.open_time for c in filter_session(candles, "5m", self.NY_SESSION)]
        self.assertEqual(kept, [base, base + 385 * 60_000])
        # 1h svíčka 9:00 se seancí překrývá (9:30–10:00) → zůstává
        hour = [make_candle(base - 30 * 60_000)]
        self.assertEqual(len(filter_session(hour, "1h", self.NY_SESSION)), 1)
        # Denní svíčky a instrumenty bez seance se nefiltrují
        self.assertEqual(len(filter_session(candles, "1D", self.NY_SESSION)), 4)
        self.assertEqual(len(filter_session(candles, "5m", None)), 4)

    def test_filter_session_weekend(self) -> None:
        saturday = 1794839400_000 - 2 * 86_400_000
        self.assertEqual(filter_session([make_candle(saturday)], "5m", self.NY_SESSION), [])

    def test_timezones(self) -> None:
        from datetime import datetime, timezone as tz

        winter = datetime(2026, 11, 17, tzinfo=tz.utc)
        self.assertEqual(utc_offset_label("Europe/Prague", winter), "UTC+1")
        self.assertEqual(utc_offset_label("America/New_York", winter), "UTC-5")
        self.assertEqual(utc_offset_label("Asia/Kolkata", winter), "UTC+5:30")
        self.assertEqual(utc_offset_label("Europe/London", winter), "UTC")
        options = timezone_options(winter)
        self.assertEqual(options[0], ("Pacific/Honolulu", "(UTC-10) Honolulu"))
        self.assertIn(("Europe/Prague", "(UTC+1) Prague"), options)


class ForexTest(unittest.TestCase):
    """Forexové páry IBKR z vyhledávacího dotazu."""

    def test_forex_suggestions(self) -> None:
        self.assertEqual(forex_suggestions("eurusd"), ["EUR.USD"])
        self.assertEqual(forex_suggestions("EUR/USD"), ["EUR.USD"])
        # Obrácené pořadí se převede na konvenci IB (základní měna EUR)
        self.assertEqual(forex_suggestions("usdeur"), ["EUR.USD"])
        self.assertEqual(forex_suggestions("usdczk"), ["USD.CZK"])
        self.assertIn("EUR.GBP", forex_suggestions("EUR"))
        self.assertEqual(forex_suggestions("AAPL"), [])
        self.assertEqual(forex_suggestions("EURUSD=X"), [])

    def test_forex_parts(self) -> None:
        self.assertEqual(forex_parts("EUR.USD"), ("EUR", "USD"))
        self.assertIsNone(forex_parts("BRK.B"))
        self.assertIsNone(forex_parts("MSFT"))

    def test_metals_and_index_cfds(self) -> None:
        # Kovy a indexová CFD: hledání podle symbolu i názvu, kontrakt a druh
        self.assertEqual(instrument_suggestions("xau"), ["XAUUSD"])
        self.assertEqual(instrument_suggestions("gold"), ["XAUUSD"])
        self.assertEqual(instrument_suggestions("S&P 500"), ["IBUS500"])
        self.assertEqual(instrument_suggestions("DAX"), ["IBDE40"])
        self.assertEqual(instrument_suggestions("US"), [])
        self.assertEqual(instrument_suggestions("oil"), ["IBUSOIL", "IBUKOIL"])
        self.assertEqual(instrument_suggestions("brent"), ["IBUKOIL"])
        provider = IbkrProvider()
        gold, dax = provider._contract("XAUUSD"), provider._contract("IBDE40")
        self.assertEqual((gold.secType, gold.currency), ("CMDTY", "USD"))
        self.assertEqual((dax.secType, dax.currency), ("CFD", "EUR"))
        self.assertEqual(provider._contract("MSFT").secType, "STK")
        self.assertEqual((provider.symbol_kind("XAUUSD"), provider.symbol_kind("IBUS500")), ("commodity", "index"))
        # Bez obchodů (MIDPOINT) a bez filtru hlavní seance
        self.assertEqual(provider._what_to_show("IBUS500"), "MIDPOINT")
        self.assertIsNone(provider.regular_session("XAGUSD"))


def future_detail(local_symbol: str, last_trade: str, contract_month: str, trading_class: str = "ES") -> SimpleNamespace:
    """Detail futures kontraktu jako z reqContractDetails (jen pole, která čte build_chain)."""
    contract = Future("ES", last_trade, "CME", localSymbol=local_symbol, multiplier="50",
                      currency="USD", tradingClass=trading_class)
    return SimpleNamespace(contract=contract, contractMonth=contract_month, minTick=0.25, longName="E-mini S&P 500")


class IbkrFuturesTest(unittest.TestCase):
    """Kontinuální futures IBKR: symboly, hledání, řetězec kontraktů a roll."""

    def test_symbols_and_suggestions(self) -> None:
        # Symbol z tabulky, obecný symbol s burzou a symboly, které futures nejsou
        self.assertEqual(future_spec("FDAX1!").trading_class, "FDAX")
        generic = future_spec("VIX1!@CFE")
        self.assertEqual((generic.root, generic.exchange, generic.trading_class), ("VIX", "CFE", ""))
        self.assertEqual(future_spec("FOO1!@EUREX").currency, "EUR")
        for symbol in ("ES", "XX1!", "1!", "ES1!@", "EUR.USD", "GC=F"):
            self.assertIsNone(future_spec(symbol))
        # Dvouznakový kořen jen přesně, od tří znaků i začátek názvu
        self.assertEqual(future_suggestions("es"), ["ES1!"])
        self.assertEqual(future_suggestions("ES1!"), ["ES1!"])
        self.assertEqual(future_suggestions("E"), [])
        self.assertEqual(future_suggestions("gold"), ["GC1!", "MGC1!"])
        self.assertEqual(future_suggestions("DAX"), ["FDAX1!", "FDXM1!"])

    def test_chain_and_segments(self) -> None:
        spec = future_spec("ES1!")
        details = [
            future_detail("ESM6", "20260618", "202606"),
            future_detail("ESU6", "20260918", "202609"),
            future_detail("ESH6", "20260320", "202603"),
            # Jiná obchodní třída a měsíc mimo spec.months se vynechají
            future_detail("XYZ", "20260918", "202609", trading_class="EW"),
            future_detail("ESN6", "20260717", "202607"),
        ]
        chain = build_chain(details, spec)
        self.assertEqual([leg.local_symbol for leg in chain], ["ESH6", "ESM6", "ESU6"])
        self.assertTrue(chain[0].contract.includeExpired)
        self.assertEqual((chain[0].multiplier, chain[0].min_tick), (50.0, 0.25))
        # Roll osm dní před posledním obchodním dnem (půlnoc UTC)
        roll = roll_time_ms("20260618", 8)
        self.assertEqual(chain[1].roll_ms, roll)
        self.assertEqual(datetime.fromtimestamp(roll / 1000, tz=timezone.utc).date().isoformat(), "2026-06-10")
        self.assertEqual(front_leg(chain, roll - 1).local_symbol, "ESM6")
        self.assertEqual(front_leg(chain, roll).local_symbol, "ESU6")
        self.assertIsNone(front_leg(chain, chain[-1].roll_ms))
        # Interval přes roll se rozdělí na úseky jednotlivých kontraktů
        segments = leg_segments(chain, roll - 1000, roll + 1000)
        self.assertEqual([(leg.local_symbol, a, b) for leg, a, b in segments],
                         [("ESM6", roll - 1000, roll - 1), ("ESU6", roll, roll + 1000)])
        # Před prvním kontraktem řetězce (starší TWS nezná) data nejsou
        self.assertEqual(leg_segments(chain, 0, 1000), [])

    def test_provider_metadata(self) -> None:
        provider = IbkrProvider()
        self.assertEqual(provider.symbol_kind("ES1!"), "futures")
        self.assertEqual(provider.currencies("FDAX1!"), (None, "EUR"))
        self.assertEqual(provider.exchange_timezone("GC1!"), "America/Chicago")
        self.assertIsNone(provider.regular_session("ES1!"))
        self.assertEqual(provider._what_to_show("ES1!"), "TRADES")
        self.assertEqual((provider.price_tick("ES1!", 6000), provider.contract_size("ES1!")), (0.25, 50))
        # Obecný symbol bez načteného řetězce tick ani násobitel nezná, akcie žádný nemají
        self.assertIsNone(provider.contract_size("VIX1!@CFE"))
        self.assertIsNone(provider.contract_size("MSFT"))


class ProvidersBaseTest(unittest.IsolatedAsyncioTestCase):
    """Omezovač dotazů a smyčka pollingu."""

    def test_clip_spikes(self) -> None:
        # Klidné svíčky stříbra (rozsah 0,10) a jedna se chybným knotem ze středu bid/ask
        candles = [Candle(i, 48.5, 48.55, 48.45, 48.5, 0) for i in range(12)]
        candles[6] = Candle(6, 48.46, 53.93, 48.41, 49.02, 0)
        # Zprávový pohyb: knot 8× rozsah okolí zůstane, stejně jako svíčka bez dost sousedů
        candles[9] = Candle(9, 48.5, 49.3, 48.45, 48.5, 0)
        clip_spikes(candles)
        self.assertEqual((candles[6].high, candles[6].low), (49.02, 48.41))
        self.assertEqual(candles[9].high, 49.3)
        short = [Candle(0, 48.5, 48.55, 48.45, 48.5, 0), Candle(1, 48.5, 60.0, 48.4, 48.5, 0)]
        clip_spikes(short)
        self.assertEqual(short[1].high, 60.0)
        # Knot dolů se zastřihne na spodek těla
        dip = [Candle(i, 1.10, 1.1002, 1.0998, 1.10, 0) for i in range(9)]
        dip[4] = Candle(4, 1.1000, 1.1001, 1.0500, 1.0999, 0)
        clip_spikes(dip)
        self.assertEqual(dip[4].low, 1.0999)

    def test_clean_float32_price(self) -> None:
        # Šum převodu z float32 (Yahoo) zmizí, přesné float64 ceny se nemění
        self.assertEqual(clean_float32_price(23.110000610351562), 23.11)
        self.assertEqual(clean_float32_price(1.1010999679565430), 1.1011)
        self.assertEqual(clean_float32_price(1.10114), 1.10114)
        self.assertEqual(clean_float32_price(63735.99), 63735.99)
        self.assertEqual(clean_float32_price(25.0), 25.0)

    async def test_rate_limiter_spreads_concurrent_calls(self) -> None:
        limiter = RateLimiter(min_interval=0.05)
        started = time.monotonic()
        stamps: list[float] = []

        async def call() -> None:
            await limiter.acquire()
            stamps.append(time.monotonic() - started)

        await asyncio.gather(*(call() for _ in range(3)))
        stamps.sort()
        self.assertGreaterEqual(stamps[1] - stamps[0], 0.04)
        self.assertGreaterEqual(stamps[2] - stamps[1], 0.04)

    async def test_poll_reports_changes_finalization_and_outage(self) -> None:
        responses = [
            [make_candle(1, 1.0)],
            [make_candle(1, 1.0)],
            RuntimeError("výpadek"),
            RuntimeError("výpadek"),
            [make_candle(1, 2.0), make_candle(2, 3.0)],
        ]

        async def fetch() -> list[Candle]:
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        items = []
        stream = poll_latest_candles(fetch, 0)
        async for item in stream:
            items.append(item)
            if len(items) == 5:
                break
        await stream.aclose()
        self.assertEqual(items[0], make_candle(1, 1.0))
        # Výpadek se hlásí jen jednou, obnovení také
        self.assertEqual(items[1], StreamStatus(False, "zdroj neodpovídá: výpadek"))
        self.assertEqual(items[2], StreamStatus(True, "zdroj opět odpovídá"))
        # Konečná podoba předchozí svíčky i nová svíčka
        self.assertEqual(items[3:], [make_candle(1, 2.0), make_candle(2, 3.0)])


class FakeIB:
    """Náhrada připojení k TWS pro test průběžných barů (jen události a rušení)."""

    def __init__(self) -> None:
        self.errorEvent = Event("errorEvent")
        self.disconnectedEvent = Event("disconnectedEvent")
        self.cancelled: list[BarDataList] = []

    def isConnected(self) -> bool:
        return True

    def cancelHistoricalData(self, bars: BarDataList) -> None:
        self.cancelled.append(bars)


def make_bar(minute: int, close: float) -> BarData:
    """Minutový bar TWS v daném čase (minuty od epochy) se závěrečnou cenou."""
    date = datetime.fromtimestamp(minute * 60, tz=timezone.utc)
    return BarData(date=date, open=close, high=close + 1, low=close - 1, close=close, volume=10)


class ProviderAvailableTest(unittest.IsolatedAsyncioTestCase):
    """Dostupnost zdroje pro hledání: IBKR jen s navázaným spojením k TWS."""

    async def test_ibkr_available(self) -> None:
        provider = IbkrProvider()
        # Neběžící TWS (selhané připojení) = zdroj se v hledání nenabízí
        with mock.patch.object(provider, "_ensure_connected", side_effect=RuntimeError("TWS neběží")):
            self.assertFalse(await provider.available())
        with mock.patch.object(provider, "_ensure_connected", return_value=object()):
            self.assertTrue(await provider.available())
        # Zdroje bez trvalého spojení jsou dostupné vždy
        self.assertTrue(await YahooProvider().available())


class IbkrStreamTest(unittest.IsolatedAsyncioTestCase):
    """Průběžné bary IBKR (keepUpToDate): stav, změny svíček a ukončení odběru."""

    async def test_follow_bars(self) -> None:
        ib = FakeIB()
        bars = BarDataList([make_bar(1, 1.0), make_bar(2, 2.0)])
        bars.reqId = 7
        stream = IbkrProvider._follow_bars(ib, bars, False, FailureLatch())
        next_item = stream.__anext__

        # Nejdřív stav s informací o zpožděných datech, pak jen poslední svíčka
        self.assertEqual((await next_item()).live, False)
        self.assertEqual((await next_item()).open_time, 120_000)
        # Změna poslední svíčky a pak nová svíčka v jedné dávce: pošle se
        # konečná podoba předchozí i nová svíčka
        bars[-1] = make_bar(2, 2.5)
        bars.append(make_bar(3, 3.0))
        bars.updateEvent.emit(bars, True)
        self.assertEqual((await next_item()).close, 2.5)
        self.assertEqual((await next_item()).open_time, 180_000)
        # Varování 21xx odběr neukončí, chyba našeho reqId ano
        ib.errorEvent.emit(7, 2104, "farm OK", None)
        ib.errorEvent.emit(7, 162, "query cancelled", None)
        status = await next_item()
        self.assertEqual(status.connected, False)
        self.assertIn("162", status.message)
        with self.assertRaises(StopAsyncIteration):
            await stream.__anext__()
        self.assertEqual(ib.cancelled, [bars])

    async def test_follow_bars_clips_spikes(self) -> None:
        # Data MIDPOINT: chybný knot poslední svíčky se zastřihne podle okolních svíček
        ib = FakeIB()
        bars = BarDataList([make_bar(minute, 100.0) for minute in range(1, 8)])
        bars[-1] = BarData(date=bars[-1].date, open=100.0, high=150.0, low=99.0, close=100.0, volume=0)
        bars.reqId = 9
        stream = IbkrProvider._follow_bars(ib, bars, True, FailureLatch(), clip=True)
        await stream.__anext__()
        self.assertEqual((await stream.__anext__()).high, 100.0)
        await stream.aclose()

    async def test_follow_bars_ends_at_roll(self) -> None:
        # Po čase rollu futures odběr skončí bez hlášení výpadku a zruší se
        ib = FakeIB()
        bars = BarDataList([make_bar(1, 1.0)])
        bars.reqId = 8
        latch = FailureLatch()
        stream = IbkrProvider._follow_bars(ib, bars, True, latch, until_ms=0)
        items = [item async for item in stream]
        self.assertEqual([type(item).__name__ for item in items], ["StreamStatus", "Candle"])
        self.assertFalse(latch.failing)
        self.assertEqual(ib.cancelled, [bars])


class ConfigTest(unittest.TestCase):
    """Zápis sekce do config.yaml se zachováním komentářů."""

    def test_update_section_keeps_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                "# hlavička\nalpaca:\n  endpoint: https://x\n  api_key: \"\"   # např. PK...\n"
                "  secret: \"\"\n\n# IBKR\nibkr:\n  port: 7497\n",
                encoding="utf-8",
            )
            update_config_section("alpaca", {"api_key": 'a"b', "secret": "c#d"}, path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("# hlavička", text)
            self.assertIn("# např. PK...", text)
            self.assertIn("# IBKR", text)
            parsed = yaml.safe_load(text)
            self.assertEqual(parsed["alpaca"], {"endpoint": "https://x", "api_key": 'a"b', "secret": "c#d"})
            self.assertEqual(parsed["ibkr"], {"port": 7497})

    def test_new_file_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            update_config_section("alpaca", {"api_key": "k"}, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8")), {"alpaca": {"api_key": "k"}})


class SymbolSearchTest(unittest.IsolatedAsyncioTestCase):
    """Hledání symbolu: futures z Yahoo a přímé zadání „zdroj:SYMBOL“."""

    async def test_yahoo_futures(self) -> None:
        # Yahoo vrací futures (GC=F) s typem FUTURE – musí projít do výsledků
        quotes = [{"symbol": "GC=F", "quoteType": "FUTURE", "shortname": "Gold"},
                  {"symbol": "GLD", "quoteType": "ETF", "shortname": "SPDR Gold"},
                  {"symbol": "X", "quoteType": "OPTION"}]
        with mock.patch("tickview.providers.yahoo.yf.Search") as search:
            search.return_value.quotes = quotes
            found = await YahooProvider().search("gold")
        self.assertEqual([(info.symbol, info.kind) for info in found], [("GC=F", "futures"), ("GLD", "fund")])

    def test_split_symbol(self) -> None:
        # Jeden rozklad „zdroj:SYMBOL“ pro vstupy indikátoru, scanner i hledání
        self.assertEqual(split_symbol(" Yahoo:gc=f "), ("yahoo", "GC=F"))
        self.assertEqual(split_symbol("btcusdt"), ("", "BTCUSDT"))

    def test_direct_symbol(self) -> None:
        provider, symbol = provider_symbol(" Yahoo:gc=f ")
        self.assertEqual((provider.id, symbol), ("yahoo", "GC=F"))
        # Bez prefixu, neznámý zdroj nebo prázdný symbol = běžné hledání
        for text in ("GC=F", "oanda:XAUUSD", "yahoo:", "yahoo:GC F"):
            self.assertIsNone(provider_symbol(text))


class WatchlistSortTest(unittest.TestCase):
    """Řazení symbolů watchlistu podle abecedy."""

    def test_sort(self) -> None:
        items = [{"source": "ibkr", "symbol": "MSFT"}, {"source": "binance", "symbol": "BTCUSDT"},
                 {"source": "alpaca", "symbol": "MSFT"}, {"source": "yahoo", "symbol": "aapl"}]
        pairs = lambda result: [f"{item['source']}:{item['symbol']}" for item in result]  # noqa: E731
        # Abeceda bez ohledu na velikost písmen, shodný symbol vzestupně podle zdroje
        self.assertEqual(pairs(sort_watchlist(items, "asc")),
                         ["yahoo:aapl", "binance:BTCUSDT", "alpaca:MSFT", "ibkr:MSFT"])
        self.assertEqual(pairs(sort_watchlist(items, "desc")),
                         ["alpaca:MSFT", "ibkr:MSFT", "binance:BTCUSDT", "yahoo:aapl"])
        # Bez řazení (i neznámý klíč) pořadí přidání
        self.assertEqual(sort_watchlist(items, ""), items)
        self.assertEqual(sort_watchlist(items, "xyz"), items)

    def test_sort_by_flag(self) -> None:
        # Barvy v pořadí palety, bez vlaječky (i neznámá barva) na konec; stejná
        # barva drží vlastní pořadí i obráceně
        items = [{"symbol": "A", "flag": None}, {"symbol": "B", "flag": "green"}, {"symbol": "C", "flag": "red"},
                 {"symbol": "D", "flag": "green"}, {"symbol": "E", "flag": "xyz"}]
        symbols = lambda result: "".join(item["symbol"] for item in result)  # noqa: E731
        self.assertEqual(symbols(sort_watchlist(items, "flag")), "CBDAE")
        self.assertEqual(symbols(sort_watchlist(items, "flag_desc")), "AEBDC")

    def test_flag_colors_styled(self) -> None:
        # Každá barva palety má odstín ve style.css (atribut data-flag)
        css = (Path(__file__).resolve().parent.parent / "tickview/ui/static/style.css").read_text(encoding="utf-8")
        for color in FLAG_COLORS:
            self.assertIn(f'[data-flag="{color}"]', css)

    def test_sort_column(self) -> None:
        # Sloupec záhlaví podle řazení, vlastní pořadí (i neznámé) žádný
        self.assertEqual([sort_column(sort) for sort in ("asc", "desc", "flag", "flag_desc")],
                         ["symbol", "symbol", "flag", "flag"])
        self.assertIsNone(sort_column(""))
        self.assertIsNone(sort_column("xyz"))

    def test_next_sort(self) -> None:
        # Klik na záhlaví: cyklus sloupce, z řazení jiného sloupce jeho první stav
        self.assertEqual([next_sort(sort, "symbol") for sort in ("", "asc", "desc", "flag")], ["asc", "desc", "", "asc"])
        self.assertEqual([next_sort(sort, "flag") for sort in ("", "flag", "flag_desc", "desc")],
                         ["flag", "flag_desc", "", "flag"])

    def test_click_flag(self) -> None:
        # Klik na vlaječku řádku: přidá naposledy použitou barvu (neznámá = červená), nebo odebere
        self.assertEqual(click_flag(None, None, "blue"), "blue")
        self.assertEqual(click_flag(None, None, None), "red")
        self.assertIsNone(click_flag("green", None, "blue"))
        # Barva z palety: nastaví ji, stejná barva vlaječku odebere, neznámá = klik na řádek
        self.assertEqual(click_flag("green", "pink", "blue"), "pink")
        self.assertIsNone(click_flag("pink", "pink", "blue"))
        self.assertEqual(click_flag(None, "xyz", "cyan"), "cyan")


class WatchlistsTest(unittest.IsolatedAsyncioTestCase):
    """Více pojmenovaných watchlistů a převod původního jediného seznamu."""

    async def test_migration_and_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            # Databáze ze starší verze: jen tabulka watchlist se dvěma symboly
            legacy = sqlite3.connect(path)
            legacy.executescript(
                "CREATE TABLE watchlist (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, "
                "symbol TEXT NOT NULL, added_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE (source, symbol));"
                "INSERT INTO watchlist (source, symbol) VALUES ('binance', 'BTCUSDT'), ('ibkr', 'MSFT');"
            )
            legacy.commit()
            legacy.close()
            cache = KlineCache(path)
            await cache.init()
            try:
                lists = await cache.watchlists()
                self.assertEqual([(item["name"], item["count"]) for item in lists], [("Watchlist", 2)])
                first = lists[0]["id"]
                self.assertEqual(
                    await cache.watchlist_list(first),
                    [{"source": "binance", "symbol": "BTCUSDT", "section_id": None, "flag": None},
                     {"source": "ibkr", "symbol": "MSFT", "section_id": None, "flag": None}],
                )
                # Kopie seznamu, stejný symbol ve dvou seznamech, přejmenování
                copy = await cache.watchlist_create("Forex", copy_from=first)
                await cache.watchlist_add(copy, "ibkr", "EUR.USD")
                await cache.watchlist_add(copy, "ibkr", "EUR.USD")
                await cache.watchlist_remove(copy, "binance", "BTCUSDT")
                await cache.watchlist_rename(copy, "FX")
                self.assertEqual(len(await cache.watchlist_list(copy)), 2)
                # Vlaječka platí pro symbol ve všech seznamech, None ji odebere
                await cache.symbol_flag_set("ibkr", "MSFT", "blue")
                for list_id in (first, copy):
                    flags = {item["symbol"]: item["flag"] for item in await cache.watchlist_list(list_id)}
                    self.assertEqual(flags["MSFT"], "blue")
                await cache.symbol_flag_set("ibkr", "MSFT", None)
                self.assertEqual([item["flag"] for item in await cache.watchlist_list(first)], [None, None])
                self.assertEqual(len(await cache.watchlist_list(first)), 2)
                self.assertEqual([item["name"] for item in await cache.watchlists()], ["Watchlist", "FX"])
                await cache.watchlist_clear(first)
                self.assertEqual(await cache.watchlist_list(first), [])
                await cache.watchlist_delete(copy)
                self.assertEqual([item["id"] for item in await cache.watchlists()], [first])
            finally:
                await cache.close()


class NewDatabaseTest(unittest.IsolatedAsyncioTestCase):
    """Příznak nově založené databáze (první spuštění po instalaci → uvítací dialog)."""

    async def test_created_only_for_new_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            cache = KlineCache(path)
            await cache.init()
            self.assertTrue(cache.created)
            await cache.close()
            # Další spuštění nad existující databází (i po aktualizaci aplikace)
            cache = KlineCache(path)
            await cache.init()
            self.assertFalse(cache.created)
            await cache.close()

class WatchlistSectionsLogicTest(unittest.TestCase):
    """Sekce watchlistu: skupiny, přesuny myší a odebrání sekce."""

    # Symboly bez sekce A, B; sekce 1 s C; sekce 2 s D, E
    GROUPS = [(None, ["x:A", "x:B"]), (1, ["x:C"]), (2, ["x:D", "x:E"])]
    # Sekce 1 nahoře, pak symboly bez sekce, pak sekce 2
    MIDDLE = [(1, ["x:C"]), (None, ["x:A", "x:B"]), (2, ["x:D", "x:E"])]

    def test_build_groups(self) -> None:
        items = [{"source": "x", "symbol": "C", "section_id": 1}, {"source": "x", "symbol": "A", "section_id": None},
                 {"source": "x", "symbol": "Z", "section_id": 99}]
        sections = [{"id": 1, "position": 0}, {"id": 2, "position": 2}]
        # Neznámá (smazaná) sekce = bez sekce; prázdná sekce zůstane; skupina bez sekce na své pozici
        self.assertEqual(build_groups(items, sections, 1), [(1, ["x:C"]), (None, ["x:A", "x:Z"]), (2, [])])
        # Při shodě pozic je skupina bez sekce první; prázdná skupina bez sekce se drží na začátku
        self.assertEqual(build_groups(items, sections, 0)[0][0], None)
        self.assertEqual(build_groups(items[:1], sections, 5), [(None, []), (1, ["x:C"]), (2, [])])

    def test_move_symbol(self) -> None:
        # Za záhlaví = začátek sekce, před záhlaví = konec předchozí skupiny
        self.assertEqual(move_symbol(self.GROUPS, "x:A", ("section", 2), True),
                         [(None, ["x:B"]), (1, ["x:C"]), (2, ["x:A", "x:D", "x:E"])])
        self.assertEqual(move_symbol(self.GROUPS, "x:D", ("section", 1), False),
                         [(None, ["x:A", "x:B", "x:D"]), (1, ["x:C"]), (2, ["x:E"])])
        # Před / za symbol jiné sekce, bez cíle = konec seznamu
        self.assertEqual(move_symbol(self.GROUPS, "x:E", ("symbol", "x:C"), False),
                         [(None, ["x:A", "x:B"]), (1, ["x:E", "x:C"]), (2, ["x:D"])])
        self.assertEqual(move_symbol(self.GROUPS, "x:A", None, True),
                         [(None, ["x:B"]), (1, ["x:C"]), (2, ["x:D", "x:E", "x:A"])])
        # Vstup se nemění
        self.assertEqual(self.GROUPS[0], (None, ["x:A", "x:B"]))
        # Nad první sekci, když symboly bez sekce stojí jinde = začátek první sekce
        self.assertEqual(move_symbol(self.MIDDLE, "x:D", ("section", 1), False),
                         [(1, ["x:D", "x:C"]), (None, ["x:A", "x:B"]), (2, ["x:E"])])
        # Poslední symbol bez sekce odejde – prázdná skupina se přesune na začátek
        self.assertEqual(move_symbol([(1, []), (None, ["x:A"])], "x:A", ("section", 1), True),
                         [(None, []), (1, ["x:A"])])

    def test_move_section(self) -> None:
        swapped = [(None, ["x:A", "x:B"]), (2, ["x:D", "x:E"]), (1, ["x:C"])]
        self.assertEqual(move_section(self.GROUPS, 2, ("section", 1), False), swapped)
        self.assertEqual(move_section(self.GROUPS, 1, ("section", 2), True), swapped)
        # Na symbol = za jeho skupinu, vlastní sekce nebo její symbol nic nezmění
        self.assertEqual(move_section(self.GROUPS, 1, ("symbol", "x:E"), False), swapped)
        self.assertEqual(move_section(self.GROUPS, 2, ("symbol", "x:D"), False), self.GROUPS)
        # Horní polovina prvního symbolu skupiny = před skupinu (sekce nad symboly bez sekce)
        self.assertEqual(move_section(self.GROUPS, 1, ("symbol", "x:A"), False), self.MIDDLE)
        self.assertEqual(move_section(self.GROUPS, 1, ("symbol", "x:B"), False), self.GROUPS)
        # Skupina bez sekce (záhlaví Bez sekce) jde přesunout zpět na začátek i na konec
        self.assertEqual(move_section(self.MIDDLE, None, ("section", 1), False), self.GROUPS)
        self.assertEqual(move_section(self.MIDDLE, None, ("section", 2), True),
                         [(1, ["x:C"]), (2, ["x:D", "x:E"]), (None, ["x:A", "x:B"])])

    def test_remove_section(self) -> None:
        # Symboly odebrané sekce připadnou skupině nad ní
        self.assertEqual(remove_section(self.GROUPS, 2), [(None, ["x:A", "x:B"]), (1, ["x:C", "x:D", "x:E"])])
        self.assertEqual(remove_section(self.GROUPS, 1), [(None, ["x:A", "x:B", "x:C"]), (2, ["x:D", "x:E"])])
        # První sekce: symboly přejdou mezi symboly bez sekce (na jejich začátek)
        self.assertEqual(remove_section(self.MIDDLE, 1), [(None, ["x:C", "x:A", "x:B"]), (2, ["x:D", "x:E"])])

    def test_parse_entry(self) -> None:
        self.assertEqual(parse_entry("symbol:ibkr:EUR.USD"), ("symbol", "ibkr:EUR.USD"))
        self.assertEqual(parse_entry("section:12"), ("section", 12))
        self.assertEqual(parse_entry("section:none"), ("section", None))
        for invalid in (None, "", "section:x", "symbol:", "foo:1"):
            self.assertIsNone(parse_entry(invalid), invalid)


class WatchlistSectionsTest(unittest.IsolatedAsyncioTestCase):
    """Sekce seznamu symbolů v databázi: vytvoření, uložení přesunu, kopie, smazání."""

    async def test_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = KlineCache(Path(tmp) / "test.db")
            await cache.init()
            try:
                list_id = (await cache.watchlists())[0]["id"]
                for symbol in ("A", "B", "C"):
                    await cache.watchlist_add(list_id, "x", symbol)
                tech = await cache.watchlist_section_create(list_id, "Tech")
                fx = await cache.watchlist_section_create(list_id, "FX")
                await cache.watchlist_add(list_id, "x", "D", fx)
                await cache.watchlist_section_rename(tech, "Akcie")
                await cache.watchlist_section_collapse(tech, True)
                self.assertEqual(await cache.watchlist_sections(list_id), [
                    {"id": tech, "name": "Akcie", "position": 1, "collapsed": True},
                {"id": fx, "name": "FX", "position": 2, "collapsed": False}])
                # Přesun: FX nahoru, pak symboly bez sekce, pak Akcie; C do FX za D, A do Akcie
                layout = [(fx, ["x:D", "x:C"]), (None, ["x:B"]), (tech, ["x:A"])]
                await cache.watchlist_save_layout(list_id, layout)

                async def groups_of(target_list: int) -> list:
                    # Skupiny seznamu načtené z databáze
                    free_position = next(
                        item["free_position"] for item in await cache.watchlists() if item["id"] == target_list
                    )
                    return build_groups(
                        await cache.watchlist_list(target_list), await cache.watchlist_sections(target_list), free_position
                    )

                self.assertEqual(await groups_of(list_id), layout)
                # Kopie seznamu převezme sekce, rozřazení symbolů i pozici skupiny bez sekce
                copy = await cache.watchlist_create("Kopie", copy_from=list_id)
                copy_sections = await cache.watchlist_sections(copy)
                self.assertEqual([section["name"] for section in copy_sections], ["FX", "Akcie"])
                self.assertEqual(
                    await groups_of(copy),
                    [(copy_sections[0]["id"], ["x:D", "x:C"]), (None, ["x:B"]), (copy_sections[1]["id"], ["x:A"])],
                )
                # Nová sekce přijde na konec (za skupinu bez sekce i při shodě pozic)
                last = await cache.watchlist_section_create(list_id, "Nová")
                self.assertEqual((await groups_of(list_id))[-1], (last, []))
                # Odebrání sekce s přeřazením symbolů, vyprázdnění smaže i sekce
                await cache.watchlist_save_layout(list_id, [(None, ["x:B"]), (fx, ["x:D", "x:C", "x:A"]), (last, [])], tech)
                self.assertEqual([section["id"] for section in await cache.watchlist_sections(list_id)], [fx, last])
                await cache.watchlist_clear(list_id)
                self.assertEqual(await cache.watchlist_sections(list_id), [])
                self.assertEqual(len(await cache.watchlist_sections(copy)), 2)
            finally:
                await cache.close()


class StylesTest(unittest.TestCase):
    """Barvy s průhledností, styl grafu, šablony a oblíbené nástroje."""

    def test_normalize_color(self) -> None:
        self.assertEqual(normalize_color("#2962FF"), "#2962ff")
        # Plně krycí rgba se ukládá jako hex, průhledná zůstane rgba
        self.assertEqual(normalize_color("rgba(41, 98, 255, 1)"), "#2962ff")
        self.assertEqual(normalize_color("rgb(41,98,255)"), "#2962ff")
        self.assertEqual(normalize_color("rgba(41,98,255,0.456)"), "rgba(41, 98, 255, 0.46)")
        self.assertEqual(normalize_color("rgba(0, 0, 0, 0)"), "rgba(0, 0, 0, 0)")
        for invalid in ("#12345", "red", "rgba(256, 0, 0, 1)", "rgba(1, 2, 3, 1.5)", None, 5):
            self.assertIsNone(normalize_color(invalid), invalid)

    def test_chart_style(self) -> None:
        style = normalize_chart_style({"body_up": "rgba(1, 2, 3, 0.5)", "wick": 0, "body_down": "xx", "extra": 1})
        self.assertEqual(style["body_up"], "rgba(1, 2, 3, 0.5)")
        self.assertIs(style["wick"], False)
        # Neplatná barva → výchozí, neznámý klíč zmizí
        self.assertEqual(style["body_down"], CHART_DEFAULTS["body_down"])
        self.assertNotIn("extra", style)
        self.assertEqual(load_chart_style("{nejson"), CHART_DEFAULTS)
        self.assertEqual(load_chart_style(None), CHART_DEFAULTS)
        # Starší uložený styl bez aktuální ceny: čára a štítek zapnuté s výchozími barvami
        legacy = normalize_chart_style({"body": False})
        self.assertIs(legacy["last_price"], True)
        self.assertEqual(legacy["last_price_down"], CHART_DEFAULTS["last_price_down"])

    def test_templates(self) -> None:
        templates = save_template({}, "Moje", {"a": 1})
        templates = save_template(templates, "alfa", {"a": 2})
        # Stejný název bez ohledu na velikost písmen šablonu přepíše
        templates = save_template(templates, "MOJE", {"a": 3})
        self.assertEqual(list(templates), ["alfa", "MOJE"])
        self.assertEqual(templates["MOJE"], {"a": 3})
        self.assertEqual(list(delete_template(templates, "alfa")), ["MOJE"])
        self.assertEqual(load_templates('{"b": {"x": 1}, "a": {"y": 2}, "": {}, "c": 5}'), {"a": {"y": 2}, "b": {"x": 1}})
        self.assertEqual(load_templates("[1]"), {})
        self.assertEqual(clean_name("  Můj   styl "), "Můj styl")
        self.assertIsNone(clean_name("   "))
        self.assertIsNone(clean_name("x" * 41))

    def test_drawing_style_and_chart_type(self) -> None:
        style = normalize_drawing_style({"color": "rgba(1, 2, 3, 0.5)", "options": {"width": 3, "lineStyle": "x", "fontSize": 200}})
        options = dict(style["options"])
        # Viditelnost podle timeframu je vždy doplněná (výchozí = všude), testuje ji test_layouts
        self.assertIn("visibility", options)
        options.pop("visibility")
        self.assertEqual(style["color"], "rgba(1, 2, 3, 0.5)")
        self.assertEqual(options, {"width": 3, "lineStyle": "solid", "fontSize": 72})
        # Prázdná šablona = tovární styl
        factory = dict(normalize_drawing_style(None)["options"])
        factory.pop("visibility")
        self.assertEqual(factory, {"width": 1.5, "lineStyle": "solid", "fontSize": 13})
        self.assertEqual(normalize_chart_type("heikin"), "heikin")
        self.assertEqual(normalize_chart_type("renko"), "candles")

    def test_favorite_tools(self) -> None:
        allowed = ["cursor", "trend", "hline", "fib"]
        # Pořadí podle lišty, neznámé a duplicitní režimy vynechány
        self.assertEqual(load_favorites('["fib", "nope", "trend", "fib"]', allowed), ["trend", "fib"])
        self.assertEqual(load_favorites("{}", allowed), [])
        self.assertEqual(load_favorites("x", allowed), [])
        # Dosud neuložené oblíbené = výchozí; uložený prázdný seznam zůstane prázdný
        self.assertEqual(load_favorites(None, allowed, ["hline", "cursor"]), ["cursor", "hline"])
        self.assertEqual(load_favorites("[]", allowed, ["hline"]), [])
        self.assertEqual(load_panel_position('{"left": 10.6, "top": -5}'), {"left": 10, "top": 0})
        self.assertIsNone(load_panel_position('{"left": true, "top": 1}'))
        self.assertIsNone(load_panel_position(None))
        # Hodnoty přímo z události JS (bez JSON)
        self.assertEqual(normalize_favorites(["hline", 5], allowed), ["hline"])
        self.assertEqual(normalize_favorites("trend", allowed), [])
        self.assertEqual(normalize_panel_position({"left": 3, "top": 4}), {"left": 3, "top": 4})

    def test_favorite_indicators(self) -> None:
        # Bez seznamu povolených zůstanou i neznámé druhy (dočasně nenačtený vlastní
        # indikátor); duplicity a ne-řetězce ne, pořadí abecední
        self.assertEqual(load_favorites('["rsi", "my_custom", 3, "rsi"]', None), ["my_custom", "rsi"])
        self.assertEqual(load_favorites(None, None), [])
        self.assertEqual(load_favorites('{"rsi": 1}', None), [])
        self.assertEqual(load_favorites("x", None), [])


if __name__ == "__main__":
    unittest.main()
