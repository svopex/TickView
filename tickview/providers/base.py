"""Společné rozhraní datových zdrojů (poskytovatelů tržních dat)."""

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
