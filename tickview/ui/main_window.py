"""Hlavní stránka TickView: vyhledání symbolu, timeframy, graf, indikátory, kreslení, živý stream."""

import asyncio
import json
import time
import uuid

from nicegui import context, ui

from tickview.config import load_config, update_config_section
from tickview.logic.cache import KlineCache, fetch_history
from tickview.logic.indicators import (
    INDICATOR_DEFS,
    PARAM_LABELS,
    compute_indicators,
    load_saved_indicators,
    normalize_params,
    tail_payload,
)
from tickview.providers import PROVIDERS, get_provider
from tickview.providers.base import TIMEFRAME_MS, Candle, StreamStatus, is_closed, utc_now_ms
from tickview.ui.lightweight_chart import LightweightChart

# Kolik svíček historie se načte pro jedno zobrazení (a uloží do cache)
DEFAULT_BARS = 600
# Kolik posledních svíček je vidět po načtení grafu
VISIBLE_BARS = 150
# Kolikrát se při donačítání historie zdvojnásobí okno, když je prázdné
# (víkend, noc u akcií); pak se historie považuje za vyčerpanou
OLDER_LOAD_ATTEMPTS = 4
# Jak často nejvýš se z živých svíček přepočítají indikátory (sekundy)
LIVE_INDICATOR_REFRESH_S = 2.0

