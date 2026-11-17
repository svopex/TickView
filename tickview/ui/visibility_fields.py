"""Pole záložky Visibility jako v TradingView (kresby a indikátory).

Pro každou jednotku timeframu (VISIBILITY_UNITS) zaškrtávátko, mez od,
posuvník rozsahu a mez do; změna čísla posune posuvník a naopak. Jednotky
bez rozsahu (Ticks, Ranges) mají jen zaškrtávátko.
"""

from nicegui import ui

from tickview.logic.styles import VISIBILITY_UNITS, normalize_visibility


class VisibilityFields:
    """Mřížka polí viditelnosti podle timeframu (vytvoří se v aktuálním kontextu UI)."""

    def __init__(self) -> None:
        self.fields: dict[str, dict] = {}
        # Potlačení vazeb mezí a posuvníku při hromadném vyplňování
        self._filling = False
        with ui.element("div").classes("visibility-grid w-full"):
            for unit, (unit_label, low, high) in VISIBILITY_UNITS.items():
                check = ui.checkbox(unit_label, value=True).classes("visibility-check tv-check")
                if low is None:
                    # Jen zaškrtnutí přes celý řádek mřížky
                    check.classes("visibility-check-only")
                    self.fields[unit] = {"enabled": check}
                    continue
                fields = {
                    "enabled": check,
                    "from": ui.number(value=low, min=low, max=high, step=1, format="%d").props(
                        "outlined dense"
                    ).classes("visibility-num"),
                    # Posuvník jako v TradingView: silná světlá dráha a bílé úchyty (kruh je
                    # v SVG Quasaru 60 % thumb-size)
                    "range": ui.range(min=low, max=high, step=1, value={"min": low, "max": high}).props(
                        "thumb-size=24px track-size=12px thumb-color=white selection-color=grey-4 "
                        "track-color=grey-4 inner-track-color=grey-4"
                    ).classes("visibility-range"),
                    "to": ui.number(value=high, min=low, max=high, step=1, format="%d").props(
                        "outlined dense"
                    ).classes("visibility-num"),
                }
                for source in ("from", "to", "range"):
                    fields[source].on_value_change(lambda _event, unit=unit, source=source: self._sync(unit, source))
                self.fields[unit] = fields

    def _sync(self, unit: str, source: str) -> None:
        """Vazba mezí a posuvníku jednotky.

        Parametry:
            unit: jednotka timeframu (klíč VISIBILITY_UNITS).
            source: změněné pole ("from", "to" nebo "range").
        """
        if self._filling:
            return
        fields = self.fields[unit]
        self._filling = True
        try:
            if source == "range":
                fields["from"].value = fields["range"].value["min"]
                fields["to"].value = fields["range"].value["max"]
            else:
                # Ořez do rozsahu jednotky a srovnání mezí jako při uložení
                rule = normalize_visibility({unit: {"from": fields["from"].value, "to": fields["to"].value}})[unit]
                fields["range"].value = {"min": rule["from"], "max": rule["to"]}
        finally:
            self._filling = False

    def fill(self, visibility) -> None:
        """Vyplní pole z viditelnosti (None = viditelné všude)."""
        values = normalize_visibility(visibility)
        self._filling = True
        try:
            for unit, fields in self.fields.items():
                fields["enabled"].value = values[unit]["enabled"]
                if "range" not in fields:
                    continue
                fields["from"].value = values[unit]["from"]
                fields["to"].value = values[unit]["to"]
                fields["range"].value = {"min": values[unit]["from"], "max": values[unit]["to"]}
        finally:
            self._filling = False

    def values(self) -> dict:
        """Viditelnost z polí (normalizace srovná neplatné meze)."""
        return normalize_visibility(
            {
                unit: {key: field.value for key, field in fields.items() if key != "range"}
                for unit, fields in self.fields.items()
            }
        )
