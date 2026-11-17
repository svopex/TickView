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

from tickview.providers.base import (
    Candle,
    DataProvider,
    StreamItem,
    SymbolInfo,
    clean_float32_price,
    poll_latest_candles,
    poll_seconds_for,
)

# Typy instrumentů z vyhledávání Yahoo, které umíme zobrazit (quoteType) → druh instrumentu pro kategorie vyhledávání
# Typy výsledků hledání Yahoo, které TickView nabízí (typ -> druh instrumentu);
# futures (GC=F, CL=F…) a krypto (BTC-USD) jdou načíst stejně jako akcie
QUOTE_TYPE_KINDS = {
    "EQUITY": "stock",
    "ETF": "fund",
    "MUTUALFUND": "fund",
    "INDEX": "index",
    "CURRENCY": "forex",
    "FUTURE": "futures",
    "CRYPTOCURRENCY": "crypto",
}
# Časová pásma burz podle přípony symbolu Yahoo (bez přípony = USA)
SUFFIX_TIMEZONES = {
    "=X": "Europe/London",
    "-USD": "UTC",
    "=F": "America/New_York",
    ".L": "Europe/London",
    ".DE": "Europe/Berlin",
    ".F": "Europe/Berlin",
    ".PR": "Europe/Prague",
    ".PA": "Europe/Paris",
    ".AS": "Europe/Amsterdam",
    ".SW": "Europe/Zurich",
    ".MI": "Europe/Rome",
    ".MC": "Europe/Madrid",
    ".VI": "Europe/Vienna",
    ".WA": "Europe/Warsaw",
    ".T": "Asia/Tokyo",
    ".HK": "Asia/Hong_Kong",
    ".TO": "America/Toronto",
    ".AX": "Australia/Sydney",
}

# Jak často se smí obnovit cena watchlistu (throttle kvůli limitům Yahoo)
TICKER_TTL_S = 60.0


