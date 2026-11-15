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
        # Unikátní DOM id, pod kterým JS komponenta graf hledá
        self.props(f"id={self._js_id}")
        self.classes("chart-container")
        self._dark = dark
        self._on_ready = on_ready
        # Graf inicializujeme až po prvním vykreslení stránky v prohlížeči
        ui.timer(0.1, self._init_in_browser, once=True)

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
