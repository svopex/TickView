"""UI replaye (backtest ručních obchodů jako ve FX Replay).

Lišta přehrávání pod grafy (start, play / pauza, krok, rychlost, skok,
Buy / Sell, stav účtu), spodní panel obchodů (Pozice, Příkazy, Historie,
Statistiky) a dialogy: sessions (seznam a nová session), příkaz, úprava
obchodu / příkazu, skok v čase, nastavení session a potvrzení.

Logiku drží ReplayController (services/replay.py); stránka (main_window)
předá objekt host s přístupem ke grafům rozložení a společným funkcím.
"""

import asyncio
import math
import time
from datetime import datetime

from nicegui import background_tasks, ui

from tickview.indicators.compute import context_for, detect_tick, run_indicator
from tickview.indicators.registry import get_indicator
from tickview.logic.alerts import format_price
from tickview.logic.styles import MAX_NAME_LENGTH, clean_name
from tickview.logic.replay import (
    ACCOUNT_CURRENCIES,
    DEFAULT_SETTINGS,
    EXIT_REASONS,
    ORDER_TYPES,
    QTY_MODES,
    SIDES,
    SPEEDS,
    STEP_UNITS,
    clean_session_name,
    default_contract_size,
    position_size,
    positive_number,
    stats_by_tag,
    trades_csv,
)
from tickview.providers.base import TIMEFRAME_MS, utc_now_ms
from tickview.services.replay import ReplayController
from tickview.ui.templates import settings_buttons, settings_header

# Formát pole datetime-local (místní čas serveru = prohlížeče u desktopové aplikace)
DATETIME_INPUT_FORMAT = "%Y-%m-%dT%H:%M"
# Jak často se překreslí lišta a čáry v grafech (s) a nejvýš jak často panel obchodů při přehrávání
REFRESH_S = 0.2
PANEL_REFRESH_S = 0.8
# Volby přesnosti simulace (základní timeframe) v dialogu nové session
BASE_CHOICES = {"auto": "Automaticky (nejjemnější dostupná data)", "1m": "1 minuta", "5m": "5 minut",
                "15m": "15 minut", "1h": "1 hodina", "1D": "1 den"}
# Kolik posledních uzavřených obchodů se ukáže v historii (export obsahuje všechny)
HISTORY_LIMIT = 500


def parse_local_datetime(text) -> int | None:
    """Text pole datetime-local (místní čas) → ms UTC; neplatný / prázdný = None."""
    try:
        return int(datetime.strptime(str(text or "").strip(), DATETIME_INPUT_FORMAT).timestamp() * 1000)
    except ValueError:
        return None


def input_datetime(ms: int) -> str:
    """ms UTC → text pole datetime-local v místním čase."""
    return datetime.fromtimestamp(ms / 1000).strftime(DATETIME_INPUT_FORMAT)


def display_time(ms: int | None) -> str:
    """ms UTC → „dd.mm.rrrr hh:mm“ v místním čase (prázdné pro None)."""
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y %H:%M") if ms else ""


def fmt_money(value: float | None, currency: str = "", sign: bool = False) -> str:
    """Částka na 2 desetinná místa s oddělovačem tisíců (mezera), volitelně se znaménkem."""
    if value is None or not math.isfinite(value):
        return "—"
    text = f"{abs(value):,.2f}".replace(",", " ")
    prefix = ("+" if value > 0 else "−" if value < 0 else "") if sign else ("−" if value < 0 else "")
    return f"{prefix}{text} {currency}".strip()


def fmt_price(value: float | None) -> str:
    """Cena bez zbytečných nul (format_price); None = pomlčka."""
    return "—" if value is None else format_price(value)


def fmt_r(value: float | None) -> str:
    """Násobek rizika R se znaménkem (bez stopu pomlčka)."""
    return "—" if value is None else f"{value:+.2f} R"


def pnl_class(value: float | None) -> str:
    """CSS třída barvy výsledku (zisk zelená, ztráta červená)."""
    if value is None or value == 0:
        return "replay-neutral"
    return "replay-profit" if value > 0 else "replay-loss"


def level_error(side: str, reference: float, sl: float | None, tp: float | None) -> str | None:
    """Kontrola polohy SL / TP vůči ceně (long: SL pod, TP nad; short obráceně); None = v pořádku."""
    long = side == "buy"
    if sl is not None and (sl >= reference if long else sl <= reference):
        return f"Stop loss musí být {'pod' if long else 'nad'} cenou {fmt_price(reference)}."
    if tp is not None and (tp <= reference if long else tp >= reference):
        return f"Take profit musí být {'nad' if long else 'pod'} cenou {fmt_price(reference)}."
    return None


class SessionSettingsFields:
    """Pole nastavení účtu session (nová session i úprava otevřené).

    Vytvoří se v aktuálním kontextu UI (uvnitř dialogu).
    """

    def __init__(self) -> None:
        with ui.grid(columns=2).classes("replay-form w-full"):
            self.currency = ui.select(list(ACCOUNT_CURRENCIES), value="USD", label="Měna účtu",
                                      new_value_mode="add-unique").props("outlined dense")
            self.balance = ui.number("Počáteční zůstatek", min=0).props("outlined dense")
            self.contract = ui.number("Jednotek v 1 lotu / kontraktu", min=0).props("outlined dense")
            self.lot_step = ui.number("Nejmenší krok lotu", min=0).props("outlined dense")
            self.tick = ui.number("Tick (nejmenší změna ceny)", min=0).props("outlined dense")
            self.fixed_rate = ui.number("Pevný kurz k měně účtu (0 = auto)", min=0).props("outlined dense")
            self.spread = ui.number("Spread (ticky)", min=0).props("outlined dense")
            self.slippage = ui.number("Skluz market / stop (ticky)", min=0).props("outlined dense")
            self.commission = ui.number("Komise za lot a stranu (měna účtu)", min=0).props("outlined dense")
        ui.label("Automatická pauza přehrávání").classes("tv-settings-section")
        self.pause_fill = ui.checkbox("Po naplnění příkazu")
        self.pause_exit = ui.checkbox("Po uzavření obchodu (SL / TP)")
        self.pause_signal = ui.checkbox("Při signálu indikátoru na uzavřené svíčce")

    def fill(self, settings: dict) -> None:
        """Vyplní pole hodnotami nastavení."""
        self.currency.value = settings["account_currency"]
        self.balance.value = settings["initial_balance"]
        self.contract.value = settings["contract_size"]
        self.lot_step.value = settings["lot_step"]
        self.tick.value = settings["tick"]
        self.fixed_rate.value = settings["fixed_rate"]
        self.spread.value = settings["spread_ticks"]
        self.slippage.value = settings["slippage_ticks"]
        self.commission.value = settings["commission_per_lot"]
        self.pause_fill.value = settings["pause_on_fill"]
        self.pause_exit.value = settings["pause_on_exit"]
        self.pause_signal.value = settings["pause_on_signal"]

    def collect(self) -> dict:
        """Hodnoty polí jako nastavení (zkontroluje je normalize_settings)."""
        return {
            "account_currency": self.currency.value,
            "initial_balance": self.balance.value,
            "contract_size": self.contract.value,
            "lot_step": self.lot_step.value,
            "tick": self.tick.value,
            "fixed_rate": self.fixed_rate.value or 0,
            "spread_ticks": self.spread.value or 0,
            "slippage_ticks": self.slippage.value or 0,
            "commission_per_lot": self.commission.value or 0,
            "pause_on_fill": self.pause_fill.value,
            "pause_on_exit": self.pause_exit.value,
            "pause_on_signal": self.pause_signal.value,
        }


