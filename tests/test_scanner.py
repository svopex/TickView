"""Testy rozhraní indikátorů, loaderu, scanneru a časových připomínek (spuštění: python -m unittest)."""

import asyncio
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.test_logic import FakeProvider
from tickview.indicators import registry
from tickview.indicators.api import SCANNER_SYMBOLS, Context, Indicator, Param, Result, Signal, scanner_params
from tickview.indicators.compute import compute_indicators, normalize_indicators
from tickview.logic.cache import KlineCache, fetch_recent
from tickview.logic.reminders import ReminderTracker, normalize_reminder, parse_hhmm
from tickview.logic.scanner import (
    CloseAlertWindow,
    alert_message,
    bar_hits,
    current_bar_close,
    error_row,
    layout_jobs,
    make_job,
    overview_payload,
    parse_symbol,
    status_kind,
    status_text,
    symbol_row,
    visible_rows,
)
from tickview.providers.base import TIMEFRAME_MS, Candle, utc_now_ms
from tickview.services.alerts import AlertEngine
from tickview.services.scanner import ScannerService


class ParamTest(unittest.TestCase):
    """Schéma parametrů: převod a kontrola hodnot."""

    def test_numbers(self) -> None:
        length = Param.int("length", 20, "Délka", min=1, max=100)
        self.assertEqual(length.normalize(20.0), 20)
        self.assertEqual(length.normalize(None), 20)
        for bad in (0, 101, 2.5, "x"):
            with self.assertRaises(ValueError):
                length.normalize(bad)
        tolerance = Param.float("tol", 0.1, "Tolerance", min=0, max=1)
        self.assertEqual(tolerance.normalize(0), 0.0)
        positive = Param.float("mult", 2.0, "Násobek", min=0, max=10, exclusive_min=True)
        with self.assertRaises(ValueError):
            positive.normalize(0)

    def test_symbol(self) -> None:
        symbol = Param.symbol("s", "")
        self.assertEqual(symbol.normalize(" Yahoo:EURUSD=X "), "yahoo:EURUSD=X")
        self.assertEqual(symbol.normalize("BTCUSDT"), "BTCUSDT")
        self.assertEqual(symbol.normalize(""), "")
        for bad in ("EUR USD", "yahoo:"):
            with self.assertRaises(ValueError):
                symbol.normalize(bad)

    def test_other_kinds(self) -> None:
        self.assertIs(Param.bool("b", True).normalize(0), False)
        color = Param.color("c", "#112233")
        self.assertEqual(color.normalize("#AABBCC"), "#aabbcc")
        # Průhledná barva se přijme jako rgba, neplatná = výchozí
        self.assertEqual(color.normalize("rgba(1, 2, 3, 0.5)"), "rgba(1, 2, 3, 0.5)")
        self.assertEqual(color.normalize("red"), "#112233")
        select = Param.select("s", "a", options=[("a", "A"), ("b", "B")])
        self.assertEqual(select.normalize("b"), "b")
        with self.assertRaises(ValueError):
            select.normalize("c")
        self.assertEqual(Param.timeframe("tf", "1h").normalize("4h"), "4h")


class InstancesTest(unittest.TestCase):
    """Instance indikátorů: nenačtený indikátor se nezahodí."""

    def test_unknown_kind_is_kept(self) -> None:
        items = normalize_indicators([{"id": "q", "kind": "missing_plugin", "params": {"a": 1}}])
        self.assertEqual(items, [{"id": "q", "kind": "missing_plugin", "params": {"a": 1}, "visible": True}])
        candles = [Candle(i * 60_000, 1, 2, 0.5, 1.5, 1) for i in range(50)]
        payload = compute_indicators(items, candles)
        self.assertEqual(payload["groups"][0]["title"], "missing_plugin (nenačten)")
        self.assertEqual(payload["overlays"], [])

    def test_invalid_params_fall_back_to_defaults(self) -> None:
        items = normalize_indicators([{"id": "s", "kind": "sma", "params": {"length": -5}}])
        self.assertEqual(items[0]["params"], {"length": 20})


