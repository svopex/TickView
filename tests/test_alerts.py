"""Testy alertů, notifikací a pozic Long/Short (spuštění: python -m unittest)."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from tickview.logic.alerts import (
    AlertTracker,
    condition_met,
    default_message,
    is_expired,
    line_price,
    normalize_alert,
    render_message,
    webhook_payload,
)
from tickview.logic.cache import KlineCache
from tickview.logic.styles import normalize_drawing_style, normalize_position_points
from tickview.providers.base import Candle, StreamStatus, utc_now_ms
from tickview.services.alerts import AlertEngine
from tickview.services.notify import find_telegram_chat_id, send_telegram, send_webhook


def candle(open_time: int, close: float) -> Candle:
    """Svíčka pro testy s daným časem otevření (ms) a závěrem."""
    return Candle(open_time, close, close, close, close, 1.0)


def alert_spec(**overrides) -> dict:
    """Platná definice alertu z dialogu s možností přepsat jednotlivé hodnoty."""
    data = {
        "source": "fake",
        "symbol": "X",
        "timeframe": "1m",
        "condition": "crossing",
        "target": {"type": "value", "price": 10.0},
        "trigger": "once",
        "expires_at": None,
        "message": "",
        "notify": {"telegram": False, "toast": True, "sound": False, "webhook": ""},
    }
    data.update(overrides)
    return normalize_alert(data)


class AlertLogicTest(unittest.TestCase):
    """Podmínky, ceny čar, triggery a texty alertů."""

    def test_conditions(self) -> None:
        self.assertTrue(condition_met("crossing", 9, 10, 10))
        self.assertTrue(condition_met("crossing", 11, 9, 10))
        self.assertFalse(condition_met("crossing", None, 11, 10))
        self.assertTrue(condition_met("crossing_up", 9, 11, 10))
        self.assertFalse(condition_met("crossing_up", 11, 9, 10))
        self.assertTrue(condition_met("crossing_down", 11, 9, 10))
        self.assertTrue(condition_met("greater", None, 11, 10))
        self.assertFalse(condition_met("less", None, 11, 10))
        # Pohyblivá úroveň: cena stojí, čára ji překříží zespodu
        self.assertTrue(condition_met("crossing_down", 10, 10, 10.5, previous_level=9.5))

    def test_line_price(self) -> None:
        self.assertEqual(line_price("hray", [{"time": 0, "price": 5}], 100), 5.0)
        trend = [{"time": 0, "price": 10}, {"time": 100, "price": 20}]
        self.assertEqual(line_price("trend", trend, 50), 15.0)
        # Za druhým bodem se čára prodlužuje
        self.assertEqual(line_price("ray", trend, 200), 30.0)
        self.assertIsNone(line_price("rect", trend, 50))
        self.assertIsNone(line_price("trend", [{"time": 5, "price": 1}, {"time": 5, "price": 2}], 5))

    def test_tracker_once_per_bar(self) -> None:
        tracker = AlertTracker("crossing", "per_bar", 60_000)
        level = lambda _t: 10.0  # noqa: E731
        self.assertIsNone(tracker.update(candle(0, 9), 1_000, level))
        self.assertEqual(tracker.update(candle(0, 11), 2_000, level), 11)
        # Znovu překříženo ve stejném baru – nespustí se, v dalším baru ano
        self.assertIsNone(tracker.update(candle(0, 9), 3_000, level))
        self.assertIsNone(tracker.update(candle(0, 11), 4_000, level))
        # Pokles pod úroveň v novém baru je také překřížení – spustí se jednou
        self.assertEqual(tracker.update(candle(60_000, 9), 61_000, level), 9)
        self.assertIsNone(tracker.update(candle(60_000, 11), 62_000, level))

    def test_tracker_per_minute_and_bar_close(self) -> None:
        tracker = AlertTracker("greater", "per_minute", 60_000)
        level = lambda _t: 10.0  # noqa: E731
        self.assertEqual(tracker.update(candle(0, 11), 0, level), 11)
        self.assertIsNone(tracker.update(candle(0, 12), 30_000, level))
        self.assertEqual(tracker.update(candle(0, 12), 61_000, level), 12)
        # Once per bar close: rozhoduje závěr uzavřeného baru, ne průběh
        closing = AlertTracker("crossing_up", "per_bar_close", 60_000)
        self.assertIsNone(closing.update(candle(0, 9), 0, level))
        self.assertIsNone(closing.update(candle(60_000, 9), 0, level))
        self.assertIsNone(closing.update(candle(60_000, 12), 0, level))
        self.assertIsNone(closing.update(candle(60_000, 9.5), 0, level))
        self.assertIsNone(closing.update(candle(120_000, 11), 0, level))
        self.assertEqual(closing.update(candle(180_000, 11), 0, level), 11)

    def test_normalize_and_messages(self) -> None:
        spec = alert_spec(target={"type": "drawing", "id": 7, "kind": "hray"})
        self.assertEqual(spec["message"], "X, 1m Crossing horizontal ray")
        self.assertEqual(default_message("EUR.USD", "5m", "greater", {"type": "value", "price": 1.10250}), "EUR.USD, 5m Greater Than 1.1025")
        for invalid in (
            {"condition": "nope"},
            {"trigger": "always"},
            {"target": {"type": "value", "price": "x"}},
            {"target": {"type": "drawing", "id": 1, "kind": "rect"}},
            {"timeframe": "3m"},
            {"notify": {"webhook": "ftp://x"}},
            {"message": "x" * 1001},
        ):
            with self.assertRaises(ValueError, msg=invalid):
                alert_spec(**invalid)
        self.assertTrue(is_expired({"expires_at": 10}, 10))
        self.assertFalse(is_expired({"expires_at": None}, 10))
        self.assertEqual(render_message("{{ticker}} @ {{ close }} {{nope}}", {"ticker": "X", "close": "1.5"}), "X @ 1.5 {{nope}}")
        self.assertEqual(webhook_payload('{"a": 1}')[1], "application/json; charset=utf-8")
        self.assertEqual(webhook_payload("ahoj")[1], "text/plain; charset=utf-8")


class PositionStyleTest(unittest.TestCase):
    """Parametry a ceny pozice Long / Short."""

    def test_position_options(self) -> None:
        style = normalize_drawing_style({"options": {"accountSize": "5000", "risk": 250, "riskUnit": "percent", "lotSize": -1}}, "long")
        self.assertEqual(style["options"]["accountSize"], 5000.0)
        # Riziko v procentech nejvýš 100 %, neplatný lot = výchozí
        self.assertEqual(style["options"]["risk"], 100.0)
        self.assertEqual(style["options"]["lotSize"], 1.0)
        self.assertNotIn("accountSize", normalize_drawing_style({}, "trend")["options"])

    def test_position_points(self) -> None:
        points = [{"time": 1, "price": 10}, {"time": 5, "price": 12}, {"time": 5, "price": 8}]
        result = normalize_position_points("long", 10, 13, 9, points)
        self.assertEqual([p["price"] for p in result], [10.0, 13.0, 9.0])
        self.assertEqual([p["time"] for p in result], [1, 5, 5])
        with self.assertRaises(ValueError):
            normalize_position_points("long", 10, 9, 13, points)
        with self.assertRaises(ValueError):
            normalize_position_points("short", 10, 13, 9, points)


class NotifyTest(unittest.IsolatedAsyncioTestCase):
    """Telegram a webhook přes falešný HTTP transport (bez sítě)."""

    async def test_telegram_and_webhook(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": [{"message": {"chat": {"id": 42}}}]})
            if "bad" in str(request.url):
                return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await send_telegram(client, "TOKEN", "42", "zpráva")
            self.assertEqual(json.loads(requests[-1].content)["chat_id"], "42")
            self.assertEqual(await find_telegram_chat_id(client, "TOKEN"), "42")
            with self.assertRaisesRegex(RuntimeError, "Unauthorized"):
                await send_telegram(client, "bad", "42", "x")
            await send_webhook(client, "https://example.test/hook", '{"side": "buy"}')
            self.assertEqual(requests[-1].headers["content-type"], "application/json; charset=utf-8")


class FakeStreamProvider:
    """Testovací zdroj s živým streamem z fronty svíček."""

    id = "fake"
    name = "Fake"
    supports_stream = True

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    async def stream(self, symbol: str, timeframe: str):
        yield StreamStatus(True, "ok")
        while True:
            yield await self.queue.get()


class AlertEngineTest(unittest.IsolatedAsyncioTestCase):
    """Vyhodnocení alertů nad streamem, uložení stavu a logu."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = KlineCache(Path(self.tmp.name) / "test.db")
        await self.cache.init()
        self.provider = FakeStreamProvider()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
        self.engine = AlertEngine(self.cache, provider_lookup=lambda _id: self.provider, client=self.client)
        self.events: list[dict] = []
        self.engine.add_listener(self.events.append)

    async def asyncTearDown(self) -> None:
        await self.engine.stop()
        await self.cache.close()
        self.tmp.cleanup()

    async def wait_for(self, predicate) -> None:
        # Počká, až engine zpracuje svíčky z fronty (nejvýš ~2 s)
        for _ in range(200):
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("Podmínka se nesplnila")

    async def test_once_alert_fires_and_deactivates(self) -> None:
        notify = {"telegram": False, "toast": True, "sound": False, "webhook": "https://example.test/hook"}
        alert_id = await self.cache.save_alert(alert_spec(message="{{ticker}} {{close}}", notify=notify))
        await self.engine.start()
        now = utc_now_ms()
        for close in (9.0, 9.5, 10.5):
            await self.provider.queue.put(candle(now, close))
        await self.wait_for(lambda: any(e["type"] == "log_changed" for e in self.events))
        fired = [e for e in self.events if e["type"] == "fired"]
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["message"], "X 10.5")
        alert = await self.cache.get_alert(alert_id)
        self.assertFalse(alert["active"])
        self.assertEqual((alert["status"], alert["fire_count"]), ("triggered", 1))
        log = await self.cache.alert_log()
        self.assertEqual(log[0]["delivery"], [{"channel": "webhook", "ok": True, "error": ""}])

    async def test_drawing_alert_and_missing_drawing(self) -> None:
        drawing_id = await self.cache.add_drawing("fake", "X", "1m", "hline", [{"time": 0, "price": 20.0}])
        alert_id = await self.cache.save_alert(
            alert_spec(condition="greater", trigger="per_bar", target={"type": "drawing", "id": drawing_id, "kind": "hline"})
        )
        await self.engine.start()
        now = utc_now_ms()
        await self.provider.queue.put(candle(now, 21.0))
        await self.wait_for(lambda: any(e["type"] == "fired" for e in self.events))
        # Smazaná kresba (UI ohlásí změnu) alert vypne se stavem chyby
        await self.cache.delete_drawing("fake", "X", "1m", drawing_id)
        self.engine.drawing_changed(drawing_id)
        await self.provider.queue.put(candle(now + 60_000, 22.0))
        await self.wait_for(lambda: self.engine._alerts == {})
        self.assertEqual((await self.cache.get_alert(alert_id))["status"], "error")

    async def test_expired_alert_is_deactivated(self) -> None:
        alert_id = await self.cache.save_alert(alert_spec(expires_at=utc_now_ms() - 1))
        await self.engine.start()
        await self.provider.queue.put(candle(utc_now_ms(), 11.0))
        await self.wait_for(lambda: self.engine._alerts == {})
        self.assertEqual((await self.cache.get_alert(alert_id))["status"], "expired")


if __name__ == "__main__":
    unittest.main()
