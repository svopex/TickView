"""Časové připomínky na pozadí serveru (opakovaná notifikace v časovém okně).

ReminderService každých CHECK_INTERVAL_S projde aktivní připomínky a při
novém slotu okna pošle notifikaci přes AlertEngine (toast, Telegram,
webhook, log). Běží i bez otevřené stránky.
"""

import asyncio
import json
import logging
from typing import Optional

from tickview.logic.cache import KlineCache
from tickview.logic.reminders import REMINDERS_SETTINGS_KEY, ReminderTracker, load_reminders
from tickview.providers.base import utc_now_ms

logger = logging.getLogger(__name__)

# Jak často se připomínky kontrolují (s)
CHECK_INTERVAL_S = 20.0


class ReminderService:
    """Vyhodnocuje časové připomínky a posílá jejich notifikace.

    Parametry:
        cache: úložiště nastavení (seznam připomínek).
        alert_engine: engine alertů (odesílání notifikací a log).
    """

    def __init__(self, cache: KlineCache, alert_engine) -> None:
        self._cache = cache
        self._alert_engine = alert_engine
        self.reminders: list[dict] = []
        # Stav připomínek podle id (upravená připomínka si ponechá odeslané sloty dne)
        self._trackers: dict[str, ReminderTracker] = {}
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Načte připomínky a spustí kontrolu."""
        self.reminders = load_reminders(await self._cache.get_setting(REMINDERS_SETTINGS_KEY))
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def save(self, reminders: list[dict]) -> None:
        """Uloží seznam připomínek (už normalizovaných) a začne podle něj kontrolovat."""
        self.reminders = list(reminders)
        await self._cache.set_setting(REMINDERS_SETTINGS_KEY, json.dumps(self.reminders))
        ids = {reminder["id"] for reminder in self.reminders}
        self._trackers = {key: tracker for key, tracker in self._trackers.items() if key in ids}

    async def _loop(self) -> None:
        while True:
            try:
                await self.check(utc_now_ms())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Kontrola připomínek selhala")
            await asyncio.sleep(CHECK_INTERVAL_S)

    async def check(self, now_ms: int) -> None:
        """Pošle notifikace připomínek, které mají v tomto okamžiku nový slot."""
        for reminder in list(self.reminders):
            tracker = self._trackers.setdefault(reminder["id"], ReminderTracker())
            # Sloty se počítají i u vypnuté připomínky – po zapnutí uprostřed okna odejde až další slot
            fire = tracker.check(reminder, now_ms)
            if fire and reminder["active"]:
                await self._alert_engine.notify_external(
                    reminder["notify"], source="reminder", symbol=reminder["name"], timeframe="připomínka",
                    message=reminder["message"],
                )
