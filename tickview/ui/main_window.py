"""Hlavní stránka TickView: vyhledání symbolu, timeframy, graf, indikátory, kreslení, živý stream."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

from nicegui import context, ui

from tickview.config import load_config, update_config_section
from tickview.logic.cache import KlineCache, fetch_history
from tickview.logic.indicators import (
    CATEGORIES,
    INDICATOR_DEFS,
    PARAM_LABELS,
    compute_indicators,
    indicator_color,
    indicator_title,
    load_saved_indicators,
    new_indicator,
    normalize_params,
    tail_payload,
)
from tickview.logic.styles import (
    CHART_DEFAULTS,
    load_chart_style,
    load_favorite_tools,
    load_panel_position,
    normalize_chart_style,
    normalize_color,
    normalize_favorite_tools,
    normalize_panel_position,
)
from tickview.logic.sessions import (
    SESSION_ETH,
    SESSION_RTH,
    filter_session,
    in_session,
    timezone_options,
)
from tickview.providers import PROVIDERS, get_provider
from tickview.providers.base import TIMEFRAME_MS, Candle, StreamStatus, is_closed, utc_now_ms
from tickview.ui.color_button import ColorButton
from tickview.ui.lightweight_chart import LightweightChart
from tickview.ui.templates import TemplateMenu, load_default_template, settings_buttons, settings_header

# Kolik svíček historie se načte pro jedno zobrazení (a uloží do cache)
DEFAULT_BARS = 600
# Kolik posledních svíček je vidět po načtení grafu
VISIBLE_BARS = 150
# Kolikrát se při donačítání historie zdvojnásobí okno, když je prázdné
# (víkend, noc u akcií); pak se historie považuje za vyčerpanou
OLDER_LOAD_ATTEMPTS = 4
# Jak často nejvýš se z živých svíček přepočítají indikátory (sekundy)
LIVE_INDICATOR_REFRESH_S = 2.0
# Tovární styl kreseb: barva (nové kresby, „Apply defaults“), šířka čáry a písmo
DEFAULT_DRAWING_COLOR = "#2962ff"
DEFAULT_LINE_WIDTH = 1.5
DEFAULT_FONT_SIZE = 13
# Za jak dlouho po poslední změně barvy v liště se barva uloží (tažení
# průhlednosti posílá změny průběžně – do databáze stačí výsledná)
DRAWING_COLOR_SAVE_DELAY_S = 0.5


def clamp_font_size(value) -> int:
    """Velikost písma textové kresby v rozsahu 8–72 px (neplatná hodnota = výchozí)."""
    try:
        return max(8, min(72, int(value or DEFAULT_FONT_SIZE)))
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE



# Složka statických souborů UI (styly, JS grafu, vendored knihovna)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _static_version() -> str:
    """Verze statických souborů pro cache-busting: otisk obsahu CSS a JS.

    Změna kteréhokoli souboru (po restartu serveru) změní URL, takže prohlížeč
    nepoužije starou verzi z cache – verzi není potřeba ručně zvyšovat.
    """
    digest = hashlib.sha1()
    for name in ("style.css", "color_picker.js", "lightweight_chart.js", "lightweight-charts.standalone.production.js"):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:10]


# Verze se počítá jednou při startu (ne při každém načtení stránky)
STATIC_VERSION = _static_version()


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
        f'<script src="/static/color_picker.js?v={STATIC_VERSION}"></script>'
        f'<script src="/static/lightweight_chart.js?v={STATIC_VERSION}"></script>'
    )

    # Nastavení z minula: motiv, poslední symbol a timeframe
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
    # Barva nových kreseb (tlačítko barvy v liště)
    saved_drawing_color = normalize_color(await cache.get_setting("drawing_color")) or DEFAULT_DRAWING_COLOR
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
        # Název a burza zobrazeného instrumentu pro nadpis grafu
        "symbol_meta": None,
        "show_volume": show_volume,
        "chart_style": chart_style,
        "timezone": saved_timezone,
        "session": saved_session,
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
        # Přesná shoda > prefix > výskyt; celkový počet návrhů omezíme na 12.
        # Porovnává se bez oddělovačů, aby "EURUSD" našlo i EUR.USD a EURUSD=X
        def compact(text: str) -> str:
            return "".join(ch for ch in text.upper() if ch.isalnum())

        upper = compact(query)

        def relevance(result: dict) -> int:
            symbol = compact(result["symbol"])
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
        # Pásmo burzy (volba Exchange) a dostupnost přepínače RTH/ETH
        chart.set_timezone(state["timezone"], provider.exchange_timezone(symbol))
        refresh_session_button()
        # Nadpis grafu: hned symbol, název a burzu doplní zdroj na pozadí
        # (zdroje si metadata cachují samy)
        key = f"{provider_id}:{symbol}"
        state["symbol_meta"] = None
        push_symbol_info()
        asyncio.create_task(describe_symbol(provider, symbol, key))
        await cache.set_setting("last_symbol", key)
        await reload_chart()

    def push_symbol_info() -> None:
        # Nadpis tickeru v legendě grafu: „Název · timeframe · burza“
        provider = state.get("provider")
        symbol = state.get("symbol")
        if provider is None or not symbol:
            return
        meta = state.get("symbol_meta") or {}
        chart.set_symbol_info(
            {
                "symbol": symbol,
                "name": meta.get("name") or symbol,
                "exchange": meta.get("exchange") or provider.name,
                "timeframe": state["timeframe"],
                "source": provider.name,
            }
        )

    async def describe_symbol(provider, symbol: str, key: str) -> None:
        # Název a burzu zjistí zdroj (může trvat – Yahoo, TWS); výsledek se
        # použije jen tehdy, když je symbol pořád zobrazený
        try:
            meta = await provider.describe(symbol)
        except Exception:
            # Bez metadat zůstane v nadpisu symbol a název zdroje
            return
        if f"{state.get('provider_id')}:{state.get('symbol')}" == key:
            state["symbol_meta"] = meta
            push_symbol_info()

    async def set_timeframe(value) -> None:
        if not value:
            return
        state["timeframe"] = str(value)
        push_symbol_info()
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
        # Barevná tečka stavu v nadpisu grafu (živě / zpožděno / bez dat)
        chart.set_legend_status("none" if live is None else "live" if live else "delayed")

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

    def active_session():
        # Hlavní seance aktuálního instrumentu, když je zvolené RTH (jinak None)
        provider = state.get("provider")
        if state["session"] != SESSION_RTH or provider is None or not state.get("symbol"):
            return None
        return provider.regular_session(state["symbol"])

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
                    # Výpadek / obnovení spojení se zdrojem; zdroj může zároveň
                    # upřesnit, zda jsou data živá (IBKR podle předplatného)
                    if item.live is not None:
                        live = item.live
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
                session = active_session()
                if session and not in_session(candle, timeframe, session):
                    # RTH: svíčky pre-market / after-hours do grafu nepatří, do
                    # cache ano (uzavřené) – pro přepnutí na ETH
                    if is_closed(candle, step_ms, utc_now_ms()):
                        await cache.store(provider.id, symbol, timeframe, [candle])
                    continue
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
                    gap = filter_session(gap, timeframe, session)
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
            # RTH: jen svíčky hlavní obchodní seance (ETH = vše, co zdroj poslal)
            candles = filter_session(candles, timeframe, active_session())
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
                await push_indicators()
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
                older = filter_session(older, timeframe, active_session())
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
    # Skupiny nástrojů lišty (jako v TradingView): id, ikona, tooltip, nástroje;
    # položka {"section": …} je jen nadpis sekce ve flyout nabídce
    DRAWING_GROUPS = [
        {"id": "g_cursor", "icon": "near_me", "tooltip": "Kurzor (výběr a tažení kreseb)",
         "tools": [{"mode": "cursor", "tooltip": "Kurzor", "icon": "near_me"}]},
        {"id": "g_lines", "icon": "show_chart", "tooltip": "Čáry, kanály a pitchforky",
         "tools": [
             {"section": "Čáry"},
             {"mode": "trend", "tooltip": "Trendová čára", "icon": "show_chart"},
             {"mode": "ray", "tooltip": "Polopřímka", "icon": "trending_up"},
             {"mode": "extended", "tooltip": "Přímka (oboustranná)", "icon": "sync_alt"},
             {"mode": "arrow", "tooltip": "Trendová šipka", "icon": "north_east"},
             {"mode": "hline", "tooltip": "Horizontální linie", "icon": "horizontal_rule"},
             {"mode": "hray", "tooltip": "Horizontální polopřímka", "icon": "trending_flat"},
             {"mode": "vline", "tooltip": "Svislá linie", "icon": "height"},
             {"mode": "cross", "tooltip": "Kříž", "icon": "add"},
             {"section": "Kanály"},
             {"mode": "channel", "tooltip": "Paralelní kanál (3 kliky)", "icon": "view_stream"},
             {"section": "Pitchforky"},
             {"mode": "pitchfork", "tooltip": "Pitchfork (3 kliky)", "icon": "gesture"},
         ]},
        {"id": "g_fib", "icon": "align_vertical_center", "tooltip": "Fibonacci",
         "tools": [
             {"mode": "fib", "tooltip": "Fibonacci retracement", "icon": "align_vertical_center"},
             {"mode": "fibext", "tooltip": "Fibonacci extension", "icon": "linear_scale"},
         ]},
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
    }
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
            group_modes = [tool["mode"] for tool in group["tools"] if "mode" in tool]
            button.classes(
                add="drawing-btn-active" if mode in group_modes else "",
                remove="" if mode in group_modes else "drawing-btn-active",
            )
        await chart.set_drawing_mode(None if mode == "cursor" else mode)

    async def set_drawing_color(color) -> None:
        # Barva nových kreseb (tlačítko barvy v liště): do grafu hned, do
        # databáze až po DRAWING_COLOR_SAVE_DELAY_S bez další změny
        color = normalize_color(color)
        if not color:
            return
        await chart.set_drawing_color(color)
        pending = state.get("drawing_color_save")
        if pending and not pending.done():
            pending.cancel()

        async def save_later() -> None:
            await asyncio.sleep(DRAWING_COLOR_SAVE_DELAY_S)
            await cache.set_setting("drawing_color", color)

        state["drawing_color_save"] = asyncio.create_task(save_later())

    # ---------- oblíbené nástroje (plovoucí panel) ----------

    async def save_favorites(modes) -> None:
        # Oblíbené nástroje z hvězdiček ve flyoutu (JS je už zobrazil)
        favorites = normalize_favorite_tools(modes, TOOL_MODES)
        await cache.set_setting("favorite_tools", json.dumps(favorites))
        show_status(f"Oblíbené nástroje: {len(favorites)}." if favorites else "Žádné oblíbené nástroje.")

    async def save_favorites_position(left, top) -> None:
        # Pozice plovoucího panelu po přetažení (px vůči grafu)
        position = normalize_panel_position({"left": left, "top": top})
        if position:
            await cache.set_setting("favorites_position", json.dumps(position))

    def paint_favorites_button() -> None:
        # Ikona hvězdy v liště: plná = panel oblíbených zobrazen
        action_buttons["favorites"].props(f'icon={"star" if state["favorites_visible"] else "star_border"}')

    async def toggle_favorites_bar() -> None:
        # Tlačítko hvězdy v liště: zobrazí / skryje plovoucí panel oblíbených
        state["favorites_visible"] = not state.get("favorites_visible", True)
        visible = state["favorites_visible"]
        paint_favorites_button()
        chart.set_favorites_visible(visible)
        await cache.set_setting("favorites_visible", "1" if visible else "0")
        show_status(f"Panel oblíbených nástrojů {'zobrazen' if visible else 'skryt'}.")

    async def restore_favorites() -> None:
        # Oblíbené nástroje, pozice a viditelnost panelu z minula (po init grafu)
        favorites = load_favorite_tools(await cache.get_setting("favorite_tools"), TOOL_MODES)
        position = load_panel_position(await cache.get_setting("favorites_position"))
        state["favorites_visible"] = (await cache.get_setting("favorites_visible", "1")) != "0"
        paint_favorites_button()
        chart.set_favorites(favorites, position, state["favorites_visible"])

    # Popisy režimů magnetu pro stavovou lištu
    MAGNET_LABELS = {
        "off": "vypnut",
        "weak": "slabý – bod kresby se přichytí k O/H/L/C svíčky, když je kurzor blízko",
        "strong": "silný – body kreseb se vždy přichytí k nejbližší O/H/L/C svíčky",
    }

    async def set_magnet(mode, announce: bool = True) -> None:
        # Magnet: přichytávání bodů kreseb k OHLC svíčky pod kurzorem; režim se
        # ukládá a přetrvá restart
        mode = mode if mode in MAGNET_LABELS else "off"
        active = mode != "off"
        button = action_buttons["magnet"]
        button.classes(add="drawing-btn-active" if active else "", remove="" if active else "drawing-btn-active")
        await chart.set_magnet(mode)
        # Obnova uloženého režimu při načtení stránky ho znovu neukládá
        if announce:
            await cache.set_setting("magnet", mode)
            show_status(f"Magnet {MAGNET_LABELS[mode]}.")

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

    async def open_text_dialog(time_s: int, price: float) -> None:
        # Dialog pro vložení textu na plátno (anchora v čase a ceně); úprava
        # existujícího textu jde přes Settings kresby
        state["text_pending"] = {"time": int(time_s), "price": float(price)}
        text_input.value = ""
        text_dialog.open()

    async def save_text() -> None:
        pending = state.get("text_pending")
        value = (text_input.value or "").strip()
        text_dialog.close()
        if not pending or not value:
            return
        await chart.add_text(pending["time"], pending["price"], value)
        show_status("Text přidán a uložen.")
        state["text_pending"] = None

    # ---------- Settings kresby a grafu (pravý klik / dvojklik) ----------

    # Styly čar pro dialog Settings kresby (hodnota v JS → popisek)
    LINE_STYLES = {"solid": "Plná", "dashed": "Přerušovaná", "dotted": "Tečkovaná"}
    # Šířky čar v px
    LINE_WIDTHS = {1: "1 px", 1.5: "1,5 px (výchozí)", 2: "2 px", 3: "3 px", 4: "4 px"}
    # Kresby bez čáry (šířka a styl nemají smysl)
    NO_LINE_KINDS = {"text", "sticker"}

    def apply_drawing_template(values: dict) -> None:
        # Promítne hodnoty šablony (barva, šířka a styl čáry, velikost písma)
        # do polí dialogu; neplatné nebo chybějící hodnoty nahradí výchozími
        options = values.get("options") if isinstance(values.get("options"), dict) else {}
        drawing_color.value = normalize_color(values.get("color")) or DEFAULT_DRAWING_COLOR
        width = options.get("width")
        drawing_width.value = width if width in LINE_WIDTHS else DEFAULT_LINE_WIDTH
        drawing_style.value = options.get("lineStyle") if options.get("lineStyle") in LINE_STYLES else "solid"
        drawing_font.value = clamp_font_size(options.get("fontSize"))

    def apply_drawing_factory() -> None:
        # „Apply defaults“: tovární styl kresby
        apply_drawing_template({"color": DEFAULT_DRAWING_COLOR, "options": {}})

    def collect_drawing_settings() -> dict | None:
        # Styl kresby z polí dialogu (bez textu – ten do šablony nepatří)
        current = state.get("drawing_settings")
        if not current:
            return None
        kind = current["kind"]
        options: dict = {}
        if kind not in NO_LINE_KINDS:
            options["width"] = float(drawing_width.value or DEFAULT_LINE_WIDTH)
            options["lineStyle"] = drawing_style.value or "solid"
        if kind == "text":
            options["fontSize"] = clamp_font_size(drawing_font.value)
        return {"color": normalize_color(drawing_color.value) or DEFAULT_DRAWING_COLOR, "options": options}

    async def open_drawing_settings(payload: dict) -> None:
        # Dialog Settings kresby podle jejího typu: barva, šířka a styl čáry,
        # u textu obsah a velikost písma; šablony podle typu kresby
        kind = payload.get("kind")
        state["drawing_settings"] = {"id": int(payload["id"]), "kind": kind}
        drawing_settings_title.text = DRAWING_LABELS.get(kind, kind)
        apply_drawing_template({"color": payload.get("color"), "options": payload.get("options") or {}})
        has_line = kind not in NO_LINE_KINDS
        drawing_color_row.set_visibility(kind != "sticker")
        drawing_width.set_visibility(has_line)
        drawing_style.set_visibility(has_line)
        drawing_text.value = payload.get("text") or ""
        drawing_text.set_visibility(kind == "text")
        drawing_font.set_visibility(kind == "text")
        # Sticker nemá nastavitelný styl – šablony nedávají smysl
        drawing_templates.set_visible(kind != "sticker")
        await drawing_templates.refresh()
        drawing_settings_dialog.open()

    async def submit_drawing_settings() -> None:
        # Uloží nastavení kresby do databáze a promítne ho do grafu
        current = state.get("drawing_settings")
        provider_id, symbol, timeframe = state.get("provider_id"), state.get("symbol"), state.get("timeframe")
        values = collect_drawing_settings()
        if not current or not provider_id or values is None:
            drawing_settings_dialog.close()
            return
        kind = current["kind"]
        text = None
        if kind == "text":
            text = (drawing_text.value or "").strip()
            if not text:
                ui.notify("Text nesmí být prázdný.", type="warning")
                return
        # Sticker barvu nemá (emoji) – necháme ji prázdnou jako dosud
        color = values["color"] if kind != "sticker" else None
        options = values["options"]
        drawing_settings_dialog.close()
        await cache.update_drawing_style(provider_id, symbol, timeframe, current["id"], color, text, options)
        chart.apply_drawing_settings(current["id"], {"color": color, "text": text, "options": options})
        show_status(f"{DRAWING_LABELS.get(kind, kind)}: nastavení uloženo.")

    async def set_volume_visible(visible: bool) -> None:
        # Zobrazení objemu (Settings grafu / oko v legendě); přetrvá restart.
        # Stejná hodnota (i programové nastavení přepínače) nic nedělá
        if bool(visible) == state["show_volume"]:
            return
        state["show_volume"] = bool(visible)
        chart.set_volume_visible(state["show_volume"])
        await cache.set_setting("show_volume", "1" if state["show_volume"] else "0")

    # ---------- časové pásmo a obchodní seance (stavová lišta) ----------

    def mark_active(items: dict, active: str) -> None:
        # Zvýrazní zvolenou položku nabídky (ostatním zvýraznění odebere)
        for value, item in items.items():
            item.classes(add="menu-item-active" if value == active else "", remove="" if value == active else "menu-item-active")

    def make_timezone_click(value: str):
        async def _handler() -> None:
            await set_timezone(value)

        return _handler

    async def set_timezone(value: str) -> None:
        # Časové pásmo osy, crosshairu a hodin; uloží se a přetrvá restart
        state["timezone"] = value
        mark_active(timezone_items, value)
        provider = state.get("provider")
        exchange = provider.exchange_timezone(state["symbol"]) if provider and state.get("symbol") else "UTC"
        chart.set_timezone(value, exchange)
        await cache.set_setting("timezone", value)

    def refresh_session_button() -> None:
        # Popisek RTH/ETH; u instrumentu bez rozšířených hodin (krypto, forex,
        # Yahoo) přepínač nemá vliv, proto je neaktivní
        provider = state.get("provider")
        available = bool(provider and state.get("symbol") and provider.regular_session(state["symbol"]))
        session_button.text = "RTH" if state["session"] == SESSION_RTH else "ETH"
        if available:
            session_button.props(remove="disable")
        else:
            session_button.props("disable")
        mark_active(session_items, state["session"])

    async def set_session(value: str) -> None:
        # Přepnutí seance: uloží volbu a přenačte graf (RTH filtruje svíčky)
        if value == state["session"]:
            return
        state["session"] = value
        await cache.set_setting("session", value)
        refresh_session_button()
        await reload_chart()

    # ---------- Settings grafu (styl svíček, objem, šablony) ----------

    # Stav dialogu: „filling“ potlačí náhled při hromadném vyplňování polí,
    # „dirty“ = graf ukazuje neuložený náhled (zavření bez Ok ho vrátí)
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

    def preview_chart_style() -> None:
        # Živý náhled v grafu (jako v TradingView); uloží se až tlačítkem Ok
        if chart_dialog_state["filling"]:
            return
        values = collect_chart_values()
        chart_dialog_state["dirty"] = True
        chart.set_chart_style(normalize_chart_style(values))
        chart.set_volume_visible(values["volume"])

    async def open_chart_settings() -> None:
        # Pole se vyplní uloženým stavem bez náhledu (graf ho už ukazuje)
        fill_chart_values({**state["chart_style"], "volume": state["show_volume"]}, preview=False)
        chart_dialog_state["dirty"] = False
        await chart_templates.refresh()
        chart_settings_dialog.open()

    async def submit_chart_settings() -> None:
        # Ok: styl svíček uloží do nastavení, objem přes set_volume_visible;
        # graf už ukazuje potvrzené hodnoty, zavření nic nevrací
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
        chart.set_chart_style(state["chart_style"])
        chart.set_volume_visible(state["show_volume"])

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
            elif payload.get("type") == "indicator":
                await on_indicator_event(payload)
            elif payload.get("type") == "tool":
                await set_drawing_mode(payload.get("mode"))
            elif payload.get("type") == "action":
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
            elif payload.get("type") == "favorites":
                await save_favorites(payload.get("modes"))
            elif payload.get("type") == "favorites_position":
                await save_favorites_position(payload.get("left"), payload.get("top"))
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
            elif payload.get("type") == "drawing_settings" and payload.get("id"):
                await open_drawing_settings(payload)
            elif payload.get("type") == "chart_settings":
                await open_chart_settings()
            elif payload.get("type") == "volume":
                await set_volume_visible(bool(payload.get("visible")))
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
        await set_magnet(saved_magnet, announce=False)
        await chart.set_drawing_color(saved_drawing_color)
        await restore_favorites()
        mark_active(timezone_items, state["timezone"])
        refresh_session_button()
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

    async def save_indicators() -> None:
        # Aktivní indikátory přetrvají restart (nastavení v SQLite)
        await cache.set_setting("indicators", json.dumps(state["indicators"]))

    async def indicators_changed() -> None:
        # Po každé změně sady indikátorů: uložit, přepočítat, obnovit nabídku
        await save_indicators()
        await push_indicators()
        # Nabídku přestavujeme jen otevřenou (jinak se postaví při otevření)
        if indicator_dialog.value:
            render_indicator_menu()

    def find_indicator(indicator_id) -> dict | None:
        return next((spec for spec in state["indicators"] if spec["id"] == indicator_id), None)

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
        state["indicators"].append(spec)
        await indicators_changed()
        show_status(f"Přidán indikátor {indicator_title(spec)} – nastavení přes ozubené kolečko v legendě grafu.")

    async def remove_indicator(indicator_id) -> None:
        spec = find_indicator(indicator_id)
        if not spec:
            return
        state["indicators"] = [item for item in state["indicators"] if item["id"] != indicator_id]
        await indicators_changed()
        show_status(f"Indikátor {indicator_title(spec)} odebrán.")

    async def toggle_indicator(indicator_id) -> None:
        # Oko v legendě: skrytí/zobrazení čar indikátoru (zůstává v legendě)
        spec = find_indicator(indicator_id)
        if not spec:
            return
        spec["visible"] = not spec.get("visible", True)
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

    def indicator_matches(kind: str, query: str) -> bool:
        # Hledání v nabídce: zkratka, celý název i popis (bez ohledu na velikost)
        definition = INDICATOR_DEFS[kind]
        haystack = " ".join((kind, definition["title"], definition["name"], definition["desc"])).lower()
        return all(word in haystack for word in query.lower().split())

    def render_indicator_menu() -> None:
        # Přestaví levý sloupec kategorií a seznam indikátorů podle hledání
        category = menu_state["category"]
        query = menu_state["query"].strip()
        counts: dict[str, int] = {}
        for spec in state["indicators"]:
            counts[spec["kind"]] = counts.get(spec["kind"], 0) + 1

        indicator_categories.clear()
        with indicator_categories:
            entries = [("all", "Všechny indikátory"), ("active", f"Aktivní ({len(state['indicators'])})")]
            entries += list(CATEGORIES.items())
            for key, label in entries:
                ui.label(label).classes("ind-cat" + (" active" if key == category and not query else "")).on(
                    "click", make_category_click(key)
                )

        indicator_list.clear()
        with indicator_list:
            if category == "active" and not query:
                # Aktivní instance s nastavením / skrytím / odebráním
                if not state["indicators"]:
                    ui.label("Žádný aktivní indikátor – vyberte ze seznamu.").classes("ind-empty")
                for spec in state["indicators"]:
                    definition = INDICATOR_DEFS[spec["kind"]]
                    with ui.row().classes("ind-item ind-item-active w-full"):
                        with ui.column().classes("ind-item-text"):
                            ui.label(indicator_title(spec)).classes("ind-item-title")
                            ui.label(definition["name"]).classes("ind-item-desc")
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
            # Seznam definic: filtr kategorie (při hledání se prohledávají všechny)
            kinds = [
                kind
                for kind, definition in INDICATOR_DEFS.items()
                if (query and indicator_matches(kind, query))
                or (not query and category in ("all", definition["category"]))
            ]
            kinds.sort(key=lambda kind: INDICATOR_DEFS[kind]["name"].lower())
            if not kinds:
                ui.label("Nic nenalezeno.").classes("ind-empty")
            for kind in kinds:
                definition = INDICATOR_DEFS[kind]
                with ui.row().classes("ind-item w-full").on("click", make_add_click(kind)):
                    with ui.column().classes("ind-item-text"):
                        with ui.row().classes("ind-item-head"):
                            ui.label(definition["name"]).classes("ind-item-title")
                            ui.label(definition["title"]).classes("ind-item-abbr")
                        ui.label(definition["desc"]).classes("ind-item-desc")
                    ui.space()
                    ui.label(CATEGORIES[definition["category"]]).classes("ind-item-cat")
                    if counts.get(kind):
                        ui.badge(str(counts[kind])).classes("ind-item-count").tooltip("Počet aktivních instancí")

    def on_indicator_search(event) -> None:
        menu_state["query"] = event.value or ""
        render_indicator_menu()

    def open_indicator_dialog() -> None:
        # Nabídka indikátorů (TradingView styl): hledání, kategorie, seznam
        menu_state["query"] = ""
        indicator_search.value = ""
        render_indicator_menu()
        indicator_dialog.open()

    # Upravovaná instance indikátoru a pole parametrů v dialogu nastavení
    settings_state: dict = {"id": None}
    settings_fields: dict = {}

    def fill_indicator_settings(kind: str, params: dict) -> None:
        # Pole parametrů podle typu indikátoru (hodnoty z instance / výchozí)
        settings_fields.clear()
        settings_params_container.clear()
        with settings_params_container:
            if not INDICATOR_DEFS[kind]["params"]:
                ui.label("Indikátor nemá nastavitelné parametry.").classes("settings-note")
            for name, default in INDICATOR_DEFS[kind]["params"].items():
                settings_fields[name] = ui.number(
                    PARAM_LABELS.get(name, name),
                    value=params.get(name, default),
                    step=1 if isinstance(default, int) else 0.1,
                ).props("outlined dense")

    def indicator_scope() -> str:
        # Rozsah šablon upravovaného indikátoru: šablony platí pro typ indikátoru
        spec = find_indicator(settings_state["id"])
        return f"indicator.{spec['kind'] if spec else ''}"

    async def open_indicator_settings(indicator_id) -> None:
        spec = find_indicator(indicator_id)
        if not spec:
            return
        settings_state["id"] = indicator_id
        definition = INDICATOR_DEFS[spec["kind"]]
        settings_title.text = f"{definition['name']} ({definition['title']})"
        fill_indicator_settings(spec["kind"], spec["params"])
        index = state["indicators"].index(spec)
        settings_color.value = indicator_color(spec, index)
        await indicator_templates.refresh()
        indicator_settings_dialog.open()

    def apply_indicator_template(values: dict) -> None:
        # Promítne šablonu (parametry + barva) do dialogu; chybějící parametry
        # doplní výchozími, bez barvy se použije barva z palety
        spec = find_indicator(settings_state["id"])
        if not spec:
            return
        params = values.get("params") if isinstance(values.get("params"), dict) else {}
        fill_indicator_settings(spec["kind"], params)
        palette_color = indicator_color({}, state["indicators"].index(spec))
        settings_color.value = normalize_color(values.get("color")) or palette_color

    def reset_indicator_settings() -> None:
        # „Apply defaults“ bez uložené výchozí šablony: tovární parametry a barva
        apply_indicator_template({})

    def collect_indicator_settings() -> dict | None:
        # Parametry a barva z dialogu; neplatný parametr ohlásí a vrátí None
        spec = find_indicator(settings_state["id"])
        if not spec:
            return None
        try:
            params = normalize_params(spec["kind"], {name: field.value for name, field in settings_fields.items()})
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return None
        values: dict = {"params": params}
        if color := normalize_color(settings_color.value):
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
        # Vlastní barva jen v platném tvaru, jinak zůstane barva z palety
        if "color" in values:
            spec["color"] = values["color"]
        indicator_settings_dialog.close()
        await indicators_changed()
        show_status(f"Indikátor {indicator_title(spec)} upraven.")

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
            ui.button("Indikátory", icon="add_chart", on_click=open_indicator_dialog).props(
                "flat"
            ).classes("indicator-add").tooltip("Indikátory (nabídka s hledáním a kategoriemi)")
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
                        # Barva nových kreseb: výběr barvy ve stylu TradingView
                        ColorButton(
                            saved_drawing_color,
                            on_change=lambda event: set_drawing_color(event.value),
                            title="Barva nových kreseb",
                        ).classes("toolbar-color")
                        ui.separator().props("vertical").classes("toolbar-sep")
                        # Akční tlačítka (zpracovává je on_drawing_finished přes frontu)
                        action_buttons = {}
                        for action, tooltip, icon in [
                            ("magnet", "Magnet – přichytávání bodů kreseb k O/H/L/C (klik = výběr režimu)", "push_pin"),
                            ("pencil", "Režim kreslení (nástroj zůstává aktivní)", "edit"),
                            ("lock", "Zamknout kresby", "lock"),
                            ("eye", "Skrýt/zobrazit kresby", "visibility"),
                            ("trash", "Smazat označenou / všechny kresby", "delete_sweep"),
                            ("favorites", "Zobrazit / skrýt panel oblíbených nástrojů (hvězdička ve flyoutu)", "star"),
                        ]:
                            action_buttons[action] = (
                                ui.button(icon=icon)
                                .props(f'flat dense data-tool-action={action}')
                                .classes("drawing-btn")
                                .tooltip(tooltip)
                            )
                    chart = LightweightChart(
                        dark=dark, on_ready=initial_load, show_volume=show_volume, chart_style=chart_style
                    )
                    # Definice skupin pro flyout nabídky lišty (může přijít i před init grafu)
                    chart.set_tool_groups(DRAWING_GROUPS)
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
    with ui.dialog() as indicator_settings_dialog, ui.card().classes("tv-settings"):
        settings_title = settings_header(indicator_settings_dialog)
        ui.label("Inputs").classes("tv-settings-section")
        settings_params_container = ui.column().classes("indicator-dialog-params w-full")
        ui.label("Style").classes("tv-settings-section")
        with ui.row().classes("tv-settings-row"):
            ui.label("Barva").classes("tv-settings-label")
            settings_color = ColorButton(indicator_color({}, 0), title="Barva indikátoru")
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

    # Dialog Settings kresby (pravý klik → Settings… nebo dvojklik na kresbu)
    with ui.dialog() as drawing_settings_dialog, ui.card().classes("tv-settings"):
        drawing_settings_title = settings_header(drawing_settings_dialog, "Settings")
        drawing_text = ui.input("Text").props("outlined dense").classes("w-full")
        drawing_font = ui.number("Velikost písma (px)", value=DEFAULT_FONT_SIZE, min=8, max=72, step=1).props("outlined dense")
        with ui.row().classes("tv-settings-row") as drawing_color_row:
            ui.label("Barva").classes("tv-settings-label")
            drawing_color = ColorButton(DEFAULT_DRAWING_COLOR, title="Barva kresby")
        drawing_width = ui.select(LINE_WIDTHS, value=1.5, label="Šířka čáry").props("outlined dense").classes("w-full")
        drawing_style = ui.select(LINE_STYLES, value="solid", label="Styl čáry").props("outlined dense").classes("w-full")
        with ui.row().classes("tv-settings-actions w-full"):
            drawing_templates = TemplateMenu(
                cache,
                lambda: f"drawing.{(state.get('drawing_settings') or {}).get('kind', '')}",
                collect_drawing_settings,
                apply_drawing_template,
                apply_drawing_factory,
            )
            settings_buttons(drawing_settings_dialog, submit_drawing_settings)

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
