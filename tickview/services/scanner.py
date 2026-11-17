"""Scanner na pozadí serveru: signály indikátoru přes seznam symbolů a alert před zavřením svíčky.

ScannerService jednou za SCAN_INTERVAL_S (a hned po změně nastavení) stáhne
pro každý symbol zvoleného seznamu historii timeframu scanneru (přes cache,
ze sítě jen chybějící úsek a rozpracovanou svíčku), spočítá indikátor se
signály a výsledek ohlásí otevřeným stránkám. Každých CHECK_INTERVAL_S
kontroluje alert před zavřením svíčky; před odesláním si výsledky obnoví,
aby zpráva odpovídala aktuálním datům. Běží i bez otevřené stránky.
"""

import asyncio
import json
import logging
from typing import Callable, Optional

from tickview.indicators.compute import context_for, history_bars, run_indicator
from tickview.indicators.registry import get_indicator
from tickview.logic.cache import KlineCache, fetch_recent
from tickview.logic.scanner import (
    SCANNER_SETTINGS_KEY,
    CloseAlertWindow,
    alert_message,
    bar_hits,
    current_bar_close,
    error_row,
    load_scanner,
    normalize_scanner,
    scanner_title,
    symbol_row,
)
from tickview.providers import get_provider
from tickview.providers.base import TIMEFRAME_MS, utc_now_ms
from tickview.services.listeners import Listeners

logger = logging.getLogger(__name__)

# Jak často se scanner přepočítá (s) a jak často se kontroluje alert (s)
SCAN_INTERVAL_S = 60.0
CHECK_INTERVAL_S = 15.0
# Výsledky starší než tato doba (s) se před odesláním alertu přepočítají
FRESH_RESULTS_S = 20.0
# Kolik symbolů se stahuje a počítá souběžně
SCAN_CONCURRENCY = 4
# Výsledek bez průchodu (vypnutý scanner, start aplikace)
EMPTY_RESULTS: dict = {"rows": [], "scanned_at": None, "error": ""}


