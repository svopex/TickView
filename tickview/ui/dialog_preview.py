"""Živý náhled nastavení v dialogu (indikátor, kresba): změny polí se hned
promítnou do grafu, Ok je potvrdí a zavření bez Ok je vrátí – jako v TradingView.

Hodnoty se čtou časovačem z celého dialogu (všechny záložky, i barvy
a nabídky v popupech), takže jednotlivá pole nemusí hlásit změny. Náhled
se pošle až po ustálení hodnot (stejné při dvou čteních) – psaní čísla
nepřepočítává po každé číslici.
"""

import inspect
from collections.abc import Callable

from nicegui import ui

# Interval čtení polí dialogu v sekundách
PREVIEW_INTERVAL_S = 0.3


class DialogPreview:
    """Náhled hodnot jednoho dialogu v grafu.

    Parametry:
        dialog: dialog NiceGUI; časovač běží jen při otevřeném dialogu a jeho
            zavření bez potvrzení vrátí graf do původního stavu.
        collect: vrátí aktuální hodnoty polí (porovnatelné ==), nebo None
            (neplatné / rozepsané hodnoty – náhled se přeskočí). Při otevření
            dialogu jsou pole už vyplněná – jejich hodnoty graf ukazuje.
        apply: promítne hodnoty do grafu (bez uložení); může být async.
        restore: vrátí graf do stavu před otevřením dialogu; může být async.
    """

    def __init__(self, dialog: ui.dialog, collect: Callable, apply: Callable, restore: Callable) -> None:
        self.collect = collect
        self.apply = apply
        self.restore = restore
        # Hodnoty zobrazené v grafu, hodnoty z minulého čtení a příznak, že graf ukazuje náhled
        self.shown = None
        self.seen = None
        self.dirty = False
        self.timer = ui.timer(PREVIEW_INTERVAL_S, self._tick, active=False)
        dialog.on_value_change(self._toggle)

    def confirm(self) -> None:
        """Ok: hodnoty jsou uložené, zavření dialogu je nevrací."""
        self.dirty = False

    async def _tick(self) -> None:
        # Náhled jen ustálených, platných a dosud nezobrazených hodnot
        values = self.collect()
        stable = values == self.seen
        self.seen = values
        if values is None or not stable or values == self.shown:
            return
        self.shown = values
        self.dirty = True
        await _call(self.apply, values)

    async def _toggle(self, event) -> None:
        # Otevření: výchozí stav z vyplněných polí a čtení polí časovačem;
        # zavření bez Ok (Cancel, křížek, Esc, klik mimo) vrátí graf, pokud ukazuje náhled
        self.timer.active = event.value
        if event.value:
            self.shown = self.seen = self.collect()
            self.dirty = False
        elif self.dirty:
            await _call(self.restore)


async def _call(callback: Callable, *args) -> None:
    """Zavolá callback a počká na něj, je-li asynchronní."""
    result = callback(*args)
    if inspect.isawaitable(result):
        await result
