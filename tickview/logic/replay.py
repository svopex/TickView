"""Logická vrstva replaye (backtestování ručních obchodů jako ve FX Replay).

Engine je čistý a deterministický: stav účtu (příkazy, obchody, zůstatek)
je funkcí základních svíček (base timeframe) a seznamu příkazů uživatele
s časem, kdy byly zadány. Díky tomu jde session kdykoli obnovit (po
restartu) i vrátit v čase – příkazy po bodu návratu se zahodí a stav se
přepočítá od začátku session.

Časy jsou v ms UTC; „čas příkazu“ je otevření poslední odkryté základní
svíčky (kurzor replaye) – příkaz se provede za závěrečnou cenu této svíčky
a od další svíčky se vyhodnocuje.
"""

import bisect
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Callable, Optional

from tickview.logic.sessions import parse_hhmm
from tickview.logic.styles import clean_name
from tickview.providers.base import TIMEFRAME_MS, Candle

# Druhy příkazů (vstup do obchodu) a jejich popisky v UI
ORDER_TYPES = {"market": "Market", "limit": "Limit", "stop": "Stop"}
# Strany příkazu: nákup otevírá long, prodej short
SIDES = {"buy": "Buy", "sell": "Sell"}
# Režimy zadání velikosti pozice: loty / kontrakty, % zůstatku účtu, částka rizika
QTY_MODES = {"lots": "Loty / kontrakty", "risk_pct": "Riziko % účtu", "risk_amount": "Riziko částkou"}
# Důvody uzavření (části) obchodu
EXIT_REASONS = {"sl": "SL", "tp": "TP", "manual": "Ručně", "partial": "Částečně"}
# Chování kroku zpět v čase (Settings aplikace → Replay)
REWIND_MODES = {
    "delete": "Povolit – obchody po bodu návratu se smažou",
    "confirm": "Povolit po potvrzení – zeptat se před smazáním obchodů",
    "off": "Zakázat krok zpět",
}
DEFAULT_REWIND_MODE = "delete"
# Rychlosti přehrávání (základních svíček za sekundu)
SPEEDS = (1, 2, 5, 10, 25, 50, 100)
DEFAULT_SPEED = 5
# Krok tlačítka Step: jedna svíčka grafu aktivního grafu, nebo jedna základní svíčka
STEP_UNITS = {"chart": "1 bar grafu", "base": "1 bar základu"}
# Kandidáti základního timeframu od nejjemnějšího (automatický výběr podle dostupnosti dat)
BASE_TIMEFRAME_CANDIDATES = ("1m", "5m", "15m", "1h", "1D")
# Měny účtu nabízené v UI (převod P&L z měny instrumentu)
ACCOUNT_CURRENCIES = ("USD", "EUR", "CZK", "GBP", "CHF", "JPY", "PLN", "HUF", "CAD", "AUD")
# Stablecoiny kotované na kryptoburzách se pro převod berou jako dolar
STABLECOINS = {"USDT": "USD", "USDC": "USD", "BUSD": "USD", "FDUSD": "USD", "TUSD": "USD", "DAI": "USD"}
# Výchozí nastavení nové session (hodnoty dialogu Nová session)
DEFAULT_SETTINGS = {
    # Měna účtu a počáteční zůstatek
    "account_currency": "USD",
    "initial_balance": 10000.0,
    # Jednotek instrumentu v jednom lotu (forex 100 000, akcie / krypto 1)
    "contract_size": 1.0,
    # Nejmenší krok velikosti pozice v lotech
    "lot_step": 0.01,
    # Nejmenší změna ceny instrumentu (spread a skluz se zadávají v tickách)
    "tick": 0.01,
    "spread_ticks": 0.0,
    "slippage_ticks": 0.0,
    # Komise v měně účtu za lot a stranu (vstup i výstup)
    "commission_per_lot": 0.0,
    # Pevný kurz měny instrumentu → měna účtu (0 = automaticky z historie)
    "fixed_rate": 0.0,
    # Automatická pauza přehrávání při naplnění příkazu, uzavření obchodu a signálu
    # indikátoru (výchozí vypnutá – přehrávání běží dál)
    "pause_on_fill": False,
    "pause_on_exit": False,
    "pause_on_signal": False,
    # Čas dne pro skok „na další den“ (místní čas, HH:MM) – např. začátek obchodování
    "jump_time": "09:00",
}


