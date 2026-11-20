"""Dialogy Settings ve stylu TradingView: hlavička, tlačítka a nabídka šablon („Template“)."""

import json
from collections.abc import Awaitable, Callable

from nicegui import ui

from tickview.i18n import tr
from tickview.logic.cache import KlineCache
from tickview.logic.styles import (
    MAX_NAME_LENGTH,
    clean_name,
    default_template_key,
    delete_template,
    load_templates,
    save_template,
    templates_key,
)


def settings_header(dialog: ui.dialog, title: str = "") -> ui.label:
    """Hlavička dialogu Settings: titulek vlevo, křížek vpravo; vrací prvek titulku.

    Parametry:
        dialog: dialog, který křížek zavře.
        title: počáteční text titulku.
    """
    with ui.row().classes("tv-settings-header w-full"):
        title_label = ui.label(title).classes("tv-settings-title")
        ui.space()
        ui.button(icon="close", on_click=dialog.close).props("flat dense round")
    return title_label


def settings_buttons(dialog: ui.dialog, on_ok, ok_text: str = "Ok") -> None:
    """Tlačítka Cancel a Ok zarovnaná vpravo (volá se uvnitř řádku patičky).

    Tlačítka nemají barvu Quasaru (color=None) – vzhled podle motivu řeší CSS.

    Parametry:
        dialog: dialog, který Cancel zavře.
        on_ok: handler potvrzovacího tlačítka.
        ok_text: popisek potvrzovacího tlačítka.
    """
    ui.space()
    ui.button("Cancel", on_click=dialog.close, color=None).props("outline no-caps").classes("tv-btn-cancel")
    ui.button(ok_text, on_click=on_ok, color=None).props("unelevated no-caps").classes("tv-btn-ok")


async def load_default_template(cache: KlineCache, scope: str) -> dict | None:
    """Vrátí uživatelskou výchozí šablonu rozsahu („Save as default“), nebo None.

    Parametry:
        cache: úložiště nastavení.
        scope: rozsah šablon, např. "indicator.rsi".
    """
    raw = await cache.get_setting(default_template_key(scope))
    if not raw:
        return None
    try:
        values = json.loads(raw)
    except ValueError:
        return None
    return values if isinstance(values, dict) else None


