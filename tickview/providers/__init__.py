"""Registr dostupných datových zdrojů.

Nový zdroj stačí přidat do seznamu PROVIDERS; zbytek aplikace se nezmění.
Alpaca je volitelný – přidá se jen tehdy, když jsou v lokální konfiguraci
(data/config.local.json) uložené API klíče.
"""

from tickview.providers.alpaca import AlpacaProvider
from tickview.providers.base import DataProvider
from tickview.providers.binance import BinanceProvider
from tickview.providers.yahoo import YahooProvider


def _build_providers() -> list[DataProvider]:
    providers: list[DataProvider] = [BinanceProvider(), YahooProvider()]
    alpaca = AlpacaProvider()
    if alpaca.configured:
        # Volitelný zdroj se zapne až po zadání API klíčů v nastavení
        providers.append(alpaca)
    return providers


PROVIDERS: list[DataProvider] = _build_providers()


def get_provider(provider_id: str) -> DataProvider:
    """Vrátí zdroj podle identifikátoru; neznámý id vyhodí chybu."""
    for provider in PROVIDERS:
        if provider.id == provider_id:
            return provider
    raise KeyError(f"Neznámý zdroj dat: {provider_id}")
