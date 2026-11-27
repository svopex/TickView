"""Přepínač voleb s ikonami místo textu (Quasar QBtnToggle) – šetří místo v liště."""

from nicegui import ui


class IconToggle(ui.toggle):
    """Přepínač, jehož volby jsou ikony Material s tooltipem (title).

    Parametry:
        options: {hodnota: popisek} – popisek je tooltip a přístupný název tlačítka.
        icons: {hodnota: název ikony Material}.
        value: zvolená hodnota.
        on_change: callback při změně volby.
    """

    def __init__(self, options: dict, icons: dict, *, value=None, on_change=None) -> None:
        # Ikony musí existovat dřív, než rodič poprvé sestaví volby (_update_options)
        self._icons = icons
        super().__init__(options, value=value, on_change=on_change)

    def _update_options(self) -> None:
        # Volby sestaví rodič; ikona nahradí text, popisek je tooltip a název pro čtečky
        super()._update_options()
        for option, value in zip(self._props["options"], self._values):
            label = option.pop("label")
            option["icon"] = self._icons[value]
            option["attrs"] = {"title": label, "aria-label": label}
