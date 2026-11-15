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
STATIC_VERSION = "0.2.3"


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

    # ---------- kreslení (krok 5) ----------

    DRAWING_KINDS = ["hline", "trend", "rect"]
    DRAWING_LABELS = {"hline": "Horizontální linie", "trend": "Trendová čára", "rect": "Obdélník"}

    async def set_drawing_mode(mode: str | None) -> None:
        # Aktivní nástroj zvýrazníme a přepneme mód v grafu
        state["drawing_mode"] = mode
        for kind, button in drawing_buttons.items():
            button.classes(add="drawing-btn-active" if kind == mode else "", remove="" if kind == mode else "drawing-btn-active")
        await chart.set_drawing_mode(mode)

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
        # Stránkový timer vyzvedne hotové kresby z grafu (fronta) a uloží je
        provider_id = state.get("provider_id")
        symbol = state.get("symbol")
        timeframe = state.get("timeframe")
        if not provider_id or not symbol:
            return
        saved = 0
        for _ in range(20):
            payload = await ui.run_javascript(
                f'return window.TickViewChart.popFinishedDrawing("{chart.js_id}");'
            )
            if not isinstance(payload, dict) or payload.get("kind") not in DRAWING_KINDS:
                break
            points = [
                {"time": int(p["time"]), "price": float(p["price"])}
                for p in (payload.get("points") or [])
            ]
            await cache.add_drawing(provider_id, symbol, timeframe, payload["kind"], points)
            saved += 1
        if saved:
            await reload_drawings()
            show_status(f"Kresba uložena ({DRAWING_LABELS[payload['kind']]}) k {symbol} ({timeframe})")

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
        # Po inicializaci grafu v prohlížeči obnovíme poslední zobrazený symbol
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

    def make_mode_handler(kind: str):
        # Klik na aktivní nástroj ho vypne, jiný klik ho zapne
        async def _handler(_event):
            await set_drawing_mode(None if state["drawing_mode"] == kind else kind)

        return _handler

    with ui.column().classes("page-root w-full"):
        with ui.row().classes("topbar w-full"):
            ui.label("TickView").classes("brand")
            search_input = ui.input(placeholder="Hledat symbol…").classes("symbol-search")
            ui.button(icon="search", on_click=search_symbols).classes("search-button")
            symbol_select = ui.select(options={}, label="Symboly").classes("symbol-select")
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
                with ui.row().classes("drawing-bar w-full"):
                    ui.label("Kreslení:").classes("indicator-label")
                    drawing_buttons = {}
                    for kind in DRAWING_KINDS:
                        icon = {"hline": "horizontal_rule", "trend": "show_chart", "rect": "crop_square"}[kind]
                        drawing_buttons[kind] = (
                            ui.button(DRAWING_LABELS[kind], icon=icon, on_click=make_mode_handler(kind))
                            .props("flat dense size=sm")
                            .classes("drawing-btn")
                        )
                    ui.button("Smazat kresby", icon="delete_sweep", on_click=clear_drawings).props(
                        "flat dense size=sm"
                    ).classes("drawing-btn")
                chart = LightweightChart(dark=dark, on_ready=initial_load)
                status_label = ui.label("").classes("statusbar w-full")

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
    search_input.on("keydown.enter", search_symbols)
    symbol_select.on_value_change(lambda event: load_symbol(event.value))
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
