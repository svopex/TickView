"""Datový zdroj Interactive Brokers přes TWS API (ib_async).

Napojení na TWS / IB Gateway: výchozí host 127.0.0.1, port 7497 (demo TWS)
a clientId 30; hodnoty lze přepsat v config.yaml v sekci "ibkr".
Zdroj se připojuje lazy až při prvním dotazu a po výpadku se znovu připojuje
s cooldownem, aby se TWS nezahlcovala pokusy. Když clientId už používá jiný
klient (např. druhá instance aplikace), zkusí se několik následujících.
"""

import asyncio
import math
import time as time_module
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from ib_async import IB, Stock

from tickview.config import load_config
from tickview.providers.base import (
    Candle,
    DataProvider,
    RateLimiter,
    StreamItem,
    SymbolInfo,
)

# Výchozí napojení na TWS (demo): host, port a clientId
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7497
DEFAULT_CLIENT_ID = 30
# Jak dlouho po neúspěšném připojení se TWS nezkouší znovu (sekundy)
RECONNECT_COOLDOWN_S = 60.0
# Kolik dalších clientId se zkusí, když je nastavený obsazený jiným klientem
CLIENT_ID_ATTEMPTS = 5
# Jak často se obnovují ceny watchlistu (throttle kvůli TWS pacing)
TICKER_TTL_S = 30.0

# Mapování kanonických timeframeů na barSizeSetting TWS API
BAR_SIZES = {
    "1m": "1 min",
    "5m": "5 mins",
    "15m": "15 mins",
    "1h": "1 hour",
    "4h": "4 hours",
    "1D": "1 day",
    "1W": "1 week",
}


