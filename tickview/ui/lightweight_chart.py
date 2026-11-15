"""Vlastní komponenta NiceGUI obalující TradingView Lightweight Charts."""

import json
import uuid

from nicegui import ui

from tickview.providers.base import Candle


class LightweightChart(ui.element):
    """Obal JS grafu: inicializace v prohlížeči, výměna dat, inkrementální svíčka, motiv.

    Parametry:
        dark: počáteční motiv grafu.
        on_ready: async callback volaný po inicializaci grafu v prohlížeči.
    """

    def __init__(self, dark: bool = True, on_ready=None) -> None:
        super().__init__("div")
        self._js_id = f"tickview-chart-{uuid.uuid4().hex[:8]}"
        # JS graf hledá kontejner přes data-chart-id (vlastní id prvku necháme
        # NiceGUI, které ho používá pro mapování DOM uzlů a eventy)
        self.props(f"data-chart-id={self._js_id}")
        self.classes("chart-container")
        self._dark = dark
        self._on_ready = on_ready
        # Graf inicializujeme až po prvním vykreslení stránky v prohlížeči
        ui.timer(0.1, self._init_in_browser, once=True)

    @property
    def js_id(self) -> str:
        """DOM id grafu, pod kterým se volá JS API."""
        return self._js_id

    async def _init_in_browser(self) -> None:
        # Background volání z venčí (bez slotu) vstupují do kontextu tohoto prvku
        with self:
            ui.run_javascript(
                f'window.TickViewChart.init("{self._js_id}", {{ dark: {str(self._dark).lower()} }});'
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

    async def set_candles(self, candles: list[Candle], visible_bars: int = 150) -> None:
        """Nahradí celý dataset grafu (načtení historie)."""
        payload = json.dumps([self._candle_payload(candle) for candle in candles])
        with self:
            ui.run_javascript(
                f'window.TickViewChart.setCandles("{self._js_id}", {payload},'
                f" {{ visibleBars: {visible_bars} }});"
            )

    async def update_candle(self, candle: Candle) -> None:
        """Inkrementální doplnění/aktualizace poslední svíčky (živá data)."""
        payload = json.dumps(self._candle_payload(candle))
        with self:
            ui.run_javascript(f'window.TickViewChart.updateCandle("{self._js_id}", {payload});')

    async def set_theme(self, dark: bool) -> None:
        """Přepne motiv grafu v prohlížeči."""
        self._dark = dark
        with self:
            ui.run_javascript(f'window.TickViewChart.setTheme("{self._js_id}", {str(dark).lower()});')

    async def set_indicators(self, payload: dict) -> None:
        """Nahradí všechny indikátory (overlay čáry + panely) v grafu."""
        payload_json = json.dumps(payload)
        with self:
            ui.run_javascript(
                f'window.TickViewChart.setIndicators("{self._js_id}", {payload_json});'
            )

    async def set_drawing_mode(self, mode: str | None) -> None:
        """Zapne/vypne kreslicí mód (hline/trend/rect/brush/fib/text/measure/None)."""
        with self:
            ui.run_javascript(
                f'window.TickViewChart.setDrawingMode("{self._js_id}", {json.dumps(mode)});'
            )

    async def set_drawing_color(self, color: str) -> None:
        """Nastaví barvu nových kreseb."""
        with self:
            ui.run_javascript(
                f'window.TickViewChart.setDrawingColor("{self._js_id}", {json.dumps(color)});'
            )

    async def set_magnet(self, enabled: bool) -> None:
        """Zapne/vypne magnet (přichytávání k OHLC svíček)."""
        with self:
            ui.run_javascript(
                f'window.TickViewChart.setMagnet("{self._js_id}", {str(enabled).lower()});'
            )

    async def set_drawings_hidden(self, hidden: bool) -> None:
        """Skryje/zobrazí všechny kresby."""
        with self:
            ui.run_javascript(
                f'window.TickViewChart.setDrawingsHidden("{self._js_id}", {str(hidden).lower()});'
            )

    async def add_text(self, time_s: int, price: float, text: str) -> None:
        """Přidá textovou kresbu (po potvrzení dialogu)."""
        with self:
            ui.run_javascript(
                f'window.TickViewChart.addText("{self._js_id}", {int(time_s)}, {float(price)}, {json.dumps(text)});'
            )

    async def update_text(self, drawing_id: int, text: str) -> None:
        """Aktualizuje text existující textové kresby."""
        with self:
            ui.run_javascript(
                f'window.TickViewChart.updateText("{self._js_id}", {int(drawing_id)}, {json.dumps(text)});'
            )

    def set_tool_groups(self, groups: list[dict]) -> None:
        """Pošle definice skupin nástrojů pro flyout nabídky lišty."""
        groups_json = json.dumps(groups)
        with self:
            ui.run_javascript(
                f'window.TickViewChart.setToolGroups("{self._js_id}", {groups_json});'
            )

    async def trash_drawings(self) -> int:
        """Smaže označenou kresbu (nebo všechny) – vrací počet smazaných."""
        with self:
            removed = await ui.run_javascript(
                f'return window.TickViewChart.trashDrawings("{self._js_id}");'
            )
        return int(removed or 0)

    async def set_drawings(self, drawings: list[dict]) -> None:
        """Nahradí zobrazené kresby (načtené z cache)."""
        with self:
            ui.run_javascript(
                f'window.TickViewChart.setDrawings("{self._js_id}", {json.dumps(drawings)});'
            )
