"""Uvítací dialog při prvním spuštění po instalaci: přehled zdrojů dat a toho,
jaká data který zdroj poskytuje (trhy, živá data, objem, co je potřeba)."""

from dataclasses import dataclass
from typing import Awaitable, Callable

from nicegui import ui

from tickview.i18n import N_, tr
from tickview.ui.templates import settings_header

# Klíč nastavení: „1“ = uvítací dialog se ukazuje při otevření stránky, „0“ =
# uživatel zaškrtl „Již nezobrazovat“; „1“ zapíše start aplikace jen do nově
# založené databáze (první spuštění po instalaci), starší instalace ho nemají
WELCOME_PENDING_KEY = "welcome_pending"


@dataclass(frozen=True)
class SourceCard:
    """Popis jednoho zdroje dat v uvítacím dialogu.

    Parametry:
        name: název zdroje (nepřekládá se).
        icon: ikona Material Icons.
        ready: True = funguje hned bez registrace, False = potřebuje účet nebo klíče.
        markets: trhy, které zdroj pokrývá.
        live: jak chodí živá data.
        volume: jaký objem zdroj dodává.
        needs: co je potřeba ke zprovoznění.
    """

    name: str
    icon: str
    ready: bool
    markets: str
    live: str
    volume: str
    needs: str

    def rows(self) -> tuple[tuple[str, str, str], ...]:
        """Řádky karty jako (ikona, popisek, hodnota); texty zatím nepřeložené."""
        return (
            ("category", N_("Trhy"), self.markets),
            ("bolt", N_("Živá data"), self.live),
            ("bar_chart", N_("Objem"), self.volume),
            ("key", N_("Potřebuje"), self.needs),
        )


# Zdroje dat v pořadí od těch, které fungují hned; texty jsou označené N_
# a překládají se až při vykreslení (jazyk se může změnit za běhu procesu)
SOURCES: tuple[SourceCard, ...] = (
    SourceCard(
        "Binance", "currency_bitcoin", True,
        N_("Kryptoměny (spot)"), N_("V reálném čase (websocket)"), N_("Skutečný objem obchodů"), N_("Nic"),
    ),
    SourceCard(
        "Yahoo Finance", "public", True,
        N_("Akcie, ETF, indexy, forex, futures"), N_("Polling, zpožděné cca 15 min"),
        N_("Burzovní objem (forex bez objemu)"), N_("Nic"),
    ),
    SourceCard(
        "CFTC", "assessment", True,
        N_("COT report (pozice velkých hráčů)"), N_("Týdně, v pátek 15:30 NY"), "–",
        N_("Nic (app token volitelně)"),
    ),
    SourceCard(
        "Alpaca", "show_chart", False,
        N_("Americké akcie a ETF"), N_("Websocket 1m (feed IEX), jinak polling"), N_("Objem z burzy IEX"),
        N_("API klíče (účet Basic zdarma)"),
    ),
    SourceCard(
        "Interactive Brokers", "account_balance", False,
        N_("Akcie, forex, kovy, CFD, futures"), N_("Podle předplatného dat"),
        N_("Akcie a futures; forex, kovy a CFD bez objemu"), N_("Běžící TWS nebo IB Gateway"),
    ),
    SourceCard(
        "cTrader", "currency_exchange", False,
        N_("Forex, kovy, indexové CFD"), N_("V reálném čase (WebSocket)"), N_("Tick volume (i pro IBKR)"),
        N_("Jen registrace na openapi.ctrader.com"),
    ),
)

def source_card(source: SourceCard) -> None:
    """Vykreslí kartu jednoho zdroje (volá se v kontextu stránky).

    Parametry:
        source: popis zdroje dat.
    """
    with ui.column().classes("welcome-card"):
        # Hlavička karty: ikona, název a štítek dostupnosti (vpravo, při
        # nedostatku místa na dalším řádku)
        with ui.row().classes("welcome-card-head w-full"):
            ui.icon(source.icon).classes("welcome-card-icon")
            ui.label(source.name).classes("welcome-card-name")
            badge, variant = (N_("Bez registrace"), "ready") if source.ready else (N_("Nutný účet"), "setup")
            ui.label(tr(badge)).classes(f"welcome-badge welcome-badge-{variant}")
        # Vlastnosti zdroje: ikona, popisek a hodnota pod sebou
        for icon, label, value in source.rows():
            with ui.row().classes("welcome-row w-full"):
                ui.icon(icon).classes("welcome-row-icon")
                with ui.column().classes("welcome-row-text"):
                    ui.label(tr(label)).classes("welcome-row-label")
                    ui.label(tr(value)).classes("welcome-row-value")


def build_welcome_dialog(
    open_settings: Callable[[], None], set_hidden: Callable[[bool], Awaitable[None]], hidden: bool
) -> ui.dialog:
    """Vytvoří (zavřený) uvítací dialog s přehledem zdrojů dat v aktuálním kontextu stránky.

    Parametry:
        open_settings: otevře Settings aplikace (tlačítko pro zadání klíčů zdrojů).
        set_hidden: uloží volbu „Již nezobrazovat“ (True = dialog se už neukáže).
        hidden: počáteční stav volby „Již nezobrazovat“.
    """

    def go_to_settings() -> None:
        # Uvítání zavřeme, ať Settings nejsou pod ním
        dialog.close()
        open_settings()

    with ui.dialog() as dialog, ui.card().classes("tv-settings welcome-dialog"):
        settings_header(dialog, tr("Vítejte v TickView"))
        ui.label(
            tr("Grafy můžete prohlížet hned – Binance a Yahoo Finance fungují bez registrace. "
               "Další zdroje zapnete v Settings (ozubené kolečko vpravo nahoře).")
        ).classes("welcome-intro")
        with ui.element("div").classes("welcome-grid w-full"):
            for source in SOURCES:
                source_card(source)
        ui.label(
            tr("Symbol z konkrétního zdroje zadáte předponou, např. yahoo:AAPL nebo binance:BTCUSDT.")
        ).classes("welcome-hint")
        with ui.row().classes("tv-settings-actions w-full"):
            # Volba se uloží hned při změně – platí i při zavření křížkem nebo Esc
            ui.checkbox(
                tr("Již nezobrazovat"), value=hidden, on_change=lambda event: set_hidden(bool(event.value))
            ).props("dense").classes("welcome-hide")
            ui.space()
            ui.button(tr("Otevřít Settings"), color=None, on_click=go_to_settings).props(
                "flat no-caps"
            ).classes("tv-btn-cancel")
            ui.button(tr("Začít"), color=None, on_click=dialog.close).props("unelevated no-caps").classes("tv-btn-ok")
    return dialog