class TemplateMenu:
    """Tlačítko „Template“ s nabídkou jako v TradingView.

    Nabídka: „Apply defaults“, „Save as…“, volitelně „Save as default“
    a „Reset default“, pod čarou uložené šablony (klik = použít, křížek =
    smazat). Šablony se ukládají do nastavení v SQLite podle rozsahu (graf,
    typ indikátoru, typ kresby).

    Parametry:
        cache: úložiště nastavení (šablony).
        get_scope: vrací aktuální rozsah, např. "chart" nebo "drawing.trend".
        collect: vrací hodnoty dialogu k uložení, nebo None (neplatné hodnoty –
            collect sám upozorní uživatele).
        apply: promítne hodnoty šablony do dialogu.
        apply_factory: nastaví v dialogu tovární výchozí hodnoty.
        allow_default: nabízet „Save as default“ (nové instance pak převezmou
            uloženou výchozí šablonu – indikátory, kresby).
        on_default_change: async callback po uložení / zrušení výchozí šablony
            (např. předání výchozího stylu nových kreseb do grafu).
    """

    def __init__(
        self,
        cache: KlineCache,
        get_scope: Callable[[], str],
        collect: Callable[[], dict | None],
        apply: Callable[[dict], None],
        apply_factory: Callable[[], None],
        allow_default: bool = False,
        on_default_change: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._cache = cache
        self._get_scope = get_scope
        self._collect = collect
        self._apply = apply
        self._apply_factory = apply_factory
        self._allow_default = allow_default
        self._on_default_change = on_default_change
        # Tlačítko s nabídkou (Quasar QMenu se otevírá klikem na rodiče)
        with ui.button("Template", color=None).props("outline no-caps icon-right=expand_less").classes("tpl-button") as self.button:
            self.menu = ui.menu().props("anchor='top left' self='bottom left'").classes("tpl-menu")
        # Dialog pro název nové šablony („Save as…“)
        with ui.dialog() as self.name_dialog, ui.card().classes("tpl-name-dialog"):
            ui.label(tr("Uložit šablonu")).classes("tv-settings-title")
            too_long = tr("Nejvýš {max_length} znaků").format(max_length=MAX_NAME_LENGTH)
            self.name_input = (
                ui.input(tr("Název šablony"), validation={too_long: lambda value: len(value or "") <= MAX_NAME_LENGTH})
                .props("outlined dense autofocus")
                .classes("w-full")
            )
            self.name_input.on("keydown.enter", self._save_named)
            with ui.row().classes("tv-settings-actions w-full"):
                settings_buttons(self.name_dialog, self._save_named, "Save")

    def set_visible(self, visible: bool) -> None:
        """Zobrazí / skryje tlačítko (např. kresba bez nastavitelného stylu)."""
        self.button.set_visibility(visible)

    async def _templates(self) -> dict[str, dict]:
        # Uložené šablony aktuálního rozsahu (seřazené podle názvu)
        return load_templates(await self._cache.get_setting(templates_key(self._get_scope())))

    async def _store(self, templates: dict[str, dict]) -> None:
        # Uloží šablony aktuálního rozsahu do nastavení
        await self._cache.set_setting(templates_key(self._get_scope()), json.dumps(templates))

    async def refresh(self) -> None:
        """Přestaví položky nabídky podle uložených šablon (volá se při otevření dialogu)."""
        templates = await self._templates()
        has_default = self._allow_default and await load_default_template(self._cache, self._get_scope()) is not None
        self.menu.clear()
        with self.menu:
            ui.menu_item("Apply defaults", on_click=self._apply_defaults).classes("tpl-item")
            ui.menu_item("Save as…", on_click=self._open_name_dialog).classes("tpl-item")
            if self._allow_default:
                ui.menu_item("Save as default", on_click=self._save_default).classes("tpl-item")
                if has_default:
                    ui.menu_item("Reset default", on_click=self._reset_default).classes("tpl-item")
            if templates:
                ui.separator()
            for name in templates:
                with ui.menu_item(on_click=self._make_apply(name)).classes("tpl-item tpl-saved"):
                    ui.label(name).classes("tpl-name")
                    ui.space()
                    # Křížek smaže šablonu; .stop zabrání současnému použití šablony
                    ui.button(icon="close").props("flat dense round size=xs").classes("tpl-delete").tooltip(
                        tr("Smazat šablonu")
                    ).on("click.stop", self._make_delete(name))

    def _make_apply(self, name: str) -> Callable[[], Awaitable[None]]:
        async def _handler() -> None:
            templates = await self._templates()
            if name in templates:
                self._apply(templates[name])
                ui.notify(tr("Použita šablona „{name}“.").format(name=name))

        return _handler

    def _make_delete(self, name: str) -> Callable[[], Awaitable[None]]:
        async def _handler() -> None:
            templates = delete_template(await self._templates(), name)
            await self._store(templates)
            await self.refresh()
            ui.notify(tr("Šablona „{name}“ smazána.").format(name=name))

        return _handler

    async def _apply_defaults(self) -> None:
        # Uživatelská výchozí šablona má přednost před továrním nastavením
        saved = await load_default_template(self._cache, self._get_scope()) if self._allow_default else None
        if saved is not None:
            self._apply(saved)
        else:
            self._apply_factory()

    def _open_name_dialog(self) -> None:
        self.name_input.value = ""
        self.name_dialog.open()

    async def _save_named(self) -> None:
        # Uloží hodnoty dialogu pod zadaným názvem (stejný název přepíše)
        name = clean_name(self.name_input.value)
        if not name:
            ui.notify(tr("Zadejte název šablony (1–{max_length} znaků).").format(max_length=MAX_NAME_LENGTH), type="warning")
            return
        values = self._collect()
        if values is None:
            return
        templates = await self._templates()
        overwritten = any(existing.lower() == name.lower() for existing in templates)
        templates = save_template(templates, name, values)
        await self._store(templates)
        self.name_dialog.close()
        await self.refresh()
        # Celé věty zvlášť – v překladu se tvar slovesa neskládá z kousků
        message = tr("Šablona „{name}“ přepsána.") if overwritten else tr("Šablona „{name}“ uložena.")
        ui.notify(message.format(name=name))

    async def _save_default(self) -> None:
        # Výchozí šablona: převezmou ji nově přidané instance (indikátory, kresby)
        values = self._collect()
        if values is None:
            return
        await self._cache.set_setting(default_template_key(self._get_scope()), json.dumps(values))
        if self._on_default_change:
            await self._on_default_change()
        await self.refresh()
        ui.notify(tr("Uloženo jako výchozí nastavení."))

    async def _reset_default(self) -> None:
        # Zruší uživatelskou výchozí šablonu a vrátí tovární hodnoty
        await self._cache.set_setting(default_template_key(self._get_scope()), "")
        if self._on_default_change:
            await self._on_default_change()
        self._apply_factory()
        await self.refresh()
        ui.notify(tr("Výchozí nastavení vráceno na tovární."))