class ReplayUI:
    """Ovládání replaye na stránce.

    Parametry:
        cache: databáze (sessions, svíčky).
        host: objekt stránky s funkcemi active() a panes() (grafy rozložení),
            show_status(zpráva, error), set_drawing_mode(režim) (async),
            refresh_timeframes(), rewind_mode() (Settings → Replay),
            push_alert_lines() (async), load_symbol(key) (async),
            show_time(pane, ms), replay_changed() (tabulka scanneru, štítek zdroje)
            delete_drawing(pane, id) (async – pozice převedená na příkaz),
            alerts_blocked() (hláška o alertech v grafu replaye), right_panel()
            (obsah pravého panelu) a show_sessions() (async – zobrazí záložku sessions).
    """

    def __init__(self, cache, host) -> None:
        self.cache = cache
        self.host = host
        self.controller = ReplayController(cache, self._on_bars, self._on_state, host.show_status)
        # Překreslení lišty / čar a panelu obchodů (timer _refresh)
        self._dirty = True
        self._panel_dirty = True
        self._panel_at = 0.0
        # Účel výběru času v grafu ("start" nové session / "rewind" krok zpět)
        self.pick_purpose: str | None = None
        self.picked_start: int | None = None
        # Upravovaná položka (dialog obchodu) a čekající akce potvrzení
        self.edit: dict | None = None
        # Kresba Long / Short position, ze které se zadává příkaz (graf, id) – po odeslání se smaže
        self.order_drawing: tuple | None = None
        self._confirm_action = None
        # Čas svíčky s posledním signálem indikátoru v grafu (bez opakované pauzy)
        self._signal_seen: dict[str, int] = {}
        # Revize stavu obchodů, se kterou je vykreslená každá záložka panelu
        self._rendered: dict[str, tuple] = {}
        # Otevřená session a přehrávání při posledním vykreslení seznamu sessions
        # (timer seznam obnoví po otevření / zavření session a po pauze;
        # None = obnovit při nejbližší příležitosti)
        self._sessions_key: tuple | None = None
        self._sessions_rendering = False
        # Session, která se přejmenovává (id)
        self._rename_id: int | None = None

    # ---------- stav ----------

    @property
    def active(self) -> bool:
        return self.controller.active

    def covered_panes(self) -> list:
        """Grafy rozložení, které zobrazují data replaye."""
        return [pane for pane in self.host.panes() if pane.replay_covers() and pane.replay_shown]

    async def _on_bars(self, bars: list, reset: bool) -> None:
        """Nové odkryté svíčky (callback controlleru): grafy v replayi je promítnou."""
        if reset:
            # Otevření / zavření session, krok zpět, velký skok: grafy se přenačtou
            # (grafy, které replay opouštějí, se vrátí k živým datům)
            targets = [pane for pane in self.host.panes() if pane.replay_covers() or pane.replay_shown]
            await asyncio.gather(*(pane.reload_chart() for pane in targets))
            if not self.active:
                await self.host.push_alert_lines()
            self.host.refresh_timeframes()
            # Tabulka scanneru a štítek zdroje podle režimu grafů (živě / replay)
            self.host.replay_changed()
        else:
            # Grafy rozložení souběžně
            panes = self.covered_panes()
            started = await asyncio.gather(*(pane.apply_replay_bars(bars) for pane in panes))
            if self.controller.settings.get("pause_on_signal"):
                for pane, count in zip(panes, started):
                    if count:
                        await self._check_signals(pane)
        self._on_state()

    def _on_state(self) -> None:
        """Změna stavu (callback controlleru): lišta, čáry a panel se překreslí v timeru."""
        self._dirty = True
        self._panel_dirty = True

    async def _check_signals(self, pane) -> None:
        """Pauza přehrávání při signálu indikátoru na právě uzavřené svíčce grafu."""
        candles = list(pane.candles or [])
        if len(candles) < 3:
            return
        ctx = context_for(pane.provider, pane.symbol, pane.timeframe, candles, live=True, signal_bars=2)
        for spec in pane.indicators:
            indicator = get_indicator(spec["kind"])
            if indicator is None or not indicator.has_signals or spec.get("visible") is False:
                continue
            try:
                result = await asyncio.to_thread(run_indicator, indicator, spec["params"], candles, ctx)
            except Exception:
                # Chyba výpočtu indikátoru nesmí zastavit přehrávání
                continue
            closed_time = candles[-2].open_time // 1000
            for signal in result.signals:
                key = f"{pane.chart.js_id}:{spec['id']}"
                if signal.bars_ago == 1 and self._signal_seen.get(key) != closed_time:
                    self._signal_seen[key] = closed_time
                    self.controller.pause()
                    label = f"{signal.direction.upper()} {signal.label}".strip()
                    self.host.show_status(f"Replay pozastaven: signál {label} ({indicator.name}).", False)
                    return

    # ---------- stavba UI ----------

    def build_topbar_button(self) -> None:
        """Tlačítko Replay v horní liště (sessions a nová session)."""
        self.topbar_button = ui.button("Replay", icon="history", color=None, on_click=self.host.show_sessions).props(
            "flat dense no-caps"
        ).classes("topbar-btn replay-topbar-btn").tooltip("Replay – backtest ručních obchodů v historii")

    def build_bar(self) -> None:
        """Lišta replaye pod grafy (viditelná jen s otevřenou session)."""
        with ui.row().classes("replay-bar w-full") as self.bar:
            ui.icon("history").classes("replay-bar-icon")
            self.name_button = ui.button("", color=None, on_click=self.host.show_sessions).props(
                "flat dense no-caps"
            ).classes("replay-name").tooltip("Sessions replaye")
            ui.button(icon="content_cut", color=None, on_click=self.start_rewind_pick).props("flat dense").classes(
                "replay-btn"
            ).tooltip("Vrátit replay zpět – klikněte do grafu na svíčku, od které pokračovat")
            self.play_button = ui.button(icon="play_arrow", color=None, on_click=self.toggle_play).props(
                "flat dense"
            ).classes("replay-btn replay-play").tooltip("Přehrát / pozastavit (mezerník)")
            ui.button(icon="skip_next", color=None, on_click=self.step).props("flat dense").classes(
                "replay-btn"
            ).tooltip("Krok vpřed (Shift+→)")
            self.unit_select = ui.select(STEP_UNITS, value=self.controller.step_unit,
                                         on_change=lambda e: setattr(self.controller, "step_unit", e.value)).props(
                "dense borderless options-dense").classes("replay-select").tooltip("Krok tlačítka vpřed")
            self.speed_select = ui.select({speed: f"{speed}×" for speed in SPEEDS}, value=self.controller.speed,
                                          on_change=lambda e: self.controller.set_speed(e.value)).props(
                "dense borderless options-dense").classes("replay-select").tooltip(
                "Rychlost přehrávání (základních svíček za sekundu)")
            ui.button(icon="event", color=None, on_click=self.open_jump).props("flat dense").classes(
                "replay-btn"
            ).tooltip("Skok na datum a čas")
            self.time_label = ui.label("").classes("replay-time")
            ui.separator().props("vertical").classes("replay-sep")
            ui.button("Buy", color=None, on_click=lambda: self.open_order("buy")).props("unelevated dense no-caps").classes(
                "replay-buy").tooltip("Nový nákupní příkaz (Market / Limit / Stop)")
            ui.button("Sell", color=None, on_click=lambda: self.open_order("sell")).props(
                "unelevated dense no-caps").classes("replay-sell").tooltip("Nový prodejní příkaz (Market / Limit / Stop)")
            self.account_label = ui.label("").classes("replay-account")
            self.pnl_label = ui.label("").classes("replay-account")
            ui.space()
            ui.button(icon="table_rows", color=None, on_click=self.toggle_panel).props("flat dense").classes(
                "replay-btn").tooltip("Panel obchodů")
            ui.button(icon="tune", color=None, on_click=self.open_session_settings).props("flat dense").classes(
                "replay-btn").tooltip("Nastavení session (účet, náklady, automatická pauza)")
            ui.button(icon="close", color=None, on_click=self.close_session).props("flat dense").classes(
                "replay-btn").tooltip("Ukončit replay (session zůstane uložená)")
        self.bar.set_visibility(False)

    def build_panel(self) -> None:
        """Spodní panel obchodů: Pozice, Příkazy, Historie, Statistiky."""
        with ui.column().classes("replay-panel w-full") as self.panel:
            # Posuvná horní hrana panelu (výška, static/resizers.js)
            ui.element("div").classes("pane-resizer pane-resizer-top").props(
                "data-resize=replay-panel data-resize-min=120 data-resize-max=0.75")
            with ui.row().classes("replay-panel-head w-full"):
                with ui.tabs().props("dense no-caps align=left indicator-color=primary").classes("replay-tabs") as tabs:
                    self.tab_positions = ui.tab("positions", label="Pozice")
                    self.tab_orders = ui.tab("orders", label="Příkazy")
                    self.tab_history = ui.tab("history", label="Historie")
                    self.tab_stats = ui.tab("stats", label="Statistiky")
                ui.space()
                ui.button(icon="download", color=None, on_click=self.export_csv).props("flat dense round size=sm").classes(
                    "replay-btn").tooltip("Export obchodů do CSV")
                ui.button(icon="expand_more", color=None, on_click=self.toggle_panel).props(
                    "flat dense round size=sm").classes("replay-btn").tooltip("Skrýt panel")
            # Přepnutí záložky ji vykreslí (vykresluje se jen viditelná záložka)
            tabs.on_value_change(lambda _e: self._on_state())
            self.panel_tabs = tabs
            with ui.tab_panels(tabs, value=self.tab_positions).props("animated=false").classes("replay-panel-body w-full"):
                with ui.tab_panel(self.tab_positions).classes("replay-tab-panel"):
                    self.positions_box = ui.column().classes("replay-table w-full")
                with ui.tab_panel(self.tab_orders).classes("replay-tab-panel"):
                    self.orders_box = ui.column().classes("replay-table w-full")
                with ui.tab_panel(self.tab_history).classes("replay-tab-panel"):
                    self.history_box = ui.column().classes("replay-table w-full")
                with ui.tab_panel(self.tab_stats).classes("replay-tab-panel"):
                    with ui.row().classes("replay-stats-layout w-full"):
                        self.stats_box = ui.element("div").classes("replay-stats")
                        with ui.column().classes("replay-stats-side"):
                            self.equity_chart = ui.echart(self._equity_options([])).classes("replay-equity")
                            self.tags_box = ui.column().classes("replay-table w-full")
        self.panel.set_visibility(False)

    def build_dialogs(self) -> None:
        """Dialogy replaye (volá se v kontextu stránky mimo rozložení)."""
        self._build_new_dialog()
        self._build_rename_dialog()
        self._build_order_dialog()
        self._build_edit_dialog()
        self._build_jump_dialog()
        self._build_settings_dialog()
        with ui.dialog() as self.confirm_dialog, ui.card().classes("tpl-name-dialog"):
            settings_header(self.confirm_dialog, "Replay")
            self.confirm_text = ui.label("")
            with ui.row().classes("tv-settings-actions w-full"):
                settings_buttons(self.confirm_dialog, self._confirm_ok)

    def start(self) -> None:
        """Spustí obnovu lišty / panelu a klávesové zkratky (mezerník, Shift+→)."""
        ui.timer(REFRESH_S, self._refresh)
        ui.keyboard(on_key=self._on_key, repeating=False)

    # ---------- obnova lišty, čar a panelu ----------

    def _refresh(self) -> None:
        """Překreslí lištu a čáry v grafech, panel obchodů nejvýš jednou za PANEL_REFRESH_S."""
        controller = self.controller
        if self._dirty:
            self._dirty = False
            active = controller.active
            self.bar.set_visibility(active)
            self.topbar_button.classes(add="replay-on" if active else "", remove="" if active else "replay-on")
            payload = controller.chart_payload() if active else None
            static = controller.chart_static() if active else None
            for pane in self.host.panes():
                shown = pane.replay_shown and pane.replay_covers()
                pane.chart.set_replay(payload if shown else None, static)
            if active:
                self._paint_bar()
            else:
                self.panel.set_visibility(False)
        # Seznam sessions: obnova po změně otevřené session nebo pauze (nejvýš jedno vykreslení naráz)
        if ((controller.session_id, controller.playing) != self._sessions_key and not self._sessions_rendering
                and self.host.right_panel() == "replay"):
            background_tasks.create(self.render_sessions())
        if not self._panel_dirty or not self.panel.visible or not controller.active:
            return
        if controller.playing and time.monotonic() - self._panel_at < PANEL_REFRESH_S:
            return
        self._panel_dirty = False
        self._panel_at = time.monotonic()
        self.render_panel()

    def _paint_bar(self) -> None:
        # Název session, čas replaye, ikona přehrávání a stav účtu
        controller = self.controller
        engine = controller.engine
        currency = controller.currency
        self.name_button.text = f"{controller.session['name']} · {controller.session['symbol']}"
        self.time_label.text = f"{display_time(controller.now_ms)} · přesnost {controller.base_tf}"
        self.play_button.props(f"icon={'pause' if controller.playing else 'play_arrow'}")
        open_pnl = engine.open_pnl()
        self.account_label.text = (
            f"Zůstatek {fmt_money(engine.balance, currency)} · Equity {fmt_money(engine.balance + open_pnl, currency)}"
        )
        self.pnl_label.text = f"Open P&L {fmt_money(open_pnl, currency, sign=True)}"
        self.pnl_label.classes(replace=f"replay-account {pnl_class(open_pnl)}")
        if self.unit_select.value != controller.step_unit:
            self.unit_select.value = controller.step_unit
        if self.speed_select.value != controller.speed:
            self.speed_select.value = controller.speed

    def toggle_panel(self) -> None:
        """Zobrazí / skryje panel obchodů."""
        self.panel.set_visibility(not self.panel.visible and self.active)
        self._panel_dirty = True

    def render_panel(self) -> None:
        """Přestaví viditelnou záložku panelu obchodů.

        Pozice se mění s cenou (plovoucí P&L), ostatní záložky se přestaví
        jen po změně obchodů, příkazů nebo poznámek (revize controlleru).
        """
        controller = self.controller
        # Hodnota záložek je prvek ui.tab (výchozí) nebo jeho název (po kliknutí)
        value = self.panel_tabs.value
        tab = value if isinstance(value, str) else value.props.get("name")
        revision = controller.revision
        if tab != "positions" and self._rendered.get(tab) == revision:
            return
        self._rendered[tab] = revision
        if tab == "positions":
            self._render_positions(controller.trades(open_only=True))
        elif tab == "orders":
            self._render_orders(controller.orders())
        elif tab == "history":
            self._render_history([trade for trade in controller.trades() if trade["closed"]])
        else:
            self._render_stats()

    def _header(self, columns: str, labels: list[str]) -> None:
        # Záhlaví tabulky panelu (mřížka sloupců podle třídy columns)
        with ui.element("div").classes(f"replay-row replay-head {columns}"):
            for label in labels:
                ui.label(label)

    def _action(self, icon: str, tooltip: str, handler) -> None:
        # Malé ikonové tlačítko akce v řádku tabulky
        ui.button(icon=icon, color=None, on_click=handler).props("flat dense round size=sm").classes(
            "replay-row-btn").tooltip(tooltip)

    def _render_positions(self, trades: list[dict]) -> None:
        # Otevřené obchody s akcemi: upravit, break-even, zavřít polovinu, zavřít
        currency = self.controller.currency
        box = self.positions_box
        box.clear()
        with box:
            if not trades:
                ui.label("Žádné otevřené pozice. Buy / Sell v liště nebo nástroj Long / Short position v grafu.").classes(
                    "replay-empty")
                return
            self._header("replay-cols-positions", ["#", "Strana", "Loty", "Vstup", "SL", "TP", "P&L", "R", ""])
            for trade in trades:
                floating_r = trade["floating"] / trade["risk"] if trade["risk"] > 0 else None
                with ui.element("div").classes("replay-row replay-cols-positions"):
                    ui.label(f"#{trade['id']}")
                    ui.label("Long" if trade["long"] else "Short").classes(
                        "replay-side-buy" if trade["long"] else "replay-side-sell")
                    ui.label(f"{trade['open_qty']:g}")
                    ui.label(fmt_price(trade["entry_price"])).tooltip(display_time(trade["entry_time"]))
                    ui.label(fmt_price(trade["sl"]))
                    ui.label(fmt_price(trade["tp"]))
                    ui.label(fmt_money(trade["floating"], currency, sign=True)).classes(pnl_class(trade["floating"]))
                    ui.label(fmt_r(floating_r))
                    with ui.row().classes("replay-row-actions"):
                        self._action("edit", "Upravit SL / TP, částečné zavření, poznámka",
                                     lambda trade=trade: self.open_edit("trade", trade))
                        self._action("vertical_align_center", "Stop na vstup (break-even)",
                                     lambda trade=trade: self.break_even(trade))
                        self._action("content_cut", "Zavřít polovinu pozice",
                                     lambda trade=trade: self.close_trade(trade["id"], trade["open_qty"] / 2))
                        self._action("close", "Zavřít pozici za trh", lambda trade=trade: self.close_trade(trade["id"]))

    def _render_orders(self, orders: list[dict]) -> None:
        # Čekající příkazy s akcemi: upravit, zrušit
        box = self.orders_box
        box.clear()
        with box:
            if not orders:
                ui.label("Žádné čekající příkazy.").classes("replay-empty")
                return
            self._header("replay-cols-orders", ["#", "Příkaz", "Loty", "Cena", "SL", "TP", "Zadáno", "Expirace", ""])
            for order in orders:
                with ui.element("div").classes("replay-row replay-cols-orders"):
                    ui.label(f"#{order['id']}")
                    ui.label(f"{SIDES[order['side']]} {ORDER_TYPES[order['type']]}").classes(
                        "replay-side-buy" if order["side"] == "buy" else "replay-side-sell")
                    ui.label(f"{order['qty']:g}")
                    ui.label(fmt_price(order["price"]))
                    ui.label(fmt_price(order["sl"]))
                    ui.label(fmt_price(order["tp"]))
                    ui.label(display_time(order["created"]))
                    ui.label(display_time(order["expires"]) or "GTC")
                    with ui.row().classes("replay-row-actions"):
                        self._action("edit", "Upravit příkaz", lambda order=order: self.open_edit("order", order))
                        self._action("close", "Zrušit příkaz", lambda order=order: self.cancel_order(order["id"]))

    def _render_history(self, trades: list[dict]) -> None:
        # Uzavřené obchody (nejnovější nahoře); klik = vstup v grafu, tužka = poznámka a štítek
        currency = self.controller.currency
        box = self.history_box
        box.clear()
        with box:
            if not trades:
                ui.label("Zatím žádné uzavřené obchody.").classes("replay-empty")
                return
            self._header("replay-cols-history", ["#", "Strana", "Loty", "Vstup", "Cena", "Výstup", "Cena", "Důvod",
                                                 "P&L", "R", "Štítek", ""])
            ordered = sorted(trades, key=lambda trade: trade["exit_time"] or 0, reverse=True)[:HISTORY_LIMIT]
            for trade in ordered:
                reasons = ", ".join(EXIT_REASONS.get(item["reason"], item["reason"]) for item in trade["exits"])
                with ui.element("div").classes("replay-row replay-cols-history replay-row-click").on(
                    "click", lambda _e, trade=trade: self.show_trade(trade)
                ):
                    ui.label(f"#{trade['id']}")
                    ui.label("Long" if trade["long"] else "Short").classes(
                        "replay-side-buy" if trade["long"] else "replay-side-sell")
                    ui.label(f"{trade['qty']:g}")
                    ui.label(display_time(trade["entry_time"]))
                    ui.label(fmt_price(trade["entry_price"]))
                    ui.label(display_time(trade["exit_time"]))
                    ui.label(fmt_price(trade["exit_price"]))
                    reason_label = ui.label(reasons + (" ⚠" if trade["ambiguous"] else ""))
                    if trade["ambiguous"]:
                        reason_label.tooltip("Odhad: stop i cíl (nebo vstup a stop) v jedné základní svíčce – "
                                             "počítá se konzervativně stop.")
                    ui.label(fmt_money(trade["realized"], currency, sign=True)).classes(pnl_class(trade["realized"]))
                    ui.label(fmt_r(trade["r"]))
                    tag_label = ui.label(trade["tag"] or "")
                    if trade["note"]:
                        tag_label.tooltip(trade["note"])
                    with ui.row().classes("replay-row-actions"):
                        self._action("edit_note", "Poznámka a štítek setupu",
                                     lambda trade=trade: self.open_edit("closed", trade))

    def _render_stats(self) -> None:
        # Dlaždice statistik, křivka zůstatku a výsledky podle štítku setupu
        controller = self.controller
        stats = controller.stats()
        currency = controller.currency
        profit_factor = stats["profit_factor"]
        tiles = [
            ("Čistý zisk", f"{fmt_money(stats['net'], currency, sign=True)} ({stats['return_pct']:+.2f} %)",
             pnl_class(stats["net"])),
            ("Obchodů", f"{stats['count']} ({stats['wins']} / {stats['losses']})", ""),
            ("Win rate", f"{stats['win_rate']:.1f} %", ""),
            ("Profit factor", "∞" if math.isinf(profit_factor) else f"{profit_factor:.2f}", ""),
            ("Průměrná výhra", fmt_money(stats["avg_win"], currency), "replay-profit"),
            ("Průměrná ztráta", fmt_money(stats["avg_loss"], currency), "replay-loss"),
            ("Expectancy / obchod", fmt_money(stats["expectancy"], currency, sign=True), pnl_class(stats["expectancy"])),
            ("Průměrné R / celkem", f"{fmt_r(stats['avg_r'])} / {fmt_r(stats['total_r'])}", ""),
            ("Max. drawdown", f"{fmt_money(stats['max_drawdown'], currency)} ({stats['max_drawdown_pct']:.2f} %)",
             "replay-loss"),
            ("Nejdelší série ztrát", str(stats["max_losing_streak"]), ""),
            ("Největší výhra / ztráta",
             f"{fmt_money(stats['largest_win'], currency)} / {fmt_money(stats['largest_loss'], currency)}", ""),
            ("Komise", fmt_money(stats["commissions"], currency), ""),
            ("Odhadnuté obchody", str(stats["ambiguous"]), ""),
        ]
        self.stats_box.clear()
        with self.stats_box:
            for title, value, css in tiles:
                with ui.column().classes("replay-stat"):
                    ui.label(title).classes("replay-stat-title")
                    ui.label(value).classes(f"replay-stat-value {css}")
        curve = controller.curve()
        self.equity_chart.options.update(self._equity_options(curve))
        self.equity_chart.update()
        rows = stats_by_tag(list(controller.engine.trades.values()), controller.session["annotations"])
        self.tags_box.clear()
        with self.tags_box:
            if rows:
                self._header("replay-cols-tags", ["Štítek", "Obchodů", "Win rate", "P&L", "Průměrné R"])
                for row in rows:
                    with ui.element("div").classes("replay-row replay-cols-tags"):
                        ui.label(row["tag"])
                        ui.label(str(row["count"]))
                        ui.label(f"{row['win_rate']:.0f} %")
                        ui.label(fmt_money(row["net"], currency, sign=True)).classes(pnl_class(row["net"]))
                        ui.label(fmt_r(row["avg_r"]))

    @staticmethod
    def _equity_options(curve: list[tuple[int, float]]) -> dict:
        # Volby ECharts pro křivku realizovaného zůstatku (čas → zůstatek)
        return {
            "animation": False,
            "grid": {"left": 60, "right": 12, "top": 16, "bottom": 28},
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "time", "axisLabel": {"color": "#787b86"}},
            "yAxis": {"type": "value", "scale": True, "axisLabel": {"color": "#787b86"},
                      "splitLine": {"lineStyle": {"color": "rgba(128,128,128,0.2)"}}},
            "series": [{"type": "line", "step": "end", "showSymbol": False, "lineStyle": {"color": "#2962ff"},
                        "areaStyle": {"color": "rgba(41,98,255,0.12)"},
                        "data": [[time_ms, round(value, 2)] for time_ms, value in curve]}],
        }

    # ---------- přehrávání ----------

    async def toggle_play(self) -> None:
        """Play / pauza."""
        if not self.active:
            return
        if self.controller.playing:
            self.controller.pause()
        else:
            self.controller.play()

    def _active_anchor(self) -> tuple[int | None, int]:
        # Kotva a délka svíčky aktivního grafu (krok „1 bar grafu“)
        pane = self.host.active()
        if pane.replay_covers() and pane.replay_shown:
            return pane.replay_anchor, TIMEFRAME_MS[pane.timeframe]
        return None, TIMEFRAME_MS[self.controller.base_tf]

    async def step(self) -> None:
        """Krok vpřed (jedna svíčka grafu aktivního grafu nebo jedna základní svíčka)."""
        if not self.active:
            return
        self.controller.pause()
        anchor, step_ms = self._active_anchor()
        await self.controller.step(anchor, step_ms)

    async def _on_key(self, event) -> None:
        # Klávesové zkratky replaye (jen s otevřenou session a bez otevřeného dialogu)
        if not self.active or not event.action.keydown:
            return
        if event.key.space and not event.modifiers.ctrl and not event.modifiers.meta:
            await self.toggle_play()
        elif event.key.arrow_right and event.modifiers.shift:
            await self.step()

    # ---------- sessions ----------

    def build_sessions_panel(self):
        """Záložka Replay v pravém panelu: seznam sessions a nová session; vrací sekci panelu."""
        with ui.column().classes("watchlist-panel w-full") as section:
            with ui.row().classes("watchlist-header w-full"):
                ui.label("Replay – sessions").classes("replay-sessions-title")
                ui.space()
                ui.button(icon="add", color=None, on_click=self.open_new_session).props(
                    "flat dense round size=sm").classes("alert-btn").tooltip("Nová session pro symbol aktivního grafu")
            self.sessions_list = ui.column().classes("replay-sessions w-full")
        return section

    def _build_new_dialog(self) -> None:
        # Formulář nové session (symbol aktivního grafu, start, přesnost, účet)
        with ui.dialog() as self.new_dialog, ui.card().classes("tv-settings replay-dialog"):
            settings_header(self.new_dialog, "Nová session replaye")
            self.new_symbol = ui.label("").classes("settings-note")
            self.new_name = ui.input("Název").props("outlined dense").classes("w-full")
            with ui.row().classes("replay-start-row w-full"):
                self.new_start = ui.input("Začátek (historie odkrytá do)").props(
                    "outlined dense type=datetime-local").classes("replay-start-input")
                ui.button("Vybrat v grafu", icon="ads_click", color=None, on_click=self.start_new_pick).props(
                    "flat no-caps").tooltip("Klikněte do grafu na svíčku, od které replay začne")
            self.new_base = ui.select(BASE_CHOICES, value="auto", label="Přesnost simulace (základní timeframe)").props(
                "outlined dense").classes("w-full")
            ui.label(
                "Příkazy, stopy a cíle se vyhodnocují na základním timeframu (svíčky grafu se z něj skládají). "
                "Starší data mají u některých zdrojů jen hrubší timeframe (Yahoo 1m jen ~30 dní)."
            ).classes("settings-note")
            self.new_fields = SessionSettingsFields()
            with ui.row().classes("tv-settings-actions w-full"):
                settings_buttons(self.new_dialog, self.create_session, "Spustit replay")

    def _build_rename_dialog(self) -> None:
        # Přejmenování session (tužka v seznamu sessions)
        with ui.dialog() as self.rename_dialog, ui.card().classes("tpl-name-dialog"):
            settings_header(self.rename_dialog, "Přejmenovat session")
            self.rename_input = ui.input("Název").props("outlined dense autofocus").classes("w-full")
            self.rename_input.on("keydown.enter", self.submit_rename)
            with ui.row().classes("tv-settings-actions w-full"):
                settings_buttons(self.rename_dialog, self.submit_rename, "Save")

    async def open_new_session(self) -> None:
        """Dialog nové session s předvyplněným symbolem aktivního grafu, startem a účtem."""
        pane = self.host.active()
        if not pane.key:
            self.host.show_status("Graf nemá symbol – nejdřív zvolte symbol.", True)
            return
        self.new_symbol.text = f"Symbol: {pane.key} (aktivní graf)"
        if not self.new_name.value:
            self.new_name.value = f"{pane.symbol} {datetime.now().strftime('%d.%m.%Y')}"
        if not self.new_start.value:
            # Výchozí start: před měsícem
            self.new_start.value = input_datetime(utc_now_ms() - 30 * 86_400_000)
        # Výchozí účet: tick a velikost lotu podle instrumentu
        settings = dict(DEFAULT_SETTINGS)
        kind = pane.provider.symbol_kind(pane.symbol) if pane.provider else ""
        settings["contract_size"] = default_contract_size(kind)
        candles = pane.candles or []
        tick = pane.provider.price_tick(pane.symbol, candles[-1].close) if pane.provider and candles else None
        settings["tick"] = tick or (detect_tick(candles) if candles else 0.01)
        self.new_fields.fill(settings)
        self.new_dialog.open()

    async def render_sessions(self) -> None:
        """Seznam sessions v pravém panelu (jen viditelný): klik = otevřít, tužka = přejmenovat, koš = smazat."""
        if self.host.right_panel() != "replay":
            return
        self._sessions_key = (self.controller.session_id, self.controller.playing)
        self._sessions_rendering = True
        try:
            sessions = await self.cache.replay_sessions()
        finally:
            self._sessions_rendering = False
        self.sessions_list.clear()
        with self.sessions_list:
            if not sessions:
                ui.label("Zatím žádné sessions – tlačítko + založí novou pro symbol aktivního grafu.").classes(
                    "replay-empty")
            for session in sessions:
                summary = session["summary"]
                current = session["id"] == self.controller.session_id
                with ui.row().classes("replay-session-row w-full" + (" replay-session-current" if current else "")):
                    # Klik na název a údaje session ji otevře (tlačítka vpravo mají vlastní akce)
                    with ui.column().classes("replay-session-info").on(
                        "click", lambda _e, s=session: self.resume_session(s)
                    ).tooltip("Otevřená session" if current else "Otevřít session"):
                        ui.label(session["name"]).classes("replay-session-name")
                        ui.label(f"{session['source']}:{session['symbol']} · přesnost {session['base_tf']}").classes(
                            "alert-meta")
                        ui.label(f"{display_time(session['start_ms'])} → {display_time(session['cursor_ms'])}").classes(
                            "alert-meta")
                    with ui.column().classes("replay-session-result"):
                        net = summary.get("net")
                        ui.label(fmt_money(net, summary.get("currency", ""), sign=True) if net is not None else "").classes(
                            pnl_class(net))
                        ui.label(f"obchodů {summary.get('trades', 0)}").classes("alert-meta")
                    with ui.row().classes("replay-row-actions"):
                        ui.button(icon="edit", color=None, on_click=lambda s=session: self.open_rename(s)).props(
                            "flat dense round size=sm").classes("replay-row-btn").tooltip("Přejmenovat")
                        ui.button(icon="delete", color=None, on_click=lambda s=session: self.ask_delete_session(s)).props(
                            "flat dense round size=sm").classes("replay-row-btn wl-danger").tooltip(
                            "Smazat session (i její obchody a kresby)")

    def open_rename(self, session: dict) -> None:
        """Dialog přejmenování session."""
        self._rename_id = session["id"]
        self.rename_input.value = session["name"]
        self.rename_dialog.open()

    async def submit_rename(self) -> None:
        """Uloží nový název session (otevřená session ho hned ukáže v liště)."""
        name = clean_name(self.rename_input.value)
        if not name:
            ui.notify(f"Zadejte název (1–{MAX_NAME_LENGTH} znaků).", type="warning")
            return
        self.rename_dialog.close()
        if self._rename_id == self.controller.session_id:
            await self.controller.rename(name)
        else:
            await self.cache.update_replay_session(self._rename_id, name=name)
        # Seznam obnoví timer
        self._sessions_key = None

    async def create_session(self) -> None:
        """Založí a otevře novou session pro symbol aktivního grafu."""
        pane = self.host.active()
        key = pane.key
        if not key:
            return
        start = parse_local_datetime(self.new_start.value)
        if self.picked_start and self.new_start.value == input_datetime(self.picked_start):
            # Čas vybraný klikem do grafu je přesnější než pole (minuty)
            start = self.picked_start
        if start is None:
            ui.notify("Zadejte začátek replaye.", type="warning")
            return
        if start >= utc_now_ms() - 60_000:
            ui.notify("Začátek replaye musí být v minulosti.", type="warning")
            return
        name = clean_session_name(self.new_name.value, f"{pane.symbol} replay")
        base = None if self.new_base.value == "auto" else self.new_base.value
        self.new_dialog.close()
        self.host.show_status(f"Zakládám replay {name} – hledám data…", False)
        session_id = await self.controller.create(
            name, key, start, self.new_fields.collect(), pane.session_filter(), base
        )
        if session_id is None:
            return
        self.new_name.value = ""
        self.picked_start = None
        await self._open(session_id, key)

    async def resume_session(self, session: dict) -> None:
        """Otevře uloženou session ze seznamu (grafy se symbolem session přejdou do replaye)."""
        if session["id"] != self.controller.session_id:
            await self._open(session["id"], f"{session['source']}:{session['symbol']}")

    async def _open(self, session_id: int, key: str) -> None:
        # Aktivní graf zobrazí symbol session (ostatní grafy se stejným symbolem
        # přejdou do replaye samy); pak se session otevře
        pane = self.host.active()
        if pane.key != key:
            await self.host.load_symbol(key)
        if await self.controller.open(session_id, self.host.active().session_filter()):
            self.panel.set_visibility(True)
            self.host.show_status(
                f"Replay {self.controller.session['name']}: {display_time(self.controller.now_ms)}. "
                "Mezerník = play / pauza, Shift+→ = krok.", False)

    def ask_delete_session(self, session: dict) -> None:
        """Potvrzení smazání session."""
        self.confirm(f"Smazat session „{session['name']}“ včetně obchodů a kreseb?",
                     lambda: self.delete_session(session["id"]))

    async def delete_session(self, session_id: int) -> None:
        """Smaže session (otevřenou nejdřív zavře)."""
        if session_id == self.controller.session_id:
            await self.controller.close()
        await self.cache.delete_replay_session(session_id)
        # Seznam obnoví timer
        self._sessions_key = None
        self.host.show_status("Session replaye smazána.", False)

    async def close_session(self) -> None:
        """Ukončí replay (session zůstane uložená, grafy se vrátí k živým datům)."""
        await self.controller.close()
        self.host.show_status("Replay ukončen – session je uložená (záložka Replay v pravém panelu).", False)

    # ---------- výběr času v grafu (start, krok zpět) ----------

    async def start_new_pick(self) -> None:
        """Výběr startu nové session klikem do grafu (dialog se po kliku znovu otevře)."""
        self.new_dialog.close()
        self.pick_purpose = "start"
        await self.host.set_drawing_mode("replay_pick")
        self.host.show_status("Klikněte do grafu na svíčku, od které má replay začít (Esc = zrušit).", False)

    async def start_rewind_pick(self) -> None:
        """Krok zpět klikem do grafu na svíčku, od které replay pokračuje."""
        if not self.active:
            return
        if self.host.rewind_mode() == "off":
            self.host.show_status("Krok zpět je vypnutý (Settings → Replay).", True)
            return
        self.controller.pause()
        self.pick_purpose = "rewind"
        await self.host.set_drawing_mode("replay_pick")
        self.host.show_status("Klikněte do grafu na svíčku, od které replay pokračuje (Esc = zrušit).", False)

    async def on_pick(self, pane, time_s: int) -> None:
        """Klik do grafu ve výběru času: start nové session nebo krok zpět.

        Vybraná svíčka grafu je poslední odkrytá – historie se odkryje do jejího konce.
        """
        purpose, self.pick_purpose = self.pick_purpose, None
        end_ms = int(time_s) * 1000 + TIMEFRAME_MS.get(pane.timeframe, 60_000) - 1
        if purpose == "start":
            self.picked_start = min(end_ms, utc_now_ms() - 60_000)
            self.new_start.value = input_datetime(self.picked_start)
            await self.open_new_session()
        elif purpose == "rewind":
            await self.request_rewind(end_ms)

    async def request_rewind(self, time_ms: int) -> None:
        """Krok zpět podle nastavení aplikace (smazat / potvrdit / zakázáno)."""
        mode = self.host.rewind_mode()
        if mode == "off":
            self.host.show_status("Krok zpět je vypnutý (Settings → Replay).", True)
            return
        count = self.controller.trades_after(time_ms)
        if mode == "confirm" and count:
            self.confirm(f"Krok zpět smaže {count} příkazů / změn zadaných po {display_time(time_ms)}. Pokračovat?",
                         lambda: self.controller.rewind(time_ms))
            return
        await self.controller.rewind(time_ms)

    # ---------- skok v čase ----------

    def _build_jump_dialog(self) -> None:
        with ui.dialog() as self.jump_dialog, ui.card().classes("tpl-name-dialog"):
            settings_header(self.jump_dialog, "Skok na datum a čas")
            self.jump_input = ui.input("Datum a čas").props("outlined dense type=datetime-local").classes("w-full")
            ui.label("Pozdější čas = skok vpřed (obchody se vyhodnotí), dřívější = krok zpět.").classes("settings-note")
            with ui.row().classes("tv-settings-actions w-full"):
                settings_buttons(self.jump_dialog, self.submit_jump, "Skočit")

    def open_jump(self) -> None:
        """Dialog skoku v čase s aktuálním časem replaye."""
        if not self.active:
            return
        self.jump_input.value = input_datetime(self.controller.now_ms)
        self.jump_dialog.open()

    async def submit_jump(self) -> None:
        """Skok vpřed nebo krok zpět na zadaný čas."""
        target = parse_local_datetime(self.jump_input.value)
        if target is None:
            ui.notify("Zadejte datum a čas.", type="warning")
            return
        self.jump_dialog.close()
        if target >= self.controller.now_ms:
            await self.controller.jump(target)
        elif target < self.controller.cursor:
            await self.request_rewind(target)

    # ---------- nastavení session ----------

    def _build_settings_dialog(self) -> None:
        with ui.dialog() as self.settings_dialog, ui.card().classes("tv-settings replay-dialog"):
            settings_header(self.settings_dialog, "Nastavení session")
            self.settings_name = ui.input("Název").props("outlined dense").classes("w-full")
            self.settings_fields = SessionSettingsFields()
            ui.label("Změna účtu nebo nákladů přepočítá všechny obchody session.").classes("settings-note")
            with ui.row().classes("tv-settings-actions w-full"):
                settings_buttons(self.settings_dialog, self.submit_session_settings)

    def open_session_settings(self) -> None:
        """Dialog nastavení otevřené session."""
        if not self.active:
            return
        self.settings_name.value = self.controller.session["name"]
        self.settings_fields.fill(self.controller.settings)
        self.settings_dialog.open()

    async def submit_session_settings(self) -> None:
        """Uloží nastavení session (přepočítá obchody, když se změnil účet nebo náklady)."""
        self.settings_dialog.close()
        controller = self.controller
        name = clean_session_name(self.settings_name.value, controller.session["name"])
        if name != controller.session["name"]:
            await controller.rename(name)
        values = {**controller.settings, **self.settings_fields.collect()}
        await controller.update_settings(values)
        self.host.show_status("Nastavení session uloženo.", False)

    # ---------- příkazy ----------

    def _build_order_dialog(self) -> None:
        # Nový příkaz: strana, druh, cena, SL / TP, velikost (loty nebo riziko), expirace, štítek
        with ui.dialog() as self.order_dialog, ui.card().classes("tv-settings replay-order-dialog"):
            self.order_title = settings_header(self.order_dialog, "Nový příkaz")
            self.order_side = ui.toggle({"buy": "Buy / Long", "sell": "Sell / Short"}, value="buy").classes(
                "replay-side-toggle")
            self.order_type = ui.toggle(ORDER_TYPES, value="market").classes("replay-type-toggle")
            self.order_quote = ui.label("").classes("settings-note")
            self.order_price = ui.number("Cena (limit / stop)").props("outlined dense").classes("w-full")
            with ui.row().classes("replay-qty-row w-full"):
                self.order_qty_mode = ui.select(QTY_MODES, value="risk_pct", label="Velikost").props("outlined dense")
                self.order_qty = ui.number("Hodnota", value=1, min=0).props("outlined dense")
            with ui.row().classes("replay-qty-row w-full"):
                self.order_sl = ui.number("Stop loss (cena)").props("outlined dense")
                self.order_tp = ui.number("Take profit (cena)").props("outlined dense")
            self.order_summary = ui.label("").classes("replay-order-summary")
            self.order_expires = ui.input("Expirace (prázdné = GTC)").props(
                "outlined dense clearable type=datetime-local").classes("w-full")
            with ui.row().classes("replay-qty-row w-full"):
                self.order_tag = ui.input("Štítek setupu").props("outlined dense")
                self.order_note = ui.input("Poznámka").props("outlined dense")
            with ui.row().classes("tv-settings-actions w-full"):
                ui.space()
                ui.button("Cancel", on_click=self.order_dialog.close, color=None).props("outline no-caps").classes(
                    "tv-btn-cancel")
                self.order_submit = ui.button("Odeslat", on_click=self.submit_order, color=None).props(
                    "unelevated no-caps").classes("replay-buy")
        for field in (self.order_side, self.order_type, self.order_price, self.order_qty_mode, self.order_qty,
                      self.order_sl, self.order_tp):
            field.on_value_change(lambda _e: self._paint_order())

    def open_order(self, side: str, kind: str = "market", price: float | None = None,
                   sl: float | None = None, tp: float | None = None) -> None:
        """Otevře dialog příkazu (Buy / Sell v liště, nástroj Long / Short position).

        Parametry:
            side: "buy" / "sell".
            kind: druh příkazu (market / limit / stop).
            price, sl, tp: předvyplněné úrovně (None = prázdné).
        """
        if not self.active:
            return
        # Při zadávání příkazu se cena nesmí měnit – přehrávání se pozastaví
        self.controller.pause()
        self.order_drawing = None
        self.order_side.value = side
        self.order_type.value = kind
        self.order_price.value = price
        self.order_sl.value = sl
        self.order_tp.value = tp
        self.order_expires.value = None
        self.order_tag.value = ""
        self.order_note.value = ""
        if self.order_qty_mode.value != "lots" and not sl:
            # Bez stopu nejde velikost podle rizika – výchozí 1 lot
            self.order_qty_mode.value = "lots"
            self.order_qty.value = 1
        self._paint_order()
        self.order_dialog.open()

    def open_order_from_drawing(self, kind: str, points: list[dict], drawing: tuple | None = None) -> None:
        """Pozice nakreslená nástrojem Long / Short position v replayi → dialog příkazu.

        Druh příkazu podle polohy vstupu vůči ceně: long pod cenou = Limit,
        nad cenou = Stop (u shortu obráceně); vstup do 1 ticku od ceny = Market.

        Parametry:
            kind: "long" / "short".
            points: body kresby [vstup, cíl, stop].
            drawing: (graf, id kresby) – kresba se po odeslání příkazu smaže
                (nahradí ji čáry příkazu); po Cancel zůstane k doladění.
        """
        if not self.active or kind not in ("long", "short") or len(points) < 3:
            return
        engine = self.controller.engine
        side = "buy" if kind == "long" else "sell"
        entry = float(points[0]["price"])
        reference = engine.ask() if side == "buy" else engine.bid()
        tick = self.controller.settings["tick"]
        if abs(entry - reference) <= tick:
            order_kind = "market"
        elif side == "buy":
            order_kind = "limit" if entry < reference else "stop"
        else:
            order_kind = "limit" if entry > reference else "stop"
        self.order_qty_mode.value = "risk_pct"
        self.order_qty.value = 1
        self.open_order(side, order_kind, None if order_kind == "market" else entry,
                        float(points[2]["price"]), float(points[1]["price"]))
        self.order_drawing = drawing

    def _order_values(self) -> dict:
        # Hodnoty dialogu příkazu: vstupní cena (market = ask / bid), úrovně a velikost v lotech
        controller = self.controller
        engine = controller.engine
        side = self.order_side.value
        kind = self.order_type.value
        market = engine.ask() if side == "buy" else engine.bid()
        entry = market if kind == "market" else positive_number(self.order_price.value)
        sl = positive_number(self.order_sl.value)
        tp = positive_number(self.order_tp.value)
        lots = 0.0
        if entry:
            lots = position_size(self.order_qty_mode.value, self.order_qty.value, entry, sl, controller.settings,
                                 engine.balance, controller.rate_now())
        return {"side": side, "type": kind, "entry": entry, "market": market, "sl": sl, "tp": tp, "lots": lots}

    def _paint_order(self) -> None:
        # Aktuální cena, viditelnost polí a souhrn (loty, riziko, R:R) v dialogu příkazu
        if not self.active:
            return
        controller = self.controller
        engine = controller.engine
        values = self._order_values()
        pending = values["type"] != "market"
        self.order_price.set_visibility(pending)
        self.order_expires.set_visibility(pending)
        self.order_quote.text = f"Bid {fmt_price(engine.bid())} · Ask {fmt_price(engine.ask())} · {controller.session['symbol']}"
        self.order_submit.text = f"{SIDES[values['side']]} {ORDER_TYPES[values['type']]}"
        self.order_submit.classes(replace="replay-buy" if values["side"] == "buy" else "replay-sell")
        parts = [f"{values['lots']:g} lotu"]
        entry, sl, tp = values["entry"], values["sl"], values["tp"]
        units_value = values["lots"] * controller.settings["contract_size"] * controller.rate_now()
        if entry and sl:
            risk = abs(entry - sl) * units_value
            pct = risk / engine.balance * 100 if engine.balance > 0 else 0
            parts.append(f"riziko {fmt_money(risk, controller.currency)} ({pct:.2f} %)")
            if tp:
                parts.append(f"R:R 1 : {abs(tp - entry) / abs(entry - sl):.2f}")
        elif self.order_qty_mode.value != "lots":
            parts.append("velikost podle rizika potřebuje stop loss")
        self.order_summary.text = " · ".join(parts)

    async def submit_order(self) -> None:
        """Zkontroluje a odešle příkaz (cena limitu / stopu vůči trhu, poloha SL / TP, velikost)."""
        values = self._order_values()
        side, kind, entry = values["side"], values["type"], values["entry"]
        market = values["market"]
        if entry is None:
            ui.notify("Zadejte cenu příkazu.", type="warning")
            return
        if kind != "market":
            # Buy limit a sell stop leží pod trhem, buy stop a sell limit nad ním
            want_below = (side == "buy") == (kind == "limit")
            if (entry < market) != want_below:
                ui.notify(f"{SIDES[side]} {kind} musí být {'pod' if want_below else 'nad'} aktuální cenou "
                          f"({fmt_price(market)}).", type="warning")
                return
        error = level_error(side, entry, values["sl"], values["tp"])
        if error:
            ui.notify(error, type="warning")
            return
        if values["lots"] <= 0:
            ui.notify("Velikost pozice vychází nulová (zkontrolujte velikost, stop loss a krok lotu).", type="warning")
            return
        expires = parse_local_datetime(self.order_expires.value) if kind != "market" else None
        if expires is not None and expires <= self.controller.now_ms:
            ui.notify("Expirace musí být po aktuálním čase replaye.", type="warning")
            return
        self.order_dialog.close()
        command = {"op": "order", "side": side, "type": kind, "qty": values["lots"],
                   "price": None if kind == "market" else entry, "sl": values["sl"], "tp": values["tp"],
                   "expires": expires}
        events = await self.controller.submit(command)
        if any(event.type == "rejected" for event in events):
            return
        tag, note = (self.order_tag.value or "").strip(), (self.order_note.value or "").strip()
        if tag or note:
            await self.controller.annotate(self.controller.engine.next_id - 1, note, tag)
        # Kresbu pozice, ze které příkaz vznikl, nahradí čáry příkazu
        drawing, self.order_drawing = self.order_drawing, None
        if drawing and drawing[1]:
            await self.host.delete_drawing(*drawing)

    async def close_trade(self, trade_id: int, qty: float | None = None) -> None:
        """Zavře obchod (nebo jeho část) za tržní cenu (neexistující / uzavřený engine ignoruje)."""
        command = {"op": "close", "id": int(trade_id)}
        if qty is not None:
            command["qty"] = qty
        await self.controller.submit(command)

    async def break_even(self, trade: dict) -> None:
        """Přesune stop obchodu na cenu vstupu (jen když je cena na správné straně)."""
        engine = self.controller.engine
        reference = engine.bid() if trade["long"] else engine.ask()
        error = level_error(trade["side"], reference, trade["entry_price"], None)
        if error:
            ui.notify(f"Break-even teď nejde: {error}", type="warning")
            return
        await self.controller.submit({"op": "modify", "id": trade["id"], "sl": trade["entry_price"]})

    async def cancel_order(self, order_id: int) -> None:
        """Zruší čekající příkaz."""
        await self.controller.submit({"op": "cancel", "id": int(order_id)})

    async def on_chart_modify(self, item_id: int, field: str, price: float | None) -> None:
        """Tažení / ✕ úrovně v grafu: SL, TP obchodu nebo cena, SL, TP příkazu (zkontroluje polohu)."""
        if not self.active or field not in ("sl", "tp", "price"):
            return
        engine = self.controller.engine
        trade = engine.trades.get(item_id)
        order = engine.orders.get(item_id)
        if trade is not None and not trade.closed and field != "price":
            # Otevřený obchod: SL / TP vůči aktuální ceně (long za bid, short za ask)
            reference = engine.bid() if trade.long else engine.ask()
            error = level_error(trade.side, reference, price if field == "sl" else None,
                                price if field == "tp" else None)
        elif order is not None and order.status == "pending":
            if field == "price" and price is None:
                await self.cancel_order(item_id)
                return
            entry = price if field == "price" else order.price
            sl = price if field == "sl" else order.sl
            tp = price if field == "tp" else order.tp
            error = level_error(order.side, entry, sl, tp)
        else:
            error = "Obchod nebo příkaz už neexistuje."
        if error:
            ui.notify(error, type="warning")
        else:
            await self.controller.submit({"op": "modify", "id": item_id, field: price})
        # Změna je zpracovaná – tažená úroveň se vrátí na platná data
        for pane in self.covered_panes():
            pane.chart.end_replay_drag()

    async def handle_chart_event(self, pane, event_type: str, payload: dict, points: list[dict]) -> bool:
        """Události grafu, které patří replayi; True = vyřízeno (stránka je dál nezpracuje).

        Výběr času (start, krok zpět), obchod z kresby pozice, ✕ a tažení úrovní
        v grafu; alerty v grafu replaye nejsou (graf ukazuje minulost).
        """
        if event_type in ("alert_request", "alert_action") and pane.replay_covers():
            self.host.alerts_blocked()
            return True
        if event_type == "replay_pick":
            await self.on_pick(pane, int(payload.get("time") or 0))
        elif event_type == "replay_position_order":
            # Pravý klik / dvojklik na Long / Short position: obchod z kresby
            self.open_order_from_drawing(payload.get("kind"), points, (pane, int(payload.get("id") or 0)))
        elif event_type == "replay_close":
            await self.close_trade(int(payload.get("id") or 0))
        elif event_type == "replay_cancel":
            await self.cancel_order(int(payload.get("id") or 0))
        elif event_type == "replay_modify":
            price = payload.get("price")
            await self.on_chart_modify(int(payload.get("id") or 0), str(payload.get("field")),
                                       float(price) if price else None)
        else:
            return False
        return True

    def drawing_hint(self, pane, kind: str) -> str | None:
        """Nápověda po nakreslení kresby v replayi (pozice = obchod se zadá z menu), jinak None."""
        if kind in ("long", "short") and pane.replay_covers():
            return ("Pozice nakreslena – upravte SL / TP a obchod zadejte pravým klikem → Zadat obchod… "
                    "(nebo dvojklikem).")
        return None

    def show_trade(self, trade: dict) -> None:
        """Posune aktivní graf na vstup obchodu (klik na řádek historie)."""
        pane = self.host.active()
        if pane.replay_covers():
            self.host.show_time(pane, trade["entry_time"])

    # ---------- úprava obchodu / příkazu ----------

    def _build_edit_dialog(self) -> None:
        with ui.dialog() as self.edit_dialog, ui.card().classes("tv-settings replay-order-dialog"):
            self.edit_title = settings_header(self.edit_dialog, "Obchod")
            self.edit_info = ui.label("").classes("settings-note")
            with ui.row().classes("replay-qty-row w-full") as self.edit_levels:
                self.edit_price = ui.number("Cena příkazu").props("outlined dense")
                self.edit_qty = ui.number("Loty", min=0).props("outlined dense")
            with ui.row().classes("replay-qty-row w-full") as self.edit_stops:
                self.edit_sl = ui.number("Stop loss (prázdné = bez)").props("outlined dense")
                self.edit_tp = ui.number("Take profit (prázdné = bez)").props("outlined dense")
            with ui.row().classes("replay-qty-row w-full") as self.edit_partial:
                self.edit_percent = ui.number("Zavřít % pozice", value=50, min=1, max=100).props("outlined dense")
                ui.button("Zavřít část", color=None, on_click=self.close_part).props("flat no-caps")
                ui.button("Break-even", color=None, on_click=self._edit_break_even).props("flat no-caps")
            self.edit_cancel_order = ui.button("Zrušit příkaz", color=None, on_click=self._edit_cancel).props(
                "flat no-caps").classes("wl-danger")
            with ui.row().classes("replay-qty-row w-full"):
                self.edit_tag = ui.input("Štítek setupu").props("outlined dense")
                self.edit_note = ui.input("Poznámka").props("outlined dense")
            with ui.row().classes("tv-settings-actions w-full"):
                settings_buttons(self.edit_dialog, self.submit_edit, "Uložit")

    def open_edit(self, mode: str, item: dict) -> None:
        """Dialog úpravy: "trade" (otevřený obchod), "closed" (poznámka) nebo "order" (čekající příkaz)."""
        self.edit = {"mode": mode, "item": item}
        annotations = self.controller.session["annotations"].get(str(item["id"])) or {}
        self.edit_tag.value = annotations.get("tag", "")
        self.edit_note.value = annotations.get("note", "")
        self.edit_levels.set_visibility(mode == "order")
        self.edit_stops.set_visibility(mode != "closed")
        self.edit_partial.set_visibility(mode == "trade")
        self.edit_cancel_order.set_visibility(mode == "order")
        self.edit_sl.value = item.get("sl")
        self.edit_tp.value = item.get("tp")
        currency = self.controller.currency
        if mode == "order":
            self.edit_title.text = f"Příkaz #{item['id']}"
            self.edit_price.value = item["price"]
            self.edit_qty.value = item["qty"]
            self.edit_info.text = f"{SIDES[item['side']]} {ORDER_TYPES[item['type']]} · zadán {display_time(item['created'])}"
        elif mode == "trade":
            self.edit_title.text = f"Obchod #{item['id']}"
            self.edit_info.text = (f"{'Long' if item['long'] else 'Short'} {item['open_qty']:g} @ "
                                   f"{fmt_price(item['entry_price'])} · P&L {fmt_money(item['floating'], currency, True)}")
        else:
            self.edit_title.text = f"Obchod #{item['id']}"
            self.edit_info.text = (f"{'Long' if item['long'] else 'Short'} {item['qty']:g} · "
                                   f"{fmt_price(item['entry_price'])} → {fmt_price(item['exit_price'])} · "
                                   f"P&L {fmt_money(item['realized'], currency, True)} · {fmt_r(item['r'])} · "
                                   f"MAE {fmt_price(item['mae'])} · MFE {fmt_price(item['mfe'])}")
        self.edit_dialog.open()

    async def submit_edit(self) -> None:
        """Uloží úpravu (úrovně jen při změně, poznámku a štítek vždy)."""
        edit = self.edit
        if not edit:
            return
        mode, item = edit["mode"], edit["item"]
        sl = positive_number(self.edit_sl.value)
        tp = positive_number(self.edit_tp.value)
        engine = self.controller.engine
        if mode == "trade":
            reference = engine.bid() if item["long"] else engine.ask()
            error = level_error(item["side"], reference, sl if sl != item["sl"] else None,
                                tp if tp != item["tp"] else None)
            if error:
                ui.notify(error, type="warning")
                return
            if sl != item["sl"] or tp != item["tp"]:
                await self.controller.submit({"op": "modify", "id": item["id"], "sl": sl, "tp": tp})
        elif mode == "order":
            price = positive_number(self.edit_price.value) or item["price"]
            error = level_error(item["side"], price, sl, tp)
            if error:
                ui.notify(error, type="warning")
                return
            changes = {"price": price, "sl": sl, "tp": tp, "qty": positive_number(self.edit_qty.value) or item["qty"]}
            if any(changes[key] != item[key] for key in changes):
                await self.controller.submit({"op": "modify", "id": item["id"], **changes})
        self.edit_dialog.close()
        await self.controller.annotate(item["id"], self.edit_note.value or "", self.edit_tag.value or "")

    async def close_part(self) -> None:
        """Zavře zadané procento otevřeného obchodu."""
        edit = self.edit
        if not edit or edit["mode"] != "trade":
            return
        percent = min(100.0, max(0.0, positive_number(self.edit_percent.value) or 0))
        qty = edit["item"]["open_qty"] * percent / 100
        self.edit_dialog.close()
        await self.close_trade(edit["item"]["id"], None if percent >= 100 else qty)

    async def _edit_break_even(self) -> None:
        # Break-even z dialogu obchodu
        if self.edit and self.edit["mode"] == "trade":
            self.edit_dialog.close()
            await self.break_even(self.edit["item"])

    async def _edit_cancel(self) -> None:
        # Zrušení příkazu z dialogu
        if self.edit and self.edit["mode"] == "order":
            self.edit_dialog.close()
            await self.cancel_order(self.edit["item"]["id"])

    # ---------- export a potvrzení ----------

    def export_csv(self) -> None:
        """Stáhne obchody session jako CSV."""
        if not self.active:
            return
        controller = self.controller
        content = trades_csv(list(controller.engine.trades.values()), controller.session["annotations"],
                             controller.currency, display_time)
        name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in controller.session["name"])
        ui.download.content(content.encode("utf-8-sig"), f"replay_{name}.csv", "text/csv")

    def confirm(self, text: str, action) -> None:
        """Potvrzovací dialog; action je async funkce bez parametrů."""
        self._confirm_action = action
        self.confirm_text.text = text
        self.confirm_dialog.open()

    async def _confirm_ok(self) -> None:
        # Potvrzení: zavře dialog a provede čekající akci
        self.confirm_dialog.close()
        action, self._confirm_action = self._confirm_action, None
        if action:
            await action()

    def shutdown(self) -> None:
        """Odpojení klienta: uloží a zavře session na pozadí."""
        if self.active:
            background_tasks.create(self.controller.close())