# Verze statických souborů pro cache-busting v prohlížeči (při změně CSS/JS zvýšit)
STATIC_VERSION = "0.5.3"


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
    # Aktivní indikátory z minula (neplatné položky se vynechají)
    saved_indicators = load_saved_indicators(await cache.get_setting("indicators"))
    # Handle si držíme: každé volání ui.dark_mode() by vytvořilo nový prvek
    dark_mode = ui.dark_mode(dark)

    # Stav zobrazeného symbolu; load_seq chrání proti překříženým odpovědím
    state: dict = {
        "provider_id": None,
        "provider": None,
        "symbol": None,
        "timeframe": last_timeframe,
        "load_seq": 0,
        # Pořadí hledání: pomalejší starší hledání nesmí přepsat novější návrhy
        "search_seq": 0,
        # Zámek řadí vykreslení: starší (pomalejší) odpověď nesmí přepsat
        # novější data po rychlém přepnutí timeframu
        "load_lock": asyncio.Lock(),
        "stream_task": None,
        "badge_live": None,
        "indicators": saved_indicators,
        # Svíčky v grafu (podklad indikátorů); živý stream je průběžně doplňuje
        "candles": None,
        # Monotonic čas posledního přepočtu indikátorů ze živých svíček
        "indicators_refreshed_at": 0.0,
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
        state["search_seq"] += 1
        seq = state["search_seq"]
        state["suggestions"] = []
        state["highlight"] = 0
        if not query:
            hide_suggestions()
            return
        # Zdroje se dotazují souběžně – pomalý zdroj (Yahoo, nedostupná TWS)
        # nesmí zdržet ostatní; pořadí výsledků odpovídá pořadí PROVIDERS
        responses = await asyncio.gather(
            *(provider.search(query) for provider in PROVIDERS), return_exceptions=True
        )
        if seq != state["search_seq"]:
            # Mezitím uživatel psal dál – zastaralé výsledky zahodíme
            return
        results: list[dict] = []
        for provider, found in zip(PROVIDERS, responses):
            if isinstance(found, BaseException):
                show_status(f"Zdroj {provider.name} není dostupný: {found}", error=True)
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

    async def add_key_to_watchlist(key: str) -> None:
        # Přidá symbol "zdroj:symbol" do watchlistu, jen když tam ještě není
        provider_id, _, symbol = key.partition(":")
        if key in watch_rows:
            show_status(f"{symbol} už ve watchlistu je ({provider_id}).")
            return
        await cache.watchlist_add(provider_id, symbol)
        await rebuild_watchlist()
        show_status(f"{symbol} přidán do watchlistu ({provider_id}).")

    def make_load_from_search(key: str):
        # Klik na návrh: přidá do watchlistu (jen když tam ještě není), načte graf
        async def _handler(_event):
            hide_suggestions()
            await add_key_to_watchlist(key)
            await load_symbol(key)

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
        hide_suggestions()
        await add_key_to_watchlist(suggestion["key"])

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
        refresh_watchlist_active()
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

    def merge_live_candle(candle: Candle) -> None:
        # Promítne živou svíčku do state["candles"] (podklad indikátorů):
        # stejná svíčka se nahradí, novější se připojí, starší se ignoruje
        candles = state.get("candles")
        if not candles:
            return
        if candle.open_time == candles[-1].open_time:
            candles[-1] = candle
        elif candle.open_time > candles[-1].open_time:
            candles.append(candle)

    async def push_indicators(tail: bool = False) -> None:
        # Přepočet indikátorů nad state["candles"] (ve vlákně – pandas nad tisíci
        # svíček by zdržel smyčku událostí); tail=True pošle jen poslední body
        # (živý přepočet), jinak celé série
        state["indicators_refreshed_at"] = time.monotonic()
        payload = await asyncio.to_thread(
            compute_indicators, list(state["indicators"]), list(state.get("candles") or [])
        )
        await chart.set_indicators(tail_payload(payload) if tail else payload)

    def show_loaded_status(provider, symbol: str, timeframe: str) -> None:
        # Souhrn načteného grafu ve stavové liště
        count = len(state.get("candles") or [])
        show_status(f"{symbol} · {timeframe} · {count} svíček · zdroj {provider.name}")

    async def consume_stream(provider, symbol: str, timeframe: str, seq: int) -> None:
        """Konzumuje živý stream zdroje: doplňuje svíčky do grafu, cache a indikátorů.

        Stavové zprávy zdroje (výpadek / obnovení spojení) promítá do štítku
        a stavové lišty; díru po výpadku (chybějící svíčky mezi poslední
        známou a právě přišlou) doplní přes cache a historii zdroje.
        """
        step_ms = TIMEFRAME_MS[timeframe]
        live = provider.is_live_for(timeframe)
        market_closed_warned = False
        try:
            async for item in provider.stream(symbol, timeframe):
                if seq != state["load_seq"]:
                    # Mezitím se přepnulo zobrazení – stream ukončíme
                    return
                if isinstance(item, StreamStatus):
                    # Výpadek / obnovení spojení se zdrojem
                    set_badge(live if item.connected else False)
                    if item.connected:
                        show_status(f"{symbol}: {item.message}")
                    else:
                        show_status(
                            f"Živý stream {symbol} přerušen, čekám na znovupřipojení: {item.message}",
                            error=True,
                        )
                    continue
                candle = item
                set_badge(live)
                batch = [candle]
                known = state.get("candles") or []
                new_candle = bool(known) and candle.open_time > known[-1].open_time
                if known and candle.open_time > known[-1].open_time + step_ms:
                    # Mezi poslední známou a novou svíčkou chybí data (výpadek
                    # websocketu, uspání počítače…) – doplníme je přes cache
                    last_open = known[-1].open_time
                    try:
                        gap = await fetch_history(
                            provider, cache, symbol, timeframe, last_open + step_ms, candle.open_time - 1
                        )
                    except Exception:
                        # Doplnění se nepovedlo – díru doplní příští načtení grafu
                        gap = []
                    if seq != state["load_seq"]:
                        return
                    batch = [c for c in gap if last_open < c.open_time < candle.open_time] + batch
                # Graf jedním voláním, podklad indikátorů a cache (jen uzavřená
                # živá svíčka; doplněnou díru uložil už fetch_history)
                await chart.update_candles(batch)
                for batch_candle in batch:
                    merge_live_candle(batch_candle)
                now_ms = utc_now_ms()
                if is_closed(candle, step_ms, now_ms):
                    await cache.store(provider.id, symbol, timeframe, [candle])
                # Indikátory: po doplnění díry celé, jinak jen poslední body a
                # nejvýš jednou za LIVE_INDICATOR_REFRESH_S (nová svíčka hned)
                if state["indicators"]:
                    if len(batch) > 1:
                        await push_indicators()
                    elif new_candle or time.monotonic() - state["indicators_refreshed_at"] >= LIVE_INDICATOR_REFRESH_S:
                        await push_indicators(tail=True)
                # Nejnovější svíčka je starší než celé další období → nová
                # nepřibývá, trh je pravděpodobně zavřený (hlásíme jen jednou).
                # Uzavřená svíčka sama o sobě nestačí: websocket (Binance, Alpaca)
                # ji posílá těsně po konci období i při otevřeném trhu.
                if not market_closed_warned and candle.open_time + 2 * step_ms <= now_ms:
                    market_closed_warned = True
                    show_status(
                        f"{symbol}: trh je pravděpodobně zavřený – živá data nepřibývají"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Neočekávaná chyba mimo zdroj (zdroje výpadky hlásí přes StreamStatus)
            if seq == state["load_seq"]:
                set_badge(False)
                show_status(f"Živý stream ukončen chybou: {exc}", error=True)

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
            # Vykreslení držíme pod zámkem a číslo požadavku kontrolujeme znovu
            # uvnitř: data do grafu smí zapsat jen nejnovější načtení
            async with state["load_lock"]:
                if seq != state["load_seq"]:
                    return
                await chart.set_candles(candles, TIMEFRAME_MS[timeframe], visible_bars=VISIBLE_BARS)
                state["candles"] = candles
                # Přepočet aktivních indikátorů nad novými daty
                await refresh_indicators()
            # Kresby patří k symbolu/timeframu – načteme uložené
            await reload_drawings()
            if seq != state["load_seq"]:
                return
            show_loaded_status(provider, symbol, timeframe)
            # Historie načtena → spustíme živý stream pro aktuální zobrazení
            start_stream()
        except Exception as exc:
            show_status(f"Načtení dat selhalo: {exc}", error=True)

    async def load_older(before_s) -> None:
        """Donačte starší svíčky před první svíčkou grafu (posun grafu doleva).

        Prázdné okno (víkend, noc u akcií) se posune dál do minulosti a
        zdvojnásobí; když nic nevrátí ani poslední pokus, graf historii označí
        za vyčerpanou.
        """
        provider = state.get("provider")
        symbol = state.get("symbol")
        timeframe = state.get("timeframe")
        seq = state["load_seq"]
        if provider is None or not symbol or before_s is None:
            return
        step_ms = TIMEFRAME_MS[timeframe]
        end_ms = int(before_s) * 1000 - 1
        span_ms = DEFAULT_BARS * step_ms
        older: list[Candle] = []
        try:
            for _ in range(OLDER_LOAD_ATTEMPTS):
                older = await fetch_history(provider, cache, symbol, timeframe, end_ms - span_ms, end_ms)
                if older or seq != state["load_seq"]:
                    break
                # Prázdný úsek už znovu nestahujeme – další pokus začne před ním
                end_ms -= span_ms
                span_ms *= 2
        except Exception as exc:
            if seq == state["load_seq"]:
                show_status(f"Starší data {symbol} se nepodařilo načíst: {exc}", error=True)
                # Nic nepřidáno, historie ale není vyčerpaná – graf to zkusí později
                await chart.prepend_candles([], exhausted=False)
            return
        async with state["load_lock"]:
            if seq != state["load_seq"]:
                return
            await chart.prepend_candles(older, exhausted=not older)
            if not older:
                show_status(f"{symbol} ({timeframe}): starší data nejsou k dispozici.")
                return
            first_open = state["candles"][0].open_time if state.get("candles") else None
            state["candles"] = [
                c for c in older if first_open is None or c.open_time < first_open
            ] + (state.get("candles") or [])
            if state["indicators"]:
                await push_indicators()
            show_loaded_status(provider, symbol, timeframe)

    def start_load_older(before_s) -> None:
        # Donačítání běží na pozadí, aby nezdrželo zpracování ostatních událostí grafu
        async def run() -> None:
            try:
                await load_older(before_s)
            except Exception as exc:
                show_status(f"Starší data se nepodařilo načíst: {exc}", error=True)

        asyncio.create_task(run())

    async def toggle_theme(value: bool) -> None:
        dark_mode.set_value(value)
        await chart.set_theme(value)
        await cache.set_setting("theme", "dark" if value else "light")

    # ---------- kreslení (lišta ve stylu TradingView) ----------

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
        # Aktivní nástroj zvýrazníme na skupině, do které patří (jako v TradingView);
        # None (JS po dokončení kresby vrací kurzor) znamená režim kurzoru
        mode = mode or "cursor"
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
        await chart.set_drawing_mode(None if mode == "cursor" else mode)

    async def set_drawing_color(color: str) -> None:
        # Barva nových kreseb; aktivní paletu zvýrazníme
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
        # Koš: smaže označenou kresbu, jinak všechny; do databáze se smazání
        # propíše přes události "delete" ve frontě grafu (viz on_drawing_finished)
        removed = await chart.trash_drawings()
        if removed:
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

    def drawing_points(payload: dict) -> list[dict]:
        # Body kresby z události grafu (čas v sekundách UTC + cena)
        return [
            {"time": int(p["time"]), "price": float(p["price"])}
            for p in (payload.get("points") or [])
        ]

    async def on_drawing_finished() -> None:
        # Stránkový timer vyzvedne události z grafu (kresby, nástroje, akce,
        # donačtení historie) a projeví je v databázi a v UI. Graf má stav kreseb
        # aktuální sám – nové kresbě jen pošleme id z databáze, nic se nenačítá znovu
        provider_id = state.get("provider_id")
        symbol = state.get("symbol")
        timeframe = state.get("timeframe")
        if not provider_id or not symbol:
            return
        for payload in await chart.pop_events():
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "load_older":
                # Graf se posunul k začátku dat – donačteme starší svíčky
                start_load_older(payload.get("before"))
            elif payload.get("type") == "tool":
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
                    show_status("Kresba smazána.")
            elif payload.get("type") == "update":
                drawing_id = payload.get("id")
                if drawing_id:
                    await cache.update_drawing(
                        provider_id, symbol, timeframe, int(drawing_id),
                        drawing_points(payload), payload.get("text"),
                    )
            elif payload.get("type") == "text_request":
                await open_text_dialog(payload.get("time"), payload.get("price"))
            elif payload.get("type") == "text_edit" and payload.get("id"):
                # Bez id (kresba ještě není v DB) by se uložení chovalo jako nový text na (0, 0)
                await open_text_dialog(0, 0.0, edit_id=payload.get("id"), current=payload.get("text") or "")
            elif payload.get("type") == "add" and payload.get("kind") in DRAWING_KINDS:
                drawing_id = await cache.add_drawing(
                    provider_id,
                    symbol,
                    timeframe,
                    payload["kind"],
                    drawing_points(payload),
                    payload.get("color"),
                    payload.get("text"),
                )
                # Graf si kresbu spáruje podle lokálního id (další úpravy už jdou s id)
                if payload.get("localId") is not None:
                    await chart.assign_drawing_id(payload["localId"], drawing_id)
                show_status(
                    f"Kresba uložena ({DRAWING_LABELS[payload['kind']]}) k {symbol} ({timeframe})"
                )

    # ---------- watchlist ----------

    # Krátké kódy zdrojů pro watchlist (aby sloupec nebyl široký)
    PROVIDER_CODES = {"binance": "BIN", "yahoo": "YAH", "alpaca": "ALP", "ibkr": "IBK"}

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
        await add_key_to_watchlist(f"{provider_id}:{symbol}")

    async def rebuild_watchlist() -> None:
        # Přestaví řádky watchlistu podle databáze
        watch_rows.clear()
        watchlist_body.clear()
        items = await cache.watchlist_list()
        with watchlist_body:
            for item in items:
                key = f"{item['source']}:{item['symbol']}"
                code = PROVIDER_CODES.get(item["source"], item["source"][:3].upper())
                with ui.row().classes("watch-row w-full") as watch_row_element:
                    ui.label(code).classes("watch-code").tooltip(item["source"])
                    ui.label(item["symbol"]).classes("watch-symbol").on(
                        "click", make_watch_click(key)
                    )
                    price_label = ui.label("—").classes("watch-price")
                    change_label = ui.label("—").classes("watch-change")
                    ui.button(
                        icon="close", on_click=make_watch_remove(key)
                    ).props("flat dense size=sm").classes("watch-remove")
                watch_rows[key] = {"row": watch_row_element, "price": price_label, "change": change_label}
        refresh_watchlist_active()
        await refresh_watchlist_prices()

    def refresh_watchlist_active() -> None:
        # Zvýrazní řádek aktuálně zobrazeného tickeru (jako v TradingView)
        current = (state.get("provider_id"), state.get("symbol"))
        for key, row in watch_rows.items():
            provider_id, _, symbol = key.partition(":")
            active = (provider_id, symbol) == current
            element = row["row"]
            element.classes(
                add="watch-row-active" if active else "",
                remove="" if active else "watch-row-active",
            )

    async def initial_load() -> None:
        # Po inicializaci grafu obnovíme poslední symbol a výchozí režim kurzoru
        await set_drawing_mode("cursor")
        await load_symbol(last_symbol)

    # ---------- nastavení API klíčů (volitelný zdroj Alpaca) ----------

    async def save_settings() -> None:
        # Uloží klíče do config.yaml v kořenu projektu (necommituje se); zbytek
        # souboru včetně komentářů a vlastního endpointu zůstane beze změny
        values = {
            "api_key": (settings_key.value or "").strip(),
            "secret": (settings_secret.value or "").strip(),
        }
        if not (load_config().get("alpaca") or {}).get("endpoint"):
            values = {"endpoint": "https://paper-api.alpaca.markets/v2", **values}
        update_config_section("alpaca", values)
        settings_dialog.close()
        show_status(
            "Alpaca klíče uloženy do config.yaml – zdroj se aktivuje po restartu aplikace."
        )

    def open_settings_dialog() -> None:
        # Vyplníme aktuální klíče z konfigurace pro kontrolu/přepis
        alpaca_config = load_config().get("alpaca", {})
        settings_key.value = alpaca_config.get("api_key", "")
        settings_secret.value = alpaca_config.get("secret", "")
        settings_dialog.open()

    # ---------- indikátory ----------

    async def refresh_indicators() -> None:
        # Přepočítá všechny aktivní indikátory a přestaví lištu jejich čipů
        await push_indicators()
        rebuild_indicator_chips()

    async def save_indicators() -> None:
        # Aktivní indikátory přetrvají restart (nastavení v SQLite)
        await cache.set_setting("indicators", json.dumps(state["indicators"]))

    def rebuild_indicator_chips() -> None:
        # Lišta čipů aktivních indikátorů (klik na × odstraní)
        indicator_bar.clear()
        if not state["indicators"]:
            indicator_bar.classes(add="hidden")
            return
        indicator_bar.classes(remove="hidden")

        def make_remove(indicator_id: str):
            async def _remove(_event):
                state["indicators"] = [
                    spec for spec in state["indicators"] if spec["id"] != indicator_id
                ]
                await save_indicators()
                await refresh_indicators()

            return _remove

        with indicator_bar:
            ui.label("Indikátory:").classes("indicator-label")
            for spec in state["indicators"]:
                title = INDICATOR_DEFS[spec["kind"]]["title"] + " " + " ".join(
                    str(v) for v in spec["params"].values()
                )
                ui.button(title, icon="close", on_click=make_remove(spec["id"])).props(
                    "flat dense size=sm"
                ).classes("indicator-chip")

    async def submit_indicator() -> None:
        # Přečte a ověří parametry z dialogu a přidá indikátor do aktivních;
        # při neplatné hodnotě dialog zůstane otevřený s upozorněním
        kind = dialog_state["kind"]
        try:
            params = normalize_params(
                kind, {name: field.value for name, field in dialog_params.items()}
            )
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return
        state["indicators"].append(
            {"id": uuid.uuid4().hex[:8], "kind": kind, "params": params}
        )
        indicator_dialog.close()
        await save_indicators()
        await refresh_indicators()

    def rebuild_dialog_params(kind: str) -> None:
        # Pole parametrů se mění podle typu indikátoru; zvolený typ si
        # zapamatujeme pro submit_indicator
        dialog_state["kind"] = kind
        dialog_params.clear()
        dialog_params_container.clear()
        with dialog_params_container:
            for name, default in INDICATOR_DEFS[kind]["params"].items():
                dialog_params[name] = ui.number(
                    PARAM_LABELS.get(name, name), value=default
                ).props("outlined dense")

    def open_indicator_dialog() -> None:
        # Výchozí hodnoty parametrů při každém otevření
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
                "API klíče Alpaca – uloží se lokálně do config.yaml v kořenu projektu "
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
