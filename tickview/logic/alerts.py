"""Logika alertů ve stylu TradingView: podmínky, spouštění, kontrola a texty.

Modul obsahuje jen čisté funkce a třídu AlertTracker (stav vyhodnocení
jednoho alertu); stream dat, ukládání a odesílání notifikací řeší
tickview/services/alerts.py.

Alert je slovník:
    {"id", "source", "symbol", "timeframe", "condition", "target", "trigger",
     "expires_at", "message", "notify", "active", "status", "created_at",
     "last_fired_at", "fire_count"}
kde target je {"type": "value", "price": cena},
{"type": "drawing", "id": id kresby, "kind": druh kresby} nebo
{"type": "indicator", "kind": id indikátoru, "params": parametry, "minutes": předstih}
(alert na signál indikátoru, např. Svopex TTW).
"""

import json
import math
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from tickview.indicators.api import Param
from tickview.indicators.compute import instance_params
from tickview.indicators.registry import get_indicator
from tickview.providers.base import TIMEFRAME_MS, Candle

# Podmínky jako v TradingView (id -> popisek v dialogu a ve zprávě)
CONDITIONS: dict[str, str] = {
    "crossing": "Crossing",
    "crossing_up": "Crossing Up",
    "crossing_down": "Crossing Down",
    "greater": "Greater Than",
    "less": "Less Than",
}

# Způsob spouštění (Trigger) jako v TradingView
TRIGGERS: dict[str, str] = {
    "once": "Only Once",
    "per_bar": "Once Per Bar",
    "per_bar_close": "Once Per Bar Close",
    "per_minute": "Once Per Minute",
}

# Stavy alertu pro seznam alertů (status -> popisek)
STATUSES: dict[str, str] = {
    "active": "Aktivní",
    "paused": "Pozastaveno",
    "triggered": "Spuštěno",
    "expired": "Vypršelo",
    "error": "Chyba",
}

# Kresby, na které lze nastavit alert (čára s cenou v čase)
LINE_KINDS: dict[str, str] = {
    "hline": "Horizontal line",
    "hray": "Horizontal ray",
    "trend": "Trend line",
    "ray": "Ray",
    "extended": "Extended line",
}

# Podmínky alertu na signál indikátoru (id -> popisek) a směr signálu, který splňují
INDICATOR_CONDITIONS: dict[str, str] = {
    "signal": "Signál Long nebo Short",
    "signal_long": "Signál Long",
    "signal_short": "Signál Short",
}
_CONDITION_DIRECTIONS = {"signal": ("long", "short"), "signal_long": ("long",), "signal_short": ("short",)}

# Spouštění alertu na signál indikátoru: X minut před zavřením svíčky (jako
# alert Pine skriptu Svopex TTW) nebo triggery cenových alertů
INDICATOR_TRIGGERS: dict[str, str] = {
    "before_close": "X min před zavřením svíčky",
    "per_bar_close": "Once Per Bar Close",
    "per_bar": "Once Per Bar",
    "once": "Only Once",
}
# Výchozí a nejvyšší předstih alertu před zavřením svíčky (minuty)
DEFAULT_ALERT_MINUTES = 10
MAX_ALERT_MINUTES = 1440
# Nejkratší rozestup přepočtů indikátoru pro alert během běžící svíčky (ms)
INDICATOR_COMPUTE_MS = 15_000

# Výchozí notifikace nového alertu (Telegram + toast v aplikaci)
DEFAULT_NOTIFY: dict = {"telegram": True, "webhook": "", "toast": True, "sound": False}

# Nejdelší povolená zpráva alertu (Telegram povoluje 4096 znaků)
MAX_MESSAGE_LENGTH = 1000
# Výchozí doba platnosti nového alertu (ms) – jako v TradingView zhruba měsíc
DEFAULT_EXPIRATION_MS = 30 * 86_400_000
# Nejkratší rozestup spuštění u triggeru „Once Per Minute“ (ms)
PER_MINUTE_MS = 60_000

# Zástupné texty ve zprávě: {{ticker}}, {{close}} apod. (mezery uvnitř povoleny)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def line_price(kind: str, points: list[dict], time_s: float) -> Optional[float]:
    """Cena čáry kresby v čase (alert na kresbu).

    Horizontální linie a polopřímka mají cenu stálou, šikmé čáry se lineárně
    interpolují podle času (i mimo úsek mezi body – čára se prodlužuje).

    Parametry:
        kind: druh kresby (viz LINE_KINDS).
        points: body kresby [{"time": s, "price": cena}, …].
        time_s: čas v sekundách UTC.

    Vrací cenu, nebo None (neznámá kresba, chybějící body, svislá čára).
    """
    if kind not in LINE_KINDS or not points:
        return None
    try:
        if kind in ("hline", "hray"):
            return float(points[0]["price"])
        if len(points) < 2:
            return None
        t1, p1 = float(points[0]["time"]), float(points[0]["price"])
        t2, p2 = float(points[1]["time"]), float(points[1]["price"])
    except (KeyError, TypeError, ValueError):
        return None
    if t1 == t2:
        return None
    return p1 + (p2 - p1) * (time_s - t1) / (t2 - t1)


