"""Scanner na pozadí serveru: signály indikátoru přes symboly z jeho vstupů a alert před zavřením svíčky.

Úlohy scanneru jsou instance indikátorů se vstupy scanneru (scanner_params)
v rozložení grafů (viz logic.scanner.make_job). ScannerService každou úlohu
přepočítá jednou za SCAN_INTERVAL_S (novou úlohu hned): pro každý symbol ze
vstupů stáhne historii timeframu scanu (přes cache, ze sítě jen chybějící
úsek a rozpracovanou svíčku), spočítá indikátor a výsledek ohlásí otevřeným
stránkám (tabulka v grafu). Každých CHECK_INTERVAL_S kontroluje alert před
zavřením svíčky; před odesláním si výsledky obnoví, aby zpráva odpovídala
aktuálním datům. Zpráva odejde přes aktivní alerty na indikátor úlohy
s podmínkou alert_call (jako „Any alert() function call“ v TradingView),
které určují i notifikace. Běží i bez otevřené stránky.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from tickview.indicators.compute import context_for, history_bars, run_indicator
from tickview.indicators.registry import get_indicator
from tickview.logic.cache import KlineCache, fetch_recent
from tickview.logic.layouts import LAYOUT_SETTINGS_KEY, load_layout, normalize_layout
from tickview.logic.scanner import (
    CloseAlertWindow,
    alert_message,
    bar_hits,
    current_bar_close,
    error_row,
    layout_jobs,
    overview_payload,
    symbol_row,
)
from tickview.providers import get_provider
from tickview.providers.base import TIMEFRAME_MS, utc_now_ms
from tickview.services.listeners import Listeners

logger = logging.getLogger(__name__)

# Jak často se úloha přepočítá (s) a jak často se kontroluje alert (s)
SCAN_INTERVAL_S = 60.0
CHECK_INTERVAL_S = 15.0
# Výsledky starší než tato doba (s) se před odesláním alertu přepočítají
FRESH_RESULTS_S = 20.0
# Kolik symbolů se stahuje a počítá souběžně
SCAN_CONCURRENCY = 4
# Nejkratší rozestup událostí o průběhu prvního přepočtu (s)
PROGRESS_EVENT_S = 1.0


@dataclass
class _JobState:
    """Stav jedné úlohy scanneru.

    Atributy:
        results: poslední výsledek {"rows", "scanned_at"} (None = ještě nepočítáno).
        progress: průběh běžícího přepočtu (hotové symboly, všechny) pro poznámku v tabulce.
        window: stav alertu před zavřením svíčky.
        rescanned_close: zavření svíčky, po kterém už proběhl přepočet (při
            zavřeném trhu se nepřepočítává dokola).
        progress_emitted: monotonic čas poslední události o průběhu.
    """

    results: Optional[dict] = None
    progress: Optional[tuple[int, int]] = None
    window: CloseAlertWindow = field(default_factory=CloseAlertWindow)
    rescanned_close: Optional[int] = None
    progress_emitted: float = 0.0


class ScannerService(Listeners):
    """Scanner signálů indikátorů přes symboly z jejich vstupů s alertem před zavřením svíčky.

    Parametry:
        cache: úložiště svíček a nastavení (rozložení grafů).
        alert_engine: engine alertů (alerty scanneru, odesílání notifikací a log).
        provider_lookup: funkce id zdroje -> zdroj (testy dosadí vlastní).
    """

    def __init__(self, cache: KlineCache, alert_engine, provider_lookup: Callable = get_provider) -> None:
        super().__init__()
        self._cache = cache
        self._alert_engine = alert_engine
        self._provider_lookup = provider_lookup
        # Úlohy podle klíče, jejich stav a klíč úlohy tabulky každého grafu rozložení
        self.jobs: dict[str, dict] = {}
        self._states: dict[str, _JobState] = {}
        self._tables: list[Optional[str]] = []
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._scan_lock = asyncio.Lock()

    # ---------- životní cyklus a úlohy ----------

    async def start(self) -> None:
        """Načte úlohy z uloženého rozložení a spustí smyčku scanneru."""
        self.set_layout(load_layout(await self._cache.get_setting(LAYOUT_SETTINGS_KEY)))
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Ukončí smyčku scanneru (při ukončení aplikace)."""
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def set_layout(self, layout: dict) -> None:
        """Úlohy z rozložení grafů (volá UI po jeho uložení): nové se hned
        přepočítají, odebrané zahodí výsledek i stav alertu.

        Parametry:
            layout: konfigurace rozložení {"panes": [{"symbol", "indicators"}, …]}.
        """
        jobs, self._tables = layout_jobs(normalize_layout(layout))
        if jobs.keys() == self.jobs.keys():
            return
        self.jobs = jobs
        self._states = {key: self._states.get(key) or _JobState() for key in jobs}
        self._wake.set()

    def result(self, key: str) -> Optional[dict]:
        """Poslední výsledek úlohy {"rows", "scanned_at"} (None = ještě nepočítáno)."""
        state = self._states.get(key)
        return state.results if state else None

    def table(self, pane_index: int) -> Optional[dict]:
        """Data tabulky scanneru pro graf rozložení (pořadí v rozložení); None = bez tabulky."""
        key = self._tables[pane_index] if pane_index < len(self._tables) else None
        if key is None:
            return None
        state = self._states[key]
        return overview_payload(self.jobs[key], state.results, state.progress)

    # ---------- smyčka ----------

    async def _loop(self) -> None:
        while True:
            # Čeká na nové úlohy, nejdéle do další kontroly alertu
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=CHECK_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            for key, job in list(self.jobs.items()):
                if key not in self.jobs:
                    continue
                try:
                    results = self.result(key)
                    if not results or utc_now_ms() - results["scanned_at"] >= SCAN_INTERVAL_S * 1000:
                        await self.scan(job)
                    await self._check_alert(job)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Scanner %s selhal", job["title"])

    # ---------- přepočet ----------

    async def scan(self, job: dict) -> Optional[dict]:
        """Přepočítá všechny symboly úlohy a ohlásí výsledek stránkám; vrací výsledek
        (None = úloha byla mezitím odebrána)."""
        async with self._scan_lock:
            rows = await self._scan_rows(job)
            state = self._states.get(job["key"])
            # Mezitím odebraná úloha – výsledek zahodíme
            if state is None:
                return None
            state.results = {"rows": rows, "scanned_at": utc_now_ms()}
            self._emit({"type": "scanner_updated"})
            return state.results

    async def _scan_rows(self, job: dict) -> list[dict]:
        """Řádky scanneru pro všechny symboly úlohy (nenačtený indikátor = chyba u každého)."""
        indicator = get_indicator(job["kind"])
        needed = history_bars(indicator, job["params"]) if indicator else 0
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
        symbols = job["config"]["symbols"]
        state = self._states.get(job["key"])
        if state:
            state.progress = (0, len(symbols))

        async def scan_one(source: str, symbol: str) -> dict:
            if indicator is None:
                return error_row(source, symbol, f"Indikátor {job['kind']} se nenačetl.")
            async with semaphore:
                row = await self._scan_symbol(indicator, job, needed, source, symbol)
            if state:
                self._report_progress(state)
            return row

        try:
            return list(await asyncio.gather(*(scan_one(source, symbol) for source, symbol in symbols)))
        finally:
            if state:
                state.progress = None

    def _report_progress(self, state: _JobState) -> None:
        """Započte hotový symbol; tabulka ukazuje průběh jen do prvního výsledku
        (první stažení historie může trvat minuty), události nejvýš jednou za PROGRESS_EVENT_S."""
        done, total = state.progress or (0, 0)
        state.progress = (done + 1, total)
        now = time.monotonic()
        if state.results is None and now - state.progress_emitted >= PROGRESS_EVENT_S:
            state.progress_emitted = now
            self._emit({"type": "scanner_updated"})

    async def _scan_symbol(self, indicator, job: dict, needed: int, source: str, symbol: str) -> dict:
        """Spočítá indikátor pro jeden symbol; chyba se promítne do řádku (ostatní symboly pokračují).

        Parametry:
            needed: kolik svíček historie výpočet potřebuje (history_bars).
        """
        config, params = job["config"], job["params"]
        timeframe = config["timeframe"]
        try:
            provider = self._provider_lookup(source)
        except KeyError:
            return error_row(source, symbol, f"Neznámý zdroj {source}.")
        if timeframe not in provider.timeframes:
            return error_row(source, symbol, f"Zdroj {provider.name} nemá timeframe {timeframe}.")
        try:
            candles = await fetch_recent(provider, self._cache, symbol, timeframe, utc_now_ms(),
                                         min_count=needed, window_bars=needed)
            if not candles:
                return error_row(source, symbol, "Žádná data.")
            ctx = context_for(provider, symbol, timeframe, candles, signal_bars=config["bars"])
            result = await asyncio.to_thread(run_indicator, indicator, params, candles, ctx)
        except Exception as exc:
            logger.warning("Scanner %s:%s selhal: %s", source, symbol, exc)
            return error_row(source, symbol, str(exc) or type(exc).__name__)
        return symbol_row(source, symbol, result.signals, config["bars"], candles[-1].open_time)

    # ---------- alert před zavřením svíčky ----------

    async def _check_alert(self, job: dict) -> None:
        """Zkontroluje alert úlohy před zavřením svíčky (případně si výsledky obnoví)."""
        config = job["config"]
        alert = config["alert"]
        state = self._states.get(job["key"])
        # Bez aktivního alertu scanneru na indikátor úlohy není kam zprávu poslat
        channels = self._alert_engine.alert_call_alerts(job["kind"])
        if not alert["enabled"] or state is None or state.results is None or not channels:
            return
        results = state.results
        step_ms = TIMEFRAME_MS[config["timeframe"]]
        now_ms = utc_now_ms()
        bar_close = current_bar_close(results["rows"], step_ms, now_ms)
        if bar_close <= now_ms and bar_close != state.rescanned_close:
            # Začala nová svíčka, výsledky jsou z předchozí – přepočet (jednou;
            # při zavřeném trhu nová svíčka nepřibude a přepočítává běžný interval)
            state.rescanned_close = bar_close
            results = await self.scan(job)
            if results is None:
                return
            bar_close = current_bar_close(results["rows"], step_ms, now_ms)
        minutes_left = (bar_close - now_ms) / 60_000
        hits = bar_hits(results["rows"], config["strip_prefix"])
        if state.window.check(bar_close, minutes_left, len(hits), alert["minutes1"], alert["minutes2"],
                              commit=False) is None:
            return
        if now_ms - results["scanned_at"] > FRESH_RESULTS_S * 1000:
            # Před odesláním čerstvá data (signál na běžící svíčce se mohl změnit)
            results = await self.scan(job)
            if results is None:
                return
            hits = bar_hits(results["rows"], config["strip_prefix"])
        due = state.window.check(bar_close, minutes_left, len(hits), alert["minutes1"], alert["minutes2"])
        if due is None:
            return
        # Souhrnná zpráva přes každý aktivní alert scanneru (každý má vlastní notifikace)
        message = alert_message(job["title"], config["timeframe"], due, hits)
        for alert_spec in channels:
            await self._alert_engine.fire_message(alert_spec, message, symbol=f"Scanner {job['title']}",
                                                  timeframe=config["timeframe"])
