"""Služba replaye: data session, přehrávání v čase, příkazy uživatele a ukládání.

Jeden ReplayController patří jedné stránce (klientovi). Drží otevřenou
session, základní svíčky od začátku session až kousek za kurzor (budoucí
svíčky zůstávají jen na serveru – do prohlížeče jdou jen odkryté) a engine
s aktuálním stavem účtu. Grafy stránky si změny vyzvedávají přes callbacky.
"""

import asyncio
import bisect
import copy
import logging
import time
from typing import Awaitable, Callable, Optional

from tickview.indicators.compute import normalize_indicators
from tickview.logic.cache import KlineCache, fetch_history
from tickview.logic.replay import (
    BASE_TIMEFRAME_CANDIDATES,
    DEFAULT_SPEED,
    SPEEDS,
    CurrencyConversion,
    EngineEvent,
    ReplayEngine,
    bucket_start,
    commands_until,
    conversion_pairs,
    equity_curve,
    normalize_settings,
    order_dict,
    plan_conversion,
    simulate,
    trade_dict,
    trade_stats,
)
from tickview.providers import get_provider
from tickview.providers.base import TIMEFRAME_MS, Candle, DataProvider, is_closed, utc_now_ms

logger = logging.getLogger(__name__)

DAY_MS = TIMEFRAME_MS["1D"]
# Délka jednoho dotazu na základní svíčky podle timeframu (Yahoo vrací 1m
# nejvýš za 8 dní v jednom dotazu, IBKR omezuje délku podle velikosti baru)
CHUNK_SPAN_MS = {
    "1m": 3 * DAY_MS,
    "2m": 5 * DAY_MS,
    "5m": 10 * DAY_MS,
    "10m": 20 * DAY_MS,
    "15m": 30 * DAY_MS,
    "1h": 60 * DAY_MS,
    "4h": 180 * DAY_MS,
    "1D": 3 * 365 * DAY_MS,
    "1W": 10 * 365 * DAY_MS,
}
# Kolik základních svíček za kurzorem držet načtených (přednačítání na pozadí)
AHEAD_BARS = 1500
# Kolik prázdných úseků za sebou (víkend, svátky, výpadek dat) se zkusí, než
# se dál nehledá (jinak by se při chybějících datech stahovalo až do současnosti)
EMPTY_CHUNKS_LIMIT = 12
# Kolik svíček v jednom posunu se ještě do grafů posílá po jedné; víc = graf se přenačte
INCREMENTAL_LIMIT = 400
# Jak často se při přehrávání ukládá kurzor do databáze (sekundy)
SAVE_INTERVAL_S = 5.0
# Nejkratší interval kroku přehrávání (sekundy) – víc svíček za sekundu jde po dávkách
MIN_TICK_S = 0.05



def settings_for(provider: DataProvider, symbol: str, data) -> dict:
    """Nastavení session zkontrolované s výchozími hodnotami instrumentu od zdroje.

    Parametry:
        provider, symbol: instrument session (druh a velikost kontraktu).
        data: uložené / zadané nastavení.
    """
    return normalize_settings(data, provider.symbol_kind(symbol), provider.contract_size(symbol))

