"""Dialog Předplatná dat IBKR: jak u Interactive Brokers zapnout živá data.

Srovnává levnou variantu (bez futures) a dražší variantu s futures CME Group.
Ceny platí pro neprofesionálního uživatele (NP), stav k listopadu 2026.
"""

from nicegui import ui

from tickview.i18n import N_, tr
from tickview.ui.templates import settings_header

# Společný postup pro obě varianty: oprávnění, předplatné Index CFDs,
# sdílení s demo účtem a kontrola, že data chodí živě; texty se přeloží
# až při zobrazení (označené N_)
SETUP_TEXT = N_("""
Bez předplatného posílá TWS zpožděná data (10–15 minut). Aplikace si říká
o typ dat 3 (`reqMarketDataType(3)`): když předplatné máte, TWS posílá živá
data, bez něj zpožděná. Štítek **živě / zpožděno** v grafu ukazuje, co
skutečně chodí. V aplikaci se nic nenastavuje.

#### Postup (pro obě varianty)

1. **Obchodní oprávnění CFDs:** Client Portal → ikona uživatele → Settings →
   Trading → Trading Permissions → **Request** → produkt CFDs, země United
   States (pro `IBDE40`, `IBGB100`… i jejich země). Bez něj nejde přidat
   předplatné Index CFDs. Žádost se obvykle schvaluje přes noc.
2. **Předplatné Index CFDs (NP,L1):** Settings → Market Data Subscriptions →
   Configure. Je zdarma, IB ho jen musí mít zapnuté.
3. **Sdílení dat s demo účtem:** demo TWS (port 7497) dostane data jen
   z předplatného ostrého účtu, které nasdílíte v nastavení Paper Trading
   Account. Projeví se zhruba do 24 hodin.
4. **Restart TWS / IB Gateway** po každé změně předplatného.

#### Kontrola

- Porovnejte poslední svíčku s aktuální cenou jinde (např. TradingView).
- Když demo účet (7497) ukazuje zpožděná data a ostrý účet (7496) živá,
  chybí sdílení dat s demo účtem.
""")

# Varianta 1: placená jen tři L1 předplatná US burz (akcie a ETF), futures zpožděné
CHEAP_TEXT = N_("""
**4,50 USD měsíčně** (3 × 1,50 USD). Ostatní předplatná jsou zdarma
(Fee Waived).

| Předplatné | Cena |
|---|---|
| NASDAQ (Network C/UTP) (NP,L1) | 1,50 USD |
| NYSE (Network A/CTA) (NP,L1) | 1,50 USD |
| NYSE American, BATS, ARCA, IEX, and Regional Exchanges (Network B) (NP,L1) | 1,50 USD |
| **Index CFDs (NP,L1)** | zdarma |
| IDEALPRO FX (IDEAL PRO) | zdarma |
| Physical Metals and Commodities (L1) | zdarma |
| CME CF Real-Time Crypto Indices | zdarma |
| IEX Depth of Book (NP,L2) | zdarma |
| US Real-Time Non Consolidated Streaming Quotes | zdarma |
| US Mutual Funds (NP,L1), European Mutual Funds | zdarma |
| Alternative European Equities (L1) | zdarma |
| Korea Equities Bundle (NP,L2) | zdarma |
| US and EU Bond Quotes (L1) | zdarma |

| Trh | Symboly | Data |
|---|---|---|
| Indexová CFD | `IBUS500`, `IBUST100`, `IBDE40` | **živě** |
| Spotové kovy | `XAUUSD`, `XAGUSD` | **živě** |
| Forex IDEALPRO | `EUR.USD`, `GBP.USD` | **živě** |
| US akcie a ETF | `MSFT`, `SPY`, `IWM` | **živě** |
| **Futures** | `ES1!`, `NQ1!`, `GC1!`, `CL1!` | **zpožděně** |

**Pro koho:** obchodování přes CFD (`IBUS500` místo `ES1!`, `XAUUSD`
místo `GC1!`), akcie a forex. Futures se hodí jen na historii a delší
timeframy.
""")