def _finite(value) -> Optional[float]:
    """Hodnota jako konečné číslo, jinak None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def non_negative(value, default: float) -> float:
    """Konečné nezáporné číslo, jinak výchozí hodnota (spread, komise – nula je platná)."""
    number = _finite(value)
    return number if number is not None and number >= 0 else default


def positive_number(value, default: Optional[float] = None) -> Optional[float]:
    """Konečné kladné číslo, jinak výchozí hodnota (prázdné pole / nula = default)."""
    number = _finite(value)
    return number if number is not None and number > 0 else default


def default_contract_size(kind: str) -> float:
    """Výchozí velikost lotu podle druhu instrumentu (forex 100 000 jednotek, jinak 1)."""
    return 100_000.0 if kind == "forex" else 1.0


def normalize_settings(data, kind: str = "", contract_size: Optional[float] = None) -> dict:
    """Zkontroluje nastavení session; chybějící a neplatné hodnoty doplní výchozími.

    Parametry:
        data: uložené / zadané nastavení (dict nebo cokoli jiného).
        kind: druh instrumentu (výchozí velikost lotu u forexu, u futures krok
              velikosti pozice 1 = celé kontrakty).
        contract_size: výchozí velikost kontraktu od zdroje (násobitel futures);
                       None = podle druhu instrumentu.
    """
    data = data if isinstance(data, dict) else {}
    settings = dict(DEFAULT_SETTINGS)
    settings["contract_size"] = contract_size or default_contract_size(kind)
    if kind == "futures":
        settings["lot_step"] = 1.0
    currency = str(data.get("account_currency") or "").upper()
    if len(currency) == 3 and currency.isalpha():
        settings["account_currency"] = currency
    # Číselné hodnoty: zůstatek, lot a tick musí být kladné, náklady nezáporné
    for key in ("initial_balance", "contract_size", "lot_step", "tick"):
        settings[key] = positive_number(data.get(key), settings[key])
    for key in ("spread_ticks", "slippage_ticks", "commission_per_lot", "fixed_rate"):
        settings[key] = non_negative(data.get(key), settings[key])
    for key in ("pause_on_fill", "pause_on_exit", "pause_on_signal"):
        if key in data:
            settings[key] = bool(data[key])
    settings["jump_time"] = clean_time_of_day(data.get("jump_time")) or settings["jump_time"]
    return settings


def clean_time_of_day(text) -> Optional[str]:
    """Čas dne „H:MM“ / „HH:MM“ (i s vteřinami z pole type=time) → „HH:MM“; neplatný = None."""
    minutes = parse_hhmm(text)
    return None if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"


def time_of_day_stopper(after_ms: int, time_of_day: str) -> Callable[[int], bool]:
    """Podmínka zastavení skoku na nejbližší místní čas dne `time_of_day` („HH:MM“).

    Cílem je nejbližší výskyt času ostře po `after_ms` (replay přesně na tomto
    čase skočí na další den). Svíčky před cílem se odkrývají. Když další svíčka
    padne do dne cíle (v cíli nebo po něm – trh otevírá později), skok končí
    před ní. Když cílový den nemá data (víkend, svátek), cíl se přesune na
    stejný čas dne svíčky; víkendový den po mezeře v datech (forex otevírá
    v neděli večer) se přeskočí na pondělí.

    Parametry:
        after_ms: aktuální čas replaye (ms UTC).
        time_of_day: čas dne „HH:MM“ (místní čas).
    Vrací funkci (otevření další svíčky v ms) → True = tu už neodkrývat.
    """
    minutes = parse_hhmm(time_of_day)
    at = dt_time(minutes // 60, minutes % 60)

    def target_on(day: date) -> int:
        # Čas dne v místním dni → ms UTC
        return int(datetime.combine(day, at).timestamp() * 1000)

    today = datetime.fromtimestamp(after_ms / 1000).date()
    target = target_on(today)
    if target <= after_ms:
        target = target_on(today + timedelta(days=1))

    def stop(bar_ms: int) -> bool:
        nonlocal target
        if bar_ms < target:
            return False
        bar_day = datetime.fromtimestamp(bar_ms / 1000).date()
        if bar_day == datetime.fromtimestamp(target / 1000).date():
            return True
        # Cílový den bez dat: cíl se posune na den svíčky
        target = target_on(bar_day)
        if bar_ms < target:
            return False
        if bar_day.weekday() >= 5:
            # Víkendová svíčka po mezeře (nedělní otevření forexu) – cílem je pondělí
            target = target_on(bar_day + timedelta(days=7 - bar_day.weekday()))
            return False
        return True

    return stop


def clean_session_name(name, fallback: str) -> str:
    """Název session (clean_name – mezery, délka); prázdný nebo příliš dlouhý = fallback."""
    return clean_name(name) or fallback


def round_to_step(value: float, step: float) -> float:
    """Zaokrouhlí hodnotu dolů na násobek kroku (velikost pozice podle rizika nesmí riziko překročit)."""
    if step <= 0:
        return value
    # Malá tolerance proti chybám binární reprezentace (0.3 / 0.1 = 2.9999…)
    steps = math.floor(value / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(steps * step, decimals + 2)


def round_price(price: float, tick: float) -> float:
    """Zaokrouhlí cenu na celé ticky instrumentu (bez šumu binární reprezentace)."""
    if tick <= 0:
        return price
    decimals = max(0, -int(math.floor(math.log10(tick)))) + 1
    return round(round(price / tick) * tick, decimals)


def position_size(
    mode: str, value: float, entry: float, stop: Optional[float], settings: dict, balance: float, rate: float
) -> float:
    """Velikost pozice v lotech podle režimu zadání.

    Parametry:
        mode: "lots" (value = loty), "risk_pct" (value = % zůstatku) nebo
            "risk_amount" (value = riziko v měně účtu).
        value: zadaná hodnota.
        entry, stop: cena vstupu a stop lossu (režimy rizika bez stopu vrací 0).
        settings: nastavení session (velikost lotu, krok lotu).
        balance: zůstatek účtu (režim %).
        rate: kurz měny instrumentu → měna účtu.
    """
    value = non_negative(value, 0.0)
    if mode == "lots":
        return round_to_step(value, settings["lot_step"])
    if stop is None or rate <= 0:
        return 0.0
    distance = abs(entry - stop)
    if distance <= 0:
        return 0.0
    risk_amount = balance * value / 100.0 if mode == "risk_pct" else value
    # Riziko v měně účtu / (vzdálenost stopu × jednotek v lotu × kurz)
    lots = risk_amount / (distance * settings["contract_size"] * rate)
    return round_to_step(lots, settings["lot_step"])


# ---------- převod měn ----------


def normalize_currency(code: str | None) -> str:
    """Kód měny pro převod: stablecoiny jako USD, prázdný jako USD."""
    code = (code or "USD").strip()
    if code == "GBp":
        # Pence (londýnské akcie) zůstávají rozlišené – převádí se přes GBP / 100
        return code
    code = code.upper()
    return STABLECOINS.get(code, code)


@dataclass
class CurrencyConversion:
    """Kurz měny instrumentu (kotované měny) na měnu účtu v čase replaye.

    Atributy:
        mode: "one" (stejná měna), "inverse" (forexový pár se základní měnou =
            měna účtu: kurz = 1 / cena), "table" (denní kurzy z historie),
            "fixed" (pevný kurz z nastavení session).
        times, rates: denní kurzy (otevření dne v ms, kurz) pro režim "table".
        fixed: pevný kurz (režim "fixed" a záloha, když tabulka chybí).
        divisor: dělitel ceny (pence → libry = 100).
    """

    mode: str = "one"
    times: list[int] = field(default_factory=list)
    rates: list[float] = field(default_factory=list)
    fixed: float = 1.0
    divisor: float = 1.0

    def rate(self, time_ms: int, price: float) -> float:
        """Kurz v čase time_ms (price = aktuální cena instrumentu pro režim "inverse")."""
        if self.mode == "inverse":
            return 1.0 / price / self.divisor if price > 0 else 0.0
        if self.mode == "table" and self.times:
            # Poslední známý denní kurz s otevřením ≤ čas (bez nahlížení do budoucna)
            index = bisect.bisect_right(self.times, time_ms) - 1
            return self.rates[max(0, index)] / self.divisor
        if self.mode == "one":
            return 1.0 / self.divisor
        return self.fixed / self.divisor


def conversion_pairs(quote: str, account: str) -> list[tuple[str, bool]]:
    """Forexové páry Yahoo, ze kterých jde zjistit kurz quote → account.

    Vrací [(symbol, inverze)]: přímý pár QUOTEACCOUNT=X a obrácený
    ACCOUNTQUOTE=X (kurz = 1 / cena).
    """
    quote = "GBP" if quote == "GBp" else quote
    return [(f"{quote}{account}=X", False), (f"{account}{quote}=X", True)]


def plan_conversion(base: str | None, quote: str, account: str, fixed_rate: float) -> CurrencyConversion:
    """Určí způsob převodu bez stahování dat (tabulku kurzů doplní služba replaye).

    Parametry:
        base: základní měna forexového páru (None u ostatních instrumentů).
        quote: kotovaná měna instrumentu (měna ceny).
        account: měna účtu.
        fixed_rate: pevný kurz z nastavení session (0 = automaticky).
    """
    quote = normalize_currency(quote)
    account = normalize_currency(account)
    divisor = 100.0 if quote == "GBp" else 1.0
    if fixed_rate > 0:
        return CurrencyConversion("fixed", fixed=fixed_rate, divisor=divisor)
    if (quote == "GBp" and account == "GBP") or quote == account:
        return CurrencyConversion("one", divisor=divisor)
    if base and normalize_currency(base) == account:
        # USDJPY na dolarovém účtu: 1 JPY = 1 / cena USD
        return CurrencyConversion("inverse")
    return CurrencyConversion("table", divisor=divisor)


# ---------- příkazy, obchody a engine ----------


@dataclass
class Order:
    """Příkaz ke vstupu do obchodu.

    Atributy:
        id: pořadové číslo (obchod z příkazu má stejné id).
        side: "buy" / "sell".
        type: "market" / "limit" / "stop".
        qty: velikost v lotech.
        price: cena limitu / stopu (u market cena naplnění).
        sl, tp: stop loss a take profit (None = bez).
        created: čas zadání (ms).
        expires: čas expirace čekajícího příkazu (None = GTC).
        status: "pending" / "filled" / "cancelled" / "expired".
        filled: čas naplnění (ms) nebo None.
    """

    id: int
    side: str
    type: str
    qty: float
    price: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    created: int
    expires: Optional[int] = None
    status: str = "pending"
    filled: Optional[int] = None


@dataclass
class Exit:
    """Uzavření (části) obchodu: čas, cena, loty, důvod, P&L a komise v měně účtu."""

    time: int
    price: float
    qty: float
    reason: str
    pnl: float
    commission: float


@dataclass
class Trade:
    """Obchod (pozice) vzniklý naplněním příkazu.

    Atributy:
        id: id příkazu, ze kterého vznikl.
        side: "buy" = long, "sell" = short.
        entry_type: druh vstupního příkazu (market / limit / stop).
        qty: počáteční velikost v lotech; open_qty = dosud neuzavřená část.
        entry_price, entry_time: naplnění vstupu.
        sl, tp: aktuální úrovně; initial_sl = stop při vstupu (riziko pro R).
        risk: riziko při vstupu v měně účtu (0 = bez stopu).
        entry_commission: komise za vstup.
        exits: uzavřené části.
        mae, mfe: největší pohyb proti obchodu / ve prospěch (cenový rozdíl).
        ambiguous: výsledek je odhad (stop i cíl v jedné základní svíčce).
    """

    id: int
    side: str
    entry_type: str
    qty: float
    open_qty: float
    entry_price: float
    entry_time: int
    sl: Optional[float]
    tp: Optional[float]
    initial_sl: Optional[float]
    risk: float
    entry_commission: float
    exits: list[Exit] = field(default_factory=list)
    mae: float = 0.0
    mfe: float = 0.0
    ambiguous: bool = False

    @property
    def long(self) -> bool:
        return self.side == "buy"

    @property
    def closed(self) -> bool:
        return self.open_qty <= 1e-12

    @property
    def commissions(self) -> float:
        """Komise za vstup i všechny výstupy v měně účtu."""
        return self.entry_commission + sum(item.commission for item in self.exits)

    @property
    def realized(self) -> float:
        """Realizovaný P&L po komisích (vstup + výstupy) v měně účtu."""
        return sum(item.pnl for item in self.exits) - self.commissions

    @property
    def exit_time(self) -> Optional[int]:
        return self.exits[-1].time if self.exits else None

    @property
    def exit_price(self) -> Optional[float]:
        """Průměrná cena výstupu vážená loty (None bez výstupu)."""
        total = sum(item.qty for item in self.exits)
        return sum(item.price * item.qty for item in self.exits) / total if total else None

    @property
    def r_multiple(self) -> Optional[float]:
        """Výsledek v násobcích počátečního rizika (None bez stopu)."""
        return self.realized / self.risk if self.risk > 0 else None


@dataclass
class EngineEvent:
    """Událost enginu pro UI a automatickou pauzu: "fill", "exit", "expired", "rejected"."""

    type: str
    trade_id: int
    message: str


class ReplayEngine:
    """Simulace účtu nad základními svíčkami a příkazy uživatele.

    Parametry:
        settings: nastavení session (normalize_settings).
        conversion: převod měny instrumentu na měnu účtu.
    """

    def __init__(self, settings: dict, conversion: Optional[CurrencyConversion] = None) -> None:
        self.settings = settings
        self.conversion = conversion or CurrencyConversion()
        self.balance = float(settings["initial_balance"])
        self.orders: dict[int, Order] = {}
        self.trades: dict[int, Trade] = {}
        # Čekající příkazy a otevřené obchody zvlášť (vložené podle id – bez
        # řazení a procházení uzavřených na každé svíčce)
        self.pending: dict[int, Order] = {}
        self.open: dict[int, Trade] = {}
        self.next_id = 1
        # Zvyšuje se při každé změně příkazů a obchodů (UI překresluje jen při změně)
        self.revision = 0
        # Poslední zpracovaná základní svíčka (aktuální cena a čas kurzoru)
        self.last: Optional[Candle] = None
        # Události od posledního vyzvednutí (pop_events)
        self.events: list[EngineEvent] = []

    # ----- ceny a převody -----

    @property
    def spread(self) -> float:
        return self.settings["spread_ticks"] * self.settings["tick"]

    @property
    def slippage(self) -> float:
        return self.settings["slippage_ticks"] * self.settings["tick"]

    @property
    def now(self) -> Optional[int]:
        """Čas kurzoru (otevření poslední zpracované svíčky)."""
        return self.last.open_time if self.last else None

    def bid(self) -> Optional[float]:
        """Prodejní cena (data = bid, nebo střed u zdrojů bez bid/ask)."""
        return self.last.close if self.last else None

    def ask(self) -> Optional[float]:
        """Nákupní cena = bid + spread."""
        return self.last.close + self.spread if self.last else None

    def rate(self, time_ms: int, price: float) -> float:
        """Kurz měny instrumentu na měnu účtu v daném čase."""
        return self.conversion.rate(time_ms, price)

    def units(self, lots: float) -> float:
        """Počet jednotek instrumentu v lotech."""
        return lots * self.settings["contract_size"]

    def commission(self, lots: float) -> float:
        """Komise za jednu stranu obchodu v měně účtu."""
        return self.settings["commission_per_lot"] * lots

    def pop_events(self) -> list[EngineEvent]:
        """Vyzvedne události od posledního volání."""
        events, self.events = self.events, []
        return events

    # ----- plnění příkazů -----

    def _trigger(self, side: str, kind: str, level: float, candle: Candle) -> Optional[float]:
        """Cena naplnění limitu / stopu ve svíčce, nebo None, když se nenaplní.

        Buy se plní na asku (bid + spread), sell na bidu. Gap přes úroveň
        (svíčka otevře za ní) plní za otevření – u limitu lepší, u stopu
        horší cenou; stop navíc dostane skluz.
        """
        spread = self.spread
        if side == "buy":
            open_, low, high = candle.open + spread, candle.low + spread, candle.high + spread
            if kind == "limit":
                return min(level, open_) if low <= level else None
            return max(level, open_) + self.slippage if high >= level else None
        open_, low, high = candle.open, candle.low, candle.high
        if kind == "limit":
            return max(level, open_) if high >= level else None
        return min(level, open_) - self.slippage if low <= level else None

    def _open_trade(self, order: Order, price: float, time_ms: int) -> Trade:
        """Naplní příkaz: vytvoří obchod a strhne komisi za vstup."""
        price = round_price(price, self.settings["tick"] / 10)
        order.status = "filled"
        order.filled = time_ms
        order.price = price
        rate = self.rate(time_ms, price)
        risk = abs(price - order.sl) * self.units(order.qty) * rate if order.sl is not None else 0.0
        commission = self.commission(order.qty)
        trade = Trade(
            id=order.id, side=order.side, entry_type=order.type, qty=order.qty, open_qty=order.qty,
            entry_price=price, entry_time=time_ms, sl=order.sl, tp=order.tp, initial_sl=order.sl,
            risk=risk, entry_commission=commission,
        )
        self.trades[trade.id] = trade
        self.open[trade.id] = trade
        self.pending.pop(order.id, None)
        self.revision += 1
        self.balance -= commission
        direction = "Long" if trade.long else "Short"
        self.events.append(EngineEvent("fill", trade.id, f"#{trade.id} {direction} {order.qty:g} naplněn za {price:g}"))
        return trade

    def _close(self, trade: Trade, price: float, qty: float, time_ms: int, reason: str) -> None:
        """Uzavře část obchodu: P&L převedený na měnu účtu, komise, zůstatek."""
        qty = min(qty, trade.open_qty)
        if qty <= 0:
            return
        price = round_price(price, self.settings["tick"] / 10)
        diff = price - trade.entry_price if trade.long else trade.entry_price - price
        pnl = diff * self.units(qty) * self.rate(time_ms, price)
        commission = self.commission(qty)
        trade.exits.append(Exit(time_ms, price, qty, reason, pnl, commission))
        trade.open_qty = round(trade.open_qty - qty, 10)
        self.balance += pnl - commission
        self.revision += 1
        if trade.closed:
            self.open.pop(trade.id, None)
            label = EXIT_REASONS.get(reason, reason)
            self.events.append(
                EngineEvent("exit", trade.id, f"#{trade.id} uzavřen ({label}) za {price:g}, P&L {trade.realized:+.2f}")
            )

    def _check_exits(self, trade: Trade, candle: Candle, entry_bar: bool) -> None:
        """Zásah stopu / cíle obchodu ve svíčce (konzervativně stop před cílem).

        Stop i cíl v jedné svíčce = stop a obchod se označí jako odhad.
        Ve svíčce vstupu se cíl nebere (pořadí pohybů ve svíčce neznáme),
        stop ano – opět jako odhad.
        """
        exit_side = "sell" if trade.long else "buy"
        stop = self._trigger(exit_side, "stop", trade.sl, candle) if trade.sl is not None else None
        target = self._trigger(exit_side, "limit", trade.tp, candle) if trade.tp is not None and not entry_bar else None
        if stop is not None:
            if target is not None or entry_bar:
                trade.ambiguous = True
            self._close(trade, stop, trade.open_qty, candle.open_time, "sl")
        elif target is not None:
            self._close(trade, target, trade.open_qty, candle.open_time, "tp")

    def _track_excursion(self, trade: Trade, candle: Candle) -> None:
        """MAE / MFE: nejhorší a nejlepší cena svíčky proti vstupu."""
        if trade.long:
            favorable, adverse = candle.high - trade.entry_price, trade.entry_price - candle.low
        else:
            favorable = trade.entry_price - (candle.low + self.spread)
            adverse = candle.high + self.spread - trade.entry_price
        trade.mfe = max(trade.mfe, favorable)
        trade.mae = max(trade.mae, adverse)

    def process(self, candle: Candle) -> None:
        """Zpracuje další základní svíčku: expirace a naplnění příkazů, stopy a cíle, MAE/MFE."""
        # Čekající příkazy v pořadí zadání; expirace platí od svíčky s časem ≥ expirace
        for order in list(self.pending.values()):
            if order.expires is not None and candle.open_time >= order.expires:
                order.status = "expired"
                del self.pending[order.id]
                self.revision += 1
                self.events.append(EngineEvent("expired", order.id, f"Příkaz #{order.id} expiroval"))
                continue
            price = self._trigger(order.side, order.type, order.price, candle)
            if price is not None:
                trade = self._open_trade(order, price, candle.open_time)
                self._track_excursion(trade, candle)
                self._check_exits(trade, candle, entry_bar=True)
        # Otevřené obchody z dřívějších svíček
        for trade in list(self.open.values()):
            if trade.entry_time >= candle.open_time:
                continue
            self._track_excursion(trade, candle)
            self._check_exits(trade, candle, entry_bar=False)
        self.last = candle

    # ----- příkazy uživatele -----

    def apply(self, command: dict) -> None:
        """Provede příkaz uživatele v čase kurzoru (viz commands v ReplaySession).

        Operace: "order" (nový příkaz), "modify" (SL/TP obchodu, cena / SL /
        TP / loty čekajícího příkazu), "cancel" (zrušení čekajícího příkazu),
        "close" (uzavření obchodu nebo jeho části za tržní cenu).
        Neplatný příkaz se ignoruje (do událostí se zapíše "rejected").
        """
        op = command.get("op")
        if self.last is None:
            return
        if op == "order":
            self._place(command)
        elif op == "modify":
            self._modify(command)
        elif op == "cancel":
            order = self.pending.pop(int(command.get("id") or 0), None)
            if order:
                order.status = "cancelled"
                self.revision += 1
        elif op == "close":
            trade = self.open.get(int(command.get("id") or 0))
            if trade:
                qty = positive_number(command.get("qty"), trade.open_qty)
                price = self.bid() if trade.long else self.ask()
                # Market výstup se skluzem proti obchodu
                price = price - self.slippage if trade.long else price + self.slippage
                reason = "partial" if qty < trade.open_qty - 1e-12 else "manual"
                self._close(trade, price, qty, self.last.open_time, reason)

    def _optional_level(self, value) -> Optional[float]:
        # Úroveň SL / TP / ceny: kladné číslo zaokrouhlené na tick, jinak None
        number = positive_number(value)
        return round_price(number, self.settings["tick"]) if number else None

    def _place(self, command: dict) -> None:
        """Nový příkaz; market se naplní hned za aktuální cenu (ask / bid se skluzem)."""
        side = command.get("side")
        kind = command.get("type")
        qty = round_to_step(non_negative(command.get("qty"), 0.0), self.settings["lot_step"])
        if side not in SIDES or kind not in ORDER_TYPES or qty <= 0:
            self.events.append(EngineEvent("rejected", 0, "Neplatný příkaz (strana, druh nebo velikost)"))
            return
        price = self._optional_level(command.get("price"))
        if kind != "market" and price is None:
            self.events.append(EngineEvent("rejected", 0, "Limit / stop příkaz potřebuje cenu"))
            return
        expires = command.get("expires")
        order = Order(
            id=self.next_id, side=side, type=kind, qty=qty, price=price,
            sl=self._optional_level(command.get("sl")), tp=self._optional_level(command.get("tp")),
            created=self.last.open_time,
            expires=int(expires) if expires else None,
        )
        self.next_id += 1
        self.orders[order.id] = order
        self.pending[order.id] = order
        self.revision += 1
        if kind == "market":
            fill = self.ask() + self.slippage if side == "buy" else self.bid() - self.slippage
            self._open_trade(order, fill, self.last.open_time)

    def _modify(self, command: dict) -> None:
        """Změna úrovní: obchod (SL, TP) nebo čekající příkaz (cena, SL, TP, loty)."""
        item_id = int(command.get("id") or 0)
        self.revision += 1
        trade = self.trades.get(item_id)
        if trade is not None:
            if trade.closed:
                return
            if "sl" in command:
                trade.sl = self._optional_level(command["sl"])
            if "tp" in command:
                trade.tp = self._optional_level(command["tp"])
            return
        order = self.pending.get(item_id)
        if order is None:
            return
        if "price" in command and self._optional_level(command["price"]) is not None:
            order.price = self._optional_level(command["price"])
        if "sl" in command:
            order.sl = self._optional_level(command["sl"])
        if "tp" in command:
            order.tp = self._optional_level(command["tp"])
        if "qty" in command:
            qty = round_to_step(non_negative(command["qty"], 0.0), self.settings["lot_step"])
            if qty > 0:
                order.qty = qty

    # ----- přehledy -----

    def floating(self, trade: Trade) -> float:
        """Nerealizovaný P&L otevřené části obchodu v měně účtu (long za bid, short za ask)."""
        if trade.closed or self.last is None:
            return 0.0
        price = self.bid() if trade.long else self.ask()
        diff = price - trade.entry_price if trade.long else trade.entry_price - price
        return diff * self.units(trade.open_qty) * self.rate(self.last.open_time, price)

    def open_pnl(self) -> float:
        """Součet nerealizovaných P&L otevřených obchodů."""
        return sum(self.floating(trade) for trade in self.open.values())

    def equity(self) -> float:
        """Zůstatek + nerealizovaný P&L."""
        return self.balance + self.open_pnl()

    def pending_orders(self) -> list[Order]:
        return list(self.pending.values())


def simulate(
    settings: dict,
    conversion: CurrencyConversion,
    candles: list[Candle],
    commands: list[dict],
    until: Optional[int] = None,
) -> ReplayEngine:
    """Přepočítá stav účtu od začátku session (obnovení, krok zpět).

    Příkazy s časem t se provedou po zpracování svíčky s otevřením t (v pořadí
    seznamu); svíčky po čase until se nezpracují.

    Parametry:
        settings, conversion: nastavení session a převod měny.
        candles: základní svíčky session vzestupně.
        commands: příkazy uživatele [{"t", "op", …}].
        until: čas kurzoru (ms), None = všechny svíčky.
    """
    engine = ReplayEngine(settings, conversion)
    by_time: dict[int, list[dict]] = {}
    for command in commands:
        by_time.setdefault(int(command.get("t", 0)), []).append(command)
    for candle in candles:
        if until is not None and candle.open_time > until:
            break
        engine.process(candle)
        for command in by_time.get(candle.open_time, ()):
            engine.apply(command)
    engine.pop_events()
    return engine


def commands_until(commands: list[dict], time_ms: int) -> list[dict]:
    """Příkazy zadané nejpozději v čase time_ms (krok zpět zahodí pozdější)."""
    return [command for command in commands if int(command.get("t", 0)) <= time_ms]


# ---------- statistiky ----------


def max_drawdown(curve: list[float]) -> tuple[float, float]:
    """Největší pokles křivky od maxima: (částka, procento z maxima)."""
    peak = -math.inf
    worst, worst_pct = 0.0, 0.0
    for value in curve:
        peak = max(peak, value)
        drop = peak - value
        if drop > worst:
            worst = drop
            worst_pct = drop / peak * 100 if peak > 0 else 0.0
    return worst, worst_pct


def trade_stats(trades: list[Trade], initial_balance: float) -> dict:
    """Statistiky uzavřených obchodů (záložka Statistiky).

    Vrací počty, win rate, hrubý zisk / ztrátu, profit factor, průměrnou
    výhru / ztrátu, expectancy (měna i R), max. drawdown realizovaného
    zůstatku, nejdelší sérii ztrát, komise a počet odhadnutých obchodů.
    """
    closed = sorted((trade for trade in trades if trade.closed), key=lambda trade: trade.exit_time or 0)
    results = [trade.realized for trade in closed]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value <= 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    r_values = [trade.r_multiple for trade in closed if trade.r_multiple is not None]
    # Realizovaný zůstatek po každém obchodu (začíná počátečním zůstatkem)
    curve = [initial_balance]
    for value in results:
        curve.append(curve[-1] + value)
    drawdown, drawdown_pct = max_drawdown(curve)
    # Nejdelší série ztrátových obchodů za sebou
    streak = longest = 0
    for value in results:
        streak = streak + 1 if value <= 0 else 0
        longest = max(longest, streak)
    count = len(closed)
    return {
        "count": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / count * 100 if count else 0.0,
        "net": sum(results),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0),
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "largest_win": max(wins) if wins else 0.0,
        "largest_loss": min(losses) if losses else 0.0,
        "expectancy": sum(results) / count if count else 0.0,
        "avg_r": sum(r_values) / len(r_values) if r_values else None,
        "total_r": sum(r_values) if r_values else None,
        "max_drawdown": drawdown,
        "max_drawdown_pct": drawdown_pct,
        "max_losing_streak": longest,
        "commissions": sum(trade.commissions for trade in closed),
        "ambiguous": sum(1 for trade in closed if trade.ambiguous),
        "return_pct": sum(results) / initial_balance * 100 if initial_balance else 0.0,
    }


def equity_curve(trades: list[Trade], initial_balance: float, start_ms: int) -> list[tuple[int, float]]:
    """Realizovaný zůstatek v čase: (čas výstupu, zůstatek) od začátku session."""
    points = [(start_ms, initial_balance)]
    balance = initial_balance
    for trade in sorted((trade for trade in trades if trade.closed), key=lambda trade: trade.exit_time or 0):
        balance += trade.realized
        points.append((trade.exit_time, balance))
    return points


def stats_by_tag(trades: list[Trade], annotations: dict) -> list[dict]:
    """Statistiky uzavřených obchodů podle štítku setupu (bez štítku = „—“)."""
    groups: dict[str, list[Trade]] = {}
    for trade in trades:
        if trade.closed:
            tag = (annotations.get(str(trade.id)) or {}).get("tag") or "—"
            groups.setdefault(tag, []).append(trade)
    rows = []
    for tag, items in sorted(groups.items()):
        stats = trade_stats(items, 0.0)
        rows.append({"tag": tag, **{key: stats[key] for key in ("count", "win_rate", "net", "avg_r")}})
    return rows


# ---------- skládání svíček grafu ze základních svíček ----------


def bucket_start(time_ms: int, anchor: int, step_ms: int) -> int:
    """Začátek svíčky grafu (délky step_ms), do které patří čas time_ms.

    Svíčky grafu jsou zarovnané podle kotvy (otevření známé svíčky grafu
    ze zdroje) – akcie mají hodinové svíčky od :30, denní svíčky začínají
    o půlnoci v pásmu burzy apod.
    """
    return anchor + (time_ms - anchor) // step_ms * step_ms


def merge_into(candle: Optional[Candle], part: Candle, bucket: int) -> Candle:
    """Přidá základní svíčku do rozpracované svíčky grafu (nová svíčka, když candle je None)."""
    if candle is None or candle.open_time != bucket:
        return Candle(bucket, part.open, part.high, part.low, part.close, part.volume)
    return Candle(bucket, candle.open, max(candle.high, part.high), min(candle.low, part.low), part.close,
                  candle.volume + part.volume)


def is_finer(timeframe: str, base_timeframe: str) -> bool:
    """True, když je timeframe jemnější než základní timeframe replaye (nelze ho zobrazit)."""
    return TIMEFRAME_MS[timeframe] < TIMEFRAME_MS[base_timeframe]


# ---------- serializace pro UI a databázi ----------


def trade_dict(trade: Trade, engine: ReplayEngine, annotations: dict) -> dict:
    """Obchod pro panel a graf: uložené hodnoty, P&L, R a poznámka / štítek."""
    data = asdict(trade)
    note = annotations.get(str(trade.id)) or {}
    data.update(
        long=trade.long,
        closed=trade.closed,
        realized=trade.realized,
        floating=engine.floating(trade),
        exit_time=trade.exit_time,
        exit_price=trade.exit_price,
        r=trade.r_multiple,
        note=note.get("note", ""),
        tag=note.get("tag", ""),
    )
    return data


def order_dict(order: Order) -> dict:
    """Čekající příkaz pro panel a graf."""
    return asdict(order)


def trades_csv(trades: list[Trade], annotations: dict, currency: str, format_time: Callable[[int], str]) -> str:
    """Uzavřené i otevřené obchody jako CSV (oddělovač středník – Excel v češtině).

    Parametry:
        trades: obchody session.
        annotations: poznámky a štítky {id: {"note", "tag"}}.
        currency: měna účtu (záhlaví sloupce P&L).
        format_time: převod času (ms) na text.
    """
    header = [
        "id", "strana", "druh vstupu", "loty", "vstup čas", "vstup cena", "výstup čas", "výstup cena",
        "důvod", "SL", "TP", f"P&L {currency}", "R", "MAE", "MFE", "komise", "odhad", "štítek", "poznámka",
    ]
    rows = [";".join(header)]
    for trade in sorted(trades, key=lambda item: item.id):
        note = annotations.get(str(trade.id)) or {}
        reasons = ",".join(EXIT_REASONS.get(item.reason, item.reason) for item in trade.exits)
        values = [
            trade.id, "long" if trade.long else "short", trade.entry_type, f"{trade.qty:g}",
            format_time(trade.entry_time), f"{trade.entry_price:g}",
            format_time(trade.exit_time) if trade.exit_time else "",
            f"{trade.exit_price:g}" if trade.exit_price is not None else "",
            reasons, "" if trade.initial_sl is None else f"{trade.initial_sl:g}",
            "" if trade.tp is None else f"{trade.tp:g}", f"{trade.realized:.2f}",
            "" if trade.r_multiple is None else f"{trade.r_multiple:.2f}",
            f"{trade.mae:g}", f"{trade.mfe:g}", f"{trade.commissions:.2f}", "ano" if trade.ambiguous else "",
            note.get("tag", ""), note.get("note", ""),
        ]
        # Středník a nový řádek v textu by rozbily CSV – nahradíme je
        rows.append(";".join(str(value).replace(";", ",").replace("\n", " ") for value in values))
    return "\n".join(rows) + "\n"
