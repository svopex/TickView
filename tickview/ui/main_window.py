"""Hlavní stránka TickView: vyhledání symbolu, timeframy, graf, indikátory, kreslení, živý stream."""

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
from nicegui import background_tasks, context, ui

from tickview.config import load_config, update_config_section
from tickview.logic.alerts import (
    ALERT_SORTS,
    CONDITIONS,
    DEFAULT_EXPIRATION_MS,
    DEFAULT_ALERT_MINUTES,
    DEFAULT_NOTIFY,
    MAX_ALERT_MINUTES,
    LINE_KINDS,
    STATUSES,
    TRIGGERS,
    default_message,
    describe_alert,
    indicator_conditions,
    indicator_triggers,
    target_label,
    filter_alerts,
    format_price,
    normalize_alert,
)
from tickview.logic.cache import KlineCache
from tickview.logic.indicator_templates import (
    INDICATOR_TEMPLATES_KEY,
    delete_indicator_template,
    describe_indicator_template,
    favorite_indicator_templates,
    find_indicator_template,
    load_indicator_templates,
    recent_indicator_templates,
    save_indicator_template,
    template_indicators,
    update_indicator_template,
)
from tickview.indicators.api import CATEGORIES, Param, split_symbol
from tickview.indicators.compute import (
    indicator_color,
    indicator_title,
    instance_options,
    load_saved_indicators,
    move_indicator,
    new_indicator,
    normalize_params,
    plot_defaults,
    sample_lines,
)
from tickview.indicators.registry import all_indicators, get_indicator, load_errors
from tickview.logic.layouts import (
    LAYOUT_SECTIONS,
    LAYOUT_SETTINGS_KEY,
    LAYOUTS,
    SYNC_OPTIONS,
    load_layout,
    resize_panes,
)
from tickview.logic.watchlists import WATCHLIST_SORTS, sort_watchlist
from tickview.logic.styles import (
    CHART_DEFAULTS,
    CHART_TYPE_SECTIONS,
    CHART_TYPES,
    DEFAULT_DRAWING_COLOR,
    DEFAULT_FAVORITE_CHART_TYPES,
    DEFAULT_FONT_SIZE,
    DEFAULT_LINE_WIDTH,
    DEFAULT_TEXT_BACKGROUND,
    DEFAULT_TEXT_BORDER,
    DRAWING_LINE_STYLES,
    DRAWING_LINE_WIDTHS,
    MAX_NAME_LENGTH,
    POSITION_DEFAULTS,
    POSITION_KINDS,
    POSITION_RISK_UNITS,
    TEXT_FONT_SIZES,
    TIMEFRAME_SECTIONS,
    TIMEFRAMES,
    clean_name,
    default_template_key,
    load_chart_style,
    load_favorites,
    load_panel_position,
    normalize_chart_style,
    normalize_chart_type,
    normalize_color,
    normalize_drawing_style,
    normalize_favorites,
    normalize_panel_position,
    normalize_position_points,
    normalize_indicator_style,
    normalize_text_options,
    plural,
)
from tickview.logic.replay import DEFAULT_REWIND_MODE, REWIND_MODES
from tickview.logic.sessions import (
    SESSION_ETH,
    SESSION_RTH,
    timezone_options,
)
from tickview.providers import PROVIDERS, get_provider, provider_symbol
from tickview.providers.alpaca import DEFAULT_ENDPOINT as ALPACA_DEFAULT_ENDPOINT
from tickview.providers.base import SymbolInfo, utc_now_ms
from tickview.providers.ibkr import DEFAULT_HOST as IBKR_DEFAULT_HOST
from tickview.providers.ibkr import IbkrProvider, connection_settings
from tickview.services.alerts import AlertEngine
from tickview.services.reminders import ReminderService
from tickview.services.scanner import ScannerService
from tickview.services.notify import find_telegram_chat_id, send_telegram, telegram_configured, telegram_settings
from tickview.ui.about_dialog import build_about_dialog
from tickview.ui.chart_pane import ChartPane
from tickview.ui.color_button import ColorButton
from tickview.ui.ibkr_data_dialog import build_ibkr_data_dialog
from tickview.ui.icons import ICONS, svg
from tickview.ui.param_form import ParamForm
from tickview.ui.plot_style_fields import PlotStyleFields
from tickview.ui.reminders_dialog import RemindersDialog
from tickview.ui.replay_panel import ReplayUI
from tickview.ui.templates import TemplateMenu, load_default_template, settings_buttons, settings_header
from tickview.ui.visibility_fields import VisibilityFields

# Hledání symbolu: kolik výsledků jednoho zdroje a kolik celkem se zobrazí
SEARCH_PER_PROVIDER = 15
SEARCH_LIMIT = 60
# Kratší animace vyskakovacích nabídek a dialogů (Quasar má výchozí 300 ms)
ui.menu.default_props("transition-duration=90")
ui.select.default_props("transition-duration=90")
ui.dialog.default_props("transition-duration=120")

# Složka statických souborů UI (styly, JS grafu, vendored knihovna)
STATIC_DIR = Path(__file__).resolve().parent / "static"


# Skripty UI v pořadí načítání (vendored knihovna grafu před komponentou grafu)
STATIC_SCRIPTS = (
    "lightweight-charts.standalone.production.js",
    "color_picker.js",
    "dialog_tabs.js",
    "resizers.js",
    "lightweight_chart.js",
)


def _static_version() -> str:
    """Verze statických souborů pro cache-busting: otisk obsahu CSS a JS.

    Změna kteréhokoli souboru (po restartu serveru) změní URL, takže prohlížeč
    nepoužije starou verzi z cache – verzi není potřeba ručně zvyšovat.
    """
    digest = hashlib.sha1()
    for name in ("style.css", *STATIC_SCRIPTS):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:10]


# Verze se počítá jednou při startu (ne při každém načtení stránky)
STATIC_VERSION = _static_version()


def icon_html(name: str) -> ui.html:
    """SVG piktogram z icons.py jako prvek UI (statický obsah – bez sanitizace)."""
    return ui.html(svg(name), sanitize=False).classes("tv-icon-wrap")


def drawing_scope(kind: str) -> str:
    """Rozsah šablon Settings kresby daného typu (šablony i výchozí styl nových kreseb)."""
    return f"drawing.{kind}"


# Krátké kódy zdrojů (watchlist, hledání, alerty – aby sloupec nebyl široký)
PROVIDER_CODES = {"binance": "BIN", "yahoo": "YAH", "alpaca": "ALP", "ibkr": "IBK"}


def provider_code(source: str) -> str:
    """Krátký kód zdroje dat; neznámý zdroj = první tři písmena velkými."""
    return PROVIDER_CODES.get(source, source[:3].upper())


# Obsahy pravého panelu ("" = panel skrytý, zůstane jen pravá lišta)
RIGHT_PANELS = ("watchlist", "alerts", "replay", "")
# Záhlaví sloupce Symbol ve watchlistu: další stav řazení po kliknutí a ikona stavu
WATCHLIST_SORT_NEXT = {"": "asc", "asc": "desc", "desc": ""}
WATCHLIST_SORT_ICONS = {"": "unfold_more", "asc": "arrow_upward", "desc": "arrow_downward"}


def register(cache: KlineCache, alert_engine: AlertEngine, scanner: ScannerService, reminders: ReminderService) -> None:
    """Registruje hlavní stránku aplikace (volá se z main.py).

    Parametry:
        cache: úložiště svíček, nastavení, kreseb a alertů.
        alert_engine: vyhodnocování alertů na pozadí (stránka se k němu přihlásí).
        scanner: scanner signálů na pozadí (tabulka v grafu s indikátorem se vstupy scanneru).
        reminders: časové připomínky na pozadí (dialog v panelu Alerty).
    """

    @ui.page("/")
    async def index() -> None:
        await _build_index(cache, alert_engine, scanner, reminders)


