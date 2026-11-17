"""Společné rozhraní datových zdrojů (poskytovatelů tržních dat)."""

import asyncio
import time as time_module
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import AsyncIterator, Awaitable, Callable, Optional, Union

import numpy as np

# Kanonické timeframy aplikace; každý zdroj si je mapuje na vlastní API hodnoty
CANONICAL_TIMEFRAMES = ("1m", "2m", "5m", "10m", "15m", "1h", "4h", "1D", "1W")

# Délka timeframeu v milisekundách (UTC), klíč = kanonický timeframe
TIMEFRAME_MS = {
    "1m": 60_000,
    "2m": 120_000,
    "5m": 300_000,
    "10m": 600_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1D": 86_400_000,
    "1W": 604_800_000,
}

# Hlavní obchodní seance amerických akcií (RTH): pásmo, otevření, zavření
US_EQUITY_SESSION = ("America/New_York", "09:30", "16:00")

# Interval dotazování (polling) živých dat v sekundách: intraday vs. denní/týdenní
POLL_SECONDS_INTRADAY = 15.0
POLL_SECONDS_DAILY = 60.0


def utc_now_ms() -> int:
    """Aktuální čas v milisekundách UTC."""
    return int(time_module.time() * 1000)


def is_closed(candle: "Candle", step_ms: int, now_ms: int) -> bool:
    """True, když období svíčky už uplynulo (svíčka se nezmění a smí do cache)."""
    return candle.open_time + step_ms <= now_ms


class FailureLatch:
    """Hlídá stav spojení streamu: výpadek i obnovení ohlásí vždy jen jednou.

    Metody vrací StreamStatus k odeslání, nebo None, když se stav nezměnil.
    """

    def __init__(self) -> None:
        self.failing = False

    def fail(self, message: str) -> Optional["StreamStatus"]:
        """Výpadek: zpráva jen při přechodu z funkčního stavu."""
        if self.failing:
            return None
        self.failing = True
        return StreamStatus(False, message)

    def ok(self, message: str) -> Optional["StreamStatus"]:
        """Spojení funguje: zpráva jen při obnovení po výpadku."""
        if not self.failing:
            return None
        self.failing = False
        return StreamStatus(True, message)

    def reset(self) -> None:
        """Spojení funguje a obnovení ohlásí volající sám (bez zprávy z latche)."""
        self.failing = False


class FreshCandles:
    """Vybírá svíčky k odeslání ve živém streamu z posledních známých svíček.

    Poprvé vybere jen poslední svíčku, dál všechny od té naposledy vybrané
    (tedy i konečnou podobu předchozí svíčky, když mezitím začala nová), a to
    jen pokud jsou nové nebo se změnily.
    """

    def __init__(self) -> None:
        # Kopie naposledy vybrané svíčky (konzument může původní objekt měnit)
        self.last: Optional["Candle"] = None

    def take(self, candles: list["Candle"]) -> list["Candle"]:
        """Vrátí nové/změněné svíčky; candles musí být vzestupně podle času."""
        if self.last is None:
            candidates = candles[-1:]
        else:
            candidates = [c for c in candles if c.open_time >= self.last.open_time]
        fresh = [c for c in candidates if c != self.last]
        if fresh:
            self.last = replace(fresh[-1])
        return fresh


def merge_candle(candles: list["Candle"], candle: "Candle") -> None:
    """Promítne živou svíčku do seznamu (vzestupně): stejná se nahradí, novější připojí, starší ignoruje."""
    if candles and candle.open_time == candles[-1].open_time:
        candles[-1] = candle
    elif not candles or candle.open_time > candles[-1].open_time:
        candles.append(candle)


def aggregate_candles(candles: list["Candle"], step_ms: int) -> list["Candle"]:
    """Sloučí kratší svíčky do delšího timeframu (např. 1m → 2m, 5m → 10m).

    Svíčky se seskupí podle začátku období zarovnaného na step_ms od epochy
    (UTC); open = první, close = poslední, high/low = extrémy, objem = součet.

    Parametry:
        candles: svíčky kratšího timeframu (pořadí nevadí).
        step_ms: délka cílového timeframu v ms.
    """
    merged: list[Candle] = []
    for candle in sorted(candles, key=lambda item: item.open_time):
        bucket = candle.open_time - candle.open_time % step_ms
        if merged and merged[-1].open_time == bucket:
            last = merged[-1]
            last.high = max(last.high, candle.high)
            last.low = min(last.low, candle.low)
            last.close = candle.close
            last.volume += candle.volume
        else:
            merged.append(
                Candle(bucket, candle.open, candle.high, candle.low, candle.close, candle.volume)
            )
    return merged


