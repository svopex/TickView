"""Obchodní seance (RTH / ETH) a časová pásma pro zobrazení grafu.

RTH (regular trading hours) = jen hlavní obchodní hodiny burzy, ETH
(extended trading hours) = včetně pre-market a after-hours. Zdroje, které
rozšířené hodiny posílají (IBKR, Alpaca), stahují vždy ETH data; RTH se
z nich vybírá filtrem (cache tak drží jednu sadu dat pro obě seance).
"""

import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from tickview.providers.base import TIMEFRAME_MS, Candle

# Hodnoty nastavení seance
SESSION_RTH = "rth"
SESSION_ETH = "eth"
# Čas ve tvaru HH:MM (hodiny 0–23, minuty 00–59), volitelně s vteřinami (pole type=time)
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")

# Časová pásma nabídky (jako v TradingView): IANA název -> město
TIMEZONE_CITIES = [
    ("Pacific/Honolulu", "Honolulu"),
    ("America/Anchorage", "Anchorage"),
    ("America/Juneau", "Juneau"),
    ("America/Los_Angeles", "Los Angeles"),
    ("America/Vancouver", "Vancouver"),
    ("America/Denver", "Denver"),
    ("America/Phoenix", "Phoenix"),
    ("America/Chicago", "Chicago"),
    ("America/Mexico_City", "Mexico City"),
    ("America/El_Salvador", "San Salvador"),
    ("America/Bogota", "Bogota"),
    ("America/Lima", "Lima"),
    ("America/New_York", "New York"),
    ("America/Toronto", "Toronto"),
    ("America/Caracas", "Caracas"),
    ("America/Halifax", "Halifax"),
    ("America/Argentina/Buenos_Aires", "Buenos Aires"),
    ("America/Santiago", "Santiago"),
    ("America/Sao_Paulo", "Sao Paulo"),
    ("Atlantic/Azores", "Azores"),
    ("Europe/Dublin", "Dublin"),
    ("Europe/Lisbon", "Lisbon"),
    ("Europe/London", "London"),
    ("Atlantic/Reykjavik", "Reykjavik"),
    ("Europe/Amsterdam", "Amsterdam"),
    ("Europe/Belgrade", "Belgrade"),
    ("Europe/Berlin", "Berlin"),
    ("Europe/Bratislava", "Bratislava"),
    ("Europe/Brussels", "Brussels"),
    ("Europe/Budapest", "Budapest"),
    ("Africa/Casablanca", "Casablanca"),
    ("Europe/Copenhagen", "Copenhagen"),
    ("Africa/Lagos", "Lagos"),
    ("Europe/Luxembourg", "Luxembourg"),
    ("Europe/Madrid", "Madrid"),
    ("Europe/Malta", "Malta"),
    ("Europe/Oslo", "Oslo"),
    ("Europe/Paris", "Paris"),
    ("Europe/Prague", "Prague"),
    ("Europe/Rome", "Rome"),
    ("Europe/Stockholm", "Stockholm"),
    ("Europe/Vienna", "Vienna"),
    ("Europe/Warsaw", "Warsaw"),
    ("Europe/Zurich", "Zurich"),
    ("Europe/Athens", "Athens"),
    ("Europe/Bucharest", "Bucharest"),
    ("Africa/Cairo", "Cairo"),
    ("Europe/Helsinki", "Helsinki"),
    ("Asia/Jerusalem", "Jerusalem"),
    ("Africa/Johannesburg", "Johannesburg"),
    ("Europe/Kyiv", "Kyiv"),
    ("Europe/Riga", "Riga"),
    ("Europe/Tallinn", "Tallinn"),
    ("Europe/Vilnius", "Vilnius"),
    ("Europe/Istanbul", "Istanbul"),
    ("Europe/Moscow", "Moscow"),
    ("Asia/Riyadh", "Riyadh"),
    ("Asia/Qatar", "Qatar"),
    ("Africa/Nairobi", "Nairobi"),
    ("Asia/Dubai", "Dubai"),
    ("Asia/Muscat", "Muscat"),
    ("Asia/Karachi", "Karachi"),
    ("Asia/Kolkata", "Kolkata"),
    ("Asia/Kathmandu", "Kathmandu"),
    ("Asia/Dhaka", "Dhaka"),
    ("Asia/Almaty", "Almaty"),
    ("Asia/Bangkok", "Bangkok"),
    ("Asia/Jakarta", "Jakarta"),
    ("Asia/Ho_Chi_Minh", "Ho Chi Minh"),
    ("Asia/Hong_Kong", "Hong Kong"),
    ("Asia/Shanghai", "Shanghai"),
    ("Asia/Singapore", "Singapore"),
    ("Asia/Taipei", "Taipei"),
    ("Australia/Perth", "Perth"),
    ("Asia/Manila", "Manila"),
    ("Asia/Tokyo", "Tokyo"),
    ("Asia/Seoul", "Seoul"),
    ("Australia/Adelaide", "Adelaide"),
    ("Australia/Brisbane", "Brisbane"),
    ("Australia/Sydney", "Sydney"),
    ("Pacific/Norfolk", "Norfolk Island"),
    ("Pacific/Auckland", "Auckland"),
    ("Pacific/Chatham", "Chatham Islands"),
]


