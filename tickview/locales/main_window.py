"""Anglické překlady: hlavní okno (tickview/ui/main_window.py)."""

EN = {
    # Horní lišta a společné prvky
    "Jazyk rozhraní": "Interface language",
    "Tmavý motiv": "Dark theme",
    "Světlý motiv": "Light theme",
    "Motiv podle systému (Auto)": "System theme (Auto)",
    "{theme} – klikem přepnete": "{theme} – click to switch",
    "O aplikaci TickView": "About TickView",
    "Hledat symbol (nebo začněte psát nad grafem)": "Search symbol (or start typing over the chart)",
    "Všechny timeframy": "All timeframes",
    "Typ grafu": "Chart type",
    "Indikátory": "Indicators",
    "Indikátory (nabídka s hledáním a kategoriemi)": "Indicators (menu with search and categories)",
    "Šablony indikátorů": "Indicator templates",
    "Vytvořit alert pro aktuální symbol": "Create alert for the current symbol",
    "živě": "live",
    "zpožděno": "delayed",
    "Rozložení: {count}.": "Layout: {count}.",
    "graf": "chart",
    "grafy": "charts",
    "grafů": "charts",
    "Místní čas": "Local time",
    "Časové pásmo grafu": "Chart time zone",
    "Nastavení": "Settings",
    "Snímek grafu": "Take a snapshot",
    "Nastavení beze změny.": "Settings unchanged.",
    # Hledání symbolu
    "Vše": "All",
    "Akcie": "Stocks",
    "Fondy": "Funds",
    "Futures": "Futures",
    "Forex": "Forex",
    "Krypto": "Crypto",
    "Indexy": "Indices",
    "Komodity": "Commodities",
    "Zadaný symbol": "Entered symbol",
    "Nic nenalezeno.": "Nothing found.",
    "Nedostupné zdroje: {sources}.": "Unavailable sources: {sources}.",
    "Zdroj {provider}": "Source {provider}",
    "V seznamu {name}": "In list {name}",
    "Přidat do seznamu {name}": "Add to list {name}",
    "{symbol} už v seznamu {name} je ({source}).": "{symbol} is already in list {name} ({source}).",
    "{symbol} přidán do seznamu {name} ({source}).": "{symbol} added to list {name} ({source}).",
    "Symbol, název nebo ISIN…": "Symbol, name or ISIN…",
    "Všechny zdroje": "All sources",
    # Kreslení
    "Kurzor (výběr a tažení kreseb)": "Cursor (select and drag drawings)",
    "Nitkový kříž": "Cross",
    "Tečka": "Dot",
    "Šipka": "Arrow",
    "Prezentace": "Demonstration",
    "Magie": "Magic",
    "Guma (klik na kresbu ji smaže)": "Eraser (click a drawing to delete it)",
    "Čáry, kanály a pitchforky": "Lines, channels and pitchforks",
    "Čáry": "Lines",
    "Trendová čára": "Trend line",
    "Polopřímka": "Ray",
    "Přímka (oboustranná)": "Extended line (both ways)",
    "Přímka": "Extended line",
    "Trendová šipka": "Arrow",
    "Horizontální linie": "Horizontal line",
    "Horizontální polopřímka": "Horizontal ray",
    "Svislá linie": "Vertical line",
    "Kříž": "Cross line",
    "Kanály": "Channels",
    "Paralelní kanál (3 kliky)": "Parallel channel (3 clicks)",
    "Paralelní kanál": "Parallel channel",
    "Pitchforky": "Pitchforks",
    "Pitchfork (3 kliky)": "Pitchfork (3 clicks)",
    "Tvary": "Shapes",
    "Obdélník": "Rectangle",
    "Štětec": "Brush",
    "Text a stickers": "Text and stickers",
    "Prognózy, objem a měření (Long / Short position, FRVP, měřič)": (
        "Forecasting, volume and measurement (Long / Short position, FRVP, measure)"
    ),
    "Prognózy": "Forecasting",
    "Objemové": "Volume-based",
    # Settings kresby FRVP (Fixed range volume profile)
    "Profil se počítá ze svíček grafu v rozsahu: objem každé svíčky se rozdělí rovnoměrně mezi cenové řádky od jejího low po high.": (
        "The profile is computed from the chart candles in the range: each candle's volume is spread evenly "
        "across the price rows from its low to its high."
    ),
    "Bary se číslují od první načtené svíčky grafu.": "Bars are numbered from the first loaded candle of the chart.",
    "Zadejte čísla barů rozsahu.": "Enter the bar numbers of the range.",
    "Bary rozsahu nelze převést na čas (graf nemá data).": "The range bars cannot be converted to time (the chart has no data).",
    "Měření": "Measurement",
    "Měřič (Δcena, %, bary)": "Measure (Δprice, %, bars)",
    "Oblíbené nástroje: {count}.": "Favorite tools: {count}.",
    "Žádné oblíbené nástroje.": "No favorite tools.",
    "Panel oblíbených nástrojů zobrazen.": "Favorite tools panel shown.",
    "Panel oblíbených nástrojů skryt.": "Favorite tools panel hidden.",
    "vypnut": "off",
    "slabý – bod kresby se přichytí k O/H/L/C svíčky, když je kurzor blízko": (
        "weak – a drawing point snaps to the candle's O/H/L/C when the cursor is close"
    ),
    "silný – body kreseb se vždy přichytí k nejbližší O/H/L/C svíčky": (
        "strong – drawing points always snap to the nearest candle O/H/L/C"
    ),
    "Magnet {state}.": "Magnet {state}.",
    "Režim kreslení zapnut.": "Drawing mode on.",
    "Režim kreslení vypnut.": "Drawing mode off.",
    "Kresby uzamčeny.": "Drawings locked.",
    "Kresby odemčeny.": "Drawings unlocked.",
    "Smazáno kreseb: {count}.": "Drawings deleted: {count}.",
    "Text nesmí být prázdný.": "Text must not be empty.",
    "Text přidán a uložen.": "Text added and saved.",
    "{drawing}: nastavení uloženo.": "{drawing}: settings saved.",
    "{drawing}: použito výchozí nastavení.": "{drawing}: default settings applied.",
    "{drawing}: použita šablona „{name}“.": "{drawing}: template “{name}” applied.",
    "Šablona „{name}“ neexistuje.": "Template “{name}” does not exist.",
    "Kresba smazána.": "Drawing deleted.",
    "Kresba uložena ({drawing}) k {symbol}": "Drawing saved ({drawing}) to {symbol}",
    "Magnet – přichytávání bodů kreseb k O/H/L/C (klik = výběr režimu)": (
        "Magnet – snap drawing points to O/H/L/C (click = choose mode)"
    ),
    "Režim kreslení (nástroj zůstává aktivní)": "Drawing mode (tool stays active)",
    "Zamknout kresby": "Lock drawings",
    "Skrýt/zobrazit kresby": "Hide/show drawings",
    "Smazat označenou / všechny kresby": "Delete selected / all drawings",
    "Zobrazit / skrýt panel oblíbených nástrojů (hvězdička ve flyoutu)": (
        "Show / hide the favorite tools panel (star in the flyout)"
    ),
    "Vyberte sticker": "Choose a sticker",
    "Sticker {emoji} přidán a uložen.": "Sticker {emoji} added and saved.",
    # Settings kresby
    "Barva textu": "Text color",
    "Tučné": "Bold",
    "Kurzíva": "Italic",
    "Barva pozadí": "Background color",
    "Barva rámečku": "Border color",
    "Barva": "Color",
    "Barva kresby": "Drawing color",
    "Šířka čáry": "Line width",
    "Styl čáry": "Line style",
    "Sticker nemá nastavitelný styl – na záložce Visibility zvolte, na kterých timeframech je vidět.": (
        "A sticker has no adjustable style – on the Visibility tab choose the timeframes it is shown on."
    ),
    "Velikost účtu": "Account size",
    "Riziko": "Risk",
    "Velikost lotu": "Lot size",
    "Úrovně": "Levels",
    "Vstup (cena)": "Entry (price)",
    "Cíl – Profit level (cena)": "Target – Profit level (price)",
    "Stop level (cena)": "Stop level (price)",
    "Kresba patří k symbolu a je vidět na timeframech, které odpovídají zaškrtnutým jednotkám a rozsahům.": (
        "The drawing belongs to the symbol and is shown on timeframes matching the checked units and ranges."
    ),
    # Settings grafu
    "Settings grafu uloženy.": "Chart settings saved.",
    "Svíčky": "Candles",
    "Obarvit svíčky podle předchozího závěru": "Color bars based on previous close",
    "Tělo": "Body",
    "Okraje": "Borders",
    "Knoty": "Wick",
    "{part} – růst": "{part} – up",
    "{part} – pokles": "{part} – down",
    # Watchlist a seznamy symbolů
    "Ceny watchlistu ({source}) nedostupné: {error}": "Watchlist prices ({source}) unavailable: {error}",
    "Nový seznam…": "New list…",
    "Přejmenovat…": "Rename…",
    "Vytvořit kopii…": "Make a copy…",
    "Vyprázdnit seznam": "Clear list",
    "Smazat seznam": "Delete list",
    "Nový seznam": "New list",
    "Přejmenovat seznam": "Rename list",
    "Kopie seznamu": "Copy of list",
    "{name} (kopie)": "{name} (copy)",
    "Zadejte název seznamu (1–{max_length} znaků).": "Enter a list name (1–{max_length} characters).",
    "Seznam přejmenován na {name}.": "List renamed to {name}.",
    "Seznam {name} vytvořen.": "List {name} created.",
    "Odebrat ze seznamu {name} všechny symboly a sekce?": "Remove all symbols and sections from list {name}?",
    "Smazat seznam {name}?": "Delete list {name}?",
    "Seznam {name} vyprázdněn.": "List {name} cleared.",
    "Seznam {name} smazán.": "List {name} deleted.",
    "Název seznamu": "List name",
    "Seznam symbolů": "Symbol list",
    "Přidat symbol grafu do seznamu": "Add the chart symbol to the list",
    "Řadit podle abecedy (A→Z, Z→A, vlastní pořadí)": "Sort alphabetically (A→Z, Z→A, custom order)",
    "Řadit podle vlaječek (barvy, obráceně, vlastní pořadí)": "Sort by flags (colors, reversed, custom order)",
    # Sekce watchlistu
    "Přidat sekci": "Add section",
    "Přidat sekci…": "Add section…",
    "Nová sekce": "New section",
    "Přejmenovat sekci": "Rename section",
    "Název sekce": "Section name",
    "Zadejte název sekce (1–{max_length} znaků).": "Enter a section name (1–{max_length} characters).",
    "Odebrat sekci": "Remove section",
    "Bez sekce": "No section",
    "Přidat symbol…": "Add symbol…",
    "Sekce {name} vytvořena – symboly do ní přetáhněte myší.": "Section {name} created – drag symbols into it with the mouse.",
    "Sekce přejmenována na {name}.": "Section renamed to {name}.",
    "Sekce {name} odebrána, její symboly zůstaly v seznamu.": "Section {name} removed, its symbols stayed in the list.",
    # Settings aplikace
    "IBKR: připojení k {host}:{port} selhalo – {error}": "IBKR: connection to {host}:{port} failed – {error}",
    "Telegram uložen": "Telegram saved",
    "IBKR uloženo a přepojeno": "IBKR saved and reconnected",
    "Alpaca uložena – změna se projeví po restartu aplikace": "Alpaca saved – the change takes effect after restarting the app",
    "Replay uložen": "Replay saved",
    "cTrader uložen – zapnutí zdroje se projeví po restartu aplikace": (
        "cTrader saved – enabling the source takes effect after restarting the app"
    ),
    "cTrader: připojení selhalo – {error}": "cTrader: connection failed – {error}",
    "Forex, kovy a CFD s tick volume (jako v TradingView) od brokera s platformou cTrader "
    "(Pepperstone, IC Markets, Purple Trading…); zároveň dodá objem pro IBKR forex, kovy a CFD. "
    "Stačí demo účet u brokera. Na openapi.ctrader.com založte aplikaci (Applications → Add new "
    "app) a počkejte na schválení (status se změní ze Submitted na Active). Pak v Credentials zkopírujte "
    "Client ID a Secret a v Sandbox získejte Access a Refresh token (scope accounts). Token se "
    "obnovuje sám. Zapnutí zdroje se projeví po restartu.": (
        "Forex, metals and CFDs with tick volume (as in TradingView) from a broker with the cTrader "
        "platform (Pepperstone, IC Markets, Purple Trading…); it also provides volume for IBKR forex, "
        "metals and CFDs. A demo account at the broker is enough. Create an application at "
        "openapi.ctrader.com (Applications → Add new app) and wait for approval (the status changes "
        "from Submitted to Active). Then copy the Client ID and Secret under Credentials and get an Access "
        "and Refresh token in the Sandbox (scope accounts). The token refreshes automatically. "
        "Enabling the source takes effect after a restart."
    ),
    "Demo účet": "Demo account",
    "Stažení Telegramu": "Download Telegram",
    "Založení bota (@BotFather)": "Create a bot (@BotFather)",
    "Stažení TWS": "Download TWS",
    "Stažení IB Gateway": "Download IB Gateway",
    "Předplatné dat (Settings → Market Data Subscriptions)": "Data subscriptions (Settings → Market Data Subscriptions)",
    "API klíče (Paper Trading dashboard → API Keys)": "API keys (Paper Trading dashboard → API Keys)",
    "Aplikace Open API (Add new app, Credentials, Sandbox)": "Open API applications (Add new app, Credentials, Sandbox)",
    "Živý účet": "Live account",
    "Server": "Server",
    "ID účtu (prázdné = první účet)": "Account ID (empty = first account)",
    "Nejdřív vložte token bota od @BotFather.": "First paste the bot token from @BotFather.",
    "Bot zatím nemá žádnou zprávu – napište mu (např. /start) a zkuste to znovu.": (
        "The bot has no message yet – write to it (e.g. /start) and try again."
    ),
    "Chat ID {chat_id} doplněno – nezapomeňte uložit.": "Chat ID {chat_id} filled in – don't forget to save.",
    "Vyplňte token bota i chat ID.": "Fill in both the bot token and the chat ID.",
    "✅ TickView: testovací zpráva alertů.": "✅ TickView: alert test message.",
    "Odeslání selhalo: {error}": "Sending failed: {error}",
    "Testovací zpráva odeslána.": "Test message sent.",
    "Ukládá se lokálně do config.yaml v kořenu projektu (necommituje se, komentáře zůstanou).": (
        "Saved locally to config.yaml in the project root (not committed, comments are kept)."
    ),
    "Notifikace alertů, scanneru a připomínek. Bota založíte u @BotFather (příkaz /newbot), "
    "token vložte sem. Pak botovi napište (např. /start) a tlačítkem Zjistit chat ID se chat "
    "doplní sám. Platí hned.": (
        "Notifications of alerts, the scanner and reminders. Create a bot with @BotFather (command /newbot) "
        "and paste its token here. Then write to the bot (e.g. /start) and the Detect chat ID button fills "
        "in the chat automatically. Takes effect immediately."
    ),
    "Zjistit chat ID": "Detect chat ID",
    "Odeslat test": "Send test",
    "TWS nebo IB Gateway musí běžet a mít povolené API (Configure → API → Settings). "
    "Porty: TWS paper 7497, TWS live 7496, IB Gateway paper 4002, live 4001. "
    "Při obsazeném client ID se zkusí další. Po uložení se zdroj hned přepojí.": (
        "TWS or IB Gateway must be running with the API enabled (Configure → API → Settings). "
        "Ports: TWS paper 7497, TWS live 7496, IB Gateway paper 4002, live 4001. "
        "If the client ID is taken, the next one is tried. After saving, the source reconnects immediately."
    ),
    "Otestovat připojení": "Test connection",
    "Jak na předplatné dat": "How to subscribe to data",
    "Volitelný zdroj živých amerických akcií. Klíče z Paper Trading dashboardu; bez klíčů "
    "se zdroj nezapne. Změna se projeví po restartu aplikace.": (
        "Optional source of live US stocks. Keys from the Paper Trading dashboard; without keys "
        "the source stays off. The change takes effect after restarting the app."
    ),
    "Krok zpět v replayi (nůžky v liště replaye nebo skok na dřívější čas). Příkazy a obchody "
    "zadané po bodu návratu se smažou a stav účtu se přepočítá. Platí hned.": (
        "Step back in replay (scissors in the replay bar or a jump to an earlier time). Orders and trades "
        "placed after the return point are deleted and the account balance is recalculated. Takes effect immediately."
    ),
    "Krok zpět": "Step back",
    # Indikátory
    "Přidán indikátor {indicator} – nastavení přes ozubené kolečko v legendě grafu.": (
        "Indicator {indicator} added – settings via the gear icon in the chart legend."
    ),
    "Indikátor {indicator} odebrán.": "Indicator {indicator} removed.",
    "Indikátor {indicator} upraven.": "Indicator {indicator} updated.",
    "Indikátor {kind} se nenačetl – zkontrolujte jeho soubor.": "Indicator {kind} failed to load – check its file.",
    "Všechny indikátory": "All indicators",
    "Oblíbené ({count})": "Favorites ({count})",
    "Aktivní ({count})": "Active ({count})",
    "Žádný aktivní indikátor – vyberte ze seznamu.": "No active indicator – choose one from the list.",
    "Indikátor se nenačetl": "Indicator failed to load",
    "Skrýt / zobrazit": "Hide / show",
    "Odebrat": "Remove",
    "Žádný oblíbený indikátor – označte ho hvězdičkou vpravo.": "No favorite indicator – mark one with the star on the right.",
    "Počet aktivních instancí": "Number of active instances",
    "Hledat (např. RSI, průměr, objem)…": "Search (e.g. RSI, average, volume)…",
    "Indikátor se počítá a kreslí (včetně tabulky scanneru) jen na timeframech, které odpovídají "
    "zaškrtnutým jednotkám a rozsahům; v legendě zůstává.": (
        "The indicator is calculated and drawn (including the scanner table) only on timeframes matching "
        "the checked units and ranges; it stays in the legend."
    ),
    # Šablony indikátorů
    "Šablona indikátorů „{name}“ smazána.": "Indicator template “{name}” deleted.",
    "Šablona indikátorů „{name}“: {description}.": "Indicator template “{name}”: {description}.",
    "Zadejte název šablony (1–{max_length} znaků).": "Enter a template name (1–{max_length} characters).",
    "Šablona indikátorů „{name}“ přepsána ({count}).": "Indicator template “{name}” overwritten ({count}).",
    "Šablona indikátorů „{name}“ uložena ({count}).": "Indicator template “{name}” saved ({count}).",
    "indikátor": "indicator",
    "indikátory": "indicators",
    "indikátorů": "indicators",
    "Žádné šablony – uložte je přes „Save indicator template…“.": "No templates – save them via “Save indicator template…”.",
    "Smazat šablonu": "Delete template",
    "Název šablony": "Template name",
    "Hledat šablonu…": "Search template…",
    # Alerty
    "Alerty v replayi nejsou k dispozici – graf ukazuje minulost.": "Alerts are not available in replay – the chart shows the past.",
    "Nejdřív zvolte symbol.": "Choose a symbol first.",
    "Cena (hodnota)": "Price (value)",
    "{symbol} (cena)": "{symbol} (price)",
    "{target} (nastavení alertu)": "{target} (alert settings)",
    "Upravit alert on {symbol}, {timeframe}": "Edit alert on {symbol}, {timeframe}",
    "Zadejte platné datum a čas expirace.": "Enter a valid expiration date and time.",
    "Expirace musí být v budoucnosti.": "Expiration must be in the future.",
    "Zadejte Webhook URL, nebo webhook vypněte.": "Enter a Webhook URL or turn the webhook off.",
    "Alert upraven: {message}": "Alert updated: {message}",
    "Alert vytvořen: {message}": "Alert created: {message}",
    "Test alertu: Telegram ani webhook nejsou zapnuté.": "Alert test: neither Telegram nor webhook is enabled.",
    "Test alertu: {results}": "Alert test: {results}",
    "Alert {symbol} smazán.": "Alert {symbol} deleted.",
    "Upravit…": "Edit…",
    "Klonovat…": "Clone…",
    "Pozastavit": "Pause",
    "Spustit znovu": "Restart",
    "Zobrazit v grafu": "Show on chart",
    "Test alertu": "Test alert",
    "Smazat": "Delete",
    "Upravit": "Edit",
    "Žádné alerty – vytvořte je tlačítkem + nebo pravým klikem do grafu.": (
        "No alerts – create them with the + button or by right-clicking the chart."
    ),
    "Hledání neodpovídá žádný alert.": "No alert matches the search.",
    "vyprší {time}": "expires {time}",
    "spuštěno {count}×": "triggered {count}×",
    "Zatím se žádný alert nespustil.": "No alert has triggered yet.",
    "Smazat všechny neaktivní alerty": "Delete all inactive alerts",
    "Smazat všechny alerty": "Delete all alerts",
    "{action}? Akci nelze vrátit.": "{action}? This action cannot be undone.",
    "Hromadná akce: {count}.": "Bulk action: {count}.",
    "alert": "alert",
    "alerty": "alerts",
    "alertů": "alerts",
    "Alerty": "Alerts",
    "Hledat v alertech": "Search alerts",
    "Řazení alertů": "Sort alerts",
    "Další akce": "More actions",
    "Spustit všechny neaktivní": "Restart all inactive",
    "Pozastavit všechny": "Pause all",
    "Smazat všechny neaktivní": "Delete all inactive",
    "Smazat všechny": "Delete all",
    "Časové připomínky…": "Time reminders…",
    "Vymazat log": "Clear log",
    "Hledat": "Search",
    "Cena": "Price",
    "Minut před zavřením svíčky": "Minutes before candle close",
    "Bez expirace (open-ended)": "Open-ended",
    "Zástupné texty: {{ticker}} {{exchange}} {{interval}} {{close}} {{time}}, u indikátoru {{signal}}, "
    "u alertu scanneru {{message}}": (
        "Placeholders: {{ticker}} {{exchange}} {{interval}} {{close}} {{time}}, for an indicator {{signal}}, "
        "for a scanner alert {{message}}"
    ),
    "Telegram není nastavený – ozubené kolečko v horní liště → Telegram.": (
        "Telegram is not set up – gear icon in the top bar → Telegram."
    ),
    "Toast v aplikaci": "In-app toast",
    "Přehrát zvuk": "Play sound",
}