class LoaderTest(unittest.TestCase):
    """Načítání vlastních indikátorů z adresářů (jeden adresář = jeden indikátor)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "tv_test_custom"
        files = {
            "good/indicator.py": """
                from tickview.indicators.api import Indicator, Result
                class Good(Indicator):
                    id = "test_good"
                    title = name = "Good"
                    def compute(self, df, params, ctx):
                        return Result()
                INDICATOR = Good()
            """,
            "broken/indicator.py": "this is not python",
            "oldapi/indicator.py": """
                from tickview.indicators.api import Indicator
                class Old(Indicator):
                    id = "test_old"
                    api_version = 0
                INDICATOR = Old()
            """,
            # Adresář bez indicator.py (např. jen Pine verze) se ignoruje
            "notes/readme.txt": "jen poznámky",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        (root / "__init__.py").write_text("")
        sys.path.insert(0, self.tmp.name)
        self.saved = (registry.CUSTOM_DIR, registry.CUSTOM_PACKAGE)
        registry.CUSTOM_DIR, registry.CUSTOM_PACKAGE = root, "tv_test_custom"
        self._reset()

    def tearDown(self) -> None:
        registry.CUSTOM_DIR, registry.CUSTOM_PACKAGE = self.saved
        sys.path.remove(self.tmp.name)
        for name in [name for name in sys.modules if name.startswith("tv_test_custom")]:
            del sys.modules[name]
        self._reset()
        self.tmp.cleanup()

    @staticmethod
    def _reset() -> None:
        registry._registry.clear()
        registry._errors.clear()
        registry._loaded = False

    def test_loader_isolates_errors(self) -> None:
        # Chyby vadných indikátorů jdou do logu (zachytíme je, ať nezaplevelí výstup testů)
        with self.assertLogs("tickview.indicators.registry", level="ERROR"):
            indicators = registry.all_indicators()
        self.assertIn("test_good", indicators)
        self.assertIn("sma", indicators)
        self.assertNotIn("test_old", indicators)
        errors = registry.load_errors()
        self.assertEqual(set(errors), {"broken", "oldapi"})
        self.assertIn("verze", errors["oldapi"])


class CustomTestsTest(unittest.TestCase):
    """Testy vlastních indikátorů v custom/<název>/tests/ musí najít python -m unittest."""

    def test_tests_are_discoverable(self) -> None:
        # Discovery vleze jen do balíčků: bez __init__.py by se testy potichu přeskočily
        for tests_dir in registry.CUSTOM_DIR.glob("*/tests"):
            for package in (tests_dir.parent, tests_dir):
                self.assertTrue((package / "__init__.py").exists(), f"{package} nemá __init__.py")


class FakeSignals(Indicator):
    """Testovací indikátor se signálem long na poslední svíčce symbolu „HIT“."""

    id = "test_signals"
    title = name = "Signals"
    has_signals = True
    params = [Param.int("length", 5, min=1, max=50), *scanner_params(["fake:HIT", "MISS", "nope:X"])]

    def compute(self, df, params, ctx: Context) -> Result:
        if ctx.symbol != "HIT":
            return Result()
        return Result(signals=[Signal(int(df["time"].iloc[-1]), "long", 0, "P1"),
                               Signal(int(df["time"].iloc[-2]), "short", 1, "P2")])


def register_fake_signals(test: unittest.TestCase) -> None:
    """Zaregistruje FakeSignals do konce testu (testy scanneru nezávisí na žádném vlastním indikátoru).

    Parametry:
        test: běžící test; úklid registru se zařadí přes addCleanup.
    """
    registry.all_indicators()
    registry._registry[FakeSignals.id] = FakeSignals()
    test.addCleanup(registry._registry.pop, FakeSignals.id, None)


class ScannerLogicTest(unittest.TestCase):
    """Řádky scanneru, texty a alert před zavřením svíčky."""

    def setUp(self) -> None:
        register_fake_signals(self)

    def test_symbol_row(self) -> None:
        signals = [Signal(3, "long", 3), Signal(1, "long", 1, "P2"), Signal(0, "short", 0, "P1")]
        row = symbol_row("y", "EURUSD", signals, 2, 1000)
        self.assertEqual((row["long"], row["short"]), (1, 0))
        self.assertEqual(status_text(row), "LONG(1) + SHORT(0)")
        self.assertEqual(status_kind(row), "both")
        empty = symbol_row("y", "GBPUSD", signals, 1, 1000)
        self.assertEqual(status_text(empty), "SHORT(0)")
        quiet = symbol_row("y", "USDJPY", [], 2, 1000)
        self.assertEqual(visible_rows([row, quiet], True), [row])
        self.assertEqual(len(visible_rows([row, quiet], False)), 2)
        self.assertEqual(bar_hits([row, empty, quiet]), [("EURUSD", "SHORT"), ("GBPUSD", "SHORT")])

    def test_bar_close_and_message(self) -> None:
        rows = [{"last_open": 3_600_000}, {"last_open": 7_200_000}, {"last_open": None}]
        self.assertEqual(current_bar_close(rows, 3_600_000, 0), 10_800_000)
        self.assertEqual(current_bar_close([], 3_600_000, 5_000_000), 7_200_000)
        message = alert_message("Signals", "1h", 10, [("EURUSD", "LONG"), ("XAUUSD", "SHORT")])
        self.assertEqual(message, "Signals 1h | 10 min do zavření svíčky | 2 trhy: EURUSD LONG, XAUUSD SHORT")

    def test_close_alert_window(self) -> None:
        window = CloseAlertWindow()
        # Mimo prahy nic, 10 min před zavřením první práh, 5 min druhý – každý jednou
        self.assertIsNone(window.check(100, 12.0, 2, 10, 5))
        self.assertEqual(window.check(100, 9.8, 2, 10, 5), 10)
        self.assertIsNone(window.check(100, 9.0, 2, 10, 5))
        self.assertEqual(window.check(100, 4.9, 2, 10, 5), 5)
        self.assertIsNone(window.check(100, 1.0, 2, 10, 5))
        # Bez signálu se nic neposílá; nová svíčka povolí prahy znovu
        self.assertIsNone(window.check(200, 3.0, 0, 10, 5))
        # Oba prahy najednou (díra mezi kontrolami) = jedna zpráva s prahem 1
        self.assertEqual(window.check(200, 3.0, 1, 10, 5), 10)
        self.assertIsNone(window.check(200, 2.0, 1, 10, 5))
        # commit=False jen zjišťuje
        self.assertEqual(window.check(300, 4.0, 1, 0, 5, commit=False), 5)
        self.assertEqual(window.check(300, 4.0, 1, 0, 5), 5)

    def test_jobs_from_layout(self) -> None:
        self.assertEqual(parse_symbol("yahoo:EURUSD=X", "binance"), ("yahoo", "EURUSD=X"))
        self.assertEqual(parse_symbol("BTCUSDT", "binance"), ("binance", "BTCUSDT"))
        self.assertIsNone(parse_symbol(" ", "binance"))
        params = {**FakeSignals().defaults(), "scan_alert": False}
        spec = {"kind": FakeSignals.id, "params": params, "visible": True}
        job, table = make_job(spec, "binance")
        self.assertTrue(table)
        self.assertEqual(job["config"]["symbols"][0], ("fake", "HIT"))
        self.assertEqual(job["config"]["timeframe"], "1h")
        # Skrytý indikátor bez alertu, tabulka „nezobrazovat“ bez alertu a bez symbolů = bez úlohy
        self.assertEqual(make_job({**spec, "visible": False}, "binance"), (None, False))
        self.assertIsNone(make_job({**spec, "params": {**params, "scan_table_position": "off"}}, "binance")[0])
        alert_only = make_job({**spec, "visible": False, "params": {**params, "scan_alert": True}}, "binance")
        self.assertEqual((alert_only[0] is not None, alert_only[1]), (True, False))
        # Vypnuté Tables nebo indikátor skrytý na timeframu grafu = bez tabulky
        self.assertIsNone(make_job({**spec, "style": {"tables": False}}, "binance")[0])
        hidden_hours = {**spec, "visibility": {"hours": {"enabled": False}}}
        self.assertFalse(make_job(hidden_hours, "binance", "1h")[1])
        self.assertTrue(make_job(hidden_hours, "binance", "5m")[1])
        no_symbols = {**params, **{f"scan_symbol_{index}": "" for index in range(1, SCANNER_SYMBOLS + 1)}}
        self.assertIsNone(make_job({**spec, "params": no_symbols}, "binance")[0])
        # Indikátor bez vstupů scanneru nic neskenuje; shodné instance ve dvou grafech = jedna úloha
        self.assertIsNone(make_job({"kind": "sma", "params": {"length": 20}}, "binance")[0])
        pane = {"symbol": "binance:BTCUSDT", "indicators": [spec]}
        jobs, tables = layout_jobs({"panes": [pane, pane, {"symbol": "binance:X", "indicators": []}]})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(tables, [job["key"], job["key"], None])


class RemindersTest(unittest.TestCase):
    """Časové připomínky: kontrola zadání a sloty okna."""

    @staticmethod
    def at(text: str) -> int:
        return int(datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Europe/Prague")).timestamp() * 1000)

    def test_normalize(self) -> None:
        self.assertEqual(parse_hhmm("7:05"), 425)
        self.assertIsNone(parse_hhmm("24:00"))
        reminder = normalize_reminder({"start": "22:30", "end": "22:55", "interval": 5.0,
                                       "timezone": "Europe/Prague", "days": [0, 1, 9]})
        self.assertEqual((reminder["start"], reminder["interval"], reminder["days"]), ("22:30", 5, [0, 1]))
        for bad in ({"start": "23:00", "end": "22:00"}, {"interval": 0}, {"days": []}, {"timezone": "Mars/Base"}):
            data = {"start": "22:30", "end": "22:55", "interval": 5, "timezone": "Europe/Prague", "days": [0], **bad}
            with self.assertRaises(ValueError):
                normalize_reminder(data)

    def test_tracker_slots(self) -> None:
        reminder = normalize_reminder({"start": "22:30", "end": "22:55", "interval": 5,
                                       "timezone": "Europe/Prague", "days": [0, 1, 2, 3, 4]})
        tracker = ReminderTracker()
        # 2026-11-16 je pondělí
        fired = [tracker.check(reminder, self.at(f"2026-11-16T{time}")) for time in
                 ("22:29", "22:30", "22:32", "22:36", "22:39", "22:55", "22:56")]
        self.assertEqual(fired, [False, True, False, True, False, True, False])
        # Další den se série povolí znovu, o víkendu (vybrané jen Po–Pá) nic
        self.assertTrue(tracker.check(reminder, self.at("2026-11-17T22:31")))
        self.assertFalse(tracker.check(reminder, self.at("2026-11-21T22:31")))


class FakeAlertEngine:
    """Zachytí zprávy scanneru místo spuštění alertu.

    Parametry:
        alerts: aktivní alerty scanneru (podmínka alert_call) podle id indikátoru.
    """

    def __init__(self, alerts: dict[str, list[dict]] | None = None) -> None:
        self.alerts = alerts or {}
        self.sent: list[dict] = []

    def alert_call_alerts(self, kind: str) -> list[dict]:
        return self.alerts.get(kind, [])

    async def fire_message(self, alert: dict, message: str, **event) -> None:
        self.sent.append({"alert_id": alert["id"], "message": message, **event})


class ScannerServiceTest(unittest.IsolatedAsyncioTestCase):
    """Scanner nad testovacím zdrojem a indikátorem se signály."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = KlineCache(Path(self.tmp.name) / "test.db")
        await self.cache.init()
        register_fake_signals(self)
        provider = FakeProvider()
        provider.name = "Fake"
        provider.timeframes = {"1h": "1h"}
        provider.symbol_kind = lambda symbol: "forex"
        provider.exchange_timezone = lambda symbol: "UTC"
        provider.price_tick = lambda symbol, price: None
        self.provider = provider

        def lookup(source: str):
            if source != "fake":
                raise KeyError(source)
            return provider

        self.engine = FakeAlertEngine()
        self.scanner = ScannerService(self.cache, self.engine, provider_lookup=lookup)

    async def asyncTearDown(self) -> None:
        await self.cache.close()
        self.tmp.cleanup()

    async def test_scan_and_alert(self) -> None:
        # Úloha z uloženého rozložení: instance indikátoru se vstupy scanneru (MISS bez prefixu = zdroj grafu)
        params = FakeSignals().normalize_params({"scan_alert": True, "scan_alert_minutes1": 60,
                                                 "scan_alert_minutes2": 0})
        layout = {"panes": [{"symbol": "fake:EURUSD", "timeframe": "1h",
                             "indicators": [{"kind": FakeSignals.id, "params": params}]}]}
        self.scanner.set_layout(layout)
        self.assertEqual(len(self.scanner.jobs), 1)
        job = next(iter(self.scanner.jobs.values()))
        await self.scanner.scan(job)
        rows = {row["symbol"]: row for row in self.scanner.result(job["key"])["rows"]}
        self.assertEqual((rows["HIT"]["long"], rows["HIT"]["short"]), (0, 1))
        self.assertEqual((rows["MISS"]["long"], rows["MISS"]["short"]), (None, None))
        self.assertIn("Neznámý zdroj", rows["X"]["error"])
        # Tabulka pro graf s indikátorem
        overview = self.scanner.table(0)
        self.assertEqual(overview["rows"][0]["symbol"], "HIT")
        # Bez alertu scanneru na indikátor se nic neposílá
        await self.scanner._check_alert(job)
        self.assertEqual(self.engine.sent, [])
        # Práh 60 min je vždy splněný – alert odejde jednou za svíčku přes alert scanneru
        self.engine.alerts = {FakeSignals.id: [{"id": 7}]}
        await self.scanner._check_alert(job)
        await self.scanner._check_alert(job)
        self.assertEqual(len(self.engine.sent), 1)
        self.assertIn("HIT LONG", self.engine.sent[0]["message"])
        self.assertEqual((self.engine.sent[0]["alert_id"], self.engine.sent[0]["symbol"]), (7, "Scanner Signals"))


