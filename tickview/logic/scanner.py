"""Logika scanneru: úlohy ze vstupů indikátoru, stav symbolu ze signálů, texty a alert.

Scanner se nastavuje přímo ve vstupech
indikátoru (skupiny Scanner – viz scanner_params v tickview/indicators/api.py).
Každá instance takového indikátoru v rozložení grafů je jedna úloha: pro
každý vyplněný symbol spočítá indikátor se stejnými parametry na timeframu
scanu a zjistí, na které z posledních svíček je long / short signál. Výsledek
se ukáže jako tabulka v grafu s indikátorem; alert pošle X minut před
zavřením svíčky jednu souhrnnou zprávu se seznamem trhů, které mají signál na
právě běžící svíčce (index 0). Modul obsahuje jen čisté funkce a stav alertu
bez I/O; stahování dat a odesílání řeší tickview/services/scanner.py.
"""

import json
from typing import Optional

from tickview.i18n import N_, plural, tr
from tickview.indicators.api import SCANNER_SYMBOLS, split_symbol
from tickview.indicators.registry import get_indicator
from tickview.logic.styles import normalize_indicator_style, timeframe_visible


def parse_symbol(text: str, default_source: str) -> Optional[tuple[str, str]]:
    """Symbol ze vstupu scanneru jako (zdroj, symbol); prázdný vstup = None.

    Parametry:
        text: "zdroj:SYMBOL" nebo jen "SYMBOL".
        default_source: zdroj grafu pro symbol bez prefixu.
    """
    text = (text or "").strip()
    if not text:
        return None
    source, symbol = split_symbol(text)
    return source or default_source, symbol


def scan_config(params: dict, default_source: str) -> dict:
    """Nastavení scanneru ze (normalizovaných) parametrů instance indikátoru.

    Vrací {"timeframe", "bars", "position", "size", "only_hits",
    "strip_prefix", "symbols": [(zdroj, symbol)], "alert": {"enabled",
    "minutes1", "minutes2"}}; opakovaný symbol se počítá jednou. Notifikace
    alertu se nastavují v alertu s podmínkou alert_call (logic.alerts).
    """
    symbols: list[tuple[str, str]] = []
    for index in range(1, SCANNER_SYMBOLS + 1):
        parsed = parse_symbol(params.get(f"scan_symbol_{index}", ""), default_source)
        if parsed and parsed not in symbols:
            symbols.append(parsed)
    return {
        "timeframe": params["scan_timeframe"],
        "bars": params["scan_bars"],
        "position": params["scan_table_position"],
        "size": params["scan_text_size"],
        "only_hits": params["scan_only_hits"],
        "strip_prefix": params["scan_strip_prefix"],
        "symbols": symbols,
        "alert": {
            "enabled": params["scan_alert"],
            "minutes1": params["scan_alert_minutes1"],
            "minutes2": params["scan_alert_minutes2"],
        },
    }


def make_job(spec: dict, default_source: str, timeframe: str = "") -> tuple[Optional[dict], bool]:
    """Úloha scanneru pro instanci indikátoru v grafu a zda instance ukazuje tabulku.

    Instance skenuje, když indikátor má vstupy scanneru, aspoň jeden symbol
    a buď viditelnou tabulku (viditelný indikátor i na timeframu grafu,
    zapnuté Tables v záložce Style, pozice ≠ nezobrazovat), nebo zapnutý alert. Klíč úlohy je stejný pro shodné instance ve více
    grafech, takže se počítají jednou.

    Parametry:
        spec: instance {"kind", "params", "visible"} s normalizovanými parametry.
        default_source: zdroj grafu (pro symboly bez prefixu).
        timeframe: timeframe grafu (záložka Visibility; prázdný = viditelný).

    Vrací (úloha {"key", "kind", "title", "params", "config"} nebo None, tabulka).
    """
    indicator = get_indicator(spec["kind"])
    if indicator is None or not indicator.has_scanner:
        return None, False
    params = spec["params"]
    config = scan_config(params, default_source)
    table = (bool(config["symbols"]) and spec.get("visible", True) and config["position"] != "off"
             and normalize_indicator_style(spec.get("style"))["tables"]
             and timeframe_visible(spec.get("visibility"), timeframe))
    if not (table or (config["symbols"] and config["alert"]["enabled"])):
        return None, False
    # Klíč: indikátor, parametry výpočtu (bez vstupů scanneru) a nastavení scanneru
    scanner_names = {param.name for param in indicator.params if param.scanner}
    compute_params = {name: value for name, value in params.items() if name not in scanner_names}
    key = json.dumps([spec["kind"], compute_params, config], sort_keys=True)
    return {"key": key, "kind": spec["kind"], "title": indicator.title, "params": params, "config": config}, table


def layout_jobs(layout: dict) -> tuple[dict[str, dict], list[Optional[str]]]:
    """Úlohy scanneru ze všech grafů rozložení (logic.layouts.normalize_layout).

    Vrací (klíč -> úloha, klíč úlohy tabulky pro každý graf v pořadí rozložení
    nebo None); graf ukazuje tabulku první instance, která ji má zobrazit
    (jako table v Pine – víc tabulek by se v jednom rohu překrývalo).
    """
    jobs: dict[str, dict] = {}
    tables: list[Optional[str]] = []
    for pane in layout["panes"]:
        default_source = split_symbol(pane["symbol"])[0]
        table_key = None
        for spec in pane["indicators"]:
            job, table = make_job(spec, default_source, pane.get("timeframe", ""))
            if job:
                jobs[job["key"]] = job
                if table and table_key is None:
                    table_key = job["key"]
        tables.append(table_key)
    return jobs, tables


