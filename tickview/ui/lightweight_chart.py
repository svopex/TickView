"""Vlastní komponenta NiceGUI obalující TradingView Lightweight Charts."""

import json
import uuid

from nicegui import ui

from tickview.logic.styles import CHART_DEFAULTS
from tickview.providers.base import Candle


class LightweightChart(ui.element):
    """Obal JS grafu: inicializace v prohlížeči, výměna dat, inkrementální svíčka, motiv.

    Všechny metody volají funkce window.TickViewChart.<název>(id grafu, ...)
    ze souboru static/lightweight_chart.js.

    Parametry:
        dark: počáteční motiv grafu.
        on_ready: async callback volaný po inicializaci grafu v prohlížeči.
        chart_style: styl svíček (Settings grafu, viz CHART_DEFAULTS).
        chart_type: typ grafu (viz CHART_TYPES – svíčky, čára, …).
    """

    def __init__(
        self,
        dark: bool = True,
        on_ready=None,
        chart_style: dict | None = None,
        chart_type: str = "candles",
    ) -> None:
        super().__init__("div")
        self._js_id = f"tickview-chart-{uuid.uuid4().hex[:8]}"
        # JS graf hledá kontejner přes data-chart-id (vlastní id prvku necháme
        # NiceGUI, které ho používá pro mapování DOM uzlů a eventy)
        self.props(f"data-chart-id={self._js_id}")
        self.classes("chart-container")
        self._dark = dark
        self._chart_style = chart_style or dict(CHART_DEFAULTS)
        self._chart_type = chart_type
        self._on_ready = on_ready
        # Naposledy poslaná data tabulky scanneru (stejná se neposílají znovu)
        self._overview: dict | None = None
        # Naposledy poslaná data replaye – živá a statická část (stejná se neposílají znovu)
        self._replay: dict | None = None
        self._replay_static: dict | None = None
        # Graf inicializujeme až po prvním vykreslení stránky v prohlížeči
        ui.timer(0.1, self._init_in_browser, once=True)

    @property
    def js_id(self) -> str:
        """Id grafu v JS (klíč událostí z popAllEvents)."""
        return self._js_id

    def _js(self, method: str, args: tuple) -> str:
        # Volání JS API grafu; argumenty převede json.dumps (řetězce, čísla,
        # True/False/None → true/false/null, seznamy a slovníky)
        arguments = ", ".join(json.dumps(value) for value in (self._js_id, *args))
        return f"window.TickViewChart.{method}({arguments})"

    def _call(self, method: str, *args) -> None:
        """Zavolá JS funkci grafu bez čekání na výsledek."""
        # Background volání z venčí (bez slotu) vstupují do kontextu tohoto prvku
        with self:
            ui.run_javascript(self._js(method, args) + ";")

    async def _call_result(self, method: str, *args):
        """Zavolá JS funkci grafu a vrátí její výsledek."""
        with self:
            return await ui.run_javascript("return " + self._js(method, args) + ";")

    async def _init_in_browser(self) -> None:
        self._call(
            "init",
            {
                "dark": self._dark,
                "chartStyle": self._chart_style,
                "chartType": self._chart_type,
            },
        )
        if self._on_ready:
            await self._on_ready()

    @staticmethod
    def _candle_payload(candle: Candle) -> dict:
        # Lightweight Charts pracuje se sekundami UTC; cache i zdroje mají milisekundy
        return {
            "time": candle.open_time // 1000,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }

    async def set_candles(self, candles: list[Candle], step_ms: int, visible_bars: int = 150) -> None:
        """Nahradí celý dataset grafu (načtení historie).

        Parametry:
            candles: svíčky vzestupně podle času.
            step_ms: délka baru timeframu (graf podle ní umisťuje kresby mimo svíčky).
            visible_bars: kolik posledních svíček má být vidět.
        """
        self._call(
            "setCandles",
            [self._candle_payload(candle) for candle in candles],
            {"visibleBars": visible_bars, "stepSeconds": step_ms // 1000},
        )

    async def prepend_candles(self, candles: list[Candle], exhausted: bool) -> None:
        """Přidá starší svíčky na začátek grafu (donačtení historie při posunu doleva).

        Parametry:
            candles: starší svíčky (prázdný seznam = nic nového).
            exhausted: True = starší data už nejsou, graf se na ně přestane ptát.
        """
        self._call("prependCandles", [self._candle_payload(candle) for candle in candles], exhausted)

    async def update_candles(self, candles: list[Candle]) -> None:
        """Inkrementální doplnění/aktualizace posledních svíček (živá data, doplněná díra)."""
        self._call("updateCandles", [self._candle_payload(candle) for candle in candles])

    async def set_theme(self, dark: bool) -> None:
        """Přepne motiv grafu v prohlížeči."""
        self._dark = dark
        self._call("setTheme", dark)

    async def set_indicators(self, payload: dict) -> None:
        """Nahradí indikátory (overlay čáry + panely); payload s "tail" jen doplní poslední body."""
        self._call("setIndicators", payload)

    async def set_drawing_mode(self, mode: str | None) -> None:
        """Zapne/vypne kreslicí mód (hline/trend/rect/brush/fib/text/measure/None)."""
        self._call("setDrawingMode", mode)

    def set_drawing_defaults(self, defaults: dict[str, dict]) -> None:
        """Výchozí styl nových kreseb podle typu: {druh: {"color", "options"}} (výchozí šablony)."""
        self._call("setDrawingDefaults", defaults)

    def set_drawing_config(self, config: dict) -> None:
        """Konfigurace kreslení: druhy se stylem a s popiskem, šířky a styly čar a šablony.

        {"kinds", "labelKinds", "widths": [[šířka, popisek]], "lineStyles":
        [[styl, popisek]], "templates": {druh: {název: styl}}} – lišta vybrané
        kresby, podmenu Template v pravém kliku a výběr šablony u aktivního nástroje.
        """
        self._call("setDrawingConfig", config)

    async def set_magnet(self, mode: str) -> None:
        """Nastaví režim magnetu: "off", "weak" (jen v blízkosti OHLC) nebo "strong" (vždy na OHLC)."""
        self._call("setMagnet", mode)

    def set_symbol_info(self, info: dict) -> None:
        """Nastaví nadpis tickeru v legendě: {"symbol", "name", "exchange", "timeframe", "source"}."""
        self._call("setSymbolInfo", info)

    def set_legend_status(self, status: str) -> None:
        """Stav dat v nadpisu legendy: "live", "delayed" nebo "none"."""
        self._call("setLegendStatus", status)

    async def set_keep_tool(self, enabled: bool) -> None:
        """Zapne/vypne režim tužky (nástroj zůstává aktivní i po nakreslení)."""
        self._call("setKeepTool", enabled)

    async def set_locked(self, locked: bool) -> None:
        """Zamkne/odemkne kresby (nelze je vybírat, posouvat ani mazat)."""
        self._call("setLocked", locked)

    async def set_drawings_hidden(self, hidden: bool) -> None:
        """Skryje/zobrazí všechny kresby."""
        self._call("setDrawingsHidden", hidden)

    async def add_text(self, time_s: int, price: float, text: str, color: str | None = None, options: dict | None = None) -> None:
        """Přidá textovou kresbu (po potvrzení dialogu Text).

        Parametry:
            time_s, price: kotevní bod (levý horní roh textu).
            text: obsah (může mít více řádků).
            color, options: barva a volby z dialogu (písmo, pozadí, rámeček,
                zalomení, viditelnost); None = výchozí styl typu.
        """
        self._call("addText", int(time_s), float(price), text, color, options or {})

    async def add_sticker(self, time_s: int, price: float, emoji: str) -> None:
        """Přidá sticker (emoji) na pozici kliku (po výběru v dialogu)."""
        self._call("addSticker", int(time_s), float(price), emoji)

    def set_timezone(self, setting: str, exchange_timezone: str) -> None:
        """Časové pásmo zobrazení: "local", "exchange", "UTC" nebo IANA název.

        Parametry:
            setting: volba uživatele.
            exchange_timezone: IANA pásmo burzy aktuálního instrumentu (pro "exchange").
        """
        self._call("setTimezone", setting, exchange_timezone)

    def set_chart_style(self, style: dict) -> None:
        """Použije styl svíček (barvy těla, okrajů a knotů, obarvení podle předchozího závěru)."""
        self._call("setChartStyle", style)

    def set_icons(self, icons: dict[str, str]) -> None:
        """Pošle SVG piktogramy nástrojů (icons.ICONS) pro flyout nabídky a panel oblíbených."""
        self._call("setIcons", icons)

    def set_chart_type(self, chart_type: str) -> None:
        """Přepne typ grafu (Bars, Candles, Line, Area, …)."""
        self._call("setChartType", chart_type)

    def set_favorites(self, modes: list[str], position: dict | None, visible: bool) -> None:
        """Oblíbené nástroje pro plovoucí panel.

        Parametry:
            modes: režimy oblíbených nástrojů.
            position: pozice panelu {"left", "top"} v px vůči oknu (None = nahoře uprostřed grafu).
            visible: zda panel zobrazit.
        """
        self._call("setFavorites", modes, position, visible)

    def set_favorites_visible(self, visible: bool) -> None:
        """Zobrazí / skryje plovoucí panel oblíbených nástrojů."""
        self._call("setFavoritesVisible", visible)

    def set_overview(self, data: dict | None) -> None:
        """Tabulka scanneru v grafu (logic.scanner.overview_payload); None = skrýt.

        Stejná data se znovu neposílají (scanner hlásí výsledky všem grafům).
        """
        if data == self._overview:
            return
        self._overview = data
        self._call("setOverview", data)

    def set_alert_lines(self, lines: list[dict]) -> None:
        """Čárkované čáry aktivních cenových alertů symbolu: [{"price", "title"}]."""
        self._call("setAlertLines", lines)

    def set_replay(self, payload: dict | None, static: dict | None = None) -> None:
        """Data replaye pro graf; None = graf není v replayi.

        Parametry:
            payload: živá část – otevřené obchody a čekající příkazy (čáry a štítky).
            static: uzavřené obchody a šipky (mění se jen s obchody).
        Stejná data se znovu neposílají (stav se hlásí po každém kroku přehrávání).
        """
        if payload is None:
            static = None
        if static != self._replay_static:
            self._replay_static = static
            if static is not None:
                self._call("setReplayStatic", static)
        if payload != self._replay:
            self._replay = payload
            self._call("setReplay", payload)

    def show_time(self, time_s: int) -> None:
        """Posune graf tak, aby svíčka s časem time_s (s UTC) byla uprostřed (klik na obchod v historii)."""
        self._call("showTime", int(time_s))

    def end_replay_drag(self) -> None:
        """Python zpracoval taženou úroveň replaye – čára se vrátí na aktuální data."""
        self._call("endReplayDrag")

    def set_alert_kinds(self, kinds: list[str]) -> None:
        """Druhy kreseb, u kterých kontextové menu nabídne „Přidat alert…“ (logic.alerts.LINE_KINDS)."""
        self._call("setAlertKinds", kinds)

    def play_sound(self) -> None:
        """Přehraje krátký tón spuštěného alertu (Web Audio v prohlížeči)."""
        self._call("playSound")

    async def assign_drawing_id(self, local_id: int, drawing_id: int) -> None:
        """Přiřadí nově uložené kresbě její id z databáze (podle lokálního id z grafu)."""
        self._call("assignDrawingId", int(local_id), int(drawing_id))

    def set_tool_groups(self, groups: list[dict]) -> None:
        """Pošle definice skupin nástrojů pro flyout nabídky lišty."""
        self._call("setToolGroups", groups)

    async def trash_drawings(self) -> int:
        """Smaže označenou kresbu (nebo všechny) – vrací počet smazaných."""
        return int(await self._call_result("trashDrawings") or 0)

    async def set_drawings(self, drawings: list[dict]) -> None:
        """Nahradí zobrazené kresby (načtené z cache při načtení symbolu)."""
        self._call("setDrawings", drawings)

    def set_active(self) -> None:
        """Označí graf jako aktivní v rozložení více grafů (rámeček, lišta, klávesnice)."""
        self._call("setActive")

    def set_layout_sync(self, sync: dict) -> None:
        """Synchronizace mezi grafy rozložení {"crosshair", "time", "date_range"} (platí pro všechny grafy)."""
        self._call("setLayoutSync", sync)

    def destroy(self) -> None:
        """Odstraní graf z prohlížeče i ze stránky (menší počet grafů v rozložení)."""
        self._call("destroy")
        self.delete()
