"""Pole dialogu Settings kresby FRVP (Fixed range volume profile) jako v TradingView.

Záložka Inputs: rozvržení a velikost řádků, druh objemu, Value Area
a prodloužení čar doprava. Záložka Style: histogram (hodnoty, šířka,
umístění, barvy), čáry VAH / VAL / POC a vyvíjející se POC / Value Area
a barva boxu. Záložka Coordinates: první a poslední bar rozsahu.
"""

from nicegui import ui

from tickview.i18n import tr
from tickview.logic.styles import (
    FRVP_DEFAULTS,
    FRVP_LINE_DEFAULTS,
    FRVP_LINES,
    FRVP_MAX_ROWS,
    FRVP_PLACEMENTS,
    FRVP_ROWS_LAYOUTS,
    FRVP_VOLUME_MODES,
)
from tickview.ui.color_button import ColorButton
from tickview.ui.plot_style_button import PlotStyleButton

# Klíče stylu čáry v hodnotě PlotStyleButton
LINE_STYLE_KEYS = ("color", "width", "lineStyle")
# Popisky čar profilu v záložce Style (pořadí jako v TradingView)
LINE_LABELS = {"vah": "VAH", "val": "VAL", "poc": "POC", "devPoc": "Developing POC", "devVa": "Developing VA"}
# Barvy histogramu: klíč voleb → popisek řádku
PROFILE_COLORS = {
    "upColor": "Up Volume",
    "downColor": "Down Volume",
    "vaUpColor": "Value Area Up",
    "vaDownColor": "Value Area Down",
}


def _param_row(label: str) -> ui.row:
    """Řádek „popisek – prvek“ (prvek se vytvoří uvnitř vráceného řádku)."""
    with ui.row().classes("param-row w-full") as row:
        ui.label(label).classes("param-label")
    return row


def _select(options: dict, key: str) -> ui.select:
    """Rozbalovací nabídka voleb s výchozí hodnotou FRVP_DEFAULTS[key]."""
    return ui.select(options, value=FRVP_DEFAULTS[key]).props("outlined dense options-dense").classes("param-control")


def _int_number(value, low: int | None = None, high: int | None = None) -> ui.number:
    """Celočíselné pole v rozsahu low–high (None = bez meze)."""
    return ui.number(value=value, min=low, max=high, step=1, format="%d").props("outlined dense").classes(
        "param-control"
    )


class FrvpFields:
    """Pole záložek Inputs, Style a Coordinates kresby FRVP.

    Prvky se vytvoří metodami build_* v aktuálním kontejneru NiceGUI (každá
    záložka dialogu volá svou); fill / values převádějí volby kresby.
    """

    def __init__(self) -> None:
        # Pole s jednou hodnotou podle klíče voleb (čáry úrovní mají vlastní slovník)
        self.fields: dict = {}
        self.lines: dict[str, tuple[ui.checkbox, PlotStyleButton]] = {}

    def build_inputs(self) -> ui.column:
        """Záložka Inputs; vrátí sloupec polí (dialog ho skrývá u jiných kreseb)."""
        with ui.column().classes("indicator-style-group w-full") as column:
            with _param_row("Rows Layout"):
                self.fields["rowsLayout"] = _select(FRVP_ROWS_LAYOUTS, "rowsLayout")
            with _param_row("Row Size"):
                self.fields["rowSize"] = _int_number(FRVP_DEFAULTS["rowSize"], 1, FRVP_MAX_ROWS)
            with _param_row("Volume"):
                self.fields["volumeMode"] = _select(FRVP_VOLUME_MODES, "volumeMode")
            with _param_row("Value Area Volume"):
                self.fields["valueArea"] = _int_number(FRVP_DEFAULTS["valueArea"], 1, 100)
            self.fields["extendRight"] = ui.checkbox("Extend Right").classes("param-check tv-check")
            ui.label(
                tr("Profil se počítá ze svíček grafu v rozsahu: objem každé svíčky se rozdělí "
                   "rovnoměrně mezi cenové řádky od jejího low po high.")
            ).classes("settings-note")
        return column

    def build_style(self) -> None:
        """Záložka Style: histogram, čáry úrovní a box."""
        with ui.column().classes("indicator-style-group w-full"):
            self.fields["profile"] = ui.checkbox("Volume profile", value=True).classes("param-check tv-check")
            # Volby histogramu odsazené pod přepínačem jako v TradingView
            with ui.column().classes("indicator-style-group w-full"):
                with ui.row().classes("param-row w-full"):
                    self.fields["values"] = ui.checkbox("Values").classes("param-check tv-check param-label")
                    self.fields["valuesColor"] = ColorButton(FRVP_DEFAULTS["valuesColor"], title="Values")
                with _param_row("Width (% of the box)"):
                    self.fields["profileWidth"] = _int_number(FRVP_DEFAULTS["profileWidth"], 1, 100)
                with _param_row("Placement"):
                    self.fields["placement"] = _select(FRVP_PLACEMENTS, "placement")
                for key, label in PROFILE_COLORS.items():
                    with _param_row(label):
                        self.fields[key] = ColorButton(FRVP_DEFAULTS[key], title=label)
            # Čáry úrovní: zobrazení a tlačítko barvy s tloušťkou a stylem čáry
            for name in FRVP_LINES:
                default = FRVP_LINE_DEFAULTS[name]
                with ui.row().classes("plot-row w-full"):
                    check = ui.checkbox(LINE_LABELS[name], value=default["visible"]).classes(
                        "param-check tv-check plot-check"
                    )
                    button = PlotStyleButton(
                        {key: default[key] for key in LINE_STYLE_KEYS},
                        title=tr("{name} – barva a čára").format(name=LINE_LABELS[name]),
                    )
                self.lines[name] = (check, button)
            with _param_row("Histogram Box"):
                self.fields["boxColor"] = ColorButton(FRVP_DEFAULTS["boxColor"], title="Histogram Box")

    def build_coordinates(self) -> None:
        """Záložka Coordinates: pořadová čísla prvního a posledního baru rozsahu."""
        with ui.column().classes("indicator-style-group w-full"):
            with _param_row("#1 (bar)"):
                self.bar1 = _int_number(None)
            with _param_row("#2 (bar)"):
                self.bar2 = _int_number(None)
            ui.label(tr("Bary se číslují od první načtené svíčky grafu.")).classes("settings-note")

    def fill(self, options: dict) -> None:
        """Vyplní záložky Inputs a Style z normalizovaných voleb kresby (normalize_drawing_style)."""
        for key, field in self.fields.items():
            field.value = options[key]
        for name, (check, button) in self.lines.items():
            check.value = options[name]["visible"]
            button.value = {key: options[name][key] for key in LINE_STYLE_KEYS}

    def values(self) -> dict:
        """Volby záložek Inputs a Style (normalizuje je normalize_drawing_style)."""
        options = {key: field.value for key, field in self.fields.items()}
        for name, (check, button) in self.lines.items():
            options[name] = {**(button.value or {}), "visible": check.value}
        return options

    def fill_bars(self, bars) -> None:
        """Vyplní záložku Coordinates čísly barů [první, poslední] (jiný tvar = prázdná pole)."""
        valid = isinstance(bars, list) and len(bars) == 2
        self.bar1.value = bars[0] if valid else None
        self.bar2.value = bars[1] if valid else None

    def bars(self) -> list[int] | None:
        """Čísla barů ze záložky Coordinates; neplatné / prázdné pole = None."""
        try:
            return [int(self.bar1.value), int(self.bar2.value)]
        except (TypeError, ValueError):
            return None
