"""Jeden graf rozložení (Layout jako v TradingView): data, živý stream, indikátory a kresby.

Stránka může mít více grafů najednou; každý má vlastní symbol, timeframe,
typ grafu, indikátory, načítání historie a živý stream. Ovládání stránky
(horní lišta, watchlist, dialogy) působí na aktivní graf. Společné věci
(seance, časové pásmo, stavová lišta, štítek zdroje) zprostředkuje objekt
stránky předaný v konstruktoru.
"""

import asyncio
import time

from tickview.i18n import tr
from tickview.logic.cache import KlineCache, fetch_history, fetch_recent, replay_drawing_source
from tickview.logic.replay import bucket_start, is_finer, merge_into
from tickview.indicators.compute import compute_indicators, context_for, tail_payload
from tickview.indicators.registry import get_indicator
from tickview.logic.sessions import SESSION_RTH, filter_session, in_session
from tickview.logic.styles import drawing_visible
from tickview.providers import get_provider
from tickview.providers.base import TIMEFRAME_MS, Candle, StreamStatus, is_closed, merge_candle, utc_now_ms
from tickview.services import datafeeds
from tickview.ui.lightweight_chart import LightweightChart

# Kolik svíček historie se načte pro jedno zobrazení (a uloží do cache)
DEFAULT_BARS = 600
# Kolik posledních svíček je vidět po načtení grafu
VISIBLE_BARS = 150
# Kolik oken historie se nejvýš stáhne, když první nemá dost svíček (víkend,
# noc u akcií, RTH před otevřením trhu); každé další je dvojnásobné a starší,
# pak se historie považuje za vyčerpanou
OLDER_LOAD_ATTEMPTS = 4
# Jak často nejvýš se z živých svíček přepočítají indikátory (sekundy)
LIVE_INDICATOR_REFRESH_S = 2.0
# Jak často nejvýš se přepočítají indikátory při přehrávání replaye (sekundy);
# nová svíčka grafu je přepočítá hned
REPLAY_INDICATOR_REFRESH_S = 0.3


