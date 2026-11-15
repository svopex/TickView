"""Registr dostupných datových zdrojů.

Nový zdroj stačí přidat do seznamu PROVIDERS; zbytek aplikace se nezmění.
"""

from tickview.providers.base import DataProvider
from tickview.providers.binance import BinanceProvider

PROVIDERS: list[DataProvider] = [BinanceProvider()]


def get_provider(provider_id: str) -> DataProvider:
    """Vrátí zdroj podle identifikátoru; neznámý id vyhodí chybu."""
    for provider in PROVIDERS:
        if provider.id == provider_id:
            return provider
    raise KeyError(f"Neznámý zdroj dat: {provider_id}")
