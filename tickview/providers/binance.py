"""Datový zdroj Binance (spot): veřejné REST API bez klíče."""

import asyncio
import time as time_module
from typing import Any, Optional

import httpx

from tickview.providers.base import Candle, DataProvider, SymbolInfo, TIMEFRAME_MS

REST_BASE_URL = "https://api.binance.com"
# Seznam symbolů se nemění často; stačí ho načítat jednou za den
EXCHANGE_INFO_TTL_S = 24 * 3600
# Maximální počet svíček na jeden REST dotaz (limit Binance)
KLINES_PAGE_LIMIT = 1000
# Pojistka proti nekonečnému stahování při velkých mezerách v datech
MAX_KLINES_PAGES = 100


class RateLimiter:
    """Omezovač dotazů: minimální mezera mezi požadavky + exponenciální čekání po 429.

    Parametry:
        min_interval: minimální mezera mezi požadavky v sekundách.
        max_backoff: horní mez exponenciálního čekání v sekundách.
    """

    def __init__(self, min_interval: float = 0.15, max_backoff: float = 60.0):
        self._min_interval = min_interval
        self._max_backoff = max_backoff
        self._next_slot = 0.0
        self._backoff = 0.0

    async def acquire(self) -> None:
        """Zablokuje, dokud není povolen další požadavek."""
        now = time_module.monotonic()
        # Čekáme max(minimální slot, aktuální backoff po 429)
        wait_until = max(self._next_slot, now + self._backoff)
        delay = wait_until - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._next_slot = max(time_module.monotonic() + self._min_interval, self._next_slot)

    def penalize(self, retry_after: Optional[float] = None) -> None:
        """Zvyšuje čekání po chybě 429/5xx; hlavička Retry-After má přednost."""
        self._backoff = min(max(self._backoff * 2, 1.0), self._max_backoff)
        if retry_after:
            self._backoff = max(self._backoff, min(retry_after, self._max_backoff))

    def clear_backoff(self) -> None:
        """Po úspěšném dotazu vrátí backoff na nulu."""
        self._backoff = 0.0


class BinanceProvider(DataProvider):
    """Kryptoburza Binance – historie přes veřejné REST, živá data přes websocket (krok 2)."""

    id = "binance"
    name = "Binance"
    supports_stream = True

    def __init__(self) -> None:
        # Mapování kanonických timeframeů na intervaly Binance (bez 4h necháváme, Binance ji umí)
        self.timeframes = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "4h": "4h",
            "1D": "1d",
            "1W": "1w",
        }
        self._client: Optional[httpx.AsyncClient] = None
        self._limiter = RateLimiter(min_interval=0.15)
        self._symbols: list[SymbolInfo] = []
        self._symbols_loaded_at = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        # Lazy klient – vytvoří se až u prvního dotazu v běžící smyčce
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=REST_BASE_URL, timeout=15.0)
        return self._client

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        """REST dotaz s omezovačem, backoffem po 429 a opakováním při 5xx."""
        client = await self._get_client()
        server_errors = 0
        while True:
            await self._limiter.acquire()
            response = await client.request(method, path, params=params)
            if response.status_code == 429:
                # Příliš mnoho dotazů: exponenciální čekání (respektujeme Retry-After) a opakujeme
                retry_after = response.headers.get("Retry-After")
                self._limiter.penalize(float(retry_after) if retry_after else None)
                continue
            if response.status_code >= 500:
                # Dočasná chyba serveru: pár opakování s rostoucím čekáním
                server_errors += 1
                if server_errors > 3:
                    response.raise_for_status()
                self._limiter.penalize()
                continue
            response.raise_for_status()
            self._limiter.clear_backoff()
            return response.json()

    async def _ensure_symbols(self) -> None:
        # Načte seznam aktivních spot symbolů; ukládá se v paměti na 24 h
        now = time_module.monotonic()
        if self._symbols and now - self._symbols_loaded_at < EXCHANGE_INFO_TTL_S:
            return
        data = await self._request("GET", "/api/v3/exchangeInfo")
        # Jen aktivní symboly; burza neposkytuje hezké názvy, stačí base/quote
        self._symbols = [
            SymbolInfo(
                provider_id=self.id,
                symbol=s["symbol"],
                label=f"{s['symbol']} ({s['quoteAsset']})",
            )
            for s in data["symbols"]
            if s.get("status") == "TRADING"
        ]
        self._symbols.sort(key=lambda si: si.symbol)
        self._symbols_loaded_at = now

    async def search(self, query: str, limit: int = 30) -> list[SymbolInfo]:
        """Vyhledá páry podle dotazu; hledá prefix i výskyt uvnitř symbolu."""
        await self._ensure_symbols()
        q = query.strip().upper()
        if not q:
            # Prázdný dotaz: pár oblíbených párů pro rychlý start
            popular = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
            available = {si.symbol: si for si in self._symbols}
            return [available[p] for p in popular if p in available]
        # Symboly začínající dotazem mají přednost před ostatními výskyty;
        # v rámci skupiny dáváme dopředu páry na USDT (nejčastější volba)
        def rank(symbol_info: SymbolInfo) -> tuple[int, str]:
            return (0 if symbol_info.symbol.endswith("USDT") else 1, symbol_info.symbol)

        prefix = sorted((si for si in self._symbols if si.symbol.startswith(q)), key=rank)
        contains = sorted(
            (si for si in self._symbols if q in si.symbol and not si.symbol.startswith(q)), key=rank
        )
        return (prefix + contains)[:limit]

    async def history(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Candle]:
        """Stáhne svíčky po stránkách (max 1000 na dotaz) přes veřejný REST."""
        interval = self.timeframes[timeframe]
        step_ms = TIMEFRAME_MS[timeframe]
        candles: list[Candle] = []
        cursor = start_ms
        pages = 0
        while cursor <= end_ms:
            pages += 1
            if pages > MAX_KLINES_PAGES:
                # Pojistka: moc velký rozsah po ukončíme, co se stáhlo vrátíme
                break
            data = await self._request(
                "GET",
                "/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": KLINES_PAGE_LIMIT,
                },
            )
            if not data:
                break
            for row in data:
                candles.append(
                    Candle(
                        open_time=int(row[0]),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                )
            last_open = int(data[-1][0])
            # API vrátilo méně než limit → další starší data nejsou
            if len(data) < KLINES_PAGE_LIMIT:
                break
            cursor = last_open + step_ms
        return candles
