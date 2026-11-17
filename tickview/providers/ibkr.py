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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from ib_async import IB, Contract, Forex, Stock

from tickview.config import load_config
from tickview.providers.base import (
    TIMEFRAME_MS,
    US_EQUITY_SESSION,
    Candle,
    DataProvider,
    FailureLatch,
    FreshCandles,
    RateLimiter,
    StreamItem,
    StreamStatus,
    SymbolInfo,
    poll_seconds_for,
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

# Typy tržních dat TWS (reqMarketDataType / Ticker.marketDataType)
MARKET_DATA_DELAYED = 3
MARKET_DATA_DELAYED_FROZEN = 4
# Jak dlouho se po přihlášení k tickům čeká na první tick / typ dat (sekundy)
MARKET_DATA_PROBE_S = 4.0
# Chyby reqMktData, které znamenají chybějící předplatné živých dat
# (354 nepředplaceno, 10089 vyžaduje předplatné, 10167 zobrazuje se
# zpožděně, 10168 nepředplaceno a zpožděná data nejsou povolena)
NOT_SUBSCRIBED_CODES = frozenset({354, 10089, 10167, 10168})
# Kolik posledních svíček se stáhne při přihlášení k průběžným barům
STREAM_SEED_BARS = 5
# Pauza před novým přihlášením k průběžným barům po chybě odběru (sekundy)
STREAM_RETRY_S = 5.0
# Kódy TWS, které odběr průběžných barů neukončí: 165 informační zpráva
# historických dat, 2100–2199 varování o stavu datových farem
IGNORED_STREAM_CODES = frozenset({165, *range(2100, 2200)})

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


# Časové pásmo obchodních hodin v TWS a označení trhu podle typu kontraktu
# (kovy CMDTY se řídí newyorským časem, CFD londýnským)
SEC_TYPE_INFO: dict[str, tuple[str, str]] = {
    "CMDTY": ("America/New_York", "IBKR Kov"),
    "CFD": ("Europe/London", "IBKR CFD"),
}


@dataclass(frozen=True)
class IbInstrument:
    """Instrument IB mimo forex a akcie (spotový kov, indexové nebo ropné CFD).

    Atributy:
        sec_type: typ kontraktu TWS (klíč SEC_TYPE_INFO).
        currency: měna kontraktu.
        name: název pro hledání a nadpis grafu.
        kind: druh instrumentu (SYMBOL_KINDS).
        aliases: další názvy, podle kterých ho hledání najde (bez mezer, velkými písmeny).
    """

    sec_type: str
    currency: str
    name: str
    kind: str
    aliases: tuple[str, ...] = ()

    @property
    def timezone(self) -> str:
        """Časové pásmo obchodních hodin kontraktu v TWS."""
        return SEC_TYPE_INFO[self.sec_type][0]

    @property
    def market(self) -> str:
        """Označení trhu ve výsledcích hledání a v nadpisu grafu."""
        return SEC_TYPE_INFO[self.sec_type][1]


# Spotové kovy, indexová a ropná CFD od IB (ověřené v TWS: kontrakt na SMART
# a historie MIDPOINT); obchodují téměř nonstop jako kovy, indexy a ropa OANDA
# v TradingView. IB nemá CFD na Russell 2000 (místo něj ETF IWM jako akcie –
# CFD na IWM historii přes API nevrací, cena je stejná)
IB_INSTRUMENTS: dict[str, IbInstrument] = {
    "XAUUSD": IbInstrument("CMDTY", "USD", "Zlato (London Gold)", "commodity", ("GOLD", "ZLATO", "XAU")),
    "XAGUSD": IbInstrument("CMDTY", "USD", "Stříbro (London Silver)", "commodity", ("SILVER", "STRIBRO", "XAG")),
    "IBUSOIL": IbInstrument("CFD", "USD", "US Crude Oil (WTI)", "commodity", ("WTI", "OIL", "ROPA", "CRUDE", "USOIL")),
    "IBUKOIL": IbInstrument("CFD", "USD", "UK Crude Oil (Brent)", "commodity", ("BRENT", "OIL", "ROPA", "CRUDE", "UKOIL")),
    "IBUS500": IbInstrument("CFD", "USD", "US 500 (S&P 500)", "index", ("SP500", "SPX", "US500")),
    "IBUS30": IbInstrument("CFD", "USD", "US 30 (Dow Jones)", "index", ("DOW", "DJI", "US30")),
    "IBUST100": IbInstrument("CFD", "USD", "US Tech 100 (Nasdaq 100)", "index", ("NASDAQ", "NDX", "NAS100", "US100")),
    "IBDE40": IbInstrument("CFD", "EUR", "Germany 40 (DAX)", "index", ("DAX", "DE40", "DE30", "GER40")),
    "IBGB100": IbInstrument("CFD", "GBP", "UK 100 (FTSE 100)", "index", ("FTSE", "UK100")),
    "IBEU50": IbInstrument("CFD", "EUR", "Euro 50 (Euro Stoxx 50)", "index", ("STOXX", "EU50")),
    "IBFR40": IbInstrument("CFD", "EUR", "France 40 (CAC 40)", "index", ("CAC", "FR40")),
    "IBJP225": IbInstrument("CFD", "JPY", "Japan 225 (Nikkei 225)", "index", ("NIKKEI", "JP225")),
    "IBCH20": IbInstrument("CFD", "CHF", "Switzerland 20 (SMI)", "index", ("SMI", "CH20")),
    "IBES35": IbInstrument("CFD", "EUR", "Spain 35 (IBEX 35)", "index", ("IBEX", "ES35")),
    "IBNL25": IbInstrument("CFD", "EUR", "Netherlands 25 (AEX)", "index", ("AEX", "NL25")),
    "IBHK50": IbInstrument("CFD", "HKD", "Hong Kong 50 (Hang Seng)", "index", ("HANGSENG", "HSI", "HK50")),
    "IBAU200": IbInstrument("CFD", "AUD", "Australia 200 (ASX 200)", "index", ("ASX", "AU200")),
}


def instrument_suggestions(query: str) -> list[str]:
    """Kovy a CFD odpovídající dotazu: začátek symbolu nebo názvu ("XAU", "gold", "DAX", "S&P 500", "oil")."""
    text = "".join(ch for ch in query.upper() if ch.isalnum())
    if len(text) < 3:
        return []
    return [
        symbol for symbol, instrument in IB_INSTRUMENTS.items()
        if symbol.startswith(text) or any(alias.startswith(text) or text.startswith(alias) for alias in instrument.aliases)
    ]


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


def connection_settings(config: dict) -> dict:
    """Připojovací údaje z konfigurace doplněné výchozími: {"host", "port", "client_id"}."""
    section = config.get("ibkr") or {}
    return {
        "host": str(section.get("host") or DEFAULT_HOST),
        "port": int(section.get("port") or DEFAULT_PORT),
        "client_id": int(section.get("client_id") or DEFAULT_CLIENT_ID),
    }


class IbkrProvider(DataProvider):
    """Interactive Brokers (TWS/Gateway) – historie a průběžně aktualizované bary."""

    id = "ibkr"
    name = "IBKR"
    supports_stream = True
    # Zda jsou data živá, se zjistí až podle předplatného při otevření streamu
    # (StreamStatus.live); do té doby štítek ukazuje „zpožděno“
    is_live = False

    def __init__(self) -> None:
        settings = connection_settings(load_config())
        self._host, self._port, self._client_id = settings["host"], settings["port"], settings["client_id"]
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
        # Zda TWS posílá pro kontrakt živá data (conId -> bool); předplatné se
        # za běhu nemění, sonda se tak dělá jen jednou za kontrakt
        self._live_cache: dict[int, bool] = {}
        # Kvalifikované kontrakty podle symbolu (viz _qualified)
        self._contracts: dict[str, Contract] = {}
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
                # Typ 3 pro celé spojení: s předplatným živé ticky, bez něj
                # zpožděné místo chyby (využívá sonda _probe_live_data)
                ib.reqMarketDataType(MARKET_DATA_DELAYED)
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

    def _drop_connection(self) -> None:
        # Zavře spojení s TWS; další dotaz se připojí znovu
        if self._ib is not None:
            self._disconnect_quietly(self._ib)
            self._ib = None

    async def close(self) -> None:
        """Odpojí se od TWS (při ukončení aplikace)."""
        self._drop_connection()

    def reload_config(self) -> None:
        """Nové připojovací údaje z config.yaml (Settings) bez restartu aplikace.

        Staré spojení se zavře a cooldown po neúspěchu zruší; další dotaz se
        připojí s novými údaji (živé streamy se samy znovu připojí).
        """
        settings = connection_settings(load_config())
        changed = (settings["host"], settings["port"], settings["client_id"]) != (self._host, self._port, self._client_id)
        if changed:
            self._host, self._port, self._client_id = settings["host"], settings["port"], settings["client_id"]
            self._last_fail = 0.0
            self._drop_connection()

    @staticmethod
    async def test_connection(host: str, port: int, client_id: int) -> str:
        """Zkušební připojení k TWS / IB Gateway s údaji z dialogu; vrátí popis, chyba = výjimka.

        Používá clientId za rozsahem běžného připojení, aby se nestřetlo
        se spojením, které zdroj právě drží.
        """
        ib = IB()
        try:
            await ib.connectAsync(host, port, clientId=client_id + CLIENT_ID_ATTEMPTS, timeout=6)
            accounts = ", ".join(ib.managedAccounts()) or "bez účtu"
            return f"TWS odpovídá (server verze {ib.client.serverVersion()}, účet {accounts})."
        finally:
            IbkrProvider._disconnect_quietly(ib)

    @staticmethod
    def _contract(symbol: str) -> Contract:
        """Kontrakt podle symbolu: "EUR.USD" = forex IDEALPRO, kov / indexové CFD
        z IB_INSTRUMENTS (XAUUSD, IBUS500…), jinak americká akcie/ETF na SMART v USD."""
        if parts := forex_parts(symbol):
            return Forex(parts[0] + parts[1])
        if instrument := IB_INSTRUMENTS.get(symbol):
            return Contract(secType=instrument.sec_type, symbol=symbol, exchange="SMART", currency=instrument.currency)
        return Stock(symbol, "SMART", "USD")

    def _instrument_info(self, symbol: str) -> SymbolInfo:
        # Návrh kovu / CFD pro vyhledávání
        instrument = IB_INSTRUMENTS[symbol]
        return SymbolInfo(self.id, symbol, f"{symbol} – {instrument.name}", instrument.name, instrument.kind,
                          instrument.market)

    async def _qualified(self, ib: IB, symbol: str) -> Contract:
        """Kvalifikovaný kontrakt symbolu (conId od TWS); cachuje se, conId se nemění.

        Scanner a watchlist se ptají opakovaně – bez cache by každý dotaz
        znamenal navíc qualifyContracts v TWS.
        """
        if symbol in self._contracts:
            return self._contracts[symbol]
        qualified = await ib.qualifyContractsAsync(self._contract(symbol))
        contract = qualified[0] if qualified else None
        # Neznámý kontrakt (None / bez conId) se necachuje – volající dostane chybu jako dřív
        if contract is None or not contract.conId:
            raise ValueError(f"TWS nezná kontrakt {symbol}.")
        self._contracts[symbol] = contract
        return contract

    @staticmethod
    def _midpoint(symbol: str) -> bool:
        """Forex, kovy a CFD: bez obchodů (data MIDPOINT) a téměř nonstop (bez filtru hlavní seance)."""
        return bool(forex_parts(symbol)) or symbol in IB_INSTRUMENTS

    @staticmethod
    def _forex_label(pair: str) -> str:
        # Popisek návrhu forexového páru: "EUR.USD – Euro / Americký dolar (Forex)"
        return f"{pair} – {forex_name(*pair.split('.'))} (Forex)"

    def _forex_info(self, pair: str) -> SymbolInfo:
        # Návrh forexového páru pro vyhledávání (trh IDEALPRO)
        return SymbolInfo(self.id, pair, self._forex_label(pair), forex_name(*pair.split(".")), "forex", "IDEALPRO")

    async def describe(self, symbol: str) -> dict:
        """Název a burza pro nadpis grafu (forex lokálně, akcie z contract details TWS)."""
        if parts := forex_parts(symbol):
            return {"name": forex_name(*parts), "exchange": "IDEALPRO"}
        if instrument := IB_INSTRUMENTS.get(symbol):
            return {"name": instrument.name, "exchange": instrument.market}
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

    def symbol_kind(self, symbol: str) -> str:
        """Forex IDEALPRO ("EUR.USD"), kov / indexové CFD podle IB_INSTRUMENTS, jinak americká akcie."""
        if forex_parts(symbol):
            return "forex"
        instrument = IB_INSTRUMENTS.get(symbol)
        return instrument.kind if instrument else "stock"

    def currencies(self, symbol: str) -> tuple[Optional[str], str]:
        """Forex EUR.USD → (EUR, USD), kov / CFD podle měny kontraktu, akcie v dolarech."""
        parts = forex_parts(symbol)
        if parts:
            return parts
        instrument = IB_INSTRUMENTS.get(symbol)
        return None, instrument.currency if instrument else "USD"

    def exchange_timezone(self, symbol: str) -> str:
        """Americké akcie, forex IDEALPRO a kovy se v TWS řídí newyorským časem, CFD londýnským."""
        instrument = IB_INSTRUMENTS.get(symbol)
        return instrument.timezone if instrument else US_EQUITY_SESSION[0]

    def regular_session(self, symbol: str) -> Optional[tuple[str, str, str]]:
        """Akcie: historie se stahuje i mimo hlavní hodiny (useRTH=False), RTH
        je 9:30–16:00 New York; forex, kovy a CFD obchodují téměř nepřetržitě."""
        return None if self._midpoint(symbol) else US_EQUITY_SESSION

    @classmethod
    def _what_to_show(cls, symbol: str) -> str:
        # Forex, kovy a CFD nemají obchody (TRADES) – TWS nabízí jen MIDPOINT/BID/ASK
        return "MIDPOINT" if cls._midpoint(symbol) else "TRADES"

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
            return [SymbolInfo(self.id, symbol, symbol, kind="stock") for symbol in popular]
        # Forexové páry, kovy a CFD se nabízí lokálně (i bez TWS); přesně zadaný
        # pár nebo instrument („EUR.USD“, „XAUUSD“) se v TWS už nehledá
        results: list[SymbolInfo] = [self._forex_info(pair) for pair in forex_suggestions(q)]
        results += [self._instrument_info(symbol) for symbol in instrument_suggestions(q)]
        if forex_parts(q) or q in IB_INSTRUMENTS:
            return results
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
                    results.append(self._forex_info(pair))
                continue
            # Jinak jen akcie/ETF; duplicity napříč burzami sloučíme podle symbolu
            if sec_type != "STK":
                continue
            symbol = contract.symbol
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            # Název firmy: novější TWS ho posílá v description, starší v longName
            long_name = getattr(contract, "description", "") or getattr(contract, "longName", "") or ""
            label = f"{symbol} – {long_name}" if long_name else symbol
            exchange = getattr(contract, "primaryExchange", "") or getattr(contract, "exchange", "") or ""
            results.append(SymbolInfo(self.id, symbol, label, long_name, "stock", exchange))
            if len(results) >= min(limit, 12):
                break
        return results

    async def history(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Candle]:
        """Stáhne historii svíček z TWS (akcie TRADES, forex MIDPOINT; včetně
        obchodování mimo hlavní hodiny)."""
        ib = await self._ensure_connected()
        # Kvalifikace doplní conId a ověří, že kontrakt existuje
        contract = await self._qualified(ib, symbol)
        delta_seconds = max(60, (end_ms - start_ms) // 1000)
        end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        bars = await self._request_bars(
            ib, contract, symbol, timeframe, end_dt, self._duration(delta_seconds)
        )
        candles = [candle for bar in bars if (candle := self._bar_to_candle(bar))]
        # TWS může vrátit data mimo požadovaný interval – ořízneme a seřadíme
        candles = [c for c in candles if start_ms <= c.open_time <= end_ms]
        candles.sort(key=lambda c: c.open_time)
        return candles

    async def _request_bars(
        self, ib: IB, contract: Contract, symbol: str, timeframe: str,
        end, duration: str, keep_up_to_date: bool = False,
    ):
        """Požadavek na bary TWS se společnými parametry (vč. mimo hlavní hodiny).

        Parametry:
            ib, contract: připojení a kvalifikovaný kontrakt.
            symbol, timeframe: určují whatToShow a barSizeSetting.
            end: konec intervalu (datetime), "" = současnost (nutné pro keepUpToDate).
            duration: délka intervalu ve formátu TWS ("3600 S", "5 D"…).
            keep_up_to_date: True = průběžně aktualizovaný odběr poslední svíčky.
        """
        await self._limiter.acquire()
        # Kratší timeout: při chybě validace TWS (např. neplatná duration) totiž
        # ib_async čeká na odpověď, která nikdy nepřijde, a UI by zamrzlo
        return await ib.reqHistoricalDataAsync(
            contract,
            endDateTime=end,
            durationStr=duration,
            barSizeSetting=self.timeframes[timeframe],
            whatToShow=self._what_to_show(symbol),
            useRTH=False,
            formatDate=2,
            keepUpToDate=keep_up_to_date,
            timeout=20,
        )

    @staticmethod
    def _bar_to_candle(bar) -> Optional[Candle]:
        """Převede bar TWS na Candle; bar s neplatnou cenou (NaN) vrátí jako None."""
        # ib_async 2.x: intraday bary mají datetime (UTC aware), denní/weekly
        # bary vrací datetime.date → považujeme za UTC půlnoc
        bar_time = getattr(bar, "date", None) or getattr(bar, "time", None)
        if isinstance(bar_time, datetime):
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)
        else:
            bar_time = datetime.combine(bar_time, datetime.min.time(), tzinfo=timezone.utc)
        if not all(math.isfinite(value) for value in (bar.open, bar.high, bar.low, bar.close)):
            return None
        return Candle(
            open_time=int(bar_time.timestamp() * 1000),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            # Forex (MIDPOINT) objem nemá – TWS posílá -1
            volume=max(0.0, float(bar.volume or 0)),
        )

    async def _probe_live_data(self, ib: IB, contract: Contract) -> bool:
        """Zjistí, zda TWS pro kontrakt posílá živá (předplacená) data.

        Krátce se přihlásí k tickům (spojení má typ dat 3 = zpožděná, když
        chybí předplatné) a přečte Ticker.marketDataType, případně chybu
        o chybějícím předplatném. Snapshot se nepoužívá, protože ho IB
        u nepředplacených dat zpoplatňuje. Výsledek se cachuje podle conId.

        Vrací False = zpožděná data / bez předplatného, jinak True (i když se
        v limitu nic nedozvíme – TWS průběžné bary přijala).
        """
        if contract.conId in self._live_cache:
            return self._live_cache[contract.conId]
        ticker = ib.reqMktData(contract, "", False, False)
        decided = asyncio.Event()
        not_subscribed = False

        def on_error(_req_id: int, code: int, _message: str, error_contract) -> None:
            # Chyba o chybějícím předplatném pro náš kontrakt (jiný odběr ticků
            # k němu během sondy neběží)
            nonlocal not_subscribed
            if code in NOT_SUBSCRIBED_CODES and getattr(error_contract, "conId", 0) == contract.conId:
                not_subscribed = True
                decided.set()

        def on_ticker_update(_ticker) -> None:
            # TWS posílá typ dat před prvními ticky (výchozí hodnota Tickeru je 1),
            # takže stačí počkat na první cenový tick (živý i zpožděný)
            if math.isfinite(ticker.marketPrice()) or math.isfinite(ticker.close):
                decided.set()

        ib.errorEvent += on_error
        ticker.updateEvent += on_ticker_update
        try:
            await asyncio.wait_for(decided.wait(), MARKET_DATA_PROBE_S)
        except asyncio.TimeoutError:
            pass
        finally:
            ib.errorEvent -= on_error
            ticker.updateEvent -= on_ticker_update
            ib.cancelMktData(contract)
        live = not not_subscribed and ticker.marketDataType not in (
            MARKET_DATA_DELAYED, MARKET_DATA_DELAYED_FROZEN
        )
        if decided.is_set():
            self._live_cache[contract.conId] = live
        return live

    def stream(self, symbol: str, timeframe: str) -> AsyncIterator[StreamItem]:
        """Živý stream průběžně aktualizovaných barů z TWS (keepUpToDate)."""
        return self._stream_bars(symbol, timeframe)

    async def _stream_bars(self, symbol: str, timeframe: str) -> AsyncIterator[StreamItem]:
        """Přihlásí se k průběžným barům TWS a posílá změny poslední svíčky.

        Při každém (znovu)přihlášení pošle StreamStatus s live podle
        předplatného (sonda běží souběžně s přihlášením k barům). Když TWS
        průběžné bary odmítne (bez předplatného, nepodporovaná velikost
        baru), přepne se na polling historie se štítkem „zpožděno“. Výpadek
        TWS ohlásí jednou a přihlásí se znovu (připojení hlídá cooldown
        v _connect, proto tu není exponenciální backoff jako u websocketů).
        """
        step_s = TIMEFRAME_MS[timeframe] // 1000
        latch = FailureLatch()
        while True:
            # Přihlášení: připojení, kontrakt a souběžně sonda typu dat a odběr barů
            try:
                ib = await self._ensure_connected()
                contract = await self._qualified(ib, symbol)
                live, bars = await asyncio.gather(
                    self._probe_live_data(ib, contract),
                    self._request_bars(
                        ib, contract, symbol, timeframe, "",
                        self._duration(STREAM_SEED_BARS * step_s), keep_up_to_date=True,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Výpadek TWS hlásíme jen jednou, dokud se neobnoví
                if status := latch.fail(f"TWS neodpovídá: {exc}"):
                    yield status
                await asyncio.sleep(poll_seconds_for(timeframe))
                continue
            # Přihlášení uspělo – stav (i obnovení po výpadku) ohlásí _follow_bars
            latch.reset()

            if not bars:
                # TWS odběr odmítla (chyba ukončí požadavek prázdným výsledkem)
                # → zrušíme registraci odběru a zůstaneme u pollingu historie
                self._cancel_bars(ib, bars)
                yield StreamStatus(
                    True,
                    "TWS odmítla průběžné bary (chybí předplatné živých dat?) – "
                    "data se obnovují v intervalu",
                    live=False,
                )
                async for item in self._poll_history(symbol, timeframe):
                    yield item
                return

            async for item in self._follow_bars(ib, bars, live, latch):
                yield item
            # Odběr skončil chybou nebo odpojením → krátká pauza a nové přihlášení
            await asyncio.sleep(STREAM_RETRY_S)

    @staticmethod
    async def _follow_bars(ib: IB, bars, live: bool, latch: FailureLatch) -> AsyncIterator[StreamItem]:
        """Posílá změny průběžných barů jednoho keepUpToDate odběru.

        Nejdřív pošle stav (live) a poslední svíčku, pak každou změnu; při
        nové svíčce i konečnou podobu předchozí. Skončí po chybě odběru nebo
        odpojení TWS (výpadek ohlásí přes latch); odběr vždy zruší.

        Parametry:
            ib: připojená instance IB, ke které odběr patří.
            bars: BarDataList z reqHistoricalData(keepUpToDate=True).
            live: zda jde o živá (předplacená) data – pro štítek v UI.
            latch: stav výpadků streamu (výpadek se hlásí jen jednou).
        """
        # Callbacky ib_async jen nastaví příznak změny, případně důvod konce
        # odběru; více aktualizací mezi průchody smyčky se tak sloučí
        changed = asyncio.Event()
        end_reason: Optional[str] = None

        def finish(reason: str) -> None:
            nonlocal end_reason
            end_reason = reason
            changed.set()

        def on_update(_bars, _has_new_bar: bool) -> None:
            changed.set()

        def on_error(req_id: int, code: int, message: str, _contract) -> None:
            # Chyba našeho odběru; informační zprávy a varování farem ignorujeme
            if req_id == bars.reqId and code not in IGNORED_STREAM_CODES:
                finish(f"chyba {code}: {message}")

        def on_disconnect() -> None:
            finish("TWS se odpojila")

        bars.updateEvent += on_update
        ib.errorEvent += on_error
        ib.disconnectedEvent += on_disconnect
        try:
            yield StreamStatus(
                True,
                "živá data z TWS" if live else "TWS posílá zpožděná data (chybí předplatné)",
                live=live,
            )
            fresh_candles = FreshCandles()
            while True:
                recent = [IbkrProvider._bar_to_candle(bar) for bar in bars[-STREAM_SEED_BARS:]]
                for candle in fresh_candles.take([c for c in recent if c]):
                    yield candle
                if end_reason is not None:
                    break
                await changed.wait()
                changed.clear()
            if status := latch.fail(f"průběžné bary přerušeny – {end_reason}"):
                yield status
        finally:
            bars.updateEvent -= on_update
            ib.errorEvent -= on_error
            ib.disconnectedEvent -= on_disconnect
            IbkrProvider._cancel_bars(ib, bars)

    @staticmethod
    def _cancel_bars(ib: IB, bars) -> None:
        # Zrušení keepUpToDate odběru; po odpojení TWS ho ib_async uklidil sám
        if not ib.isConnected():
            return
        try:
            ib.cancelHistoricalData(bars)
        except Exception:
            pass

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
                contract = await self._qualified(ib, symbol)
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