def poll_seconds_for(timeframe: str) -> float:
    """Vrátí interval pollingu pro timeframe (denní/týdenní data stačí obnovovat řidčeji)."""
    return POLL_SECONDS_DAILY if timeframe in ("1D", "1W") else POLL_SECONDS_INTRADAY


def clean_float32_price(value: float) -> float:
    """Odstraní šum převodu z float32 (23.110000610351562 → 23.11).

    Yahoo Finance posílá ceny ve float32; po převodu na float64 mají až osm
    „falešných“ desetinných míst, podle kterých by se špatně určila přesnost
    ceny. Hodnota přesně reprezentovatelná ve float32 se nahradí nejkratším
    desetinným zápisem, který dá stejný float32; ostatní hodnoty se nemění.

    Parametry:
        value: cena (float64).
    """
    # str(np.float32) dává nejkratší zápis, který se vrátí na stejný float32;
    # srovnání ve float64 (mimo rozsah float32 vyjde inf a hodnota se nemění)
    with np.errstate(over="ignore"):
        as_float32 = np.float32(value)
    return float(str(as_float32)) if float(as_float32) == value else value


async def poll_latest_candles(
    fetch: Callable[[], Awaitable[list["Candle"]]], poll_seconds: float
) -> AsyncIterator["StreamItem"]:
    """Společná smyčka pollingu: v intervalu volá fetch a posílá nové svíčky.

    Při prvním dotazu pošle jen poslední svíčku, dál všechny svíčky od té
    naposledy poslané (tedy i konečnou podobu předchozí svíčky, když mezitím
    začala nová), a to jen pokud jsou nové nebo změněné (FreshCandles).
    Výpadek zdroje ohlásí jednou jako StreamStatus(connected=False) a zkusí to
    znovu v dalším kole; po obnovení pošle StreamStatus(connected=True).
    Generátor běží, dokud ho konzument nezruší (CancelledError).

    Parametry:
        fetch: async funkce vracející posledních pár svíček vzestupně podle času.
        poll_seconds: pauza mezi dotazy v sekundách.
    """
    fresh_candles = FreshCandles()
    latch = FailureLatch()
    while True:
        try:
            candles = await fetch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Výpadek zdroje hlásíme jen jednou, dokud se neobnoví
            if status := latch.fail(f"zdroj neodpovídá: {exc}"):
                yield status
        else:
            if status := latch.ok("zdroj opět odpovídá"):
                yield status
            for candle in fresh_candles.take(candles):
                yield candle
        await asyncio.sleep(poll_seconds)


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
        # Slot rezervujeme ještě před čekáním, aby souběžná volání nevyrazila
        # ve stejný okamžik (každé další dostane slot o min_interval později)
        self._next_slot = wait_until + self._min_interval
        delay = wait_until - now
        if delay > 0:
            await asyncio.sleep(delay)

    def penalize(self, retry_after: Optional[float] = None) -> None:
        """Zvyšuje čekání po chybě 429/5xx; hlavička Retry-After má přednost."""
        self._backoff = min(max(self._backoff * 2, 1.0), self._max_backoff)
        if retry_after:
            self._backoff = max(self._backoff, min(retry_after, self._max_backoff))

    def clear_backoff(self) -> None:
        """Po úspěšném dotazu vrátí backoff na nulu."""
        self._backoff = 0.0


@dataclass(slots=True)
class Candle:
    """Jedna svíčka; open_time je UTC v milisekundách (otevření svíčky)."""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class StreamStatus:
    """Stavová zpráva živého streamu (výpadek / obnovení spojení se zdrojem).

    Atributy:
        connected: True = spojení obnoveno, False = výpadek (čeká se na znovupřipojení).
        message: popis pro stavovou lištu.
        live: True/False = zdroj upřesňuje, zda posílá živá data (např. IBKR
              podle předplatného); None = platí DataProvider.is_live_for.
    """

    connected: bool
    message: str
    live: Optional[bool] = None


