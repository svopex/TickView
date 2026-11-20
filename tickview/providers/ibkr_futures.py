"""Futures Interactive Brokers: kontinuální front month s automatickým rollem.

Symbol "ES1!" (jako v TradingView) znamená nejbližší (front month) kontrakt
z tabulky FUTURES; libovolný jiný kořen se zadá s burzou jako "VIX1!@CFE".
Kontinuální řada není cenově upravená (back-adjusted): každý časový úsek
bere data kontraktu, který byl v té době front month, a ke kontraktu
následujícímu se přechází roll_days dní před posledním obchodním dnem.
Modul obsahuje jen čisté funkce a tabulky (bez spojení s TWS), aby šel testovat.
"""

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from tickview.i18n import N_
from tickview.providers.base import TIMEFRAME_MS

# Přípona kontinuálního front month kontraktu (jako v TradingView)
CONTINUOUS_SUFFIX = "1!"
# Kódy měsíců futures (F = leden … Z = prosinec)
MONTH_CODES = "FGHJKMNQUVXZ"
# Pásma obchodních hodin podle burzy IB (CME Group se řídí chicagským časem)
EXCHANGE_TIMEZONES = {
    "CME": "America/Chicago",
    "CBOT": "America/Chicago",
    "NYMEX": "America/Chicago",
    "COMEX": "America/Chicago",
    "CFE": "America/Chicago",
    "EUREX": "Europe/Berlin",
    "ICEEU": "Europe/London",
}
# Měna kontraktů podle burzy (odhad pro obecný symbol, než TWS pošle detaily)
EXCHANGE_CURRENCIES = {"EUREX": "EUR", "ICEEU": "GBP"}
# O kolik dní před posledním obchodním dnem se obecný symbol přetočí na další kontrakt
GENERIC_ROLL_DAYS = 5
# Kolik dní před rollem prvního známého kontraktu se mu ještě přiřadí data,
# když chybí předchozí kontrakt (řetězec má jediný kontrakt)
FIRST_LEG_SPAN_DAYS = 90


@dataclass(frozen=True)
class FutureSpec:
    """Popis kontinuálního futures kontraktu.

    Atributy:
        root: symbol podkladu v TWS ("ES", "DAX", "EUR").
        exchange: burza IB ("CME", "EUREX"…).
        trading_class: obchodní třída v TWS ("FDAX", "6E"); prázdná = vybere se podle řetězce.
        currency: měna kontraktu.
        name: název pro hledání a nadpis grafu.
        multiplier: hodnota jednoho bodu (velikost kontraktu); 0 = podle TWS.
        tick: nejmenší změna ceny; 0 = podle TWS.
        months: měsíce kontraktů, které tvoří kontinuální řadu ("GJMQVZ"); prázdné = všechny.
        roll_days: o kolik dní před posledním obchodním dnem se přechází na další kontrakt.
        aliases: další názvy pro hledání (bez mezer, velkými písmeny).
    """

    root: str
    exchange: str
    trading_class: str
    currency: str
    name: str
    multiplier: float = 0.0
    tick: float = 0.0
    months: str = ""
    roll_days: int = GENERIC_ROLL_DAYS
    aliases: tuple[str, ...] = ()

    @property
    def timezone(self) -> str:
        """Časové pásmo obchodních hodin burzy."""
        return EXCHANGE_TIMEZONES.get(self.exchange, "America/Chicago")


