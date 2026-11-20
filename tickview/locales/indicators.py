"""Anglické překlady: indikátory (tickview/indicators – rozhraní, vestavěné, COT, vlastní
indikátory, registr, výpočet) a formulář parametrů (tickview/ui/param_form.py)."""

EN = {
    # Kategorie nabídky indikátorů (api.CATEGORIES)
    "Klouzavé průměry": "Moving Averages",
    "Pásma a kanály": "Bands and Channels",
    "Trend": "Trend",
    "Oscilátory": "Oscillators",
    "Volatilita": "Volatility",
    "Vlastní": "Custom",
    # Chyby parametrů (api.Param.normalize)
    "{label}: zadejte číslo.": "{label}: enter a number.",
    "{label}: zadejte celé číslo {low}–{high}.": "{label}: enter a whole number {low}–{high}.",
    "{label}: zadejte kladné číslo nejvýš {high}.": "{label}: enter a positive number up to {high}.",
    "{label}: zadejte číslo {low}–{high}.": "{label}: enter a number {low}–{high}.",
    "{label}: neplatná volba.": "{label}: invalid option.",
    "{label}: zadejte symbol ve tvaru zdroj:SYMBOL.": "{label}: enter a symbol in the form source:SYMBOL.",
    # Vstupy scanneru (api.scanner_params)
    "Scanner – alert před zavřením svíčky": "Scanner – alert before candle close",
    "Scanner – symboly": "Scanner – symbols",
    "nezobrazovat": "off",
    "Timeframe scanu": "Scan timeframe",
    "Počet posledních svíček pro signál": "Number of recent candles for signal",
    "0 = běžící svíčka; signál starší než zadaný počet svíček se v tabulce neukáže.": (
        "0 = current candle; a signal older than the given number of candles is not shown in the table."
    ),
    "Pozice tabulky": "Table position",
    "Při „nezobrazovat“ se symboly počítají jen kvůli alertu.": (
        "With “off”, the symbols are computed only for the alert."
    ),
    "Zobrazit pouze trhy se signálem": "Show only markets with a signal",
    "Velikost textu": "Text size",
    "Skrýt prefix zdroje": "Hide source prefix",
    "Posílat alert před zavřením svíčky": "Send alert before candle close",
    "Jedna souhrnná zpráva se seznamem trhů se signálem na běžící svíčce timeframu scanu. "
    "Odejde přes alert na tento indikátor s podmínkou „Any alert() function call“ "
    "(Alert → Condition), kde se nastavují i notifikace (Telegram, toast, zvuk, webhook).": (
        "One summary message listing the markets with a signal on the current candle of the scan timeframe. "
        "It is sent through an alert on this indicator with the condition “Any alert() function call” "
        "(Alert → Condition), where notifications are set up as well (Telegram, toast, sound, webhook)."
    ),
    "1. upozornění (min před zavřením)": "1st notification (min before close)",
    "2. upozornění (min před zavřením)": "2nd notification (min before close)",
    "0 = vypnuto; každý práh nejvýš jednou za svíčku.": "0 = off; each threshold at most once per candle.",
    # Registr indikátorů (chyby načtení vlastních indikátorů)
    "{origin}: INDICATOR musí být instance Indicator.": "{origin}: INDICATOR must be an instance of Indicator.",
    "{origin}: indikátor je pro rozhraní verze {version}, TickView má {api_version}.": (
        "{origin}: the indicator is for API version {version}, TickView has {api_version}."
    ),
    "{origin}: indikátor nemá id.": "{origin}: the indicator has no id.",
    "{origin}: id {id} už používá jiný indikátor.": "{origin}: id {id} is already used by another indicator.",
    "{origin}: neznámá kategorie {category}.": "{origin}: unknown category {category}.",
    "{origin}: chyba importu – {error}": "{origin}: import error – {error}",
    # Instance a výpočet (compute)
    "{kind} (nenačten)": "{kind} (not loaded)",
    "Neznámý indikátor: {kind}": "Unknown indicator: {kind}",
    "{title} (chyba výpočtu)": "{title} (calculation error)",
    # Formulář parametrů (ui/param_form.py)
    "Indikátor se nenačetl – nastavení nelze upravit.": "The indicator failed to load – settings cannot be edited.",
    "Indikátor nemá nastavitelné parametry.": "The indicator has no adjustable inputs.",
    "zdroj:SYMBOL": "source:SYMBOL",
    "Hledat symbol": "Search symbol",
    # Vestavěné indikátory – společné parametry a čáry
    "Délka (perioda)": "Length (period)",
    "Rychlá perioda": "Fast length",
    "Pomalá perioda": "Slow length",
    "Signální perioda": "Signal length",
    "Rychlá perioda musí být kratší než pomalá.": "Fast length must be shorter than slow length.",
    "Horní": "Upper",
    "Střed": "Basis",
    "Dolní": "Lower",
    "Růst": "Up trend",
    "Pokles": "Down trend",
    "Signál": "Signal",
    # Vestavěné indikátory – názvy, popisy a parametry
    "Jednoduchý klouzavý průměr": "Simple Moving Average",
    "Aritmetický průměr závěrečných cen za periodu.": "Arithmetic mean of closing prices over the period.",
    "Exponenciální klouzavý průměr": "Exponential Moving Average",
    "Průměr s větší váhou novějších cen.": "Average giving more weight to recent prices.",
    "Vážený klouzavý průměr": "Weighted Moving Average",
    "Lineárně vážený průměr závěrečných cen.": "Linearly weighted average of closing prices.",
    "Hullův klouzavý průměr": "Hull Moving Average",
    "Rychlý a vyhlazený průměr s malým zpožděním.": "Fast, smooth average with little lag.",
    "Objemem vážený klouzavý průměr": "Volume Weighted Moving Average",
    "Průměr cen vážený objemem obchodů.": "Average of prices weighted by trading volume.",
    "Objemem vážená průměrná cena": "Volume Weighted Average Price",
    "Průměrná cena vážená objemem od začátku dne (UTC).": (
        "Average price weighted by volume since the start of the day (UTC)."
    ),
    "Klouzavý průměr ± násobek směrodatné odchylky.": "Moving average ± a multiple of the standard deviation.",
    "Počet směrodatných odchylek": "StdDev multiplier",
    "Keltnerovy kanály": "Keltner Channels",
    "EMA ± násobek ATR.": "EMA ± a multiple of ATR.",
    "Násobek": "Multiplier",
    "Donchianovy kanály": "Donchian Channels",
    "Nejvyšší maximum a nejnižší minimum za periodu.": "Highest high and lowest low over the period.",
    "Tenkan, Kijun, Senkou A/B a Chikou span.": "Tenkan, Kijun, Senkou A/B and Chikou span.",
    "Tenkan (konverzní linie)": "Tenkan (conversion line)",
    "Kijun (základní linie)": "Kijun (base line)",
    "Senkou B (perioda)": "Senkou B (period)",
    "Body stop-and-reverse pod / nad cenou.": "Stop-and-reverse dots below / above the price.",
    "Krok zrychlení (AF)": "Acceleration step (AF)",
    "Maximální AF": "Maximum AF",
    "Trendová linie z ATR; zelená = růst, červená = pokles.": "ATR-based trend line; green = up, red = down.",
    "Síla trendu (ADX) a směrové indikátory +DI / −DI.": "Trend strength (ADX) and directional indicators +DI / −DI.",
    "Oscilátor 0–100, překoupeno nad 70, přeprodáno pod 30.": (
        "Oscillator 0–100, overbought above 70, oversold below 30."
    ),
    "Rozdíl dvou EMA, signální linie a histogram.": "Difference of two EMAs, signal line and histogram.",
    "Stochastický oscilátor": "Stochastic Oscillator",
    "Poloha závěru v rozpětí periody (%K, %D).": "Position of the close within the period range (%K, %D).",
    "%K perioda": "%K length",
    "%D vyhlazení": "%D smoothing",
    "Vyhlazení %K": "%K smoothing",
    "Stochastické RSI": "Stochastic RSI",
    "Stochastický oscilátor aplikovaný na RSI.": "Stochastic oscillator applied to RSI.",
    "Délka RSI": "RSI length",
    "Odchylka ceny od průměru; hranice ±100.": "Deviation of price from its average; bounds ±100.",
    "Oscilátor −100–0, překoupeno nad −20.": "Oscillator −100–0, overbought above −20.",
    "Rozdíl závěru a závěru před N svíčkami.": "Difference between the close and the close N candles ago.",
    "Procentní změna ceny za periodu.": "Percentage price change over the period.",
    "Rozdíl SMA mediánové ceny (5 a 34).": "Difference of SMAs of the median price (5 and 34).",
    "Změna trojitě vyhlazené EMA a signální linie.": "Change of a triple-smoothed EMA and signal line.",
    "Dvakrát vyhlazené momentum a signální linie.": "Double-smoothed momentum and signal line.",
    "Průměrné skutečné rozpětí svíček.": "Average true range of the candles.",
    "Kumulativní objem podle směru svíček.": "Cumulative volume by candle direction.",
    "RSI vážené objemem; hranice 20 / 80.": "Volume-weighted RSI; bounds 20 / 80.",
    "Tok peněz za periodu (−1 až 1).": "Money flow over the period (−1 to 1).",
    # COT indikátory
    "Data COT": "COT data",
    "Tabulka": "Table",
    "Průvodce interpretací": "Interpretation guide",
    "Commercials jsou typicky hedgeři (výrobci, zpracovatelé, banky).": (
        "Commercials are typically hedgers (producers, processors, banks)."
    ),
    "Kladná net pozice: víc longů než shortů; záporná: víc shortů.": (
        "Positive net position: more longs than shorts; negative: more shorts."
    ),
    "Obchodují proti trendu – kontrariánský indikátor.": "They trade against the trend – a contrarian indicator.",
    "Extrémy pozice často předcházejí obratu trhu.": "Position extremes often precede a market reversal.",
    "Non-Commercials jsou velcí spekulanti a fondy.": "Non-Commercials are large speculators and funds.",
    "Obvykle následují trend – indikátor momenta.": "They usually follow the trend – a momentum indicator.",
    "Extrémní pozice ukazují přeplněný obchod a riziko obratu.": (
        "Extreme positions indicate a crowded trade and reversal risk."
    ),
    "Non-reportables jsou drobní obchodníci pod hranicí hlášení.": (
        "Non-reportables are small traders below the reporting threshold."
    ),
    "Často obchodují proti institucím – kontrariánský indikátor.": (
        "They often trade against institutions – a contrarian indicator."
    ),
    "Extrémy pozice bývají u vrcholů a propadů trhu.": "Position extremes tend to occur at market tops and bottoms.",
    "Commercials > {upper}: potenciální býčí obrat; < {lower}: potenciální medvědí tlak.": (
        "Commercials > {upper}: potential bullish reversal; < {lower}: potential bearish pressure."
    ),
    "Non-Commercials > {upper}: přeplněné longy fondů; < {lower}: přeplněné shorty.": (
        "Non-Commercials > {upper}: crowded fund longs; < {lower}: crowded shorts."
    ),
    "Non-reportables > {upper}: drobní překoupení (medvědí); < {lower}: přeprodaní (býčí).": (
        "Non-reportables > {upper}: small traders overbought (bearish); < {lower}: oversold (bullish)."
    ),
    "Pozice jsou obrácené: pár USD/xxx roste, když xxx slábne.": (
        "Positions are inverted: the USD/xxx pair rises when xxx weakens."
    ),
    "Kód trhu CFTC": "CFTC market code",
    "Prázdné = podle symbolu grafu (futures, forex, kovy, indexová CFD). "
    "Jinak šestimístný kód trhu z COT reportu, např. 209742 (Nasdaq mini).": (
        "Empty = based on the chart symbol (futures, forex, metals, index CFDs). "
        "Otherwise the six-character market code from the COT report, e.g. 209742 (Nasdaq mini)."
    ),
    "Obrátit směr": "Invert direction",
    "Automaticky (pár USD/xxx)": "Automatic (USD/xxx pair)",
    "Ne": "No",
    "Ano": "Yes",
    "Pár USD/JPY se pohybuje opačně než futures na jen (6J) – pozice se obrátí.": (
        "The USD/JPY pair moves opposite to yen futures (6J) – positions are inverted."
    ),
    "Platnost reportu od": "Report valid from",
    "Zveřejnění (pátek 15:30 NY)": "Release (Friday 15:30 NY)",
    "Data reportu (úterý)": "Report date (Tuesday)",
    "Report popisuje stav k úterku, ale vychází až v pátek. Zveřejnění = bez pohledu "
    "do budoucnosti (replay, signály); datum reportu = jako v TradingView.": (
        "The report describes positions as of Tuesday but is released on Friday. Release = no look-ahead "
        "(replay, signals); report date = as in TradingView."
    ),
    "Vlevo tabulka nezakrývá nejnovější data ani popisky čar u cenové osy.": (
        "On the left, the table does not cover the latest data or the line labels at the price scale."
    ),
    "Vždy": "Always",
    "Po najetí myší na tabulku": "On table hover",
    "Nezobrazovat": "Off",
    "{report} (vyšel {released})": "{report} (released {released})",
    "Trh": "Market",
    "symbol nemá COT": "symbol has no COT data",
    "Tip": "Tip",
    "zadejte kód trhu CFTC": "enter a CFTC market code",
    "načítám COT report…": "loading COT report…",
    "trh nemá data": "market has no data",
    "COT – pozice skupiny": "COT – group positions",
    "Čistá pozice skupiny obchodníků z COT reportu CFTC (Commercials, Non-Commercials, "
    "Non-reportables) a její týdenní změna.": (
        "Net position of a trader group from the CFTC COT report (Commercials, Non-Commercials, "
        "Non-reportables) and its weekly change."
    ),
    "Skupina": "Group",
    "Týdenní změna": "Weekly change",
    "graf končí před prvním reportem": "chart ends before the first report",
    "Čistá pozice": "Net position",
    "Poloha čisté pozice skupiny v rozsahu posledních N týdnů (0–100) podle Larryho Williamse; "
    "extrémy nad a pod úrovněmi zvýrazní pozadí a dají signál.": (
        "Position of the group's net position within the range of the last N weeks (0–100) after Larry Williams; "
        "extremes above and below the levels highlight the background and give a signal."
    ),
    "Lookback (týdny)": "Lookback (weeks)",
    "Skupina 1 (signály)": "Group 1 (signals)",
    "Skupina 2": "Group 2",
    "Horní extrém": "Upper extreme",
    "Dolní extrém": "Lower extreme",
    "Zvýraznit extrémy pozadím": "Highlight extremes with background",
    "Dolní extrém musí být menší než horní.": "Lower extreme must be less than upper extreme.",
    "Skupina 2 musí být jiná než skupina 1.": "Group 2 must differ from group 1.",
    "Index = poloha čisté pozice v rozsahu posledních {weeks} týdnů (0–100).": (
        "Index = position of the net position within the range of the last {weeks} weeks (0–100)."
    ),
    "{weeks} týdnů": "{weeks} weeks",
    # Vlastní indikátor Svopex TTW
    "Praha (Europe/Prague)": "Prague (Europe/Prague)",
    "Pásmo burzy symbolu": "Symbol exchange time zone",
    "Londýn (Europe/London)": "London (Europe/London)",
    "Detekce signálu": "Signal detection",
    "Rozsah zpracování": "Processing range",
    "Swingy": "Swings",
    "Tolerance": "Tolerance",
    "Půlnoční svíčky": "Midnight candles",
    "Zobrazení": "Display",
    "Trendové linky, šablony a double bottom/top u formací ONE / ENG / STAR (převod z Pine).": (
        "Trend lines, templates and double bottom/top at ONE / ENG / STAR formations (port from Pine)."
    ),
    "Minimální délka svíčky (poměr k průměrné)": "Minimum candle length (ratio to average)",
    "Maximální délka svíčky (poměr k průměrné)": "Maximum candle length (ratio to average)",
    "ENG/STAR: uzavření nad high předchozí svíčky": "ENG/STAR: close above the previous candle's high",
    "STAR: poměr velikosti 1. a 3. svíčky ku 2.": "STAR: size ratio of the 1st and 3rd candle to the 2nd",
    "Brát signály pouze zdola (long) / shora (short)": "Take signals only from the bottom (long) / top (short)",
    "Koeficient pro „brát zdola“": "Coefficient for “from the bottom”",
    "Počet svíček pro test „brát zdola“": "Number of candles for the “from the bottom” test",
    "Zobrazovat i shorty": "Show shorts too",
    "Počet svíček, které se zpracovávají": "Number of candles to process",
    "Zároveň perioda průměrné velikosti svíčky – graf musí mít aspoň tolik svíček.": (
        "Also the period of the average candle size – the chart must have at least this many candles."
    ),
    "Maximální délka signálu (svíčky)": "Maximum signal length (candles)",
    "Minimální délka formace (svíčky)": "Minimum formation length (candles)",
    "Minimální mezera mezi doteky": "Minimum gap between touches",
    "Maximální počet úseček pro jeden signál": "Maximum number of segments per signal",
    "Počet svíček pro minor swing": "Number of candles for minor swing",
    "Násobek průměrné výšky svíčky pro swing": "Average candle height multiplier for swing",
    "Použít major swing indikátor pro startovní svíčku": "Use major swing indicator for the starting candle",
    "Šířka major swingu": "Major swing width",
    "Šířka minor swingu": "Minor swing width",
    "Maximální míra nedosednutí na trendlinku": "Maximum trend line miss ratio",
    "Povolené protnutí úsečky svíčkou": "Allowed segment crossing by a candle",
    "Maximální odchylka u double bottom": "Maximum double bottom deviation",
    "Minimální délka formace double bottom": "Minimum double bottom formation length",
    "První dva doteky na knotech": "First two touches on wicks",
    "Poslední dotek na knotech": "Last touch on wicks",
    "Vynechat půlnoční svíčky": "Skip midnight candles",
    "Na timeframu od H2 výš se půlnoční svíčky neřeší.": "Midnight candles are ignored on timeframes from H2 up.",
    "Hodina půlnoční svíčky": "Midnight candle hour",
    "Časové pásmo hodiny": "Hour time zone",
    "V Pine se hodina počítá v pásmu burzy symbolu. Zvolte pásmo, ve kterém má "
    "půlnoční (rolloverová) svíčka nastavenou hodinu – jinak se vynechají jiné svíčky "
    "než v TradingView.": (
        "In Pine, the hour is computed in the symbol's exchange time zone. Choose the time zone in which the "
        "midnight (rollover) candle has the set hour – otherwise different candles are skipped than in TradingView."
    ),
    "Zobraz trendlinku": "Show trend line",
    "Zobraz šablonu": "Show template",
    "Zobraz double top / double bottom": "Show double top / double bottom",
    "Dvoubarevná linka signálu": "Two-color signal line",
    "Tloušťka čar": "Line width",
    "Barva trendline long": "Trend line long color",
    "Barva trendline short": "Trend line short color",
    "Barva double bottom": "Double bottom color",
    "Barva double top": "Double top color",
    "Barva prostřední části dvoubarevné linky": "Two-color line middle part color",
}
