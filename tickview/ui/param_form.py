"""Formulář parametrů indikátoru vygenerovaný ze schématu (seznam Param).

Používá ho dialog nastavení instance indikátoru i nastavení scanneru:
parametry se seskupí podle skupiny (nadpis sekce), každé pole odpovídá typu
parametru a nese tooltip z deklarace.
"""

from nicegui import ui

from tickview.indicators.api import Indicator, Param
from tickview.ui.color_button import ColorButton


class ParamForm:
    """Pole parametrů jednoho indikátoru v daném kontejneru.

    Parametry:
        container: prvek NiceGUI, do kterého se pole vykreslí (při každém
            fill se vyprázdní).
    """

    def __init__(self, container) -> None:
        self.container = container
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
                    ui.label(param.group).classes("param-group")
                group = param.group
                value = params.get(param.name, param.default)
                self.fields[param.name] = self._field(param, value)

    @staticmethod
    def _field(param: Param, value):
        """Jedno pole podle typu parametru; tooltip z deklarace."""
        label = param.label or param.name
        if param.kind in ("int", "float"):
            field = ui.number(label, value=value, min=param.min, max=param.max,
                              step=param.step or (1 if param.kind == "int" else 0.1)).props("outlined dense")
        elif param.kind == "bool":
            field = ui.checkbox(label, value=bool(value)).classes("param-check")
        elif param.kind == "color":
            # Barva: popisek vlevo, vzorek vpravo (jako řádek Style)
            with ui.row().classes("tv-settings-row param-color"):
                ui.label(label).classes("tv-settings-label")
                field = ColorButton(value or param.default, title=label)
        elif param.kind in ("select", "timeframe"):
            options = {option_value: option_label for option_value, option_label in param.options}
            field = ui.select(options, label=label, value=value if value in options else param.default).props(
                "outlined dense options-dense"
            )
        else:
            field = ui.input(label, value=str(value or "")).props("outlined dense")
        if param.tooltip:
            field.tooltip(param.tooltip).props("max-width=420px")
        return field

    def values(self) -> dict:
        """Hodnoty polí {název: hodnota}.

        Ověří je až Indicator.normalize_params; barva s průhledností (rgba)
        se tam nahradí výchozí, parametr barvy bere jen #rrggbb.
        """
        return {name: field.value for name, field in self.fields.items()}
