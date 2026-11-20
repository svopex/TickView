"""Anglické překlady: šablony nastavení, časové připomínky, předplatná dat IBKR a pole
stylu výstupů (tickview/ui/templates.py, reminders_dialog.py, notify_fields.py,
ibkr_data_dialog.py, plot_style_fields.py)."""

EN = {
    # Šablony nastavení (templates.py)
    "Uložit šablonu": "Save template",
    "Název šablony": "Template name",
    "Nejvýš {max_length} znaků": "At most {max_length} characters",
    "Smazat šablonu": "Delete template",
    "Použita šablona „{name}“.": "Template “{name}” applied.",
    "Šablona „{name}“ smazána.": "Template “{name}” deleted.",
    "Zadejte název šablony (1–{max_length} znaků).": "Enter a template name (1–{max_length} characters).",
    "Šablona „{name}“ přepsána.": "Template “{name}” overwritten.",
    "Šablona „{name}“ uložena.": "Template “{name}” saved.",
    "Uloženo jako výchozí nastavení.": "Saved as default.",
    "Výchozí nastavení vráceno na tovární.": "Default reset to factory settings.",
    # Časové připomínky (reminders_dialog.py)
    "Časové připomínky": "Time reminders",
    "Opakovaná notifikace v časovém okně, např. výzva k uzavření pozic před noční seancí.": (
        "Repeated notification within a time window, e.g. a reminder to close positions before the overnight session."
    ),
    "Přidat připomínku": "Add reminder",
    "Zavřít": "Close",
    "Připomínka": "Reminder",
    "Název": "Name",
    "Od (HH:MM)": "From (HH:MM)",
    "Do (HH:MM)": "To (HH:MM)",
    "Rozestup (min)": "Interval (min)",
    "Časové pásmo": "Time zone",
    "Zpráva": "Message",
    "Notifikace": "Notifications",
    "Žádná připomínka.": "No reminders.",
    "Zapnout / vypnout": "Turn on / off",
    "Upravit": "Edit",
    "Smazat": "Delete",
    "Upravit připomínku": "Edit reminder",
    "Nová připomínka": "New reminder",
    # Kanály notifikací (notify_fields.py)
    "Oznámení v aplikaci": "In-app notification",
    "Zvuk": "Sound",
    "Webhook URL (prázdné = neposílat)": "Webhook URL (empty = don't send)",
    # Pole stylu výstupů indikátoru (plot_style_fields.py)
    "{name} – barva a čára": "{name} – color and line",
    # Předplatná dat IBKR (ibkr_data_dialog.py)
    "Předplatná dat Interactive Brokers": "Interactive Brokers data subscriptions",
    "Co si u IB předplatit pro živá data. Ceny pro neprofesionálního uživatele (NP), stav k listopadu 2026.": (
        "What to subscribe to at IB for live data. Prices for a non-professional user (NP), as of November 2026."
    ),
    "Postup": "Setup",
    "Levná varianta": "Budget option",
    "Varianta s futures": "Option with futures",
    "Srovnání": "Comparison",
    """
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
""": """
Without a subscription, TWS sends delayed data (10–15 minutes). The app requests
market data type 3 (`reqMarketDataType(3)`): with a subscription, TWS sends live
data, without it delayed data. The **live / delayed** label on the chart shows
what actually arrives. Nothing needs to be set in the app.

#### Setup (for both options)

1. **CFDs trading permission:** Client Portal → user icon → Settings →
   Trading → Trading Permissions → **Request** → product CFDs, country United
   States (for `IBDE40`, `IBGB100`… also their countries). Without it you cannot
   add the Index CFDs subscription. The request is usually approved overnight.
2. **Index CFDs subscription (NP,L1):** Settings → Market Data Subscriptions →
   Configure. It is free, IB just needs to have it enabled.
3. **Sharing data with the paper account:** paper TWS (port 7497) gets data only
   from the live account's subscriptions, which you share in the Paper Trading
   Account settings. It takes effect within about 24 hours.
4. **Restart TWS / IB Gateway** after every subscription change.

#### Check

- Compare the last candle with the current price elsewhere (e.g. TradingView).
- If the paper account (7497) shows delayed data and the live account (7496)
  live data, data sharing with the paper account is missing.
""",
    """
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
""": """
**USD 4.50 per month** (3 × USD 1.50). All other subscriptions are free
(Fee Waived).

| Subscription | Price |
|---|---|
| NASDAQ (Network C/UTP) (NP,L1) | USD 1.50 |
| NYSE (Network A/CTA) (NP,L1) | USD 1.50 |
| NYSE American, BATS, ARCA, IEX, and Regional Exchanges (Network B) (NP,L1) | USD 1.50 |
| **Index CFDs (NP,L1)** | free |
| IDEALPRO FX (IDEAL PRO) | free |
| Physical Metals and Commodities (L1) | free |
| CME CF Real-Time Crypto Indices | free |
| IEX Depth of Book (NP,L2) | free |
| US Real-Time Non Consolidated Streaming Quotes | free |
| US Mutual Funds (NP,L1), European Mutual Funds | free |
| Alternative European Equities (L1) | free |
| Korea Equities Bundle (NP,L2) | free |
| US and EU Bond Quotes (L1) | free |

| Market | Symbols | Data |
|---|---|---|
| Index CFDs | `IBUS500`, `IBUST100`, `IBDE40` | **live** |
| Spot metals | `XAUUSD`, `XAGUSD` | **live** |
| Forex IDEALPRO | `EUR.USD`, `GBP.USD` | **live** |
| US stocks and ETFs | `MSFT`, `SPY`, `IWM` | **live** |
| **Futures** | `ES1!`, `NQ1!`, `GC1!`, `CL1!` | **delayed** |

**Who it is for:** trading via CFDs (`IBUS500` instead of `ES1!`, `XAUUSD`
instead of `GC1!`), stocks and forex. Futures are useful only for history and
higher timeframes.
""",
    """
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
""": """
**USD 14.50 per month** (USD 10 + USD 4.50).

| Subscription | Price | Live |
|---|---|---|
| US Securities Snapshot and Futures Value Bundle (NP,L1) | USD 10.00 | futures and options on futures CME, CBOT, NYMEX, COMEX; US stocks only as snapshots |
| US Equity and Options Add-On Streaming Bundle (NP) | USD 4.50 | streaming US stocks and ETFs (NYSE, Nasdaq, ARCA…) and options on stocks, ETFs and indices |

The Add-On can be ordered only together with the bundle. Keep the free
subscriptions from the budget option (Index CFDs, IDEALPRO FX, Physical
Metals and Commodities…) enabled. The Add-On covers the three paid L1
subscriptions at USD 1.50 each (Network A, B and C), so cancel them,
otherwise you pay twice. Verify the exact names and fee waivers in the
Client Portal (ⓘ icon).

| Market | Symbols | Data |
|---|---|---|
| Index CFDs, metals, forex | `IBUS500`, `XAUUSD`, `EUR.USD` | **live** |
| US stocks, ETFs and options | `MSFT`, `SPY` | **live** (Add-On) |
| **Futures CME, CBOT, NYMEX, COMEX** | `ES1!`, `MES1!`, `NQ1!`, `YM1!`, `RTY1!`, `GC1!`, `SI1!`, `CL1!`, `NG1!`, `ZN1!`, `ZC1!`, `6E1!`, `BTC1!` | **live** (bundle) |
| Futures on other exchanges | `FDAX1!`, `FESX1!` (EUREX), `VX1!` (CFE) | delayed, separate subscription |

**Who it is for:** live futures, mainly for volume. CFDs and forex have no
volume (MIDPOINT data), futures do (TRADES data). Suitable for volume-based
indicators, exact open and rollover times, or trading futures directly.
""",
    """
| | Levná | S futures |
|---|---|---|
| Měsíčně | 4,50 USD | 14,50 USD |
| Indexová CFD, kovy, forex | živě | živě |
| US akcie a ETF | živě | živě |
| Opce na US akcie, ETF a indexy | – | živě |
| Futures CME Group | **zpožděně** | **živě** |
| Futures EUREX, CFE | zpožděně | zpožděně |
| Objem u indexů | jen přes ETF (`SPY`, `QQQ`) | i přes futures (`ES1!`, `NQ1!`) |
""": """
| | Budget | With futures |
|---|---|---|
| Monthly | USD 4.50 | USD 14.50 |
| Index CFDs, metals, forex | live | live |
| US stocks and ETFs | live | live |
| Options on US stocks, ETFs and indices | – | live |
| CME Group futures | **delayed** | **live** |
| EUREX, CFE futures | delayed | delayed |
| Index volume | only via ETFs (`SPY`, `QQQ`) | also via futures (`ES1!`, `NQ1!`) |
""",
}
