"""Tlačítko barvy s výběrem ve stylu TradingView (vlastní komponenta NiceGUI)."""

from nicegui.elements.mixins.value_element import ValueElement
from nicegui.events import Handler, ValueChangeEventArguments


class ColorButton(ValueElement[str], component="color_button.js"):
    """Vzorek barvy; klik otevře panel s paletou, vlastními barvami a průhledností.

    Hodnota je barva #rrggbb, nebo rgba(r, g, b, a) při průhlednosti pod 100 %.

    Parametry:
        value: počáteční barva.
        on_change: callback při změně barvy (volá se i během tažení posuvníku
            průhlednosti, proto je událost přiškrcená).
        title: tooltip tlačítka.
    """

    VALUE_PROP = "value"

    def __init__(
        self,
        value: str = "#2962ff",
        *,
        on_change: Handler[ValueChangeEventArguments[str]] | None = None,
        title: str | None = None,
    ) -> None:
        # Posuvník průhlednosti posílá změny průběžně – do Pythonu nejvýš 20× za s
        super().__init__(value=value, on_value_change=on_change, throttle=0.05)
        if title:
            self._props["title"] = title
