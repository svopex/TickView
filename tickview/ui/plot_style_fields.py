"""Výstupy indikátoru a Output values v záložce Style (jako v TradingView).

Každý výstup (čára, histogram…) má řádek: zaškrtávátko zobrazení s názvem,
tlačítko barvy s tloušťkou a stylem čáry a tlačítko druhu vykreslení
(Plot type) s přepínačem cenové linky (Price line). Pod výstupy je sekce
Output values – přesnost hodnot (Precision), popisky na cenové ose (Labels
on price scale) a hodnoty v legendě (Values in status line).
"""

from nicegui import ui

from tickview.i18n import tr
from tickview.indicators.api import UP_COLOR
from tickview.logic.styles import PLOT_PRECISIONS, PLOT_TYPES, normalize_color
from tickview.ui.icons import svg
from tickview.ui.plot_style_button import PlotStyleButton

# Volby přesnosti: "default" = podle instrumentu, jinak počet desetinných míst
PRECISION_OPTIONS = {"default": "Default", **{digits: str(digits) for digits in PLOT_PRECISIONS}}


class PlotTypeButton:
    """Tlačítko druhu vykreslení výstupu s nabídkou: Price line a seznam druhů.

    Parametry:
        plot_type: zvolený druh (klíč PLOT_TYPES).
        price_line: cenová linka u poslední hodnoty zapnutá.
    """

    def __init__(self, plot_type: str, price_line: bool) -> None:
        self.type = plot_type
        self.items: dict[str, ui.item] = {}
        with ui.button(color=None).props("flat dense no-caps").classes("tv-plot-type-btn") as self.button:
            self.icon = ui.html(svg(f"plot_{plot_type}"), sanitize=False).classes("tv-icon-wrap")
            self.button.tooltip("Plot type")
            # Nabídka se nezavírá při přepnutí Price line, jen při volbě druhu
            with ui.menu().classes("tv-plot-type-menu") as self.menu:
                with ui.row().classes("tv-plot-price-line"):
                    ui.label("Price line")
                    ui.space()
                    self.price_line = ui.switch(value=price_line).props("dense")
                ui.separator()
                for key, label in PLOT_TYPES.items():
                    with ui.item(on_click=lambda key=key: self.select(key)).classes("tv-plot-type-item") as item:
                        ui.html(svg(f"plot_{key}"), sanitize=False).classes("tv-icon-wrap")
                        ui.label(label)
                    self.items[key] = item
        self._paint()

    def select(self, plot_type: str) -> None:
        """Zvolí druh vykreslení a zavře nabídku."""
        self.type = plot_type
        self._paint()
        self.menu.close()

    def _paint(self) -> None:
        # Ikona tlačítka a zvýraznění zvoleného druhu v nabídce
        self.icon.content = svg(f"plot_{self.type}")
        for key, item in self.items.items():
            if key == self.type:
                item.classes(add="menu-item-active")
            else:
                item.classes(remove="menu-item-active")


class PlotStyleFields:
    """Řádky výstupů a sekce Output values v záložce Style dialogu nastavení indikátoru.

    Prvky se vytvoří v aktuálním kontejneru NiceGUI; řádky výstupů se při
    každém fill přestaví podle výstupů indikátoru.
    """

    def __init__(self) -> None:
        self.plots_column = ui.column().classes("indicator-style-group w-full")
        with ui.column().classes("indicator-style-group w-full") as self.output_group:
            ui.label("Output values").classes("tv-settings-section param-group")
            with ui.row().classes("param-row w-full"):
                ui.label("Precision").classes("param-label")
                self.precision = ui.select(PRECISION_OPTIONS, value="default").props(
                    "outlined dense options-dense"
                ).classes("param-control")
            self.labels_on_scale = ui.checkbox("Labels on price scale").classes("param-check tv-check")
            self.values_in_status = ui.checkbox("Values in status line").classes("param-check tv-check")
        # Řádky výstupů: název, výchozí vzhled (s barvou, kterou tlačítko ukazuje) a prvky
        self.rows: list[dict] = []

    def fill(self, plots: list[tuple[str, dict]], style: dict) -> None:
        """Vyplní řádky výstupů a Output values.

        Parametry:
            plots: výstupy [(název, výchozí vzhled z plot_defaults)]; prázdný
                seznam = indikátor bez výstupů (sekce se skryjí).
            style: normalizovaný styl instance (normalize_indicator_style).
        """
        self.rows = []
        self.plots_column.clear()
        with self.plots_column:
            for name, plot_defaults in plots:
                # Histogram bez vlastní barvy se barví podle znaménka – tlačítko ukáže
                # barvu růstu a ta se pak bere jako výchozí (neuloží se jako změna)
                defaults = {**plot_defaults, "color": plot_defaults["color"] or UP_COLOR}
                current = {**defaults, **style["plots"].get(name, {})}
                with ui.row().classes("plot-row w-full"):
                    visible = ui.checkbox(name, value=current["visible"]).classes("param-check tv-check plot-check")
                    button = PlotStyleButton(
                        {key: current[key] for key in ("color", "width", "lineStyle")},
                        title=tr("{name} – barva a čára").format(name=name),
                    )
                    plot_type = PlotTypeButton(current["type"], current["priceLine"])
                self.rows.append({"name": name, "defaults": defaults, "visible": visible, "style": button,
                                  "type": plot_type})
        self.plots_column.set_visibility(bool(plots))
        self.output_group.set_visibility(bool(plots))
        precision = style["precision"]
        self.precision.value = "default" if precision is None else precision
        self.labels_on_scale.value = style["labels_on_scale"]
        self.values_in_status.value = style["values_in_status"]

    def values(self) -> dict:
        """Hodnoty sekcí jako část stylu instance.

        Vrací {"labels_on_scale", "values_in_status", "precision", "plots"};
        u výstupů jen změny proti výchozímu vzhledu (výchozí se neukládá).
        """
        plots: dict = {}
        for row in self.rows:
            defaults = row["defaults"]
            value = row["style"].value or {}
            # Aktuální vzhled z prvků řádku; uloží se jen klíče odlišné od výchozích
            current = {
                "visible": bool(row["visible"].value),
                "color": normalize_color(value.get("color")) or defaults["color"],
                "width": value.get("width"),
                "lineStyle": value.get("lineStyle"),
                "type": row["type"].type,
                "priceLine": bool(row["type"].price_line.value),
            }
            changes = {key: item for key, item in current.items() if item != defaults[key]}
            if changes:
                plots[row["name"]] = changes
        precision = self.precision.value
        return {
            "labels_on_scale": bool(self.labels_on_scale.value),
            "values_in_status": bool(self.values_in_status.value),
            "precision": None if precision == "default" else precision,
            "plots": plots,
        }