def condition_met(
    condition: str,
    previous: Optional[float],
    current: float,
    level: float,
    previous_level: Optional[float] = None,
) -> bool:
    """Splňuje cena podmínku alertu?

    Překřížení se posuzuje podle znaménka rozdílu ceny a úrovně před a po
    změně, takže funguje i pro pohyblivou úroveň (šikmá čára); dotyk úrovně
    zespodu/shora se počítá jako překřížení.

    Parametry:
        condition: id podmínky (viz CONDITIONS).
        previous: předchozí cena (None = první hodnota, překřížení nelze určit).
        current: aktuální cena.
        level: úroveň alertu v čase aktuální ceny.
        previous_level: úroveň v čase předchozí ceny (None = stejná jako level).
    """
    if condition == "greater":
        return current > level
    if condition == "less":
        return current < level
    if previous is None:
        return False
    before = previous - (level if previous_level is None else previous_level)
    after = current - level
    crossed_up = before < 0 <= after
    crossed_down = before > 0 >= after
    if condition == "crossing_up":
        return crossed_up
    if condition == "crossing_down":
        return crossed_down
    if condition == "crossing":
        return crossed_up or crossed_down
    return False


class AlertTracker:
    """Stav vyhodnocení jednoho alertu nad živými svíčkami.

    Pamatuje si předchozí cenu (překřížení), rozpracovaný bar (trigger „Once
    Per Bar Close“) a poslední spuštění (omezení na bar / minutu).

    Parametry:
        condition: id podmínky.
        trigger: id způsobu spouštění.
        step_ms: délka baru timeframu alertu v ms.
    """

    def __init__(self, condition: str, trigger: str, step_ms: int) -> None:
        self.condition = condition
        self.trigger = trigger
        self.step_ms = step_ms
        self.previous_price: Optional[float] = None
        self.previous_level: Optional[float] = None
        # Rozpracovaný bar (open_time, poslední close) a závěr posledního uzavřeného
        self.bar_time: Optional[int] = None
        self.bar_close: Optional[float] = None
        self.closed_close: Optional[float] = None
        self.closed_level: Optional[float] = None
        self.last_fired_bar: Optional[int] = None
        self.last_fired_ms: Optional[int] = None

    def update(self, candle: Candle, now_ms: int, level_at: Callable[[float], Optional[float]]) -> Optional[float]:
        """Zpracuje živou svíčku; vrátí cenu, při které se alert spustil, jinak None.

        Parametry:
            candle: nová nebo aktualizovaná svíčka timeframu alertu.
            now_ms: aktuální čas (UTC ms).
            level_at: funkce čas (s) -> úroveň alertu (None = úroveň nelze určit).
        """
        if self.trigger == "per_bar_close":
            return self._update_bar_close(candle, level_at)
        level = level_at(now_ms / 1000)
        if level is None:
            return None
        met = condition_met(self.condition, self.previous_price, candle.close, level, self.previous_level)
        self.previous_price = candle.close
        self.previous_level = level
        if not met:
            return None
        # Omezení počtu spuštění: jednou za bar / jednou za minutu
        if self.trigger == "per_bar" and self.last_fired_bar == candle.open_time:
            return None
        if self.trigger == "per_minute" and self.last_fired_ms is not None and now_ms - self.last_fired_ms < PER_MINUTE_MS:
            return None
        self.last_fired_bar = candle.open_time
        self.last_fired_ms = now_ms
        return candle.close

    def _update_bar_close(self, candle: Candle, level_at: Callable[[float], Optional[float]]) -> Optional[float]:
        # Vyhodnocuje se jen uzavřený bar: začne-li novější bar, předchozí se
        # uzavřel s naposledy viděným závěrem (úroveň v čase konce baru)
        fired = None
        if self.bar_time is not None and candle.open_time > self.bar_time and self.bar_close is not None:
            level = level_at((self.bar_time + self.step_ms) / 1000)
            if level is not None:
                if condition_met(self.condition, self.closed_close, self.bar_close, level, self.closed_level):
                    fired = self.bar_close
                    self.last_fired_bar = self.bar_time
                self.closed_close = self.bar_close
                self.closed_level = level
        # Opožděnou svíčku dřívějšího baru ignorujeme
        if self.bar_time is None or candle.open_time >= self.bar_time:
            self.bar_time = candle.open_time
            self.bar_close = candle.close
        return fired


