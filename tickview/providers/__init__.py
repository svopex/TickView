"""Registr dostupných datových zdrojů.

Nový zdroj stačí přidat do seznamu PROVIDERS; zbytek aplikace se nezmění.
Alpaca je volitelný – přidá se jen tehdy, když jsou v lokální konfiguraci
(config.yaml) uložené API klíče.
"""

from tickview.providers.alpaca import AlpacaProvider
from tickview.providers.base import DataProvider
from tickview.providers.binance import BinanceProvider
from tickview.providers.ibkr import IbkrProvider
from tickview.providers.yahoo import YahooProvider


def _build_providers() -> list[DataProvider]:
    """Sestaví seznam aktivních zdrojů v pořadí, v jakém se prohledávají."""
    # IBKR se připojuje lazy k TWS (127.0.0.1:7497, clientId 30); když TWS
    # neběží, zdroj jen nahlásí chybu, ostatní zdroje fungují dál
    providers: list[DataProvider] = [
        BinanceProvider(),
        YahooProvider(),
        IbkrProvider(),
    ]
    # Alpaca bez klíčů by u každého hledání a obnovy cen hlásila chybu 401/403
    alpaca = AlpacaProvider()
    if alpaca.configured:
        providers.append(alpaca)
    return providers


PROVIDERS: list[DataProvider] = _build_providers()


def get_provider(provider_id: str) -> DataProvider:
    """Vrátí zdroj podle identifikátoru; neznámý id vyhodí chybu."""
    for provider in PROVIDERS:
        if provider.id == provider_id:
            return provider
    raise KeyError(f"Neznámý zdroj dat: {provider_id}")