# Položka živého streamu: nová/aktualizovaná svíčka nebo změna stavu spojení
StreamItem = Union[Candle, StreamStatus]


# Druhy instrumentů pro kategorie vyhledávání (jako v TradingView)
SYMBOL_KINDS = ("stock", "fund", "futures", "forex", "crypto", "index", "commodity")


def split_symbol(text: str) -> tuple[str, str]:
    """Rozloží "zdroj:SYMBOL" na (zdroj, symbol); bez prefixu je zdroj prázdný.

    Zdroj se převede na malá písmena, symbol na velká (tak symboly vedou
    všechny zdroje), okolní mezery se zahodí.
    """
    source, colon, symbol = text.strip().partition(":")
    if not colon:
        return "", text.strip().upper()
    return source.strip().lower(), symbol.strip().upper()


@dataclass(slots=True)
class SymbolInfo:
    """Výsledek vyhledání symbolu u zdroje.

    Atributy:
        provider_id: id zdroje.
        symbol: symbol zdroje (např. "BTCUSDT", "EUR.USD").
        label: krátký popisek „symbol – název“.
        description: celý název instrumentu (firma, pár) pro vyhledávací dialog.
        kind: druh instrumentu (viz SYMBOL_KINDS; prázdný = neznámý).
        exchange: burza / trh (prázdný = název zdroje).
    """

    provider_id: str
    symbol: str
    label: str
    description: str = ""
    kind: str = ""
    exchange: str = ""