class ReplayFeed:
    """Základní svíčky session: od začátku session po kurzor a přednačtené za ním.

    Parametry:
        cache: cache svíček (stahuje se jen to, co v ní chybí).
        provider, symbol, base_tf: zdroj a základní timeframe session.
        transform: úprava úseku svíček (filtr hlavní seance), None = beze změny.
    """

    def __init__(self, cache: KlineCache, provider: DataProvider, symbol: str, base_tf: str, transform=None) -> None:
        self.cache = cache
        self.provider = provider
        self.symbol = symbol
        self.base_tf = base_tf
        self.step_ms = TIMEFRAME_MS[base_tf]
        # Délka jednoho dotazu na zdroj (CHUNK_SPAN_MS)
        self.span = CHUNK_SPAN_MS.get(base_tf, 30 * DAY_MS)
        self.transform = transform
        self.candles: list[Candle] = []
        # Konec staženého úseku (ms, včetně) a příznak, že další data nejsou
        self.loaded_until: Optional[int] = None
        self.exhausted = False
        self._lock = asyncio.Lock()

    async def fetch(self, start_ms: int, end_ms: int) -> list[Candle]:
        """Uzavřené základní svíčky v intervalu (po úsecích CHUNK_SPAN_MS, přes cache)."""
        span = self.span
        now_ms = utc_now_ms()
        result: list[Candle] = []
        cursor = start_ms
        while cursor <= end_ms:
            chunk_end = min(end_ms, cursor + span - 1)
            chunk = await fetch_history(self.provider, self.cache, self.symbol, self.base_tf, cursor, chunk_end)
            # Rozpracovaná svíčka současnosti do replaye nepatří (její závěr ještě není známý)
            chunk = [c for c in chunk if is_closed(c, self.step_ms, now_ms)]
            result.extend(self.transform(chunk) if self.transform else chunk)
            cursor = chunk_end + 1
        return result

    async def load(self, start_ms: int, end_ms: int) -> None:
        """Načte svíčky od začátku session po kurzor (obnovení session)."""
        self.candles = await self.fetch(start_ms, end_ms)
        self.loaded_until = end_ms
        self.exhausted = False

    async def extend(self, index: int, ahead: int = AHEAD_BARS) -> None:
        """Dotáhne svíčky za kurzorem (index), dokud jich není aspoň ahead nebo data nedojdou."""
        async with self._lock:
            span = self.span
            empty = 0
            while not self.exhausted and len(self.candles) - 1 - index < ahead:
                start = (self.loaded_until or 0) + 1
                now_ms = utc_now_ms()
                if start >= now_ms - self.step_ms:
                    # Dorazili jsme do současnosti – další data nejsou
                    self.exhausted = True
                    break
                end = min(start + span - 1, now_ms)
                chunk = await self.fetch(start, end)
                self.loaded_until = end
                last = self.candles[-1].open_time if self.candles else -1
                fresh = [c for c in chunk if c.open_time > last]
                self.candles.extend(fresh)
                if fresh:
                    empty = 0
                else:
                    empty += 1
                    if empty >= EMPTY_CHUNKS_LIMIT:
                        self.exhausted = True

    def index_at(self, time_ms: int) -> int:
        """Index poslední svíčky s otevřením ≤ time_ms (-1 = žádná)."""
        return bisect.bisect_right(self.candles, time_ms, key=lambda candle: candle.open_time) - 1


async def probe_base_timeframe(
    cache: KlineCache, provider: DataProvider, symbol: str, time_ms: int, session_filter=None,
    preferred: str | None = None,
) -> tuple[str | None, Candle | None]:
    """Nejjemnější základní timeframe, pro který má zdroj data v okolí času time_ms.

    Zkouší BASE_TIMEFRAME_CANDIDATES (jen nativní timeframy zdroje, ne
    složené); vrací (timeframe, poslední svíčka s otevřením ≤ time_ms), nebo
    (None, None), když data nejsou.

    Parametry:
        session_filter: funkce(timeframe) → filtr seance pro svíčky (nebo None).
        preferred: timeframe zvolený uživatelem (zkusí se jen ten).
    """
    candidates = [preferred] if preferred else list(BASE_TIMEFRAME_CANDIDATES)
    for timeframe in candidates:
        if timeframe not in provider.timeframes or timeframe in provider.aggregated:
            continue
        feed = ReplayFeed(cache, provider, symbol, timeframe, session_filter(timeframe) if session_filter else None)
        try:
            candles = await feed.fetch(time_ms - feed.span, time_ms)
        except Exception:
            # Zdroj tento timeframe pro dané období nevrací (limit historie) – zkusíme hrubší
            logger.exception("Zjištění základního timeframu %s selhalo", timeframe)
            continue
        candles = [c for c in candles if c.open_time <= time_ms]
        if candles:
            return timeframe, candles[-1]
    return None, None