# Kontinuální futures nabízené v hledání (klíč + "1!" = symbol aplikace).
# Kořeny, třídy, násobitele a ticky ověřené v TWS (reqContractDetails);
# roll_days odpovídá zvyklostem trhu: indexy týden před expirací, kovy, dluhopisy
# a zemědělské komodity před prvním oznamovacím dnem, ropa a plyn pár dní předem
FUTURES: dict[str, FutureSpec] = {
    # Akciové indexy
    "ES": FutureSpec("ES", "CME", "ES", "USD", "E-mini S&P 500", 50, 0.25, "HMUZ", 8, ("SP500", "SPX", "US500", "S&P500")),
    "MES": FutureSpec("MES", "CME", "MES", "USD", "Micro E-mini S&P 500", 5, 0.25, "HMUZ", 8, ("SP500", "SPX", "US500", "S&P500")),
    "NQ": FutureSpec("NQ", "CME", "NQ", "USD", "E-mini Nasdaq 100", 20, 0.25, "HMUZ", 8, ("NASDAQ", "NDX", "NAS100", "US100")),
    "MNQ": FutureSpec("MNQ", "CME", "MNQ", "USD", "Micro E-mini Nasdaq 100", 2, 0.25, "HMUZ", 8, ("NASDAQ", "NDX", "NAS100", "US100")),
    "YM": FutureSpec("YM", "CBOT", "YM", "USD", "E-mini Dow", 5, 1.0, "HMUZ", 8, ("DOW", "DJI", "US30")),
    "MYM": FutureSpec("MYM", "CBOT", "MYM", "USD", "Micro E-mini Dow", 0.5, 1.0, "HMUZ", 8, ("DOW", "DJI", "US30")),
    "RTY": FutureSpec("RTY", "CME", "RTY", "USD", "E-mini Russell 2000", 50, 0.1, "HMUZ", 8, ("RUSSELL", "RUT", "US2000")),
    "M2K": FutureSpec("M2K", "CME", "M2K", "USD", "Micro E-mini Russell 2000", 5, 0.1, "HMUZ", 8, ("RUSSELL", "RUT", "US2000")),
    "NKD": FutureSpec("NKD", "CME", "NKD", "USD", "Nikkei 225 (USD)", 5, 5.0, "HMUZ", 8, ("NIKKEI", "JP225")),
    "FDAX": FutureSpec("DAX", "EUREX", "FDAX", "EUR", "DAX 40", 25, 1.0, "HMUZ", 8, ("DAX", "DE40", "GER40")),
    "FDXM": FutureSpec("DAX", "EUREX", "FDXM", "EUR", "Mini DAX 40", 5, 1.0, "HMUZ", 8, ("DAX", "DE40", "GER40")),
    "FESX": FutureSpec("ESTX50", "EUREX", "FESX", "EUR", "Euro Stoxx 50", 10, 1.0, "HMUZ", 8, ("STOXX", "EU50", "SX5E")),
    "VX": FutureSpec("VIX", "CFE", "VX", "USD", N_("VIX (volatilita S&P 500)"), 1000, 0.05, "", 1, ("VIX",)),
    # Kovy
    "GC": FutureSpec("GC", "COMEX", "GC", "USD", N_("Zlato"), 100, 0.1, "GJMQVZ", 32, ("GOLD", "ZLATO")),
    "MGC": FutureSpec("MGC", "COMEX", "MGC", "USD", N_("Micro zlato"), 10, 0.1, "GJMQVZ", 32, ("GOLD", "ZLATO")),
    "SI": FutureSpec("SI", "COMEX", "SI", "USD", N_("Stříbro"), 5000, 0.005, "HKNUZ", 32, ("SILVER", "STRIBRO")),
    "HG": FutureSpec("HG", "COMEX", "HG", "USD", N_("Měď"), 25000, 0.0005, "HKNUZ", 32, ("COPPER", "MED")),
    "PL": FutureSpec("PL", "NYMEX", "PL", "USD", N_("Platina"), 50, 0.1, "FJNV", 32, ("PLATINUM", "PLATINA")),
    # Energie
    "CL": FutureSpec("CL", "NYMEX", "CL", "USD", N_("Ropa WTI"), 1000, 0.01, "", 5, ("WTI", "OIL", "ROPA", "CRUDE", "USOIL")),
    "MCL": FutureSpec("MCL", "NYMEX", "MCL", "USD", N_("Micro ropa WTI"), 100, 0.01, "", 5, ("WTI", "OIL", "ROPA", "CRUDE", "USOIL")),
    "BZ": FutureSpec("BZ", "NYMEX", "BZ", "USD", N_("Ropa Brent"), 1000, 0.01, "", 3, ("BRENT", "OIL", "ROPA", "UKOIL")),
    "NG": FutureSpec("NG", "NYMEX", "NG", "USD", N_("Zemní plyn (Henry Hub)"), 10000, 0.001, "", 3, ("NATGAS", "GAS", "PLYN")),
    # Dluhopisy
    "ZT": FutureSpec("ZT", "CBOT", "ZT", "USD", N_("2letá US Treasury Note"), 2000, 0.00390625, "HMUZ", 25, ("TNOTE", "TREASURY")),
    "ZF": FutureSpec("ZF", "CBOT", "ZF", "USD", N_("5letá US Treasury Note"), 1000, 0.0078125, "HMUZ", 25, ("TNOTE", "TREASURY")),
    "ZN": FutureSpec("ZN", "CBOT", "ZN", "USD", N_("10letá US Treasury Note"), 1000, 0.015625, "HMUZ", 25, ("TNOTE", "TREASURY")),
    "ZB": FutureSpec("ZB", "CBOT", "ZB", "USD", "US Treasury Bond", 1000, 0.03125, "HMUZ", 25, ("TBOND", "TREASURY")),
    "FGBL": FutureSpec("GBL", "EUREX", "FGBL", "EUR", "Euro Bund", 1000, 0.01, "HMUZ", 2, ("BUND",)),
    # Zemědělské komodity
    "ZC": FutureSpec("ZC", "CBOT", "ZC", "USD", N_("Kukuřice"), 5000, 0.0025, "HKNUZ", 18, ("CORN", "KUKURICE")),
    "ZS": FutureSpec("ZS", "CBOT", "ZS", "USD", N_("Sója"), 5000, 0.0025, "FHKNQUX", 18, ("SOYBEAN", "SOJA")),
    "ZW": FutureSpec("ZW", "CBOT", "ZW", "USD", N_("Pšenice"), 5000, 0.0025, "HKNUZ", 18, ("WHEAT", "PSENICE")),
    # Měny
    "6E": FutureSpec("EUR", "CME", "6E", "USD", "Euro FX", 125000, 0.00005, "HMUZ", 7, ("EURUSD", "EURO")),
    "6B": FutureSpec("GBP", "CME", "6B", "USD", N_("Britská libra"), 62500, 0.0001, "HMUZ", 7, ("GBPUSD", "POUND")),
    "6J": FutureSpec("JPY", "CME", "6J", "USD", N_("Japonský jen"), 12500000, 0.0000005, "HMUZ", 7, ("USDJPY", "YEN")),
    "6A": FutureSpec("AUD", "CME", "6A", "USD", N_("Australský dolar"), 100000, 0.00005, "HMUZ", 7, ("AUDUSD",)),
    "6C": FutureSpec("CAD", "CME", "6C", "USD", N_("Kanadský dolar"), 100000, 0.00005, "HMUZ", 7, ("USDCAD",)),
    "6S": FutureSpec("CHF", "CME", "6S", "USD", N_("Švýcarský frank"), 125000, 0.00005, "HMUZ", 7, ("USDCHF",)),
    # Krypto (CME)
    "BTC": FutureSpec("BRR", "CME", "BTC", "USD", "Bitcoin (CME)", 5, 5.0, "", 1, ("BITCOIN",)),
    "MBT": FutureSpec("MBT", "CME", "MBT", "USD", "Micro Bitcoin (CME)", 0.1, 5.0, "", 1, ("BITCOIN",)),
    "ETH": FutureSpec("ETHUSDRR", "CME", "ETH", "USD", "Ether (CME)", 50, 0.5, "", 1, ("ETHER", "ETHEREUM")),
    "MET": FutureSpec("MET", "CME", "MET", "USD", "Micro Ether (CME)", 0.1, 0.5, "", 1, ("ETHER", "ETHEREUM")),
}


