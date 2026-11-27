"""Testy klienta cTrader Open API s falešným serverem (spuštění: python -m unittest)."""

import asyncio
import json
import unittest
from unittest import mock

from tickview.providers import ctrader
from tickview.providers.base import Candle
from tickview.providers.ctrader import (
    CTraderClient,
    CTraderError,
    CTraderProvider,
    complete_live_bar,
    decode_trendbar,
    guess_kind,
    symbol_key,
)

MINUTE = 60_000


class FakeServer:
    """Falešný WebSocket server cTrader: odpovídá na požadavky podle payloadType.

    Parametry:
        token_valid: False = první přihlášení účtu skončí chybou prošlého tokenu.
        bars: trendbary symbolu (seřazené), stránkují se po page_size od toToTimestamp zpět.
    """

    def __init__(self, token_valid: bool = True, bars: list[dict] | None = None, page_size: int = 3,
                 live_event: dict | None = None) -> None:
        self.token_valid = token_valid
        # Spot událost, kterou server pošle hned po přihlášení živého baru (jako skutečný server)
        self.live_event = live_event
        self.bars = bars or []
        self.page_size = page_size
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue = asyncio.Queue()

    async def send(self, raw: str) -> None:
        # Požadavek klienta: zapamatovat a připravit odpověď
        message = json.loads(raw)
        self.sent.append(message)
        reply = self.reply(message["payloadType"], message.get("payload") or {})
        if reply is not None:
            payload_type, payload = reply
            await self.incoming.put(json.dumps(
                {"clientMsgId": message.get("clientMsgId"), "payloadType": payload_type, "payload": payload}
            ))
        if self.live_event and message["payloadType"] == ctrader.SUBSCRIBE_LIVE_TRENDBAR_REQ:
            await self.incoming.put(json.dumps({"payloadType": ctrader.SPOT_EVENT, "payload": self.live_event}))

    def reply(self, payload_type: int, payload: dict):
        # Odpovědi podle typu požadavku (heartbeat bez odpovědi)
        if payload_type == ctrader.HEARTBEAT_EVENT:
            return None
        if payload_type == ctrader.GET_ACCOUNTS_BY_TOKEN_REQ:
            if not self.token_valid and payload["accessToken"] == "old":
                return ctrader.OA_ERROR_RES, {"errorCode": "CH_ACCESS_TOKEN_INVALID"}
            return payload_type + 1, {"ctidTraderAccount": [
                {"ctidTraderAccountId": 7, "isLive": True}, {"ctidTraderAccountId": 9, "brokerTitleShort": "Demo FX"},
            ]}
        if payload_type == ctrader.SYMBOLS_LIST_REQ:
            return payload_type + 1, {"symbol": [
                {"symbolId": 1, "symbolName": "EUR/USD", "description": "Euro vs Dollar"},
                {"symbolId": 2, "symbolName": "US500", "enabled": False},
            ]}
        if payload_type == ctrader.SYMBOL_BY_ID_REQ:
            return payload_type + 1, {"symbol": [{"symbolId": 1, "digits": 5}]}
        if payload_type == ctrader.GET_TRENDBARS_REQ:
            # Bary v rozsahu, odpověď nejvýš page_size nejnovějších před toTimestamp
            inside = [
                bar for bar in self.bars
                if payload["fromTimestamp"] <= bar["utcTimestampInMinutes"] * MINUTE <= payload["toTimestamp"]
            ]
            page = inside[-self.page_size:]
            return payload_type + 1, {"trendbar": page, "hasMore": len(inside) > len(page)}
        if payload_type == ctrader.SUBSCRIBE_LIVE_TRENDBAR_REQ:
            return 2165, {}
        return payload_type + 1, {}

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return await self.incoming.get()

    async def close(self) -> None:
        pass


def _client(**config) -> CTraderClient:
    # Klient s vyplněnými údaji (bez zápisu tokenů do config.yaml)
    base = {"client_id": "id", "client_secret": "secret", "access_token": "tok", "refresh_token": "ref"}
    return CTraderClient({**base, **config}, persist_tokens=False)


def _run(coro):
    return asyncio.run(coro)