# Varianta 2: bundle s futures CME Group + streaming US akcií a opcí
FUTURES_TEXT = N_("""
**14,50 USD měsíčně** (10 USD + 4,50 USD).

| Předplatné | Cena | Živě |
|---|---|---|
| US Securities Snapshot and Futures Value Bundle (NP,L1) | 10,00 USD | futures a opce na futures CME, CBOT, NYMEX, COMEX; US akcie jen jako snapshot |
| US Equity and Options Add-On Streaming Bundle (NP) | 4,50 USD | streaming US akcií a ETF (NYSE, Nasdaq, ARCA…) a opcí na akcie, ETF a indexy |

Add-On jde objednat jen k bundlu. Bezplatná předplatná z levné varianty
(Index CFDs, IDEALPRO FX, Physical Metals and Commodities…) nechte
zapnutá. Tři placená L1 předplatná po 1,50 USD (Network A, B a C) Add-On
pokrývá, proto je zrušte, jinak se platí dvakrát. Přesné názvy
a odpuštění poplatku ověřte v Client Portalu (ikona ⓘ).

| Trh | Symboly | Data |
|---|---|---|
| Indexová CFD, kovy, forex | `IBUS500`, `XAUUSD`, `EUR.USD` | **živě** |
| US akcie, ETF a opce | `MSFT`, `SPY` | **živě** (Add-On) |
| **Futures CME, CBOT, NYMEX, COMEX** | `ES1!`, `MES1!`, `NQ1!`, `YM1!`, `RTY1!`, `GC1!`, `SI1!`, `CL1!`, `NG1!`, `ZN1!`, `ZC1!`, `6E1!`, `BTC1!` | **živě** (bundle) |
| Futures jiných burz | `FDAX1!`, `FESX1!` (EUREX), `VX1!` (CFE) | zpožděně, vlastní předplatné |

**Pro koho:** živé futures, hlavně kvůli objemu. CFD a forex objem nemají
(data MIDPOINT), futures ano (data TRADES). Hodí se pro indikátory
založené na objemu, přesné časy otevření a rollu nebo obchodování přímo
futures.
""")

# Souhrnné srovnání obou variant v jedné tabulce
COMPARISON_TEXT = N_("""
| | Levná | S futures |
|---|---|---|
| Měsíčně | 4,50 USD | 14,50 USD |
| Indexová CFD, kovy, forex | živě | živě |
| US akcie a ETF | živě | živě |
| Opce na US akcie, ETF a indexy | – | živě |
| Futures CME Group | **zpožděně** | **živě** |
| Futures EUREX, CFE | zpožděně | zpožděně |
| Objem u indexů | jen přes ETF (`SPY`, `QQQ`) | i přes futures (`ES1!`, `NQ1!`) |
""")

# Záložky dialogu: titulek → obsah v Markdownu; český titulek je zároveň
# jméno záložky (identifikátor), přeložený se jen zobrazí jako popisek
TABS = {
    N_("Postup"): SETUP_TEXT,
    N_("Levná varianta"): CHEAP_TEXT,
    N_("Varianta s futures"): FUTURES_TEXT,
    N_("Srovnání"): COMPARISON_TEXT,
}


def build_ibkr_data_dialog() -> ui.dialog:
    """Vytvoří (zavřený) dialog s předplatnými dat IBKR v aktuálním kontextu stránky.

    Vrací dialog; otevírá se tlačítkem v záložce Interactive Brokers v Settings.
    """
    with ui.dialog() as dialog, ui.card().classes("tv-settings ibkr-data-dialog"):
        settings_header(dialog, tr("Předplatná dat Interactive Brokers"))
        ui.label(
            tr("Co si u IB předplatit pro živá data. Ceny pro neprofesionálního uživatele (NP), stav k listopadu 2026.")
        ).classes("settings-note ibkr-data-note")
        # Každá varianta na vlastní záložce, aby se nepletly
        with ui.tabs().props("dense no-caps align=left indicator-color=primary").classes("tv-tabs w-full") as tabs:
            for title in TABS:
                ui.tab(title, label=tr(title))
        with ui.tab_panels(tabs, value=next(iter(TABS))).classes("tv-tab-panels w-full"):
            for title, text in TABS.items():
                with ui.tab_panel(title).classes("tv-tab-panel ibkr-data-panel"):
                    ui.markdown(tr(text)).classes("tv-markdown")
        with ui.row().classes("tv-settings-actions w-full"):
            ui.space()
            ui.button(tr("Zavřít"), color=None, on_click=dialog.close).props("flat no-caps").classes("tv-btn-cancel")
    return dialog