def symbol_row(source: str, symbol: str, signals: list, bars: int, last_open_ms: Optional[int]) -> dict:
    """Řádek scanneru pro jeden symbol: nejčerstvější long a short signál posledních `bars` svíček.

    Parametry:
        source, symbol: symbol zdroje.
        signals: signály indikátoru (Signal s bars_ago, direction, label).
        bars: kolik posledních svíček sledovat.
        last_open_ms: otevření poslední svíčky (ms UTC), None = bez dat.

    Vrací {"key", "symbol", "long", "short", "last_open", "error"};
    long/short = index svíčky (0 = běžící) nebo None.
    """
    row = {
        "key": f"{source}:{symbol}", "symbol": symbol, "long": None, "short": None,
        "last_open": last_open_ms, "error": "",
    }
    for signal in sorted(signals, key=lambda item: item.bars_ago):
        if signal.bars_ago >= bars or signal.direction not in ("long", "short"):
            continue
        # Hledá se od nejnovější svíčky – uloží se nejčerstvější signál směru
        if row[signal.direction] is None:
            row[signal.direction] = signal.bars_ago
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
    """Řádky tabulky: při „jen se signálem“ jen trhy se signálem (bez chybných symbolů)."""
    return [row for row in rows if not only_hits or (has_signal(row) and not row.get("error"))]


def row_label(row: dict, strip_prefix: bool) -> str:
    """Popisek symbolu v tabulce a alertu: "SYMBOL", nebo "zdroj:SYMBOL" bez skrytí prefixu."""
    return row["symbol"] if strip_prefix else row["key"]


def overview_payload(job: dict, results: Optional[dict], progress: Optional[tuple[int, int]] = None) -> Optional[dict]:
    """Data tabulky scanneru pro graf; None = tabulka se nezobrazuje.

    Tabulka jako table v Pine: hlavička „<název indikátoru> (1h) | Signál (svíčka)“,
    řádky symbol + stav (barva podle směru); místo řádků poznámka, když
    výsledek ještě není nebo žádný trh nemá signál.

    Parametry:
        job: úloha scanneru (make_job).
        results: poslední výsledek úlohy {"rows", "scanned_at"} (None = ještě nepočítáno).
        progress: průběh běžícího přepočtu (hotové symboly, všechny) do poznámky „počítám…“.
    """
    config = job["config"]
    if config["position"] == "off":
        return None
    note = ""
    rows: list[dict] = []
    if not results or not results["scanned_at"]:
        note = tr("počítám… {done}/{total}").format(done=progress[0], total=progress[1]) if progress else tr("počítám…")
    else:
        rows = [
            {"symbol": row_label(row, config["strip_prefix"]),
             "text": tr("chyba") if row.get("error") else status_text(row) or "—",
             "kind": status_kind(row)}
            for row in visible_rows(results["rows"], config["only_hits"])
        ]
        # Při „jen se signálem“ se chybné symboly (např. neběžící TWS) sloučí do
        # jednoho řádku, aby nezahltily tabulku
        errors = sum(1 for row in results["rows"] if row.get("error"))
        if config["only_hits"] and errors:
            symbols = plural(errors, N_("symbol"), N_("symboly"), N_("symbolů"))
            rows.append({"symbol": tr("{symbols} bez dat").format(symbols=symbols), "text": tr("chyba"), "kind": "error"})
        if not rows:
            note = tr("žádný signál")
    return {
        "title": f"{tr(job['title'])} ({config['timeframe']})",
        "header": tr("Signál (svíčka)"),
        "rows": rows,
        "note": note,
        "position": config["position"],
        "size": config["size"],
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


def bar_hits(rows: list[dict], strip_prefix: bool = True) -> list[tuple[str, str]]:
    """Trhy se signálem na běžící svíčce (index 0): [(symbol, "LONG" / "SHORT" / "LONG+SHORT")]."""
    hits = []
    for row in rows:
        long0, short0 = row["long"] == 0, row["short"] == 0
        if long0 or short0:
            hits.append((row_label(row, strip_prefix), "LONG+SHORT" if long0 and short0 else "LONG" if long0 else "SHORT"))
    return hits


def alert_message(title: str, timeframe: str, minutes: int, hits: list[tuple[str, str]]) -> str:
    """Souhrnná zpráva alertu, např. „<název indikátoru> 1h | 10 min do zavření svíčky | 2 trhy: EURUSD LONG, …“.

    V textu je nastavený práh, ne dopočítaný zbytek – alert odejde až pod
    prahem a dopočet by ukazoval o minutu méně.
    """
    listing = ", ".join(f"{symbol} {direction}" for symbol, direction in hits)
    markets = plural(len(hits), N_("trh"), N_("trhy"), N_("trhů"))
    return tr("{title} {timeframe} | {minutes} min do zavření svíčky | {markets}: {listing}").format(
        title=tr(title), timeframe=timeframe, minutes=minutes, markets=markets, listing=listing,
    )


class CloseAlertWindow:
    """Stav alertu před zavřením svíčky: každý ze dvou prahů nejvýš jednou za svíčku.

    Při přechodu na novou svíčku se oba prahy povolí znovu (jako varip proměnné v Pine).
    """

    def __init__(self) -> None:
        self.bar_close: Optional[int] = None
        self.sent1 = False
        self.sent2 = False

    def check(self, bar_close: int, minutes_left: float, hit_count: int, minutes1: int, minutes2: int,
              commit: bool = True) -> Optional[int]:
        """Má se teď poslat alert? Vrací práh (minuty) do textu zprávy, nebo None.

        Oba prahy jsou nezávislé; když se na jedné kontrole překročí oba,
        odejde jen jedna zpráva (s prahem 1).

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
