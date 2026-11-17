"""Časové připomínky: opakovaná notifikace v časovém okně (např. uzavření pozic před noční seancí).

Připomínka nezávisí na cenách ani signálech – hlídá jen čas. V okně od–do
(místní čas zvoleného pásma, ve zvolené dny) se posílá v pravidelném
rozestupu. Modul obsahuje jen čisté funkce a stav připomínky bez I/O;
odesílání řeší tickview/services/reminders.py.
"""

import json
import re
import uuid
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from tickview.indicators.api import Param
from tickview.logic.alerts import DEFAULT_NOTIFY, MAX_MESSAGE_LENGTH, normalize_notify

# Klíč nastavení se seznamem připomínek (JSON)
REMINDERS_SETTINGS_KEY = "reminders"
# Dny v týdnu (0 = pondělí) pro výběr v dialogu
WEEKDAYS = ("Po", "Út", "St", "Čt", "Pá", "So", "Ne")
# Výchozí připomínka (upozornění na uzavření pozic před noční seancí)
DEFAULT_REMINDER: dict = {
    "name": "Uzavření pozic",
    "start": "22:30",
    "end": "22:55",
    "interval": 5,
    "timezone": "Europe/Prague",
    "days": [0, 1, 2, 3, 4, 5, 6],
    "message": "Prosím uzavřete pozici, blíží se noční seance.",
    "notify": dict(DEFAULT_NOTIFY),
    "active": True,
}

# Rozestup upozornění v minutách
_INTERVAL = Param.int("interval", DEFAULT_REMINDER["interval"], "Rozestup (minuty)", min=1, max=240)

# Čas ve tvaru HH:MM (hodiny 0–23, minuty 00–59)
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(text) -> Optional[int]:
    """Čas "HH:MM" na minuty od půlnoci; neplatný tvar = None."""
    match = _HHMM.match(str(text or "").strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def normalize_reminder(data) -> dict:
    """Zkontroluje připomínku z dialogu; chyba vyhodí ValueError s českou zprávou."""
    if not isinstance(data, dict):
        raise ValueError("Neplatná připomínka.")
    name = str(data.get("name") or "").strip()[:80] or DEFAULT_REMINDER["name"]
    start, end = parse_hhmm(data.get("start")), parse_hhmm(data.get("end"))
    if start is None or end is None:
        raise ValueError("Začátek a konec okna zadejte ve tvaru HH:MM.")
    # Okno nepřechází přes půlnoc
    if end < start:
        raise ValueError("Konec okna musí být stejně nebo později než začátek (okno nesmí přecházet přes půlnoc).")
    interval = _INTERVAL.normalize(data.get("interval"))
    tz_name = str(data.get("timezone") or "").strip() or DEFAULT_REMINDER["timezone"]
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise ValueError(f"Neznámé časové pásmo: {tz_name}") from None
    days = sorted({day for day in data.get("days") or [] if isinstance(day, int) and 0 <= day <= 6})
    if not days:
        raise ValueError("Vyberte aspoň jeden den.")
    message = str(data.get("message") or "").strip() or DEFAULT_REMINDER["message"]
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"Zpráva může mít nejvýš {MAX_MESSAGE_LENGTH} znaků.")
    return {
        "id": str(data.get("id") or uuid.uuid4().hex[:8]),
        "name": name,
        "start": f"{start // 60:02d}:{start % 60:02d}",
        "end": f"{end // 60:02d}:{end % 60:02d}",
        "interval": interval,
        "timezone": tz_name,
        "days": days,
        "message": message,
        "notify": normalize_notify(data.get("notify") if isinstance(data.get("notify"), dict) else DEFAULT_NOTIFY),
        "active": bool(data.get("active", True)),
    }


def load_reminders(raw: Optional[str]) -> list[dict]:
    """Načte připomínky z JSON nastavení; poškozené vynechá."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    reminders = []
    for item in items if isinstance(items, list) else []:
        try:
            reminders.append(normalize_reminder(item))
        except ValueError:
            continue
    return reminders


def describe_reminder(reminder: dict) -> str:
    """Krátký popis do seznamu: „22:30–22:55 po 5 min · Po–Ne · Europe/Prague“."""
    days = reminder["days"]
    if days == list(range(7)):
        day_text = "Po–Ne"
    elif days == list(range(5)):
        day_text = "Po–Pá"
    else:
        day_text = ", ".join(WEEKDAYS[day] for day in days)
    return f"{reminder['start']}–{reminder['end']} po {reminder['interval']} min · {day_text} · {reminder['timezone']}"


class ReminderTracker:
    """Stav jedné připomínky: které upozornění okna už odešlo (slot) a ve který den.

    Slot = pořadí upozornění v okně (0 = na jeho začátku). Zaokrouhlení
    dolů zajistí, že upozornění odejde, i když kontrola přijde až několik
    minut po přesném čase slotu; každý slot odejde nejvýš jednou.
    """

    def __init__(self) -> None:
        self.day: Optional[str] = None
        self.last_slot = -1

    def check(self, reminder: dict, now_ms: int) -> bool:
        """Má se teď poslat upozornění? (zároveň si slot zapamatuje)"""
        local = datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo(reminder["timezone"]))
        day = local.date().isoformat()
        if day != self.day:
            # Nový den – celá série upozornění se povolí znovu
            self.day = day
            self.last_slot = -1
        if local.weekday() not in reminder["days"]:
            return False
        start, end = parse_hhmm(reminder["start"]), parse_hhmm(reminder["end"])
        minutes = local.hour * 60 + local.minute
        if not start <= minutes <= end:
            return False
        slot = (minutes - start) // reminder["interval"]
        if slot <= self.last_slot:
            return False
        self.last_slot = slot
        return True
