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

from ib_async import IB, Contract, Forex, Stock

from tickview.config import load_config
from tickview.providers.base import (
    US_EQUITY_SESSION,
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

# Měny forexu IDEALPRO v pořadí, ve kterém je IB řadí v páru (dřívější = základní
# měna: EUR.USD, GBP.USD, USD.JPY, EUR.CZK…); slouží k sestavení páru z dotazu
FOREX_CURRENCIES = [
    "EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "NOK", "SEK", "DKK", "CZK",
    "PLN", "HUF", "TRY", "ZAR", "MXN", "SGD", "HKD", "CNH", "ILS", "JPY",
]
# České názvy měn pro popisky návrhů a nadpis grafu
CURRENCY_NAMES = {
    "EUR": "Euro", "GBP": "Britská libra", "AUD": "Australský dolar", "NZD": "Novozélandský dolar",
    "USD": "Americký dolar", "CAD": "Kanadský dolar", "CHF": "Švýcarský frank", "NOK": "Norská koruna",
    "SEK": "Švédská koruna", "DKK": "Dánská koruna", "CZK": "Česká koruna", "PLN": "Polský zlotý",
    "HUF": "Maďarský forint", "TRY": "Turecká lira", "ZAR": "Jihoafrický rand", "MXN": "Mexické peso",
    "SGD": "Singapurský dolar", "HKD": "Hongkongský dolar", "CNH": "Čínský jüan (offshore)",
    "ILS": "Izraelský šekel", "JPY": "Japonský jen",
}
# Hlavní měny, se kterými se nabízí páry při dotazu na jednu měnu (např. "EUR")
FOREX_MAJORS = ["EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CZK"]
# Kolik forexových návrhů se nabídne nejvýš
FOREX_SUGGESTIONS = 6


def forex_name(base: str, quote: str) -> str:
    """Český název páru: "Euro / Americký dolar"."""
    return f"{CURRENCY_NAMES[base]} / {CURRENCY_NAMES[quote]}"


def forex_pair(first: str, second: str) -> str:
    """Sestaví symbol páru ve tvaru IB ("EUR.USD") se základní měnou podle konvence."""
    a, b = sorted((first, second), key=FOREX_CURRENCIES.index)
    return f"{a}.{b}"


def forex_parts(symbol: str) -> Optional[tuple[str, str]]:
    """Rozloží symbol "EUR.USD" na (základní, kótovaná měna); jiný symbol → None."""
    base, dot, quote = symbol.partition(".")
    if dot and base in CURRENCY_NAMES and quote in CURRENCY_NAMES and base != quote:
        return base, quote
    return None


def forex_suggestions(query: str) -> list[str]:
    """Forexové páry odpovídající dotazu (EURUSD, EUR/USD, EUR.USD, EUR, EURU…).

    Dotaz se zbaví oddělovačů; šest písmen dvou známých měn = konkrétní pár,
    jinak se nabídnou páry hlavních měn, jejichž zápis (v obou pořadích) dotaz
    začíná – např. "EUR" → EUR.USD, EUR.GBP, …
    """
    text = "".join(ch for ch in query.upper() if ch.isalpha())
    if len(text) < 3 or text[:3] not in CURRENCY_NAMES:
        return []
    if len(text) == 6 and text[3:] in CURRENCY_NAMES and text[:3] != text[3:]:
        return [forex_pair(text[:3], text[3:])]
    if len(text) > 6:
        return []
    first = text[:3]
    pairs: list[str] = []
    for other in FOREX_MAJORS:
        if other != first and (first + other).startswith(text):
            pairs.append(forex_pair(first, other))
    return pairs[:FOREX_SUGGESTIONS]


# Mapování kanonických timeframeů na barSizeSetting TWS API
BAR_SIZES = {
    "1m": "1 min",
    "2m": "2 mins",
    "5m": "5 mins",
    "10m": "10 mins",
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
        # Názvy a burzy instrumentů pro nadpis grafu (symbol -> info)
        self._describe_cache: dict[str, dict] = {}
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
    def _contract(symbol: str) -> Contract:
        """Kontrakt podle symbolu: "EUR.USD" = forex IDEALPRO, jinak americká
        akcie/ETF na SMART burze v USD."""
        if parts := forex_parts(symbol):
            return Forex(parts[0] + parts[1])
        return Stock(symbol, "SMART", "USD")

    @staticmethod
    def _forex_label(pair: str) -> str:
        # Popisek návrhu forexového páru: "EUR.USD – Euro / Americký dolar (Forex)"
        return f"{pair} – {forex_name(*pair.split('.'))} (Forex)"

    async def describe(self, symbol: str) -> dict:
        """Název a burza pro nadpis grafu (forex lokálně, akcie z contract details TWS)."""
        if parts := forex_parts(symbol):
            return {"name": forex_name(*parts), "exchange": "IDEALPRO"}
        if symbol in self._describe_cache:
            return self._describe_cache[symbol]
        ib = await self._ensure_connected()
        details = await ib.reqContractDetailsAsync(self._contract(symbol))
        if not details:
            return await super().describe(symbol)
        detail = details[0]
        info = {
            "name": detail.longName or symbol,
            "exchange": detail.contract.primaryExchange or detail.contract.exchange or self.name,
        }
        self._describe_cache[symbol] = info
        return info

    def exchange_timezone(self, symbol: str) -> str:
        """Americké akcie i forex IDEALPRO se v TWS řídí newyorským časem."""
        return US_EQUITY_SESSION[0]

    def regular_session(self, symbol: str) -> Optional[tuple[str, str, str]]:
        """Akcie: historie se stahuje i mimo hlavní hodiny (useRTH=False), RTH
        je 9:30–16:00 New York; forex obchoduje nepřetržitě."""
        if forex_parts(symbol):
            return None
        return US_EQUITY_SESSION

    @staticmethod
    def _what_to_show(symbol: str) -> str:
        # Forex nemá obchody (TRADES) – TWS u něj nabízí jen MIDPOINT/BID/ASK
        return "MIDPOINT" if forex_parts(symbol) else "TRADES"

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
        """Vyhledá forexové páry (podle dotazu, bez TWS) a akcie/ETF přes TWS
        matching symbols (prefixová shoda).

        Forexové páry se nabízí i bez běžící TWS; chyba připojení se ohlásí
        jen tehdy, když dotaz žádnému páru neodpovídá.
        """
        q = query.strip().upper()
        if not q:
            # Prázdný dotaz: oblíbené tickery pro rychlý start
            popular = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "SPY"]
            return [SymbolInfo(self.id, symbol, symbol) for symbol in popular]
        results: list[SymbolInfo] = [
            SymbolInfo(self.id, pair, self._forex_label(pair)) for pair in forex_suggestions(q)
        ]
        try:
            ib = await self._ensure_connected()
            await self._limiter.acquire()
            descriptions = await ib.reqMatchingSymbolsAsync(q) or []
        except Exception:
            # TWS neběží: forexové návrhy stačí; bez nich chybu ohlásíme výš
            if results:
                return results
            if self._ib is None or not self._ib.isConnected():
                raise
            # Neznámý dotaz TWS občas ukončí chybou – bereme jako prázdný výsledek
            return []
        seen: set[str] = {info.symbol for info in results}
        for description in descriptions:
            contract = description.contract
            sec_type = getattr(contract, "secType", "")
            if sec_type == "CASH":
                # Forex z TWS převedeme na tvar "EUR.USD"
                pair = f"{contract.symbol}.{contract.currency}"
                if forex_parts(pair) and pair not in seen:
                    seen.add(pair)
                    results.append(SymbolInfo(self.id, pair, self._forex_label(pair)))
                continue
            # Jinak jen akcie/ETF; duplicity napříč burzami sloučíme podle symbolu
            if sec_type != "STK":
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
        """Stáhne historii svíček z TWS (akcie TRADES, forex MIDPOINT; včetně
        obchodování mimo hlavní hodiny)."""
        ib = await self._ensure_connected()
        contract = self._contract(symbol)
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
            whatToShow=self._what_to_show(symbol),
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
                    # Forex (MIDPOINT) objem nemá – TWS posílá -1
                    volume=max(0.0, float(bar.volume or 0)),
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
                contract = (await ib.qualifyContractsAsync(self._contract(symbol)))[0]
                bars = await ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr="3 D",
                    barSizeSetting="1 day",
                    whatToShow=self._what_to_show(symbol),
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