class HelpersTest(unittest.TestCase):
    """Dekódování trendbaru a názvy symbolů."""

    def test_decode(self) -> None:
        # Ceny = (low + delta) / 100000, chybějící delta = minimum, čas v minutách
        candle = decode_trendbar({"low": 108000, "deltaHigh": 50, "deltaClose": 10, "utcTimestampInMinutes": 3,
                                  "volume": 12}, 5)
        self.assertEqual((candle.open_time, candle.open, candle.high, candle.low, candle.close, candle.volume),
                         (3 * MINUTE, 1.08, 1.0805, 1.08, 1.0801, 12.0))

    def test_complete_live_bar(self) -> None:
        # Živý bar bez deltaClose dostane close = bid; bid pod minimem posune low i delty
        bar = {"low": 100000, "deltaOpen": 30, "deltaHigh": 50, "utcTimestampInMinutes": 3}
        inside = decode_trendbar(complete_live_bar(bar, 100020), 5)
        self.assertEqual((inside.open, inside.high, inside.low, inside.close), (1.0003, 1.0005, 1.0, 1.0002))
        below = decode_trendbar(complete_live_bar(bar, 99990), 5)
        self.assertEqual((below.open, below.high, below.low, below.close), (1.0003, 1.0005, 0.9999, 0.9999))
        # Bez bidu nebo s deltaClose se bar nemění
        self.assertIs(complete_live_bar(bar, None), bar)
        closed = {**bar, "deltaClose": 10}
        self.assertIs(complete_live_bar(closed, 100020), closed)

    def test_names(self) -> None:
        self.assertEqual(symbol_key("EUR/USD"), "EURUSD")
        self.assertEqual(guess_kind("EURUSD"), "forex")
        self.assertEqual(guess_kind("XAUUSD"), "commodity")
        self.assertEqual(guess_kind("US500"), "index")


class ClientTest(unittest.TestCase):
    """Přihlášení, symboly, historie a živé bary přes falešný server."""

    def _connect(self, server: FakeServer, client: CTraderClient):
        # websockets.connect vrátí falešný server
        return mock.patch.object(ctrader.websockets, "connect", mock.AsyncMock(return_value=server))

    def test_auth_and_symbols(self) -> None:
        # Bez account_id se vybere první demo účet; vypnutý symbol se vynechá
        async def scenario():
            server = FakeServer()
            client = _client()
            with self._connect(server, client):
                await client.ensure_connected()
                symbols = await client.symbols()
            await client.close()
            return server, client, symbols

        server, client, symbols = _run(scenario())
        self.assertEqual(client.account_id, 9)
        self.assertEqual(client.broker, "Demo FX")
        self.assertEqual(list(symbols), ["EURUSD"])
        types = [message["payloadType"] for message in server.sent]
        self.assertEqual(types[:3], [ctrader.APPLICATION_AUTH_REQ, ctrader.GET_ACCOUNTS_BY_TOKEN_REQ,
                                     ctrader.ACCOUNT_AUTH_REQ])

    def test_token_refresh(self) -> None:
        # Prošlý token: obnova přes HTTP a druhý pokus o přihlášení s novým tokenem
        async def scenario():
            server = FakeServer(token_valid=False)
            client = _client(access_token="old")
            refresh = mock.AsyncMock()

            async def fake_refresh():
                client._access_token = "new"
                await refresh()

            with self._connect(server, client), mock.patch.object(client, "_refresh_access_token", fake_refresh):
                await client.ensure_connected()
            await client.close()
            return client, refresh

        client, refresh = _run(scenario())
        refresh.assert_awaited_once()
        self.assertEqual(client.account_id, 9)

    def test_trendbars_paging(self) -> None:
        # 8 minutových barů se stáhne po stránkách 3 od konce intervalu
        bars = [{"utcTimestampInMinutes": minute, "low": 100000, "deltaClose": minute, "volume": minute}
                for minute in range(10, 18)]

        async def scenario():
            server = FakeServer(bars=bars)
            client = _client()
            with self._connect(server, client):
                candles = await client.trendbars("EURUSD", 1, 10 * MINUTE, 17 * MINUTE)
            await client.close()
            return server, candles

        server, candles = _run(scenario())
        self.assertEqual([candle.volume for candle in candles], [float(m) for m in range(10, 18)])
        requests = [m for m in server.sent if m["payloadType"] == ctrader.GET_TRENDBARS_REQ]
        self.assertEqual(len(requests), 3)

    def test_unknown_symbol(self) -> None:
        async def scenario():
            server = FakeServer()
            client = _client()
            with self._connect(server, client):
                with self.assertRaises(ValueError):
                    await client.symbol_id("GBPUSD")
            await client.close()

        _run(scenario())

    def test_live_bars(self) -> None:
        # Spot událost se živým barem dojde odběrateli symbolu a periody
        async def scenario():
            server = FakeServer()
            client = _client()
            with self._connect(server, client):
                key, queue, digits = await client.subscribe("EURUSD", 1)
                await server.incoming.put(json.dumps({"payloadType": ctrader.SPOT_EVENT, "payload": {
                    "symbolId": 1, "bid": 100007,
                    "trendbar": [{"utcTimestampInMinutes": 5, "low": 100000, "deltaHigh": 9, "volume": 4}],
                }}))
                bar = await asyncio.wait_for(queue.get(), 1)
                await client.unsubscribe(key, queue)
            await client.close()
            return server, bar, digits

        server, bar, digits = _run(scenario())
        candle = decode_trendbar(bar, digits)
        self.assertEqual((candle.volume, candle.close), (4.0, 1.00007))
        types = [message["payloadType"] for message in server.sent]
        self.assertIn(ctrader.SUBSCRIBE_SPOTS_REQ, types)
        self.assertIn(ctrader.UNSUBSCRIBE_SPOTS_REQ, types)

    def test_live_bar_held(self) -> None:
        # Rozpracovaný bar: první dotaz přihlásí odběr, další ho použije; po vypršení se odhlásí
        event = {"symbolId": 1, "bid": 100005, "trendbar": [
            {"period": 9, "utcTimestampInMinutes": 60, "low": 100000, "deltaOpen": 2, "deltaHigh": 8, "volume": 3},
        ]}

        async def scenario():
            server = FakeServer(live_event=event)
            client = _client()
            with self._connect(server, client):
                first = await client.live_bar("EURUSD", 9)
                second = await client.live_bar("EURUSD", 9)
                subscribes = sum(m["payloadType"] == ctrader.SUBSCRIBE_LIVE_TRENDBAR_REQ for m in server.sent)
                client._held[(1, 9)].expires = 0.0
                await client._release_held(1.0)
            await client.close()
            return server, first, second, subscribes, client

        server, first, second, subscribes, client = _run(scenario())
        self.assertEqual((first.open_time, first.open, first.close), (60 * MINUTE, 1.00002, 1.00005))
        self.assertEqual(second, first)
        self.assertEqual(subscribes, 1)
        self.assertIn(ctrader.UNSUBSCRIBE_LIVE_TRENDBAR_REQ, [m["payloadType"] for m in server.sent])
        self.assertEqual(client._subscribers, {})

    def test_error_response(self) -> None:
        # Chybová odpověď serveru = CTraderError s kódem
        async def scenario():
            server = FakeServer()
            server.reply = lambda payload_type, payload: (ctrader.OA_ERROR_RES, {"errorCode": "CH_CLIENT_AUTH_FAILURE"})
            client = _client()
            with self._connect(server, client):
                with self.assertRaises(CTraderError):
                    await client.ensure_connected()
            await client.close()

        _run(scenario())


