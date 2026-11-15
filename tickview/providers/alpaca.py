"""Datový zdroj Alpaca (americké akcie/ETF): REST data API + websocket IEX.

Zdroj je volitelný: aktivuje se jen když jsou v data/config.local.json
uložené klíče (api_key + secret). Aplikace je read-only, obchodní API
(paper-api) se používá jen pro seznam instrumentů.
"""

import asyncio
import json
import time as time_module
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx
import websockets

from tickview.config import load_config
from tickview.providers.base import (
    Candle,
    DataProvider,
    RateLimiter,
    SymbolInfo,
    TIMEFRAME_MS,
)

DATA_BASE_URL = "https://data.alpaca.markets"
WS_BASE_URL = "wss://stream.data.alpaca.markets/v2/iex"
# Seznam instrumentů se mění zřídka – v paměti na 24 h
ASSETS_TTL_S = 24 * 3600
# Stránkování historie (limit Alpaca na stránku)
BARS_PAGE_LIMIT = 1000
# Horní mez čekání mezi pokusy o znovupřipojení websocketu
WS_MAX_BACKOFF_S = 30.0


class AlpacaProvider(DataProvider):
    """Alpaca – americké akcie a ETF; živá data přes IEX websocket (zdarma)."""

    id = "alpaca"
    name = "Alpaca"
    supports_stream = True
    # IEX feed je v reálném čase (bez 15min zpoždění jako u některých zdrojů)
    is_live = True

    def __init__(self) -> None:
        # Alpaca umí i 4h svíčky (na rozdíl od Yahoo)
        self.timeframes = {
            "1m": "1Min",
            "5m": "5Min",
            "15m": "15Min",
            "1h": "1Hour",
            "4h": "4Hour",
            "1D": "1Day",
            "1W": "1Week",
        }
        config = load_config().get("alpaca", {})
        self._key = config.get("api_key") or ""
        self._secret = config.get("secret") or ""
        # Trading API endpoint z konfigurace (paper-api pro papírový účet)
        self._endpoint = (config.get("endpoint") or "https://paper-api.alpaca.markets/v2").rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None
        self._limiter = RateLimiter(min_interval=0.2)
        self._assets: list[SymbolInfo] = []
        self._assets_loaded_at = 0.0

    @property
    def configured(self) -> bool:
        """Zdroj je použitelný jen s oběma klíči."""
        return bool(self._key and self._secret)

    def _headers(self) -> dict[str, str]:
        # Alpaca vyžaduje hlavičku APCA-API-SECRET-KEY (s „-KEY“ na konci);
        # s hlavičkou APCA-API-SECRET dle novějších docs vrací 401 unauthorized
        return {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
        }

    async def _get_client(self) -> httpx.AsyncClient:
        # Lazy klient – vytvoří se až u prvního dotazu v běžící smyčce
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def _request(
        self, url: str, params: Optional[dict[str, Any]] = None
    ) -> Any:
        """GET dotaz s klíči, omezovačem a backoffem po 429."""
        client = await self._get_client()
        server_errors = 0
        while True:
            await self._limiter.acquire()
            response = await client.get(url, params=params, headers=self._headers())
            if response.status_code == 429:
                # Příliš mnoho dotazů: exponenciální čekání a opakování
                retry_after = response.headers.get("Retry-After")
                self._limiter.penalize(float(retry_after) if retry_after else None)
                continue
            if response.status_code >= 500:
                server_errors += 1
                if server_errors > 3:
                    response.raise_for_status()
                self._limiter.penalize()
                continue
            response.raise_for_status()
            self._limiter.clear_backoff()
            return response.json()

    async def _ensure_assets(self) -> None:
        # Načte aktivní americké akcie/ETF; v paměti na 24 h
        now = time_module.monotonic()
        if self._assets and now - self._assets_loaded_at < ASSETS_TTL_S:
            return
        data = await self._request(
            f"{self._endpoint}/assets",
            params={"status": "active", "asset_class": "us_equity"},
        )
        # Popisek: symbol – název (burza); třídíme podle symbolu
        self._assets = sorted(
            (
                SymbolInfo(
                    provider_id=self.id,
                    symbol=item["symbol"],
                    label=f"{item['symbol']} – {item.get('name') or '?'} ({item.get('exchange', '?')})",
                )
                for item in data
                if item.get("tradable", True) and item.get("symbol")
            ),
            key=lambda info: info.symbol,
        )
        self._assets_loaded_at = now

    async def search(self, query: str, limit: int = 30) -> list[SymbolInfo]:
        """Vyhledá akcie/ETF podle dotazu v seznamu instrumentů Alpaca."""
        q = query.strip().upper()
        if not q:
            # Prázdný dotaz: oblíbené tickery pro rychlý start
            await self._ensure_assets()
            popular = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "SPY"]
            available = {info.symbol: info for info in self._assets}
            return [available[symbol] for symbol in popular if symbol in available]
        await self._ensure_assets()
        prefix = [info for info in self._assets if info.symbol.startswith(q)]
        contains = [
            info for info in self._assets if q in info.symbol and not info.symbol.startswith(q)
        ]
        return (prefix + contains)[:limit]

    @staticmethod
    def _iso(timestamp_ms: int) -> str:
        # Alpaca očekává RFC3339 v UTC (např. 2026-11-01T00:00:00Z)
        return (
            datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _parse_time(value: str) -> int:
        # Čas baru z Alpaca (RFC3339) na UTC milisekundy
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1000)

    async def tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Vrátí poslední cenu a denní změnu (%) ze snapshots (feed IEX)."""
        if not symbols:
            return {}
        data = await self._request(
            f"{DATA_BASE_URL}/v2/stocks/snapshots",
            params={"symbols": ",".join(symbols), "feed": "iex"},
        )
        result: dict[str, dict] = {}
        for symbol, snapshot in (data or {}).items():
            latest_trade = snapshot.get("latestTrade") or {}
            daily_bar = snapshot.get("dailyBar") or {}
            previous_bar = snapshot.get("prevDailyBar") or {}
            price = latest_trade.get("p") or daily_bar.get("c")
            previous_close = previous_bar.get("c")
            if price is None:
                continue
            # Denní změna proti předchozímu uzavření
            change_pct = ((price / previous_close) - 1.0) * 100.0 if previous_close else 0.0
            result[symbol] = {"price": float(price), "change_pct": change_pct}
        return result

    async def history(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Candle]:
        """Stáhne historii svíček po stránkách (feed IEX pro bezplatný účet)."""
        candles: list[Candle] = []
        page_token: Optional[str] = None
        for _ in range(200):
            params = {
                "symbols": symbol,
                "timeframe": self.timeframes[timeframe],
                "start": self._iso(start_ms),
                "end": self._iso(end_ms),
                "limit": BARS_PAGE_LIMIT,
                "adjustment": "raw",
                # Bez feed=iex vrací bezplatný účet 403 (SIP je placený)
                "feed": "iex",
            }
            if page_token:
                params["page_token"] = page_token
            data = await self._request(f"{DATA_BASE_URL}/v2/stocks/bars", params=params)
            for bar in (data.get("bars") or {}).get(symbol, []):
                candles.append(
                    Candle(
                        open_time=self._parse_time(bar["t"]),
                        open=float(bar["o"]),
                        high=float(bar["h"]),
                        low=float(bar["l"]),
                        close=float(bar["c"]),
                        volume=float(bar["v"]),
                    )
                )
            page_token = data.get("next_page_token")
            if not page_token:
                break
        return candles

    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        """Živé svíčky přes websocket IEX s automatickým znovupřipojením.

        Generátor běží, dokud ho konzument nezruší (CancelledError).
        """
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(WS_BASE_URL) as ws:
                    # Autentizace a odběr baru pro symbol
                    await ws.send(
                        json.dumps({"action": "auth", "key": self._key, "secret": self._secret})
                    )
                    await ws.send(json.dumps({"action": "subscribe", "bars": [symbol]}))
                    backoff = 1.0
                    async for raw in ws:
                        for message in json.loads(raw):
                            kind = message.get("T")
                            if kind == "error":
                                # Např. neplatné klíče; pošleme výš pro zobrazení v UI
                                raise RuntimeError(message.get("msg", "chyba websocketu Alpaca"))
                            if kind != "b":
                                # success/subscription/... ignorujeme
                                continue
                            close_time = self._parse_time(message["t"])
                            # Bar patří k symbolu, na který jsme se přihlásili
                            if message.get("S") != symbol:
                                continue
                            yield Candle(
                                open_time=close_time,
                                open=float(message["o"]),
                                high=float(message["h"]),
                                low=float(message["l"]),
                                close=float(message["c"]),
                                volume=float(message["v"]),
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Výpadek spojení nebo chyba autentizace: čekáme a zkoušíme znovu
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, WS_MAX_BACKOFF_S)
            else:
                await asyncio.sleep(0.5)