def continuous_symbol(key: str) -> str:
    """Symbol aplikace pro klíč tabulky FUTURES: "ES" → "ES1!"."""
    return key + CONTINUOUS_SUFFIX


def generic_symbol(root: str, exchange: str) -> str:
    """Symbol obecného kontinuálního futures mimo tabulku: ("VIX", "CFE") → "VIX1!@CFE"."""
    return f"{root}{CONTINUOUS_SUFFIX}@{exchange}"


def future_spec(symbol: str) -> Optional[FutureSpec]:
    """Popis futures podle symbolu ("ES1!" z tabulky, "VIX1!@CFE" obecně); jiný symbol → None."""
    head, at, exchange = symbol.partition("@")
    if not head.endswith(CONTINUOUS_SUFFIX):
        return None
    key = head[: -len(CONTINUOUS_SUFFIX)]
    if not key:
        return None
    if not at:
        return FUTURES.get(key)
    if not exchange:
        return None
    # Obecný symbol: měna podle burzy (zpřesní ji detaily kontraktu z TWS),
    # obchodní třída se vybere z řetězce kontraktů
    return FutureSpec(key, exchange, "", EXCHANGE_CURRENCIES.get(exchange, "USD"), key)


def future_suggestions(query: str) -> list[str]:
    """Futures z tabulky odpovídající dotazu: přesný kořen ("ES", "ES1!", "6E"),
    od tří znaků i začátek klíče, kořene TWS nebo názvu ("gold", "nasdaq", "DAX")."""
    text = "".join(ch for ch in query.upper() if ch.isalnum() or ch == "&")
    # "ES1!" i "ES1" znamenají totéž co "ES"
    if text.endswith("1") and text[:-1] in FUTURES:
        text = text[:-1]
    if not text:
        return []
    matches: list[str] = []
    for key, spec in FUTURES.items():
        names = (key, spec.root, *spec.aliases)
        if text in names or (len(text) >= 3 and any(name.startswith(text) for name in names)):
            matches.append(continuous_symbol(key))
    return matches


