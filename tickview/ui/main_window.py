"""Hlavní stránka TickView: vyhledání symbolu, timeframy, graf, indikátory, kreslení, živý stream."""

import asyncio
import uuid

from nicegui import context, ui

from tickview.config import load_config, save_config
from tickview.logic.cache import KlineCache, fetch_history
from tickview.logic.indicators import INDICATOR_DEFS, PARAM_LABELS, compute_indicators
from tickview.providers import PROVIDERS, get_provider
from tickview.providers.base import TIMEFRAME_MS, utc_now_ms
from tickview.ui.lightweight_chart import LightweightChart

# Kolik svíček historie se načte pro jedno zobrazení (a uloží do cache)
DEFAULT_BARS = 600
# Kolik posledních svíček je vidět po načtení grafu
VISIBLE_BARS = 150

# Verze statických souborů pro cache-busting v prohlížeči (při změně CSS/JS zvýšit)
STATIC_VERSION = "0.4.3"


def register(cache: KlineCache) -> None:
    """Registruje hlavní stránku aplikace (volá se z main.py)."""

    @ui.page("/")
    async def index() -> None:
        await _build_index(cache)


async def _build_index(cache: KlineCache) -> None:
    # Statické soubory (styly + JS grafu + vendored knihovna) do hlavičky stránky;
    # verze v URL zajistí, že prohlížeč po změně souborů nepoužije starou cache
    ui.add_head_html(
        f'<link rel="stylesheet" href="/static/style.css?v={STATIC_VERSION}">'
        f'<script src="/static/lightweight-charts.standalone.production.js?v={STATIC_VERSION}"></script>'
        f'<script src="/static/lightweight_chart.js?v={STATIC_VERSION}"></script>'
    )

    # Nastavení z minula: motiv, poslední symbol a timeframe
    dark = (await cache.get_setting("theme", "dark")) != "light"
    last_symbol = await cache.get_setting("last_symbol", "binance:BTCUSDT") or "binance:BTCUSDT"
    last_timeframe = await cache.get_setting("last_timeframe", "1h") or "1h"
    ui.dark_mode(dark)

    # Stav zobrazeného symbolu; load_seq chrání proti překříženým odpovědím
    state: dict = {
        "provider_id": None,
        "provider": None,
        "symbol": None,
        "timeframe": last_timeframe,
        "load_seq": 0,
        "stream_task": None,
        "badge_live": None,
        "indicators": [],
        "candles": None,
        "drawing_mode": None,
    }

    # ---------- handlery (uzávěry nad elementy vytvořenými níže) ----------

    def show_status(message: str, error: bool = False) -> None:
        # Z background tasků (živý stream) musíme vstoupit do slotu prvku
        with status_label:
            status_label.text = message
            status_label.classes(add="error" if error else "", remove="" if error else "error")

    async def search_symbols() -> None:
        # Vyhledání napříč zdroji; výsledky se ukazují jako návrhy pod polem
        query = (search_input.value or "").strip()
        state["suggestions"] = []
        state["highlight"] = 0
        if not query:
            hide_suggestions()
            return
        results: list[dict] = []
        for provider in PROVIDERS:
            try:
                found = await provider.search(query)
            except Exception as exc:
                show_status(f"Zdroj {provider.name} není dostupný: {exc}", error=True)
                continue
            for symbol_info in found[:6]:
                key = f"{symbol_info.provider_id}:{symbol_info.symbol}"
                results.append(
                    {
                        "key": key,
                        "label": f"{symbol_info.label} · {provider.name}",
                        "symbol": symbol_info.symbol,
                    }
                )
        # Přesná shoda > prefix > výskyt; celkový počet návrhů omezíme na 12
        upper = query.upper()

        def relevance(result: dict) -> int:
            symbol = result["symbol"].upper()
            if symbol == upper:
                return 0
            if symbol.startswith(upper):
                return 1
            return 2

        results.sort(key=relevance)
        state["suggestions"] = results[:12]
        render_suggestions()

    def hide_suggestions() -> None:
        suggestions_container.classes(add="hidden")

    def render_suggestions() -> None:
        # Přestaví nabídku pod vyhledávacím polem; zvýrazněný řádek reaguje na šipky
        suggestions_container.clear()
        suggestions = state.get("suggestions") or []
        if not suggestions:
            hide_suggestions()
            return
        suggestions_container.classes(remove="hidden")
        with suggestions_container:
            for index, suggestion in enumerate(suggestions):
                classes = "autocomplete-item" + (" active" if index == state["highlight"] else "")
                item = ui.label(suggestion["label"]).classes(classes)
                item.on("click", make_load_from_search(suggestion["key"]))

    def make_load_from_search(key: str):
        # Klik na návrh: přidá do watchlistu (jen když tam ještě není), načte graf
        async def _handler(_event):
            provider_id, _, symbol = key.partition(":")
            existing = await cache.watchlist_list()
            already = any(row["source"] == provider_id and row["symbol"] == symbol for row in existing)
            if not already:
                await cache.watchlist_add(provider_id, symbol)
                await rebuild_watchlist()
                show_status(f"{symbol} přidán do watchlistu ({provider_id}).")
            else:
                show_status(f"{symbol} už v watchlistu je.")
            await load_symbol(key)
            hide_suggestions()

        return _handler

    def move_highlight(delta: int) -> None:
        suggestions = state.get("suggestions") or []
        if not suggestions:
            return
        state["highlight"] = (state["highlight"] + delta) % len(suggestions)
        render_suggestions()

    async def add_search_to_watchlist() -> None:
        # Enter: přidá označený (nebo první) návrh do watchlistu, jen když tam není
        suggestions = state.get("suggestions") or []
        if not suggestions:
            show_status("Nejdřív hledejte symbol, poté ho Enterem přidáte do watchlistu.")
            return
        suggestion = suggestions[min(state["highlight"], len(suggestions) - 1)]
        provider_id, _, symbol = suggestion["key"].partition(":")
        existing = await cache.watchlist_list()
        already = any(row["source"] == provider_id and row["symbol"] == symbol for row in existing)
        if already:
            show_status(f"{symbol} už v watchlistu je ({provider_id}).")
        else:
            await cache.watchlist_add(provider_id, symbol)
            await rebuild_watchlist()
            show_status(f"{symbol} přidán do watchlistu ({provider_id}).")
        hide_suggestions()

    async def load_symbol(key) -> None:
        if not key or ":" not in str(key):
            return
        provider_id, _, symbol = str(key).partition(":")
        try:
            provider = get_provider(provider_id)
        except KeyError:
            show_status(f"Neznámý zdroj: {provider_id}", error=True)
            return
        state["provider_id"] = provider_id
        state["provider"] = provider
        state["symbol"] = symbol
        # Nabídneme jen timeframy, které zdroj podporuje; dosavadní výběr případně nahradíme
        timeframes = list(provider.timeframes.keys())
        if state["timeframe"] not in timeframes:
            state["timeframe"] = timeframes[0]
        timeframe_toggle.options = timeframes
        timeframe_toggle.value = state["timeframe"]
        timeframe_toggle.update()
        set_badge(None)
        await cache.set_setting("last_symbol", f"{provider_id}:{symbol}")
        await reload_chart()

    async def set_timeframe(value) -> None:
        if not value:
            return
        state["timeframe"] = str(value)
        await cache.set_setting("last_timeframe", str(value))
        await reload_chart()

    def set_badge(live: bool | None) -> None:
        """Aktualizuje štítek zdroje jen při změně stavu (živě / zpožděno / —)."""
        if state["badge_live"] == live:
            return
        state["badge_live"] = live
        provider = state.get("provider")
        name = provider.name if provider else "—"
        if live is None:
            text = "—"
        elif live:
            text = f"{name} · živě"
        else:
            text = f"{name} · zpožděno"
        # Z background tasků (živý stream) musíme vstoupit do slotu prvku
        with source_badge:
            source_badge.text = text
            source_badge.update()

    async def consume_stream(provider, symbol: str, timeframe: str, seq: int) -> None:
        """Konzumuje živý stream zdroje a doplňuje svíčky do grafu a cache."""
        step_ms = TIMEFRAME_MS[timeframe]
        market_closed_warned = False
        try:
            async for candle in provider.stream(symbol, timeframe):
                if seq != state["load_seq"]:
                    # Mezitím se přepnulo zobrazení – stream ukončíme
                    return
                set_badge(provider.is_live)
                await chart.update_candle(candle)
                # Do cache ukládáme jen uzavřené svíčky (uplynulé období);
                # rozepsanou svíčku vrátí příště historie ze sítě v plné podobě
                if candle.open_time + step_ms <= utc_now_ms():
                    await cache.store(provider.id, symbol, timeframe, [candle])
                    # Nejnovější svíčka už je uzavřená a žádná nová nepřišla
                    # → trh je pravděpodobně zavřený (hlásíme jen jednou)
                    if not market_closed_warned:
                        market_closed_warned = True
                        show_status(
                            f"{symbol}: trh je pravděpodobně zavřený – živá data nepřibývají"
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if seq == state["load_seq"]:
                set_badge(False)
                show_status(f"Živý stream přerušen, čekám na znovupřipojení: {exc}", error=True)

    def cancel_stream() -> None:
        task = state.get("stream_task")
        if task and not task.done():
            task.cancel()
        state["stream_task"] = None

    def start_stream() -> None:
        # Předchozí stream zrušíme (přepnutí symbolu/timeframu/klienta)
        cancel_stream()
        provider = state.get("provider")
        if provider is None or not provider.supports_stream:
            set_badge(False)
            return
        state["stream_task"] = asyncio.create_task(
            consume_stream(
                provider, state["symbol"], state["timeframe"], state["load_seq"]
            )
        )

    async def reload_chart() -> None:
        provider = state.get("provider")
        symbol = state.get("symbol")
        timeframe = state.get("timeframe")
        if provider is None or not symbol:
            return
        state["load_seq"] += 1
        seq = state["load_seq"]
        cancel_stream()
        set_badge(False)
        show_status(f"Načítám {symbol} ({timeframe})…")
        try:
            end_ms = utc_now_ms()
            start_ms = end_ms - DEFAULT_BARS * TIMEFRAME_MS[timeframe]
            candles = await fetch_history(provider, cache, symbol, timeframe, start_ms, end_ms)
            if seq != state["load_seq"]:
                # Mezitím uživatel zvolil jiný symbol/timeframe – zastaralou odpověď zahodíme
                return
            if not candles:
                show_status(f"Pro {symbol} ({timeframe}) nejsou dostupná data.", error=True)
                return
            await chart.set_candles(candles, visible_bars=VISIBLE_BARS)
            state["candles"] = candles
            # Přepočet aktivních indikátorů nad novými daty
            await refresh_indicators()
            # Kresby patří k symbolu/timeframu – načteme uložené
            await reload_drawings()
            show_status(
                f"{symbol} · {timeframe} · {len(candles)} svíček · zdroj {provider.name}"
            )
            # Historie načtena → spustíme živý stream pro aktuální zobrazení
            start_stream()
        except Exception as exc:
            show_status(f"Načtení dat selhalo: {exc}", error=True)

    async def toggle_theme(value: bool) -> None:
        # Bez argumentu ui.dark_mode() vrátí existující handle aktuálního klienta
        ui.dark_mode().set_value(value)
        await chart.set_theme(value)
        await cache.set_setting("theme", "dark" if value else "light")

    # ---------- kreslení (krok 5 + lišta ve stylu TradingView) ----------

    # Druhy kresby, které backend ukládá (cursor/measure = režimy bez ukládání)
    DRAWING_KINDS = [
        "hline", "vline", "trend", "ray", "extended", "arrow", "hray", "cross",
        "channel", "fib", "fibext", "pitchfork", "rect", "brush", "text", "sticker",
    ]
    # Skupiny nástrojů lišty (jako v TradingView): id, ikona, tooltip, nástroje
    DRAWING_GROUPS = [
        {"id": "g_cursor", "icon": "near_me", "tooltip": "Kurzor (výběr a tažení kreseb)",
         "tools": [{"mode": "cursor", "tooltip": "Kurzor", "icon": "near_me"}]},
        {"id": "g_trend", "icon": "show_chart", "tooltip": "Čáry",
         "tools": [
             {"mode": "trend", "tooltip": "Trendová čára", "icon": "show_chart"},
             {"mode": "ray", "tooltip": "Polopřímka", "icon": "trending_up"},
             {"mode": "extended", "tooltip": "Přímka (oboustranná)", "icon": "sync_alt"},
             {"mode": "arrow", "tooltip": "Trendová šipka", "icon": "north_east"},
         ]},
        {"id": "g_levels", "icon": "horizontal_rule", "tooltip": "Horizontální a svislé linie",
         "tools": [
             {"mode": "hline", "tooltip": "Horizontální linie", "icon": "horizontal_rule"},
             {"mode": "vline", "tooltip": "Svislá linie", "icon": "height"},
             {"mode": "hray", "tooltip": "Horizontální polopřímka", "icon": "trending_flat"},
             {"mode": "cross", "tooltip": "Kříž", "icon": "add"},
         ]},
        {"id": "g_fib", "icon": "align_vertical_center", "tooltip": "Fibonacci",
         "tools": [
             {"mode": "fib", "tooltip": "Fibonacci retracement", "icon": "align_vertical_center"},
             {"mode": "fibext", "tooltip": "Fibonacci extension", "icon": "linear_scale"},
         ]},
        {"id": "g_channel", "icon": "view_stream", "tooltip": "Paralelní kanál (3 kliky)",
         "tools": [{"mode": "channel", "tooltip": "Paralelní kanál (3 kliky)", "icon": "view_stream"}]},
        {"id": "g_pitchfork", "icon": "gesture", "tooltip": "Pitchfork (3 kliky)",
         "tools": [{"mode": "pitchfork", "tooltip": "Pitchfork (3 kliky)", "icon": "gesture"}]},
        {"id": "g_shapes", "icon": "crop_square", "tooltip": "Tvary",
         "tools": [
             {"mode": "rect", "tooltip": "Obdélník", "icon": "crop_square"},
             {"mode": "brush", "tooltip": "Štětec", "icon": "brush"},
         ]},
        {"id": "g_text", "icon": "title", "tooltip": "Text a stickers",
         "tools": [
             {"mode": "text", "tooltip": "Text", "icon": "title"},
             {"mode": "sticker", "tooltip": "Sticker (emoji)", "icon": "emoji_emotions"},
         ]},
        {"id": "g_measure", "icon": "straighten", "tooltip": "Měřič (Δcena, %, bary)",
         "tools": [{"mode": "measure", "tooltip": "Měřič (Δcena, %, bary)", "icon": "straighten"}]},
    ]
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
    }
    # Barvy nových kreseb (paleta v liště)
    DRAWING_COLORS = ["#2962ff", "#e91e63", "#26a69a", "#ff9800", "#d1d4dc"]
    EMOJIS = ["😀", "🚀", "💰", "📈", "📉", "👍", "⚡", "🎯"]

    async def set_drawing_mode(mode) -> None:
        # Aktivní nástroj zvýrazníme na skupině, do které patří (jako v TradingView)
        state["drawing_mode"] = mode
        for group in DRAWING_GROUPS:
            button = drawing_buttons.get(group["id"])
            if not button:
                continue
            group_modes = [tool["mode"] for tool in group["tools"]]
            button.classes(
                add="drawing-btn-active" if mode in group_modes else "",
                remove="" if mode in group_modes else "drawing-btn-active",
            )
        await chart.set_drawing_mode(None if mode in (None, "cursor") else mode)

    async def set_drawing_color(color: str) -> None:
        # Barva nových kreseb; aktivní paletu zvýrazníme
        state["drawing_color"] = color
        for hex_color, button in swatch_buttons.items():
            button.classes(add="swatch-active" if hex_color == color else "", remove="" if hex_color == color else "swatch-active")
        await chart.set_drawing_color(color)

    async def toggle_magnet() -> None:
        # Magnet: přichytávání nových bodů k OHLC nejbližší svíčky
        state["magnet"] = not state.get("magnet", False)
        button = action_buttons["magnet"]
        button.classes(add="drawing-btn-active" if state["magnet"] else "", remove="" if state["magnet"] else "drawing-btn-active")
        await chart.set_magnet(state["magnet"])
        show_status(f"Magnet {'zapnut' if state['magnet'] else 'vypnut'}.")

    async def toggle_pencil() -> None:
        # Režim tužky: nástroj zůstává vybraný i po nakreslení (jako v TradingView)
        state["keep_tool"] = not state.get("keep_tool", False)
        button = action_buttons["pencil"]
        button.classes(add="drawing-btn-active" if state["keep_tool"] else "", remove="" if state["keep_tool"] else "drawing-btn-active")
        await chart.set_keep_tool(state["keep_tool"])
        show_status(f"Režim kreslení {'zapnut' if state['keep_tool'] else 'vypnut'}.")

    async def toggle_lock() -> None:
        # Zámek: kresby nelze vybírat, posouvat ani mazat
        state["locked"] = not state.get("locked", False)
        button = action_buttons["lock"]
        button.props(f'icon={"lock" if state["locked"] else "lock_open"}')
        button.classes(add="drawing-btn-active" if state["locked"] else "", remove="" if state["locked"] else "drawing-btn-active")
        await chart.set_locked(state["locked"])
        show_status(f"Kresby {'uzamčeny' if state['locked'] else 'odemčeny'}.")

    async def toggle_hide_drawings() -> None:
        # Skrytí/zobrazení všech kreseb (ikona oka)
        state["hidden"] = not state.get("hidden", False)
        button = action_buttons["eye"]
        button.props(f'icon={"visibility_off" if state["hidden"] else "visibility"}')
        button.classes(add="drawing-btn-active" if state["hidden"] else "", remove="" if state["hidden"] else "drawing-btn-active")
        await chart.set_drawings_hidden(state["hidden"])

    async def trash_drawings() -> None:
        # Koš: smaže označenou kresbu, jinak všechny
        removed = await chart.trash_drawings()
        if removed:
            await reload_drawings()
            show_status(f"Smazáno kreseb: {removed}.")

    # ---------- textové kresby ----------

    async def open_text_dialog(time_s: int, price: float, edit_id=None, current: str = "") -> None:
        # Dialog pro vložení/editaci textu na plátně (anchora v čase a ceně)
        state["text_pending"] = {"time": int(time_s), "price": float(price), "edit_id": edit_id}
        text_input.value = current
        text_dialog.open()

    async def save_text() -> None:
        pending = state.get("text_pending")
        value = (text_input.value or "").strip()
        text_dialog.close()
        if not pending or not value:
            return
        if pending["edit_id"]:
            await chart.update_text(int(pending["edit_id"]), value)
            show_status("Text upraven.")
        else:
            await chart.add_text(pending["time"], pending["price"], value)
            show_status("Text přidán a uložen.")
        state["text_pending"] = None

    async def reload_drawings() -> None:
        """Načte kresby pro aktuální symbol z cache a pošle je do grafu."""
        provider_id = state.get("provider_id")
        symbol = state.get("symbol")
        timeframe = state.get("timeframe")
        if not provider_id or not symbol:
            return
        drawings = await cache.load_drawings(provider_id, symbol, timeframe)
        await chart.set_drawings(drawings)

    async def on_drawing_finished() -> None:
        # Stránkový timer vyzvedne události kreslení z grafu (fronta: přidání /
        # smazání pravým klikem) a projeví je v databázi
        provider_id = state.get("provider_id")
        symbol = state.get("symbol")
        timeframe = state.get("timeframe")
        if not provider_id or not symbol:
            return
        changed = False
        for _ in range(20):
            payload = await ui.run_javascript(
                f'return window.TickViewChart.popFinishedDrawing("{chart.js_id}");'
            )
            if not isinstance(payload, dict):
                break
            if payload.get("type") == "tool":
                await set_drawing_mode(payload.get("mode"))
            elif payload.get("type") == "action":
                action = payload.get("action")
                if action == "magnet":
                    await toggle_magnet()
                elif action == "pencil":
                    await toggle_pencil()
                elif action == "lock":
                    await toggle_lock()
                elif action == "eye":
                    await toggle_hide_drawings()
                elif action == "trash":
                    await trash_drawings()
            elif payload.get("type") == "sticker_request":
                state["sticker_pending"] = {
                    "time": int(payload.get("time")),
                    "price": float(payload.get("price")),
                }
                sticker_dialog.open()
            elif payload.get("type") == "delete":
                drawing_id = payload.get("id")
                if drawing_id:
                    await cache.delete_drawing(provider_id, symbol, timeframe, int(drawing_id))
                    changed = True
                    show_status("Kresba smazána.")
            elif payload.get("type") == "update":
                drawing_id = payload.get("id")
                if drawing_id:
                    points = [
                        {"time": int(p["time"]), "price": float(p["price"])}
                        for p in (payload.get("points") or [])
                    ]
                    await cache.update_drawing(
                        provider_id, symbol, timeframe, int(drawing_id), points, payload.get("text")
                    )
                    changed = True
            elif payload.get("type") == "text_request":
                await open_text_dialog(payload.get("time"), payload.get("price"))
            elif payload.get("type") == "text_edit":
                await open_text_dialog(0, 0.0, edit_id=payload.get("id"), current=payload.get("text") or "")
            elif payload.get("type") == "add" and payload.get("kind") in DRAWING_KINDS:
                points = [
                    {"time": int(p["time"]), "price": float(p["price"])}
                    for p in (payload.get("points") or [])
                ]
                await cache.add_drawing(
                    provider_id,
                    symbol,
                    timeframe,
                    payload["kind"],
                    points,
                    payload.get("color"),
                    payload.get("text"),
                )
                changed = True
                show_status(
                    f"Kresba uložena ({DRAWING_LABELS[payload['kind']]}) k {symbol} ({timeframe})"
                )
        if changed:
            await reload_drawings()

    async def clear_drawings() -> None:
        provider_id = state.get("provider_id")
        symbol = state.get("symbol")
        timeframe = state.get("timeframe")
        if not provider_id or not symbol:
            return
        await cache.clear_drawings(provider_id, symbol, timeframe)
        await chart.set_drawings([])
        show_status("Kresby smazány.")

    # ---------- watchlist (krok 6) ----------

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
            await cache.watchlist_remove(provider_id, symbol)
            await rebuild_watchlist()

        return _handler

    async def watchlist_add_current() -> None:
        # Přidá aktuálně zobrazený symbol do watchlistu
        provider_id = state.get("provider_id")
        symbol = state.get("symbol")
        if not provider_id or not symbol:
            return
        await cache.watchlist_add(provider_id, symbol)
        await rebuild_watchlist()

    async def rebuild_watchlist() -> None:
        # Přestaví řádky watchlistu podle databáze
        watch_rows.clear()
        watchlist_body.clear()
        items = await cache.watchlist_list()
        with watchlist_body:
            for item in items:
                key = f"{item['source']}:{item['symbol']}"
                with ui.row().classes("watch-row w-full"):
                    ui.label(item["symbol"]).classes("watch-symbol").on(
                        "click", make_watch_click(key)
                    )
                    price_label = ui.label("—").classes("watch-price")
                    change_label = ui.label("—").classes("watch-change")
                    ui.button(
                        icon="close", on_click=make_watch_remove(key)
                    ).props("flat dense size=sm").classes("watch-remove")
                watch_rows[key] = {"price": price_label, "change": change_label}
        await refresh_watchlist_prices()

    async def initial_load() -> None:
        # Po inicializaci grafu obnovíme poslední symbol a výchozí režim kurzoru
        await set_drawing_mode("cursor")
        await load_symbol(last_symbol)

    # ---------- nastavení API klíčů (krok 7 – volitelný zdroj Alpaca) ----------

    async def save_settings() -> None:
        # Uloží klíče do data/config.local.json (nikdy se necommituje)
        config = load_config()
        config["alpaca"] = {
            "endpoint": "https://paper-api.alpaca.markets/v2",
            "api_key": (settings_key.value or "").strip(),
            "secret": (settings_secret.value or "").strip(),
        }
        save_config(config)
        settings_dialog.close()
        show_status(
            "Alpaca klíče uloženy do data/config.local.json – zdroj se aktivuje po restartu aplikace."
        )

    def open_settings_dialog() -> None:
        # Vyplníme aktuální klíče z konfigurace pro kontrolu/přepis
        alpaca_config = load_config().get("alpaca", {})
        settings_key.value = alpaca_config.get("api_key", "")
        settings_secret.value = alpaca_config.get("secret", "")
        settings_dialog.open()

    # ---------- indikátory ----------

    async def refresh_indicators() -> None:
        # Přepočítá všechny aktivní indikátory nad aktuálními svíčkami
        candles = state.get("candles") or []
        payload = compute_indicators(state["indicators"], candles)
        await chart.set_indicators(payload)
        rebuild_indicator_chips()

    def rebuild_indicator_chips() -> None:
        # Lišta čipů aktivních indikátorů (klik na × odstraní)
        indicator_bar.clear()
        if not state["indicators"]:
            indicator_bar.classes(add="hidden")
            return
        indicator_bar.classes(remove="hidden")

        def make_remove(index: int):
            async def _remove(_event):
                del state["indicators"][index]
                await refresh_indicators()

            return _remove

        with indicator_bar:
            ui.label("Indikátory:").classes("indicator-label")
            for index, spec in enumerate(state["indicators"]):
                title = INDICATOR_DEFS[spec["kind"]]["title"] + " " + " ".join(
                    str(v) for v in spec["params"].values()
                )
                ui.button(title, icon="close", on_click=make_remove(index)).props(
                    "flat dense size=sm"
                ).classes("indicator-chip")

    async def submit_indicator() -> None:
        # Přečte parametry z dialogu a přidá indikátor do aktivních
        kind = dialog_state["kind"]
        params = {}
        for name, default in INDICATOR_DEFS[kind]["params"].items():
            value = dialog_params[name].value
            params[name] = default if value is None else value
        state["indicators"].append(
            {"id": uuid.uuid4().hex[:8], "kind": kind, "params": params}
        )
        indicator_dialog.close()
        await refresh_indicators()

    def rebuild_dialog_params(kind: str) -> None:
        # Pole parametrů se mění podle typu indikátoru
        dialog_params.clear()
        dialog_params_container.clear()
        with dialog_params_container:
            for name, default in INDICATOR_DEFS[kind]["params"].items():
                dialog_params[name] = ui.number(
                    PARAM_LABELS.get(name, name), value=default
                ).props("outlined dense")

    def open_indicator_dialog() -> None:
        # Výchozí hodnoty parametrů při každém otevření
        dialog_state["kind"] = "sma"
        dialog_kind_select.value = "sma"
        rebuild_dialog_params("sma")
        indicator_dialog.open()

    # ---------- UI ----------

    with ui.column().classes("page-root w-full"):
        with ui.row().classes("topbar w-full"):
            ui.label("TickView").classes("brand")
            with ui.element("div").classes("search-wrap"):
                search_input = ui.input(placeholder="Hledat symbol (MSFT…)…").classes(
                    "symbol-search"
                ).props("debounce=300 outlined dense")
                suggestions_container = ui.column().classes("autocomplete hidden")
            timeframe_toggle = ui.toggle(options=[], value=None).classes("timeframes")
            source_badge = ui.badge("—").classes("source-badge")
            theme_switch = ui.switch("Tmavý motiv", value=dark)
            ui.button("Indikátor", icon="add_chart", on_click=open_indicator_dialog).props(
                "flat"
            ).classes("indicator-add")
            ui.button(icon="settings", on_click=open_settings_dialog).props("flat dense").classes(
                "settings-button"
            )
        with ui.row().classes("main-area w-full"):
            with ui.column().classes("watchlist-panel"):
                with ui.row().classes("watchlist-header w-full"):
                    ui.label("Watchlist").classes("watchlist-title")
                    ui.button(icon="playlist_add", on_click=watchlist_add_current).props(
                        "flat dense size=sm"
                    ).classes("watch-add")
                watchlist_body = ui.column().classes("watchlist-body w-full")
            with ui.column().classes("content-col"):
                indicator_bar = ui.row().classes("indicator-bar w-full hidden")
                with ui.row().classes("chart-area w-full"):
                    with ui.column().classes("drawing-toolbar"):
                        # Skupinová tlačítka: klik otevře flyout nabídku (JS) a
                        # výběr nástroje přijde zpět přes frontu událostí
                        drawing_buttons = {}
                        for group in DRAWING_GROUPS:
                            drawing_buttons[group["id"]] = (
                                ui.button(icon=group["icon"])
                                .props(f'flat dense data-tool-group={group["id"]}')
                                .classes("drawing-btn")
                                .tooltip(group["tooltip"])
                            )
                        ui.separator().props("vertical").classes("toolbar-sep")
                        # Paleta barev nových kreseb
                        swatch_buttons = {}
                        for index, hex_color in enumerate(DRAWING_COLORS):
                            swatch_buttons[hex_color] = (
                                ui.button(on_click=lambda _, c=hex_color: set_drawing_color(c))
                                .props("flat dense")
                                .classes(f"drawing-btn swatch swatch-{index}")
                                .tooltip("Barva nových kreseb")
                            )
                        ui.separator().props("vertical").classes("toolbar-sep")
                        # Akční tlačítka (zpracovává je on_drawing_finished přes frontu)
                        action_buttons = {}
                        for action, tooltip, icon in [
                            ("magnet", "Magnet (přichytávání k OHLC)", "push_pin"),
                            ("pencil", "Režim kreslení (nástroj zůstává aktivní)", "edit"),
                            ("lock", "Zamknout kresby", "lock"),
                            ("eye", "Skrýt/zobrazit kresby", "visibility"),
                            ("trash", "Smazat označenou / všechny kresby", "delete_sweep"),
                        ]:
                            action_buttons[action] = (
                                ui.button(icon=icon)
                                .props(f'flat dense data-tool-action={action}')
                                .classes("drawing-btn")
                                .tooltip(tooltip)
                            )
                    chart = LightweightChart(dark=dark, on_ready=initial_load)
                    # Definice skupin pro flyout nabídky lišty (může přijít i před init grafu)
                    chart.set_tool_groups(DRAWING_GROUPS)
                status_label = ui.label("").classes("statusbar w-full")

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
        # Po výběru emoji umístí sticker na uloženou pozici kliku
        pending = state.get("sticker_pending")
        sticker_dialog.close()
        if not pending:
            return
        await chart.add_sticker(pending["time"], pending["price"], emoji)
        show_status(f"Sticker {emoji} přidán a uložen.")
        state["sticker_pending"] = None

    # Dialog pro textové kresby (textový nástroj + dvojklik na existující text)
    with ui.dialog() as text_dialog, ui.card().classes("settings-dialog"):
        with ui.column().classes("indicator-dialog-body"):
            ui.label("Text na graf").classes("settings-note")
            text_input = ui.input("Text").props("outlined dense autofocus")
            with ui.row().classes("dialog-actions"):
                ui.button("Uložit", on_click=save_text).props("unelevated")
                ui.button("Zrušit", on_click=text_dialog.close).props("flat")

    # Dialog pro přidání indikátoru; pole parametrů se přestavují podle typu
    dialog_state = {"kind": "sma"}
    dialog_params = {}
    with ui.dialog() as indicator_dialog, ui.card().classes("indicator-dialog"):
        with ui.column().classes("indicator-dialog-body"):
            dialog_kind_select = ui.select(
                {k: v["title"] for k, v in INDICATOR_DEFS.items()},
                value="sma",
                label="Typ indikátoru",
                on_change=lambda event: rebuild_dialog_params(event.value),
            ).props("outlined dense").classes("indicator-kind-select")
            dialog_params_container = ui.column().classes("indicator-dialog-params")
            with ui.row().classes("dialog-actions"):
                ui.button("Přidat", on_click=submit_indicator).props("unelevated")
                ui.button("Zrušit", on_click=indicator_dialog.close).props("flat")

    # Dialog nastavení API klíčů (volitelný zdroj Alpaca)
    with ui.dialog() as settings_dialog, ui.card().classes("settings-dialog"):
        with ui.column().classes("indicator-dialog-body"):
            ui.label(
                "API klíče Alpaca – uloží se lokálně do data/config.local.json "
                "(nikdy se necommitují). Po uložení restartujte aplikaci."
            ).classes("settings-note")
            settings_key = ui.input("API key").props("outlined dense type=password")
            settings_secret = ui.input("Secret").props("outlined dense type=password")
            with ui.row().classes("dialog-actions"):
                ui.button("Uložit", on_click=save_settings).props("unelevated")
                ui.button("Zrušit", on_click=settings_dialog.close).props("flat")

    # ---------- vazby ----------
    # Hledání: psaní hned filtruje návrhy (debounce na QInput), Enter přidá do watchlistu
    search_input.on_value_change(lambda event: search_symbols())
    search_input.on("keydown.enter", add_search_to_watchlist)
    search_input.on("keydown.arrow-down", lambda _e: move_highlight(1))
    search_input.on("keydown.arrow-up", lambda _e: move_highlight(-1))
    search_input.on("keydown.escape", lambda _e: hide_suggestions())
    timeframe_toggle.on_value_change(lambda event: set_timeframe(event.value))
    theme_switch.on_value_change(lambda event: toggle_theme(bool(event.value)))

    # Průběžné vyzvedávání dokončených kreseb z grafu (viz on_drawing_finished)
    ui.timer(0.5, on_drawing_finished)

    # Aktualizace cen watchlistu (Binance 5 s, Yahoo throttlováno na zdroji)
    ui.timer(5.0, refresh_watchlist_prices)

    # Naplnění watchlistu z databáze po načtení stránky
    ui.timer(0.2, rebuild_watchlist, once=True)

    # Při odpojení klienta ukončíme jeho živý stream, aby nezůstal běžet
    context.client.on_disconnect(cancel_stream)
