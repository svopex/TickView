"""Panel Scanner v pravém panelu: tabulka signálů přes seznam symbolů a dialog nastavení."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from nicegui import ui

from tickview.indicators.api import TIMEFRAMES
from tickview.indicators.registry import get_indicator
from tickview.logic.cache import KlineCache
from tickview.logic.scanner import (
    MAX_ALERT_MINUTES,
    MAX_SCAN_BARS,
    scanner_title,
    signal_indicators,
    status_kind,
    status_text,
    visible_rows,
)
from tickview.services.scanner import ScannerService
from tickview.ui.notify_fields import NotifyFields
from tickview.ui.param_form import ParamForm
from tickview.ui.templates import settings_buttons, settings_header


class ScannerPanel:
    """Obsah záložky Scanner a dialog jeho nastavení.

    Parametry:
        cache: úložiště (seznamy symbolů).
        scanner: služba scanneru na serveru.
        on_pick: async callback("zdroj:symbol") – klik na řádek načte symbol do grafu.
    """

    def __init__(self, cache: KlineCache, scanner: ScannerService, on_pick: Callable[[str], Awaitable[None]]) -> None:
        self.cache = cache
        self.scanner = scanner
        self.on_pick = on_pick
        # Název seznamu scanneru pro informační řádek (obnoví ho render)
        self.watchlist_name = ""

    # ---------- panel ----------

    def build(self) -> ui.column:
        """Vytvoří sekci panelu v aktuálním kontextu UI a dialog nastavení; vrací sekci."""
        with ui.column().classes("scanner-panel w-full") as section:
            with ui.row().classes("scanner-header w-full"):
                ui.label("Scanner").classes("scanner-title")
                ui.space()
                ui.button(icon="refresh", color=None, on_click=self.scanner.request_scan).props(
                    "flat dense round size=sm"
                ).classes("alert-btn").tooltip("Přepočítat hned")
                ui.button(icon="settings", color=None, on_click=self.open_settings).props(
                    "flat dense round size=sm"
                ).classes("alert-btn").tooltip("Nastavení scanneru")
            self.info = ui.label("").classes("scanner-info")
            self.body = ui.column().classes("scanner-body w-full")
        self._build_dialog()
        return section

    def make_pick(self, key: str):
        async def _handler(_event=None) -> None:
            await self.on_pick(key)

        return _handler

    def update_info(self) -> None:
        """Informační řádek: indikátor · timeframe · seznam · čas výpočtu (· počítám…)."""
        config, results = self.scanner.config, self.scanner.results
        parts = [scanner_title(config), config["timeframe"], self.watchlist_name or "bez seznamu"]
        if results["scanned_at"]:
            parts.append(datetime.fromtimestamp(results["scanned_at"] / 1000).strftime("%H:%M:%S"))
        if self.scanner.running:
            parts.append("počítám…")
        self.info.text = " · ".join(parts)

    async def render(self) -> None:
        """Překreslí tabulku podle posledního výsledku scanneru."""
        config, results = self.scanner.config, self.scanner.results
        names = {item["id"]: item["name"] for item in await self.cache.watchlists()}
        self.watchlist_name = names.get(config["watchlist_id"], "")
        self.update_info()
        self.body.clear()
        with self.body:
            if not config["enabled"]:
                ui.label("Scanner je vypnutý – zapněte ho v nastavení (ozubené kolečko).").classes("alert-empty")
                return
            if results["error"]:
                ui.label(results["error"]).classes("alert-empty")
                return
            if not results["scanned_at"]:
                ui.label("Počítám…").classes("alert-empty")
                return
            rows = visible_rows(results["rows"], config["only_hits"])
            with ui.row().classes("scanner-row scanner-row-head w-full"):
                ui.label("Symbol").classes("scanner-symbol")
                ui.label("Signál (svíčka)").classes("scanner-status")
            if not rows:
                ui.label("Žádný signál.").classes("alert-empty")
            for row in rows:
                kind = status_kind(row)
                with ui.row().classes("scanner-row w-full").on("click", self.make_pick(row["key"])):
                    ui.label(row["symbol"]).classes("scanner-symbol")
                    status = ui.label("chyba" if kind == "error" else status_text(row) or "—").classes(
                        f"scanner-status scanner-{kind}"
                    )
                    # Tooltip: typ formace u signálu, popis chyby u chybného řádku
                    labels = [label for label in (row["long_label"], row["short_label"]) if label]
                    if row["error"] or labels:
                        status.tooltip(row["error"] or " / ".join(labels))

    # ---------- nastavení ----------

    def _build_dialog(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("tv-settings scanner-settings"):
            settings_header(self.dialog, "Nastavení scanneru")
            self.enabled = ui.checkbox("Zapnout scanner")
            self.indicator = ui.select({}, label="Indikátor", on_change=self._on_indicator_change).props(
                "outlined dense options-dense"
            ).classes("w-full")
            self.watchlist = ui.select({}, label="Seznam symbolů").props("outlined dense options-dense").classes("w-full")
            with ui.row().classes("tv-settings-row w-full"):
                self.timeframe = ui.select(list(TIMEFRAMES), label="Timeframe").props("outlined dense options-dense")
                self.bars = ui.number("Počet posledních svíček", min=1, max=MAX_SCAN_BARS, step=1).props(
                    "outlined dense"
                )
            self.only_hits = ui.checkbox("Zobrazit jen trhy se signálem")
            ui.label("Alert před zavřením svíčky").classes("tv-settings-section")
            self.alert_enabled = ui.checkbox("Posílat souhrn trhů se signálem na běžící svíčce")
            with ui.row().classes("tv-settings-row w-full"):
                self.minutes1 = ui.number("1. upozornění (min, 0 = vyp.)", min=0, max=MAX_ALERT_MINUTES, step=1).props(
                    "outlined dense"
                )
                self.minutes2 = ui.number("2. upozornění (min, 0 = vyp.)", min=0, max=MAX_ALERT_MINUTES, step=1).props(
                    "outlined dense"
                )
            self.notify = NotifyFields()
            ui.label("Parametry indikátoru").classes("tv-settings-section")
            self.form = ParamForm(ui.column().classes("indicator-dialog-params w-full"))
            with ui.row().classes("tv-settings-actions w-full"):
                ui.space()
                settings_buttons(self.dialog, self.submit)

    def _fill_params(self, kind: str | None) -> None:
        # Parametry indikátoru: uložené, jen když jde o indikátor z nastavení, jinak výchozí
        config = self.scanner.config
        params = config["params"] if kind == config["indicator"] else {}
        self.form.fill(get_indicator(kind) if kind else None, params)

    def _on_indicator_change(self, event) -> None:
        self._fill_params(event.value)

    async def open_settings(self) -> None:
        """Otevře dialog s aktuálním nastavením scanneru."""
        config = self.scanner.config
        lists = await self.cache.watchlists()
        self.watchlist.options = {item["id"]: f"{item['name']} ({item['count']})" for item in lists}
        self.watchlist.update()
        self.watchlist.value = config["watchlist_id"] if config["watchlist_id"] in self.watchlist.options else None
        options = signal_indicators()
        self.indicator.options = options
        self.indicator.update()
        self.indicator.value = config["indicator"] if config["indicator"] in options else next(iter(options), None)
        # Formulář vyplníme vždy (změna hodnoty výběru vyvolá handler jen při jiné hodnotě)
        self._fill_params(self.indicator.value)
        self.enabled.value = config["enabled"]
        self.timeframe.value = config["timeframe"]
        self.bars.value = config["bars"]
        self.only_hits.value = config["only_hits"]
        alert = config["alert"]
        self.alert_enabled.value = alert["enabled"]
        self.minutes1.value = alert["minutes1"]
        self.minutes2.value = alert["minutes2"]
        self.notify.set(alert["notify"])
        self.dialog.open()

    async def submit(self) -> None:
        """Ověří a uloží nastavení; při chybě dialog zůstane otevřený."""
        indicator = get_indicator(self.indicator.value) if self.indicator.value else None
        if indicator is None:
            ui.notify("Vyberte indikátor se signály.", type="warning")
            return
        try:
            params = indicator.normalize_params(self.form.values())
            notify = self.notify.get()
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return
        if self.enabled.value and self.watchlist.value is None:
            ui.notify("Vyberte seznam symbolů.", type="warning")
            return
        await self.scanner.update_config({
            "enabled": self.enabled.value,
            "indicator": indicator.id,
            "params": params,
            "watchlist_id": self.watchlist.value,
            "timeframe": self.timeframe.value,
            "bars": self.bars.value,
            "only_hits": self.only_hits.value,
            "alert": {
                "enabled": self.alert_enabled.value,
                "minutes1": self.minutes1.value,
                "minutes2": self.minutes2.value,
                "notify": notify,
            },
        })
        self.dialog.close()
        await self.render()