async def _build_index(
    cache: KlineCache, alert_engine: AlertEngine, scanner: ScannerService, reminders: ReminderService
) -> None:
    # Statické soubory (styly + JS grafu + vendored knihovna) do hlavičky stránky;
    # verze v URL zajistí, že prohlížeč po změně souborů nepoužije starou cache
    ui.add_head_html(
        f'<link rel="stylesheet" href="/static/style.css?v={STATIC_VERSION}">'
        + "".join(f'<script src="/static/{name}?v={STATIC_VERSION}"></script>' for name in STATIC_SCRIPTS)
    )

    # Nastavení z minula: motiv, poslední symbol a timeframe (starší nastavení
    # jediného grafu – použijí se pro první graf, dokud není uložené rozložení)
    dark = (await cache.get_setting("theme", "dark")) != "light"
    last_symbol = await cache.get_setting("last_symbol", "binance:BTCUSDT") or "binance:BTCUSDT"
    last_timeframe = await cache.get_setting("last_timeframe", "1h") or "1h"
    # Aktivní indikátory z minula (neplatné položky se vynechají)
    saved_indicators = load_saved_indicators(await cache.get_setting("indicators"))
    # Časové pásmo zobrazení a obchodní seance z minula
    saved_timezone = await cache.get_setting("timezone", "local") or "local"
    saved_session = await cache.get_setting("session", SESSION_RTH) or SESSION_RTH
    # Zobrazení objemu z minula (Settings grafu)
    show_volume = (await cache.get_setting("show_volume", "1")) != "0"
    # Režim magnetu z minula (off / weak / strong)
    saved_magnet = await cache.get_setting("magnet", "off") or "off"
    # Styl svíček (Settings grafu); objem drží samostatné nastavení show_volume
    chart_style = load_chart_style(await cache.get_setting("chart_style"))
    # Typ grafu z minula (Bars, Candles, Line, …)
    saved_chart_type = normalize_chart_type(await cache.get_setting("chart_type"))
    # Rozložení více grafů (Layout setup): rozložení, synchronizace, aktivní
    # graf a stav každého grafu; bez uložené konfigurace jeden graf ze
    # starších nastavení
    layout_config = load_layout(
        await cache.get_setting(LAYOUT_SETTINGS_KEY),
        legacy={
            "symbol": last_symbol,
            "timeframe": last_timeframe,
            "chart_type": saved_chart_type,
            "indicators": saved_indicators,
        },
    )
    # Obsah pravého panelu (Watchlist / Alerty, "" = skrytý) a řazení alertů z minula
    saved_right_panel = await cache.get_setting("right_panel", "watchlist")
    if saved_right_panel not in RIGHT_PANELS:
        saved_right_panel = "watchlist"
    saved_alerts_sort = await cache.get_setting("alerts_sort", "newest")
    if saved_alerts_sort not in ALERT_SORTS:
        saved_alerts_sort = "newest"
    # Řazení watchlistu z minula ("" = pořadí přidání, asc / desc podle abecedy)
    saved_watchlist_sort = await cache.get_setting("watchlist_sort", "")
    if saved_watchlist_sort not in WATCHLIST_SORTS:
        saved_watchlist_sort = ""
    # Handle si držíme: každé volání ui.dark_mode() by vytvořilo nový prvek
    dark_mode = ui.dark_mode(dark)

    # Společný stav stránky (platí pro všechny grafy rozložení); stav
    # jednotlivých grafů (symbol, timeframe, indikátory, data) drží ChartPane
    state: dict = {
        "dark": dark,
        "drawing_mode": None,
        "show_volume": show_volume,
        "chart_style": chart_style,
        # Oblíbené typy grafu a timeframy (tlačítka v horní liště, hvězdičky
        # v nabídkách); klíč ve state je zároveň klíč nastavení
        "favorite_chart_types": load_favorites(
            await cache.get_setting("favorite_chart_types"), list(CHART_TYPES), DEFAULT_FAVORITE_CHART_TYPES
        ),
        "favorite_timeframes": load_favorites(await cache.get_setting("favorite_timeframes"), TIMEFRAMES, TIMEFRAMES),
        # Oblíbené indikátory (hvězdička v nabídce indikátorů); neznámé druhy se
        # nefiltrují, aby se dočasně nenačtený vlastní indikátor neztratil z oblíbených
        "favorite_indicators": load_favorites(await cache.get_setting("favorite_indicators"), None),
        "timezone": saved_timezone,
        "session": saved_session,
        "magnet": saved_magnet,
        # Rozložení a synchronizace mezi grafy
        "layout": layout_config["layout"],
        "sync": layout_config["sync"],
        "active": layout_config["active"],
        # Šablony indikátorů (seznam z nastavení)
        "indicator_templates": load_indicator_templates(await cache.get_setting(INDICATOR_TEMPLATES_KEY)),
        # Pravý panel (watchlist / alerts, prázdný = skrytý) a seznam alertů v něm
        "right_panel": saved_right_panel,
        "alerts_tab": "alerts",
        "alerts_sort": saved_alerts_sort,
        "watchlist_sort": saved_watchlist_sort,
        "watchlist_items": [],
        "alert_search_open": False,
        # Naposledy načtené alerty (hledání a řazení filtrují v paměti)
        "alerts": [],
    }
    # Grafy rozložení v pořadí slotů mřížky
    panes: list[ChartPane] = []

    def active() -> ChartPane:
        """Aktivní graf rozložení (cíl horní lišty, watchlistu a dialogů)."""
        return panes[min(state["active"], len(panes) - 1)]

    # ---------- handlery (uzávěry nad elementy vytvořenými níže) ----------

    def show_status(message: str, error: bool = False) -> None:
        # Z background tasků (živý stream) musíme vstoupit do slotu prvku
        with status_label:
            status_label.text = message
            status_label.classes(add="error" if error else "", remove="" if error else "error")

    def pane_status(pane: ChartPane, message: str, error: bool = False) -> None:
        # Zprávy grafu: aktivní graf vždy, ostatní grafy rozložení jen chyby
        # (zprávy obsahují symbol, takže je jasné, kterého grafu se týkají)
        if pane is active() or error:
            show_status(message, error)

    def badge_changed(pane: ChartPane) -> None:
        # Štítek zdroje v horní liště ukazuje stav dat aktivního grafu
        if pane is active():
            paint_badge()

    def paint_badge() -> None:
        """Štítek zdroje (živě / zpožděno / —) podle aktivního grafu."""
        pane = active()
        live = pane.badge_live
        name = pane.provider.name if pane.provider else "—"
        text = "—" if live is None else f"{name} · {'živě' if live else 'zpožděno'}"
        if pane.replay_shown:
            text = f"{name} · replay"
        # Z background tasků (živý stream) musíme vstoupit do slotu prvku
        with source_badge:
            source_badge.text = text
            source_badge.update()

    # Objekt stránky pro grafy rozložení (společné nastavení a hlášení stavu)
    page = SimpleNamespace(
        cache=cache, state=state, show_status=pane_status, badge_changed=badge_changed,
        pane_slot=panes.index,
    )

    # ---------- replay (backtest ručních obchodů) ----------

    def alerts_blocked() -> None:
        """Hláška: graf v replayi ukazuje minulost, alerty v něm nejsou."""
        show_status("Alerty v replayi nejsou k dispozici – graf ukazuje minulost.", True)

    def replay_rewind_mode() -> str:
        """Chování kroku zpět v replayi (Settings → Replay, config.yaml)."""
        mode = (load_config().get("replay") or {}).get("rewind")
        return mode if mode in REWIND_MODES else DEFAULT_REWIND_MODE

    # Lištu, panel a dialogy replaye staví ReplayUI; host zpřístupní grafy
    # rozložení a funkce stránky (uzávěry definované níže se volají až za běhu)
    replay_ui = ReplayUI(
        cache,
        SimpleNamespace(
            active=lambda: active(),
            panes=lambda: list(panes),
            show_status=show_status,
            set_drawing_mode=lambda mode: set_drawing_mode(mode),
            refresh_timeframes=lambda: refresh_timeframes(),
            rewind_mode=replay_rewind_mode,
            push_alert_lines=lambda: push_alert_lines(),
            load_symbol=lambda key: load_symbol(key),
            show_time=lambda pane, time_ms: pane.chart.show_time(time_ms // 1000),
            replay_changed=lambda: (push_overview(), paint_badge()),
            delete_drawing=lambda pane, drawing_id: delete_pane_drawing(pane, drawing_id),
            alerts_blocked=lambda: alerts_blocked(),
            right_panel=lambda: state["right_panel"],
            show_sessions=lambda: show_right_panel("replay"),
        ),
    )
    page.replay = replay_ui.controller

    # ---------- hledání symbolu (dialog ve stylu TradingView) ----------

    # Kategorie hledání (druhy instrumentů zdrojů) s popisky tlačítek
    SEARCH_CATEGORIES = {
        "all": "Vše",
        "stock": "Akcie",
        "fund": "Fondy",
        "futures": "Futures",
        "forex": "Forex",
        "crypto": "Krypto",
        "index": "Indexy",
        "commodity": "Komodity",
    }
    # Stav dialogu: kategorie, zdroj, výsledky, zvýrazněný řádek, pořadí hledání
    # a režim výběru (funkce, která zvolený symbol zapíše do pole indikátoru)
    search_state: dict = {
        "pick": None,
        "category": "all",
        "source": "all",
        "results": [],
        "visible": [],
        "rows": [],
        "highlight": 0,
        "seq": 0,
        "query": None,
        "errors": [],
    }

    async def run_symbol_search() -> None:
        # Vyhledání napříč zdroji souběžně; výsledky se zobrazují průběžně, jak
        # zdroje odpovídají (pomalá TWS nezdrží Binance). Pomalejší starší
        # hledání nesmí přepsat novější výsledky
        query = (symbol_search_input.value or "").strip()
        search_state["query"] = query
        search_state["seq"] += 1
        seq = search_state["seq"]
        providers = [p for p in PROVIDERS if search_state["source"] in ("all", p.id)]
        # Přímo zadaný "zdroj:SYMBOL" (např. symbol ze vstupů scanneru indikátoru): hledá se
        # jen v tom zdroji a symbol se nabídne i tehdy, když ho hledání zdroje nevrátí
        direct = provider_symbol(query)
        direct_row = None
        if direct:
            direct_provider, query = direct
            providers = [direct_provider]
            direct_row = result_row(direct_provider, SymbolInfo(
                direct_provider.id, query, query, "Zadaný symbol", direct_provider.symbol_kind(query)
            ))
        found_by_provider: dict[str, list[dict]] = {}
        search_state["errors"] = []
        search_state["highlight"] = 0

        async def search_one(provider) -> None:
            try:
                found = await provider.search(query)
            except Exception:
                found = None
            if seq != search_state["seq"]:
                return
            if found is None:
                search_state["errors"].append(provider.name)
            else:
                found_by_provider[provider.id] = [result_row(provider, info) for info in found[:SEARCH_PER_PROVIDER]]
            # Výsledky v pořadí zdrojů, pak podle relevance (stabilní řazení)
            results = [r for p in providers for r in found_by_provider.get(p.id, [])]
            if direct_row and all(result["key"] != direct_row["key"] for result in results):
                results.insert(0, direct_row)
            results.sort(key=lambda result: search_relevance(result["symbol"], query))
            search_state["results"] = results[:SEARCH_LIMIT]
            render_search_results()

        await asyncio.gather(*(search_one(provider) for provider in providers))

    def result_row(provider, info: SymbolInfo) -> dict:
        # Řádek výsledku hledání (symbol zdroje i přímo zadaný „zdroj:SYMBOL“)
        return {
            "key": f"{info.provider_id}:{info.symbol}",
            "symbol": info.symbol,
            "description": info.description,
            "kind": info.kind,
            "exchange": info.exchange or provider.name,
            "provider": provider.name,
            "code": provider_code(provider.id),
        }

    def search_relevance(symbol: str, query: str) -> int:
        # Přesná shoda > prefix > výskyt; porovnává se bez oddělovačů, aby
        # "EURUSD" našlo i EUR.USD a EURUSD=X
        def compact(text: str) -> str:
            return "".join(ch for ch in text.upper() if ch.isalnum())

        symbol, wanted = compact(symbol), compact(query)
        if symbol == wanted:
            return 0
        if symbol.startswith(wanted):
            return 1
        return 2

    def on_search_input(event) -> None:
        # Psaní v dialogu: nové hledání jen při změně textu (programové
        # nastavení hodnoty při otevření hledání spouští samo)
        if (event.value or "").strip() != search_state["query"]:
            background_tasks.create(run_symbol_search())

    def render_search_results() -> None:
        # Přestaví seznam výsledků podle zvolené kategorie
        category = search_state["category"]
        visible = [r for r in search_state["results"] if category == "all" or r["kind"] == category]
        search_state["visible"] = visible
        search_state["rows"] = []
        search_state["highlight"] = min(search_state["highlight"], max(0, len(visible) - 1))
        for key, chip in search_chips.items():
            chip.classes(add="active" if key == category else "", remove="" if key == category else "active")
        symbol_search_results.clear()
        with symbol_search_results:
            if not visible:
                empty = "Nic nenalezeno."
                if search_state["errors"]:
                    empty += " Nedostupné zdroje: " + ", ".join(search_state["errors"]) + "."
                ui.label(empty).classes("sym-empty")
            for index, result in enumerate(visible):
                classes = "sym-row w-full" + (" active" if index == search_state["highlight"] else "")
                with ui.row().classes(classes).on("click", make_pick_symbol(result["key"])) as row:
                    # Logo: první znak symbolu v kolečku barvy druhu instrumentu
                    ui.label(result["symbol"].lstrip("^")[:1]).classes(f"sym-logo sym-logo-{result['kind'] or 'other'}")
                    with ui.column().classes("sym-text"):
                        ui.label(result["symbol"]).classes("sym-symbol")
                        with ui.row().classes("sym-desc-row"):
                            if result["description"]:
                                ui.label(result["description"]).classes("sym-desc")
                            if result["kind"]:
                                ui.label(result["kind"]).classes("sym-kind")
                    ui.space()
                    ui.label(result["exchange"]).classes("sym-exchange")
                    ui.label(result["code"]).classes("sym-source").tooltip(f"Zdroj {result['provider']}")
                    # Přidání do aktivního seznamu (nepřepne graf); symbol už v seznamu = fajfka
                    in_list = result["key"] in watch_rows
                    ui.button(icon="check" if in_list else "add", color=None).props("flat dense round size=sm").classes(
                        "sym-add"
                    ).tooltip(
                        f"V seznamu {state.get('watchlist_name', '')}" if in_list else f"Přidat do seznamu {state.get('watchlist_name', '')}"
                    ).on("click.stop", make_search_watch_add(result["key"]))
                search_state["rows"].append(row)

    def make_pick_symbol(key: str):
        # Klik na výsledek: zavře hledání a načte graf symbolu (jako v TradingView);
        # v režimu výběru (pole symbolu ve vstupech indikátoru) symbol jen předá
        async def _handler(_event=None):
            symbol_search_dialog.close()
            pick = search_state["pick"]
            if pick:
                pick(key)
                return
            await load_symbol(key)

        return _handler

    def make_search_watch_add(key: str):
        # Tlačítko + u výsledku: přidá symbol do aktivního seznamu, dialog zůstane otevřený
        async def _handler(_event=None):
            await add_key_to_watchlist(key)
            render_search_results()

        return _handler

    def make_search_category(category: str):
        def _handler(_event=None):
            search_state["category"] = category
            search_state["highlight"] = 0
            render_search_results()

        return _handler

    async def set_search_source(event) -> None:
        # Výběr zdroje: hledá se znovu jen ve zvoleném zdroji
        search_state["source"] = event.value or "all"
        await run_symbol_search()

    def move_search_highlight(delta: int) -> None:
        # Šipky v dialogu posouvají zvýraznění a řádek se odroluje do pohledu
        rows = search_state["rows"]
        if not rows:
            return
        old = search_state["highlight"]
        new = (old + delta) % len(rows)
        search_state["highlight"] = new
        rows[old].classes(remove="active")
        rows[new].classes(add="active")
        ui.run_javascript(f"window.TickViewChart.scrollIntoView({json.dumps(f'c{rows[new].id}')})")

    async def pick_highlighted() -> None:
        # Enter v dialogu: načte zvýrazněný výsledek
        visible = search_state["visible"]
        if visible:
            await make_pick_symbol(visible[min(search_state["highlight"], len(visible) - 1)]["key"])()

    async def open_symbol_search(text: str | None = None) -> None:
        """Otevře hledání symbolu: bez textu s označeným aktuálním symbolem
        (psaní ho přepíše, jako v TradingView), jinak se znaky napsanými nad grafem."""
        if symbol_search_dialog.value and text:
            # Dialog už je otevřený (rychlé psaní nad grafem) – znaky připojíme
            symbol_search_input.value = (symbol_search_input.value or "") + text
            return
        await show_symbol_search(text)

    async def pick_symbol(current: str, on_pick) -> None:
        """Hledání symbolu pro pole symbolu ve vstupech indikátoru: zvolený
        "zdroj:SYMBOL" dostane on_pick, graf se nemění.

        Parametry:
            current: text pole (hledá se symbol bez prefixu zdroje).
            on_pick: funkce, která zvolený symbol zapíše do pole.
        """
        await show_symbol_search(split_symbol(current)[1] or None, on_pick)

    async def show_symbol_search(text: str | None, on_pick=None) -> None:
        # Společné otevření dialogu hledání (text None = aktuální symbol označený
        # k přepsání); on_pick = režim výběru pro pole symbolu, None = načtení do grafu
        search_state["pick"] = on_pick
        symbol_search_input.value = (active().symbol or "") if text is None else text
        search_state["category"] = "all"
        symbol_search_dialog.open()
        await run_symbol_search()
        if text is None:
            symbol_search_input.run_method("select")

    async def add_key_to_watchlist(key: str) -> None:
        # Přidá symbol "zdroj:symbol" do watchlistu, jen když tam ještě není
        provider_id, _, symbol = key.partition(":")
        list_name = state.get("watchlist_name", "")
        if key in watch_rows:
            show_status(f"{symbol} už v seznamu {list_name} je ({provider_id}).")
            return
        await cache.watchlist_add(state["watchlist_id"], provider_id, symbol)
        await rebuild_watchlist()
        show_status(f"{symbol} přidán do seznamu {list_name} ({provider_id}).")

    async def save_layout() -> None:
        """Uloží rozložení, synchronizaci, aktivní graf a stav všech grafů (přetrvá restart).

        Scanner si z rozložení vezme úlohy (instance indikátorů se vstupy scanneru).
        Indikátory grafů v replayi se uloží k otevřené session.
        """
        layout = {
            "layout": state["layout"],
            "sync": state["sync"],
            "active": state["active"],
            "panes": [pane.config() for pane in panes],
        }
        # Nezměněné rozložení (aktivace grafu, úprava indikátorů session) se znovu nezapisuje
        raw = json.dumps(layout)
        if raw != state.get("saved_layout"):
            state["saved_layout"] = raw
            await cache.set_setting(LAYOUT_SETTINGS_KEY, raw)
            scanner.set_layout(layout)
        # Indikátory grafů v replayi patří k session (rozložení drží živé)
        replay = replay_ui.controller
        if replay.active:
            await replay.save_pane_indicators(
                {index: pane.indicators for index, pane in enumerate(panes) if pane.indicator_session == replay.session_id}
            )

    def refresh_active_ui() -> None:
        """Horní lišta, watchlist a stavová lišta podle aktivního grafu."""
        pane = active()
        symbol_pill_label.text = pane.symbol or "—"
        refresh_watchlist_active()
        refresh_timeframes()
        render_chart_type_bar()
        refresh_session_button()
        paint_badge()

    async def load_symbol(key, pane: ChartPane | None = None) -> None:
        """Zobrazí symbol "zdroj:symbol" v aktivním grafu (nebo v pane).

        Se synchronizací symbolu (Sync in layout → Symbol) ho zobrazí všechny
        grafy rozložení.
        """
        if not key or ":" not in str(key):
            return
        target = pane or active()
        targets = list(panes) if state["sync"]["symbol"] and pane is None else [target]
        changed = [item for item in targets if item.set_symbol(str(key))]
        if not changed:
            return
        if active() in changed:
            refresh_active_ui()
        await save_layout()
        await push_alert_lines()
        # Grafy se načítají souběžně (každý má vlastní číslo načtení a zámek)
        await asyncio.gather(*(item.reload_chart() for item in changed))

    async def set_timeframe(value) -> None:
        # Stejná hodnota = jen programové nastavení přepínače (load_symbol,
        # refresh_timeframes) – graf už se načítá, znovu ho nespouštíme
        if not value or str(value) == active().timeframe:
            return
        # Se synchronizací intervalu se timeframe změní ve všech grafech
        await apply_timeframe(list(panes) if state["sync"]["interval"] else [active()], str(value))

    async def apply_timeframe(targets: list[ChartPane], value: str) -> None:
        """Nastaví timeframe grafům targets (jen těm, jejichž zdroj ho podporuje) a přenačte je."""
        changed = [pane for pane in targets if pane.set_timeframe(value)]
        # Neoblíbený timeframe zvolený z nabídky je v liště jen dočasně
        refresh_timeframes()
        await save_layout()
        # Tabulka scanneru závisí i na viditelnosti indikátoru na timeframu grafu
        push_overview(changed)
        await asyncio.gather(*(pane.reload_chart() for pane in changed if pane.key))

    async def set_chart_type(chart_type: str) -> None:
        # Typ grafu z lišty nebo nabídky (aktivní graf): graf, tlačítka v liště,
        # zvýraznění; přetrvá restart v konfiguraci rozložení
        chart_type = normalize_chart_type(chart_type)
        pane = active()
        pane.chart_type = chart_type
        pane.chart.set_chart_type(chart_type)
        render_chart_type_bar()
        await save_layout()

    def make_chart_type_click(chart_type: str):
        async def _handler() -> None:
            await set_chart_type(chart_type)

        return _handler

    # Prvky hvězdiček v nabídkách: (klíč oblíbených, hodnota) -> (span, ikona)
    menu_stars: dict = {}

    def favorite_star(key: str, value: str, allowed: list[str] | None, on_change) -> None:
        # Hvězdička položky nabídky: přidá / odebere oblíbenou položku (state[key]
        # = klíč nastavení, allowed viz normalize_favorites); .stop zabrání zároveň
        # výběru položky a zavření nabídky
        async def toggle() -> None:
            state[key] = normalize_favorites(list(set(state[key]) ^ {value}), allowed)
            paint_star(key, value)
            on_change()
            await cache.set_setting(key, json.dumps(state[key]))

        with ui.element("span").classes("menu-star").on("click.stop", toggle) as span:
            icon = icon_html("star_border")
        menu_stars[(key, value)] = (span, icon)
        paint_star(key, value)

    def paint_star(key: str, value: str) -> None:
        # Plná žlutá hvězdička = oblíbená položka
        span, icon = menu_stars[(key, value)]
        active_item = value in state[key]
        span.classes(add="on" if active_item else "", remove="" if active_item else "on")
        icon.content = svg("star" if active_item else "star_border")

    def bar_items(allowed: list[str], key: str, current: str) -> list[str]:
        # Položky lišty: oblíbené a vždy aktuální, v pořadí nabídky
        return [item for item in allowed if item in state[key] or item == current]

    def render_chart_type_bar() -> None:
        # Horní lišta: tlačítka oblíbených typů grafu (a aktuální typ aktivního
        # grafu, i když oblíbený není) jako v TradingView; v nabídce jen
        # zvýrazní aktuální typ
        current = active().chart_type
        chart_type_bar.clear()
        with chart_type_bar:
            for type_id in bar_items(list(CHART_TYPES), "favorite_chart_types", current):
                label, icon = CHART_TYPES[type_id]
                with (
                    ui.button(color=None, on_click=make_chart_type_click(type_id))
                    .props("flat dense")
                    .classes("chart-type-btn" + (" chart-type-btn-active" if type_id == current else ""))
                    .tooltip(label)
                ):
                    icon_html(icon)
        mark_active(chart_type_items, current)

    def build_chart_type_menu() -> None:
        # Nabídka všech typů grafu po sekcích s hvězdičkami (staví se jednou)
        with chart_type_menu:
            for section_index, section in enumerate(CHART_TYPE_SECTIONS):
                if section_index:
                    ui.separator()
                for type_id, type_label, type_icon in section:
                    with ui.menu_item(on_click=make_chart_type_click(type_id)).classes("menu-fav-item") as item:
                        icon_html(type_icon)
                        ui.label(type_label)
                        ui.space()
                        favorite_star("favorite_chart_types", type_id, list(CHART_TYPES), render_chart_type_bar)
                    chart_type_items[type_id] = item

    # ---------- timeframy (oblíbené v liště, všechny v nabídce) ----------

    def supported_timeframes() -> list[str]:
        # Timeframy zdroje aktivního grafu v pořadí nabídky (bez zdroje všechny)
        supported = set(active().timeframes()) if active().provider else set(TIMEFRAMES)
        return [timeframe for timeframe in TIMEFRAMES if timeframe in supported]

    def refresh_timeframes() -> None:
        # Lišta: oblíbené timeframy, které zdroj podporuje, a vždy aktuální;
        # nabídka se přestaví jen při změně podporovaných timeframů (jiný zdroj)
        supported = supported_timeframes()
        current = active().timeframe
        timeframe_toggle.options = bar_items(supported, "favorite_timeframes", current)
        timeframe_toggle.value = current
        timeframe_toggle.update()
        if state.get("timeframe_menu_for") != supported:
            state["timeframe_menu_for"] = supported
            build_timeframe_menu(supported)
        mark_active(timeframe_items, current)

    def build_timeframe_menu(supported: list[str]) -> None:
        # Nabídka podporovaných timeframů po sekcích (Minuty, Hodiny, Dny) s hvězdičkami
        timeframe_menu.clear()
        timeframe_items.clear()
        with timeframe_menu:
            for section_title, items in TIMEFRAME_SECTIONS:
                available = [(t, label) for t, label in items if t in supported]
                if not available:
                    continue
                ui.label(section_title).classes("menu-section")
                for timeframe, label in available:
                    with ui.menu_item(on_click=make_timeframe_click(timeframe)).classes("menu-fav-item") as item:
                        ui.label(label)
                        ui.space()
                        favorite_star("favorite_timeframes", timeframe, TIMEFRAMES, refresh_timeframes)
                    timeframe_items[timeframe] = item

    def make_timeframe_click(timeframe: str):
        async def _handler() -> None:
            # Výběr z nabídky: refresh_timeframes v set_timeframe ho zobrazí i v liště
            await set_timeframe(timeframe)

        return _handler

    async def toggle_theme(value: bool) -> None:
        dark_mode.set_value(value)
        state["dark"] = value
        for pane in panes:
            await pane.chart.set_theme(value)
        await cache.set_setting("theme", "dark" if value else "light")

    # ---------- kreslení (lišta ve stylu TradingView) ----------

    # Druhy kresby, které backend ukládá (cursor/measure = režimy bez ukládání)
    DRAWING_KINDS = [
        "hline", "vline", "trend", "ray", "extended", "arrow", "hray", "cross",
        "channel", "fib", "fibext", "pitchfork", "rect", "brush", "text", "sticker",
        "long", "short",
    ]
    # Skupiny nástrojů lišty (jako v TradingView): id, ikona, tooltip, nástroje;
    # položka {"section": …} je jen nadpis sekce ve flyout nabídce
    DRAWING_GROUPS = [
        {"id": "g_cursor", "icon": "cursor", "tooltip": "Kurzor (výběr a tažení kreseb)",
         "tools": [{"mode": "cursor", "tooltip": "Kurzor", "icon": "cursor"}]},
        {"id": "g_lines", "icon": "trend", "tooltip": "Čáry, kanály a pitchforky",
         "tools": [
             {"section": "Čáry"},
             {"mode": "trend", "tooltip": "Trendová čára", "icon": "trend"},
             {"mode": "ray", "tooltip": "Polopřímka", "icon": "ray"},
             {"mode": "extended", "tooltip": "Přímka (oboustranná)", "icon": "extended"},
             {"mode": "arrow", "tooltip": "Trendová šipka", "icon": "arrow"},
             {"mode": "hline", "tooltip": "Horizontální linie", "icon": "hline"},
             {"mode": "hray", "tooltip": "Horizontální polopřímka", "icon": "hray"},
             {"mode": "vline", "tooltip": "Svislá linie", "icon": "vline"},
             {"mode": "cross", "tooltip": "Kříž", "icon": "cross"},
             {"section": "Kanály"},
             {"mode": "channel", "tooltip": "Paralelní kanál (3 kliky)", "icon": "channel"},
             {"section": "Pitchforky"},
             {"mode": "pitchfork", "tooltip": "Pitchfork (3 kliky)", "icon": "pitchfork"},
         ]},
        {"id": "g_fib", "icon": "fib", "tooltip": "Fibonacci",
         "tools": [
             {"mode": "fib", "tooltip": "Fibonacci retracement", "icon": "fib"},
             {"mode": "fibext", "tooltip": "Fibonacci extension", "icon": "fibext"},
         ]},
        {"id": "g_shapes", "icon": "rect", "tooltip": "Tvary",
         "tools": [
             {"mode": "rect", "tooltip": "Obdélník", "icon": "rect"},
             {"mode": "brush", "tooltip": "Štětec", "icon": "brush"},
         ]},
        {"id": "g_text", "icon": "text", "tooltip": "Text a stickers",
         "tools": [
             {"mode": "text", "tooltip": "Text", "icon": "text"},
             {"mode": "sticker", "tooltip": "Sticker (emoji)", "icon": "sticker"},
         ]},
        {"id": "g_forecast", "icon": "long", "tooltip": "Prognózy a měření (Long / Short position, měřič)",
         "tools": [
             {"section": "Prognózy"},
             {"mode": "long", "tooltip": "Long position", "icon": "long"},
             {"mode": "short", "tooltip": "Short position", "icon": "short"},
             {"section": "Měření"},
             {"mode": "measure", "tooltip": "Měřič (Δcena, %, bary)", "icon": "measure"},
         ]},
    ]
    # Režimy všech nástrojů v pořadí lišty (oblíbené v plovoucím panelu)
    TOOL_MODES = [tool["mode"] for group in DRAWING_GROUPS for tool in group["tools"] if "mode" in tool]
    DRAWING_LABELS = {
        "hline": "Horizontální linie",
        "vline": "Svislá linie",
        "trend": "Trendová čára",
        "ray": "Polopřímka",
        "extended": "Přímka",
        "arrow": "Trendová šipka",
        "hray": "Horizontální polopřímka",
        "cross": "Kříž",
        "channel": "Paralelní kanál",
        "fib": "Fibonacci retracement",
        "fibext": "Fibonacci extension",
        "pitchfork": "Pitchfork",
        "rect": "Obdélník",
        "brush": "Štětec",
        "text": "Text",
        "sticker": "Sticker",
        "long": "Long position",
        "short": "Short position",
    }
    EMOJIS = ["😀", "🚀", "💰", "📈", "📉", "👍", "⚡", "🎯"]

    async def set_drawing_mode(mode) -> None:
        # Aktivní nástroj zvýrazníme na skupině, do které patří (jako v TradingView);
        # None (JS po dokončení kresby vrací kurzor) znamená režim kurzoru.
        # Nástroj platí pro všechny grafy rozložení – kreslit lze do kteréhokoli
        mode = mode or "cursor"
        state["drawing_mode"] = mode
        for group in DRAWING_GROUPS:
            button = drawing_buttons.get(group["id"])
            if not button:
                continue
            group_modes = [tool["mode"] for tool in group["tools"] if "mode" in tool]
            button.classes(
                add="drawing-btn-active" if mode in group_modes else "",
                remove="" if mode in group_modes else "drawing-btn-active",
            )
        for pane in panes:
            await pane.chart.set_drawing_mode(None if mode == "cursor" else mode)

    async def load_drawing_defaults() -> dict:
        # Výchozí styl nových kreseb: uživatelská výchozí šablona typu kresby
        # („Save as default“ v Settings kresby), jinak tovární styl; všechny
        # výchozí šablony kreseb jedním dotazem do databáze
        saved = await cache.get_settings_prefix(default_template_key(drawing_scope("")))
        defaults = {}
        for kind in DRAWING_KINDS:
            try:
                template = json.loads(saved.get(default_template_key(drawing_scope(kind))) or "{}")
            except ValueError:
                template = {}
            defaults[kind] = normalize_drawing_style(template, kind)
        return defaults

    async def push_drawing_defaults() -> None:
        # Výchozí styl nových kreseb do všech grafů rozložení (načte se jednou
        # a drží ve state pro grafy přidané později)
        state["drawing_defaults"] = await load_drawing_defaults()
        for pane in panes:
            pane.chart.set_drawing_defaults(state["drawing_defaults"])

    # ---------- oblíbené nástroje (plovoucí panel) ----------

    async def save_favorites(modes) -> None:
        # Oblíbené nástroje z hvězdiček ve flyoutu (JS je už zobrazil)
        favorites = normalize_favorites(modes, TOOL_MODES)
        await cache.set_setting("favorite_tools", json.dumps(favorites))
        show_status(f"Oblíbené nástroje: {len(favorites)}." if favorites else "Žádné oblíbené nástroje.")

    async def save_favorites_position(left, top) -> None:
        # Pozice plovoucího panelu po přetažení (px vůči oknu)
        position = normalize_panel_position({"left": left, "top": top})
        if position:
            await cache.set_setting("favorites_position", json.dumps(position))

    def paint_favorites_button() -> None:
        # Ikona hvězdy v liště: plná = panel oblíbených zobrazen
        set_action_icon("favorites", "star" if state["favorites_visible"] else "star_border")

    async def toggle_favorites_bar() -> None:
        # Tlačítko hvězdy v liště: zobrazí / skryje plovoucí panel oblíbených
        state["favorites_visible"] = not state.get("favorites_visible", True)
        visible = state["favorites_visible"]
        paint_favorites_button()
        active().chart.set_favorites_visible(visible)
        await cache.set_setting("favorites_visible", "1" if visible else "0")
        show_status(f"Panel oblíbených nástrojů {'zobrazen' if visible else 'skryt'}.")

    async def restore_favorites() -> None:
        # Oblíbené nástroje, pozice a viditelnost panelu z minula (po init
        # prvního grafu; panel je společný všem grafům)
        favorites = load_favorites(await cache.get_setting("favorite_tools"), TOOL_MODES)
        position = load_panel_position(await cache.get_setting("favorites_position"))
        state["favorites_visible"] = (await cache.get_setting("favorites_visible", "1")) != "0"
        paint_favorites_button()
        active().chart.set_favorites(favorites, position, state["favorites_visible"])

    # Popisy režimů magnetu pro stavovou lištu
    MAGNET_LABELS = {
        "off": "vypnut",
        "weak": "slabý – bod kresby se přichytí k O/H/L/C svíčky, když je kurzor blízko",
        "strong": "silný – body kreseb se vždy přichytí k nejbližší O/H/L/C svíčky",
    }

    def set_action_icon(action: str, icon: str) -> None:
        # Vymění SVG piktogram akčního tlačítka lišty (zámek, oko, hvězda)
        action_icons[action].content = svg(icon)

    def paint_action(action: str, on: bool) -> None:
        # Zvýrazní akční tlačítko lišty (magnet, tužka, zámek, oko)
        action_buttons[action].classes(add="drawing-btn-active" if on else "", remove="" if on else "drawing-btn-active")

    async def set_magnet(mode) -> None:
        # Magnet: přichytávání bodů kreseb k OHLC svíčky pod kurzorem (všechny
        # grafy rozložení); režim se ukládá a přetrvá restart
        mode = mode if mode in MAGNET_LABELS else "off"
        state["magnet"] = mode
        paint_action("magnet", mode != "off")
        for pane in panes:
            await pane.chart.set_magnet(mode)
        await cache.set_setting("magnet", mode)
        show_status(f"Magnet {MAGNET_LABELS[mode]}.")

    async def toggle_pencil() -> None:
        # Režim tužky: nástroj zůstává vybraný i po nakreslení (jako v TradingView)
        state["keep_tool"] = not state.get("keep_tool", False)
        paint_action("pencil", state["keep_tool"])
        for pane in panes:
            await pane.chart.set_keep_tool(state["keep_tool"])
        show_status(f"Režim kreslení {'zapnut' if state['keep_tool'] else 'vypnut'}.")

    async def toggle_lock() -> None:
        # Zámek: kresby nelze vybírat, posouvat ani mazat (všechny grafy)
        state["locked"] = not state.get("locked", False)
        set_action_icon("lock", "lock" if state["locked"] else "lock_open")
        paint_action("lock", state["locked"])
        for pane in panes:
            await pane.chart.set_locked(state["locked"])
        show_status(f"Kresby {'uzamčeny' if state['locked'] else 'odemčeny'}.")

    async def toggle_hide_drawings() -> None:
        # Skrytí/zobrazení všech kreseb (ikona oka, všechny grafy)
        state["hidden"] = not state.get("hidden", False)
        set_action_icon("eye", "eye_off" if state["hidden"] else "eye")
        paint_action("eye", state["hidden"])
        for pane in panes:
            await pane.chart.set_drawings_hidden(state["hidden"])

    async def trash_drawings() -> None:
        # Koš: smaže označenou kresbu aktivního grafu, jinak všechny jeho
        # kresby; do databáze se smazání propíše přes události "delete"
        removed = await active().chart.trash_drawings()
        if removed:
            show_status(f"Smazáno kreseb: {removed}.")

    async def apply_pane_settings(pane: ChartPane) -> None:
        """Pošle nově inicializovanému grafu společné nastavení kreslení a rozložení."""
        chart = pane.chart
        if "drawing_defaults" not in state:
            state["drawing_defaults"] = await load_drawing_defaults()
        chart.set_drawing_defaults(state["drawing_defaults"])
        await chart.set_drawing_mode(None if state["drawing_mode"] in (None, "cursor") else state["drawing_mode"])
        await chart.set_magnet(state["magnet"])
        await chart.set_keep_tool(state.get("keep_tool", False))
        await chart.set_locked(state.get("locked", False))
        await chart.set_drawings_hidden(state.get("hidden", False))
        if pane is active():
            chart.set_active()

    # ---------- Settings kresby včetně textu (pravý klik / dvojklik / nástroj Text) ----------

    # Kresby bez čáry (šířka a styl nemají smysl)
    NO_LINE_KINDS = {"text", "sticker"}
    # Stav přepínačů tučného písma a kurzívy v dialogu Text
    text_flags: dict = {"bold": False, "italic": False}

    def paint_text_flags() -> None:
        # Tlačítka B / I v dialogu Text: zvýrazněné = zapnuté
        for key, button in (("bold", text_bold), ("italic", text_italic)):
            on = text_flags[key]
            button.classes(add="active" if on else "", remove="" if on else "active")

    def toggle_text_flag(key: str) -> None:
        text_flags[key] = not text_flags[key]
        paint_text_flags()

    def apply_drawing_template(values: dict) -> None:
        # Promítne hodnoty šablony (barva, šířka a styl čáry, písmo, pozadí
        # a rámeček textu, viditelnost, u pozice parametry výpočtu) do polí
        # dialogu; neplatné nebo chybějící hodnoty nahradí výchozími
        kind = (state.get("drawing_settings") or {}).get("kind")
        style = normalize_drawing_style(values, kind)
        options = style["options"]
        drawing_color.value = style["color"]
        text_color.value = style["color"]
        drawing_width.value = options["width"]
        drawing_style.value = options["lineStyle"]
        text_size.value = options["fontSize"] if options["fontSize"] in TEXT_FONT_SIZES else min(
            TEXT_FONT_SIZES, key=lambda size: abs(size - options["fontSize"])
        )
        text_options = normalize_text_options(options)
        text_flags["bold"] = text_options["bold"]
        text_flags["italic"] = text_options["italic"]
        paint_text_flags()
        text_background.value = text_options["background"]
        text_background_color.value = text_options["backgroundColor"]
        text_border.value = text_options["border"]
        text_border_color.value = text_options["borderColor"]
        text_wrap.value = text_options["wrap"]
        drawing_visibility.fill(options["visibility"])
        if kind in POSITION_KINDS:
            position_account.value = options["accountSize"]
            position_risk.value = options["risk"]
            position_risk_unit.value = options["riskUnit"]
            position_lot.value = options["lotSize"]

    def apply_drawing_factory() -> None:
        # „Apply defaults“ bez uložené výchozí šablony: tovární styl kresby
        apply_drawing_template({})

    def collect_drawing_settings() -> dict | None:
        # Styl kresby z polí dialogu (bez textu – ten do šablony nepatří)
        current = state.get("drawing_settings")
        if not current:
            return None
        is_text = current["kind"] == "text"
        return normalize_drawing_style(
            {
                "color": text_color.value if is_text else drawing_color.value,
                "options": {
                    "width": drawing_width.value,
                    "lineStyle": drawing_style.value,
                    "fontSize": text_size.value,
                    "visibility": drawing_visibility.values(),
                    # Vzhled textu (u ostatních kreseb ho normalizace zahodí)
                    "bold": text_flags["bold"],
                    "italic": text_flags["italic"],
                    "background": text_background.value,
                    "backgroundColor": text_background_color.value,
                    "border": text_border.value,
                    "borderColor": text_border_color.value,
                    "wrap": text_wrap.value,
                    # Parametry pozice (u ostatních kreseb je normalizace zahodí)
                    "accountSize": position_account.value,
                    "risk": position_risk.value,
                    "riskUnit": position_risk_unit.value,
                    "lotSize": position_lot.value,
                },
            },
            current["kind"],
        )

    def set_price_decimals(decimals) -> None:
        # Ceny pozice s přesností instrumentu (529.95, forex 1.10025) místo
        # dlouhých desetinných čísel; krok šipek = jeden tick
        try:
            digits = max(0, min(8, int(decimals)))
        except (TypeError, ValueError):
            digits = 2
        for field in (position_entry, position_target, position_stop):
            field.format = f"%.{digits}f"
            field.props(f"step={10 ** -digits:.{digits}f}")
        return digits

    async def show_drawing_dialog(kind: str, title: str) -> None:
        # Společné nastavení dialogu podle druhu kresby: první záložka (Text /
        # Inputs / Style), viditelná pole a šablony; druhá záložka Visibility
        is_position = kind in POSITION_KINDS
        is_text = kind == "text"
        drawing_settings_title.text = title
        style_tab.props(f'label="{"Text" if is_text else "Inputs" if is_position else "Style"}"')
        drawing_tabs.value = style_tab
        text_fields.set_visibility(is_text)
        line_fields.set_visibility(kind not in NO_LINE_KINDS and not is_position)
        position_fields.set_visibility(is_position)
        sticker_note.set_visibility(kind == "sticker")
        drawing_templates.set_visible(kind != "sticker")
        await drawing_templates.refresh()
        drawing_settings_dialog.open()

    async def open_text_dialog(pane: ChartPane, time_s: int, price: float) -> None:
        # Nástroj Text: dialog jako v TradingView se objeví hned po kliknutí do
        # grafu; Ok vytvoří kresbu s vyplněným vzhledem (výchozí šablona typu)
        state["drawing_settings"] = {
            "id": None,
            "kind": "text",
            "points": [{"time": int(time_s), "price": float(price)}],
            "pane": pane,
        }
        default = await load_default_template(cache, drawing_scope("text")) or {}
        apply_drawing_template(default)
        drawing_text.value = ""
        await show_drawing_dialog("text", "Text")
        drawing_text.run_method("focus")

    async def open_drawing_settings(payload: dict, pane: ChartPane) -> None:
        # Dialog Settings existující kresby podle jejího typu
        kind = payload.get("kind")
        points = drawing_points(payload)
        state["drawing_settings"] = {"id": int(payload["id"]), "kind": kind, "points": points, "pane": pane}
        apply_drawing_template({"color": payload.get("color"), "options": payload.get("options") or {}})
        if kind in POSITION_KINDS and len(points) >= 3:
            digits = set_price_decimals(payload.get("decimals"))
            state["drawing_settings"]["decimals"] = digits
            position_entry.value = round(points[0]["price"], digits)
            position_target.value = round(points[1]["price"], digits)
            position_stop.value = round(points[2]["price"], digits)
        drawing_text.value = payload.get("text") or ""
        await show_drawing_dialog(kind, DRAWING_LABELS.get(kind, kind))

    async def refresh_symbol_drawings(pane: ChartPane, skip: ChartPane | None = None) -> None:
        """Znovu načte kresby ve všech grafech se stejným vlastníkem kreseb jako pane (kromě skip).

        Kresby patří k symbolu (v replayi k session), takže změna v jednom grafu
        se projeví i v dalších grafech rozložení se stejným symbolem (třeba na
        jiném timeframu).
        """
        owner = pane.drawing_owner
        targets = [item for item in panes if item is not skip and item.key and item.drawing_owner == owner]
        if not targets:
            return
        drawings = await cache.load_drawings(*owner)
        for item in targets:
            await item.reload_drawings(drawings)

    async def delete_pane_drawing(pane: ChartPane, drawing_id: int, skip: ChartPane | None = None) -> None:
        """Smaže kresbu grafu a obnoví kresby v ostatních grafech (skip = graf, který ji už nemá)."""
        await cache.delete_drawing(*pane.drawing_owner, int(drawing_id))
        # Alert na kresbu si změnu (posun / smazání) načte znovu
        alert_engine.drawing_changed(int(drawing_id))
        await refresh_symbol_drawings(pane, skip=skip)

    async def submit_drawing_settings() -> None:
        # Uloží nastavení kresby do databáze (nový text vytvoří) a promítne ho do grafů
        current = state.get("drawing_settings")
        values = collect_drawing_settings()
        pane = (current or {}).get("pane")
        if not current or values is None or pane is None or pane.closed or not pane.key:
            drawing_settings_dialog.close()
            return
        kind = current["kind"]
        text = None
        if kind == "text":
            text = (drawing_text.value or "").strip()
            if not text:
                ui.notify("Text nesmí být prázdný.", type="warning")
                return
        # Pozice: ceny vstupu, cíle a stopu z dialogu (časy zůstanou)
        points = None
        if kind in POSITION_KINDS:
            try:
                points = normalize_position_points(
                    kind, position_entry.value, position_target.value, position_stop.value, current["points"],
                    current.get("decimals"),
                )
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
                return
        # Sticker barvu nemá (emoji) – necháme ji prázdnou jako dosud
        color = values["color"] if kind != "sticker" else None
        options = values["options"]
        drawing_settings_dialog.close()
        if current["id"] is None:
            # Nový text z nástroje Text: graf ho přidá a pošle k uložení
            anchor = current["points"][0]
            await pane.chart.add_text(anchor["time"], anchor["price"], text, color, options)
            show_status("Text přidán a uložen.")
            return
        await cache.update_drawing_style(*pane.drawing_owner, current["id"], color, text, options)
        if points is not None:
            await cache.update_drawing(*pane.drawing_owner, current["id"], points, None)
            alert_engine.drawing_changed(current["id"])
        # Grafy se symbolem kresby si ji načtou znovu (viditelnost podle
        # timeframu může kresbu v některém grafu skrýt nebo ukázat)
        await refresh_symbol_drawings(pane)
        show_status(f"{DRAWING_LABELS.get(kind, kind)}: nastavení uloženo.")

    async def set_volume_visible(visible: bool) -> None:
        # Zobrazení objemu (Settings grafu / oko v legendě; všechny grafy);
        # přetrvá restart. Stejná hodnota (i programové nastavení přepínače) nic nedělá
        if bool(visible) == state["show_volume"]:
            return
        state["show_volume"] = bool(visible)
        for pane in panes:
            pane.chart.set_volume_visible(state["show_volume"])
        await cache.set_setting("show_volume", "1" if state["show_volume"] else "0")

    # ---------- časové pásmo a obchodní seance (stavová lišta) ----------

    def mark_active(items: dict, active_value: str, css_class: str = "menu-item-active") -> None:
        # Zvýrazní zvolenou položku (nabídky, přepínače) třídou css_class a ostatním ji odebere
        for value, item in items.items():
            on = value == active_value
            item.classes(add=css_class if on else "", remove="" if on else css_class)

    def make_timezone_click(value: str):
        async def _handler() -> None:
            await set_timezone(value)

        return _handler

    async def set_timezone(value: str) -> None:
        # Časové pásmo osy, crosshairu a hodin (všechny grafy); uloží se a přetrvá restart
        state["timezone"] = value
        mark_active(timezone_items, value)
        for pane in panes:
            pane.apply_timezone()
        await cache.set_setting("timezone", value)

    def refresh_session_button() -> None:
        # Popisek RTH/ETH; u instrumentu aktivního grafu bez rozšířených hodin
        # (krypto, forex, Yahoo) přepínač nemá vliv, proto je neaktivní
        pane = active()
        available = bool(pane.provider and pane.symbol and pane.provider.regular_session(pane.symbol))
        session_button.text = "RTH" if state["session"] == SESSION_RTH else "ETH"
        if available:
            session_button.props(remove="disable")
        else:
            session_button.props("disable")
        mark_active(session_items, state["session"])

    async def set_session(value: str) -> None:
        # Přepnutí seance: uloží volbu a přenačte grafy (RTH filtruje svíčky)
        if value == state["session"]:
            return
        state["session"] = value
        await cache.set_setting("session", value)
        refresh_session_button()
        await asyncio.gather(*(pane.reload_chart() for pane in panes))

    # ---------- Settings grafu (styl svíček, objem, šablony) ----------

    # Stav dialogu: „filling“ potlačí náhled při hromadném vyplňování polí,
    # „dirty“ = grafy ukazují neuložený náhled (zavření bez Ok ho vrátí)
    chart_dialog_state: dict = {"filling": False, "dirty": False}

    def collect_chart_values() -> dict:
        # Hodnoty dialogu (styl svíček + objem) – obsah šablony grafu
        style = normalize_chart_style({key: field.value for key, field in chart_fields.items()})
        return {**style, "volume": bool(volume_checkbox.value)}

    def fill_chart_values(values: dict, preview: bool = True) -> None:
        # Vyplní pole dialogu (otevření, šablona, výchozí hodnoty); náhled
        # v grafu se pošle jednou až po vyplnění všech polí
        style = normalize_chart_style(values)
        chart_dialog_state["filling"] = True
        try:
            for key, field in chart_fields.items():
                field.value = style[key]
            volume_checkbox.value = bool(values.get("volume", True))
        finally:
            chart_dialog_state["filling"] = False
        if preview:
            preview_chart_style()

    def broadcast_chart_style(style: dict, volume: bool) -> None:
        # Styl svíček a objem platí pro všechny grafy rozložení
        for pane in panes:
            pane.chart.set_chart_style(style)
            pane.chart.set_volume_visible(volume)

    def preview_chart_style() -> None:
        # Živý náhled v grafech (jako v TradingView); uloží se až tlačítkem Ok
        if chart_dialog_state["filling"]:
            return
        values = collect_chart_values()
        chart_dialog_state["dirty"] = True
        broadcast_chart_style(normalize_chart_style(values), values["volume"])

    def has_volume() -> bool:
        # Posílá zdroj u instrumentu aktivního grafu objem? (forex MIDPOINT ani indexy ne)
        return any(candle.volume > 0 for candle in active().candles or [])

    async def open_chart_settings() -> None:
        # Pole se vyplní uloženým stavem bez náhledu (graf ho už ukazuje)
        fill_chart_values({**state["chart_style"], "volume": state["show_volume"]}, preview=False)
        # Objem se u instrumentu bez objemu nezobrazí ani zapnutý – vysvětlíme proč
        volume_note.set_visibility(not has_volume())
        chart_dialog_state["dirty"] = False
        await chart_templates.refresh()
        chart_settings_dialog.open()

    async def submit_chart_settings() -> None:
        # Ok: styl svíček uloží do nastavení, objem přes set_volume_visible;
        # grafy už ukazují potvrzené hodnoty, zavření nic nevrací
        values = collect_chart_values()
        state["chart_style"] = normalize_chart_style(values)
        chart_dialog_state["dirty"] = False
        chart_settings_dialog.close()
        await cache.set_setting("chart_style", json.dumps(state["chart_style"]))
        await set_volume_visible(values["volume"])
        show_status("Settings grafu uloženy.")

    def on_chart_dialog_change(event) -> None:
        # Zavření bez Ok (Cancel, křížek, Esc, klik mimo) vrátí uložený vzhled
        if event.value or not chart_dialog_state["dirty"]:
            return
        chart_dialog_state["dirty"] = False
        broadcast_chart_style(state["chart_style"], state["show_volume"])

    def drawing_points(payload: dict) -> list[dict]:
        # Body kresby z události grafu (čas v sekundách UTC + cena)
        return [
            {"time": int(p["time"]), "price": float(p["price"])}
            for p in (payload.get("points") or [])
        ]

    # Události grafu, které vznikly interakcí uživatele v konkrétním grafu –
    # ten se před zpracováním stane aktivním (dialogy pak míří na něj)
    USER_EVENTS = {
        "indicator", "text_request", "sticker_request", "drawing_settings", "chart_settings",
        "volume", "symbol_search", "alert_request", "alert_action", "replay_pick", "replay_position_order",
    }

    async def process_chart_events() -> None:
        # Stránkový timer vyzvedne události všech grafů jedním dotazem
        # (kresby, nástroje, akce, donačtení historie) a projeví je v databázi
        # a v UI. Grafy mají stav kreseb aktuální samy – nové kresbě jen
        # pošleme id z databáze, nic se nenačítá znovu
        if not panes:
            return
        try:
            result = await ui.run_javascript("return window.TickViewChart.popAllEvents();")
        except TimeoutError:
            # Prohlížeč neodpověděl včas (zaneprázdněný / uspaný) – zkusíme příště
            return
        by_id = {pane.chart.js_id: pane for pane in panes}
        for chart_id, events in (result or {}).items():
            pane = by_id.get(chart_id)
            if pane is None or not isinstance(events, list):
                continue
            for payload in events:
                if isinstance(payload, dict):
                    await handle_chart_event(pane, payload)

    async def handle_chart_event(pane: ChartPane, payload: dict) -> None:
        """Zpracuje jednu událost grafu pane (viz process_chart_events)."""
        event_type = payload.get("type")
        provider_id, symbol, timeframe = pane.provider_id, pane.symbol, pane.timeframe
        # Lišta, nástroje a oblíbené jsou společné stránce; ostatní události
        # patří ke grafu se symbolem
        if event_type not in ("tool", "action", "favorites", "favorites_position") and (not provider_id or not symbol):
            return
        if event_type in USER_EVENTS:
            await activate_pane(pane)
        # Události replaye (výběr startu, obchody v grafu) a alerty v grafu replaye vyřídí ReplayUI
        if await replay_ui.handle_chart_event(pane, event_type, payload, drawing_points(payload)):
            return
        if event_type == "load_older":
            # Graf se posunul k začátku dat – donačteme starší svíčky
            pane.start_load_older(payload.get("before"))
        elif event_type == "indicator":
            await on_indicator_event(payload)
        elif event_type == "tool":
            await set_drawing_mode(payload.get("mode"))
        elif event_type == "action":
            action = payload.get("action")
            if action == "magnet":
                await set_magnet(payload.get("mode"))
            elif action == "pencil":
                await toggle_pencil()
            elif action == "lock":
                await toggle_lock()
            elif action == "eye":
                await toggle_hide_drawings()
            elif action == "trash":
                await trash_drawings()
            elif action == "favorites":
                await toggle_favorites_bar()
        elif event_type == "favorites":
            await save_favorites(payload.get("modes"))
        elif event_type == "favorites_position":
            await save_favorites_position(payload.get("left"), payload.get("top"))
        elif event_type == "sticker_request":
            state["sticker_pending"] = {
                "pane": pane,
                "time": int(payload.get("time")),
                "price": float(payload.get("price")),
            }
            sticker_dialog.open()
        elif event_type == "delete":
            drawing_id = payload.get("id")
            if drawing_id:
                await delete_pane_drawing(pane, int(drawing_id), skip=pane)
                show_status("Kresba smazána.")
        elif event_type == "update":
            drawing_id = payload.get("id")
            if drawing_id:
                await cache.update_drawing(*pane.drawing_owner, int(drawing_id), drawing_points(payload), payload.get("text"))
                alert_engine.drawing_changed(int(drawing_id))
                await refresh_symbol_drawings(pane, skip=pane)
        elif event_type == "text_request":
            await open_text_dialog(pane, payload.get("time"), payload.get("price"))
        elif event_type == "drawing_settings" and payload.get("id"):
            await open_drawing_settings(payload, pane)
        elif event_type == "chart_settings":
            await open_chart_settings()
        elif event_type == "volume":
            await set_volume_visible(bool(payload.get("visible")))
        elif event_type == "symbol_search":
            # Psaní nad grafem otevře hledání symbolu s napsanými znaky
            await open_symbol_search(str(payload.get("text") or ""))
        elif event_type == "alert_request":
            # Pravý klik: alert na ceně pod kurzorem, nebo na čáře kresby
            drawing_id = payload.get("drawingId")
            await open_alert_dialog(price=payload.get("price"), drawing_id=int(drawing_id) if drawing_id else None)
        elif event_type == "alert_action" and payload.get("action") in ("edit", "delete"):
            # Pravý klik na čáru alertu: úprava nebo smazání (alert mohl mezitím zmizet)
            alert = await cache.get_alert(int(payload.get("id") or 0))
            if alert:
                await make_alert_action(payload["action"], alert)()
        elif event_type == "add" and payload.get("kind") in DRAWING_KINDS:
            # Styl nové kresby (výchozí šablona typu nebo dialog Text) zkontrolujeme
            style = normalize_drawing_style(payload, payload["kind"])
            drawing_id = await cache.add_drawing(
                *pane.drawing_owner,
                timeframe,
                payload["kind"],
                drawing_points(payload),
                style["color"],
                payload.get("text"),
                style["options"],
            )
            # Graf si kresbu spáruje podle lokálního id (další úpravy už jdou s id)
            if payload.get("localId") is not None:
                await pane.chart.assign_drawing_id(payload["localId"], drawing_id)
            await refresh_symbol_drawings(pane, skip=pane)
            # Kresba v replayi může mít vlastní nápovědu (pozice = zadání obchodu z menu)
            show_status(replay_ui.drawing_hint(pane, payload["kind"])
                        or f"Kresba uložena ({DRAWING_LABELS[payload['kind']]}) k {symbol}")

    # ---------- watchlist ----------

    # Řádky watchlistu: key -> elementy pro aktualizaci cen
    watch_rows: dict = {}

    def _format_price(price: float) -> str:
        # Formát ceny podle řádu hodnoty
        if abs(price) >= 1000:
            return f"{price:,.2f}"
        if abs(price) >= 1:
            return f"{price:,.4f}"
        return f"{price:.8f}".rstrip("0").rstrip(".")

    async def refresh_watchlist_prices() -> None:
        # Aktualizuje ceny a denní změny v řádcích watchlistu (per zdroj 1 dotaz)
        by_provider: dict[str, list[str]] = {}
        for key in watch_rows:
            provider_id, _, symbol = key.partition(":")
            by_provider.setdefault(provider_id, []).append(symbol)
        for provider_id, symbols in by_provider.items():
            try:
                provider = get_provider(provider_id)
                tickers = await provider.tickers(symbols)
            except Exception as exc:
                show_status(f"Ceny watchlistu ({provider_id}) nedostupné: {exc}", error=True)
                continue
            for symbol in symbols:
                quote = tickers.get(symbol)
                if not quote:
                    continue
                row = watch_rows.get(f"{provider_id}:{symbol}")
                if not row:
                    continue
                with row["price"]:
                    row["price"].text = _format_price(quote["price"])
                with row["change"]:
                    row["change"].text = f"{quote['change_pct']:+.2f} %"
                    row["change"].classes(
                        add="up" if quote["change_pct"] >= 0 else "down",
                        remove="down" if quote["change_pct"] >= 0 else "up",
                    )

    def make_watch_click(key: str):
        async def _handler(_event):
            await load_symbol(key)

        return _handler

    def make_watch_remove(key: str):
        async def _handler(_event):
            provider_id, _, symbol = key.partition(":")
            await cache.watchlist_remove(state["watchlist_id"], provider_id, symbol)
            await rebuild_watchlist()

        return _handler

    async def watchlist_add_current() -> None:
        # Přidá symbol aktivního grafu do watchlistu
        if active().key:
            await add_key_to_watchlist(active().key)

    # ---------- seznamy symbolů (více watchlistů jako v TradingView) ----------

    async def select_active_watchlist(lists: list[dict]) -> dict:
        # Aktivní seznam: uložená volba, jinak první (smazaný seznam = první)
        saved = await cache.get_setting("active_watchlist")
        return next((item for item in lists if str(item["id"]) == saved), lists[0])

    def render_watchlist_menu(lists: list[dict]) -> None:
        # Nabídka seznamů: přepnutí (s počtem symbolů) a správa aktivního seznamu;
        # pod hlavičkou kulatá tlačítka s iniciálou pro rychlé přepnutí (jako v TradingView)
        watchlist_button.text = state["watchlist_name"]
        watchlist_quick.clear()
        with watchlist_quick:
            for item in lists:
                active = item["id"] == state["watchlist_id"]
                ui.button(item["name"][:1].upper(), color=None, on_click=make_switch_watchlist(item["id"])).props(
                    "flat dense round no-caps"
                ).classes("wl-quick-btn" + (" active" if active else "")).tooltip(item["name"])
        watchlist_menu.clear()
        with watchlist_menu:
            for item in lists:
                with ui.menu_item(on_click=make_switch_watchlist(item["id"])).classes(
                    "wl-item" + (" menu-item-active" if item["id"] == state["watchlist_id"] else "")
                ):
                    ui.label(item["name"]).classes("wl-name")
                    ui.space()
                    ui.label(str(item["count"])).classes("wl-count")
            ui.separator()
            ui.menu_item("Nový seznam…", on_click=lambda: open_watchlist_name("create")).classes("wl-item")
            ui.menu_item("Přejmenovat…", on_click=lambda: open_watchlist_name("rename")).classes("wl-item")
            ui.menu_item("Vytvořit kopii…", on_click=lambda: open_watchlist_name("copy")).classes("wl-item")
            ui.menu_item("Vyprázdnit seznam", on_click=lambda: confirm_watchlist("clear")).classes("wl-item")
            # Poslední seznam smazat nejde – vždy musí existovat aspoň jeden
            if len(lists) > 1:
                ui.menu_item("Smazat seznam", on_click=lambda: confirm_watchlist("delete")).classes("wl-item wl-danger")

    def make_switch_watchlist(list_id: int):
        async def _handler() -> None:
            await cache.set_setting("active_watchlist", str(list_id))
            await rebuild_watchlist()

        return _handler

    def open_watchlist_name(mode: str) -> None:
        # Dialog názvu seznamu: nový / přejmenování / kopie aktivního seznamu
        state["watchlist_name_mode"] = mode
        titles = {"create": "Nový seznam", "rename": "Přejmenovat seznam", "copy": "Kopie seznamu"}
        watchlist_name_title.text = titles[mode]
        current = state["watchlist_name"]
        watchlist_name_input.value = {"create": "", "rename": current, "copy": f"{current} (kopie)"}[mode]
        watchlist_name_dialog.open()

    async def submit_watchlist_name() -> None:
        # Uloží název podle režimu dialogu; nový seznam i kopie se rovnou zobrazí
        name = clean_name(watchlist_name_input.value)
        if not name:
            ui.notify(f"Zadejte název seznamu (1–{MAX_NAME_LENGTH} znaků).", type="warning")
            return
        mode = state.get("watchlist_name_mode")
        watchlist_name_dialog.close()
        if mode == "rename":
            # Přejmenování mění jen nabídku – řádky ani ceny se nepřestavují
            await cache.watchlist_rename(state["watchlist_id"], name)
            state["watchlist_name"] = name
            render_watchlist_menu(await cache.watchlists())
            show_status(f"Seznam přejmenován na {name}.")
            return
        copy_from = state["watchlist_id"] if mode == "copy" else None
        list_id = await cache.watchlist_create(name, copy_from=copy_from)
        await cache.set_setting("active_watchlist", str(list_id))
        show_status(f"Seznam {name} vytvořen.")
        await rebuild_watchlist()

    def confirm_watchlist(action: str) -> None:
        # Potvrzení nevratné akce nad aktivním seznamem (vyprázdnění / smazání)
        state["watchlist_confirm"] = action
        name = state["watchlist_name"]
        watchlist_confirm_text.text = (
            f"Odebrat ze seznamu {name} všechny symboly?" if action == "clear" else f"Smazat seznam {name}?"
        )
        watchlist_confirm_dialog.open()

    async def submit_watchlist_confirm() -> None:
        action = state.get("watchlist_confirm")
        watchlist_confirm_dialog.close()
        name = state["watchlist_name"]
        if action == "clear":
            await cache.watchlist_clear(state["watchlist_id"])
            show_status(f"Seznam {name} vyprázdněn.")
        elif action == "delete":
            await cache.watchlist_delete(state["watchlist_id"])
            show_status(f"Seznam {name} smazán.")
        await rebuild_watchlist()

    async def rebuild_watchlist() -> None:
        # Přestaví nabídku seznamů a řádky aktivního seznamu podle databáze
        lists = await cache.watchlists()
        active = await select_active_watchlist(lists)
        state["watchlist_id"] = active["id"]
        state["watchlist_name"] = active["name"]
        render_watchlist_menu(lists)
        watch_rows.clear()
        watchlist_body.clear()
        # Symboly v pořadí přidání si pamatujeme pro přeřazení bez načítání (záhlaví Symbol)
        state["watchlist_items"] = await cache.watchlist_list(active["id"])
        paint_watchlist_sort()
        with watchlist_body:
            for item in sort_watchlist(state["watchlist_items"], state["watchlist_sort"]):
                key = f"{item['source']}:{item['symbol']}"
                code = provider_code(item["source"])
                # Klik kamkoli do řádku (symbol, cena, změna) přepne graf
                with ui.row().classes("watch-row w-full").on("click", make_watch_click(key)) as watch_row_element:
                    ui.label(code).classes("watch-code").tooltip(item["source"])
                    ui.label(item["symbol"]).classes("watch-symbol")
                    price_label = ui.label("—").classes("watch-price")
                    change_label = ui.label("—").classes("watch-change")
                    # Odebrání nesmí zároveň přepnout graf (.stop zastaví klik řádku)
                    ui.button(icon="close").props("flat dense size=sm").classes("watch-remove").on(
                        "click.stop", make_watch_remove(key)
                    )
                watch_rows[key] = {"row": watch_row_element, "price": price_label, "change": change_label}
        refresh_watchlist_active()
        await refresh_watchlist_prices()

    async def toggle_watchlist_sort() -> None:
        # Klik na záhlaví Symbol (jako v TradingView): A→Z, Z→A, pořadí přidání;
        # řazení platí pro všechny seznamy a přetrvá restart. Existující řádky
        # se jen přeskládají (ceny zůstanou, nic se nenačítá znovu)
        state["watchlist_sort"] = WATCHLIST_SORT_NEXT[state["watchlist_sort"]]
        paint_watchlist_sort()
        for index, item in enumerate(sort_watchlist(state["watchlist_items"], state["watchlist_sort"])):
            row = watch_rows.get(f"{item['source']}:{item['symbol']}")
            if row:
                row["row"].move(target_index=index)
        await cache.set_setting("watchlist_sort", state["watchlist_sort"])

    def paint_watchlist_sort() -> None:
        # Ikona a zvýraznění záhlaví podle stavu řazení
        sort = state["watchlist_sort"]
        watchlist_sort_icon.name = WATCHLIST_SORT_ICONS[sort]
        watchlist_sort_button.classes(add="active" if sort else "", remove="" if sort else "active")

    def refresh_watchlist_active() -> None:
        # Zvýrazní řádek tickeru aktivního grafu (jako v TradingView)
        current = active().key
        for key, row in watch_rows.items():
            on = key == current
            row["row"].classes(add="watch-row-active" if on else "", remove="" if on else "watch-row-active")

    async def on_pane_ready(pane: ChartPane) -> None:
        # Graf je inicializovaný v prohlížeči: první graf obnoví i společné
        # prvky stránky (lišta, nabídky, oblíbené), každý graf dostane
        # společné nastavení kreslení a načte svůj symbol
        if not state.get("page_ready"):
            state["page_ready"] = True
            await set_drawing_mode("cursor")
            paint_action("magnet", state["magnet"] != "off")
            await restore_favorites()
            mark_active(timezone_items, state["timezone"])
            build_chart_type_menu()
        await apply_pane_settings(pane)
        # Uložený symbol grafu: rozložení se kvůli tomu znovu neukládá a čáry
        # alertů dostane jen tento graf
        if not pane.set_symbol(pane.initial_key):
            return
        if pane is active():
            refresh_active_ui()
        pane.push_alert_lines(await cache.alerts(active_only=True))
        push_overview([pane])
        await pane.reload_chart()

    # ---------- Settings: Telegram, Interactive Brokers, Alpaca (config.yaml) ----------

    def ibkr_form_values() -> dict | None:
        # Připojovací údaje IBKR z dialogu (prázdné pole = výchozí); neplatné číslo ohlásí a vrátí None
        defaults = connection_settings({})
        try:
            port = Param.int("port", defaults["port"], "IBKR port", min=1, max=65535).normalize(settings_ibkr_port.value)
            client_id = Param.int("client_id", defaults["client_id"], "IBKR client ID", min=0,
                                  max=2**31 - 1).normalize(settings_ibkr_client.value)
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return None
        return {"host": (settings_ibkr_host.value or "").strip() or IBKR_DEFAULT_HOST, "port": port,
                "client_id": client_id}

    async def test_ibkr() -> None:
        # Zkušební připojení k TWS / IB Gateway s hodnotami z dialogu (i neuloženými)
        values = ibkr_form_values()
        if values is None:
            return
        try:
            message = await IbkrProvider.test_connection(values["host"], values["port"], values["client_id"])
        except Exception as exc:
            ui.notify(f"IBKR: připojení k {values['host']}:{values['port']} selhalo – {exc!r}", type="negative",
                      multi_line=True)
            return
        ui.notify(message, type="positive")

    async def save_settings() -> None:
        # Uloží nastavení do config.yaml v kořenu projektu (necommituje se); zbytek
        # souboru včetně komentářů zůstane beze změny. Zapisují se jen změněné
        # sekce (Telegram a IBKR platí hned, Alpaca po restartu)
        config = load_config()
        ibkr = ibkr_form_values()
        if ibkr is None:
            return
        messages = []
        telegram = {
            "bot_token": (settings_telegram_token.value or "").strip(),
            "chat_id": (settings_telegram_chat.value or "").strip(),
        }
        if telegram != dict(zip(("bot_token", "chat_id"), telegram_settings(config))):
            update_config_section("telegram", telegram)
            messages.append("Telegram uložen")
        if ibkr != connection_settings(config):
            update_config_section("ibkr", ibkr)
            messages.append("IBKR uloženo a přepojeno")
        alpaca_config = config.get("alpaca") or {}
        alpaca = {
            "endpoint": (settings_alpaca_endpoint.value or "").strip() or ALPACA_DEFAULT_ENDPOINT,
            "api_key": (settings_key.value or "").strip(),
            "secret": (settings_secret.value or "").strip(),
        }
        saved_alpaca = {key: str(alpaca_config.get(key) or "") for key in alpaca}
        saved_alpaca["endpoint"] = saved_alpaca["endpoint"] or ALPACA_DEFAULT_ENDPOINT
        if alpaca != saved_alpaca:
            update_config_section("alpaca", alpaca)
            messages.append("Alpaca uložena – změna se projeví po restartu aplikace")
        if settings_replay_rewind.value != replay_rewind_mode():
            update_config_section("replay", {"rewind": settings_replay_rewind.value})
            messages.append("Replay uložen")
        if messages:
            # Zdroje si změněnou konfiguraci načtou samy (IBKR se přepojí bez restartu)
            for provider in PROVIDERS:
                provider.reload_config()
        settings_dialog.close()
        show_status((" · ".join(messages) + " (config.yaml).") if messages else "Nastavení beze změny.")

    def open_settings_dialog() -> None:
        # Vyplníme aktuální hodnoty z konfigurace pro kontrolu/přepis
        config = load_config()
        ibkr = connection_settings(config)
        settings_ibkr_host.value = ibkr["host"]
        settings_ibkr_port.value = ibkr["port"]
        settings_ibkr_client.value = ibkr["client_id"]
        alpaca_config = config.get("alpaca") or {}
        settings_alpaca_endpoint.value = alpaca_config.get("endpoint") or ALPACA_DEFAULT_ENDPOINT
        settings_key.value = alpaca_config.get("api_key", "")
        settings_secret.value = alpaca_config.get("secret", "")
        settings_telegram_token.value, settings_telegram_chat.value = telegram_settings(config)
        settings_replay_rewind.value = replay_rewind_mode()
        settings_dialog.open()

    async def detect_telegram_chat() -> None:
        # Chat id z poslední zprávy botovi (uživatel mu musí nejdřív napsat)
        token = (settings_telegram_token.value or "").strip()
        if not token:
            ui.notify("Nejdřív vložte token bota od @BotFather.", type="warning")
            return
        try:
            async with httpx.AsyncClient() as client:
                chat_id = await find_telegram_chat_id(client, token)
        except Exception as exc:
            ui.notify(f"Telegram: {exc}", type="negative")
            return
        if not chat_id:
            ui.notify("Bot zatím nemá žádnou zprávu – napište mu (např. /start) a zkuste to znovu.", type="warning")
            return
        settings_telegram_chat.value = chat_id
        ui.notify(f"Chat ID {chat_id} doplněno – nezapomeňte uložit.", type="positive")

    async def test_telegram() -> None:
        # Testovací zpráva s hodnotami z dialogu (i neuloženými)
        token = (settings_telegram_token.value or "").strip()
        chat_id = (settings_telegram_chat.value or "").strip()
        if not token or not chat_id:
            ui.notify("Vyplňte token bota i chat ID.", type="warning")
            return
        try:
            async with httpx.AsyncClient() as client:
                await send_telegram(client, token, chat_id, "✅ TickView: testovací zpráva alertů.")
        except Exception as exc:
            ui.notify(f"Odeslání selhalo: {exc}", type="negative")
            return
        ui.notify("Testovací zpráva odeslána.", type="positive")

    # ---------- indikátory ----------

    def push_overview(targets: list[ChartPane] | None = None) -> None:
        """Tabulka scanneru do grafů s viditelným indikátorem se vstupy scanneru.

        Tabulku grafu určuje scanner podle uloženého rozložení (pořadí grafů).
        """
        for pane in targets if targets is not None else panes:
            # Graf v replayi ukazuje minulost – živá tabulka scanneru do něj nepatří
            pane.chart.set_overview(None if pane.replay_shown else scanner.table(panes.index(pane)))

    async def indicators_changed() -> None:
        # Po každé změně sady indikátorů aktivního grafu: uložit (konfigurace
        # rozložení), přepočítat, obnovit nabídku
        await save_layout()
        # Indikátor může potřebovat delší historii, než graf má (donačte ji a přepočítá)
        await active().ensure_history()
        await active().push_indicators()
        push_overview([active()])
        # Nabídku přestavujeme jen otevřenou (jinak se postaví při otevření)
        if indicator_dialog.value:
            render_indicator_menu()

    def find_indicator(indicator_id) -> dict | None:
        return next((spec for spec in active().indicators if spec["id"] == indicator_id), None)

    async def add_indicator(kind: str) -> None:
        # Klik v nabídce přidá indikátor s výchozími parametry (jako v TradingView;
        # nabídka zůstává otevřená, parametry se mění ozubeným kolečkem v legendě)
        # Uživatelská výchozí šablona typu („Save as default“) má přednost
        # před továrními parametry; neplatná šablona se ignoruje
        default = await load_default_template(cache, f"indicator.{kind}") or {}
        try:
            spec = new_indicator(kind, default.get("params") if isinstance(default.get("params"), dict) else None)
        except ValueError:
            spec = new_indicator(kind)
        if color := normalize_color(default.get("color")):
            spec["color"] = color
        active().indicators.append(spec)
        await indicators_changed()
        show_status(f"Přidán indikátor {indicator_title(spec)} – nastavení přes ozubené kolečko v legendě grafu.")

    async def remove_indicator(indicator_id) -> None:
        spec = find_indicator(indicator_id)
        if not spec:
            return
        active().indicators = [item for item in active().indicators if item["id"] != indicator_id]
        await indicators_changed()
        show_status(f"Indikátor {indicator_title(spec)} odebrán.")

    async def toggle_indicator(indicator_id) -> None:
        # Oko v legendě: skrytí/zobrazení čar indikátoru (zůstává v legendě)
        spec = find_indicator(indicator_id)
        if not spec:
            return
        spec["visible"] = not spec.get("visible", True)
        await indicators_changed()

    async def move_indicator_pane(indicator_id, before_id) -> None:
        # Přetažení panelu indikátoru za titulek: nové pořadí instancí = nové
        # pořadí panelů (uloží se s rozložením, graf panely přestaví)
        reordered = move_indicator(active().indicators, indicator_id, before_id)
        if reordered is active().indicators:
            return
        active().indicators = reordered
        await indicators_changed()

    async def on_indicator_event(payload: dict) -> None:
        # Tlačítka řádku indikátoru v legendě grafu (oko, nastavení, odebrání)
        action = payload.get("action")
        indicator_id = payload.get("id")
        if action == "toggle":
            await toggle_indicator(indicator_id)
        elif action == "remove":
            await remove_indicator(indicator_id)
        elif action == "settings":
            await open_indicator_settings(indicator_id)
        elif action == "alert":
            await open_alert_dialog(indicator_id=indicator_id)
        elif action == "move":
            await move_indicator_pane(indicator_id, payload.get("before"))

    # Stav nabídky indikátorů: zvolená kategorie a hledaný text
    menu_state = {"category": "all", "query": ""}

    def make_category_click(category: str):
        def _handler(_event=None):
            menu_state["category"] = category
            render_indicator_menu()

        return _handler

    def make_add_click(kind: str):
        async def _handler(_event=None):
            await add_indicator(kind)

        return _handler

    def make_instance_action(action: str, indicator_id: str):
        async def _handler(_event=None):
            await on_indicator_event({"action": action, "id": indicator_id})

        return _handler

    def indicator_matches(indicator, query: str) -> bool:
        # Hledání v nabídce: zkratka, celý název i popis (bez ohledu na velikost)
        haystack = " ".join((indicator.id, indicator.title, indicator.name, indicator.description)).lower()
        return all(word in haystack for word in query.lower().split())

    def render_indicator_menu() -> None:
        # Přestaví levý sloupec kategorií a seznam indikátorů podle hledání
        category = menu_state["category"]
        query = menu_state["query"].strip()
        counts: dict[str, int] = {}
        for spec in active().indicators:
            counts[spec["kind"]] = counts.get(spec["kind"], 0) + 1

        indicator_categories.clear()
        with indicator_categories:
            indicators = all_indicators()
            favorite_count = sum(1 for kind in state["favorite_indicators"] if kind in indicators)
            entries = [
                ("all", "Všechny indikátory"),
                ("favorites", f"Oblíbené ({favorite_count})"),
                ("active", f"Aktivní ({len(active().indicators)})"),
            ]
            entries += list(CATEGORIES.items())
            for key, label in entries:
                ui.label(label).classes("ind-cat" + (" active" if key == category and not query else "")).on(
                    "click", make_category_click(key)
                )

        indicator_list.clear()
        with indicator_list:
            if category == "active" and not query:
                # Aktivní instance s nastavením / skrytím / odebráním
                if not active().indicators:
                    ui.label("Žádný aktivní indikátor – vyberte ze seznamu.").classes("ind-empty")
                for spec in active().indicators:
                    indicator = get_indicator(spec["kind"])
                    with ui.row().classes("ind-item ind-item-active w-full"):
                        with ui.column().classes("ind-item-text"):
                            ui.label(indicator_title(spec)).classes("ind-item-title")
                            ui.label(indicator.name if indicator else "Indikátor se nenačetl").classes("ind-item-desc")
                        ui.space()
                        visible = spec.get("visible", True)
                        ui.button(
                            icon="visibility" if visible else "visibility_off",
                            on_click=make_instance_action("toggle", spec["id"]),
                        ).props("flat dense round size=sm").tooltip("Skrýt / zobrazit")
                        ui.button(icon="settings", on_click=make_instance_action("settings", spec["id"])).props(
                            "flat dense round size=sm"
                        ).tooltip("Nastavení")
                        ui.button(icon="close", on_click=make_instance_action("remove", spec["id"])).props(
                            "flat dense round size=sm"
                        ).tooltip("Odebrat")
                return
            # Seznam indikátorů registru: při hledání se prohledávají všechny,
            # jinak filtr kategorie, nebo oblíbené
            favorites = state["favorite_indicators"]
            show_favorites = not query and category == "favorites"
            kinds = [
                kind
                for kind, indicator in indicators.items()
                if (
                    indicator_matches(indicator, query)
                    if query
                    else category in ("all", indicator.category) or (show_favorites and kind in favorites)
                )
            ]
            kinds.sort(key=lambda kind: indicators[kind].name.lower())
            if not kinds:
                ui.label(
                    "Žádný oblíbený indikátor – označte ho hvězdičkou vpravo." if show_favorites else "Nic nenalezeno."
                ).classes("ind-empty")
            # Vlastní indikátory, které se nenačetly (chyba v souboru), ukážeme v kategorii Vlastní
            if category == "custom" and not query:
                for error in load_errors().values():
                    ui.label(error).classes("ind-empty ind-error")
            for kind in kinds:
                indicator = indicators[kind]
                with ui.row().classes("ind-item w-full").on("click", make_add_click(kind)):
                    with ui.column().classes("ind-item-text"):
                        with ui.row().classes("ind-item-head"):
                            ui.label(indicator.name).classes("ind-item-title")
                            ui.label(indicator.title).classes("ind-item-abbr")
                        ui.label(indicator.description).classes("ind-item-desc")
                    ui.space()
                    ui.label(CATEGORIES[indicator.category]).classes("ind-item-cat")
                    if counts.get(kind):
                        ui.badge(str(counts[kind])).classes("ind-item-count").tooltip("Počet aktivních instancí")
                    # Hvězdička oblíbeného (.stop – kliknutí na ni indikátor do grafu nepřidá)
                    favorite_star("favorite_indicators", kind, None, render_indicator_menu)

    def on_indicator_search(event) -> None:
        menu_state["query"] = event.value or ""
        render_indicator_menu()

    def open_indicator_dialog() -> None:
        # Nabídka indikátorů (TradingView styl): hledání, kategorie, seznam
        menu_state["query"] = ""
        indicator_search.value = ""
        render_indicator_menu()
        indicator_dialog.open()

    # Upravovaná instance indikátoru (pole parametrů drží settings_form): id,
    # barva instance (výchozí barva výstupů bez vlastní barvy) a výstupy indikátoru
    settings_state: dict = {"id": None, "color": None, "lines": []}

    def fill_indicator_settings(kind: str, params: dict, options: dict | None = None) -> None:
        """Vyplní záložky Inputs / Style / Visibility dialogu nastavení indikátoru.

        Záložky Style a Visibility má každý indikátor (jako v TradingView).
        Style ukazuje výstupy (zobrazení, barva, tloušťka a styl čáry, druh
        vykreslení, cenová linka) a Output values jen u indikátorů s výstupy,
        grafické objekty Lines (úsečky) / Tables (tabulka scanneru) jen
        u indikátorů, které je mají; Inputs in status line (hodnoty vstupů
        v legendě) vždy.

        Parametry:
            kind: id indikátoru.
            params: hodnoty parametrů (chybějící = výchozí).
            options: instance nebo šablona s klíči "style"? (Style, chybějící =
                výchozí vzhled) a "visibility"? (chybějící = všude).
        """
        indicator = get_indicator(kind)
        settings_form.fill(indicator, params)
        options = options or {}
        style = normalize_indicator_style(options.get("style"))
        for key, check in settings_style_checks.items():
            check.value = style[key]
        # Výchozí vzhled výstupů podle deklarace indikátoru a barvy instance
        plot_fields.fill(
            [(line.name, plot_defaults(line, settings_state["color"])) for line in settings_state["lines"]], style
        )
        settings_visibility.fill(options.get("visibility"))
        has_lines = bool(indicator and indicator.has_lines)
        has_tables = bool(indicator and (indicator.has_scanner or indicator.has_tables))
        settings_style_checks["lines"].set_visibility(has_lines)
        settings_style_checks["tables"].set_visibility(has_tables)
        settings_objects.set_visibility(has_lines or has_tables)

    def indicator_scope() -> str:
        # Rozsah šablon upravovaného indikátoru: šablony platí pro typ indikátoru
        spec = find_indicator(settings_state["id"])
        return f"indicator.{spec['kind'] if spec else ''}"

    async def open_indicator_settings(indicator_id) -> None:
        spec = find_indicator(indicator_id)
        if not spec:
            return
        indicator = get_indicator(spec["kind"])
        if indicator is None:
            show_status(f"Indikátor {spec['kind']} se nenačetl – zkontrolujte jeho soubor.", error=True)
            return
        settings_state["id"] = indicator_id
        settings_state["color"] = indicator_color(spec, active().indicators.index(spec))
        # Výstupy indikátoru pro záložku Style (výpočet nad umělými svíčkami mimo smyčku událostí)
        settings_state["lines"] = await asyncio.to_thread(sample_lines, indicator, spec["params"])
        # Titulek jako v TradingView: název, zkratka jen když se liší
        settings_title.text = indicator.name if indicator.title == indicator.name else f"{indicator.name} ({indicator.title})"
        settings_ind_tabs.value = inputs_tab
        fill_indicator_settings(spec["kind"], spec["params"], spec)
        await indicator_templates.refresh()
        indicator_settings_dialog.open()

    def apply_indicator_template(values: dict) -> None:
        # Promítne šablonu (parametry, barva, styl, viditelnost) do dialogu; chybějící
        # parametry doplní výchozími, bez barvy se použije barva z palety
        spec = find_indicator(settings_state["id"])
        if not spec:
            return
        params = values.get("params") if isinstance(values.get("params"), dict) else {}
        palette_color = indicator_color({}, active().indicators.index(spec))
        settings_state["color"] = normalize_color(values.get("color")) or palette_color
        fill_indicator_settings(spec["kind"], params, values)

    def reset_indicator_settings() -> None:
        # „Apply defaults“ bez uložené výchozí šablony: tovární parametry a barva
        apply_indicator_template({})

    def collect_indicator_settings() -> dict | None:
        # Parametry a barva z dialogu; neplatný parametr ohlásí a vrátí None
        spec = find_indicator(settings_state["id"])
        if not spec:
            return None
        try:
            params = normalize_params(spec["kind"], settings_form.values())
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return None
        # Styl a viditelnost jen se změnou proti výchozímu stavu (jako v instanci)
        style = {key: check.value for key, check in settings_style_checks.items()} | plot_fields.values()
        values: dict = {"params": params, **instance_options({"style": style, "visibility": settings_visibility.values()})}
        if color := normalize_color(settings_state["color"]):
            values["color"] = color
        return values

    async def submit_indicator_settings() -> None:
        # Ověří parametry; při neplatné hodnotě dialog zůstane otevřený
        spec = find_indicator(settings_state["id"])
        if not spec:
            indicator_settings_dialog.close()
            return
        values = collect_indicator_settings()
        if values is None:
            return
        spec["params"] = values["params"]
        # Styl a viditelnost: v hodnotách jsou jen změněné, výchozí se z instance odeberou
        for key in ("style", "visibility"):
            spec.pop(key, None)
            if key in values:
                spec[key] = values[key]
        # Vlastní barva jen v platném tvaru, jinak zůstane barva z palety
        if "color" in values:
            spec["color"] = values["color"]
        indicator_settings_dialog.close()
        await indicators_changed()
        show_status(f"Indikátor {indicator_title(spec)} upraven.")

    # ---------- alerty (jako v TradingView; vyhodnocuje je AlertEngine na serveru) ----------

    # Stav dialogu alertu: upravovaný alert (id None = nový), jeho symbol a
    # timeframe, zda je zpráva automatická (mění se s podmínkou) a potlačení
    # událostí při hromadném vyplňování polí
    alert_form: dict = {
        "id": None,
        "source": None,
        "symbol": None,
        "timeframe": None,
        "auto_message": True,
        "filling": False,
        # Druhy čar nabízených jako cíl alertu: id kresby -> druh
        "drawing_kinds": {},
        # Indikátory se signály nabízené jako zdroj podmínky: "ind:…" -> {"kind", "params"}
        "indicator_targets": {},
    }

    def format_local_time(ms: int, with_date: bool = True) -> str:
        # Čas v místním pásmu serveru (stejné jako prohlížeč u desktopové aplikace)
        moment = datetime.fromtimestamp(ms / 1000)
        return moment.strftime("%d.%m.%Y %H:%M") if with_date else moment.strftime("%d.%m. %H:%M:%S")

    def alert_on_indicator() -> bool:
        return (alert_source.value or "price") != "price"

    def set_select_options(field, options: dict) -> None:
        # Nové nabídky výběru; hodnota zůstane, pokud v nich je, jinak první nabídka
        field.set_options(options, value=field.value if field.value in options else next(iter(options)))

    def apply_alert_source() -> None:
        # Zdroj podmínky: cena / kresba, nebo indikátor – jiné podmínky (indikátor
        # se scannerem nabízí i alert() scanneru) a pole (u indikátoru bez ceny a cíle)
        on_indicator = alert_on_indicator()
        if on_indicator:
            kind = alert_form["indicator_targets"][alert_source.value]["kind"]
            set_select_options(alert_condition, indicator_conditions(kind))
        else:
            set_select_options(alert_condition, CONDITIONS)
        alert_target.set_visibility(not on_indicator)
        apply_alert_condition()

    def apply_alert_condition() -> None:
        # Spouštění podle podmínky: alert() scanneru má jediné (určují ho vstupy indikátoru)
        set_select_options(alert_trigger, indicator_triggers(alert_condition.value) if alert_on_indicator() else TRIGGERS)
        apply_alert_trigger()

    def apply_alert_trigger() -> None:
        # Předstih v minutách jen u alertu na indikátor se spouštěním před zavřením svíčky
        alert_minutes.set_visibility(alert_on_indicator() and alert_trigger.value == "before_close")

    def alert_target_from_form() -> dict:
        # Cíl alertu z dialogu: signál indikátoru, cena, nebo kresba vybraná v seznamu cílů
        if alert_on_indicator():
            target = alert_form["indicator_targets"][alert_source.value]
            return {"type": "indicator", **target, "minutes": alert_minutes.value}
        choice = alert_target.value or "value"
        if choice.startswith("drawing:"):
            drawing_id = int(choice.split(":", 1)[1])
            return {"type": "drawing", "id": drawing_id, "kind": alert_form["drawing_kinds"].get(drawing_id)}
        return {"type": "value", "price": alert_price.value}

    def refresh_alert_message() -> None:
        # Automatická zpráva sleduje podmínku a cíl, dokud ji uživatel nepřepíše
        alert_price.set_visibility(not alert_on_indicator() and not (alert_target.value or "").startswith("drawing:"))
        if alert_form["filling"] or not alert_form["auto_message"]:
            return
        target = alert_target_from_form()
        if target["type"] == "value" and alert_price.value is None:
            return
        alert_form["filling"] = True
        try:
            alert_message.value = default_message(
                alert_form["symbol"], alert_form["timeframe"], alert_condition.value, target
            )
        finally:
            alert_form["filling"] = False

    def on_alert_message_change(_event) -> None:
        # Ruční úprava zprávy vypne její automatické přepisování
        if not alert_form["filling"]:
            alert_form["auto_message"] = False

    async def open_alert_dialog(
        alert: dict | None = None,
        price=None,
        drawing_id: int | None = None,
        clone: bool = False,
        indicator_id: str | None = None,
    ) -> None:
        """Dialog vytvoření / úpravy alertu.

        Parametry:
            alert: upravovaný alert (None = nový alert pro aktuální graf).
            price: počáteční cena nového alertu (None = poslední cena grafu).
            drawing_id: kresba, na kterou se nový alert nastaví (pravý klik na čáru).
            clone: alert se nepřepíše, ale vytvoří se z něj nový (Klonovat).
            indicator_id: instance indikátoru grafu, na jejíž signál se nový alert nastaví.
        """
        pane = active()
        if alert is None:
            if not pane.key:
                ui.notify("Nejdřív zvolte symbol.", type="warning")
                return
            if pane.replay_covers():
                # Všechny cesty k novému alertu (lišta, graf, legenda) – graf v replayi ukazuje minulost
                alerts_blocked()
                return
            source, symbol, timeframe = pane.provider_id, pane.symbol, pane.timeframe
        else:
            source, symbol, timeframe = alert["source"], alert["symbol"], alert["timeframe"]
        editing = alert is not None and not clone
        alert_form.update(
            {"id": alert["id"] if editing else None, "source": source, "symbol": symbol, "timeframe": timeframe}
        )
        # Cíle: cena a čáry kreseb symbolu a timeframu alertu
        drawings = [d for d in await cache.load_drawings(source, symbol) if d["kind"] in LINE_KINDS]
        alert_form["drawing_kinds"] = {d["id"]: d["kind"] for d in drawings}
        options = {"value": "Cena (hodnota)"}
        for drawing in drawings:
            label = DRAWING_LABELS.get(drawing["kind"], drawing["kind"])
            if drawing["kind"] in ("hline", "hray") and drawing["points"]:
                label += f" · {format_price(float(drawing['points'][0]['price']))}"
            options[f"drawing:{drawing['id']}"] = f"{label} (#{drawing['id']})"
        alert_target.options = options
        alert_target.update()
        # Zdroje podmínky: cena a indikátory se signály v grafu stejného symbolu
        # a timeframu; upravovaný alert na indikátor nabízí i své uložené nastavení
        sources = {"price": f"{symbol} (cena)"}
        targets: dict = {}
        if pane.provider_id == source and pane.symbol == symbol and pane.timeframe == timeframe:
            for instance in pane.indicators:
                indicator = get_indicator(instance["kind"])
                if indicator and indicator.has_signals:
                    sources[f"ind:{instance['id']}"] = indicator.legend_title(instance["params"])
                    targets[f"ind:{instance['id']}"] = {"kind": instance["kind"], "params": dict(instance["params"])}
        saved_target = alert["target"] if alert and alert["target"]["type"] == "indicator" else None
        if saved_target:
            sources["ind:saved"] = f"{target_label(saved_target)} (nastavení alertu)"
            targets["ind:saved"] = {"kind": saved_target["kind"], "params": saved_target["params"]}
        alert_form["indicator_targets"] = targets
        alert_source.options = sources
        alert_source.update()
        candles = pane.candles or []
        has_telegram = telegram_configured()
        if alert is None:
            last_price = candles[-1].close if candles and source == pane.provider_id else None
            spec = {
                "condition": "crossing",
                "target": {"type": "drawing", "id": drawing_id} if drawing_id else {"type": "value", "price": price if price is not None else last_price},
                "trigger": "once",
                "expires_at": utc_now_ms() + DEFAULT_EXPIRATION_MS,
                "message": "",
                "notify": dict(DEFAULT_NOTIFY, telegram=has_telegram),
            }
        else:
            spec = dict(alert)
            # Klon prošlého alertu dostane novou výchozí expiraci
            if clone and spec.get("expires_at") and spec["expires_at"] <= utc_now_ms():
                spec["expires_at"] = utc_now_ms() + DEFAULT_EXPIRATION_MS
        alert_dialog_title.text = f"{'Upravit' if editing else 'Create'} alert on {symbol}, {timeframe}"
        alert_submit.text = "Save" if editing else "Create"
        alert_form["filling"] = True
        try:
            if saved_target:
                alert_source.value = "ind:saved"
            elif alert is None and indicator_id and f"ind:{indicator_id}" in sources:
                alert_source.value = f"ind:{indicator_id}"
                spec.update({"condition": "signal", "trigger": "before_close"})
            else:
                alert_source.value = "price"
            apply_alert_source()
            alert_minutes.value = saved_target["minutes"] if saved_target else DEFAULT_ALERT_MINUTES
            alert_condition.value = spec["condition"]
            apply_alert_condition()
            target = spec["target"]
            if target["type"] == "drawing" and f"drawing:{target['id']}" in options:
                alert_target.value = f"drawing:{target['id']}"
            else:
                alert_target.value = "value"
            alert_price.value = target.get("price") if target["type"] == "value" else (candles[-1].close if candles else None)
            alert_trigger.value = spec["trigger"]
            apply_alert_trigger()
            expires_at = spec.get("expires_at")
            alert_open_ended.value = expires_at is None
            alert_expiration.value = datetime.fromtimestamp(
                (expires_at or utc_now_ms() + DEFAULT_EXPIRATION_MS) / 1000
            ).strftime("%Y-%m-%dT%H:%M")
            notify = spec.get("notify") or DEFAULT_NOTIFY
            alert_notify_telegram.value = bool(notify.get("telegram"))
            alert_notify_toast.value = bool(notify.get("toast"))
            alert_notify_sound.value = bool(notify.get("sound"))
            alert_notify_webhook.value = bool(notify.get("webhook"))
            alert_webhook_url.value = notify.get("webhook") or ""
            alert_message.value = spec.get("message") or ""
        finally:
            alert_form["filling"] = False
        # Nový alert má automatickou zprávu, upravovaný si drží uloženou
        alert_form["auto_message"] = alert is None
        refresh_alert_message()
        alert_telegram_note.set_visibility(not has_telegram)
        alert_dialog.open()

    async def submit_alert() -> None:
        # Kontrola a uložení alertu; engine ho hned začne vyhodnocovat
        expires_at = None
        if not alert_open_ended.value:
            try:
                expires_at = int(datetime.fromisoformat(alert_expiration.value).timestamp() * 1000)
            except (TypeError, ValueError):
                ui.notify("Zadejte platné datum a čas expirace.", type="warning")
                return
            if expires_at <= utc_now_ms():
                ui.notify("Expirace musí být v budoucnosti.", type="warning")
                return
        data = {
            "source": alert_form["source"],
            "symbol": alert_form["symbol"],
            "timeframe": alert_form["timeframe"],
            "condition": alert_condition.value,
            "target": alert_target_from_form(),
            "trigger": alert_trigger.value,
            "expires_at": expires_at,
            "message": alert_message.value,
            "notify": {
                "telegram": alert_notify_telegram.value,
                "toast": alert_notify_toast.value,
                "sound": alert_notify_sound.value,
                "webhook": alert_webhook_url.value if alert_notify_webhook.value else "",
            },
        }
        try:
            spec = normalize_alert(data)
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return
        if alert_notify_webhook.value and not spec["notify"]["webhook"]:
            ui.notify("Zadejte Webhook URL, nebo webhook vypněte.", type="warning")
            return
        alert_dialog.close()
        await cache.save_alert(spec, alert_form["id"])
        await alert_engine.reload()
        show_status(f"Alert {'upraven' if alert_form['id'] else 'vytvořen'}: {spec['message']}")

    async def set_alert_active(alert: dict, active: bool) -> None:
        # Pozastavení / obnovení alertu (obnovený začne vyhodnocovat od další ceny)
        await cache.set_alert_state(alert["id"], active, "active" if active else "paused")
        await alert_engine.reload()

    def show_alert_popup(message: str, toast: bool, sound: bool, icon: str = "⏰") -> None:
        """Toast a zvuk spuštěného (nebo testovaného) alertu v této stránce.

        Parametry:
            message: hotová zpráva alertu.
            toast, sound: zda ukázat toast / přehrát zvuk (nastavení alertu).
            icon: značka před zprávou (⏰ spuštění, 🧪 test).
        """
        # Volá se i z úlohy na pozadí nebo po zániku nabídky – UI do kořene stránky
        with page_root:
            if toast:
                ui.notify(
                    f"{icon} {message}",
                    type="warning",
                    position="top-right",
                    close_button="OK",
                    timeout=20000,
                    multi_line=True,
                )
            if sound and panes:
                active().chart.play_sound()

    async def test_alert(alert: dict) -> None:
        """Test alertu z nabídky: toast a zvuk hned, pak odeslání Telegramu a webhooku.

        Stav alertu ani log se nemění; výsledek odeslání ohlásí notifikace.

        Parametry:
            alert: testovaný alert.
        """
        # Cena pro {{close}}: poslední svíčka grafu, který symbol alertu zobrazuje
        price = None
        for pane in panes:
            if pane.candles and (pane.provider_id, pane.symbol) == (alert["source"], alert["symbol"]):
                price = pane.candles[-1].close
                break
        message = alert_engine.render_test_message(alert, price)
        show_alert_popup(message, alert["notify"].get("toast"), alert["notify"].get("sound"), icon="🧪")
        delivery = await alert_engine.send_test(alert, message, price)
        # Souhrn odeslání ve formátu logu (✓ / ✗ kanál, u chyby její text);
        # bez Telegramu i webhooku jen upozornění, že není co posílat ven
        with page_root:
            if not delivery:
                ui.notify("Test alertu: Telegram ani webhook nejsou zapnuté.", type="info")
                return
            all_ok = all(item["ok"] for item in delivery)
            parts = [
                f"✓ {item['channel']}" if item["ok"] else f"✗ {item['channel']}: {item['error']}"
                for item in delivery
            ]
            ui.notify(
                "Test alertu: " + ", ".join(parts),
                type="positive" if all_ok else "negative",
                multi_line=True,
                timeout=5000 if all_ok else 15000,
            )

    async def delete_alert(alert: dict) -> None:
        await cache.delete_alert(alert["id"])
        await alert_engine.reload()
        show_status(f"Alert {alert['symbol']} smazán.")

    def alerts_visible() -> bool:
        # Seznam / log alertů je vidět jen při otevřeném panelu Alerty
        return state["right_panel"] == "alerts"

    async def refresh_alerts_ui(with_log: bool = True) -> None:
        """Čáry alertů v grafu a otevřený panel Alerty.

        Parametry:
            with_log: překreslit i log (spuštění alertu ho obnoví samo přes log_changed).
        """
        alerts = await cache.alerts()
        state["alerts"] = alerts
        await push_alert_lines(alerts)
        await render_alerts_panel(with_log)

    async def render_alerts_panel(with_log: bool = True) -> None:
        # Seznam (z naposledy načtených alertů) otevřeného panelu Alerty; log
        # obnoví i při zavřeném panelu aspoň odznaky nepřečtených
        if alerts_visible():
            render_alerts_list()
        if with_log:
            await render_alert_log()

    async def push_alert_lines(alerts: list[dict] | None = None) -> None:
        # Čárkované čáry aktivních cenových alertů symbolu každého grafu (všechny timeframy)
        if alerts is None:
            alerts = await cache.alerts(active_only=True)
        for pane in panes:
            pane.push_alert_lines(alerts)

    def make_alert_action(action: str, alert: dict):
        """Handler akce u alertu (řádek, tlačítka při najetí, nabídka ⋯ a pravý klik).

        Parametry:
            action: edit, clone, pause, resume, test (zkušební odeslání notifikací),
                delete nebo show (přepnout graf na symbol).
            alert: alert, kterého se akce týká.
        """

        async def _handler(_event=None):
            if action == "edit":
                await open_alert_dialog(alert)
            elif action == "clone":
                await open_alert_dialog(alert, clone=True)
            elif action == "pause":
                await set_alert_active(alert, False)
            elif action == "resume":
                await set_alert_active(alert, True)
            elif action == "test":
                await test_alert(alert)
            elif action == "delete":
                await delete_alert(alert)
            elif action == "show":
                await load_symbol(f"{alert['source']}:{alert['symbol']}")

        return _handler

    def alert_menu_items(alert: dict) -> None:
        # Položky nabídky alertu (⋯ i pravý klik) jako v TradingView
        ui.menu_item("Upravit…", on_click=make_alert_action("edit", alert))
        ui.menu_item("Klonovat…", on_click=make_alert_action("clone", alert))
        if alert["active"]:
            ui.menu_item("Pozastavit", on_click=make_alert_action("pause", alert))
        else:
            ui.menu_item("Spustit znovu", on_click=make_alert_action("resume", alert))
        ui.menu_item("Zobrazit v grafu", on_click=make_alert_action("show", alert))
        ui.menu_item("Test alertu", on_click=make_alert_action("test", alert))
        ui.separator()
        ui.menu_item("Smazat", on_click=make_alert_action("delete", alert)).classes("wl-danger")

    def render_alerts_list() -> None:
        """Seznam alertů v pravém panelu ve stylu TradingView.

        Řádek: zpráva, pod ní zdroj, symbol, timeframe a stav; při najetí
        tlačítka spustit / pozastavit, upravit, smazat a nabídka ⋯; pravý klik
        otevře stejnou nabídku, dvojklik alert upraví, klik přepne graf.

        Vychází z naposledy načtených alertů (state["alerts"]), hledání a
        řazení tak nečtou databázi.
        """
        alerts = state["alerts"]
        mark_active(alert_sort_items, state["alerts_sort"])
        shown = filter_alerts(alerts, alert_search.value or "", state["alerts_sort"])
        alerts_list.clear()
        with alerts_list:
            if not alerts:
                ui.label("Žádné alerty – vytvořte je tlačítkem + nebo pravým klikem do grafu.").classes("alert-empty")
            elif not shown:
                ui.label("Hledání neodpovídá žádný alert.").classes("alert-empty")
            for alert in shown:
                status = alert["status"] if alert["status"] in STATUSES else "active"
                code = provider_code(alert["source"])
                with (
                    ui.row()
                    .classes(f"alert-row alert-row-{status} w-full")
                    .on("click", make_alert_action("show", alert))
                    .on("dblclick", make_alert_action("edit", alert))
                ):
                    with ui.column().classes("alert-text"):
                        ui.label(alert["message"]).classes("alert-title").tooltip(describe_alert(alert))
                        with ui.row().classes("alert-meta"):
                            ui.label(code).classes("watch-code").tooltip(alert["source"])
                            ui.label(f"{alert['symbol']}, {alert['timeframe']}").classes("alert-meta-symbol")
                            ui.label("·")
                            ui.label(STATUSES[status]).classes(f"alert-status-text alert-status-{status}")
                        details = []
                        if alert.get("expires_at"):
                            details.append(f"vyprší {format_local_time(alert['expires_at'])}")
                        if alert.get("fire_count"):
                            details.append(f"spuštěno {alert['fire_count']}×")
                        if details:
                            ui.label(" · ".join(details)).classes("alert-msg")
                    # Akce při najetí; nesmí zároveň přepnout graf ani alert otevřít
                    # (kontejner zastaví klik i dvojklik v prohlížeči)
                    with ui.row().classes("alert-actions").on(
                        "click", js_handler="(e) => e.stopPropagation()"
                    ).on("dblclick", js_handler="(e) => e.stopPropagation()"):
                        toggle = (
                            ("pause", "pause", "Pozastavit") if alert["active"] else ("resume", "play_arrow", "Spustit znovu")
                        )
                        for action, icon, tooltip in (toggle, ("edit", "edit", "Upravit"), ("delete", "close", "Smazat")):
                            ui.button(
                                icon=icon, color=None, on_click=make_alert_action(action, alert)
                            ).props("flat dense round size=sm").classes("alert-btn").tooltip(tooltip)
                        with ui.button(icon="more_horiz", color=None).props("flat dense round size=sm").classes(
                            "alert-btn"
                        ):
                            with ui.menu().classes("alerts-menu"):
                                alert_menu_items(alert)
                    # Pravý klik na řádek: stejná nabídka jako ⋯
                    with ui.context_menu().classes("alerts-menu"):
                        alert_menu_items(alert)

    def make_log_entry_seen(entry_id: int):
        # Handler kliku na nepřečtený řádek logu: označí ho jako přečtený;
        # engine pak pošle log_changed a log s odznakem se překreslí
        async def _handler() -> None:
            await alert_engine.mark_log_seen(entry_id)

        return _handler

    def paint_log_badges(unseen_count: int) -> None:
        """Počet nepřečtených záznamů logu v bublině tlačítka Alerty na pravé liště
        i v odznaku u přepínače Log (nula odznaky skryje).

        Parametry:
            unseen_count: počet nepřečtených záznamů logu.
        """
        for badge in (alerts_badge, log_badge):
            badge.text = str(unseen_count)
            badge.set_visibility(unseen_count > 0)

    async def render_alert_log() -> None:
        # Log spuštěných alertů s výsledkem odeslání notifikací; počet
        # nepřečtených záznamů ukazují odznaky, klik na řádek záznam označí
        # jako přečtený. Při zavřeném panelu stačí odznaky (jen počet z cache)
        if not alerts_visible():
            paint_log_badges(await cache.unseen_alert_log_count())
            return
        entries = await cache.alert_log()
        paint_log_badges(sum(1 for entry in entries if not entry["seen"]))
        alert_log_list.clear()
        with alert_log_list:
            if not entries:
                ui.label("Zatím se žádný alert nespustil.").classes("alert-empty")
            for entry in entries:
                unread_class = "" if entry["seen"] else " alert-row-unread"
                with ui.row().classes(f"alert-row w-full{unread_class}") as log_row:
                    # Klik má smysl jen u nepřečteného záznamu
                    if not entry["seen"]:
                        log_row.on("click", make_log_entry_seen(entry["id"]))
                    with ui.column().classes("alert-text"):
                        ui.label(entry["message"]).classes("alert-title")
                        with ui.row().classes("alert-meta"):
                            ui.label(f"{entry['symbol']}, {entry['timeframe']}").classes("alert-meta-symbol")
                            ui.label("·")
                            # Scanner a připomínky (alert_id 0) cenu nemají
                            if entry["alert_id"]:
                                ui.label(format_price(entry["price"]))
                                ui.label("·")
                            ui.label(format_local_time(entry["fired_at"]))
                        if entry["delivery"]:
                            with ui.row().classes("alert-meta"):
                                for result in entry["delivery"]:
                                    chip = ui.label(("✓ " if result["ok"] else "✗ ") + result["channel"]).classes(
                                        "alert-delivery " + ("ok" if result["ok"] else "failed")
                                    )
                                    if result.get("error"):
                                        chip.tooltip(result["error"])

    def set_alerts_tab(tab: str) -> None:
        """Přepne obsah panelu Alerty mezi seznamem alertů a logem spuštění."""
        state["alerts_tab"] = tab
        paint_alerts_tab()

    def paint_alerts_tab() -> None:
        # Zvýraznění přepínače a viditelnost lišt a seznamů podle záložky
        on_alerts = state["alerts_tab"] == "alerts"
        mark_active(alerts_tab_buttons, state["alerts_tab"], "active")
        alerts_toolbar.set_visibility(on_alerts)
        alerts_list.set_visibility(on_alerts)
        alert_search.set_visibility(on_alerts and state["alert_search_open"])
        log_toolbar.set_visibility(not on_alerts)
        alert_log_list.set_visibility(not on_alerts)

    def toggle_alert_search() -> None:
        # Pole hledání pod lištou; zavření hledání zruší i filtr
        state["alert_search_open"] = not state["alert_search_open"]
        if not state["alert_search_open"]:
            alert_search.value = ""
        paint_alerts_tab()
        if state["alert_search_open"]:
            alert_search.run_method("focus")

    def make_alert_sort(sort: str):
        # Volba řazení seznamu alertů (pamatuje se v nastavení)
        async def _handler() -> None:
            state["alerts_sort"] = sort
            render_alerts_list()
            await cache.set_setting("alerts_sort", sort)

        return _handler

    # Potvrzovací texty hromadných akcí nabídky ⋯ (mazání se nejdřív potvrdí)
    BULK_ALERT_CONFIRMS = {
        "remove_inactive": "Smazat všechny neaktivní alerty",
        "remove_all": "Smazat všechny alerty",
    }

    def make_bulk_alerts(action: str):
        # Spuštění / pozastavení proběhne hned, mazání až po potvrzení
        async def _handler() -> None:
            confirm_text = BULK_ALERT_CONFIRMS.get(action)
            if confirm_text:
                state["alerts_bulk"] = action
                alerts_confirm_text.text = f"{confirm_text}? Akci nelze vrátit."
                alerts_confirm_dialog.open()
            else:
                await run_bulk_alerts(action)

        return _handler

    async def run_bulk_alerts(action: str) -> None:
        """Provede hromadnou akci nad alerty (cache.bulk_alerts) a přenačte engine."""
        count = await cache.bulk_alerts(action)
        await alert_engine.reload()
        show_status(f"Hromadná akce: {plural(count, 'alert', 'alerty', 'alertů')}.")

    async def submit_alerts_confirm() -> None:
        alerts_confirm_dialog.close()
        await run_bulk_alerts(state.get("alerts_bulk", ""))

    async def show_right_panel(panel: str) -> None:
        """Zobrazí obsah pravého panelu ("" = skrýt), uloží volbu a vykreslí obsah, který se načítá."""
        if state["right_panel"] == panel:
            return
        state["right_panel"] = panel
        await cache.set_setting("right_panel", panel)
        paint_right_panel()
        # Obsahy, které se vykreslují až při zobrazení (seznamy z databáze)
        render = {"alerts": render_alerts_panel, "replay": replay_ui.render_sessions}.get(panel)
        if render:
            await render()

    async def toggle_right_panel(panel: str) -> None:
        """Tlačítko pravé lišty: zobrazí panel, opětovný klik na aktivní ho skryje."""
        await show_right_panel("" if state["right_panel"] == panel else panel)

    def paint_right_panel() -> None:
        # Viditelnost panelu a jeho sekcí, zvýraznění tlačítek pravé lišty
        current = state["right_panel"]
        right_panel.set_visibility(bool(current))
        watchlist_section.set_visibility(current == "watchlist")
        alerts_section.set_visibility(current == "alerts")
        replay_section.set_visibility(current == "replay")
        mark_active(right_buttons, current, "active")
        paint_alerts_tab()

    async def open_alert_for_chart() -> None:
        # Tlačítko Alert v liště / + v panelu: alert na poslední cenu zobrazeného symbolu
        await open_alert_dialog()

    def on_alert_event(event: dict) -> None:
        # Událost enginu alertů (z úlohy na pozadí): toast a zvuk se vytvoří
        # v kontextu stránky, seznam a čáry se obnoví asynchronně
        event_type = event.get("type")
        if event_type == "log_changed":
            # Nový / přečtený / vymazaný záznam logu mění jen log a odznaky
            # nepřečtených (seznam ani čáry ne)
            background_tasks.create(render_alert_log())
            return
        if event_type in ("fired", "notification"):
            show_alert_popup(event["message"], event.get("toast"), event.get("sound"))
            # Log obnoví následná událost log_changed; notifikace scanneru
            # a připomínek seznam alertů nemění
            if event_type == "fired":
                background_tasks.create(refresh_alerts_ui(with_log=False))
            return
        background_tasks.create(refresh_alerts_ui())

    # ---------- rozložení více grafů (Layout setup jako v TradingView) ----------

    def make_pane_activate(pane: ChartPane):
        # Klik (pointerdown) do grafu ho udělá aktivním
        async def _handler(_event=None) -> None:
            await activate_pane(pane)

        return _handler

    def create_pane(config: dict) -> ChartPane:
        """Vytvoří graf rozložení v mřížce grafů (načte se po inicializaci v prohlížeči)."""
        with chart_grid:
            pane = ChartPane(page, config, on_pane_ready)
        pane.chart.on("pointerdown", make_pane_activate(pane), [])
        # Definice skupin pro flyout nabídky lišty, SVG ikony a synchronizace
        # jsou v JS společné všem grafům – posílají se jednou za stránku
        # (můžou přijít i před init grafu)
        if not state.get("js_shared_sent"):
            state["js_shared_sent"] = True
            pane.chart.set_icons(ICONS)
            pane.chart.set_alert_kinds(list(LINE_KINDS))
            pane.chart.set_tool_groups(DRAWING_GROUPS)
            pane.chart.set_layout_sync(state["sync"])
        return pane

    async def activate_pane(pane: ChartPane) -> None:
        """Udělá graf aktivním: horní lišta, watchlist a dialogy pak míří na něj."""
        if pane.closed or pane not in panes or pane is active():
            return
        state["active"] = panes.index(pane)
        pane.chart.set_active()
        refresh_active_ui()
        await save_layout()

    def paint_layout() -> None:
        # Třída mřížky podle rozložení a zvýraznění rozložení v nabídce
        chart_grid.classes(replace=f"chart-grid layout-{state['layout']}")
        mark_active(layout_items, state["layout"])
        for key, switch in sync_switches.items():
            switch.value = state["sync"][key]

    async def set_layout(layout_id: str) -> None:
        """Změní rozložení: přebývající grafy odebere, nové převezmou symbol aktivního grafu."""
        if layout_id not in LAYOUTS or layout_id == state["layout"]:
            return
        configs = resize_panes([pane.config() for pane in panes], layout_id, state["active"])
        count = len(configs)
        for pane in panes[count:]:
            pane.close()
            pane.chart.destroy()
        del panes[count:]
        if state["active"] >= count:
            state["active"] = 0
        for config in configs[len(panes):]:
            panes.append(create_pane(config))
        state["layout"] = layout_id
        paint_layout()
        active().chart.set_active()
        refresh_active_ui()
        await save_layout()
        show_status(f"Rozložení: {plural(count, 'graf', 'grafy', 'grafů')}.")

    def make_layout_click(layout_id: str):
        # Výběr rozložení zavře nabídku (jako v TradingView) a změní rozložení
        async def _handler() -> None:
            layout_menu.close()
            await set_layout(layout_id)

        return _handler

    async def set_sync(key: str, value: bool) -> None:
        """Zapne / vypne synchronizaci rozložení (symbol, interval, crosshair, čas, rozsah)."""
        value = bool(value)
        if state["sync"].get(key) == value:
            return
        state["sync"][key] = value
        # Crosshair a čas synchronizuje JS (nastavení je společné všem grafům)
        if panes:
            active().chart.set_layout_sync(state["sync"])
        await save_layout()
        # Zapnutí synchronizace symbolu / intervalu hned srovná ostatní grafy
        # podle aktivního grafu (jako v TradingView)
        if value and key == "symbol" and active().key:
            await load_symbol(active().key)
        elif value and key == "interval":
            await apply_timeframe(list(panes), active().timeframe)

    def make_sync_change(key: str):
        async def _handler(event) -> None:
            await set_sync(key, event.value)

        return _handler

    # ---------- šablony indikátorů (sada indikátorů grafu jako v TradingView) ----------

    async def store_indicator_templates(templates: list[dict]) -> None:
        # Uloží šablony a obnoví nabídku i tlačítka oblíbených šablon v liště
        state["indicator_templates"] = templates
        await cache.set_setting(INDICATOR_TEMPLATES_KEY, json.dumps(templates))
        render_indicator_templates()

    def render_indicator_templates() -> None:
        """Nabídka šablon u tlačítka Indikátory a tlačítka oblíbených šablon v liště."""
        templates = state["indicator_templates"]
        # Oblíbené šablony: kulatá tlačítka s iniciálou (jako v TradingView)
        template_quick.clear()
        with template_quick:
            for template in favorite_indicator_templates(templates):
                ui.button(template["name"][:1].upper(), color=None, on_click=make_template_apply(template["name"])).props(
                    "flat dense round no-caps"
                ).classes("tpl-quick-btn").tooltip(f"{template['name']} – {describe_indicator_template(template)}")
        template_menu.clear()
        with template_menu:
            with ui.row().classes("tpl-tabs w-full"):
                ui.button("Indicators", color=None, on_click=open_indicator_dialog).props("flat dense no-caps").classes("tpl-tab")
                ui.button("Templates", color=None).props("flat dense no-caps").classes("tpl-tab active")
            with ui.menu_item(on_click=open_save_template_dialog).classes("tpl-menu-item"):
                ui.icon("cloud_upload").classes("tpl-menu-icon")
                ui.label("Save indicator template…")
            ui.separator()
            recent = recent_indicator_templates(templates)
            if recent:
                ui.label("Recently used").classes("menu-section tpl-section")
            for template in recent:
                with ui.menu_item(on_click=make_template_apply(template["name"])).classes("tpl-menu-item tpl-recent"):
                    with ui.column().classes("tpl-recent-text"):
                        ui.label(template["name"]).classes("tpl-recent-name")
                        ui.label(describe_indicator_template(template)).classes("tpl-recent-desc")
                    ui.space()
                    template_star(template)
            if recent:
                ui.separator()
            with ui.menu_item(on_click=open_templates_dialog).classes("tpl-menu-item"):
                ui.icon("folder_open").classes("tpl-menu-icon")
                ui.label("Open template…")

    def template_star(template: dict) -> None:
        # Hvězdička šablony: oblíbená šablona má tlačítko v horní liště;
        # .stop zabrání současnému použití šablony a zavření nabídky
        on = template["favorite"]
        with ui.element("span").classes("menu-star" + (" on" if on else "")).on(
            "click.stop", make_template_favorite(template["name"])
        ):
            icon_html("star" if on else "star_border")

    def make_template_apply(name: str):
        async def _handler(_event=None) -> None:
            await apply_template_set(name)

        return _handler

    def make_template_favorite(name: str):
        async def _handler(_event=None) -> None:
            template = find_indicator_template(state["indicator_templates"], name)
            if template:
                await store_indicator_templates(
                    update_indicator_template(state["indicator_templates"], name, favorite=not template["favorite"])
                )
                if templates_dialog.value:
                    render_templates_dialog()

        return _handler

    def make_template_delete(name: str):
        async def _handler(_event=None) -> None:
            await store_indicator_templates(delete_indicator_template(state["indicator_templates"], name))
            render_templates_dialog()
            show_status(f"Šablona indikátorů „{name}“ smazána.")

        return _handler

    async def apply_template_set(name: str) -> None:
        """Použije šablonu indikátorů v aktivním grafu (nahradí jeho indikátory).

        Šablona se zapamatovaným symbolem / intervalem graf přepne i na ně.
        """
        template = find_indicator_template(state["indicator_templates"], name)
        if not template:
            return
        templates_dialog.close()
        pane = active()
        pane.indicators = template_indicators(template)
        await store_indicator_templates(update_indicator_template(state["indicator_templates"], name, used_at=utc_now_ms()))
        timeframe_changed = bool(template["timeframe"]) and pane.set_timeframe(template["timeframe"])
        if timeframe_changed:
            refresh_timeframes()
        if template["symbol"] and template["symbol"] != pane.key:
            # Načtení symbolu přepočítá i nové indikátory
            await load_symbol(template["symbol"], pane=pane)
        elif timeframe_changed:
            # Přenačtení grafu na novém timeframu přepočítá i nové indikátory
            await save_layout()
            await pane.reload_chart()
        else:
            await indicators_changed()
        if indicator_dialog.value:
            render_indicator_menu()
        show_status(f"Šablona indikátorů „{name}“: {describe_indicator_template(template)}.")

    def open_save_template_dialog() -> None:
        # Dialog „Save indicator template“: název a zapamatování symbolu / intervalu
        template_name_input.value = ""
        template_remember_symbol.value = False
        template_remember_interval.value = False
        save_template_dialog.open()

    async def submit_save_template() -> None:
        # Uloží aktuální indikátory aktivního grafu jako šablonu (stejný název přepíše)
        name = clean_name(template_name_input.value)
        if not name:
            ui.notify(f"Zadejte název šablony (1–{MAX_NAME_LENGTH} znaků).", type="warning")
            return
        pane = active()
        overwritten = find_indicator_template(state["indicator_templates"], name) is not None
        templates = save_indicator_template(
            state["indicator_templates"],
            name,
            pane.indicators,
            symbol=pane.key if template_remember_symbol.value else None,
            timeframe=pane.timeframe if template_remember_interval.value else None,
            now_ms=utc_now_ms(),
        )
        save_template_dialog.close()
        await store_indicator_templates(templates)
        show_status(f"Šablona indikátorů „{name}“ {'přepsána' if overwritten else 'uložena'} ({plural(len(pane.indicators), 'indikátor', 'indikátory', 'indikátorů')}).")

    def render_templates_dialog() -> None:
        # Seznam všech šablon s hledáním: klik použije, hvězdička, křížek smaže
        query = (templates_search.value or "").strip().lower()
        templates_list.clear()
        with templates_list:
            shown = [t for t in state["indicator_templates"] if not query or query in t["name"].lower()]
            if not shown:
                ui.label("Žádné šablony – uložte je přes „Save indicator template…“.").classes("ind-empty")
            for template in shown:
                with ui.row().classes("ind-item w-full").on("click", make_template_apply(template["name"])):
                    with ui.column().classes("ind-item-text"):
                        ui.label(template["name"]).classes("ind-item-title")
                        ui.label(describe_indicator_template(template)).classes("ind-item-desc")
                    ui.space()
                    template_star(template)
                    ui.button(icon="close", color=None).props("flat dense round size=sm").classes("tpl-delete-btn").tooltip(
                        "Smazat šablonu"
                    ).on("click.stop", make_template_delete(template["name"]))

    def open_templates_dialog() -> None:
        templates_search.value = ""
        render_templates_dialog()
        templates_dialog.open()

    # ---------- UI ----------

    # Dialog časových připomínek (nabídka ⋯ v panelu Alerty)
    reminders_dialog = RemindersDialog(reminders)
    # Dialog O aplikaci (klik na název vlevo nahoře a ikona dole v pravé liště)
    about_dialog = build_about_dialog()
    about_tooltip = "O aplikaci TickView"

    with ui.column().classes("page-root w-full") as page_root:
        with ui.row().classes("topbar w-full"):
            ui.label("TickView").classes("brand").on("click", about_dialog.open).tooltip(about_tooltip)
            # Pilulka s aktuálním symbolem (jako v TradingView): klik otevře hledání
            with (
                ui.button(color=None, on_click=lambda: open_symbol_search())
                .props("flat dense no-caps")
                .classes("symbol-pill")
                .tooltip("Hledat symbol (nebo začněte psát nad grafem)")
            ):
                ui.icon("search").classes("symbol-pill-icon")
                symbol_pill_label = ui.label("—").classes("symbol-pill-text")
            # Položky nabídek timeframů a typů grafu (zvýraznění aktuální přes mark_active)
            timeframe_items: dict = {}
            chart_type_items: dict = {}
            # Timeframy jako v TradingView: oblíbené v liště, šipka otevře všechny s hvězdičkami
            with ui.row().classes("tf-bar tf-bar-timeframes"):
                timeframe_toggle = ui.toggle(options=[], value=None).classes("timeframes")
                with ui.button(icon="expand_more", color=None).props("flat dense").classes("bar-arrow").tooltip(
                    "Všechny timeframy"
                ):
                    timeframe_menu = ui.menu().classes("tf-menu")
            # Typ grafu: tlačítka oblíbených typů a šipka s nabídkou všech typů
            with ui.row().classes("tf-bar"):
                chart_type_bar = ui.row().classes("chart-type-bar")
                with ui.button(icon="expand_more", color=None).props("flat dense").classes("bar-arrow").tooltip(
                    "Typ grafu"
                ):
                    chart_type_menu = ui.menu().classes("chart-type-menu")
            # Indikátory, šablony indikátorů (šipka) a tlačítka oblíbených šablon
            with ui.row().classes("tf-bar"):
                ui.button("Indikátory", icon="add_chart", on_click=open_indicator_dialog, color=None).props(
                    "flat dense no-caps"
                ).classes("topbar-btn indicator-add").tooltip("Indikátory (nabídka s hledáním a kategoriemi)")
                with ui.button(icon="expand_more", color=None).props("flat dense").classes("bar-arrow").tooltip(
                    "Šablony indikátorů"
                ):
                    template_menu = ui.menu().classes("tpl-set-menu")
                template_quick = ui.row().classes("tpl-quick")
            # Alert: vytvoření alertu pro graf (seznam alertů je v pravém panelu)
            ui.button("Alert", icon="alarm_add", color=None, on_click=open_alert_for_chart).props(
                "flat dense no-caps"
            ).classes("topbar-btn").tooltip("Vytvořit alert pro aktuální symbol")
            # Replay: sessions a nová session (backtest ručních obchodů)
            replay_ui.build_topbar_button()
            source_badge = ui.badge("—").classes("source-badge")
            # Layout setup jako v TradingView: počet a uspořádání grafů, synchronizace
            layout_items: dict = {}
            sync_switches: dict = {}
            with ui.button(color=None).props("flat dense").classes("topbar-btn layout-button").tooltip("Layout setup"):
                ui.icon("grid_view")
                with ui.menu().classes("layout-menu") as layout_menu:
                    for count, items in LAYOUT_SECTIONS:
                        with ui.row().classes("layout-menu-row w-full"):
                            ui.label(str(count)).classes("layout-menu-count")
                            for layout_id, layout_label in items:
                                # Piktogram = zmenšená mřížka stejného rozložení (CSS layout-<id>)
                                with ui.element("div").classes("layout-option").on(
                                    "click", make_layout_click(layout_id)
                                ).tooltip(layout_label) as option:
                                    with ui.element("div").classes(f"layout-icon layout-{layout_id}"):
                                        for _ in range(LAYOUTS[layout_id]):
                                            ui.element("div").classes("layout-icon-cell")
                                layout_items[layout_id] = option
                    # Sekce synchronizace: nadpis a řádky s přepínači ve vlastním bloku s odsazením
                    with ui.column().classes("layout-sync w-full"):
                        ui.label("Sync in layout").classes("menu-section layout-sync-title")
                        for sync_key, (sync_label, sync_help) in SYNC_OPTIONS.items():
                            with ui.row().classes("layout-sync-row w-full"):
                                ui.label(sync_label)
                                ui.icon("info_outline").classes("layout-sync-info").tooltip(sync_help)
                                ui.space()
                                sync_switches[sync_key] = ui.switch(
                                    value=state["sync"][sync_key], on_change=make_sync_change(sync_key)
                                ).props("dense size=sm")
            theme_switch = ui.switch("Tmavý motiv", value=dark)
            ui.button(icon="settings", on_click=open_settings_dialog).props("flat dense").classes(
                "settings-button"
            )
        with ui.row().classes("main-area w-full"):
            with ui.column().classes("content-col"):
                with ui.row().classes("chart-area w-full"):
                    with ui.column().classes("drawing-toolbar"):
                        # Skupinová tlačítka: klik otevře flyout nabídku (JS) a
                        # výběr nástroje přijde zpět přes frontu událostí
                        drawing_buttons = {}
                        for group in DRAWING_GROUPS:
                            with (
                                ui.button(color=None)
                                .props(f'flat dense data-tool-group={group["id"]}')
                                .classes("drawing-btn")
                                .tooltip(group["tooltip"]) as group_button
                            ):
                                icon_html(group["icon"])
                            drawing_buttons[group["id"]] = group_button
                        ui.separator().props("vertical").classes("toolbar-sep")
                        # Akční tlačítka (zpracovává je on_drawing_finished přes frontu)
                        action_buttons = {}
                        # Prvky s SVG ikonou akčních tlačítek (přepínání ikony)
                        action_icons = {}
                        for action, tooltip, icon in [
                            ("magnet", "Magnet – přichytávání bodů kreseb k O/H/L/C (klik = výběr režimu)", "magnet"),
                            ("pencil", "Režim kreslení (nástroj zůstává aktivní)", "pencil"),
                            ("lock", "Zamknout kresby", "lock_open"),
                            ("eye", "Skrýt/zobrazit kresby", "eye"),
                            ("trash", "Smazat označenou / všechny kresby", "trash"),
                            ("favorites", "Zobrazit / skrýt panel oblíbených nástrojů (hvězdička ve flyoutu)", "star"),
                        ]:
                            with (
                                ui.button(color=None)
                                .props(f'flat dense data-tool-action={action}')
                                .classes("drawing-btn")
                                .tooltip(tooltip) as action_button
                            ):
                                action_icons[action] = icon_html(icon)
                            action_buttons[action] = action_button
                    # Mřížka grafů rozložení (CSS třída layout-<id> určuje uspořádání)
                    chart_grid = ui.element("div").classes(f"chart-grid layout-{state['layout']}")
                # Lišta přehrávání a panel obchodů replaye (jen s otevřenou session)
                replay_ui.build_bar()
                replay_ui.build_panel()
                # Stavová lišta: zprávy vlevo, vpravo seance (RTH/ETH) a hodiny
                # s volbou časového pásma (jako v TradingView)
                with ui.row().classes("statusbar-row w-full"):
                    status_label = ui.label("").classes("statusbar")
                    with ui.button("RTH").props("flat dense no-caps").classes("statusbar-btn") as session_button:
                        with ui.menu().classes("statusbar-menu"):
                            ui.label("SESSION").classes("statusbar-menu-header")
                            session_items = {
                                SESSION_RTH: ui.menu_item(
                                    "Regular trading hours", on_click=lambda: set_session(SESSION_RTH)
                                ),
                                SESSION_ETH: ui.menu_item(
                                    "Extended trading hours", on_click=lambda: set_session(SESSION_ETH)
                                ),
                            }
                    with ui.button().props("flat dense no-caps").classes("statusbar-btn tz-clock") as timezone_button:
                        ui.label("").classes("tz-clock-text")
                        with ui.menu().props("max-height=70vh").classes("statusbar-menu"):
                            timezone_items = {}
                            for tz_value, tz_label in [("UTC", "UTC"), ("exchange", "Exchange"), ("local", "Místní čas")]:
                                timezone_items[tz_value] = ui.menu_item(tz_label, on_click=make_timezone_click(tz_value))
                            ui.separator()
                            for tz_value, tz_label in timezone_options():
                                timezone_items[tz_value] = ui.menu_item(tz_label, on_click=make_timezone_click(tz_value))
                    timezone_button.tooltip("Časové pásmo grafu")
            # Pravý panel jako v TradingView: obsah podle tlačítka pravé lišty
            # (Watchlist / Alerty); opětovný klik na aktivní tlačítko panel skryje
            with ui.column().classes("right-panel") as right_panel:
                # Posuvná levá hrana pravého panelu (šířka, static/resizers.js)
                ui.element("div").classes("pane-resizer pane-resizer-left").props(
                    "data-resize=right-panel data-resize-min=220 data-resize-max=0.6"
                )
                with ui.column().classes("watchlist-panel w-full") as watchlist_section:
                    with ui.row().classes("watchlist-header w-full"):
                        # Název aktivního seznamu s nabídkou seznamů (jako v TradingView)
                        with ui.button("Watchlist", color=None).props(
                            "flat dense no-caps icon-right=expand_more"
                        ).classes("watchlist-title") as watchlist_button:
                            watchlist_menu = ui.menu().classes("wl-menu")
                        ui.space()
                        ui.button(icon="playlist_add", on_click=watchlist_add_current).props(
                            "flat dense size=sm"
                        ).classes("watch-add").tooltip("Přidat symbol grafu do seznamu")
                    # Rychlé přepínání seznamů (kulatá tlačítka s iniciálou názvu)
                    watchlist_quick = ui.row().classes("wl-quick w-full")
                    # Záhlaví sloupce Symbol: klik přepíná řazení podle abecedy
                    with ui.row().classes("watch-head w-full"):
                        with ui.button(color=None, on_click=toggle_watchlist_sort).props(
                            "flat dense no-caps"
                        ).classes("watch-sort").tooltip(
                            "Řadit podle abecedy (A→Z, Z→A, pořadí přidání)"
                        ) as watchlist_sort_button:
                            ui.label("Symbol")
                            watchlist_sort_icon = ui.icon(WATCHLIST_SORT_ICONS[state["watchlist_sort"]]).classes(
                                "watch-sort-icon"
                            )
                    watchlist_body = ui.column().classes("watchlist-body w-full")
                with ui.column().classes("alerts-panel w-full") as alerts_section:
                    # Přepínač Alerts / Log (log s počtem záznamů) jako v TradingView
                    with ui.row().classes("alerts-switch w-full"):
                        alerts_tab_buttons = {
                            "alerts": ui.button("Alerty", color=None, on_click=lambda: set_alerts_tab("alerts")).props(
                                "flat dense no-caps"
                            ).classes("alerts-switch-btn"),
                        }
                        with ui.button(color=None, on_click=lambda: set_alerts_tab("log")).props(
                            "flat dense no-caps"
                        ).classes("alerts-switch-btn") as log_switch_button:
                            ui.label("Log")
                            log_badge = ui.label("0").classes("alerts-count-badge")
                            log_badge.set_visibility(False)
                        alerts_tab_buttons["log"] = log_switch_button
                    # Lišta seznamu alertů: nový alert, hledání, řazení, hromadné akce
                    with ui.row().classes("alerts-toolbar w-full") as alerts_toolbar:
                        ui.button(icon="add", color=None, on_click=open_alert_for_chart).props(
                            "flat dense round size=sm"
                        ).classes("alert-btn").tooltip("Vytvořit alert pro aktuální symbol")
                        ui.space()
                        ui.button(icon="search", color=None, on_click=toggle_alert_search).props(
                            "flat dense round size=sm"
                        ).classes("alert-btn").tooltip("Hledat v alertech")
                        with ui.button(icon="sort", color=None).props("flat dense round size=sm").classes(
                            "alert-btn"
                        ).tooltip("Řazení alertů"):
                            with ui.menu().classes("alerts-menu"):
                                alert_sort_items = {
                                    sort_key: ui.menu_item(sort_label, on_click=make_alert_sort(sort_key))
                                    for sort_key, sort_label in ALERT_SORTS.items()
                                }
                        with ui.button(icon="more_horiz", color=None).props("flat dense round size=sm").classes(
                            "alert-btn"
                        ).tooltip("Další akce"):
                            with ui.menu().classes("alerts-menu"):
                                ui.menu_item("Spustit všechny neaktivní", on_click=make_bulk_alerts("restart_inactive"))
                                ui.menu_item("Pozastavit všechny", on_click=make_bulk_alerts("stop_all"))
                                ui.separator()
                                ui.menu_item(
                                    "Smazat všechny neaktivní", on_click=make_bulk_alerts("remove_inactive")
                                ).classes("wl-danger")
                                ui.menu_item("Smazat všechny", on_click=make_bulk_alerts("remove_all")).classes(
                                    "wl-danger"
                                )
                                ui.separator()
                                ui.menu_item("Časové připomínky…", on_click=reminders_dialog.open)
                    # Lišta logu: vymazání logu
                    with ui.row().classes("alerts-toolbar w-full") as log_toolbar:
                        ui.space()
                        with ui.button(icon="more_horiz", color=None).props("flat dense round size=sm").classes(
                            "alert-btn"
                        ).tooltip("Další akce"):
                            with ui.menu().classes("alerts-menu"):
                                ui.menu_item(
                                    "Vymazat log", on_click=alert_engine.clear_log
                                ).classes("wl-danger")
                    alert_search = ui.input(placeholder="Hledat").props(
                        "dense outlined clearable debounce=250"
                    ).classes("alert-search w-full")
                    alert_search.on_value_change(lambda _event: render_alerts_list())
                    alert_search.set_visibility(False)
                    alerts_list = ui.column().classes("alerts-list w-full")
                    alert_log_list = ui.column().classes("alerts-list w-full")
                # Sessions replaye (výběr, nová, přejmenování, smazání)
                replay_section = replay_ui.build_sessions_panel()
            # Svislá lišta úplně vpravo: přepínání obsahu pravého panelu
            with ui.column().classes("right-bar"):
                right_buttons = {}
                for panel, icon, tooltip in (
                    ("watchlist", "format_list_bulleted", "Watchlist"),
                    ("alerts", "alarm", "Alerty"),
                    ("replay", "history", "Replay – sessions"),
                ):
                    with ui.button(color=None, on_click=lambda panel=panel: toggle_right_panel(panel)).props(
                        "flat dense"
                    ).classes("right-bar-btn").tooltip(tooltip) as bar_button:
                        ui.icon(icon)
                        # Počet nepřečtených záznamů logu na tlačítku Alerty
                        if panel == "alerts":
                            alerts_badge = ui.badge("0").props("floating").classes("alerts-badge")
                            alerts_badge.set_visibility(False)
                    right_buttons[panel] = bar_button
                # Dole v liště: O aplikaci (produkt, autor, systém, odkaz na web)
                ui.space()
                with ui.button(color=None, on_click=about_dialog.open).props("flat dense").classes(
                    "right-bar-btn about-btn"
                ).tooltip(about_tooltip):
                    ui.icon("info_outline")

    # Dialog názvu seznamu symbolů (nový / přejmenování / kopie)
    with ui.dialog() as watchlist_name_dialog, ui.card().classes("tpl-name-dialog"):
        watchlist_name_title = settings_header(watchlist_name_dialog, "Nový seznam")
        watchlist_name_input = ui.input("Název seznamu").props("outlined dense autofocus").classes("w-full")
        watchlist_name_input.on("keydown.enter", submit_watchlist_name)
        with ui.row().classes("tv-settings-actions w-full"):
            settings_buttons(watchlist_name_dialog, submit_watchlist_name, "Save")

    # Potvrzení vyprázdnění / smazání seznamu symbolů
    with ui.dialog() as watchlist_confirm_dialog, ui.card().classes("tpl-name-dialog"):
        settings_header(watchlist_confirm_dialog, "Seznam symbolů")
        watchlist_confirm_text = ui.label("")
        with ui.row().classes("tv-settings-actions w-full"):
            settings_buttons(watchlist_confirm_dialog, submit_watchlist_confirm)

    # Potvrzení hromadného mazání alertů (nabídka ⋯ v panelu Alerty)
    with ui.dialog() as alerts_confirm_dialog, ui.card().classes("tpl-name-dialog"):
        settings_header(alerts_confirm_dialog, "Alerty")
        alerts_confirm_text = ui.label("")
        with ui.row().classes("tv-settings-actions w-full"):
            settings_buttons(alerts_confirm_dialog, submit_alerts_confirm)

    # Dialog pro výběr stickeru (emoji)
    with ui.dialog() as sticker_dialog, ui.card().classes("settings-dialog"):
        with ui.column().classes("indicator-dialog-body"):
            ui.label("Vyberte sticker").classes("settings-note")
            with ui.row().classes("emoji-row"):
                for emoji in EMOJIS:
                    ui.button(emoji, on_click=lambda _, e=emoji: place_sticker(e)).props(
                        "flat dense size=lg"
                    ).classes("emoji-button")

    async def place_sticker(emoji: str) -> None:
        # Po výběru emoji umístí sticker na uloženou pozici kliku (v grafu kliku)
        pending = state.get("sticker_pending")
        sticker_dialog.close()
        if not pending or pending["pane"].closed:
            return
        await pending["pane"].chart.add_sticker(pending["time"], pending["price"], emoji)
        show_status(f"Sticker {emoji} přidán a uložen.")
        state["sticker_pending"] = None

    # Nabídka indikátorů ve stylu TradingView: hledání, kategorie vlevo, seznam vpravo
    with ui.dialog() as indicator_dialog, ui.card().classes("ind-dialog"):
        with ui.row().classes("ind-dialog-header w-full"):
            ui.label("Indikátory").classes("ind-dialog-title")
            ui.space()
            ui.button(icon="close", on_click=indicator_dialog.close).props("flat dense round")
        indicator_search = (
            ui.input(placeholder="Hledat (např. RSI, průměr, objem)…", on_change=on_indicator_search)
            .props("outlined dense clearable autofocus")
            .classes("ind-search w-full")
        )
        with ui.row().classes("ind-dialog-body w-full"):
            indicator_categories = ui.column().classes("ind-categories")
            indicator_list = ui.column().classes("ind-list")

    # Dialog nastavení jedné instance indikátoru (ozubené kolečko v legendě)
    # se záložkami Inputs / Style / Visibility jako v TradingView
    with ui.dialog() as indicator_settings_dialog, ui.card().classes("tv-settings indicator-settings"):
        settings_title = settings_header(indicator_settings_dialog)
        with ui.tabs().props("dense no-caps align=left indicator-color=primary").classes("tv-tabs w-full") as settings_ind_tabs:
            inputs_tab = ui.tab("inputs", label="Inputs")
            settings_style = ui.tab("style", label="Style")
            settings_visibility_tab = ui.tab("visibility", label="Visibility")
        with ui.tab_panels(settings_ind_tabs, value=inputs_tab).props("animated=false").classes("tv-tab-panels w-full"):
            with ui.tab_panel(inputs_tab).classes("tv-tab-panel"):
                settings_form = ParamForm(ui.column().classes("indicator-dialog-params w-full"), pick_symbol)
            with ui.tab_panel(settings_style).classes("tv-tab-panel"):
                # Výstupy indikátoru (čáry, histogramy) a Output values
                plot_fields = PlotStyleFields()
                # Grafické objekty indikátoru (úsečky, tabulka scanneru) a hodnoty vstupů v legendě
                # – přepínače podle klíčů INDICATOR_STYLE_DEFAULTS (Output values má plot_fields)
                settings_style_checks: dict = {}
                with ui.column().classes("indicator-style-group w-full") as settings_objects:
                    ui.label("Graphic objects").classes("tv-settings-section param-group")
                    settings_style_checks["lines"] = ui.checkbox("Lines").classes("param-check tv-check")
                    settings_style_checks["tables"] = ui.checkbox("Tables").classes("param-check tv-check")
                with ui.column().classes("indicator-style-group w-full"):
                    ui.label("Input values").classes("tv-settings-section param-group")
                    settings_style_checks["inputs_in_status"] = ui.checkbox("Inputs in status line").classes(
                        "param-check tv-check"
                    )
            with ui.tab_panel(settings_visibility_tab).classes("tv-tab-panel"):
                # Viditelnost podle timeframu jako u kreseb
                settings_visibility = VisibilityFields()
                ui.label(
                    "Indikátor se počítá a kreslí (včetně tabulky scanneru) jen na timeframech, které odpovídají "
                    "zaškrtnutým jednotkám a rozsahům; v legendě zůstává."
                ).classes("settings-note")
        with ui.row().classes("tv-settings-actions w-full"):
            indicator_templates = TemplateMenu(
                cache,
                indicator_scope,
                collect_indicator_settings,
                apply_indicator_template,
                reset_indicator_settings,
                allow_default=True,
            )
            settings_buttons(indicator_settings_dialog, submit_indicator_settings)

    # Dialog Settings kresby jako v TradingView (pravý klik → Settings…, dvojklik
    # na kresbu, nástroj Text): záložka Text / Style / Inputs podle druhu kresby
    # a záložka Visibility (na kterých timeframech je kresba vidět)
    with ui.dialog() as drawing_settings_dialog, ui.card().classes("tv-settings drawing-settings"):
        drawing_settings_title = settings_header(drawing_settings_dialog, "Settings")
        with ui.tabs().props("dense no-caps align=left indicator-color=primary").classes("tv-tabs w-full") as drawing_tabs:
            style_tab = ui.tab("style", label="Style")
            visibility_tab = ui.tab("visibility", label="Visibility")
        with ui.tab_panels(drawing_tabs, value=style_tab).props("animated=false").classes("tv-tab-panels w-full"):
            with ui.tab_panel(style_tab).classes("tv-tab-panel"):
                # Text: barva, velikost, tučné, kurzíva, obsah, pozadí, rámeček, zalomení
                with ui.column().classes("text-fields w-full") as text_fields:
                    with ui.row().classes("text-toolbar"):
                        text_color = ColorButton(DEFAULT_DRAWING_COLOR, title="Barva textu")
                        text_size = ui.select(TEXT_FONT_SIZES, value=DEFAULT_FONT_SIZE).props(
                            "outlined dense options-dense"
                        ).classes("text-size")
                        text_bold = ui.button("B", color=None, on_click=lambda: toggle_text_flag("bold")).props(
                            "outline no-caps"
                        ).classes("text-flag text-flag-bold").tooltip("Tučné")
                        text_italic = ui.button("I", color=None, on_click=lambda: toggle_text_flag("italic")).props(
                            "outline no-caps"
                        ).classes("text-flag text-flag-italic").tooltip("Kurzíva")
                    drawing_text = ui.textarea(placeholder="Text").props("outlined rows=5").classes("w-full text-area")
                    with ui.row().classes("tv-settings-row"):
                        text_background = ui.checkbox("Background").classes("tv-settings-check")
                        text_background_color = ColorButton(DEFAULT_TEXT_BACKGROUND, title="Barva pozadí")
                    with ui.row().classes("tv-settings-row"):
                        text_border = ui.checkbox("Border").classes("tv-settings-check")
                        text_border_color = ColorButton(DEFAULT_TEXT_BORDER, title="Barva rámečku")
                    text_wrap = ui.checkbox("Text wrap")
                # Čáry a tvary: barva, šířka a styl čáry
                with ui.column().classes("line-fields w-full") as line_fields:
                    with ui.row().classes("tv-settings-row"):
                        ui.label("Barva").classes("tv-settings-label")
                        drawing_color = ColorButton(DEFAULT_DRAWING_COLOR, title="Barva kresby")
                    drawing_width = ui.select(DRAWING_LINE_WIDTHS, value=DEFAULT_LINE_WIDTH, label="Šířka čáry").props(
                        "outlined dense"
                    ).classes("w-full")
                    drawing_style = ui.select(DRAWING_LINE_STYLES, value="solid", label="Styl čáry").props(
                        "outlined dense"
                    ).classes("w-full")
                sticker_note = ui.label(
                    "Sticker nemá nastavitelný styl – na záložce Visibility zvolte, na kterých timeframech je vidět."
                ).classes("settings-note")
                # Pozice (Long / Short): parametry výpočtu množství a ceny úrovní
                with ui.column().classes("position-fields w-full") as position_fields:
                    position_account = ui.number("Velikost účtu", value=POSITION_DEFAULTS["accountSize"], min=0).props("outlined dense")
                    with ui.row().classes("position-row w-full"):
                        position_risk = ui.number("Riziko", value=POSITION_DEFAULTS["risk"], min=0).props("outlined dense")
                        position_risk_unit = ui.select(POSITION_RISK_UNITS, value=POSITION_DEFAULTS["riskUnit"]).props("outlined dense")
                    position_lot = ui.number("Velikost lotu", value=POSITION_DEFAULTS["lotSize"], min=0).props("outlined dense")
                    ui.label("Úrovně").classes("tv-settings-section")
                    position_entry = ui.number("Vstup (cena)", format="%.2f").props("outlined dense")
                    position_target = ui.number("Cíl – Profit level (cena)", format="%.2f").props("outlined dense")
                    position_stop = ui.number("Stop level (cena)", format="%.2f").props("outlined dense")
            with ui.tab_panel(visibility_tab).classes("tv-tab-panel"):
                # Viditelnost podle timeframu: jednotka, od, posuvník rozsahu, do
                drawing_visibility = VisibilityFields()
                ui.label(
                    "Kresba patří k symbolu a je vidět na timeframech, které odpovídají zaškrtnutým jednotkám a rozsahům."
                ).classes("settings-note")
        with ui.row().classes("tv-settings-actions w-full"):
            drawing_templates = TemplateMenu(
                cache,
                lambda: drawing_scope((state.get("drawing_settings") or {}).get("kind", "")),
                collect_drawing_settings,
                apply_drawing_template,
                apply_drawing_factory,
                allow_default=True,
                on_default_change=push_drawing_defaults,
            )
            settings_buttons(drawing_settings_dialog, submit_drawing_settings)

    # Uložení šablony indikátorů (Save indicator template) – aktuální indikátory aktivního grafu
    with ui.dialog() as save_template_dialog, ui.card().classes("tpl-name-dialog"):
        settings_header(save_template_dialog, "Save indicator template")
        template_name_input = ui.input("Název šablony").props("outlined dense autofocus").classes("w-full")
        template_name_input.on("keydown.enter", submit_save_template)
        template_remember_symbol = ui.checkbox("Remember symbol")
        template_remember_interval = ui.checkbox("Remember interval")
        with ui.row().classes("tv-settings-actions w-full"):
            settings_buttons(save_template_dialog, submit_save_template, "Save")

    # Seznam šablon indikátorů (Open template…): hledání, použití, oblíbené, smazání
    with ui.dialog() as templates_dialog, ui.card().classes("ind-dialog tpl-list-dialog"):
        with ui.row().classes("ind-dialog-header w-full"):
            ui.label("Indicator templates").classes("ind-dialog-title")
            ui.space()
            ui.button(icon="close", on_click=templates_dialog.close).props("flat dense round")
        templates_search = (
            ui.input(placeholder="Hledat šablonu…", on_change=lambda _e: render_templates_dialog())
            .props("outlined dense clearable autofocus")
            .classes("ind-search w-full")
        )
        templates_list = ui.column().classes("ind-list tpl-list w-full")

    # Settings grafu (pravý klik do grafu → Settings…): styl svíček, objem, šablony
    with ui.dialog() as chart_settings_dialog, ui.card().classes("tv-settings"):
        settings_header(chart_settings_dialog, "Settings")
        ui.label("Svíčky").classes("tv-settings-section")
        chart_fields: dict = {
            "color_by_prev_close": ui.checkbox("Obarvit svíčky podle předchozího závěru"),
        }
        # Řádky Tělo / Okraje / Knoty: přepínač + barva růstu a poklesu
        for toggle_key, prefix, label in (("body", "body", "Tělo"), ("borders", "border", "Okraje"), ("wick", "wick", "Knoty")):
            with ui.row().classes("tv-settings-row"):
                chart_fields[toggle_key] = ui.checkbox(label).classes("tv-settings-check")
                chart_fields[f"{prefix}_up"] = ColorButton(CHART_DEFAULTS[f"{prefix}_up"], title=f"{label} – růst")
                chart_fields[f"{prefix}_down"] = ColorButton(CHART_DEFAULTS[f"{prefix}_down"], title=f"{label} – pokles")
        ui.label("Objem").classes("tv-settings-section")
        volume_checkbox = ui.checkbox("Objem (Volume)")
        volume_note = ui.label(
            "Zdroj u tohoto instrumentu objem neposílá (forex IBKR MIDPOINT, indexy) – objem se nezobrazí."
        ).classes("settings-note volume-note")
        with ui.row().classes("tv-settings-actions w-full"):
            chart_templates = TemplateMenu(
                cache,
                lambda: "chart",
                collect_chart_values,
                fill_chart_values,
                lambda: fill_chart_values({**CHART_DEFAULTS, "volume": True}),
            )
            settings_buttons(chart_settings_dialog, submit_chart_settings)
    # Každá změna pole se hned promítne do grafu (náhled); zavření bez Ok ji vrátí
    for chart_field in [*chart_fields.values(), volume_checkbox]:
        chart_field.on_value_change(lambda _event: preview_chart_style())
    chart_settings_dialog.on_value_change(on_chart_dialog_change)

    # Popup s předplatnými dat IBKR (otevírá se ze záložky Interactive Brokers v Settings)
    ibkr_data_dialog = build_ibkr_data_dialog()

    # Dialog nastavení: Telegram pro alerty a API klíče Alpaca (config.yaml)
    # Settings aplikace: všechny sekce config.yaml (Telegram, Interactive Brokers,
    # Alpaca) v záložkách; ukládá se jen to, co se změnilo
    with ui.dialog() as settings_dialog, ui.card().classes("tv-settings app-settings"):
        settings_header(settings_dialog, "Settings")
        ui.label(
            "Ukládá se lokálně do config.yaml v kořenu projektu (necommituje se, komentáře zůstanou)."
        ).classes("settings-note")
        with ui.tabs().props("dense no-caps align=left indicator-color=primary").classes("tv-tabs w-full") as settings_tabs:
            tab_telegram = ui.tab("Telegram")
            tab_ibkr = ui.tab("Interactive Brokers")
            tab_alpaca = ui.tab("Alpaca")
            tab_replay = ui.tab("Replay")
        with ui.tab_panels(settings_tabs, value=tab_telegram).classes("tv-tab-panels w-full"):
            with ui.tab_panel(tab_telegram).classes("tv-tab-panel"):
                ui.label(
                    "Notifikace alertů, scanneru a připomínek. Bota založíte u @BotFather (příkaz /newbot), "
                    "token vložte sem. Pak botovi napište (např. /start) a tlačítkem Zjistit chat ID se chat "
                    "doplní sám. Platí hned."
                ).classes("settings-note")
                settings_telegram_token = ui.input("Bot token").props("outlined dense type=password").classes("w-full")
                settings_telegram_chat = ui.input("Chat ID").props("outlined dense").classes("w-full")
                with ui.row().classes("dialog-actions"):
                    ui.button("Zjistit chat ID", on_click=detect_telegram_chat, color=None).props("flat no-caps")
                    ui.button("Odeslat test", on_click=test_telegram, color=None).props("flat no-caps")
            with ui.tab_panel(tab_ibkr).classes("tv-tab-panel"):
                ui.label(
                    "TWS nebo IB Gateway musí běžet a mít povolené API (Configure → API → Settings). "
                    "Porty: TWS paper 7497, TWS live 7496, IB Gateway paper 4002, live 4001. "
                    "Při obsazeném client ID se zkusí další. Po uložení se zdroj hned přepojí."
                ).classes("settings-note")
                settings_ibkr_host = ui.input("Host").props("outlined dense").classes("w-full")
                with ui.row().classes("tv-settings-row w-full"):
                    settings_ibkr_port = ui.number("Port", min=1, max=65535, step=1).props("outlined dense")
                    settings_ibkr_client = ui.number("Client ID", min=0, step=1).props("outlined dense")
                with ui.row().classes("dialog-actions"):
                    ui.button("Otestovat připojení", on_click=test_ibkr, color=None).props("flat no-caps")
                    # Popup s předplatnými dat IB (levná varianta a varianta s futures)
                    ui.button("Jak na předplatné dat", icon="info", on_click=ibkr_data_dialog.open,
                              color=None).props("flat no-caps")
            with ui.tab_panel(tab_alpaca).classes("tv-tab-panel"):
                ui.label(
                    "Volitelný zdroj živých amerických akcií. Klíče z Paper Trading dashboardu; bez klíčů "
                    "se zdroj nezapne. Změna se projeví po restartu aplikace."
                ).classes("settings-note")
                settings_alpaca_endpoint = ui.input("Endpoint (Trading API)").props("outlined dense").classes("w-full")
                settings_key = ui.input("API key").props("outlined dense type=password").classes("w-full")
                settings_secret = ui.input("Secret").props("outlined dense type=password").classes("w-full")
            with ui.tab_panel(tab_replay).classes("tv-tab-panel"):
                ui.label(
                    "Krok zpět v replayi (nůžky v liště replaye nebo skok na dřívější čas). Příkazy a obchody "
                    "zadané po bodu návratu se smažou a stav účtu se přepočítá. Platí hned."
                ).classes("settings-note")
                settings_replay_rewind = ui.select(REWIND_MODES, label="Krok zpět").props("outlined dense").classes(
                    "w-full"
                )
        with ui.row().classes("tv-settings-actions w-full"):
            ui.space()
            settings_buttons(settings_dialog, save_settings)

    # Dialogy replaye (sessions, příkaz, úprava obchodu, skok, nastavení session)
    replay_ui.build_dialogs()

    # Hledání symbolu ve stylu TradingView: pole, kategorie, zdroj a výsledky
    with ui.dialog().props("position=top") as symbol_search_dialog, ui.card().classes("sym-dialog"):
        with ui.row().classes("sym-search-row w-full"):
            symbol_search_input = (
                ui.input(placeholder="Symbol, název nebo ISIN…", on_change=on_search_input)
                .props("borderless dense autofocus clearable debounce=250")
                .classes("sym-search-input")
            )
            with symbol_search_input.add_slot("prepend"):
                ui.icon("search")
            ui.button(icon="close", color=None, on_click=symbol_search_dialog.close).props("flat dense round")
        with ui.row().classes("sym-chips w-full"):
            search_chips = {
                key: ui.button(label, color=None, on_click=make_search_category(key)).props("flat dense no-caps rounded").classes("sym-chip")
                for key, label in SEARCH_CATEGORIES.items()
            }
            ui.space()
            ui.select(
                {"all": "Všechny zdroje", **{provider.id: provider.name for provider in PROVIDERS}},
                value="all",
                on_change=set_search_source,
            ).props("dense borderless options-dense").classes("sym-source-select")
        symbol_search_results = ui.column().classes("sym-results w-full")
    symbol_search_input.on("keydown.enter", pick_highlighted)
    symbol_search_input.on("keydown.arrow-down", lambda _e: move_search_highlight(1))
    symbol_search_input.on("keydown.arrow-up", lambda _e: move_search_highlight(-1))

    # Dialog vytvoření / úpravy alertu (jako „Create alert on …“ v TradingView)
    with ui.dialog() as alert_dialog, ui.card().classes("tv-settings alert-dialog"):
        alert_dialog_title = settings_header(alert_dialog, "Create alert")
        with ui.grid(columns="110px 1fr").classes("alert-grid w-full"):
            ui.label("Condition").classes("alert-label")
            with ui.column().classes("alert-field"):
                # Zdroj podmínky jako v TradingView: cena symbolu, nebo indikátor v grafu
                alert_source = ui.select({"price": "Cena"}, value="price").props("outlined dense").classes("w-full")
                alert_condition = ui.select(CONDITIONS, value="crossing").props("outlined dense").classes("w-full")
                alert_target = ui.select({"value": "Cena (hodnota)"}, value="value").props("outlined dense").classes("w-full")
                alert_price = ui.number("Cena").props("outlined dense").classes("w-full")
            ui.label("Trigger").classes("alert-label")
            with ui.column().classes("alert-field"):
                alert_trigger = ui.select(TRIGGERS, value="once").props("outlined dense").classes("w-full")
                alert_minutes = ui.number("Minut před zavřením svíčky", min=1, max=MAX_ALERT_MINUTES, step=1).props(
                    "outlined dense"
                ).classes("w-full")
            ui.label("Expiration").classes("alert-label")
            with ui.column().classes("alert-field"):
                alert_expiration = ui.input().props("outlined dense type=datetime-local").classes("w-full")
                alert_open_ended = ui.checkbox("Bez expirace (open-ended)")
            ui.label("Message").classes("alert-label")
            with ui.column().classes("alert-field"):
                alert_message = ui.textarea().props("outlined dense autogrow").classes("w-full")
                ui.label(
                    "Zástupné texty: {{ticker}} {{exchange}} {{interval}} {{close}} {{time}}, u indikátoru {{signal}}, "
                    "u alertu scanneru {{message}}"
                ).classes("settings-note")
            ui.label("Notifications").classes("alert-label")
            with ui.column().classes("alert-field"):
                alert_notify_telegram = ui.checkbox("Telegram")
                alert_telegram_note = ui.label("Telegram není nastavený – ozubené kolečko v horní liště → Telegram.").classes(
                    "settings-note"
                )
                alert_notify_toast = ui.checkbox("Toast v aplikaci")
                alert_notify_sound = ui.checkbox("Přehrát zvuk")
                alert_notify_webhook = ui.checkbox("Webhook URL")
                alert_webhook_url = ui.input(placeholder="https://…").props("outlined dense").classes("w-full")
        with ui.row().classes("tv-settings-actions w-full"):
            ui.space()
            ui.button("Cancel", on_click=alert_dialog.close, color=None).props("outline no-caps").classes("tv-btn-cancel")
            alert_submit = ui.button("Create", on_click=submit_alert, color=None).props("unelevated no-caps").classes("tv-btn-ok")
    # Zdroj podmínky mění nabídky podmínek a spouštění, spouštění viditelnost minut
    alert_source.on_value_change(lambda _event: (apply_alert_source(), refresh_alert_message()))
    alert_condition.on_value_change(lambda _event: apply_alert_condition())
    alert_trigger.on_value_change(lambda _event: apply_alert_trigger())
    for alert_field in (alert_condition, alert_target, alert_price):
        alert_field.on_value_change(lambda _event: refresh_alert_message())
    alert_message.on_value_change(on_alert_message_change)
    alert_webhook_url.bind_visibility_from(alert_notify_webhook, "value")
    alert_expiration.bind_visibility_from(alert_open_ended, "value", backward=lambda value: not value)

    # ---------- vazby ----------
    timeframe_toggle.on_value_change(lambda event: set_timeframe(event.value))
    theme_switch.on_value_change(lambda event: toggle_theme(bool(event.value)))

    # Grafy rozložení (vytvoří se v mřížce; data načtou po inicializaci v prohlížeči)
    for pane_config in layout_config["panes"]:
        panes.append(create_pane(pane_config))
    paint_layout()
    render_indicator_templates()
    # Pravý panel z minula (Watchlist / Alerty / skrytý)
    paint_right_panel()

    # Průběžné vyzvedávání událostí všech grafů (viz process_chart_events)
    ui.timer(0.5, process_chart_events)

    # Replay: obnova lišty, čar v grafech a panelu obchodů, klávesové zkratky
    replay_ui.start()

    # Aktualizace cen watchlistu (Binance 5 s, Yahoo throttlováno na zdroji)
    ui.timer(5.0, refresh_watchlist_prices)

    # Naplnění watchlistu z databáze po načtení stránky
    ui.timer(0.2, rebuild_watchlist, once=True)

    # Alerty: stránka dostává události enginu (toast, zvuk, obnova seznamu)
    alert_engine.add_listener(on_alert_event)
    ui.timer(0.3, refresh_alerts_ui, once=True)

    def on_scanner_event(_event: dict) -> None:
        # Nový výsledek scanneru obnoví tabulky v grafech
        with page_root:
            push_overview()

    scanner.add_listener(on_scanner_event)

    def on_disconnect() -> None:
        # Při odpojení klienta ukončíme živé streamy jeho grafů a odhlásíme ho od alertů a scanneru
        # Otevřená session replaye se uloží (kurzor, příkazy)
        replay_ui.shutdown()
        for pane in panes:
            pane.close()
        alert_engine.remove_listener(on_alert_event)
        scanner.remove_listener(on_scanner_event)

    context.client.on_disconnect(on_disconnect)
