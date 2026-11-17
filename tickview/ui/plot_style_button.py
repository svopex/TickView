"""Tlačítko stylu výstupu indikátoru – barva, tloušťka a styl čáry (vlastní komponenta NiceGUI)."""

from nicegui.elements.mixins.value_element import ValueElement

from tickview.logic.styles import PLOT_LINE_STYLES, PLOT_WIDTHS


class PlotStyleButton(ValueElement[dict], component="plot_style_button.js"):
    """Vzorek barvy s náhledem čáry; klik otevře výběr barvy s Thickness a Line style.

    Hodnota je {"color", "width", "lineStyle"} (barva #rrggbb nebo rgba(),
    tloušťka z PLOT_WIDTHS, styl "solid" / "dashed" / "dotted").

    Parametry:
        value: počáteční hodnota.
        title: tooltip tlačítka.
    """

    VALUE_PROP = "value"

    def __init__(self, value: dict, *, title: str | None = None) -> None:
        # Posuvník průhlednosti posílá změny průběžně – do Pythonu nejvýš 20× za s
        super().__init__(value=dict(value), on_value_change=None, throttle=0.05)
        # Volby panelu Thickness a Line style
        self._props["widths"] = list(PLOT_WIDTHS)
        self._props["lineStyles"] = list(PLOT_LINE_STYLES)
        if title:
            self._props["title"] = title
