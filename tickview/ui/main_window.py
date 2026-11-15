"""Hlavní stránka TickView: vyhledání symbolu, timeframy, graf, živý stream."""

import asyncio

from nicegui import context, ui

from tickview.logic.cache import KlineCache, fetch_history
from tickview.providers import PROVIDERS, get_provider
from tickview.providers.base import TIMEFRAME_MS, utc_now_ms
from tickview.ui.lightweight_chart import LightweightChart

# Kolik svíček historie se načte pro jedno zobrazení (a uloží do cache)
DEFAULT_BARS = 600
# Kolik posledních svíček je vidět po načtení grafu
VISIBLE_BARS = 150

# Verze statických souborů pro cache-busting v prohlížeči (při změně CSS/JS zvýšit)
STATIC_VERSION = "0.1.3"


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
    }

    # ---------- handlery (uzávěry nad elementy vytvořenými níže) ----------

    def show_status(message: str, error: bool = False) -> None:
        # Z background tasků (živý stream) musíme vstoupit do slotu prvku
        with status_label:
            status_label.text = message
            status_label.classes(add="error" if error else "", remove="" if error else "error")

    async def search_symbols() -> None:
        # Vyhledání napříč zdroji; výpadek jednoho zdroje nesmí shodit UI
        results: dict[str, str] = {}
        query = search_input.value or ""
        for provider in PROVIDERS:
            try:
                found = await provider.search(query)
            except Exception as exc:
                show_status(f"Zdroj {provider.name} není dostupný: {exc}", error=True)
                continue
            for symbol_info in found:
                key = f"{symbol_info.provider_id}:{symbol_info.symbol}"
                results[key] = f"{symbol_info.label} · {provider.name}"
        symbol_select.options = results
        symbol_select.value = None
        symbol_select.update()
        if results:
            show_status(f"Nalezeno {len(results)} symbolů.")
        else:
            show_status("Nic nenalezeno, zkuste jiný dotaz.")

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
        try:
            async for candle in provider.stream(symbol, timeframe):
                if seq != state["load_seq"]:
                    # Mezitím se přepnulo zobrazení – stream ukončíme
                    return
                set_badge(True)
                await chart.update_candle(candle)
                # Do cache ukládáme jen uzavřené svíčky (uplynulé období);
                # rozepsanou svíčku vrátí příště historie ze sítě v plné podobě
                if candle.open_time + step_ms <= utc_now_ms():
                    await cache.store(provider.id, symbol, timeframe, [candle])
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

    async def initial_load() -> None:
        # Po inicializaci grafu v prohlížeči obnovíme poslední zobrazený symbol
        await load_symbol(last_symbol)

    # ---------- UI ----------

    with ui.column().classes("page-root w-full"):
        with ui.row().classes("topbar w-full"):
            ui.label("TickView").classes("brand")
            search_input = ui.input(placeholder="Hledat symbol…").classes("symbol-search")
            ui.button(icon="search", on_click=search_symbols).classes("search-button")
            symbol_select = ui.select(options={}, label="Symboly").classes("symbol-select")
            timeframe_toggle = ui.toggle(options=[], value=None).classes("timeframes")
            source_badge = ui.badge("—").classes("source-badge")
            theme_switch = ui.switch("Tmavý motiv", value=dark)
        chart = LightweightChart(dark=dark, on_ready=initial_load)
        status_label = ui.label("").classes("statusbar w-full")

    # ---------- vazby ----------
    search_input.on("keydown.enter", search_symbols)
    symbol_select.on_value_change(lambda event: load_symbol(event.value))
    timeframe_toggle.on_value_change(lambda event: set_timeframe(event.value))
    theme_switch.on_value_change(lambda event: toggle_theme(bool(event.value)))

    # Při odpojení klienta ukončíme jeho živý stream, aby nezůstal běžet
    context.client.on_disconnect(cancel_stream)
