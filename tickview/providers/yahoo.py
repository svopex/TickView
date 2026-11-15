"""Datový zdroj Yahoo Finance (yfinance): akcie, ETF, indexy, forex.

Historie i vyhledávání se volají v asyncio.to_thread (blokující volání);
živá aktualizace probíhá dotazováním v intervalu (polling).
"""

import asyncio
import time as time_module
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import pandas as pd
import yfinance as yf

from tickview.providers.base import Candle, DataProvider, SymbolInfo

# Yahoo provides quotes only for these types via search
SEARCH_QUOTE_TYPES = {"EQUITY", "ETF", "INDEX", "CURRENCY"}
# Jak často se při pollingem živá data obnovují (sekundy), podle timeframeu
POLL_SECONDS_INTRADAY = 15
POLL_SECONDS_DAILY = 60
# Jak často se smí obnovit cena watchlistu (throttle kvůli limitům Yahoo)
TICKER_TTL_S = 60.0


class YahooProvider(DataProvider):
    """Yahoo Finance – historie přes yfinance, živá data pollingem; data bývají zpožděná."""

    id = "yahoo"
    name = "Yahoo Finance"
    supports_stream = True
    # Zdroj posílá data pollingem a bývají zpožděná → štítek zůstává „zpožděno“
    is_live = False

    def __init__(self) -> None:
        # Yahoo nemá 4h svíčky; mapujeme jen podporované intervaly
        self.timeframes = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "1D": "1d",
            "1W": "1wk",
        }
        # Cache cen pro watchlist (symbol -> (čas, data)) s TTL kvůli limitům
        self._ticker_cache: dict[str, tuple[float, dict]] = {}

    async def tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Vrátí poslední cenu a denní změnu (%) z denních svíček (polling)."""
        result: dict[str, dict] = {}
        now = time_module.monotonic()
        for symbol in symbols:
            cached = self._ticker_cache.get(symbol)
            if cached and now - cached[0] < TICKER_TTL_S:
                result[symbol] = cached[1]
                continue
            try:
                candles = await self._download(
                    symbol, "1D", None, None, period="5d"
                )
                quote: Optional[dict] = None
                if len(candles) >= 2:
                    last, prev = candles[-1].close, candles[-2].close
                    quote = {
                        "price": last,
                        "change_pct": (last / prev - 1.0) * 100.0 if prev else 0.0,
                    }
                elif len(candles) == 1:
                    quote = {"price": candles[-1].close, "change_pct": 0.0}
                if quote is not None:
                    self._ticker_cache[symbol] = (now, quote)
                    result[symbol] = quote
            except Exception:
                # Výpadek zdroje: symbol bez aktualizace, zkusíme příště
                pass
        return result

    async def search(self, query: str, limit: int = 30) -> list[SymbolInfo]:
        """Vyhledá symboly přes yf.Search (blokující volání ve vlákně)."""
        q = query.strip()
        if not q:
            # Prázdný dotaz: pár oblíbených tickers pro rychlý start
            popular = [
                SymbolInfo(self.id, "AAPL", "AAPL – Apple Inc. (NMS)"),
                SymbolInfo(self.id, "MSFT", "MSFT – Microsoft Corporation (NMS)"),
                SymbolInfo(self.id, "^GSPC", "^GSPC – S&P 500 (SPX)"),
                SymbolInfo(self.id, "EURUSD=X", "EURUSD=X – EUR/USD (CCY)"),
            ]
            return popular

        def do_search() -> list[dict]:
            # yfinance spravuje cookies/impersonaci, raw HTTP API vrací 429
            return yf.Search(q, max_results=limit).quotes

        quotes = await asyncio.to_thread(do_search)
        return [
            SymbolInfo(
                provider_id=self.id,
                symbol=item["symbol"],
                label=f"{item['symbol']} – {item.get('shortname') or item.get('longname') or '?'}"
                f" ({item.get('exchange', '?')})",
            )
            for item in quotes
            if item.get("quoteType") in SEARCH_QUOTE_TYPES and item.get("symbol")
        ]

    @staticmethod
    def _dataframe_to_candles(df: pd.DataFrame) -> list[Candle]:
        """Převede výstup yfinance na seznam svíček s časem UTC v ms."""
        if df is None or df.empty:
            return []
        # yfinance vrací MultiIndex sloupce i pro jeden symbol → srovnáme
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        candles: list[Candle] = []
        for timestamp, row in df.iterrows():
            if pd.isna(row.get("Open")) or pd.isna(row.get("Close")):
                continue
            # Index je pro intraday aware (čas burzy), pro denní data naive;
            # sjednotíme na UTC a uložíme v ms
            if getattr(timestamp, "tzinfo", None) is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
            candles.append(
                Candle(
                    open_time=int(timestamp.timestamp() * 1000),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume") or 0),
                )
            )
        return candles

    async def _download(
        self,
        symbol: str,
        timeframe: str,
        start_ms: Optional[int],
        end_ms: Optional[int],
        period: Optional[str] = None,
    ) -> list[Candle]:
        """Zabalí blokující yf.download do vlákna, aby nezdrželo UI smyčku."""
        interval = self.timeframes[timeframe]
        kwargs = {
            "interval": interval,
            "auto_adjust": False,
            "progress": False,
        }
        if period is not None:
            # Polling stahuje jen poslední den/týden, ne celou historii
            kwargs["period"] = period
        if start_ms is not None:
            kwargs["start"] = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        if end_ms is not None:
            kwargs["end"] = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        df = await asyncio.to_thread(yf.download, symbol, **kwargs)
        return self._dataframe_to_candles(df)

    async def history(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Candle]:
        """Vrátí svíčky z Yahoo Finance v daném intervalu (UTC ms)."""
        return await self._download(symbol, timeframe, start_ms, end_ms)

    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        """Živá aktualizace dotazováním: v pravidelných intervalech posílá
        poslední svíčku; novou (uzavřenou i rozepsanou) jen když se změnila.

        Generator běží, dokud ho konzument nezruší (CancelledError).
        """
        step_ms = {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "1h": 3_600_000,
            "1D": 86_400_000,
            "1W": 604_800_000,
        }[timeframe]
        # Denní/weekly data stačí obnovovat řidčeji než intraday
        poll_seconds = (
            POLL_SECONDS_DAILY if timeframe in ("1D", "1W") else POLL_SECONDS_INTRADAY
        )
        last_open_time = -1
        last_close = None
        while True:
            try:
                # Polling stahuje jen poslední den (intraday) resp. týden (denní)
                period = "1d" if timeframe not in ("1D", "1W") else "5d"
                candles = await self._download(symbol, timeframe, None, None, period=period)
                if candles:
                    latest = candles[-1]
                    # Posíláme jen svíčky novější než ta načtená do grafu
                    if (
                        latest.open_time > last_open_time
                        or latest.close != last_close
                    ):
                        last_open_time = latest.open_time
                        last_close = latest.close
                        yield latest
            except asyncio.CancelledError:
                raise
            except Exception:
                # Výpadek zdroje: zkusíme to znovu v dalším kole
                pass
            await asyncio.sleep(poll_seconds)
