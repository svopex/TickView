"""Logika scanneru: nastavení, stav symbolu ze signálů, texty a alert před zavřením svíčky.

Scanner pro každý symbol zvoleného seznamu spočítá indikátor se signály
(např. Svopex TTW) na zvoleném timeframu a zobrazí, na které z posledních
svíček je long / short signál. Alert pošle X minut před zavřením svíčky
jednu souhrnnou zprávu se seznamem trhů, které mají signál na právě běžící
svíčce (index 0). Modul obsahuje jen čisté funkce a stav alertu bez I/O;
stahování dat a odesílání řeší tickview/services/scanner.py.
"""

import json
from typing import Optional

from tickview.indicators.api import Param
from tickview.indicators.compute import instance_params
from tickview.indicators.registry import all_indicators, get_indicator
from tickview.logic.alerts import DEFAULT_NOTIFY, MAX_ALERT_MINUTES, normalize_notify
from tickview.logic.styles import plural
from tickview.providers.base import TIMEFRAME_MS

# Klíč nastavení se scannerem (JSON)
SCANNER_SETTINGS_KEY = "scanner"
# Kolik posledních svíček nejvýš sledovat
MAX_SCAN_BARS = 20
# Přehled (tabulka scanneru) v grafech s indikátorem scanneru – pozice a velikost písma jako v Pine
OVERVIEW_POSITIONS = {
    "top_right": "Vpravo nahoře",
    "top_left": "Vlevo nahoře",
    "middle_right": "Vpravo uprostřed",
    "bottom_right": "Vpravo dole",
    "bottom_left": "Vlevo dole",
    "off": "Nezobrazovat",
}
OVERVIEW_SIZES = {"tiny": "Drobné", "small": "Malé", "normal": "Normální", "large": "Velké"}

DEFAULT_SCANNER: dict = {
    "enabled": False,
    "indicator": "svopex_ttw",
    "params": {},
    "watchlist_id": None,
    "timeframe": "1h",
    "bars": 2,
    "only_hits": True,
    "overview": {"position": "top_right", "size": "normal"},
    "alert": {"enabled": False, "minutes1": 10, "minutes2": 5, "notify": dict(DEFAULT_NOTIFY)},
}


def signal_indicators() -> dict[str, str]:
    """Indikátory, které vrací signály (volby scanneru): id -> název."""
    return {kind: indicator.name for kind, indicator in all_indicators().items() if indicator.has_signals}


def _int_in(value, default: int, low: int, high: int) -> int:
    """Celé číslo v rozsahu; jinak (i chybějící) výchozí hodnota."""
    if isinstance(value, bool):
        return default
    try:
        return Param.int("value", default, min=low, max=high).normalize(value)
    except ValueError:
        return default


def scanner_title(config: dict) -> str:
    """Zkratka indikátoru scanneru do textů (nenačtený indikátor = jeho id)."""
    indicator = get_indicator(config["indicator"])
    return indicator.title if indicator else config["indicator"]


def normalize_scanner(data) -> dict:
    """Zkontroluje nastavení scanneru; neplatné části nahradí výchozími.

    Parametry indikátoru projdou jako u instance v grafu (instance_params).
    """
    source = data if isinstance(data, dict) else {}
    kind = source.get("indicator") if isinstance(source.get("indicator"), str) else DEFAULT_SCANNER["indicator"]
    params = instance_params(kind, source.get("params"))
    watchlist_id = source.get("watchlist_id")
    if isinstance(watchlist_id, bool) or not isinstance(watchlist_id, int):
        watchlist_id = None
    timeframe = source.get("timeframe") if source.get("timeframe") in TIMEFRAME_MS else DEFAULT_SCANNER["timeframe"]
    overview = source.get("overview") if isinstance(source.get("overview"), dict) else {}
    default_overview = DEFAULT_SCANNER["overview"]
    alert = source.get("alert") if isinstance(source.get("alert"), dict) else {}
    default_alert = DEFAULT_SCANNER["alert"]
    try:
        notify = normalize_notify(alert.get("notify"))
    except ValueError:
        notify = dict(DEFAULT_NOTIFY)
    return {
        "enabled": bool(source.get("enabled", False)),
        "indicator": kind,
        "params": params,
        "watchlist_id": watchlist_id,
        "timeframe": timeframe,
        "bars": _int_in(source.get("bars"), DEFAULT_SCANNER["bars"], 1, MAX_SCAN_BARS),
        "only_hits": bool(source.get("only_hits", True)),
        "overview": {
            "position": overview["position"] if overview.get("position") in OVERVIEW_POSITIONS
            else default_overview["position"],
            "size": overview["size"] if overview.get("size") in OVERVIEW_SIZES else default_overview["size"],
        },
        "alert": {
            "enabled": bool(alert.get("enabled", False)),
            "minutes1": _int_in(alert.get("minutes1"), default_alert["minutes1"], 0, MAX_ALERT_MINUTES),
            "minutes2": _int_in(alert.get("minutes2"), default_alert["minutes2"], 0, MAX_ALERT_MINUTES),
            "notify": notify,
        },
    }