class SignalAlertTracker:
    """Stav vyhodnocení alertu na signál indikátoru (např. Svopex TTW).

    Signál na běžící svíčce (bars_ago 0) se může během svíčky objevit
    i zmizet, proto se podle triggeru hlídá:
    - before_close: signál na běžící svíčce nejvýš `minutes` minut před jejím zavřením,
    - per_bar / once: signál na běžící svíčce kdykoli během ní,
    - per_bar_close: signál na právě uzavřené svíčce (bars_ago 1 po začátku nové).
    Každá svíčka spustí alert nejvýš jednou.

    Parametry:
        condition: id podmínky (INDICATOR_CONDITIONS – směr signálu).
        trigger: id spouštění (INDICATOR_TRIGGERS).
        step_ms: délka svíčky timeframu alertu v ms.
        minutes: předstih u before_close.
    """

    def __init__(self, condition: str, trigger: str, step_ms: int, minutes: int = DEFAULT_ALERT_MINUTES) -> None:
        self.directions = _CONDITION_DIRECTIONS.get(condition, ())
        self.trigger = trigger
        self.step_ms = step_ms
        self.minutes = minutes
        self.bar_time: Optional[int] = None
        self.last_fired_bar: Optional[int] = None
        self.last_compute_ms: Optional[int] = None

    def wants_compute(self, candle: Candle, now_ms: int) -> bool:
        """Má se teď indikátor přepočítat? (přepočet je drahý – jen když může alert spustit)

        Volá se s každou živou svíčkou; zároveň si pamatuje běžící svíčku.
        """
        new_bar = self.bar_time is not None and candle.open_time > self.bar_time
        if self.bar_time is None or candle.open_time >= self.bar_time:
            self.bar_time = candle.open_time
        if self.trigger == "per_bar_close":
            # Uzavřená svíčka se vyhodnotí jednou, hned po začátku další
            return new_bar
        if self.last_fired_bar == candle.open_time:
            return False
        if self.trigger == "before_close":
            minutes_left = (candle.open_time + self.step_ms - now_ms) / 60_000
            if not 0 <= minutes_left <= self.minutes:
                return False
        # Během svíčky nejvýš jednou za INDICATOR_COMPUTE_MS (nová svíčka hned)
        if not new_bar and self.last_compute_ms is not None and now_ms - self.last_compute_ms < INDICATOR_COMPUTE_MS:
            return False
        self.last_compute_ms = now_ms
        return True

    def update(self, signals: list, candle: Candle) -> Optional[str]:
        """Vyhodnotí signály přepočteného indikátoru; vrátí popis signálu („LONG ENG“), nebo None.

        Parametry:
            signals: signály indikátoru (Signal s bars_ago, direction, label).
            candle: běžící svíčka, ke které přepočet patří.
        """
        wanted_bars_ago = 1 if self.trigger == "per_bar_close" else 0
        bar = candle.open_time - self.step_ms if wanted_bars_ago else candle.open_time
        if self.last_fired_bar == bar:
            return None
        for signal in signals:
            if signal.bars_ago == wanted_bars_ago and signal.direction in self.directions:
                self.last_fired_bar = bar
                return f"{signal.direction.upper()} {signal.label}".strip()
        return None


def is_expired(alert: dict, now_ms: int) -> bool:
    """Vypršela platnost alertu? (bez expirace = nikdy)"""
    expires_at = alert.get("expires_at")
    return expires_at is not None and now_ms >= expires_at


def format_price(price: float) -> str:
    """Cena do zprávy bez zbytečných nul (1.22547, 515.37, 64903.91)."""
    if not math.isfinite(price):
        return "—"
    text = f"{price:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def target_label(target: dict) -> str:
    """Popisek cíle alertu: cena, druh kresby (jako v TradingView), nebo název indikátoru."""
    if target.get("type") == "drawing":
        return LINE_KINDS.get(target.get("kind"), "Drawing")
    if target.get("type") == "indicator":
        indicator = get_indicator(target.get("kind", ""))
        return indicator.title if indicator else str(target.get("kind"))
    return format_price(float(target.get("price", 0.0)))


