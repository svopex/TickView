"""Testy logické vrstvy a společných částí zdrojů (spuštění: python -m unittest)."""

import asyncio
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml
from eventkit import Event
from ib_async import BarData, BarDataList

from tickview.config import update_config_section
from tickview.logic.cache import KlineCache, fetch_history, uncovered_ranges
from tickview.indicators.builtin import BUILTIN_INDICATORS
from tickview.indicators.compute import (
    compute_indicators,
    load_saved_indicators,
    new_indicator,
    normalize_params,
)
from tickview.logic.sessions import filter_session, timezone_options, utc_offset_label
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
from tickview.providers.ibkr import IbkrProvider, forex_parts, forex_suggestions


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


class ProvidersBaseTest(unittest.IsolatedAsyncioTestCase):
    """Omezovač dotazů a smyčka pollingu."""

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
                    [{"source": "binance", "symbol": "BTCUSDT"}, {"source": "ibkr", "symbol": "MSFT"}],
                )
                # Kopie seznamu, stejný symbol ve dvou seznamech, přejmenování
                copy = await cache.watchlist_create("Forex", copy_from=first)
                await cache.watchlist_add(copy, "ibkr", "EUR.USD")
                await cache.watchlist_add(copy, "ibkr", "EUR.USD")
                await cache.watchlist_remove(copy, "binance", "BTCUSDT")
                await cache.watchlist_rename(copy, "FX")
                self.assertEqual(len(await cache.watchlist_list(copy)), 2)
                self.assertEqual(len(await cache.watchlist_list(first)), 2)
                self.assertEqual([item["name"] for item in await cache.watchlists()], ["Watchlist", "FX"])
                await cache.watchlist_clear(first)
                self.assertEqual(await cache.watchlist_list(first), [])
                await cache.watchlist_delete(copy)
                self.assertEqual([item["id"] for item in await cache.watchlists()], [first])
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


if __name__ == "__main__":
    unittest.main()
