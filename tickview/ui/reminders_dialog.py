"""Dialog Časové připomínky: seznam připomínek, přidání, úprava, zapnutí a smazání."""

from nicegui import ui

from tickview.i18n import tr, trc
from tickview.logic.reminders import DEFAULT_REMINDER, WEEKDAYS, describe_reminder, normalize_reminder
from tickview.logic.sessions import timezone_options
from tickview.services.reminders import ReminderService
from tickview.ui.notify_fields import NotifyFields
from tickview.ui.templates import settings_buttons, settings_header


class RemindersDialog:
    """Správa časových připomínek (vytvoří dialogy v aktuálním kontextu stránky).

    Parametry:
        service: služba připomínek na serveru (drží a ukládá seznam).
    """

    def __init__(self, service: ReminderService) -> None:
        self.service = service
        # Upravovaná připomínka (None = nová)
        self.editing: dict | None = None
        with ui.dialog() as self.dialog, ui.card().classes("tv-settings reminders-dialog"):
            settings_header(self.dialog, tr("Časové připomínky"))
            ui.label(
                tr("Opakovaná notifikace v časovém okně, např. výzva k uzavření pozic před noční seancí.")
            ).classes("settings-note")
            self.list = ui.column().classes("reminders-list w-full")
            with ui.row().classes("tv-settings-actions w-full"):
                ui.button(tr("Přidat připomínku"), icon="add", color=None, on_click=lambda: self.open_edit(None)).props(
                    "flat dense no-caps"
                )
                ui.space()
                ui.button(tr("Zavřít"), color=None, on_click=self.dialog.close).props("flat no-caps").classes("tv-btn-cancel")
        self._build_edit()

    def _build_edit(self) -> None:
        with ui.dialog() as self.edit_dialog, ui.card().classes("tv-settings"):
            self.edit_title = settings_header(self.edit_dialog, tr("Připomínka"))
            self.name = ui.input(tr("Název")).props("outlined dense").classes("w-full")
            with ui.row().classes("tv-settings-row w-full"):
                self.start = ui.input(tr("Od (HH:MM)")).props("outlined dense")
                self.end = ui.input(tr("Do (HH:MM)")).props("outlined dense")
                self.interval = ui.number(tr("Rozestup (min)"), min=1, max=240, step=1).props("outlined dense")
            self.timezone = ui.select(dict(timezone_options()), label=tr("Časové pásmo"), with_input=True).props(
                "outlined dense options-dense"
            ).classes("w-full")
            with ui.row().classes("reminder-days"):
                # Zkratky dnů jsou v logic.reminders; přeloží se až při zobrazení
                self.days = [ui.checkbox(trc("weekday", label)) for label in WEEKDAYS]
            self.message = ui.textarea(tr("Zpráva")).props("outlined dense autogrow").classes("w-full")
            ui.label(tr("Notifikace")).classes("tv-settings-section")
            self.notify = NotifyFields()
            with ui.row().classes("tv-settings-actions w-full"):
                ui.space()
                settings_buttons(self.edit_dialog, self.submit_edit)

    # ---------- seznam ----------

    def open(self) -> None:
        self.render()
        self.dialog.open()

    def make_action(self, action: str, reminder_id: str):
        async def _handler(_event=None) -> None:
            await self.run_action(action, reminder_id)

        return _handler

    def render(self) -> None:
        """Překreslí seznam připomínek."""
        self.list.clear()
        with self.list:
            if not self.service.reminders:
                ui.label(tr("Žádná připomínka.")).classes("alert-empty")
            for reminder in self.service.reminders:
                with ui.row().classes("alert-row w-full"):
                    with ui.column().classes("alert-text"):
                        ui.label(reminder["name"]).classes("alert-title")
                        ui.label(describe_reminder(reminder)).classes("alert-meta")
                    ui.space()
                    ui.switch(value=reminder["active"], on_change=self.make_action("toggle", reminder["id"])).props(
                        "dense"
                    ).tooltip(tr("Zapnout / vypnout"))
                    ui.button(icon="edit", color=None, on_click=lambda _e, rid=reminder["id"]: self.open_edit(rid)).props(
                        "flat dense round size=sm"
                    ).tooltip(tr("Upravit"))
                    ui.button(icon="delete", color=None, on_click=self.make_action("delete", reminder["id"])).props(
                        "flat dense round size=sm"
                    ).tooltip(tr("Smazat"))

    async def run_action(self, action: str, reminder_id: str) -> None:
        # Zapnutí / vypnutí nebo smazání připomínky
        reminders = []
        for reminder in self.service.reminders:
            if reminder["id"] != reminder_id:
                reminders.append(reminder)
            elif action == "toggle":
                reminders.append({**reminder, "active": not reminder["active"]})
        await self.service.save(reminders)
        self.render()

    # ---------- úprava ----------

    def open_edit(self, reminder_id: str | None) -> None:
        """Dialog připomínky: existující (id), nebo nová s výchozími hodnotami."""
        reminder = next((item for item in self.service.reminders if item["id"] == reminder_id), None)
        self.editing = reminder
        values = reminder or DEFAULT_REMINDER
        self.edit_title.text = tr("Upravit připomínku") if reminder else tr("Nová připomínka")
        self.name.value = values["name"]
        self.start.value = values["start"]
        self.end.value = values["end"]
        self.interval.value = values["interval"]
        self.timezone.value = values["timezone"] if values["timezone"] in self.timezone.options else None
        for index, checkbox in enumerate(self.days):
            checkbox.value = index in values["days"]
        self.message.value = values["message"]
        self.notify.set(values["notify"])
        self.edit_dialog.open()

    async def submit_edit(self) -> None:
        """Ověří a uloží připomínku; při chybě dialog zůstane otevřený."""
        current = self.editing
        try:
            reminder = normalize_reminder({
                "id": current["id"] if current else None,
                "name": self.name.value,
                "start": self.start.value,
                "end": self.end.value,
                "interval": self.interval.value,
                "timezone": self.timezone.value,
                "days": [index for index, checkbox in enumerate(self.days) if checkbox.value],
                "message": self.message.value,
                "notify": self.notify.get(),
                "active": current["active"] if current else True,
            })
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return
        if current:
            reminders = [reminder if item["id"] == current["id"] else item for item in self.service.reminders]
        else:
            reminders = [*self.service.reminders, reminder]
        await self.service.save(reminders)
        self.edit_dialog.close()
        self.render()