def default_message(symbol: str, timeframe: str, condition: str, target: dict) -> str:
    """Výchozí zpráva alertu, např. „AUDNZD, 1m Crossing Horizontal ray“.

    U alertu na signál indikátoru obsahuje zástupný text {{signal}} (směr
    a typ formace se doplní při spuštění): „EURUSD, 1h Svopex TTW {{signal}}“.
    """
    if target.get("type") == "indicator":
        return f"{symbol}, {timeframe} {target_label(target)} {{{{signal}}}}"
    label = target_label(target)
    if target.get("type") == "drawing":
        label = label.lower()
    return f"{symbol}, {timeframe} {CONDITIONS.get(condition, condition)} {label}"


def render_message(template: str, context: dict) -> str:
    """Doplní do zprávy zástupné texty {{ticker}}, {{exchange}}, {{interval}}, {{close}}, {{time}}.

    Neznámé zástupné texty zůstanou beze změny (uživatel vidí, že je napsal špatně).

    Parametry:
        template: zpráva alertu.
        context: hodnoty zástupných textů (klíč bez závorek).
    """
    def replace(match: re.Match) -> str:
        key = match.group(1).lower()
        return str(context[key]) if key in context else match.group(0)

    return _PLACEHOLDER.sub(replace, template)


def message_context(alert: dict, price: float, exchange: str, fired_at_ms: int, signal: str = "") -> dict:
    """Hodnoty zástupných textů zprávy pro spuštěný alert (signal = popis signálu indikátoru)."""
    return {
        "signal": signal,
        "ticker": alert["symbol"],
        "exchange": exchange,
        "interval": alert["timeframe"],
        "close": format_price(price),
        "time": datetime.fromtimestamp(fired_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def webhook_payload(message: str) -> tuple[bytes, str]:
    """Tělo a Content-Type webhooku jako v TradingView.

    Zpráva, která je platným JSON objektem/polem, se pošle jako
    application/json, jinak jako text/plain (UTF-8).
    """
    try:
        parsed = json.loads(message)
    except ValueError:
        parsed = None
    if isinstance(parsed, (dict, list)):
        return message.encode("utf-8"), "application/json; charset=utf-8"
    return message.encode("utf-8"), "text/plain; charset=utf-8"


def normalize_notify(values) -> dict:
    """Zkontroluje nastavení notifikací; neplatná webhook adresa vyhodí ValueError.

    Parametry:
        values: {"telegram", "webhook", "toast", "sound"} – může být neúplný.
    """
    source = values if isinstance(values, dict) else {}
    webhook = str(source.get("webhook") or "").strip()
    # Webhook jen přes HTTP(S) a bez mezer (TradingView povoluje i jen HTTP)
    if webhook and not re.match(r"^https?://[^\s/]+\S*$", webhook):
        raise ValueError("Webhook URL musí začínat http:// nebo https://.")
    return {
        "telegram": bool(source.get("telegram", DEFAULT_NOTIFY["telegram"])),
        "webhook": webhook,
        "toast": bool(source.get("toast", DEFAULT_NOTIFY["toast"])),
        "sound": bool(source.get("sound", DEFAULT_NOTIFY["sound"])),
    }


def normalize_target(values) -> dict:
    """Zkontroluje cíl alertu (cena, kresba nebo signál indikátoru); chyba vyhodí ValueError."""
    source = values if isinstance(values, dict) else {}
    if source.get("type") == "indicator":
        kind = source.get("kind")
        indicator = get_indicator(kind) if isinstance(kind, str) else None
        if indicator is None or not indicator.has_signals:
            raise ValueError("Alert lze nastavit jen na indikátor se signály (např. Svopex TTW).")
        minutes = source.get("minutes", DEFAULT_ALERT_MINUTES)
        if isinstance(minutes, bool):
            minutes = None
        return {
            "type": "indicator",
            "kind": kind,
            "params": instance_params(kind, source.get("params")),
            "minutes": Param.int("minutes", DEFAULT_ALERT_MINUTES, "Minut před zavřením svíčky", min=1,
                                 max=MAX_ALERT_MINUTES).normalize(minutes),
        }
    if source.get("type") == "drawing":
        kind = source.get("kind")
        drawing_id = source.get("id")
        if kind not in LINE_KINDS or not isinstance(drawing_id, int) or isinstance(drawing_id, bool):
            raise ValueError("Alert lze nastavit jen na čáru (horizontální, trendovou, polopřímku).")
        return {"type": "drawing", "id": drawing_id, "kind": kind}
    try:
        price = float(source.get("price"))
    except (TypeError, ValueError):
        raise ValueError("Zadejte cenu alertu.") from None
    if not math.isfinite(price):
        raise ValueError("Zadejte cenu alertu.")
    return {"type": "value", "price": price}


def normalize_alert(data: dict) -> dict:
    """Zkontroluje a doplní definici alertu z dialogu; chyba vyhodí ValueError s popisem.

    Vrací slovník jen s definicí (source, symbol, timeframe, condition,
    target, trigger, expires_at, message, notify) – stav (active, status,
    počty spuštění) řeší úložiště.
    """
    if not isinstance(data, dict):
        raise ValueError("Neplatný alert.")
    source = str(data.get("source") or "").strip()
    symbol = str(data.get("symbol") or "").strip()
    timeframe = data.get("timeframe")
    if not source or not symbol:
        raise ValueError("Alert musí mít symbol.")
    if timeframe not in TIMEFRAME_MS:
        raise ValueError("Neplatný timeframe alertu.")
    target = normalize_target(data.get("target"))
    # Alert na signál indikátoru má vlastní podmínky a spouštění
    on_indicator = target["type"] == "indicator"
    condition = data.get("condition")
    if condition not in (INDICATOR_CONDITIONS if on_indicator else CONDITIONS):
        raise ValueError("Neplatná podmínka alertu.")
    trigger = data.get("trigger")
    if trigger not in (INDICATOR_TRIGGERS if on_indicator else TRIGGERS):
        raise ValueError("Neplatný způsob spouštění alertu.")
    expires_at = data.get("expires_at")
    if expires_at is not None:
        if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
            raise ValueError("Neplatná expirace alertu.")
        expires_at = int(expires_at)
    # Prázdná zpráva = výchozí text podle podmínky a cíle
    message = str(data.get("message") or "").strip()
    if not message:
        message = default_message(symbol, timeframe, condition, target)
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"Zpráva alertu může mít nejvýš {MAX_MESSAGE_LENGTH} znaků.")
    return {
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "condition": condition,
        "target": target,
        "trigger": trigger,
        "expires_at": expires_at,
        "message": message,
        "notify": normalize_notify(data.get("notify")),
    }


def describe_alert(alert: dict) -> str:
    """Krátký popis alertu do seznamu: „Crossing 1.22547 · Once Per Bar“ nebo „Svopex TTW: Signál Long · 10 min před zavřením“."""
    target = alert.get("target") or {}
    if target.get("type") == "indicator":
        condition = INDICATOR_CONDITIONS.get(alert.get("condition"), "?")
        trigger = INDICATOR_TRIGGERS.get(alert.get("trigger"), "?")
        if alert.get("trigger") == "before_close":
            trigger = f"{target.get('minutes')} min před zavřením svíčky"
        return f"{target_label(target)}: {condition} · {trigger}"
    condition = CONDITIONS.get(alert.get("condition"), "?")
    return f"{condition} {target_label(target)} · {TRIGGERS.get(alert.get('trigger'), '?')}"


# Řazení seznamu alertů v pravém panelu (id -> popisek v nabídce řazení)
ALERT_SORTS: dict[str, str] = {
    "newest": "Nejnovější první",
    "oldest": "Nejstarší první",
    "symbol": "Podle symbolu",
    "status": "Podle stavu",
}

# Pořadí stavů při řazení podle stavu (aktivní nahoře jako v TradingView)
_STATUS_ORDER = {status: index for index, status in enumerate(("active", "triggered", "error", "paused", "expired"))}


def filter_alerts(alerts: list[dict], query: str = "", sort: str = "newest") -> list[dict]:
    """Alerty pro seznam v panelu: hledání podle textu a zvolené řazení.

    Parametry:
        alerts: alerty z databáze (libovolné pořadí).
        query: hledaný text (symbol, timeframe, zpráva nebo popis podmínky;
            bez ohledu na velikost písmen, prázdný = všechny).
        sort: klíč z ALERT_SORTS (neznámý = "newest").
    """
    needle = (query or "").strip().lower()
    # Hledá se ve všech textech, které řádek alertu zobrazuje
    if needle:
        alerts = [
            alert
            for alert in alerts
            if needle in " ".join(
                (str(alert.get("symbol", "")), str(alert.get("timeframe", "")),
                 str(alert.get("message", "")), describe_alert(alert))
            ).lower()
        ]
    # Řazení; sekundárně vždy nejnovější první (stabilní pořadí stejných klíčů)
    by_newest = sorted(alerts, key=lambda alert: alert.get("id") or 0, reverse=True)
    if sort == "oldest":
        return by_newest[::-1]
    if sort == "symbol":
        return sorted(by_newest, key=lambda alert: (str(alert.get("symbol", "")), str(alert.get("timeframe", ""))))
    if sort == "status":
        return sorted(by_newest, key=lambda alert: _STATUS_ORDER.get(alert.get("status"), len(_STATUS_ORDER)))
    return by_newest