class YahooProvider(DataProvider):
    """Yahoo Finance – historie přes yfinance, živá data pollingem; data bývají zpožděná."""

    id = "yahoo"
    name = "Yahoo Finance"
    supports_stream = True
    # Zdroj posílá data pollingem a bývají zpožděná → štítek zůstává „zpožděno“
    is_live = False
    # Burzovní data Yahoo bývají zpožděná ~15 min (s rezervou)
    data_delay_ms = 30 * 60_000

    def __init__(self) -> None:
        # Yahoo nemá 4h ani 10m svíčky; 10m se skládá z 5m (viz self.aggregated)
        self.timeframes = {
            "1m": "1m",
            "2m": "2m",
            "5m": "5m",
            "10m": None,
            "15m": "15m",
            "1h": "1h",
            "1D": "1d",
            "1W": "1wk",
        }
        self.aggregated = {"10m": "5m"}
        # Názvy a burzy instrumentů pro nadpis grafu (symbol -> info)
        self._describe_cache: dict[str, dict] = {}
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

    def symbol_kind(self, symbol: str) -> str:
        """Druh instrumentu podle tvaru symbolu Yahoo: EURUSD=X forex, ^GSPC index, ES=F futures."""
        upper = symbol.upper()
        if upper.endswith("=X"):
            return "forex"
        if upper.startswith("^"):
            return "index"
        if upper.endswith("=F"):
            return "futures"
        if upper.endswith("-USD") or upper.endswith("-EUR"):
            return "crypto"
        return "stock"

    def price_tick(self, symbol: str, price: float) -> Optional[float]:
        """Forex: standardní tick brokerů (0.00001, JPY páry s cenou nad 20 – 0.001).

        Odhad z dat by u forexu nemusel odpovídat ticku brokerů (Yahoo ceny
        zaokrouhluje různě); ostatní instrumenty se odhadují z dat.
        """
        if self.symbol_kind(symbol) != "forex":
            return None
        return 0.001 if price >= 20 else 0.00001

    def exchange_timezone(self, symbol: str) -> str:
        """Pásmo burzy podle přípony symbolu (AAPL → New York, SAP.DE → Berlín)."""
        for suffix, tz_name in SUFFIX_TIMEZONES.items():
            if symbol.upper().endswith(suffix.upper()):
                return tz_name
        return "America/New_York"

    async def describe(self, symbol: str) -> dict:
        """Název a burza z vyhledávání Yahoo (blokující volání ve vlákně, cache v paměti)."""
        if symbol in self._describe_cache:
            return self._describe_cache[symbol]

        def do_search() -> list[dict]:
            return yf.Search(symbol, max_results=8).quotes

        quotes = await asyncio.to_thread(do_search)
        # Bereme jen přesnou shodu symbolu (vyhledávání vrací i podobné tickery)
        match = next((item for item in quotes if item.get("symbol") == symbol), None)
        if not match:
            return await super().describe(symbol)
        info = {
            "name": match.get("longname") or match.get("shortname") or symbol,
            "exchange": match.get("exchDisp") or match.get("exchange") or self.name,
        }
        self._describe_cache[symbol] = info
        return info

    async def search(self, query: str, limit: int = 30) -> list[SymbolInfo]:
        """Vyhledá symboly přes yf.Search (blokující volání ve vlákně)."""
        q = query.strip()
        if not q:
            # Prázdný dotaz: pár oblíbených tickers pro rychlý start
            popular = [
                SymbolInfo(self.id, "AAPL", "AAPL – Apple Inc. (NMS)", "Apple Inc.", "stock", "NASDAQ"),
                SymbolInfo(self.id, "MSFT", "MSFT – Microsoft Corporation (NMS)", "Microsoft Corporation", "stock", "NASDAQ"),
                SymbolInfo(self.id, "^GSPC", "^GSPC – S&P 500 (SPX)", "S&P 500", "index", "SNP"),
                SymbolInfo(self.id, "EURUSD=X", "EURUSD=X – EUR/USD (CCY)", "EUR/USD", "forex", "CCY"),
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
                label=f"{item['symbol']} – {item.get('shortname') or item.get('longname') or '?'}",
                description=item.get("longname") or item.get("shortname") or "",
                kind=QUOTE_TYPE_KINDS.get(item.get("quoteType"), ""),
                exchange=item.get("exchDisp") or item.get("exchange") or "",
            )
            for item in quotes
            if item.get("quoteType") in QUOTE_TYPE_KINDS and item.get("symbol")
        ]

    @staticmethod
    def _dataframe_to_candles(df: pd.DataFrame) -> list[Candle]:
        """Převede výstup yfinance na seznam svíček s časem UTC v ms."""
        if df is None or df.empty:
            return []
        # yfinance vrací MultiIndex sloupce i pro jeden symbol → srovnáme
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        # Ceny přicházejí ve float32 – bez šumu převodu (23.11 místo 23.11000061)
        price_columns = ["Open", "High", "Low", "Close"]
        df[price_columns] = df[price_columns].map(clean_float32_price)
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
        if timeframe in self.aggregated:
            return await self._aggregated_history(symbol, timeframe, start_ms, end_ms)
        return await self._download(symbol, timeframe, start_ms, end_ms)

    def stream(self, symbol: str, timeframe: str) -> AsyncIterator[StreamItem]:
        """Živá aktualizace dotazováním: v pravidelných intervalech posílá
        poslední svíčku, ale jen když je nová nebo se změnila.
        """
        if timeframe in self.aggregated:
            return self._aggregated_stream(symbol, timeframe)
        # Polling stahuje jen poslední den (intraday) resp. týden (denní)
        period = "5d" if timeframe in ("1D", "1W") else "1d"

        async def fetch_latest() -> list[Candle]:
            return await self._download(symbol, timeframe, None, None, period=period)

        return poll_latest_candles(fetch_latest, poll_seconds_for(timeframe))