class NotifyExternalTest(unittest.IsolatedAsyncioTestCase):
    """Notifikace mimo uložené alerty se zapíše do logu a ohlásí stránkám."""

    async def test_log_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = KlineCache(Path(tmp) / "test.db")
            await cache.init()
            try:
                engine = AlertEngine(cache)
                events: list[dict] = []
                engine.add_listener(events.append)
                await engine.notify_external({"telegram": False, "toast": True}, source="reminder",
                                             symbol="Uzavření pozic", timeframe="připomínka", message="Zavřít!")
                log = await cache.alert_log()
                self.assertEqual((log[0]["alert_id"], log[0]["message"], log[0]["price"]), (0, "Zavřít!", 0.0))
                self.assertEqual([event["type"] for event in events], ["notification", "log_changed"])
                self.assertIsNone(events[0]["price"])
            finally:
                await cache.close()


class YoungProvider(FakeProvider):
    """Testovací zdroj se symbolem, který má jen posledních 30 hodinových svíček."""

    async def history(self, symbol, timeframe, start_ms, end_ms):
        first_allowed = utc_now_ms() - 30 * TIMEFRAME_MS["1h"]
        return [c for c in await super().history(symbol, timeframe, start_ms, end_ms) if c.open_time >= first_allowed]


class FetchRecentTest(unittest.IsolatedAsyncioTestCase):
    """Společná smyčka „aspoň N posledních svíček“ (graf a scanner)."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = KlineCache(Path(self.tmp.name) / "test.db")
        await self.cache.init()

    async def asyncTearDown(self) -> None:
        await self.cache.close()
        self.tmp.cleanup()

    async def test_window_grows_until_enough(self) -> None:
        provider = FakeProvider()
        candles = await fetch_recent(provider, self.cache, "X", "1h", utc_now_ms(), min_count=50, window_bars=20)
        self.assertGreaterEqual(len(candles), 50)
        times = [c.open_time for c in candles]
        self.assertEqual(times, sorted(set(times)))
        # Okna se nepřekrývají: 20 + 40 svíček = dva dotazy
        self.assertEqual(len(provider.calls), 2)

    async def test_young_symbol_stops_early(self) -> None:
        provider = YoungProvider()
        candles = await fetch_recent(provider, self.cache, "Y", "1h", utc_now_ms(), min_count=500, window_bars=200)
        self.assertLessEqual(len(candles), 31)
        # Data začínají dlouho po začátku okna – další okna se nestahují
        self.assertEqual(len(provider.calls), 1)


class SignalAlertTest(unittest.TestCase):
    """Alert na signál indikátoru: kontrola definice a stav vyhodnocení."""

    def setUp(self) -> None:
        register_fake_signals(self)

    def test_normalize_and_texts(self) -> None:
        from tickview.logic.alerts import describe_alert, normalize_alert

        data = {"source": "fake", "symbol": "HIT", "timeframe": "1h", "condition": "signal_long",
                "trigger": "before_close", "target": {"type": "indicator", "kind": FakeSignals.id,
                                                      "params": {"length": 999}, "minutes": 7}}
        spec = normalize_alert(data)
        # Neplatné parametry = výchozí, zpráva se zástupným textem signálu
        self.assertEqual(spec["target"], {"type": "indicator", "kind": FakeSignals.id,
                                          "params": FakeSignals().defaults(), "minutes": 7})
        self.assertEqual(spec["message"], "HIT, 1h Signals {{signal}}")
        self.assertEqual(describe_alert(spec), "Signals: Signál Long · 7 min před zavřením svíčky")
        for bad in ({"condition": "crossing"}, {"trigger": "per_minute"},
                    {"target": {"type": "indicator", "kind": "sma"}}):
            with self.assertRaises(ValueError):
                normalize_alert({**data, **bad})

    def test_alert_call(self) -> None:
        from tickview.logic.alerts import describe_alert, normalize_alert

        data = {"source": "fake", "symbol": "HIT", "timeframe": "1h", "condition": "alert_call",
                "trigger": "alert_call", "target": {"type": "indicator", "kind": FakeSignals.id}}
        spec = normalize_alert(data)
        self.assertEqual(spec["message"], "{{message}}")
        self.assertEqual(describe_alert(spec), "Signals: Any alert() function call (scanner) · "
                                               "Podle vstupů indikátoru (před zavřením svíčky)")
        # Spouštění signálu u alertu scanneru nedává smysl; indikátor bez scanneru alert() nemá
        with self.assertRaises(ValueError):
            normalize_alert({**data, "trigger": "before_close"})
        with self.assertRaises(ValueError):
            normalize_alert({**data, "target": {"type": "indicator", "kind": "macd"}})

    def test_tracker(self) -> None:
        from tickview.logic.alerts import SignalAlertTracker

        hour = 3_600_000
        bar = Candle(10 * hour, 1, 1, 1, 1, 1)
        long0, short1 = Signal(0, "long", 0, "P2"), Signal(0, "short", 1, "P1")
        # before_close: přepočet jen v posledních 10 minutách svíčky, spustí jednou
        tracker = SignalAlertTracker("signal", "before_close", hour, 10)
        self.assertFalse(tracker.wants_compute(bar, 10 * hour + 30 * 60_000))
        self.assertTrue(tracker.wants_compute(bar, 10 * hour + 52 * 60_000))
        self.assertEqual(tracker.update([long0], bar), "LONG P2")
        self.assertFalse(tracker.wants_compute(bar, 10 * hour + 55 * 60_000))
        # Směr podmínky: short alert nereaguje na long signál
        short_tracker = SignalAlertTracker("signal_short", "per_bar", hour)
        self.assertIsNone(short_tracker.update([long0], bar))
        # per_bar_close: vyhodnotí uzavřenou svíčku (bars_ago 1) po začátku další
        close_tracker = SignalAlertTracker("signal", "per_bar_close", hour)
        self.assertFalse(close_tracker.wants_compute(bar, 10 * hour))
        nxt = Candle(11 * hour, 1, 1, 1, 1, 1)
        self.assertTrue(close_tracker.wants_compute(nxt, 11 * hour + 1000))
        self.assertEqual(close_tracker.update([short1], nxt), "SHORT P1")
        self.assertIsNone(close_tracker.update([short1], nxt))


class OverviewTest(unittest.TestCase):
    """Data přehledu scanneru v grafu."""

    def setUp(self) -> None:
        register_fake_signals(self)

    def test_payload(self) -> None:
        params = FakeSignals().defaults()
        job = make_job({"kind": FakeSignals.id, "params": params}, "yahoo")[0]
        rows = [symbol_row("y", "EURUSD", [Signal(0, "long", 0)], 2, 0), symbol_row("y", "GBPUSD", [], 2, 0)]
        data = overview_payload(job, {"rows": rows, "scanned_at": 1})
        self.assertEqual(data["title"], "Signals (1h)")
        self.assertEqual(data["rows"], [{"symbol": "EURUSD", "text": "LONG(0)", "kind": "long"}])
        # Bez skrytí prefixu se ukáže i zdroj; bez výsledku poznámka
        full = make_job({"kind": FakeSignals.id, "params": {**params, "scan_strip_prefix": False}}, "yahoo")[0]
        self.assertEqual(overview_payload(full, {"rows": rows, "scanned_at": 1})["rows"][0]["symbol"], "y:EURUSD")
        self.assertEqual(overview_payload(job, None)["note"], "počítám…")
        self.assertEqual(overview_payload(job, None, (12, 37))["note"], "počítám… 12/37")
        # Chybné symboly se při „jen se signálem“ sloučí do jednoho řádku
        broken = [error_row("ibkr", "EUR.USD", "TWS neběží"), error_row("ibkr", "GBP.USD", "TWS neběží")]
        summary = overview_payload(job, {"rows": rows + broken, "scanned_at": 1})["rows"]
        self.assertEqual(summary[-1], {"symbol": "2 symboly bez dat", "text": "chyba", "kind": "error"})
        off = {**job, "config": {**job["config"], "position": "off"}}
        self.assertIsNone(overview_payload(off, None))


class IndicatorAlertEngineTest(unittest.IsolatedAsyncioTestCase):
    """AlertEngine spustí alert na signál indikátoru nad živou svíčkou."""

    async def test_fires_with_signal_text(self) -> None:
        register_fake_signals(self)
        provider = FakeProvider()
        provider.name = "Fake"
        provider.symbol_kind = lambda symbol: ""
        provider.exchange_timezone = lambda symbol: "UTC"
        provider.price_tick = lambda symbol, price: None
        with tempfile.TemporaryDirectory() as tmp:
            cache = KlineCache(Path(tmp) / "test.db")
            await cache.init()
            try:
                from tickview.logic.alerts import normalize_alert

                engine = AlertEngine(cache, provider_lookup=lambda source: provider)
                spec = normalize_alert({
                    "source": "fake", "symbol": "HIT", "timeframe": "1h", "condition": "signal",
                    "trigger": "per_bar", "target": {"type": "indicator", "kind": FakeSignals.id},
                    "notify": {"telegram": False, "toast": True},
                })
                await cache.save_alert(spec)
                events: list[dict] = []
                engine.add_listener(events.append)
                # Bez spuštění streamů (watchery) – svíčky předáme ručně
                engine._sync_watchers = lambda: None
                await engine.reload()
                step = TIMEFRAME_MS["1h"]
                now = utc_now_ms()
                await engine._on_candle(("fake", "HIT", "1h"), Candle(now - now % step, 1, 2, 0.5, 1.5, 1))
                fired = [event for event in events if event["type"] == "fired"]
                self.assertEqual(len(fired), 1)
                self.assertEqual(fired[0]["message"], "HIT, 1h Signals LONG P1")
                # Alert scanneru nemá stream ani tracker; spouští ho scanner zprávou
                call = normalize_alert({
                    "source": "fake", "symbol": "HIT", "timeframe": "1h", "condition": "alert_call",
                    "trigger": "alert_call", "target": {"type": "indicator", "kind": FakeSignals.id},
                    "message": "Scan: {{message}}", "notify": {"telegram": False, "toast": True},
                })
                call_id = await cache.save_alert(call)
                await engine.reload()
                self.assertNotIn(call_id, engine._trackers)
                self.assertNotIn(call_id, engine._alerts)
                [call_alert] = engine.alert_call_alerts(FakeSignals.id)
                await engine.fire_message(call_alert, "2 trhy", symbol="Scanner Signals", timeframe="1h")
                fired = [event for event in events if event["type"] == "fired"]
                self.assertEqual((fired[-1]["message"], fired[-1]["symbol"], fired[-1]["price"]),
                                 ("Scan: 2 trhy", "Scanner Signals", None))
                self.assertEqual(engine.alert_call_alerts(FakeSignals.id)[0]["id"], call_id)
            finally:
                await asyncio.sleep(0)
                await cache.close()