class IbkrProvider(DataProvider):
    """Interactive Brokers (TWS/Gateway) – historie a polling živých dat."""

    id = "ibkr"
    name = "IBKR"
    supports_stream = True
    # Živá data se z TWS dotazují pollingem v intervalu (ne stream) → štítek „zpožděno“
    is_live = False

    def __init__(self) -> None:
        config = load_config().get("ibkr", {})
        self._host = config.get("host") or DEFAULT_HOST
        self._port = int(config.get("port") or DEFAULT_PORT)
        self._client_id = int(config.get("client_id") or DEFAULT_CLIENT_ID)
        self.timeframes = dict(BAR_SIZES)
        self._ib: Optional[IB] = None
        # Souběžné dotazy (hledání, ceny, historie) se připojují jen jednou
        self._connect_lock = asyncio.Lock()
        # Cooldown po neúspěšném připojení (monotonic čas posledního selhání)
        self._last_fail = 0.0
        # Cache cen pro watchlist (symbol -> (čas, data))
        self._ticker_cache: dict[str, tuple[float, dict]] = {}
        # Omezovač dotazů kvůli TWS pacing limitům
        self._limiter = RateLimiter(min_interval=0.35)

    async def _ensure_connected(self) -> IB:
        """Vrátí připojené IB; po výpadku zkusí reconnect s cooldownem."""
        if self._ib is not None and self._ib.isConnected():
            return self._ib
        async with self._connect_lock:
            # Mezitím se mohl připojit jiný souběžný dotaz
            if self._ib is not None and self._ib.isConnected():
                return self._ib
            return await self._connect()

    async def _connect(self) -> IB:
        """Nové připojení k TWS (volá se pod zámkem); zkouší i další clientId."""
        now = time_module.monotonic()
        if now - self._last_fail < RECONNECT_COOLDOWN_S:
            raise RuntimeError(
                f"TWS ({self._host}:{self._port}) není dostupná – další pokus za "
                f"{int(RECONNECT_COOLDOWN_S - (now - self._last_fail))} s."
            )
        # Před novým připojením uklidíme starou instanci, pokud existuje
        if self._ib is not None:
            self._disconnect_quietly(self._ib)
            self._ib = None
        last_error: Optional[BaseException] = None
        for client_id in range(self._client_id, self._client_id + CLIENT_ID_ATTEMPTS):
            ib = IB()
            try:
                await ib.connectAsync(self._host, self._port, clientId=client_id, timeout=6)
            except Exception as exc:
                last_error = exc
                self._disconnect_quietly(ib)
                # TWS vůbec neběží – další clientId nemá smysl zkoušet; jinak jde
                # typicky o chybu 326 „client id is already in use“ (TWS spojení
                # zavře a ib_async skončí timeoutem) → zkusíme další clientId
                if isinstance(exc, ConnectionRefusedError):
                    break
                continue
            self._ib = ib
            return ib
        self._last_fail = time_module.monotonic()
        raise RuntimeError(
            f"Připojení k TWS ({self._host}:{self._port}, clientId {self._client_id}"
            f"–{self._client_id + CLIENT_ID_ATTEMPTS - 1}) selhalo: {last_error!r}"
        ) from last_error

    @staticmethod
    def _disconnect_quietly(ib: IB) -> None:
        # Úklid nepovedeného/starého spojení; chyby při odpojení nejsou podstatné
        try:
            ib.disconnect()
        except Exception:
            pass

    async def close(self) -> None:
        """Odpojí se od TWS (při ukončení aplikace)."""
        if self._ib is not None:
            self._disconnect_quietly(self._ib)
            self._ib = None

    @staticmethod
    def _stock(symbol: str) -> Stock:
        # Kontrakt americké akcie/ETF na SMART burze v USD
        return Stock(symbol, "SMART", "USD")

    @staticmethod
    def _duration(delta_seconds: int) -> str:
        """Převede délku intervalu na duration string TWS (S / D / Y)."""
        if delta_seconds < 86400:
            return f"{delta_seconds} S"
        days = max(1, math.ceil(delta_seconds / 86400))
        # TWS odmítá duration delší než 365 dní v jiné jednotce než roky (chyba 321)
        if days > 365:
            return f"{math.ceil(days / 365)} Y"
        return f"{days} D"

    async def search(self, query: str, limit: int = 30) -> list[SymbolInfo]:
        """Vyhledá akcie/ETF přes TWS matching symbols (prefixová shoda)."""
        q = query.strip().upper()
        if not q:
            # Prázdný dotaz: oblíbené tickery pro rychlý start
            popular = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "SPY"]
            return [SymbolInfo(self.id, symbol, symbol) for symbol in popular]
        ib = await self._ensure_connected()
        await self._limiter.acquire()
        try:
            descriptions = await ib.reqMatchingSymbolsAsync(q)
        except Exception:
            # Neznámý dotaz TWS občas ukončí chybou – bereme jako prázdný výsledek
            return []
        results: list[SymbolInfo] = []
        seen: set[str] = set()
        for description in descriptions:
            contract = description.contract
            # Jen akcie/ETF; duplicity napříč burzami sloučíme podle symbolu
            if getattr(contract, "secType", "") != "STK":
                continue
            symbol = contract.symbol
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            long_name = getattr(contract, "longName", "") or ""
            label = f"{symbol} – {long_name}" if long_name else symbol
            results.append(SymbolInfo(self.id, symbol, label))
            if len(results) >= min(limit, 12):
                break
        return results

    async def history(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Candle]:
        """Stáhne historii svíček z TWS (TRADES, včetně mimo hlavní hodiny)."""
        ib = await self._ensure_connected()
        contract = self._stock(symbol)
        # qualifyContracts doplní conId a ověří, že kontrakt existuje
        contract = (await ib.qualifyContractsAsync(contract))[0]
        delta_seconds = max(60, (end_ms - start_ms) // 1000)
        duration = self._duration(delta_seconds)
        end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        await self._limiter.acquire()
        # Kratší timeout: při chybě validace TWS (např. neplatná duration) totiž
        # ib_async čeká na odpověď, která nikdy nepřijde, a UI by zamrzlo
        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime=end_dt,
            durationStr=duration,
            barSizeSetting=self.timeframes[timeframe],
            whatToShow="TRADES",
            useRTH=False,
            formatDate=2,
            timeout=20,
        )
        candles: list[Candle] = []
        for bar in bars:
            # ib_async 2.x: intraday bary mají datetime (UTC aware), denní/weekly
            # bary vrací datetime.date → považujeme za UTC půlnoc
            bar_time = getattr(bar, "date", None) or getattr(bar, "time", None)
            if isinstance(bar_time, datetime):
                if bar_time.tzinfo is None:
                    bar_time = bar_time.replace(tzinfo=timezone.utc)
            else:
                bar_time = datetime.combine(bar_time, datetime.min.time(), tzinfo=timezone.utc)
            if not all(
                math.isfinite(value) for value in (bar.open, bar.high, bar.low, bar.close)
            ):
                continue
            candles.append(
                Candle(
                    open_time=int(bar_time.timestamp() * 1000),
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=float(bar.volume or 0),
                )
            )
        # TWS může vrátit data mimo požadovaný interval – ořízneme a seřadíme
        candles = [c for c in candles if start_ms <= c.open_time <= end_ms]
        candles.sort(key=lambda c: c.open_time)
        return candles

    def stream(self, symbol: str, timeframe: str) -> AsyncIterator[StreamItem]:
        """Živá aktualizace dotazováním TWS v intervalu (výpadek TWS řeší cooldown
        v _ensure_connected, polling to zkusí znovu v dalším kole)."""
        return self._poll_history(symbol, timeframe)

    async def tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Vrátí poslední cenu a denní změnu (%) z posledních dvou denních barů."""
        result: dict[str, dict] = {}
        now = time_module.monotonic()
        for symbol in symbols:
            cached = self._ticker_cache.get(symbol)
            if cached and now - cached[0] < TICKER_TTL_S:
                result[symbol] = cached[1]
                continue
            try:
                ib = await self._ensure_connected()
                contract = (await ib.qualifyContractsAsync(self._stock(symbol)))[0]
                bars = await ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr="3 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=False,
                    formatDate=2,
                )
                # Poslední dva použitelné denní bary (dnes + předchozí den)
                closes = [float(bar.close) for bar in bars if math.isfinite(bar.close)]
                if len(closes) >= 2:
                    last, previous = closes[-1], closes[-2]
                    quote = {
                        "price": last,
                        "change_pct": (last / previous - 1.0) * 100.0 if previous else 0.0,
                    }
                    self._ticker_cache[symbol] = (now, quote)
                    result[symbol] = quote
            except Exception:
                # Výpadek TWS u jednoho symbolu nesmí zablokovat ostatní
                continue
        return result
