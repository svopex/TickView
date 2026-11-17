"""Formulář parametrů indikátoru vygenerovaný ze schématu (seznam Param).

Rozložení jako záložka Inputs v TradingView: nadpis skupiny velkými písmeny,
v každém řádku popisek vlevo a pole vpravo; přepínač zabírá celý řádek
(zaškrtávátko a popisek). Každé pole odpovídá typu parametru a nese tooltip
z deklarace.
"""

from collections.abc import Callable

from nicegui import ui

from tickview.indicators.api import Indicator, Param
from tickview.ui.color_button import ColorButton

# Callback hledání symbolu: (aktuální text, funkce, která zvolený "zdroj:SYMBOL" zapíše)
SymbolSearch = Callable[[str, Callable[[str], None]], object]


class ParamForm:
    """Pole parametrů jednoho indikátoru v daném kontejneru.

    Parametry:
        container: prvek NiceGUI, do kterého se pole vykreslí (při každém
            fill se vyprázdní).
        on_symbol_search: otevře hledání symbolu pro pole typu symbol
            (None = pole bez tlačítka hledání).
    """

    def __init__(self, container, on_symbol_search: SymbolSearch | None = None) -> None:
        self.container = container
        self.on_symbol_search = on_symbol_search
        self.fields: dict[str, object] = {}

    def fill(self, indicator: Indicator | None, params: dict) -> None:
        """Vykreslí pole parametrů indikátoru s hodnotami (chybějící = výchozí).

        Parametry:
            indicator: indikátor (None = nenačtený – jen poznámka).
            params: aktuální hodnoty parametrů.
        """
        self.fields.clear()
        self.container.clear()
        with self.container:
            if indicator is None:
                ui.label("Indikátor se nenačetl – nastavení nelze upravit.").classes("settings-note")
                return
            if not indicator.params:
                ui.label("Indikátor nemá nastavitelné parametry.").classes("settings-note")
                return
            group = None
            for param in indicator.params:
                # Nadpis sekce při změně skupiny (parametry bez skupiny bez nadpisu)
                if param.group and param.group != group:
                    ui.label(param.group).classes("tv-settings-section param-group")
                group = param.group
                value = params.get(param.name, param.default)
                self.fields[param.name] = self._field(param, value)

    def _field(self, param: Param, value):
        """Jeden řádek formuláře podle typu parametru; tooltip z deklarace."""
        label = param.label or param.name
        if param.kind == "bool":
            # Přepínač: zaškrtávátko s popiskem přes celý řádek
            field = ui.checkbox(label, value=bool(value)).classes("param-check")
            hinted = [field]
        else:
            with ui.row().classes("param-row w-full"):
                text = ui.label(label).classes("param-label")
                field = self._control(param, value, label)
            hinted = [text, field]
        if param.tooltip:
            for element in hinted:
                element.tooltip(param.tooltip).props("max-width=420px")
        return field

    def _control(self, param: Param, value, label: str):
        """Ovládací prvek vpravo v řádku (číslo, výběr, barva, symbol, text)."""
        if param.kind in ("int", "float"):
            return ui.number(value=value, min=param.min, max=param.max,
                             step=param.step or (1 if param.kind == "int" else 0.1)).props(
                "outlined dense").classes("param-control")
        if param.kind == "color":
            return ColorButton(value or param.default, title=label)
        if param.kind in ("select", "timeframe"):
            options = {option_value: option_label for option_value, option_label in param.options}
            return ui.select(options, value=value if value in options else param.default).props(
                "outlined dense options-dense").classes("param-control")
        field = ui.input(value=str(value or "")).props("outlined dense").classes("param-control")
        if param.kind == "symbol":
            # Symbol jako input.symbol v TradingView: text + tužka, která otevře hledání
            field.props('placeholder="zdroj:SYMBOL" clearable').classes("param-symbol")
            if self.on_symbol_search:
                with field.add_slot("append"):
                    ui.icon("edit").classes("param-symbol-search cursor-pointer").on(
                        "click", lambda _event, field=field: self.on_symbol_search(field.value or "", field.set_value)
                    ).tooltip("Hledat symbol")
        return field

    def values(self) -> dict:
        """Hodnoty polí {název: hodnota}.

        Ověří je až Indicator.normalize_params; barva s průhledností (rgba)
        se tam nahradí výchozí, parametr barvy bere jen #rrggbb.
        """
        return {name: field.value for name, field in self.fields.items()}