def parse_hhmm(text) -> Optional[int]:
    """Čas "HH:MM" na minuty od půlnoci; neplatný tvar = None."""
    match = _HHMM.match(str(text or "").strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def utc_offset_label(tz_name: str, now: Optional[datetime] = None) -> str:
    """Aktuální posun pásma proti UTC ve tvaru "UTC+1", "UTC-5", "UTC+5:30", "UTC".

    Parametry:
        tz_name: IANA název časového pásma.
        now: okamžik, pro který se posun počítá (letní čas); výchozí = teď.
    """
    moment = now or datetime.now(timezone.utc)
    return _format_offset(moment.astimezone(ZoneInfo(tz_name)).utcoffset() or timedelta(0))


def _format_offset(offset: timedelta) -> str:
    """Posun proti UTC jako text "UTC+1" / "UTC-5" / "UTC+5:30" / "UTC"."""
    minutes = int(offset.total_seconds() // 60)
    if minutes == 0:
        return "UTC"
    sign = "+" if minutes > 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


def timezone_options(now: Optional[datetime] = None) -> list[tuple[str, str]]:
    """Položky nabídky časových pásem seřazené podle posunu: [(IANA, "(UTC+1) Prague")]."""
    moment = now or datetime.now(timezone.utc)
    items = []
    for tz_name, city in TIMEZONE_CITIES:
        offset = moment.astimezone(ZoneInfo(tz_name)).utcoffset() or timedelta(0)
        items.append((offset, city, tz_name, f"({_format_offset(offset)}) {city}"))
    items.sort(key=lambda item: (item[0], item[1]))
    return [(tz_name, label) for _, _, tz_name, label in items]


def filter_session(
    candles: list[Candle], timeframe: str, session: Optional[tuple[str, str, str]]
) -> list[Candle]:
    """Ponechá jen svíčky hlavní obchodní seance (RTH).

    Svíčka zůstane, když se její období alespoň zčásti překrývá s hlavními
    hodinami v pracovní den (např. 1h svíčka 9:00 se seancí od 9:30).
    Denní a delší timeframy ani instrumenty bez seance (krypto, forex) se
    nefiltrují.

    Parametry:
        candles: svíčky (open_time v ms UTC).
        timeframe: kanonický timeframe svíček.
        session: (IANA pásmo burzy, "HH:MM" otevření, "HH:MM" zavření) nebo None.
    """
    step_ms = TIMEFRAME_MS[timeframe]
    if session is None or step_ms >= TIMEFRAME_MS["1D"]:
        return candles
    tz_name, open_text, close_text = session
    zone = ZoneInfo(tz_name)
    open_time = time.fromisoformat(open_text)
    close_time = time.fromisoformat(close_text)
    result = []
    for candle in candles:
        start = datetime.fromtimestamp(candle.open_time / 1000, tz=timezone.utc).astimezone(zone)
        # Víkend: hlavní seance neprobíhá
        if start.weekday() >= 5:
            continue
        session_start = start.replace(hour=open_time.hour, minute=open_time.minute, second=0, microsecond=0)
        session_end = start.replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
        end = start + timedelta(milliseconds=step_ms)
        if end > session_start and start < session_end:
            result.append(candle)
    return result


def in_session(candle: Candle, timeframe: str, session: Optional[tuple[str, str, str]]) -> bool:
    """True, když svíčka patří do hlavní seance (viz filter_session)."""
    return bool(filter_session([candle], timeframe, session))
