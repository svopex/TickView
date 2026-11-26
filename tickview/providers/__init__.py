"""Registr dostupných datových zdrojů.

Nový zdroj stačí přidat do seznamu PROVIDERS; zbytek aplikace se nezmění.
Alpaca a cTrader jsou volitelné – přidají se jen tehdy, když jsou v lokální
konfiguraci (config.yaml) uložené API klíče / údaje aplikace a token.
"""

from typing import Optional

from tickview.i18n import tr
from tickview.providers.alpaca import AlpacaProvider
from tickview.providers.base import DataProvider, split_symbol
from tickview.providers.binance import BinanceProvider
from tickview.providers.ctrader import CTraderProvider
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
    # cTrader (forex, kovy a CFD s tick volume; i objem pro IBKR) jen s údaji Open API
    ctrader = CTraderProvider()
    if ctrader.configured:
        providers.append(ctrader)
    return providers


PROVIDERS: list[DataProvider] = _build_providers()


def get_provider(provider_id: str) -> DataProvider:
    """Vrátí zdroj podle identifikátoru; neznámý id vyhodí chybu."""
    for provider in PROVIDERS:
        if provider.id == provider_id:
            return provider
    raise KeyError(tr("Neznámý zdroj dat: {provider}").format(provider=provider_id))


def provider_symbol(text: str) -> Optional[tuple[DataProvider, str]]:
    """Přímo zadaný symbol "zdroj:SYMBOL" se známým zdrojem jako (zdroj, SYMBOL); jinak None.

    Rozklad jako ve vstupech indikátoru (split_symbol): "yahoo:gc=f"
    i "Yahoo:GC=F" dají GC=F z Yahoo.
    """
    source, symbol = split_symbol(text)
    if not source or not symbol or any(char.isspace() for char in symbol):
        return None
    try:
        return get_provider(source), symbol
    except KeyError:
        return None