def load_scanner(raw: Optional[str]) -> dict:
    """Načte nastavení scanneru z JSON (poškozené = výchozí)."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    return normalize_scanner(data)


def symbol_row(source: str, symbol: str, signals: list, bars: int, last_open_ms: Optional[int]) -> dict:
    """Řádek scanneru pro jeden symbol: nejčerstvější long a short signál posledních `bars` svíček.

    Parametry:
        source, symbol: symbol zdroje.
        signals: signály indikátoru (Signal s bars_ago, direction, label).
        bars: kolik posledních svíček sledovat.
        last_open_ms: otevření poslední svíčky (ms UTC), None = bez dat.

    Vrací {"key", "source", "symbol", "long", "short", "long_label",
    "short_label", "last_open", "error"}; long/short = index svíčky (0 =
    běžící) nebo None.
    """
    row = {
        "key": f"{source}:{symbol}", "source": source, "symbol": symbol,
        "long": None, "short": None, "long_label": "", "short_label": "",
        "last_open": last_open_ms, "error": "",
    }
    for signal in sorted(signals, key=lambda item: item.bars_ago):
        if signal.bars_ago >= bars or signal.direction not in ("long", "short"):
            continue
        # Hledá se od nejnovější svíčky – uloží se nejčerstvější signál směru
        if row[signal.direction] is None:
            row[signal.direction] = signal.bars_ago
            row[f"{signal.direction}_label"] = signal.label
    return row


def error_row(source: str, symbol: str, message: str) -> dict:
    """Řádek scanneru pro symbol, který se nepodařilo spočítat."""
    row = symbol_row(source, symbol, [], 0, None)
    row["error"] = message
    return row


def has_signal(row: dict) -> bool:
    return row["long"] is not None or row["short"] is not None


def status_text(row: dict) -> str:
    """Popis signálu s indexem svíčky, např. "LONG(0)" nebo "LONG(1) + SHORT(0)"."""
    parts = []
    if row["long"] is not None:
        parts.append(f"LONG({row['long']})")
    if row["short"] is not None:
        parts.append(f"SHORT({row['short']})")
    return " + ".join(parts)


def status_kind(row: dict) -> str:
    """Druh stavu pro barvu řádku: "both", "long", "short", "error" nebo "none"."""
    if row.get("error"):
        return "error"
    if row["long"] is not None and row["short"] is not None:
        return "both"
    if row["long"] is not None:
        return "long"
    return "short" if row["short"] is not None else "none"


def visible_rows(rows: list[dict], only_hits: bool) -> list[dict]:
    """Řádky tabulky: při „jen se signálem“ jen trhy se signálem (chyby se ukazují vždy)."""
    return [row for row in rows if not only_hits or has_signal(row) or row.get("error")]


def overview_payload(config: dict, results: dict) -> Optional[dict]:
    """Data přehledu (tabulky scanneru) pro graf; None = přehled se nezobrazuje.

    Tabulka jako v Pine skriptu: hlavička „Svopex TTW (1h) | Signál (svíčka)“,
    řádky symbol + stav (barva podle směru); místo řádků poznámka, když
    scanner neběží, nemá seznam nebo žádný trh nemá signál.
    """
    overview = config["overview"]
    if overview["position"] == "off":
        return None
    note = ""
    rows: list[dict] = []
    if not config["enabled"]:
        note = "scanner je vypnutý (záložka Scanner)"
    elif results["error"]:
        note = results["error"]
    elif not results["scanned_at"]:
        note = "počítám…"
    else:
        rows = [
            {"symbol": row["symbol"], "text": "chyba" if row.get("error") else status_text(row) or "—",
             "kind": status_kind(row)}
            for row in visible_rows(results["rows"], config["only_hits"])
        ]
        if not rows:
            note = "žádný signál"
    return {
        "title": f"{scanner_title(config)} ({config['timeframe']})",
        "header": "Signál (svíčka)",
        "rows": rows,
        "note": note,
        "position": overview["position"],
        "size": overview["size"],
    }


def current_bar_close(rows: list[dict], step_ms: int, now_ms: int) -> int:
    """Čas zavření právě běžící svíčky timeframu scanneru (ms UTC).

    Bere se nejnovější svíčka ze skenovaných symbolů (její otevření + délka);
    bez dat se použije svíčka zarovnaná na délku timeframu od epochy.
    """
    opens = [row["last_open"] for row in rows if row.get("last_open") is not None]
    if opens:
        return max(opens) + step_ms
    return now_ms - now_ms % step_ms + step_ms


def bar_hits(rows: list[dict]) -> list[tuple[str, str]]:
    """Trhy se signálem na běžící svíčce (index 0): [(symbol, "LONG" / "SHORT" / "LONG+SHORT")]."""
    hits = []
    for row in rows:
        long0, short0 = row["long"] == 0, row["short"] == 0
        if long0 or short0:
            hits.append((row["symbol"], "LONG+SHORT" if long0 and short0 else "LONG" if long0 else "SHORT"))
    return hits


def alert_message(title: str, timeframe: str, minutes: int, hits: list[tuple[str, str]]) -> str:
    """Souhrnná zpráva alertu, např. „Svopex TTW 1h | 10 min do zavření svíčky | 2 trhy: EURUSD LONG, …“.

    V textu je nastavený práh, ne dopočítaný zbytek – alert odejde až pod
    prahem a dopočet by ukazoval o minutu méně (stejně jako v Pine v7).
    """
    listing = ", ".join(f"{symbol} {direction}" for symbol, direction in hits)
    markets = plural(len(hits), "trh", "trhy", "trhů")
    return f"{title} {timeframe} | {minutes} min do zavření svíčky | {markets}: {listing}"


class CloseAlertWindow:
    """Stav alertu před zavřením svíčky: každý ze dvou prahů nejvýš jednou za svíčku.

    Odpovídá varip proměnným v Pine (alertPosledniBar, alertOdeslan1/2):
    při přechodu na novou svíčku se oba prahy povolí znovu.
    """

    def __init__(self) -> None:
        self.bar_close: Optional[int] = None
        self.sent1 = False
        self.sent2 = False

    def check(self, bar_close: int, minutes_left: float, hit_count: int, minutes1: int, minutes2: int,
              commit: bool = True) -> Optional[int]:
        """Má se teď poslat alert? Vrací práh (minuty) do textu zprávy, nebo None.

        Oba prahy jsou nezávislé; když se na jedné kontrole překročí oba,
        odejde jen jedna zpráva (s prahem 1, jako v Pine).

        Parametry:
            bar_close: zavření běžící svíčky (ms UTC).
            minutes_left: zbývající minuty do zavření.
            hit_count: počet trhů se signálem na běžící svíčce.
            minutes1, minutes2: prahy v minutách (0 = vypnuto).
            commit: False = jen zjistit, nic si nepamatovat.
        """
        if bar_close != self.bar_close:
            # Nová svíčka – oba prahy se povolí znovu
            self.bar_close = bar_close
            self.sent1 = self.sent2 = False
        if hit_count <= 0 or minutes_left < 0:
            return None
        send1 = minutes1 > 0 and not self.sent1 and minutes_left <= minutes1
        send2 = minutes2 > 0 and not self.sent2 and minutes_left <= minutes2
        if not (send1 or send2):
            return None
        if commit:
            self.sent1 = self.sent1 or send1
            self.sent2 = self.sent2 or send2
        return minutes1 if send1 else minutes2