class DataProvider(ABC):
    """Rozhraní zdroje dat: vyhledání symbolů, historie a živý stream.

    Atributy:
        id: krátký identifikátor zdroje (např. "binance"), používá se v UI i cache.
        name: zobrazený název zdroje.
        supports_stream: zda zdroj umí živá data (websocket nebo polling).
        timeframes: mapování kanonický timeframe -> API hodnota zdroje;
                    obsahuje jen timeframy, které zdroj podporuje.
    """

    id: str = ""
    name: str = ""
    supports_stream: bool = False
    # True = živý stream (websocket), False = polling/zpožděná data (štítek v UI)
    is_live: bool = False
    # O kolik se zdroj zpožďuje za skutečností (ms); úsek před současností
    # kratší než tato doba cache nepovažuje za kompletně stažený
    data_delay_ms: int = 0
    timeframes: dict[str, str] = {}
    # Timeframy, které zdroj nemá nativně a skládají se z kratších:
    # kanonický timeframe -> základní timeframe (musí být v self.timeframes)
    aggregated: dict[str, str] = {}

    async def _aggregated_history(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Candle]:
        """Historie složeného timeframu: stáhne základní svíčky a sloučí je.

        Základní data se stahují od začátku období, do kterého spadá start_ms,
        do konce období s end_ms (nejvýš do současnosti), takže každá vrácená
        svíčka je složená ze všech svých částí a smí do cache. Svíčka, která
        začíná před start_ms, se nevrací (patří do předchozího úseku).
        """
        base_timeframe = self.aggregated[timeframe]
        step_ms = TIMEFRAME_MS[timeframe]
        fetch_start = start_ms - start_ms % step_ms
        fetch_end = min(end_ms - end_ms % step_ms + step_ms - 1, utc_now_ms())
        base = await self.history(symbol, base_timeframe, fetch_start, max(fetch_end, start_ms))
        return [c for c in aggregate_candles(base, step_ms) if start_ms <= c.open_time <= end_ms]

    async def _aggregated_stream(self, symbol: str, timeframe: str) -> AsyncIterator[StreamItem]:
        """Živý stream složeného timeframu nad streamem základního timeframu.

        Rozpracovanou svíčku skládá z částí; když stream začne uprostřed
        období (nebo začne nové období a chybí jeho první části), doplní
        chybějící části z historie, aby open a objem odpovídaly skutečnosti.
        Stavové zprávy (výpadek / obnovení) předává beze změny.
        """
        base_timeframe = self.aggregated[timeframe]
        step_ms = TIMEFRAME_MS[timeframe]
        bucket: Optional[int] = None
        parts: dict[int, Candle] = {}
        async for item in self.stream(symbol, base_timeframe):
            if isinstance(item, StreamStatus):
                yield item
                continue
            item_bucket = item.open_time - item.open_time % step_ms
            if bucket is not None and item_bucket < bucket:
                # Opožděná část už uzavřeného období – ignorujeme
                continue
            if bucket is None or item_bucket > bucket:
                bucket = item_bucket
                parts = {}
                if item.open_time > bucket:
                    # Chybí začátek období → doplníme z historie základního timeframu
                    try:
                        seed = await self.history(symbol, base_timeframe, bucket, item.open_time - 1)
                    except Exception:
                        seed = []
                    parts.update({c.open_time: c for c in seed if bucket <= c.open_time < item.open_time})
            parts[item.open_time] = item
            yield aggregate_candles(list(parts.values()), step_ms)[0]

    def is_live_for(self, timeframe: str) -> bool:
        """Zda jsou živá data pro daný timeframe skutečně živá (štítek „živě“)."""
        return self.is_live

    def _poll_history(self, symbol: str, timeframe: str) -> AsyncIterator[StreamItem]:
        """Živá data pollingem REST historie: v intervalu stáhne poslední tři svíčky."""
        step_ms = TIMEFRAME_MS[timeframe]

        async def fetch_latest() -> list[Candle]:
            # Poslední tři svíčky stačí pro detekci nové/rozpracované svíčky
            now_ms = utc_now_ms()
            return await self.history(symbol, timeframe, now_ms - 3 * step_ms, now_ms)

        return poll_latest_candles(fetch_latest, poll_seconds_for(timeframe))

    @abstractmethod
    async def search(self, query: str, limit: int = 30) -> list[SymbolInfo]:
        """Vyhledá symboly podle dotazu (prázdný dotaz = nabídka na začátek)."""

    @abstractmethod
    async def history(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Candle]:
        """Vrátí svíčky pro symbol a timeframe v daném intervalu (UTC ms)."""

    def stream(
        self, symbol: str, timeframe: str
    ) -> AsyncIterator[StreamItem]:
        """Živý stream svíček a stavových zpráv; zdroje bez podpory streamu ho nemají."""
        raise NotImplementedError(f"Zdroj {self.id} nepodporuje živá data.")

    async def close(self) -> None:
        """Uvolní síťové prostředky zdroje (volá se při ukončení aplikace)."""

    def exchange_timezone(self, symbol: str) -> str:
        """IANA časové pásmo burzy instrumentu (volba „Exchange“ u času grafu)."""
        return "UTC"

    def reload_config(self) -> None:
        """Znovu načte svou sekci config.yaml po uložení Settings (výchozí: změna až po restartu)."""

    def symbol_kind(self, symbol: str) -> str:
        """Druh instrumentu (viz SYMBOL_KINDS) pro indikátory; prázdný = neznámý."""
        return ""

    def currencies(self, symbol: str) -> tuple[Optional[str], str]:
        """Měny instrumentu pro převod P&L v replayi: (základní měna forexového páru, měna ceny).

        Základní měna je None u instrumentů, které nejsou forexový pár;
        výchozí implementace bere cenu v dolarech.
        """
        return None, "USD"

    def price_tick(self, symbol: str, price: float) -> Optional[float]:
        """Nejmenší změna ceny instrumentu pro indikátory; None = odhadne se z dat.

        Parametry:
            symbol: symbol zdroje.
            price: poslední cena (u forexu odliší JPY páry).
        """
        return None

    def contract_size(self, symbol: str) -> Optional[float]:
        """Velikost kontraktu (hodnota bodu u futures) pro replay; None = výchozí podle druhu instrumentu."""
        return None

    def regular_session(self, symbol: str) -> Optional[tuple[str, str, str]]:
        """Hlavní obchodní seance pro filtr RTH: (pásmo, "HH:MM" od, "HH:MM" do).

        None = zdroj rozšířené hodiny neposílá nebo instrument žádné nemá
        (krypto, forex) – přepínač RTH/ETH pak nemá vliv.
        """
        return None

    async def describe(self, symbol: str) -> dict:
        """Název instrumentu a burza pro nadpis grafu: {"name", "exchange"}.

        Výchozí implementace vrací jen symbol a název zdroje; zdroje s
        metadaty (název firmy, burza) ji přepisují.
        """
        return {"name": symbol, "exchange": self.name}

    async def tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Poslední cena a denní změna pro watchlist: {symbol: {"price", "change_pct"}}.

        Výchozí implementace nic nevrací (zdroj bez podpory cen watchlistu).
        """
        return {}