async def load_conversion(
    cache: KlineCache, provider: DataProvider, symbol: str, settings: dict, start_ms: int
) -> tuple[CurrencyConversion, str]:
    """Převod měny instrumentu na měnu účtu; denní kurzy stáhne z Yahoo Finance.

    Vrací (převod, upozornění); upozornění je neprázdné, když se kurz
    nepodařilo zjistit (P&L pak zůstane v měně instrumentu, kurz 1).
    """
    base, quote = provider.currencies(symbol)
    account = settings["account_currency"]
    conversion = plan_conversion(base, quote, account, settings["fixed_rate"])
    if conversion.mode != "table":
        return conversion, ""
    try:
        yahoo = get_provider("yahoo")
    except KeyError:
        yahoo = None
    if yahoo is not None:
        for pair, inverse in conversion_pairs(quote, account):
            try:
                candles = await fetch_history(yahoo, cache, pair, "1D", start_ms - 10 * DAY_MS, utc_now_ms())
            except Exception:
                logger.exception("Kurz %s se nepodařilo stáhnout", pair)
                continue
            candles = [c for c in candles if c.open > 0]
            if candles:
                conversion.times = [c.open_time for c in candles]
                conversion.rates = [1.0 / c.open if inverse else c.open for c in candles]
                return conversion, ""
    conversion.mode = "fixed"
    conversion.fixed = 1.0
    return conversion, f"Kurz {quote} → {account} se nepodařilo zjistit – P&L je v {quote} (kurz 1)."


