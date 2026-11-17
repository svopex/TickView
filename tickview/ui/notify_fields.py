"""Pole notifikací (Telegram, toast, zvuk, webhook) pro časové připomínky."""

from nicegui import ui

from tickview.logic.alerts import normalize_notify


class NotifyFields:
    """Zaškrtávátka kanálů notifikací a adresa webhooku (vytvoří se v aktuálním kontextu UI)."""

    def __init__(self) -> None:
        with ui.column().classes("notify-fields w-full"):
            self.telegram = ui.checkbox("Telegram")
            self.toast = ui.checkbox("Oznámení v aplikaci")
            self.sound = ui.checkbox("Zvuk")
            self.webhook = ui.input("Webhook URL (prázdné = neposílat)").props("outlined dense clearable").classes("w-full")

    def set(self, notify: dict) -> None:
        """Vyplní pole z nastavení notifikací."""
        self.telegram.value = bool(notify.get("telegram"))
        self.toast.value = bool(notify.get("toast"))
        self.sound.value = bool(notify.get("sound"))
        self.webhook.value = notify.get("webhook") or ""

    def get(self) -> dict:
        """Nastavení notifikací z polí; neplatná adresa webhooku vyhodí ValueError."""
        return normalize_notify({
            "telegram": self.telegram.value,
            "toast": self.toast.value,
            "sound": self.sound.value,
            "webhook": self.webhook.value or "",
        })
