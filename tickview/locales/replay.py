"""Anglické překlady: replay (tickview/logic/replay.py, tickview/services/replay.py,
tickview/ui/replay_panel.py) a načítání dat grafu (tickview/ui/chart_pane.py)."""

EN = {
    # ----- logika replaye (výčty, události enginu, CSV export) -----
    "Loty / kontrakty": "Lots / contracts",
    "Riziko % účtu": "Risk % of account",
    "Riziko částkou": "Risk amount",
    "Ručně": "Manual",
    "Částečně": "Partial",
    "Povolit – obchody po bodu návratu se smažou": "Allow – trades after the rewind point are deleted",
    "Povolit po potvrzení – zeptat se před smazáním obchodů": (
        "Allow after confirmation – ask before deleting trades"
    ),
    "Zakázat krok zpět": "Disable rewind",
    "1 bar grafu": "1 chart bar",
    "1 bar základu": "1 base bar",
    "#{id} {direction} {qty} naplněn za {price}": "#{id} {direction} {qty} filled at {price}",
    "#{id} uzavřen ({reason}) za {price}, P&L {pnl}": "#{id} closed ({reason}) at {price}, P&L {pnl}",
    "Příkaz #{id} expiroval": "Order #{id} expired",
    "Neplatný příkaz (strana, druh nebo velikost)": "Invalid order (side, type or size)",
    "Limit / stop příkaz potřebuje cenu": "A limit / stop order needs a price",
    "strana": "side",
    "druh vstupu": "entry type",
    "loty": "lots",
    "vstup čas": "entry time",
    "vstup cena": "entry price",
    "výstup čas": "exit time",
    "výstup cena": "exit price",
    "důvod": "reason",
    "komise": "commission",
    "odhad": "estimate",
    "štítek": "tag",
    "poznámka": "note",
    "ano": "yes",
    # ----- služba replaye (stavová lišta) -----
    "Kurz {quote} → {account} se nepodařilo zjistit – P&L je v {quote} (kurz 1).": (
        "Could not get the {quote} → {account} rate – P&L is in {quote} (rate 1)."
    ),
    "{symbol}: pro zvolený čas nejsou dostupná data replaye.": (
        "{symbol}: no replay data available for the selected time."
    ),
    "Session replaye neexistuje.": "The replay session does not exist.",
    "Neznámý zdroj session: {source}": "Unknown session data source: {source}",
    "Načítám session {name}…": "Loading session {name}…",
    "Data session se nepodařilo načíst: {error}": "Failed to load session data: {error}",
    "Session nemá data (zdroj je nevrátil).": "The session has no data (the source returned none).",
    "Další data replaye se nepodařilo načíst: {error}": "Failed to load more replay data: {error}",
    "Replay došel na konec dostupných dat.": "Replay reached the end of available data.",
    "Přeskakuji…": "Skipping…",
    "Přeskakuji na {time}…": "Skipping to {time}…",
    "Replay vrácen zpět – smazáno příkazů: {count}.": "Replay rewound – orders deleted: {count}.",
    "Replay vrácen zpět.": "Replay rewound.",
    "Přehrávání selhalo: {error}": "Playback failed: {error}",
    # ----- graf (načítání dat, živý stream) -----
    "Neznámý zdroj: {source}": "Unknown data source: {source}",
    "{symbol} · {timeframe} · {count} svíček · zdroj {source}": (
        "{symbol} · {timeframe} · {count} candles · source {source}"
    ),
    "Načítám {symbol} ({timeframe})…": "Loading {symbol} ({timeframe})…",
    "Pro {symbol} ({timeframe}) nejsou dostupná data.": "No data available for {symbol} ({timeframe}).",
    "Načtení dat selhalo: {error}": "Failed to load data: {error}",
    "Replay: načítám {symbol} ({timeframe})…": "Replay: loading {symbol} ({timeframe})…",
    "Replay {symbol} · {timeframe} · {count} svíček": "Replay {symbol} · {timeframe} · {count} candles",
    "Načtení dat replaye selhalo: {error}": "Failed to load replay data: {error}",
    "Starší data {symbol} se nepodařilo načíst: {error}": "Failed to load older data for {symbol}: {error}",
    "{symbol} ({timeframe}): starší data nejsou k dispozici.": "{symbol} ({timeframe}): no older data available.",
    "Starší data se nepodařilo načíst: {error}": "Failed to load older data: {error}",
    "Živý stream {symbol} přerušen, čekám na znovupřipojení: {reason}": (
        "Live stream {symbol} interrupted, waiting to reconnect: {reason}"
    ),
    "{symbol}: trh je pravděpodobně zavřený – živá data nepřibývají": (
        "{symbol}: the market is probably closed – no new live data"
    ),
    "Živý stream ukončen chybou: {error}": "Live stream ended with an error: {error}",
    # ----- panel a dialogy replaye -----
    "Automaticky (nejjemnější dostupná data)": "Automatic (finest available data)",
    "1 minuta": "1 minute",
    "5 minut": "5 minutes",
    "15 minut": "15 minutes",
    "1 hodina": "1 hour",
    "1 den": "1 day",
    "Stop loss musí být pod cenou {price}.": "Stop loss must be below the price {price}.",
    "Stop loss musí být nad cenou {price}.": "Stop loss must be above the price {price}.",
    "Take profit musí být nad cenou {price}.": "Take profit must be above the price {price}.",
    "Take profit musí být pod cenou {price}.": "Take profit must be below the price {price}.",
    "Měna účtu": "Account currency",
    "Počáteční zůstatek": "Initial balance",
    "Jednotek v 1 lotu / kontraktu": "Units per lot / contract",
    "Nejmenší krok lotu": "Lot step",
    "Tick (nejmenší změna ceny)": "Tick (minimum price change)",
    "Pevný kurz k měně účtu (0 = auto)": "Fixed rate to account currency (0 = auto)",
    "Spread (ticky)": "Spread (ticks)",
    "Skluz market / stop (ticky)": "Slippage market / stop (ticks)",
    "Komise za lot a stranu (měna účtu)": "Commission per lot and side (account currency)",
    "Automatická pauza přehrávání": "Auto-pause playback",
    "Po naplnění příkazu": "When an order is filled",
    "Po uzavření obchodu (SL / TP)": "When a trade is closed (SL / TP)",
    "Při signálu indikátoru na uzavřené svíčce": "On an indicator signal on a closed candle",
    "Replay pozastaven: signál {signal} ({indicator}).": "Replay paused: signal {signal} ({indicator}).",
    "Replay – backtest ručních obchodů v historii": "Replay – backtest manual trades on history",
    "Sessions replaye": "Replay sessions",
    "Vrátit replay zpět – klikněte do grafu na svíčku, od které pokračovat": (
        "Rewind replay – click the candle on the chart to continue from"
    ),
    "Přehrát / pozastavit (mezerník)": "Play / pause (Space)",
    "Krok vpřed (Shift+→)": "Step forward (Shift+→)",
    "Krok tlačítka vpřed": "Step forward unit",
    "Rychlost přehrávání (základních svíček za sekundu)": "Playback speed (base candles per second)",
    "Skok na datum a čas": "Jump to date and time",
    "Čas dne pro skok na další den (místní čas)": "Time of day for jumping to the next day (local time)",
    "Skok na nejbližší zvolený čas dne (další klik = další den, víkendy a dny bez dat se přeskočí)": (
        "Jump to the next selected time of day (next click = next day, weekends and days without data are skipped)"
    ),
    "Nový nákupní příkaz (Market / Limit / Stop)": "New buy order (Market / Limit / Stop)",
    "Nový prodejní příkaz (Market / Limit / Stop)": "New sell order (Market / Limit / Stop)",
    "Panel obchodů": "Trading panel",
    "Nastavení session (účet, náklady, automatická pauza)": "Session settings (account, costs, auto-pause)",
    "Ukončit replay (session zůstane uložená)": "Exit replay (the session stays saved)",
    "Pozice": "Positions",
    "Příkazy": "Orders",
    "Historie": "History",
    "Statistiky": "Statistics",
    "Export obchodů do CSV": "Export trades to CSV",
    "Skrýt panel": "Hide panel",
    "{time} · přesnost {timeframe}": "{time} · precision {timeframe}",
    "Zůstatek {balance} · Equity {equity}": "Balance {balance} · Equity {equity}",
    "Žádné otevřené pozice. Buy / Sell v liště nebo nástroj Long / Short position v grafu.": (
        "No open positions. Use Buy / Sell in the bar or the Long / Short position tool on the chart."
    ),
    "Strana": "Side",
    "Loty": "Lots",
    "Vstup": "Entry",
    "Upravit SL / TP, částečné zavření, poznámka": "Edit SL / TP, partial close, note",
    "Stop na vstup (break-even)": "Stop to entry (break-even)",
    "Zavřít polovinu pozice": "Close half of the position",
    "Zavřít pozici za trh": "Close position at market",
    "Žádné čekající příkazy.": "No pending orders.",
    "Příkaz": "Order",
    "Cena": "Price",
    "Zadáno": "Placed",
    "Expirace": "Expiration",
    "Upravit příkaz": "Edit order",
    "Zrušit příkaz": "Cancel order",
    "Zatím žádné uzavřené obchody.": "No closed trades yet.",
    "Výstup": "Exit",
    "Důvod": "Reason",
    "Štítek": "Tag",
    "Odhad: stop i cíl (nebo vstup a stop) v jedné základní svíčce – počítá se konzervativně stop.": (
        "Estimate: stop and target (or entry and stop) within one base candle – the stop is assumed conservatively."
    ),
    "Poznámka a štítek setupu": "Note and setup tag",
    "Čistý zisk": "Net profit",
    "Obchodů": "Trades",
    "Průměrná výhra": "Avg win",
    "Průměrná ztráta": "Avg loss",
    "Expectancy / obchod": "Expectancy / trade",
    "Průměrné R / celkem": "Avg R / total",
    "Nejdelší série ztrát": "Max losing streak",
    "Největší výhra / ztráta": "Largest win / loss",
    "Komise": "Commission",
    "Odhadnuté obchody": "Estimated trades",
    "Průměrné R": "Avg R",
    "Nová session pro symbol aktivního grafu": "New session for the active chart's symbol",
    "Nová session replaye": "New replay session",
    "Název": "Name",
    "Začátek (historie odkrytá do)": "Start (history revealed up to)",
    "Vybrat v grafu": "Pick on chart",
    "Klikněte do grafu na svíčku, od které replay začne": "Click the candle on the chart where the replay starts",
    "Přesnost simulace (základní timeframe)": "Simulation precision (base timeframe)",
    "Příkazy, stopy a cíle se vyhodnocují na základním timeframu (svíčky grafu se z něj skládají). "
    "Starší data mají u některých zdrojů jen hrubší timeframe (Yahoo 1m jen ~30 dní).": (
        "Orders, stops and targets are evaluated on the base timeframe (chart candles are built from it). "
        "For some sources older data only exist on a coarser timeframe (Yahoo 1m only ~30 days)."
    ),
    "Spustit replay": "Start replay",
    "Přejmenovat session": "Rename session",
    "Graf nemá symbol – nejdřív zvolte symbol.": "The chart has no symbol – select a symbol first.",
    "Symbol: {symbol} (aktivní graf)": "Symbol: {symbol} (active chart)",
    "Zatím žádné sessions – tlačítko + založí novou pro symbol aktivního grafu.": (
        "No sessions yet – the + button creates a new one for the active chart's symbol."
    ),
    "Otevřená session": "Current session",
    "Otevřít session": "Open session",
    "{symbol} · přesnost {timeframe}": "{symbol} · precision {timeframe}",
    "obchodů {count}": "trades {count}",
    "Přejmenovat": "Rename",
    "Smazat session (i její obchody a kresby)": "Delete session (including its trades and drawings)",
    "Zadejte název (1–{max_length} znaků).": "Enter a name (1–{max_length} characters).",
    "Zadejte začátek replaye.": "Enter the replay start.",
    "Začátek replaye musí být v minulosti.": "The replay start must be in the past.",
    "Zakládám replay {name} – hledám data…": "Creating replay {name} – looking for data…",
    "Replay {name}: {time}. Mezerník = play / pauza, Shift+→ = krok.": (
        "Replay {name}: {time}. Space = play / pause, Shift+→ = step."
    ),
    "Smazat session „{name}“ včetně obchodů a kreseb?": (
        "Delete session “{name}” including its trades and drawings?"
    ),
    "Session replaye smazána.": "Replay session deleted.",
    "Replay ukončen – session je uložená (záložka Replay v pravém panelu).": (
        "Replay exited – the session is saved (Replay tab in the right panel)."
    ),
    "Klikněte do grafu na svíčku, od které má replay začít (Esc = zrušit).": (
        "Click the candle on the chart where the replay should start (Esc = cancel)."
    ),
    "Krok zpět je vypnutý (Settings → Replay).": "Rewind is disabled (Settings → Replay).",
    "Klikněte do grafu na svíčku, od které replay pokračuje (Esc = zrušit).": (
        "Click the candle on the chart to continue the replay from (Esc = cancel)."
    ),
    "Krok zpět smaže {count} příkazů / změn zadaných po {time}. Pokračovat?": (
        "Rewinding deletes {count} orders / changes placed after {time}. Continue?"
    ),
    "Datum a čas": "Date and time",
    "Pozdější čas = skok vpřed (obchody se vyhodnotí), dřívější = krok zpět.": (
        "Later time = jump forward (trades are evaluated), earlier = rewind."
    ),
    "Skočit": "Jump",
    "Zadejte datum a čas.": "Enter a date and time.",
    "Zadejte čas dne (HH:MM).": "Enter a time of day (HH:MM).",
    "Nastavení session": "Session settings",
    "Změna účtu nebo nákladů přepočítá všechny obchody session.": (
        "Changing the account or costs recalculates all trades of the session."
    ),
    "Nastavení session uloženo.": "Session settings saved.",
    "Nový příkaz": "New order",
    "Cena (limit / stop)": "Price (limit / stop)",
    "Velikost": "Size",
    "Hodnota": "Value",
    "Stop loss (cena)": "Stop loss (price)",
    "Take profit (cena)": "Take profit (price)",
    "Expirace (prázdné = GTC)": "Expiration (empty = GTC)",
    "Štítek setupu": "Setup tag",
    "Poznámka": "Note",
    "Odeslat": "Submit",
    "{lots} lotu": "{lots} lots",
    "riziko {amount} ({percent} %)": "risk {amount} ({percent} %)",
    "velikost podle rizika potřebuje stop loss": "risk-based size needs a stop loss",
    "Zadejte cenu příkazu.": "Enter the order price.",
    "{order} musí být pod aktuální cenou ({price}).": "{order} must be below the current price ({price}).",
    "{order} musí být nad aktuální cenou ({price}).": "{order} must be above the current price ({price}).",
    "Velikost pozice vychází nulová (zkontrolujte velikost, stop loss a krok lotu).": (
        "The position size comes out as zero (check the size, stop loss and lot step)."
    ),
    "Expirace musí být po aktuálním čase replaye.": "Expiration must be after the current replay time.",
    "Break-even teď nejde: {reason}": "Break-even is not possible now: {reason}",
    "Obchod nebo příkaz už neexistuje.": "The trade or order no longer exists.",
    "Pozice nakreslena – upravte SL / TP a obchod zadejte pravým klikem → Zadat obchod… (nebo dvojklikem).": (
        "Position drawn – adjust SL / TP and place the trade with right-click → Place order… (or double-click)."
    ),
    "Obchod": "Trade",
    "Cena příkazu": "Order price",
    "Stop loss (prázdné = bez)": "Stop loss (empty = none)",
    "Take profit (prázdné = bez)": "Take profit (empty = none)",
    "Zavřít % pozice": "Close % of position",
    "Zavřít část": "Close part",
    "Uložit": "Save",
    "Příkaz #{id}": "Order #{id}",
    "{order} · zadán {time}": "{order} · placed {time}",
    "Obchod #{id}": "Trade #{id}",
}