class ChartPane:
    """Jeden graf rozložení se svým symbolem, timeframem, indikátory a živými daty.

    Parametry:
        page: objekt stránky – atributy cache (KlineCache), state (společné
            nastavení: session, timezone, chart_style, dark) a metody
            show_status(pane, zpráva, error), badge_changed(pane), replay
            (ReplayController otevřené session replaye) a pane_slot(pane)
            (pořadí grafu v rozložení – klíč indikátorů grafu v session).
        config: stav grafu {"symbol": "zdroj:symbol", "timeframe", "chart_type", "indicators"}.
        on_ready: async callback(pane) po inicializaci grafu v prohlížeči.
    """

    def __init__(self, page, config: dict, on_ready) -> None:
        self.page = page
        self.cache: KlineCache = page.cache
        # Symbol, který se načte po inicializaci grafu (zdroj:symbol)
        self.initial_key: str = config["symbol"]
        self.provider_id: str | None = None
        self.provider = None
        self.symbol: str | None = None
        self.timeframe: str = config["timeframe"]
        self.chart_type: str = config["chart_type"]
        # Aktivní indikátory grafu: v živém režimu živá sada, v replayi sada session
        self.indicators: list[dict] = list(config.get("indicators") or [])
        # Živá sada odložená, dokud graf ukazuje session (None = graf je v živém
        # režimu); id session, jejíž sadu graf ukazuje (přepnutí ze session A do B)
        self.live_indicators: list[dict] | None = None
        self.indicator_session: int | None = None
        # Svíčky v grafu (podklad indikátorů); živý stream je průběžně doplňuje
        self.candles: list[Candle] | None = None
        # Název a burza zobrazeného instrumentu pro nadpis grafu
        self.symbol_meta: dict | None = None
        self.badge_live: bool | None = None
        # Číslo načtení chrání proti překříženým odpovědím; zámek řadí
        # vykreslení (starší odpověď nesmí přepsat novější data)
        self.load_seq = 0
        self.load_lock = asyncio.Lock()
        self.stream_task: asyncio.Task | None = None
        # Monotonic čas posledního přepočtu indikátorů ze živých svíček
        self.indicators_refreshed_at = 0.0
        # Graf zobrazuje data replaye (ne živá); kotva svíček grafu pro skládání
        # ze základních svíček replaye (None = graf má základní timeframe)
        self.replay_shown = False
        self.replay_anchor: int | None = None
        # Přepočet indikátorů při přehrávání běží na pozadí (přehrávání na něj
        # nečeká); další požadavky během výpočtu se sloučí do jednoho
        self._replay_indicator_task: asyncio.Task | None = None
        self._replay_indicators_due = False
        self.closed = False
        self._on_ready = on_ready
        settings = page.state
        self.chart = LightweightChart(
            dark=settings["dark"],
            on_ready=self._ready,
            chart_style=settings["chart_style"],
            chart_type=self.chart_type,
        )

    async def _ready(self) -> None:
        # Graf je inicializovaný v prohlížeči – stránka mu pošle společné
        # nastavení a načte symbol
        if not self.closed:
            await self._on_ready(self)

    @property
    def key(self) -> str | None:
        """Zobrazený symbol ve tvaru "zdroj:symbol" (None před načtením)."""
        if not self.provider_id or not self.symbol:
            return None
        return f"{self.provider_id}:{self.symbol}"

    def replay_covers(self) -> bool:
        """True, když graf patří do otevřené session replaye (stejný symbol)."""
        replay = getattr(self.page, "replay", None)
        return bool(replay and replay.active and self.key and self.key == replay.key)

    @property
    def replay_session_id(self) -> int | None:
        """Id session, ke které patří kresby a indikátory grafu (None = živý graf)."""
        return self.page.replay.session_id if self.replay_covers() else None

    @property
    def drawing_owner(self) -> tuple[str, str]:
        """(source, symbol) kreseb v databázi – kresby replaye patří k session, ne k živému symbolu."""
        session_id = self.replay_session_id
        if session_id is not None:
            return replay_drawing_source(session_id), self.symbol
        return self.provider_id, self.symbol

    def config(self) -> dict:
        """Stav grafu k uložení do konfigurace rozložení."""
        return {
            "symbol": self.key or self.initial_key,
            "timeframe": self.timeframe,
            "chart_type": self.chart_type,
            # Rozložení (a scanner) drží živé indikátory, sada session se ukládá k session
            "indicators": self.live_indicators if self.live_indicators is not None else self.indicators,
        }

    def status(self, message: str, error: bool = False) -> None:
        """Zpráva do stavové lišty (stránka ukazuje zprávy aktivního grafu a chyby)."""
        if not self.closed:
            self.page.show_status(self, message, error)

    def close(self) -> None:
        """Ukončí graf (odebrání z rozložení, odpojení klienta): stream a JS graf."""
        self.closed = True
        self.load_seq += 1
        self.cancel_stream()

    # ---------- symbol a nadpis ----------

    def timeframes(self) -> list[str]:
        """Timeframy, které graf může zobrazit: timeframy zdroje, v replayi ne jemnější než přesnost replaye."""
        available = list(self.provider.timeframes) if self.provider else []
        if self.replay_covers():
            base = self.page.replay.base_tf
            available = [timeframe for timeframe in available if not is_finer(timeframe, base)]
        return available

    def set_timeframe(self, value: str) -> bool:
        """Nastaví timeframe (bez načtení dat); False = beze změny nebo ho graf zobrazit neumí."""
        if value == self.timeframe or (self.provider and value not in self.timeframes()):
            return False
        self.timeframe = value
        self.push_symbol_info()
        return True

    def set_symbol(self, key: str) -> bool:
        """Nastaví zobrazený symbol "zdroj:symbol" (bez načtení dat); False = neznámý zdroj.

        Nepodporovaný timeframe nahradí prvním timeframem zdroje.
        """
        provider_id, _, symbol = str(key).partition(":")
        try:
            provider = get_provider(provider_id)
        except KeyError:
            self.status(tr("Neznámý zdroj: {source}").format(source=provider_id), error=True)
            return False
        self.provider_id = provider_id
        self.provider = provider
        self.symbol = symbol
        timeframes = list(provider.timeframes.keys())
        if self.timeframe not in timeframes:
            self.timeframe = timeframes[0]
        self.set_badge(None)
        self.apply_timezone()
        self.symbol_meta = None
        self.push_symbol_info()
        # Název a burzu doplní zdroj na pozadí (zdroje si metadata cachují samy)
        asyncio.create_task(self.describe_symbol(provider, symbol, self.key))
        return True

    def apply_timezone(self) -> None:
        """Pošle grafu časové pásmo zobrazení (volba Exchange = pásmo burzy symbolu)."""
        provider = self.provider
        exchange = provider.exchange_timezone(self.symbol) if provider and self.symbol else "UTC"
        self.chart.set_timezone(self.page.state["timezone"], exchange)

    def push_symbol_info(self) -> None:
        """Nadpis tickeru v legendě grafu: „Název · timeframe · burza“."""
        if self.provider is None or not self.symbol:
            return
        meta = self.symbol_meta or {}
        self.chart.set_symbol_info(
            {
                "symbol": self.symbol,
                "name": meta.get("name") or self.symbol,
                "exchange": meta.get("exchange") or self.provider.name,
                "timeframe": self.timeframe,
                "source": self.provider.name,
            }
        )

    async def describe_symbol(self, provider, symbol: str, key: str | None) -> None:
        # Název a burzu zjistí zdroj (může trvat – Yahoo, TWS); výsledek se
        # použije jen tehdy, když je symbol pořád zobrazený
        try:
            meta = await provider.describe(symbol)
        except Exception:
            # Bez metadat zůstane v nadpisu symbol a název zdroje
            return
        if not self.closed and self.key == key:
            self.symbol_meta = meta
            self.push_symbol_info()

    def set_badge(self, live: bool | None) -> None:
        """Stav dat (živě / zpožděno / —) – tečka v nadpisu grafu a štítek zdroje stránky."""
        if self.badge_live == live or self.closed:
            return
        self.badge_live = live
        self.chart.set_legend_status("none" if live is None else "live" if live else "delayed")
        self.page.badge_changed(self)

    # ---------- indikátory ----------

    def merge_live_candle(self, candle: Candle) -> None:
        # Promítne živou svíčku do self.candles (podklad indikátorů); graf bez
        # načtené historie ji ignoruje
        if self.candles:
            merge_candle(self.candles, candle)

    def _sync_indicator_set(self) -> None:
        """Vymění aktivní indikátory podle režimu grafu: živá sada, nebo sada otevřené session.

        Každá session má vlastní indikátory (jako kresby) a živý graf také;
        při vstupu do session se živá sada odloží, při odchodu vrátí.
        """
        session_id = self.replay_session_id
        if session_id == self.indicator_session:
            return
        if self.live_indicators is None:
            self.live_indicators = self.indicators
        if session_id is None:
            self.indicators, self.live_indicators = self.live_indicators, None
        else:
            self.indicators = self.page.replay.pane_indicators(self.page.pane_slot(self), self.live_indicators)
        self.indicator_session = session_id

    def required_bars(self) -> int:
        """Kolik svíček grafu potřebují aktivní indikátory (aspoň VISIBLE_BARS)."""
        needed = VISIBLE_BARS
        for spec in self.indicators:
            indicator = get_indicator(spec["kind"])
            if indicator is not None:
                try:
                    needed = max(needed, indicator.required_bars(spec["params"]))
                except Exception:
                    # Chybný požadavek indikátoru neblokuje načtení grafu
                    pass
        return needed

    async def push_indicators(self, tail: bool = False) -> None:
        """Přepočítá indikátory nad svíčkami grafu a pošle je do grafu.

        Výpočet běží ve vlákně (pandas nad tisíci svíček by zdržel smyčku
        událostí); tail=True pošle jen poslední body (živý přepočet).
        """
        self.indicators_refreshed_at = time.monotonic()
        candles = list(self.candles or [])
        indicators = list(self.indicators)
        ctx = context_for(self.provider, self.symbol or "", self.timeframe, candles, live=tail)
        # Externí data (COT report) z paměti; chybějící nebo zastaralá se načtou
        # na pozadí a indikátory se pak přepočítají (graf na ně nečeká)
        pairs = [(get_indicator(spec["kind"]), spec["params"]) for spec in indicators]
        ctx, pending = datafeeds.attach_cached(ctx, pairs)
        if pending:
            datafeeds.load_in_background(pending, self._indicator_data_loaded)
        payload = await asyncio.to_thread(compute_indicators, indicators, candles, ctx)
        if not self.closed:
            await self.chart.set_indicators(tail_payload(payload) if tail else payload)

    async def _indicator_data_loaded(self) -> None:
        # Externí data indikátorů se načetla na pozadí – přepočet s nimi
        if not self.closed:
            await self.push_indicators()

    # ---------- historie a živá data ----------

    def active_session(self):
        """Hlavní seance instrumentu, když je zvolené RTH (jinak None)."""
        if self.page.state["session"] != SESSION_RTH or self.provider is None or not self.symbol:
            return None
        return self.provider.regular_session(self.symbol)

    def session_filter(self):
        """Továrna filtru seance pro základní svíčky replaye: funkce(timeframe) → filtr; None bez RTH."""
        session = self.active_session()
        if session is None:
            return None
        return lambda timeframe: (lambda chunk: filter_session(chunk, timeframe, session))

    def show_loaded_status(self) -> None:
        # Souhrn načteného grafu ve stavové liště
        count = len(self.candles or [])
        message = tr("{symbol} · {timeframe} · {count} svíček · zdroj {source}").format(
            symbol=self.symbol, timeframe=self.timeframe, count=count, source=self.provider.name
        )
        self.status(message)

    async def reload_chart(self) -> None:
        """Načte historii zobrazeného symbolu a timeframu, indikátory, kresby a spustí stream."""
        provider, symbol, timeframe = self.provider, self.symbol, self.timeframe
        if provider is None or not symbol or self.closed:
            return
        self.load_seq += 1
        seq = self.load_seq
        self.cancel_stream()
        self._sync_indicator_set()
        if self.replay_covers():
            await self.reload_replay_chart(seq)
            return
        self.replay_shown = False
        self.set_badge(False)
        self.status(tr("Načítám {symbol} ({timeframe})…").format(symbol=symbol, timeframe=timeframe))
        try:
            # Aspoň tolik svíček, kolik je po načtení vidět a kolik potřebují
            # indikátory (zbytek donačte load_older při posunu doleva)
            candles = await self.fetch_session_history(
                provider, symbol, timeframe, utc_now_ms(), min_count=self.required_bars(), seq=seq
            )
            if seq != self.load_seq:
                # Mezitím uživatel zvolil jiný symbol/timeframe – zastaralou odpověď zahodíme
                return
            if not candles:
                self.status(
                    tr("Pro {symbol} ({timeframe}) nejsou dostupná data.").format(symbol=symbol, timeframe=timeframe),
                    error=True,
                )
                await self.clear_chart(timeframe)
                return
            # Vykreslení držíme pod zámkem a číslo požadavku kontrolujeme znovu
            # uvnitř: data do grafu smí zapsat jen nejnovější načtení
            async with self.load_lock:
                if seq != self.load_seq:
                    return
                await self.chart.set_candles(candles, TIMEFRAME_MS[timeframe], visible_bars=VISIBLE_BARS)
                self.candles = candles
                # Přepočet aktivních indikátorů nad novými daty
                await self.push_indicators()
            # Kresby patří k symbolu – načteme uložené viditelné na tomto timeframu
            await self.reload_drawings()
            if seq != self.load_seq:
                return
            self.show_loaded_status()
            # Historie načtena → spustíme živý stream pro aktuální zobrazení
            self.start_stream()
        except Exception as exc:
            if seq != self.load_seq:
                return
            self.status(tr("Načtení dat selhalo: {error}").format(error=exc), error=True)
            await self.clear_chart(timeframe)

    async def reload_replay_chart(self, seq: int) -> None:
        """Načte graf v replayi: historie do kurzoru, poslední svíčka složená ze základních svíček.

        Svíčka grafu, do které kurzor padá, se nebere ze zdroje (obsahovala by
        budoucnost), ale skládá se z odkrytých základních svíček; živý stream
        neběží. Svíčky grafu se zarovnávají podle poslední svíčky ze zdroje.
        """
        replay = self.page.replay
        provider, symbol = self.provider, self.symbol
        cursor = replay.cursor
        if is_finer(self.timeframe, replay.base_tf):
            # Jemnější timeframe než přesnost replaye zobrazit nejde – přepneme na základní
            self.timeframe = replay.base_tf
            self.push_symbol_info()
        timeframe = self.timeframe
        step_ms = TIMEFRAME_MS[timeframe]
        self.replay_shown = True
        self.set_badge(None)
        self.chart.set_legend_status("replay")
        self.chart.set_alert_lines([])
        self.status(tr("Replay: načítám {symbol} ({timeframe})…").format(symbol=symbol, timeframe=timeframe))
        try:
            history = await self.fetch_session_history(
                provider, symbol, timeframe, cursor, min_count=self.required_bars(), seq=seq
            )
            history = [candle for candle in history if candle.open_time <= cursor]
            if seq != self.load_seq:
                return
            if timeframe == replay.base_tf:
                candles = history
                self.replay_anchor = None
            else:
                # Kotva = otevření poslední svíčky zdroje; rozpracovaná svíčka grafu
                # se složí ze základních svíček od jejího začátku po kurzor
                anchor = history[-1].open_time if history else cursor
                bucket = bucket_start(cursor, anchor, step_ms)
                forming = None
                for part in await replay.base_candles(bucket, cursor):
                    forming = merge_into(forming, part, bucket)
                candles = [candle for candle in history if candle.open_time < bucket] + ([forming] if forming else [])
                self.replay_anchor = anchor
            if seq != self.load_seq:
                return
            async with self.load_lock:
                if seq != self.load_seq:
                    return
                await self.chart.set_candles(candles, step_ms, visible_bars=VISIBLE_BARS)
                self.candles = candles
                await self.push_indicators()
            await self.reload_drawings()
            message = tr("Replay {symbol} · {timeframe} · {count} svíček")
            self.status(message.format(symbol=symbol, timeframe=timeframe, count=len(candles)))
        except Exception as exc:
            if seq == self.load_seq:
                self.status(tr("Načtení dat replaye selhalo: {error}").format(error=exc), error=True)

    async def apply_replay_bars(self, bars: list[Candle]) -> int:
        """Promítne nově odkryté základní svíčky do grafu; vrací počet nově začatých svíček grafu.

        Svíčky se skládají podle kotvy grafu (replay_anchor); graf dostane jen
        změněné svíčky (series.update), indikátory se přepočítají při nové
        svíčce grafu, jinak nejvýš jednou za REPLAY_INDICATOR_REFRESH_S.
        """
        if not self.candles or self.closed:
            return 0
        step_ms = TIMEFRAME_MS[self.timeframe]
        changed: dict[int, Candle] = {}
        started = 0
        for part in bars:
            last = self.candles[-1]
            if self.replay_anchor is None:
                candle = Candle(part.open_time, part.open, part.high, part.low, part.close, part.volume)
            else:
                candle = merge_into(last, part, bucket_start(part.open_time, self.replay_anchor, step_ms))
            if candle.open_time > last.open_time:
                started += 1
            merge_candle(self.candles, candle)
            changed[candle.open_time] = candle
        if changed:
            await self.chart.update_candles(list(changed.values()))
        if self.indicators:
            self._schedule_replay_indicators()
        return started

    def _schedule_replay_indicators(self) -> None:
        """Přepočet indikátorů při přehrávání: na pozadí, nejvýš jednou za REPLAY_INDICATOR_REFRESH_S."""
        self._replay_indicators_due = True
        if self._replay_indicator_task and not self._replay_indicator_task.done():
            return

        async def run() -> None:
            # Dokud během výpočtu přibývají svíčky, počítá se znovu (požadavky se slučují)
            while self._replay_indicators_due and not self.closed and self.replay_shown:
                self._replay_indicators_due = False
                await asyncio.sleep(max(0.0, self.indicators_refreshed_at + REPLAY_INDICATOR_REFRESH_S
                                        - time.monotonic()))
                await self.push_indicators(tail=True)

        self._replay_indicator_task = asyncio.create_task(run())

    async def clear_chart(self, timeframe: str) -> None:
        """Vyprázdní graf (data se nepodařilo načíst).

        Bez dat nesmí v grafu zůstat svíčky ani kresby předchozího symbolu pod
        nadpisem nového; pod zámkem vykreslení jako úspěšné načtení.
        """
        seq = self.load_seq
        async with self.load_lock:
            if seq != self.load_seq:
                return
            self.candles = []
            await self.chart.set_candles([], TIMEFRAME_MS[timeframe], visible_bars=VISIBLE_BARS)
            await self.chart.set_drawings([])
            await self.push_indicators()

    async def fetch_session_history(
        self, provider, symbol: str, timeframe: str, end_ms: int, *, min_count: int, seq: int
    ) -> list[Candle]:
        """Stáhne svíčky končící v end_ms, odfiltrované na zvolenou seanci (viz fetch_recent).

        Začne oknem DEFAULT_BARS svíček; přestane i tehdy, když mezitím
        začalo jiné načtení (seq).

        Parametry:
            provider, symbol, timeframe: co se stahuje.
            end_ms: konec intervalu (ms UTC, včetně).
            min_count: kolik svíček po filtru seance stačí.
            seq: číslo načtení, ke kterému dotaz patří (self.load_seq).
        """
        session = self.active_session()
        return await fetch_recent(
            provider, self.cache, symbol, timeframe, end_ms,
            min_count=min_count,
            window_bars=DEFAULT_BARS,
            attempts=OLDER_LOAD_ATTEMPTS,
            transform=lambda chunk: filter_session(chunk, timeframe, session),
            should_stop=lambda: seq != self.load_seq,
        )

    async def load_older(self, before_s, recompute: bool = True) -> None:
        """Donačte starší svíčky před první svíčkou grafu (posun grafu doleva).

        Když nic nevrátí ani poslední okno fetch_session_history, graf
        historii označí za vyčerpanou. recompute=False = indikátory přepočítá
        volající až na konci (ensure_history).
        """
        provider, symbol, timeframe = self.provider, self.symbol, self.timeframe
        seq = self.load_seq
        if provider is None or not symbol or before_s is None:
            return
        try:
            older = await self.fetch_session_history(
                provider, symbol, timeframe, int(before_s) * 1000 - 1, min_count=1, seq=seq
            )
        except Exception as exc:
            if seq == self.load_seq:
                message = tr("Starší data {symbol} se nepodařilo načíst: {error}")
                self.status(message.format(symbol=symbol, error=exc), error=True)
                # Nic nepřidáno, historie ale není vyčerpaná – graf to zkusí později
                await self.chart.prepend_candles([], exhausted=False)
            return
        async with self.load_lock:
            if seq != self.load_seq:
                return
            await self.chart.prepend_candles(older, exhausted=not older)
            if not older:
                message = tr("{symbol} ({timeframe}): starší data nejsou k dispozici.")
                self.status(message.format(symbol=symbol, timeframe=timeframe))
                return
            first_open = self.candles[0].open_time if self.candles else None
            self.candles = [c for c in older if first_open is None or c.open_time < first_open] + (self.candles or [])
            if self.indicators and recompute:
                await self.push_indicators()
            self.show_loaded_status()

    async def ensure_history(self) -> None:
        """Donačte starší svíčky, dokud jich graf nemá tolik, kolik potřebují indikátory.

        Volá se po přidání nebo změně indikátoru (některé indikátory potřebují
        stovky svíček); končí, když starší data nepřibývají. Indikátory
        nepřepočítává – to udělá volající jednou na konci.
        """
        for _ in range(OLDER_LOAD_ATTEMPTS):
            candles = self.candles or []
            if not candles or len(candles) >= self.required_bars() or self.closed:
                return
            await self.load_older(candles[0].open_time // 1000, recompute=False)
            if len(self.candles or []) <= len(candles):
                return

    def start_load_older(self, before_s) -> None:
        """Donačítání na pozadí (nezdrží zpracování ostatních událostí grafu)."""

        async def run() -> None:
            try:
                await self.load_older(before_s)
            except Exception as exc:
                self.status(tr("Starší data se nepodařilo načíst: {error}").format(error=exc), error=True)

        asyncio.create_task(run())

    async def consume_stream(self, provider, symbol: str, timeframe: str, seq: int) -> None:
        """Konzumuje živý stream zdroje: doplňuje svíčky do grafu, cache a indikátorů.

        Stavové zprávy zdroje (výpadek / obnovení spojení) promítá do štítku
        a stavové lišty; díru po výpadku (chybějící svíčky mezi poslední
        známou a právě přišlou) doplní přes cache a historii zdroje.
        """
        step_ms = TIMEFRAME_MS[timeframe]
        live = provider.is_live_for(timeframe)
        market_closed_warned = False
        try:
            async for item in provider.stream(symbol, timeframe):
                if seq != self.load_seq:
                    # Mezitím se přepnulo zobrazení – stream ukončíme
                    return
                if isinstance(item, StreamStatus):
                    # Výpadek / obnovení spojení se zdrojem; zdroj může zároveň
                    # upřesnit, zda jsou data živá (IBKR podle předplatného)
                    if item.live is not None:
                        live = item.live
                    self.set_badge(live if item.connected else False)
                    if item.connected:
                        self.status(f"{symbol}: {item.message}")
                    else:
                        message = tr("Živý stream {symbol} přerušen, čekám na znovupřipojení: {reason}")
                        self.status(message.format(symbol=symbol, reason=item.message), error=True)
                    continue
                candle = item
                self.set_badge(live)
                session = self.active_session()
                if session and not in_session(candle, timeframe, session):
                    # RTH: svíčky pre-market / after-hours do grafu nepatří, do
                    # cache ano (uzavřené) – pro přepnutí na ETH
                    if is_closed(candle, step_ms, utc_now_ms()):
                        await self.cache.store(provider.id, symbol, timeframe, [candle])
                    continue
                batch = [candle]
                known = self.candles or []
                new_candle = bool(known) and candle.open_time > known[-1].open_time
                if known and candle.open_time > known[-1].open_time + step_ms:
                    # Mezi poslední známou a novou svíčkou chybí data (výpadek
                    # websocketu, uspání počítače…) – doplníme je přes cache
                    last_open = known[-1].open_time
                    try:
                        gap = await fetch_history(
                            provider, self.cache, symbol, timeframe, last_open + step_ms, candle.open_time - 1
                        )
                    except Exception:
                        # Doplnění se nepovedlo – díru doplní příští načtení grafu
                        gap = []
                    if seq != self.load_seq:
                        return
                    gap = filter_session(gap, timeframe, session)
                    batch = [c for c in gap if last_open < c.open_time < candle.open_time] + batch
                # Graf jedním voláním, podklad indikátorů a cache (jen uzavřená
                # živá svíčka; doplněnou díru uložil už fetch_history)
                await self.chart.update_candles(batch)
                for batch_candle in batch:
                    self.merge_live_candle(batch_candle)
                now_ms = utc_now_ms()
                if is_closed(candle, step_ms, now_ms):
                    await self.cache.store(provider.id, symbol, timeframe, [candle])
                # Indikátory: po doplnění díry celé, jinak jen poslední body a
                # nejvýš jednou za LIVE_INDICATOR_REFRESH_S (nová svíčka hned)
                if self.indicators:
                    if len(batch) > 1:
                        await self.push_indicators()
                    elif new_candle or time.monotonic() - self.indicators_refreshed_at >= LIVE_INDICATOR_REFRESH_S:
                        await self.push_indicators(tail=True)
                # Nejnovější svíčka je starší než celé další období → nová
                # nepřibývá, trh je pravděpodobně zavřený (hlásíme jen jednou).
                # Uzavřená svíčka sama o sobě nestačí: websocket (Binance, Alpaca)
                # ji posílá těsně po konci období i při otevřeném trhu.
                if not market_closed_warned and candle.open_time + 2 * step_ms <= now_ms:
                    market_closed_warned = True
                    message = tr("{symbol}: trh je pravděpodobně zavřený – živá data nepřibývají")
                    self.status(message.format(symbol=symbol))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Neočekávaná chyba mimo zdroj (zdroje výpadky hlásí přes StreamStatus)
            if seq == self.load_seq:
                self.set_badge(False)
                self.status(tr("Živý stream ukončen chybou: {error}").format(error=exc), error=True)

    def cancel_stream(self) -> None:
        """Zruší živý stream grafu (přepnutí symbolu, timeframu, zavření grafu)."""
        task = self.stream_task
        if task and not task.done():
            task.cancel()
        self.stream_task = None

    def start_stream(self) -> None:
        """Spustí živý stream pro aktuální zobrazení (předchozí zruší)."""
        self.cancel_stream()
        provider = self.provider
        if provider is None or not provider.supports_stream:
            self.set_badge(False)
            return
        self.stream_task = asyncio.create_task(self.consume_stream(provider, self.symbol, self.timeframe, self.load_seq))

    # ---------- kresby a alerty ----------

    async def reload_drawings(self, drawings: list[dict] | None = None) -> None:
        """Pošle grafu kresby symbolu viditelné na jeho timeframu.

        Parametry:
            drawings: už načtené kresby symbolu (více grafů se stejným symbolem
                sdílí jeden dotaz); None = načte je z cache.
        """
        if not self.provider_id or not self.symbol or self.closed:
            return
        if drawings is None:
            drawings = await self.cache.load_drawings(*self.drawing_owner)
        visible = [drawing for drawing in drawings if drawing_visible(drawing["options"], self.timeframe)]
        await self.chart.set_drawings(visible)

    def push_alert_lines(self, alerts: list[dict]) -> None:
        """Čárkované čáry aktivních cenových alertů symbolu grafu (všechny timeframy)."""
        lines = [
            {"id": alert["id"], "price": alert["target"]["price"], "title": "Alert"}
            for alert in alerts
            if alert["active"]
            and alert["source"] == self.provider_id
            and alert["symbol"] == self.symbol
            and alert["target"]["type"] == "value"
        ]
        # Graf v replayi ukazuje minulost – čáry živých alertů do něj nepatří
        self.chart.set_alert_lines([] if self.replay_shown else lines)
