"""Společné rozhraní datových zdrojů (poskytovatelů tržních dat)."""

import asyncio
import time as time_module
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional, Union

# Kanonické timeframy aplikace; každý zdroj si je mapuje na vlastní API hodnoty
CANONICAL_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1D", "1W")

# Délka timeframeu v milisekundách (UTC), klíč = kanonický timeframe
TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1D": 86_400_000,
    "1W": 604_800_000,
}

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


def poll_seconds_for(timeframe: str) -> float:
    """Vrátí interval pollingu pro timeframe (denní/týdenní data stačí obnovovat řidčeji)."""
    return POLL_SECONDS_DAILY if timeframe in ("1D", "1W") else POLL_SECONDS_INTRADAY


async def poll_latest_candles(
    fetch: Callable[[], Awaitable[list["Candle"]]], poll_seconds: float
) -> AsyncIterator["StreamItem"]:
    """Společná smyčka pollingu: v intervalu volá fetch a posílá nové svíčky.

    Při prvním dotazu pošle jen poslední svíčku, dál všechny svíčky od té
    naposledy poslané (tedy i konečnou podobu předchozí svíčky, když mezitím
    začala nová), a to jen pokud jsou nové nebo se změnila jejich cena.
    Výpadek zdroje ohlásí jednou jako StreamStatus(connected=False) a zkusí to
    znovu v dalším kole; po obnovení pošle StreamStatus(connected=True).
    Generátor běží, dokud ho konzument nezruší (CancelledError).

    Parametry:
        fetch: async funkce vracející posledních pár svíček vzestupně podle času.
        poll_seconds: pauza mezi dotazy v sekundách.
    """
    last_open_time = -1
    last_close: Optional[float] = None
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
            # První kolo: jen poslední svíčka; další kola: vše od poslední poslané
            fresh = candles[-1:] if last_open_time < 0 else [
                c for c in candles if c.open_time >= last_open_time
            ]
            for candle in fresh:
                if candle.open_time > last_open_time or (
                    candle.open_time == last_open_time and candle.close != last_close
                ):
                    last_open_time = candle.open_time
                    last_close = candle.close
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
    """

    connected: bool
    message: str


# Položka živého streamu: nová/aktualizovaná svíčka nebo změna stavu spojení
StreamItem = Union[Candle, StreamStatus]


@dataclass(slots=True)
class SymbolInfo:
    """Výsledek vyhledání symbolu u zdroje."""

    provider_id: str
    symbol: str
    label: str


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

    async def tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Poslední cena a denní změna pro watchlist: {symbol: {"price", "change_pct"}}.

        Výchozí implementace nic nevrací (zdroj bez podpory cen watchlistu).
        """
        return {}
