"""Společné rozhraní datových zdrojů (poskytovatelů tržních dat)."""

import asyncio
import time as time_module
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional

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


def utc_now_ms() -> int:
    """Aktuální čas v milisekundách UTC."""
    import time

    return int(time.time() * 1000)


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
    timeframes: dict[str, str] = {}

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
    ) -> AsyncIterator[Candle]:
        """Živý stream svíček; zdroje bez podpory streamu ho nemají."""
        raise NotImplementedError(f"Zdroj {self.id} nepodporuje živá data.")