class ReplayController:
    """Otevřená session replaye jedné stránky.

    Parametry:
        cache: databáze (sessions, cache svíček).
        on_bars: async callback(bars, reset) – nové odkryté svíčky (reset =
            grafy se mají přenačíst: otevření, krok zpět, velký skok).
        on_state: callback() – změna účtu, příkazů nebo přehrávání (panel, čáry v grafu).
        on_status: callback(zpráva, chyba) – stavová lišta.
    """

    def __init__(
        self,
        cache: KlineCache,
        on_bars: Callable[[list[Candle], bool], Awaitable[None]],
        on_state: Callable[[], None],
        on_status: Callable[[str, bool], None],
    ) -> None:
        self.cache = cache
        self.on_bars = on_bars
        self.on_state = on_state
        self.on_status = on_status
        self.session: Optional[dict] = None
        self.provider: Optional[DataProvider] = None
        self.settings: dict = {}
        self.conversion = CurrencyConversion()
        self.engine: Optional[ReplayEngine] = None
        self.feed: Optional[ReplayFeed] = None
        # Index kurzoru ve feed.candles (poslední odkrytá základní svíčka)
        self.index = -1
        self.playing = False
        self.speed = DEFAULT_SPEED
        self.step_unit = "chart"
        self._play_task: Optional[asyncio.Task] = None
        self._prefetch_task: Optional[asyncio.Task] = None
        self._saved_at = 0.0
        # Statická data grafu (revize enginu, data) – sestaví se jen po změně obchodů
        self._static_cache: Optional[tuple[int, dict]] = None
        # Počet změn poznámek a štítků (panel obchodů se překreslí jen po změně)
        self._notes_revision = 0
        # Zámek řadí posuny kurzoru (přehrávání, krok, skok, příkazy)
        self._lock = asyncio.Lock()

    # ----- vlastnosti otevřené session -----

    @property
    def active(self) -> bool:
        return self.session is not None and self.engine is not None

    @property
    def key(self) -> Optional[str]:
        """Symbol session ve tvaru "zdroj:symbol" (grafy s ním jsou v replayi)."""
        return f"{self.session['source']}:{self.session['symbol']}" if self.session else None

    @property
    def session_id(self) -> Optional[int]:
        return self.session["id"] if self.session else None

    @property
    def base_tf(self) -> str:
        return self.session["base_tf"] if self.session else "1m"

    @property
    def cursor(self) -> int:
        """Otevření poslední odkryté základní svíčky (ms)."""
        return self.feed.candles[self.index].open_time if self.feed and self.index >= 0 else 0

    @property
    def now_ms(self) -> int:
        """Čas replaye = konec poslední odkryté základní svíčky."""
        return self.cursor + TIMEFRAME_MS[self.base_tf]

    @property
    def currency(self) -> str:
        return self.settings.get("account_currency", "USD")

    def revealed(self, start_ms: int, end_ms: int) -> list[Candle]:
        """Odkryté základní svíčky v intervalu (včetně), nejvýš do kurzoru."""
        if not self.feed:
            return []
        end_ms = min(end_ms, self.cursor)
        first = self.feed.index_at(start_ms - 1) + 1
        last = self.feed.index_at(end_ms)
        return self.feed.candles[first:last + 1]

    async def base_candles(self, start_ms: int, end_ms: int) -> list[Candle]:
        """Odkryté základní svíčky v intervalu i před začátkem session (rozpracovaná svíčka grafu)."""
        if not self.feed:
            return []
        end_ms = min(end_ms, self.cursor)
        first_loaded = self.feed.candles[0].open_time if self.feed.candles else end_ms + 1
        before: list[Candle] = []
        if start_ms < first_loaded:
            # Svíčka grafu začala před začátkem session – její začátek dotáhneme z cache
            before = await self.feed.fetch(start_ms, min(end_ms, first_loaded - 1))
        return before + self.revealed(start_ms, end_ms)

    # ----- otevření, založení, zavření -----

    async def create(
        self, name: str, key: str, time_ms: int, settings: dict, session_filter=None, base_tf: str | None = None
    ) -> Optional[int]:
        """Založí session se startem v čase time_ms (poslední odkrytá svíčka ≤ time_ms); vrátí id.

        Parametry:
            name: název session.
            key: symbol "zdroj:symbol".
            time_ms: čas, do kterého je historie odkrytá.
            settings: nastavení účtu (normalize_settings).
            session_filter: funkce(timeframe) → filtr seance pro základní svíčky (nebo None).
            base_tf: základní timeframe (None = nejjemnější dostupný).
        """
        provider_id, _, symbol = key.partition(":")
        provider = get_provider(provider_id)
        timeframe, candle = await probe_base_timeframe(self.cache, provider, symbol, time_ms, session_filter, base_tf)
        if timeframe is None:
            self.on_status(f"{symbol}: pro zvolený čas nejsou dostupná data replaye.", True)
            return None
        normalized = settings_for(provider, symbol, settings)
        return await self.cache.create_replay_session(name, provider_id, symbol, timeframe, candle.open_time, normalized)

    async def open(self, session_id: int, session_filter=None) -> bool:
        """Otevře session: přepočítá stav účtu od začátku session po kurzor.

        Parametry:
            session_id: id session v databázi.
            session_filter: funkce(timeframe) → filtr seance pro základní svíčky (stejný jako u grafů).
        """
        await self.close()
        row = await self.cache.get_replay_session(session_id)
        if row is None:
            self.on_status("Session replaye neexistuje.", True)
            return False
        try:
            provider = get_provider(row["source"])
        except KeyError:
            self.on_status(f"Neznámý zdroj session: {row['source']}", True)
            return False
        settings = settings_for(provider, row["symbol"], row["settings"])
        self.on_status(f"Načítám session {row['name']}…", False)
        transform = session_filter(row["base_tf"]) if session_filter else None
        feed = ReplayFeed(self.cache, provider, row["symbol"], row["base_tf"], transform)
        try:
            await feed.load(row["start_ms"], row["cursor_ms"])
        except Exception as exc:
            self.on_status(f"Data session se nepodařilo načíst: {exc}", True)
            return False
        if not feed.candles:
            self.on_status("Session nemá data (zdroj je nevrátil).", True)
            return False
        conversion, warning = await load_conversion(self.cache, provider, row["symbol"], settings, row["start_ms"])
        index = feed.index_at(row["cursor_ms"])
        self.session = row
        self.provider = provider
        self.settings = settings
        self.conversion = conversion
        self.feed = feed
        self.index = max(0, index)
        await self._resimulate()
        if warning:
            self.on_status(warning, True)
        self._start_prefetch()
        await self.on_bars([], True)
        self.on_state()
        return True

    async def close(self) -> None:
        """Zavře session (uloží kurzor a stav); grafy se vrátí do živého režimu přes on_bars."""
        if not self.active:
            return
        self.pause()
        # Pod zámkem: rozběhnutý posun kurzoru (přehrávání) nejdřív doběhne
        async with self._lock:
            await self.save()
            self.session = None
            self.engine = None
            self.feed = None
            self.index = -1
        await self.on_bars([], True)
        self.on_state()

    async def save(self, full: bool = True) -> None:
        """Uloží kurzor a souhrn do databáze; full = i příkazy, poznámky a nastavení (mění se jen akcí uživatele)."""
        if not self.active:
            return
        self._saved_at = time.monotonic()
        fields = {"cursor_ms": self.cursor, "summary": self.summary()}
        if full:
            fields.update(commands=self.session["commands"], annotations=self.session["annotations"],
                          settings=self.settings)
        await self.cache.update_replay_session(self.session_id, **fields)

    async def _resimulate(self) -> None:
        """Přepočítá stav účtu od začátku session po kurzor (ve vlákně – dlouhá session chvíli trvá)."""
        # Nový engine začíná od revize 0 – statická data grafu se sestaví znovu
        self._static_cache = None
        self.engine = await asyncio.to_thread(
            simulate, self.settings, self.conversion, self.feed.candles, self.session["commands"], self.cursor
        )

    def _snap(self, time_ms: int) -> tuple[int, int]:
        """Index a otevření základní svíčky, na kterou padne čas time_ms (nejvýš začátek session)."""
        index = max(0, self.feed.index_at(time_ms))
        return index, self.feed.candles[index].open_time

    def summary(self) -> dict:
        """Souhrn session pro seznam sessions (zůstatek, equity, počet obchodů, win rate)."""
        engine = self.engine
        stats = self.stats()
        return {
            "balance": engine.balance,
            "equity": engine.equity(),
            "net": engine.balance - self.settings["initial_balance"],
            "trades": stats["count"],
            "win_rate": stats["win_rate"],
            "currency": self.currency,
        }

    # ----- posun v čase -----

    def _start_prefetch(self) -> None:
        # Přednačtení svíček za kurzorem na pozadí (přehrávání nečeká na síť)
        if self._prefetch_task and not self._prefetch_task.done():
            return
        feed = self.feed
        if feed is None or feed.exhausted or len(feed.candles) - 1 - self.index >= AHEAD_BARS // 2:
            return

        async def run() -> None:
            try:
                await feed.extend(self.index)
            except Exception as exc:
                self.on_status(f"Další data replaye se nepodařilo načíst: {exc}", True)

        self._prefetch_task = asyncio.create_task(run())

    async def _next_bar(self) -> Optional[Candle]:
        """Další základní svíčka za kurzorem (počká na stažení, když chybí); None = konec dat."""
        feed = self.feed
        if feed is None:
            return None
        if self.index + 1 >= len(feed.candles):
            try:
                await feed.extend(self.index, ahead=1)
            except Exception as exc:
                self.on_status(f"Další data replaye se nepodařilo načíst: {exc}", True)
                return None
        if self.index + 1 >= len(feed.candles):
            return None
        self._start_prefetch()
        return feed.candles[self.index + 1]

    def _should_pause(self, events: list[EngineEvent]) -> bool:
        # Automatická pauza podle nastavení session (naplnění, uzavření obchodu)
        return any(
            (event.type == "fill" and self.settings["pause_on_fill"])
            or (event.type == "exit" and self.settings["pause_on_exit"])
            for event in events
        )

    async def advance(
        self, max_bars: int, stop: Optional[Callable[[Candle], bool]] = None, honor_pause: bool = True
    ) -> list[Candle]:
        """Odkryje další základní svíčky a zpracuje je v enginu.

        Parametry:
            max_bars: nejvýš tolik svíček.
            stop: podmínka nad další svíčkou – True = tu už neodkrývat.
            honor_pause: automatická pauza po naplnění / uzavření obchodu ukončí posun.
        Vrací odkryté svíčky (grafům je předá on_bars).
        """
        if not self.active:
            return []
        bars: list[Candle] = []
        messages: list[str] = []
        ended = False
        async with self._lock:
            while len(bars) < max_bars:
                bar = await self._next_bar()
                if bar is None or not self.active:
                    ended = bar is None
                    break
                if stop is not None and stop(bar):
                    break
                self.index += 1
                self.engine.process(bar)
                bars.append(bar)
                events = self.engine.pop_events()
                messages.extend(event.message for event in events)
                if honor_pause and self._should_pause(events):
                    self.playing = False
                    break
        if messages:
            self.on_status(" · ".join(messages[-3:]), False)
        if ended and not bars:
            self.playing = False
            self.on_status("Replay došel na konec dostupných dat.", False)
        if bars:
            await self.on_bars(bars, len(bars) > INCREMENTAL_LIMIT)
            if time.monotonic() - self._saved_at >= SAVE_INTERVAL_S or not self.playing:
                await self.save(full=False)
        self.on_state()
        return bars

    async def step(self, anchor: Optional[int], step_ms: int) -> list[Candle]:
        """Krok vpřed: jedna základní svíčka, nebo dokončení další svíčky grafu.

        Parametry:
            anchor: kotva svíček grafu aktivního grafu (None = graf má základní timeframe).
            step_ms: délka svíčky grafu.
        """
        if self.step_unit == "base" or anchor is None or step_ms <= TIMEFRAME_MS[self.base_tf]:
            return await self.advance(1, honor_pause=False)
        first = await self._next_bar()
        if first is None:
            return await self.advance(1)
        target = bucket_start(first.open_time, anchor, step_ms)
        # Odkrýváme, dokud další svíčka patří do stejné svíčky grafu
        return await self.advance(
            1_000_000, stop=lambda bar: bucket_start(bar.open_time, anchor, step_ms) != target, honor_pause=False
        )

    async def jump(self, time_ms: int) -> None:
        """Skok vpřed na čas time_ms (otevřené obchody se vyhodnotí svíčku po svíčce)."""
        if not self.active or time_ms <= self.cursor:
            return
        self.pause()
        self.on_status("Přeskakuji…", False)
        await self.advance(10_000_000, stop=lambda bar: bar.open_time > time_ms, honor_pause=False)

    def trades_after(self, time_ms: int) -> int:
        """Kolik příkazů / obchodů vzniklo po čase time_ms (krok zpět je smaže)."""
        if not self.active:
            return 0
        commands = self.session["commands"]
        return len(commands) - len(commands_until(commands, self._snap(time_ms)[1]))

    async def rewind(self, time_ms: int) -> None:
        """Krok zpět na čas time_ms: příkazy po něm se zahodí a stav účtu se přepočítá."""
        if not self.active or time_ms >= self.cursor:
            return
        self.pause()
        async with self._lock:
            self.index, cursor = self._snap(time_ms)
            commands = commands_until(self.session["commands"], cursor)
            removed = len(self.session["commands"]) - len(commands)
            self.session["commands"] = commands
            await self._resimulate()
            # Poznámky obchodů, které po kroku zpět neexistují, zahodíme
            existing = {str(trade_id) for trade_id in self.engine.trades}
            self.session["annotations"] = {
                key: value for key, value in self.session["annotations"].items() if key in existing
            }
        await self.save()
        await self.on_bars([], True)
        self.on_state()
        suffix = f" – smazáno příkazů: {removed}" if removed else ""
        self.on_status(f"Replay vrácen zpět{suffix}.", False)

    # ----- přehrávání -----

    def play(self) -> None:
        """Spustí přehrávání (rychlost self.speed základních svíček za sekundu)."""
        if not self.active or self.playing:
            return
        self.playing = True
        self._play_task = asyncio.create_task(self._play_loop())
        self.on_state()

    def pause(self) -> None:
        """Pozastaví přehrávání."""
        if self.playing:
            self.playing = False
            self.on_state()

    def set_speed(self, speed: int) -> None:
        """Rychlost přehrávání (hodnota ze SPEEDS)."""
        if speed in SPEEDS:
            self.speed = speed

    async def _play_loop(self) -> None:
        # Každý krok odkryje tolik svíček, kolik odpovídá rychlosti za interval
        # kroku; rychlé přehrávání jde po dávkách (UI by nestíhalo 100 kroků/s)
        try:
            while self.playing and self.active:
                interval = max(MIN_TICK_S, 1.0 / self.speed)
                per_tick = max(1, round(self.speed * interval))
                started = time.monotonic()
                bars = await self.advance(per_tick)
                if not bars:
                    break
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
        except Exception as exc:
            logger.exception("Přehrávání replaye selhalo")
            self.on_status(f"Přehrávání selhalo: {exc}", True)
        finally:
            self.playing = False
            # Kurzor se ukládá v advance; po pauze zvenčí ho uložíme tady
            if self.active and time.monotonic() - self._saved_at > MIN_TICK_S:
                await self.save(full=False)
            self.on_state()

    # ----- příkazy uživatele -----

    async def submit(self, command: dict) -> list[EngineEvent]:
        """Provede příkaz uživatele v čase kurzoru a uloží ho do session.

        Parametry:
            command: {"op": "order" | "modify" | "cancel" | "close", …} (viz ReplayEngine.apply).
        Vrací události enginu (naplnění, odmítnutí).
        """
        if not self.active:
            return []
        async with self._lock:
            command = {**command, "t": self.cursor}
            self.engine.apply(command)
            events = self.engine.pop_events()
            if not any(event.type == "rejected" for event in events):
                self.session["commands"].append(command)
        await self.save()
        self.on_state()
        if events:
            self.on_status(" · ".join(event.message for event in events), any(e.type == "rejected" for e in events))
        return events

    async def annotate(self, trade_id: int, note: str, tag: str) -> None:
        """Poznámka a štítek setupu obchodu (nezávislé na čase – krok zpět je nemaže)."""
        if not self.active:
            return
        self.session["annotations"][str(trade_id)] = {"note": note.strip(), "tag": tag.strip()}
        self._notes_revision += 1
        await self.save()
        self.on_state()

    async def update_settings(self, settings: dict) -> None:
        """Změní nastavení session a přepočítá celou session (náklady, měna, lot platí zpětně)."""
        if not self.active:
            return
        self.pause()
        normalized = settings_for(self.provider, self.session["symbol"], settings)
        warning = ""
        async with self._lock:
            # Kurzy se načítají znovu jen při změně měny účtu nebo pevného kurzu
            if any(normalized[key] != self.settings[key] for key in ("account_currency", "fixed_rate")):
                self.conversion, warning = await load_conversion(
                    self.cache, self.provider, self.session["symbol"], normalized, self.session["start_ms"]
                )
            self.settings = normalized
            await self._resimulate()
        if warning:
            self.on_status(warning, True)
        await self.save()
        self.on_state()

    async def rename(self, name: str) -> None:
        """Přejmenuje otevřenou session."""
        if self.active and name:
            self.session["name"] = name
            await self.cache.update_replay_session(self.session_id, name=name)
            self.on_state()

    # ----- data pro UI -----

    def pane_indicators(self, slot: int, default: list[dict]) -> list[dict]:
        """Indikátory grafu v otevřené session (podle pořadí grafu v rozložení).

        Graf, který do session vstupuje poprvé, převezme kopii své živé sady
        (hned se uloží k session); dál jsou obě sady nezávislé (změny ukládá
        save_pane_indicators).

        Parametry:
            slot: pořadí grafu v rozložení.
            default: živé indikátory grafu (výchozí sada pro novou session).
        """
        stored = self.session["indicators"]
        if str(slot) not in stored:
            stored[str(slot)] = copy.deepcopy(default)
            asyncio.create_task(self.cache.update_replay_session(self.session_id, indicators=stored))
        return normalize_indicators(stored[str(slot)])

    async def save_pane_indicators(self, by_slot: dict[int, list[dict]]) -> None:
        """Uloží indikátory grafů otevřené session ({pořadí grafu: indikátory}); beze změny se nezapisuje.

        Kopie – seznam indikátorů grafu se mění na místě (přidání indikátoru).
        """
        stored = self.session["indicators"]
        dirty = False
        for slot, items in by_slot.items():
            if stored.get(str(slot)) != items:
                stored[str(slot)] = copy.deepcopy(items)
                dirty = True
        if dirty:
            await self.cache.update_replay_session(self.session_id, indicators=stored)

    @property
    def revision(self) -> tuple:
        """Klíč stavu obchodů a poznámek: změní se při každé změně (nový engine, obchod, poznámka)."""
        return id(self.engine), self.engine.revision if self.engine else 0, self._notes_revision

    def rate_now(self) -> float:
        """Aktuální kurz měny instrumentu → měna účtu (velikost pozice podle rizika)."""
        engine = self.engine
        return engine.rate(engine.now, engine.bid()) if engine and engine.last else 1.0

    def trades(self, open_only: bool = False) -> list[dict]:
        """Obchody session jako slovníky (trade_dict); open_only = jen otevřené."""
        engine = self.engine
        annotations = self.session["annotations"]
        source = engine.open if open_only else engine.trades
        return [trade_dict(trade, engine, annotations) for trade in source.values()]

    def orders(self) -> list[dict]:
        """Čekající příkazy."""
        return [order_dict(order) for order in self.engine.pending_orders()]

    def stats(self) -> dict:
        """Statistiky uzavřených obchodů (trade_stats)."""
        return trade_stats(list(self.engine.trades.values()), self.settings["initial_balance"])

    def curve(self) -> list[tuple[int, float]]:
        """Křivka realizovaného zůstatku (equity_curve)."""
        return equity_curve(list(self.engine.trades.values()), self.settings["initial_balance"],
                            self.session["start_ms"])

    def chart_static(self) -> dict:
        """Uzavřené obchody (spojnice vstup → výstup) a šipky vstupů / výstupů; mění se jen s revizí enginu."""
        engine = self.engine
        if self._static_cache and self._static_cache[0] == engine.revision:
            return self._static_cache[1]
        payload = {"closed": [], "markers": []}
        for trade in engine.trades.values():
            entry_s = trade.entry_time // 1000
            payload["markers"].append({"time": entry_s, "side": trade.side, "entry": True,
                                       "text": f"#{trade.id} {'Buy' if trade.long else 'Sell'} {trade.qty:g}"})
            for item in trade.exits:
                payload["markers"].append({"time": item.time // 1000, "side": "sell" if trade.long else "buy",
                                           "entry": False, "text": f"#{trade.id} {item.reason.upper()}"})
            if trade.closed:
                payload["closed"].append({
                    "id": trade.id, "side": trade.side, "entry": trade.entry_price, "entry_time": entry_s,
                    "exit": trade.exit_price, "exit_time": trade.exit_time // 1000, "pnl": trade.realized,
                })
        self._static_cache = (engine.revision, payload)
        return payload

    def chart_payload(self) -> dict:
        """Otevřené obchody a čekající příkazy pro graf (čáry vstupu / SL / TP se štítky) – mění se s cenou.

        value_per_point = změna P&L v měně účtu za pohyb ceny o 1 (štítky
        SL / TP při tažení přepočítává JS).
        """
        engine = self.engine
        rate = self.rate_now()
        units = self.settings["contract_size"]
        payload = {"currency": self.currency, "orders": [], "trades": []}
        for order in engine.pending_orders():
            payload["orders"].append({
                "id": order.id, "side": order.side, "type": order.type, "qty": order.qty, "price": order.price,
                "sl": order.sl, "tp": order.tp, "value_per_point": order.qty * units * rate,
            })
        for trade in engine.open.values():
            payload["trades"].append({
                "id": trade.id, "side": trade.side, "qty": trade.open_qty, "entry": trade.entry_price,
                "entry_time": trade.entry_time // 1000, "sl": trade.sl, "tp": trade.tp, "pnl": engine.floating(trade),
                "value_per_point": trade.open_qty * units * rate,
            })
        return payload