class ProviderHistoryTest(unittest.TestCase):
    """Historie zdroje doplněná o rozpracovanou svíčku ze živého baru."""

    HOUR = 60 * MINUTE

    def _provider(self, closed: list[Candle], live: Candle | None) -> CTraderProvider:
        # Zdroj bez načítání config.yaml s falešným klientem
        provider = CTraderProvider.__new__(CTraderProvider)
        provider.client = mock.Mock()
        provider.client.trendbars = mock.AsyncMock(return_value=closed)
        provider.client.live_bar = mock.AsyncMock(return_value=live)
        return provider

    def _candle(self, open_time: int) -> Candle:
        return Candle(open_time, 1.0, 1.1, 0.9, 1.05, 10.0)

    def test_appends_running_bar(self) -> None:
        # Interval do současnosti: k uzavřeným svíčkám přibude rozpracovaná
        now = 10 * self.HOUR + 8 * MINUTE
        closed = [self._candle(8 * self.HOUR), self._candle(9 * self.HOUR)]
        provider = self._provider(closed, self._candle(10 * self.HOUR))
        with mock.patch.object(ctrader, "utc_now_ms", return_value=now):
            candles = _run(provider.history("GBPUSD", "1h", 0, now))
        self.assertEqual([c.open_time for c in candles], [8 * self.HOUR, 9 * self.HOUR, 10 * self.HOUR])

    def test_skips_stale_or_past(self) -> None:
        # Živý bar uzavřeného období se nepřidá; starší interval se na živý bar neptá
        now = 10 * self.HOUR + 8 * MINUTE
        closed = [self._candle(9 * self.HOUR)]
        provider = self._provider(list(closed), self._candle(9 * self.HOUR))
        with mock.patch.object(ctrader, "utc_now_ms", return_value=now):
            self.assertEqual(len(_run(provider.history("GBPUSD", "1h", 0, now))), 1)
            _run(provider.history("GBPUSD", "1h", 0, 5 * self.HOUR))
        self.assertEqual(provider.client.live_bar.await_count, 1)


if __name__ == "__main__":
    unittest.main()