@dataclass(frozen=True)
class FutureLeg:
    """Jeden kontrakt řetězce kontinuálního futures.

    Atributy:
        contract: kontrakt TWS (s includeExpired, aby šla stáhnout i historie expirovaného).
        local_symbol: místní symbol kontraktu ("ESZ6").
        last_trade: poslední obchodní den "YYYYMMDD".
        roll_ms: od kdy (UTC ms) je front month už následující kontrakt.
        multiplier: hodnota bodu podle TWS (0 = neznámá).
        min_tick: nejmenší změna ceny podle TWS (0 = neznámá).
        long_name: název podkladu podle TWS.
        currency: měna kontraktu.
    """

    contract: Any
    local_symbol: str
    last_trade: str
    roll_ms: int
    multiplier: float
    min_tick: float
    long_name: str
    currency: str


def roll_time_ms(last_trade: str, roll_days: int) -> int:
    """Okamžik rollu: půlnoc UTC posledního obchodního dne mínus roll_days dní (v ms).

    Parametry:
        last_trade: poslední obchodní den "YYYYMMDD" (případně s časem za mezerou).
        roll_days: o kolik dní dřív se přechází na další kontrakt.
    """
    day = datetime.strptime(last_trade[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    return int((day - timedelta(days=roll_days)).timestamp() * 1000)


def _float(value) -> float:
    # Číslo z detailů TWS (multiplikátor je řetězec, chybějící hodnota prázdná)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_chain(details: list, spec: FutureSpec) -> list[FutureLeg]:
    """Seřazený řetězec kontraktů kontinuální řady z výsledku reqContractDetails.

    Ponechá jen obchodní třídu spec (u obecného symbolu třídu shodnou s kořenem,
    jinak tu s nejvíce kontrakty), jen měsíce spec.months a od každého
    posledního obchodního dne jen jeden kontrakt.

    Parametry:
        details: seznam ContractDetails z TWS (i s expirovanými kontrakty).
        spec: popis futures.
    """
    usable = [d for d in details if getattr(d.contract, "lastTradeDateOrContractMonth", "")[:8].isdigit()]
    # Obchodní třída: pevná z tabulky, u obecného symbolu shodná s kořenem,
    # jinak nejčastější (např. VIX: měsíční VX místo týdenních VX47…)
    trading_class = spec.trading_class
    classes = [d.contract.tradingClass for d in usable]
    if not trading_class and classes:
        trading_class = spec.root if spec.root in classes else max(set(classes), key=classes.count)
    if trading_class:
        usable = [d for d in usable if d.contract.tradingClass == trading_class]
    # Měsíce kontinuální řady podle měsíce kontraktu (ne posledního obchodního
    # dne – např. CLZ6 končí už v listopadu)
    if spec.months:
        usable = [d for d in usable if _month_code(d) in spec.months]
    legs: dict[str, FutureLeg] = {}
    for detail in usable:
        contract = detail.contract
        last_trade = contract.lastTradeDateOrContractMonth[:8]
        if last_trade in legs:
            continue
        # Kopie s includeExpired (dataclasses.replace neumí podtřídy jako Future,
        # jejichž __init__ má jiné parametry)
        leg_contract = copy.copy(contract)
        leg_contract.includeExpired = True
        legs[last_trade] = FutureLeg(
            contract=leg_contract,
            local_symbol=contract.localSymbol or contract.symbol,
            last_trade=last_trade,
            roll_ms=roll_time_ms(last_trade, spec.roll_days),
            multiplier=_float(contract.multiplier),
            min_tick=_float(getattr(detail, "minTick", 0)),
            long_name=(getattr(detail, "longName", "") or "").strip(),
            currency=contract.currency or spec.currency,
        )
    return sorted(legs.values(), key=lambda leg: leg.last_trade)


def _month_code(detail) -> str:
    # Kód měsíce kontraktu (Z = prosinec) z contractMonth "YYYYMM",
    # bez něj z posledního obchodního dne
    month = (getattr(detail, "contractMonth", "") or detail.contract.lastTradeDateOrContractMonth)[4:6]
    return MONTH_CODES[int(month) - 1] if month.isdigit() and 1 <= int(month) <= 12 else ""


def front_leg(chain: list[FutureLeg], time_ms: int) -> Optional[FutureLeg]:
    """Kontrakt, který je v daném čase front month (první s rollem po time_ms); None = řetězec skončil."""
    return next((leg for leg in chain if leg.roll_ms > time_ms), None)


def leg_segments(chain: list[FutureLeg], start_ms: int, end_ms: int) -> list[tuple[FutureLeg, int, int]]:
    """Rozdělí interval [start_ms, end_ms] na úseky podle front month kontraktu.

    Kontrakt i je front month v [roll(i-1), roll(i)); první kontrakt řetězce
    pokrývá stejně dlouhé období před svým rollem jako následující kontrakt
    (starší kontrakty TWS nezná, data z doby před nimi se nevrací).

    Vrací seznam (kontrakt, od ms, do ms) vzestupně; konec úseku je včetně.
    """
    segments: list[tuple[FutureLeg, int, int]] = []
    for index, leg in enumerate(chain):
        if index:
            leg_start = chain[index - 1].roll_ms
        elif len(chain) > 1:
            leg_start = leg.roll_ms - (chain[1].roll_ms - leg.roll_ms)
        else:
            leg_start = leg.roll_ms - FIRST_LEG_SPAN_DAYS * TIMEFRAME_MS["1D"]
        seg_start, seg_end = max(start_ms, leg_start), min(end_ms, leg.roll_ms - 1)
        if seg_start <= seg_end:
            segments.append((leg, seg_start, seg_end))
    return segments