class ScannerService(Listeners):
    """Scanner signálů indikátoru přes seznam symbolů s alertem před zavřením svíčky.

    Parametry:
        cache: úložiště svíček, nastavení a seznamů symbolů.
        alert_engine: engine alertů (odesílání notifikací a log).
        provider_lookup: funkce id zdroje -> zdroj (testy dosadí vlastní).
    """

    def __init__(self, cache: KlineCache, alert_engine, provider_lookup: Callable = get_provider) -> None:
        super().__init__()
        self._cache = cache
        self._alert_engine = alert_engine
        self._provider_lookup = provider_lookup
        self.config: dict = load_scanner(None)
        # Výsledek posledního průchodu: řádky, čas, chyba nastavení
        self.results: dict = dict(EMPTY_RESULTS)
        self.running = False
        self._window = CloseAlertWindow()
        # Zavření svíčky, po kterém už proběhl přepočet (při zavřeném trhu se nepřepočítává dokola)
        self._rescanned_close: Optional[int] = None
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._scan_lock = asyncio.Lock()

    # ---------- životní cyklus ----------

    async def start(self) -> None:
        """Načte nastavení a spustí smyčku scanneru."""
        self.config = load_scanner(await self._cache.get_setting(SCANNER_SETTINGS_KEY))
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Ukončí smyčku scanneru (při ukončení aplikace)."""
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def update_config(self, data: dict) -> dict:
        """Uloží nové nastavení, vynuluje stav alertu a spustí přepočet; vrací normalizované nastavení."""
        self.config = normalize_scanner(data)
        await self._cache.set_setting(SCANNER_SETTINGS_KEY, json.dumps(self.config))
        self._window = CloseAlertWindow()
        if not self.config["enabled"]:
            self.results = dict(EMPTY_RESULTS)
            self._emit({"type": "scanner_updated"})
        self.request_scan()
        return self.config

    def request_scan(self) -> None:
        """Přepočet hned (tlačítko Obnovit, změna nastavení)."""
        self._wake.set()

    # ---------- smyčka ----------

    async def _loop(self) -> None:
        last_scan = 0.0
        loop = asyncio.get_running_loop()
        while True:
            # Čeká na požadavek přepočtu, nejdéle do další kontroly alertu
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=CHECK_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
            woken = self._wake.is_set()
            self._wake.clear()
            if not self.config["enabled"]:
                continue
            try:
                if woken or loop.time() - last_scan >= SCAN_INTERVAL_S:
                    await self.scan()
                    last_scan = loop.time()
                if await self._check_alert():
                    last_scan = loop.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scanner selhal")

    # ---------- přepočet ----------

    async def scan(self) -> None:
        """Přepočítá všechny symboly seznamu a ohlásí výsledek stránkám."""
        async with self._scan_lock:
            config = self.config
            self.running = True
            # Začátek průchodu mění jen informační řádek („počítám…“)
            self._emit({"type": "scanner_updated", "running": True})
            try:
                rows, error = await self._scan_rows(config)
            finally:
                self.running = False
            # Mezitím změněné nastavení – výsledek zastaralého průchodu zahodíme
            if config is not self.config:
                return
            self.results = {"rows": rows, "scanned_at": utc_now_ms(), "error": error}
            self._emit({"type": "scanner_updated"})

    async def _scan_rows(self, config: dict) -> tuple[list[dict], str]:
        """Řádky scanneru pro všechny symboly seznamu; druhá hodnota = chyba nastavení."""
        indicator = get_indicator(config["indicator"])
        if indicator is None:
            return [], f"Indikátor {config['indicator']} se nenačetl."
        if config["watchlist_id"] is None:
            return [], "Vyberte seznam symbolů v nastavení scanneru."
        items = await self._cache.watchlist_list(config["watchlist_id"])
        if not items:
            return [], "Seznam symbolů je prázdný."
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def scan_one(item: dict) -> dict:
            async with semaphore:
                return await self._scan_symbol(indicator, config, item["source"], item["symbol"])

        rows = await asyncio.gather(*(scan_one(item) for item in items))
        return list(rows), ""

    async def _scan_symbol(self, indicator, config: dict, source: str, symbol: str) -> dict:
        """Spočítá indikátor pro jeden symbol; chyba se promítne do řádku (ostatní symboly pokračují)."""
        timeframe = config["timeframe"]
        try:
            provider = self._provider_lookup(source)
        except KeyError:
            return error_row(source, symbol, f"Neznámý zdroj {source}.")
        if timeframe not in provider.timeframes:
            return error_row(source, symbol, f"Zdroj {provider.name} nemá timeframe {timeframe}.")
        try:
            needed = history_bars(indicator, config["params"])
            candles = await fetch_recent(provider, self._cache, symbol, timeframe, utc_now_ms(),
                                         min_count=needed, window_bars=needed)
            if not candles:
                return error_row(source, symbol, "Žádná data.")
            ctx = context_for(provider, symbol, timeframe, candles, signal_bars=config["bars"])
            result = await asyncio.to_thread(run_indicator, indicator, config["params"], candles, ctx)
        except Exception as exc:
            logger.warning("Scanner %s:%s selhal: %s", source, symbol, exc)
            return error_row(source, symbol, str(exc) or type(exc).__name__)
        return symbol_row(source, symbol, result.signals, config["bars"], candles[-1].open_time)

    # ---------- alert před zavřením svíčky ----------

    async def _check_alert(self) -> bool:
        """Zkontroluje alert před zavřením svíčky; vrací True, když se kvůli němu přepočítalo."""
        alert = self.config["alert"]
        if not alert["enabled"]:
            return False
        rescanned = False
        step_ms = TIMEFRAME_MS[self.config["timeframe"]]
        now_ms = utc_now_ms()
        rows = self.results["rows"]
        bar_close = current_bar_close(rows, step_ms, now_ms)
        if bar_close <= now_ms and bar_close != self._rescanned_close:
            # Začala nová svíčka, výsledky jsou z předchozí – přepočet (jednou;
            # při zavřeném trhu nová svíčka nepřibude a přepočítává běžný interval)
            self._rescanned_close = bar_close
            await self.scan()
            rescanned = True
            rows = self.results["rows"]
            bar_close = current_bar_close(rows, step_ms, now_ms)
        minutes_left = (bar_close - now_ms) / 60_000
        due = self._window.check(bar_close, minutes_left, len(bar_hits(rows)), alert["minutes1"],
                                 alert["minutes2"], commit=False)
        if due is None:
            return rescanned
        scanned_at = self.results["scanned_at"] or 0
        if not rescanned and now_ms - scanned_at > FRESH_RESULTS_S * 1000:
            # Před odesláním čerstvá data (signál na běžící svíčce se mohl změnit)
            await self.scan()
            rescanned = True
            rows = self.results["rows"]
        hits = bar_hits(rows)
        due = self._window.check(bar_close, minutes_left, len(hits), alert["minutes1"], alert["minutes2"])
        if due is None:
            return rescanned
        title = scanner_title(self.config)
        message = alert_message(title, self.config["timeframe"], due, hits)
        await self._alert_engine.notify_external(
            alert["notify"], source="scanner", symbol=f"Scanner {title}", timeframe=self.config["timeframe"],
            message=message,
        )
        return rescanned
