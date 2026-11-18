# TickView

## PrvotniZadani

Naprogramuj desktopovou/webovou aplikaci pro zobrazení burzovních a krypto grafů
ve stylu TradingView. Musí běžet na Windows, macOS i Linuxu.

STACK
- Python 3.11+, asyncio, httpx + websockets
- NiceGUI (UI, websocket), spuštění v prohlížeči i přes ui.run(native=True)
- TradingView Lightweight Charts (JS) vložené jako vlastní komponenta NiceGUI
- pandas + pandas-ta pro výpočet indikátorů
- SQLite pro cache dat a nastavení

ZDROJE DAT (pouze bezplatné)
- Společné rozhraní DataProvider: search(query), history(symbol, timeframe,
  od, do), stream(symbol, timeframe). Každý zdroj je samostatný modul, který
  lze přidat nebo vyměnit bez zásahu do zbytku aplikace.
- Binance (krypto): veřejné REST pro historii, websocket pro živé svíčky,
  bez API klíče. Implementuj jako první.
- Yahoo Finance přes yfinance (akcie, ETF, indexy, forex): historie + živá
  aktualizace dotazováním v intervalu; volat mimo hlavní smyčku
  (asyncio.to_thread).
- Alpaca (živé americké akcie): volitelný, zapne se až po zadání API klíče
  v nastavení.
- U každého symbolu zobraz zdroj a štítek „živě“ / „zpožděno“.

FUNKCE (v1)
1. Vyhledání symbolu napříč zdroji a výběr.
2. Svíčkový graf + objem, timeframy 1m, 5m, 15m, 1h, 4h, 1D, 1W
   (nabízej jen ty, které daný zdroj podporuje).
3. Historie + živé doplňování, donačítání starších dat při posunu doleva.
4. Indikátory: SMA, EMA, Bollinger Bands, RSI, MACD (RSI a MACD ve vlastním
   panelu), nastavitelné parametry.
5. Kreslení: horizontální linie, trendová čára, obdélník; uložení k symbolu.
6. Watchlist s poslední cenou a denní změnou; klik přepne graf.
7. Tmavý a světlý motiv, crosshair s OHLC legendou.
8. Ukládání nastavení, watchlistu a kreseb lokálně.

TECHNICKÉ POŽADAVKY
- Vše asynchronně na jedné asyncio smyčce, žádné blokující volání v UI.
- Cache historických dat na disku; ze sítě stahovat jen chybějící úsek.
- Omezovač dotazů pro každý zdroj, při chybě 429 exponenciální čekání.
- Automatické znovupřipojení websocketu.
- Nové svíčky posílat do grafu inkrementálně (series.update), ne celý dataset.
- Časy ukládat v UTC, zobrazovat v místním čase; ošetřit zavřený trh.
- Oddělit vrstvy: data (zdroje), logika (indikátory, cache), UI.
- Ošetřit chyby: výpadek zdroje, neznámý symbol, chybějící data.
- API klíče jen v lokálním konfiguračním souboru, nikdy v kódu.
- Aplikace je pouze pro čtení, žádné obchodování.

POSTUP PRÁCE
Kroky v tomto pořadí; po každém musí být aplikace spustitelná:
1. Kostra projektu + Binance + statický graf
2. Živá data (websocket)
3. yfinance
4. Indikátory
5. Kreslení
6. Watchlist
7. Alpaca

VÝSTUP
- Kompletní zdrojový kód se strukturou složek, requirements.txt a README
  s postupem spuštění na všech třech systémech.

## Instalace a spuštění

Vyžadován Python 3.11+.

Nejrychlejší spuštění na macOS/Linux (skript vytvoří `.venv`, doinstaluje
závislosti a spustí aplikaci):

    ./run.sh

Parametry se předávají do `main.py`, např. `./run.sh --port 8090 --open`.

Ruční spuštění na všech systémech (Windows, macOS, Linux):

    python3 -m venv .venv
    # Windows: .venv\Scripts\activate
    # macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt
    python main.py

- Aplikace běží v prohlížeči na http://localhost:8080 (adresa se vypíše do konzole).
- Jiný port: `python main.py --port 8090`.
- Automatické otevření prohlížeče: `python main.py --open`.
- Nativní okno (ui.run(native=True)): `pip install pywebview` a `python main.py --native`.
- Testy (bez dalších závislostí): `python -m unittest`.
- Jiná datová složka (cache, nastavení, kresby): proměnná prostředí
  `TICKVIEW_DATA_DIR`, např. `TICKVIEW_DATA_DIR=/tmp/tv python main.py`.

### Data a nastavení

- Cache svíček a nastavení: `data/tickview.db` (SQLite, vytvoří se samo;
  složka `data/` není verzována).
- Konfigurace: `config.yaml` v kořenu projektu (necommituje se – může
  obsahovat API klíče). Šablona je `config.example.yaml` – tu udržujeme
  aktuální a uživatel si ji kopíruje na `config.yaml`. Uložení klíčů z
  aplikace mění jen příslušné řádky, komentáře v souboru zůstanou; nově
  vytvořený soubor má práva jen pro vlastníka.

### Stav funkcí

- Hotovo: hledání symbolu jako v TradingView – v horní liště je pilulka
  s aktuálním symbolem, klik otevře dialog s označeným symbolem (psaní ho
  přepíše); hledání otevře i psaní písmen / číslic přímo nad grafem. Dialog
  hledá napříč zdroji souběžně a výsledky ukazuje průběžně (pomalá TWS
  nezdrží ostatní), řazení přesná shoda > prefix > výskyt, max 60 výsledků;
  kategorie Vše / Akcie / Fondy / Futures / Forex / Krypto / Indexy /
  Komodity, výběr zdroje, u výsledku logo, symbol, název, druh, burza
  a zkratka zdroje (Yahoo nabízí i futures `GC=F`, `CL=F`, fondy a krypto);
  zadání ve tvaru „zdroj:SYMBOL“ (např. `yahoo:GC=F`, `ibkr:XAUUSD`, jako
  v polích symbolů ve vstupech indikátorů) hledá jen v daném zdroji a symbol
  nabídne, i když ho hledání zdroje nevrátí;
  klik nebo Enter načte graf (cenová osa se po přepnutí symbolu nebo
  timeframu vrátí do automatického měřítka, i když byla ručně roztažená),
  šipky posouvají výběr, Esc zavře, tlačítko +
  přidá symbol do aktivního seznamu (fajfka = už v seznamu); do watchlistu
  lze přidat i aktuální symbol tlačítkem v hlavičce watchlistu. Svíčkové grafy
  + objem, timeframy 1m/2m/5m/10m/15m/1h/4h/1D/1W (jen co zdroj podporuje;
  2m a 10m, které Binance ani Yahoo nemají, se skládají z 1m / 5m svíček
  včetně živé svíčky), tmavý a světlý motiv, crosshair s OHLC legendou,
  cache historie v SQLite,
  donačítání starší historie při posunu grafu doleva (prázdné okno – noc,
  víkend – se postupně zvětšuje, pak se historie bere jako vyčerpaná),
  ukládání posledního symbolu/timeframu/motivu, živé svíčky Binance přes
  websocket (inkrementální update grafu, automatické znovupřipojení, štítek
  „živě / zpožděno“; výpadek a obnovení spojení se hlásí ve stavové liště a
  chybějící svíčky po výpadku se doplní z historie), akcie/ETF/indexy/forex
  přes Yahoo Finance s živým pollingem a hlášením zavřeného trhu, indikátory
  (28 typů – viz níže; oscilátory ve vlastních panelech se synchronizovaným
  časem, nastavitelné a kontrolované parametry, průběžný přepočet z živých
  svíček nejvýš jednou za 2 s, aktivní indikátory každého grafu se ukládají a přetrvávají
  restart), kreslení s lištou nástrojů 1:1 ve
  stylu TradingView: skupiny nástrojů s flyout nabídkami — trendová čára,
  polopřímka, přímka, trendová šipka, horizontální/svislá linie, horizontální
  polopřímka, kříž, Fibonacci retracement i extension (s popisky), paralelní
  kanál, pitchfork, obdélník, štětec, text, sticker (emoji), Long a Short
  position (viz níže), měřič; výběr
  kresby klikem s úchopy a tažením (celá kresba i jednotlivé body; výběr
  zůstává i po uložení); úchopy jsou jako v TradingView kolečka (u pozice
  Target a Stop čtverečky) a ukážou se už při najetí myší na kresbu; kresby lze umístit i mimo existující svíčky
  (vpravo do budoucna, přes víkendové mezery), mazání
  přes pravoklikové menu („Smazat objekt“), Del nebo koš, magnet na OHLC,
  tužka (nástroj zůstává aktivní), zámek a skrytí kreseb; text i sticker se
  zadávají v dialogu po kliknutí do grafu; barvu, šířku a styl nové kresby
  určuje výchozí šablona typu kresby (viz Šablony); kresby
  se ukládají k symbolu (přetrvávají restart) a jako v TradingView jsou vidět
  na všech timeframech podle záložky Visibility (viz níže). Lišta i nabídky
  používají vlastní tenké SVG piktogramy ve stylu TradingView (Long / Short
  position: tři čáry s kotevními body a písmenem L / S v zóně cíle).
- Pravý panel jako v TradingView: svislá lišta úplně vpravo přepíná obsah
  panelu – Watchlist, Alerty (s počtem nepřečtených záznamů logu) a Replay (sessions);
  klik na aktivní tlačítko panel skryje, volba se pamatuje. Šířku panelu
  lze měnit tažením jeho levé hrany. Watchlist
  v pravém panelu (přidat/odebrat, klik kamkoli do řádku – symbol, cena,
  změna – přepne graf a řádek se zvýrazní jako aktuální, poslední cena
  a denní změna – Binance živě po 5 s, Yahoo po 60 s, IBKR po 30 s, Alpaca
  snapshots po 5 s).
- Více seznamů symbolů jako v TradingView: název aktivního seznamu v hlavičce
  watchlistu otevře nabídku seznamů (s počtem symbolů; klik přepne) a správu
  aktivního seznamu – „Nový seznam…“, „Přejmenovat…“, „Vytvořit kopii…“,
  „Vyprázdnit seznam“ a „Smazat seznam“ (s potvrzením; poslední seznam smazat
  nejde). Stejný symbol může být ve více seznamech; vyhledávání i tlačítko +
  přidávají do aktivního seznamu, aktivní seznam se pamatuje. Původní jediný
  watchlist se při prvním spuštění převede do seznamu „Watchlist“. Pod
  hlavičkou jsou kulatá tlačítka s iniciálou každého seznamu pro rychlé
  přepnutí (aktivní seznam je plný, tooltip ukazuje celý název). Klik na
  záhlaví sloupce „Symbol“ řadí symboly podle abecedy (A→Z, Z→A, zpět
  pořadí přidání); řazení platí pro všechny seznamy a pamatuje se.
- Typ grafu jako v TradingView: Bars, Candles, Hollow candles, Line, Line
  with markers, Step line, Area, Baseline (základna v polovině rozpětí dat),
  Columns, High-low a Heikin Ashi; barvy růstu / poklesu bere ze Settings
  grafu, volba se ukládá. V horní liště jsou tlačítka oblíbených typů
  (a aktuální typ), šipka vedle nich otevře nabídku všech typů – hvězdička
  u typu ho přidá do lišty / odebere (výchozí oblíbený je Candles).
- Timeframy jako v TradingView: v liště jen oblíbené (výchozí všechny),
  šipka vedle nich otevře nabídku všech timeframů zdroje po sekcích (Minuty,
  Hodiny, Dny) s hvězdičkami; timeframe zvolený z nabídky, který oblíbený
  není, je v liště jen dočasně. Oblíbené se ukládají.
- Vyskakovací nabídky mají zkrácenou animaci (90 ms, dialogy 120 ms).
- Long / Short position jako v TradingView (skupina Prognózy a měření):
  klik do grafu vytvoří pozici se vstupem v místě kliku, cílem a stopem ve
  vzdálenosti ~8 % výšky grafu (risk/reward 1:1) a šířkou ~15 % viditelných
  barů; zelená zóna cíle, červená zóna stopu. Úchopy: vstup (cena a začátek),
  cíl, stop a pravý okraj (šířka), tažení za plochu posune celou pozici.
  Štítek uprostřed ukazuje Qty (množství = riziko / vzdálenost stopu / lot),
  Risk/reward ratio a P&L simulovaného obchodu: obchod se aktivuje první
  svíčkou, která zasáhne vstup, uzavře ho první zásah stopu nebo cíle (oba
  ve stejné svíčce = stop), jinak je otevřený („Open P&L“ podle poslední
  svíčky); průběh ukazuje sytější zóna a čárkovaná spojnice vstup → výstup.
  U označené pozice nebo pod kurzorem jsou štítky Target / Stop (rozdíl
  ceny, %, ticky, Amount = účet ± zisk / ztráta). Settings pozice: velikost
  účtu, riziko (% účtu nebo částka), velikost lotu a ceny vstupu, cíle
  a stopu (s přesností cen instrumentu – akcie 2 místa, forex 5; tažení
  úchopů i celé pozice drží ceny na celé ticky); šablony včetně „Save as
  default“ pro nové pozice.
- Text jako v TradingView: nástroj Text po kliknutí do grafu otevře dialog
  „Text“ (dvojklik na text ho otevře znovu) se záložkami Text a Visibility –
  barva, velikost písma (10–40), tučné (B), kurzíva (I), víceřádkový obsah,
  pozadí a rámeček (zaškrtnutí + barva) a zalomení řádků (Text wrap, šířka
  240 px); text se kreslí od kotevního bodu doprava dolů. Šablony včetně
  „Save as default“ platí i pro text.
- Viditelnost kreseb a indikátorů (záložka Visibility v Settings každé
  kresby i indikátoru, vzhled jako v TradingView): řádky Ticks, Seconds
  (1–59), Minutes (1–59), Hours (1–24), Days (1–366), Weeks (1–52), Months
  (1–12) a Ranges; u jednotek s rozsahem zaškrtnutí, pole od / do
  a posuvník. Kresba / indikátor je vidět jen na timeframech, které
  odpovídají zaškrtnuté jednotce a rozsahu (např. Hours vypnuté = text není
  na 1h ani 4h). Výchozí je vidět všude. Ticks, Seconds, Months a Ranges se
  ukládají, ale aplikace takové timeframy zatím nemá.
- Šablony indikátorů jako v TradingView: šipka vedle tlačítka Indikátory
  otevře nabídku (záložky Indicators / Templates) – „Save indicator
  template…“ uloží všechny indikátory aktivního grafu pod názvem (volitelně
  Remember symbol / Remember interval), „Recently used“ ukazuje naposledy
  použité šablony s popisem (seznam indikátorů) a hvězdičkou, „Open
  template…“ otevře seznam všech šablon s hledáním a mazáním. Klik na šablonu
  nahradí indikátory aktivního grafu (případně přepne symbol / interval).
  Šablony s hvězdičkou mají v horní liště kulaté tlačítko s iniciálou pro
  rychlé přepnutí. Ukládají se do SQLite (`indicator_templates`).
- Více grafů najednou (Layout setup jako v TradingView): tlačítko s mřížkou
  v horní liště nabízí rozložení 1–16 grafů (2 vedle sebe / pod sebou, 3 v
  šesti uspořádáních, 4 v sedmi, 5, 6, 7, 8, 9, 10, 12, 14 a 16) s
  piktogramy. Každý graf má vlastní symbol, timeframe, typ grafu,
  indikátory, načítání historie a živý stream. Klik do grafu ho udělá
  aktivním (modrý rámeček) – na něj míří horní lišta (symbol, timeframe, typ
  grafu, Indikátory, Alert), watchlist a hledání. Kreslicí nástroj, magnet,
  zámek a skrytí kreseb platí pro všechny grafy (kreslit lze do kteréhokoli),
  kresba se hned ukáže i v dalších grafech se stejným symbolem. Sync in
  layout: Symbol a Interval (změna v aktivním grafu se projeví ve všech),
  Crosshair (crosshair ve stejném čase ve všech grafech), Time (posun jednoho
  grafu posune ostatní na stejný pravý okraj) a Date range (ostatní převezmou
  celý viditelný časový rozsah). Nový graf v rozložení převezme symbol,
  timeframe a typ aktivního grafu. Rozložení, synchronizace, aktivní graf
  a stav všech grafů se ukládají (nastavení `layout`) a přetrvají restart;
  styl svíček, objem, časové pásmo a seance jsou společné všem grafům.
- Alerty jako v TradingView: tlačítko Alert v horní liště (poslední cena),
  pravý klik do grafu „Přidat alert na <cena>“ nebo pravý klik na čáru
  (horizontální linie / polopřímka, trendová čára, polopřímka, přímka)
  „Přidat alert…“. Dialog „Create alert on SYMBOL, timeframe“: podmínka
  Crossing / Crossing Up / Crossing Down / Greater Than / Less Than, cíl cena
  nebo čára kresby (šikmé čáry se interpolují podle času a prodlužují; posun
  kresby se projeví do 5 s, smazaná kresba alert vypne se stavem Chyba),
  trigger Only Once / Once Per Bar / Once Per Bar Close / Once Per Minute,
  expirace (výchozí 30 dní) nebo bez expirace, zpráva se zástupnými texty
  `{{ticker}}`, `{{exchange}}`, `{{interval}}`, `{{close}}`, `{{time}}` (bez
  ruční úpravy se sama skládá z podmínky) a notifikace: Telegram, toast
  v aplikaci, zvuk a Webhook URL (POST se zprávou; platný JSON jako
  application/json, jinak text/plain – jako v TradingView). Alerty
  vyhodnocuje server na pozadí i bez otevřené stránky (jeden živý stream na
  symbol a timeframe, po výpadku znovupřipojení). Panel Alerty v pravém
  panelu má přepínač Alerty / Log (s počtem nepřečtených záznamů logu). Seznam alertů:
  tlačítko + (nový alert), hledání, řazení (nejnovější, nejstarší, podle
  symbolu, podle stavu – pamatuje se) a nabídka ⋯ s hromadnými akcemi
  (spustit všechny neaktivní, pozastavit všechny, smazat neaktivní / všechny
  s potvrzením). Řádek ukazuje zprávu, zdroj, symbol, timeframe a stav; klik
  přepne graf, dvojklik alert upraví, při najetí jsou tlačítka spustit /
  pozastavit, upravit, smazat a ⋯, pravý klik otevře stejnou nabídku
  (Upravit…, Klonovat…, Pozastavit / Spustit znovu, Zobrazit v grafu,
  Test alertu, Smazat); nabídka zůstane otevřená i po přejetí myší mimo
  řádek. Test alertu hned pošle zapnuté notifikace alertu – webhook se
  zprávou jako při skutečném spuštění, Telegram s označením „🧪 TEST“, toast
  a zvuk jen v aktuální stránce ({{close}} = poslední cena grafu se symbolem
  alertu, {{signal}} / {{message}} zkušební texty); stav alertu ani log se
  nemění a výsledek odeslání (✓ / ✗ s chybou) ukáže notifikace. Log spuštění ukazuje výsledek odeslání (✓ / ✗ Telegram, webhook;
  chyba v tooltipu); nepřečtený záznam má modrý proužek vlevo a klik na řádek
  ho označí jako přečtený (sníží počet u přepínače Log i v bublině tlačítka
  Alerty na pravé liště, která ukazuje počet nepřečtených záznamů logu). Aktivní cenové alerty
  zobrazeného symbolu jsou v grafu oranžové čárkované čáry s popiskem na ose.
- Settings aplikace (ozubené kolečko v horní liště): všechny sekce
  `config.yaml` v záložkách. Telegram – bot token (od @BotFather, příkaz
  /newbot), po zprávě botovi tlačítko „Zjistit chat ID“ doplní chat,
  „Odeslat test“ pošle zkušební zprávu; platí hned. Interactive Brokers –
  host, port a client ID, „Otestovat připojení“ (s hodnotami z dialogu);
  po uložení se zdroj hned přepojí bez restartu. Alpaca – endpoint, API key
  a secret; projeví se po restartu. Replay – chování kroku zpět (viz
  Replay); platí hned. Ukládají se jen změněné sekce, komentáře v souboru
  zůstanou.
- Měřič jako v TradingView: obdélník modrý při růstu / červený při poklesu,
  šipky středem (cena svisle, čas vodorovně) a štítek s Δcenou, % a počtem
  ticků, počtem barů a délkou úseku a součtem objemu.
- Nadpis grafu ve stylu TradingView: „Název · timeframe · burza“ (název firmy
  / páru a burzu dodá zdroj), barevná tečka stavu dat (zelená živě, oranžová
  zpožděno), O/H/L/C obarvené podle směru svíčky, změna proti předchozímu
  závěru (absolutně i v %), řádek Vol (skrytý u instrumentů bez objemu –
  forex, indexy) a pod ním řádky indikátorů s hodnotami pod kurzorem. Přesnost
  cen (osa i legenda) se určuje z dat (forex 5 míst, akcie 2); ceny z Yahoo
  Finance přicházejí ve float32 a čistí se od šumu převodu (VIX 23.11, ne
  23.11000061), i ve svíčkách uložených v cache.
- Indikátory: tlačítko „Indikátory“ otevře nabídku jako v TradingView –
  hledání, kategorie (klouzavé průměry, pásma a kanály, trend, oscilátory,
  volatilita, objem, vlastní, aktivní, oblíbené) a seznam s popisem; klik indikátor přidá
  s výchozími parametry. Hvězdička vpravo u položky (při najetí myší, oblíbený
  má plnou žlutou) přidá indikátor do kategorie Oblíbené / odebere ho; oblíbené
  se ukládají. K dispozici SMA, EMA, WMA, HMA, VWMA, VWAP, Bollinger
  Bands, Keltner, Donchian, Ichimoku, Parabolic SAR, Supertrend, ADX, RSI,
  MACD, Stochastic, Stoch RSI, CCI, Williams %R, Momentum, ROC, Awesome
  Oscillator, TRIX, TSI, ATR, OBV, MFI, CMF a vlastní indikátory. Řádek indikátoru v legendě (i
  v titulku panelu) má při najetí myší oko (skrýt/zobrazit), ozubené kolečko
  (Settings) a křížek (odebrat). Výška panelu indikátoru se mění tažením
  jeho horní hrany (nahoru zvětšuje na úkor hlavního grafu, u dvou panelů
  pod sebou na úkor panelu nad ním), dvojklik na hranu vrátí výchozí výšku;
  výška se pamatuje v prohlížeči pro každou instanci indikátoru. Najetí myší na čáru indikátoru (v grafu
  i v panelu) nebo na jeho řádek v legendě ho zvýrazní jako v TradingView:
  kolečka na jeho čarách v pravidelném rozestupu a podbarvený řádek legendy;
  klik (i pravý) ho označí – kolečka zůstanou a řádek legendy má modrý
  rámeček; klik jinam nebo Esc označení zruší, Del označený indikátor
  odebere. Dialog Settings má u každého indikátoru
  jako v TradingView záložky Inputs, Style a Visibility; přepnutí záložky
  nemění velikost okna (zvětší se jen, když se obsah nevejde). Style jako
  v TradingView: u každého výstupu (čáry, histogramu) zaškrtávátko zobrazení,
  tlačítko barvy (paleta, průhlednost, Thickness 1–4 a Line style plná /
  čárkovaná / tečkovaná) a tlačítko druhu vykreslení s přepínačem Price line
  (vodorovná linka u poslední hodnoty) a volbou Line, Line with breaks, Step
  line, Step line with breaks, Step line with diamonds (body jsou kulaté),
  Histogram, Area, Area with breaks, Columns a Circles (Line, Step line
  a Area mezery bez hodnot přemostí, varianty „with breaks“ je přeruší; Cross
  Lightweight Charts neumí). Output values: Precision (Default nebo 0–8
  desetinných míst; platí pro hodnoty v legendě a pro osu panelu – popisky
  čar přes hlavní graf ukazuje Lightweight Charts vždy v přesnosti
  instrumentu), Labels on price scale (popisek poslední hodnoty na cenové ose,
  výchozí zapnuto) a Values in status line (hodnoty v legendě). Výstupy
  a Output values se ukážou jen u indikátorů, které čáry vrací. Dál
  Graphic objects – Lines (úsečky indikátoru, např. trendové linky)
  a Tables (tabulka scanneru) jen u indikátorů, které je kreslí, a Input values – Inputs in status line
  (vypnuté = v legendě jen název bez hodnot vstupů). Visibility: timeframy,
  na kterých se indikátor počítá a kreslí včetně tabulky scanneru (v legendě
  zůstává). Styl a viditelnost se ukládají s instancí (jen změněné proti
  výchozímu stavu) a do šablon. Inputs se skládá ze schématu parametrů indikátoru: skupiny jako nadpisy
  sekcí, v řádku popisek vlevo a pole vpravo (číslo, barva, výběr, text,
  timeframe, symbol), přepínač přes celý řádek, nápověda v tooltipu. Pole
  symbolu má tužku, která otevře hledání symbolu a zvolený „zdroj:SYMBOL“
  zapíše do pole (graf se nemění).
  Graf načte tolik historie, kolik aktivní indikátory potřebují (po přidání
  indikátoru donačte starší svíčky). Instance indikátoru, který se nenačetl
  (chyba v jeho souboru), v grafu zůstane s poznámkou „nenačten“ i s nastavením.
- Vlastní indikátory: každý má vlastní adresář
  `tickview/indicators/custom/<název>/` se souborem `indicator.py` (třída
  odvozená od `Indicator` z `tickview/indicators/api.py` a proměnná
  `INDICATOR`); další soubory adresáře (verze Pine skriptu v `pine/`,
  poznámky) TickView ignoruje. Testy indikátoru patří do jeho adresáře
  `tests/` (s `__init__.py` v adresáři indikátoru i v `tests/`, relativní
  importy `from ..indicator import …`; chybějící `__init__.py` ohlásí
  test) a `python -m unittest` je najde sám; testy aplikace na žádném
  vlastním indikátoru nezávisí. Indikátor importuje jen `tickview.indicators.api`
  a pandas/numpy, dostane svíčky, parametry a `Context` (druh instrumentu,
  tick, pásmo burzy, timeframe) a vrátí `Result`: čáry, úrovně panelu,
  úsečky, značky u svíček a signály pro scanner (indikátor s úsečkami
  nastaví `has_lines = True`, aby Style nabídl přepínač Lines). Výchozí druh
  vykreslení čáry určuje `Line.type` (`"line"`, `"histogram"` nebo jiný druh
  záložky Style, např. `"linebr"`, `"circles"`), výchozí tloušťku a styl volby
  `lineWidth` a `lineStyle`; uživatel je v záložce Style změní. Nový indikátor = nový
  adresář, odebrání = smazání adresáře. Chyba importu nebo jiná verze
  rozhraní (`API_VERSION`) se zapíše do logu a ukáže v kategorii Vlastní,
  ostatní indikátory fungují dál. Popis konkrétního indikátoru (výpočet,
  výchozí nastavení, zvláštnosti) je v `README.md` v jeho adresáři.
- Scanner je součástí vstupů indikátoru, který k parametrům připojí vstupy
  scanneru (skupiny Scanner – `scanner_params` z rozhraní indikátorů,
  jako `request.security` a `table` v Pine) a vrací signály
  (`has_signals = True`, `Result.signals`): timeframe scanu (výchozí 1h),
  počet posledních svíček pro signál, pozice tabulky (top_right, top_left,
  middle_right, bottom_right, bottom_left, nezobrazovat), jen trhy se
  signálem, velikost textu, skrytí prefixu zdroje; alert před zavřením
  svíčky (výchozí vypnutý, indikátor ho může zapnout) – dva nezávislé prahy
  v minutách (výchozí 10 a 5), každý nejvýš jednou za svíčku, jedna souhrnná
  zpráva se seznamem trhů se signálem na běžící svíčce; a 40 polí symbolů ve
  tvaru „zdroj:SYMBOL“ (bez prefixu = zdroj grafu; výchozí prázdná, indikátor
  může předvyplnit své symboly). Pro každý symbol se spočítá indikátor se
  stejnými parametry na timeframu scanu a v grafu s indikátorem se ukáže
  tabulka („<zkratka indikátoru> (1h) | Signál (svíčka)“, LONG(0) / SHORT(1) …
  – 0 = běžící svíčka; zelená long, červená short, oranžová oba). Při „jen se signálem“
  se symboly bez dat (např. neběžící TWS) sloučí do jednoho řádku. Scanner
  běží na serveru i bez otevřené stránky nad instancemi indikátorů
  v uloženém rozložení grafů (shodné instance ve více grafech se počítají
  jednou), přepočítá se každou minutu a před odesláním alertu si data
  obnoví; skrytý indikátor nebo tabulka „nezobrazovat“ se počítá jen se
  zapnutým alertem. Data bere přes cache – první stažení historie (hlavně
  z IB) může trvat několik minut, další průchody stahují jen novou svíčku.
  Zpráva scanneru odejde jako v TradingView jen přes alert na indikátor
  s podmínkou „Any alert() function call (scanner)“ (Alert → Condition:
  zkratka indikátoru); notifikace (Telegram, toast, zvuk, webhook) se
  nastavují v tomto alertu, ne ve vstupech indikátoru. Alert zůstává aktivní (každá
  zpráva ho spustí), zpráva alertu má zástupný text `{{message}}` (výchozí
  zpráva = jen text scanneru). Alert patří ke druhu indikátoru – posílá
  zprávy všech instancí indikátoru v rozložení se zapnutým alertem.
- Alert na signál indikátoru (jako v TradingView): v dialogu alertu je první
  pole zdroj podmínky – cena, nebo indikátor v grafu, který vrací signály
  (alert si uloží parametry instance). Podmínka Signál Long / Short / Long
  nebo Short, spouštění „X min před zavřením svíčky“ (signál na běžící
  svíčce, jako `alert()` v Pine), Once Per Bar Close (signál na uzavřené
  svíčce), Once Per Bar nebo Only Once. Zpráva má zástupný text `{{signal}}`
  (směr a štítek signálu od indikátoru, např. „LONG <štítek>“). Alert lze
  vytvořit i pravým klikem na indikátor (legenda) → „Přidat alert…“. Vyhodnocuje server na pozadí: historie
  symbolu se načte jednou, dál se doplňuje živými svíčkami a indikátor se
  přepočítá jen tehdy, když může alert spustit.
- Časové připomínky (panel Alerty → ⋯ → Časové připomínky…): opakovaná
  notifikace v okně od–do (např. 22:30–22:55 po 5 minutách, výzva k uzavření
  pozic před noční seancí) ve zvoleném pásmu a dny v týdnu; zapnutí /
  vypnutí, úprava, smazání. Běží na serveru; spuštění se zapisují do logu
  alertů (bez ceny).
- Replay – backtest ručních obchodů v historii (jako FX Replay / Bar Replay
  v TradingView): tlačítko Replay v horní liště (i název session v liště
  replaye) otevře záložku Replay v pravém panelu se seznamem sessions – může
  jich být libovolně mnoho; klik na session ji otevře (přepne na ni),
  tužka ji přejmenuje, koš smaže i s obchody a kresbami, tlačítko + založí
  novou session pro symbol aktivního grafu. Start se zadá datem a časem, nebo „Vybrat v grafu“ (klik
  na svíčku – ta je poslední odkrytá, budoucnost vpravo se při výběru
  zastíní); dál měna účtu, počáteční zůstatek, jednotek v lotu (forex
  100 000, jinak 1), krok lotu, tick, spread a skluz v tickách, komise za lot
  a stranu, pevný kurz k měně účtu (0 = automaticky) a automatická pauza
  (po naplnění příkazu, po uzavření obchodu, při signálu indikátoru na
  uzavřené svíčce; ve výchozím stavu vše vypnuté). Přesnost simulace = základní timeframe:
  automaticky nejjemnější, pro který má zdroj data (Binance 1m roky
  zpátky, Yahoo 1m jen ~30 dní, pak 5m / 15m / 1h / 1D), nebo zvolený.
  Grafy se symbolem session přejdou do replaye (fialová tečka v nadpisu,
  štítek „replay“, bez živého streamu, alertů a tabulky scanneru): do
  prohlížeče jdou jen odkryté svíčky, poslední svíčka grafu se skládá ze
  základních svíček a během přehrávání roste, indikátory se počítají jen
  z odkrytých dat. Timeframe grafu jde během replaye měnit (jen ne jemnější
  než přesnost replaye), kurzor zůstává; v rozložení více grafů jedou
  všechny grafy se symbolem session na společném čase.
  Lišta replaye pod grafy: název session (sessions), nůžky = krok zpět
  klikem do grafu, Play / pauza (mezerník), krok vpřed (Shift+→; „1 bar
  grafu“ dokončí další svíčku aktivního grafu, „1 bar základu“ jednu
  základní svíčku), rychlost 1×–100× (základních svíček za sekundu), skok
  na datum a čas (vpřed se obchody vyhodnotí svíčku po svíčce, dřív = krok
  zpět), skok na čas dne (pole s časem, výchozí 9:00, ukládá se do session;
  tlačítko ☀ posune replay těsně před nejbližší svíčku toho času – čas replaye
  = 9:00 – a každý další klik na další den; dny bez dat se přeskočí,
  víkendové otevření forexu v neděli večer také, a když trh otevírá později,
  skok skončí před první svíčkou dne), čas replaye, Buy / Sell, zůstatek, equity a Open P&L, panel
  obchodů, nastavení session (změna nákladů či účtu přepočítá celou
  session) a ukončení replaye (session zůstane uložená i s kurzorem).
  Příkazy: Market, Limit a Stop (buy stop / sell stop), SL a TP v cenách,
  velikost v lotech / kontraktech, nebo podle rizika v % účtu či částkou
  (z ceny vstupu a stop lossu, zaokrouhleno dolů na krok lotu), expirace
  (prázdné = GTC), štítek setupu a poznámka; dialog ukazuje loty, riziko
  a R:R a kontroluje polohu limitu / stopu a SL / TP. Obchod jde zadat i
  nástrojem Long / Short position: nakreslená pozice zůstane v grafu, tažením
  se doladí vstup, SL a TP a obchod se zadá pravým klikem „Zadat obchod…“
  nebo dvojklikem – dialog příkazu převezme úrovně z kresby (druh podle
  polohy vstupu vůči ceně – long pod cenou Limit, nad cenou Stop, u ceny
  Market; velikost 1 % rizika); po odeslání kresbu nahradí čáry příkazu. V grafu: čára vstupu otevřeného obchodu se štítkem (#, směr,
  loty, P&L, ✕ = zavřít za trh), SL a TP čárkovaně se štítkem částky (tažením
  se posunou, ✕ úroveň odebere), čekající příkaz čárkovaně (tažení ceny,
  SL i TP, ✕ = zrušit), šipky vstupů a výstupů a čárkovaná spojnice vstup →
  výstup uzavřených obchodů (zelená zisk, červená ztráta).
  Plnění: data jsou bid, buy se plní na asku (bid + spread); limit na své
  ceně, při gapu za otevření (lepší cenou), stop na své ceně, při gapu za
  otevření (horší) + skluz; SL a TP stejně (SL přes víkendový gap = ztráta
  navíc); stop i cíl v jedné základní svíčce = stop a obchod je označený
  jako odhad (⚠), ve svíčce vstupu se cíl nebere. P&L se převádí na měnu
  účtu kurzem v čase výstupu: stejná měna 1, forexový pár se základní
  měnou = měna účtu přes 1 / cena, jinak denní kurzy z Yahoo Finance
  (`QUOTEACCOUNT=X`, příp. obrácený pár), stablecoiny jako USD, londýnské
  akcie v pencích. Panel obchodů: Pozice (P&L, R, upravit SL / TP,
  break-even, zavřít polovinu, zavřít), Příkazy (upravit, zrušit), Historie
  (vstup, výstup, důvod, P&L, R, štítek; klik posune graf na vstup, tužka =
  poznámka a štítek, v dialogu i MAE / MFE), Statistiky (čistý zisk a %,
  win rate, profit factor, průměrná výhra / ztráta, expectancy, průměrné
  a celkové R, max. drawdown, nejdelší série ztrát, největší výhra / ztráta,
  komise, počet odhadnutých obchodů, křivka zůstatku a výsledky podle
  štítku setupu) a export obchodů do CSV (středník, UTF-8 s BOM pro Excel).
  Výšku panelu obchodů i šířku pravého panelu lze měnit tažením jejich hrany
  (velikost si pamatuje prohlížeč).
  Kresby i indikátory v replayi patří k session – každá session má vlastní
  kresby a vlastní indikátory každého grafu rozložení (s nastavením
  a stylem), oddělené od živého grafu i od ostatních sessions. Graf, který
  do session vstoupí poprvé, převezme kopii svých živých indikátorů; dál
  jsou sady nezávislé a po ukončení replaye se vrátí živé indikátory
  (scanner počítá vždy jen s živými).
  Krok zpět (Settings → Replay): povolený a smaže příkazy a obchody po bodu
  návratu (výchozí), povolený po potvrzení, nebo zakázaný. Session se
  ukládá do SQLite (`replay_sessions`): příkazy s časem zadání, ze kterých
  se stav účtu deterministicky přepočítá (obnovení po restartu, krok zpět).
- Pravý klik a dvojklik (jako v TradingView): na kresbě menu „Settings…“
  (barva, šířka a styl čáry, u textu obsah a vzhled písma, u všech záložka
  Visibility) a „Smazat
  objekt“, dvojklik rovnou Settings; na čárkované čáře cenového alertu menu
  „Upravit alert…“ (dialog alertu) a „Smazat alert“; na čáře indikátoru, v jeho panelu nebo na
  řádku legendy menu „Settings…“ (parametry), „Skrýt“, „Odebrat indikátor“,
  dvojklik rovnou Settings; jinde v grafu „Settings…“ grafu: barvy těla,
  okrajů a knotů svíček pro růst / pokles (každý prvek lze vypnout),
  obarvení svíček podle předchozího závěru a zobrazení objemu (Volume; objem
  lze skrýt i okem v řádku Vol; u instrumentu bez objemu – forex IBKR
  MIDPOINT, indexy – se objem nezobrazí a dialog to vysvětlí). Když se
  data symbolu nepodaří načíst, graf se vyprázdní (nezůstanou v něm svíčky
  předchozího symbolu pod novým nadpisem). Změny v Settings grafu se hned ukazují
  v grafu, uloží se tlačítkem Ok, Cancel / zavření je vrátí.
- Výběr barvy jako v TradingView (Settings grafu, indikátoru a kresby): řada šedých, hlavní barvy, šest řad odstínů,
  vlastní barvy (tlačítko + otevře systémový výběr; ukládají se v prohlížeči)
  a průhlednost posuvníkem nebo v % – průhledná barva se ukládá jako
  `rgba(…)`, plná jako `#rrggbb`.
- Šablony (Template) jako v TradingView v dialozích Settings grafu,
  indikátoru (pro typ indikátoru) a kresby (pro typ kresby): „Apply defaults“
  (výchozí hodnoty), „Save as…“ (pojmenovaná šablona; stejný název přepíše),
  pod čarou uložené šablony – klik je použije, křížek smaže. U indikátorů
  a kreseb navíc „Save as default“ – nově přidaný indikátor (parametry,
  barva) nebo nová kresba (barva, šířka a styl čáry, písmo) daného typu
  převezme výchozí šablonu (a „Apply defaults“ ji použije); „Reset default“
  vrátí tovární hodnoty. Šablony se ukládají do SQLite.
- Oblíbené nástroje: ve flyout nabídce nástrojů je u každého nástroje
  hvězdička, u skupin s jediným nástrojem (kurzor, měřič) pravý klik na
  tlačítko lišty. Oblíbené nástroje se ukazují v plovoucím panelu (výchozí
  pozice nahoře uprostřed grafu), který lze přetáhnout za úchop ⋮⋮ kamkoli
  v okně, i nad horní lištu;
  klik nástroj vybere, pravý klik ho z oblíbených odebere. Tlačítko hvězdy
  v liště panel skryje / zobrazí. Oblíbené, pozice i viditelnost panelu se
  ukládají. Lišta má skupiny jako TradingView – čáry, kanály a pitchforky
  jsou v jedné nabídce rozdělené do sekcí.
- Kreslení: výběr textu klikem kamkoli do textu (rámeček označení), Del /
  pravý klik ho smaže. Magnet má režimy slabý (přichytí bod k O/H/L/C, když je
  kurzor do 25 px) a silný (vždy na nejbližší O/H/L/C); při kreslení ukazuje
  crosshair i kroužek na místě přichycení, Ctrl/⌘ magnet dočasně přepne a
  režim se ukládá. Aktivní nástroj ukončí Esc, pravý klik, opakovaný klik na
  nástroj i vypnutí režimu tužky.
- Stavová lišta vpravo: přepínač seance RTH / ETH (Regular / Extended trading
  hours – u IBKR a Alpaca akcií; RTH zobrazí jen svíčky 9:30–16:00 New York,
  cache drží všechna data; u krypta, forexu a Yahoo je neaktivní) a hodiny
  s časovým pásmem grafu – nabídka UTC, Exchange (pásmo burzy), místní čas
  a města jako v TradingView; pásmo platí pro časovou osu i crosshair, denní
  svíčky se ukazují v UTC; volba se ukládá.
- Když posledních 600 svíček nestačí na zaplnění grafu (RTH před otevřením
  trhu, noc, víkend), načtení sáhne dál do minulosti – graf tak ukáže
  předchozí seanci místo hlášky o chybějících datech.
- Do cache se ukládají jen uzavřené svíčky; rozpracovaná poslední svíčka se
  při každém načtení stahuje znovu. Cache si pamatuje skutečně stažené
  intervaly, takže se doplní i díry uprostřed uložených dat.
- Při ukončení aplikace se zavřou síťová spojení zdrojů i databáze.

### Alpaca (volitelný zdroj živých akcií)

- Zdroj se aktivuje zadáním API klíčů (plán Basic zdarma stačí – používá se
  IEX feed); bez klíčů se do aplikace vůbec nepřidá. Dvě cesty:
  1. V aplikaci: ozubené kolečko v horní liště → záložka Alpaca → vložit
     Key a Secret → Ok → restart aplikace.
  2. Přímo: doplnit `config.yaml` v kořenu projektu a restartovat:

         alpaca:
           endpoint: https://paper-api.alpaca.markets/v2
           api_key: PK...
           secret: ...

- Živá data: timeframe 1m přes websocket IEX (minutové bary; z nich se
  skládá i živá 2m svíčka), delší
  timeframy dotazováním REST v intervalu (15 s intraday, 60 s denní) –
  websocket Alpaca jiné než minutové bary neposílá. Všechny 1m grafy
  (i ve více záložkách) sdílí jedno websocket spojení, protože bezplatný
  účet povoluje jen jedno.

- S neplatnými klíči se zdroj chová korektně: chybu 401 zobrazí ve stavové
  liště a zůstanou funkční ostatní zdroje.

### Interactive Brokers (TWS / IB Gateway)

- Zdroj `IBKR` se připojuje k běžící TWS (demo) nebo IB Gateway s povoleným
  API. Výchozí hodnoty: host `127.0.0.1`, port `7497` (demo TWS),
  clientId `30` – lze změnit v aplikaci (Settings → Interactive Brokers,
  platí hned) nebo v `config.yaml`:

      ibkr:
        host: 127.0.0.1
        port: 7497
        client_id: 30

- Forex IDEALPRO: dotaz typu `EURUSD`, `EUR/USD` nebo jen `EUR` nabídne
  páry IBKR (symbol `EUR.USD`, data MIDPOINT, bez objemu) – návrhy fungují
  i bez běžící TWS, data se pak stahují z TWS.
- Spotové kovy, indexová a ropná CFD od IB (data MIDPOINT, obchodují téměř
  nonstop jako kovy, indexy a ropa OANDA v TradingView): `XAUUSD` (zlato),
  `XAGUSD` (stříbro), `IBUSOIL` (ropa WTI), `IBUKOIL` (ropa Brent),
  `IBUST100` (Nasdaq 100), `IBUS500` (S&P 500), `IBUS30` (Dow), `IBDE40`
  (DAX), `IBGB100`, `IBEU50`, `IBFR40`, `IBJP225`, `IBCH20`, `IBES35`,
  `IBNL25`, `IBHK50`, `IBAU200`. Hledání je nabídne i podle názvu (gold,
  oil, DAX, S&P 500, Nasdaq…); přesně zadaný symbol se v TWS už nehledá.
  IB nemá CFD na Russell 2000 – místo něj ETF `IWM` (jako akcie; CFD na IWM
  přes API historii nevrací, cena je stejná, obchoduje se 4:00–20:00 New
  York). Krypto (PAXOS) vyžaduje v IB oprávnění k tržním datům.
- Futures jako kontinuální front month (jako `ES1!` v TradingView): v tabulce
  jsou akciové indexy `ES1!`, `MES1!`, `NQ1!`, `MNQ1!`, `YM1!`, `MYM1!`,
  `RTY1!`, `M2K1!`, `NKD1!`, `FDAX1!`, `FDXM1!`, `FESX1!`, `VX1!`, kovy
  `GC1!`, `MGC1!`, `SI1!`, `HG1!`, `PL1!`, energie `CL1!`, `MCL1!`, `BZ1!`,
  `NG1!`, dluhopisy `ZT1!`, `ZF1!`, `ZN1!`, `ZB1!`, `FGBL1!`, zemědělské
  `ZC1!`, `ZS1!`, `ZW1!`, měny `6E1!`, `6B1!`, `6J1!`, `6A1!`, `6C1!`,
  `6S1!` a krypto CME `BTC1!`, `MBT1!`, `ETH1!`, `MET1!`. Hledání je nabídne
  podle kořene (`ES`, `GC`) i názvu (gold, nasdaq, DAX); další futures najde
  přes TWS (podklad s futures) jako obecný symbol `KOŘEN1!@BURZA`, např.
  `VIX1!@CFE` – ten lze zadat i ručně. Řada není cenově upravená: každý úsek
  historie bere kontrakt, který byl tehdy front month (expirované kontrakty
  s `includeExpired`, TWS je drží zhruba dva roky), takže při rollu může být
  v grafu cenový skok. Na další kontrakt se přechází N dní před posledním
  obchodním dnem (indexy 8, kovy 32, dluhopisy 25, zemědělské 18, měny 7,
  ropa 5, plyn a Brent 3, VIX a krypto 1; obecný symbol 5) a u kovů
  a komodit jen mezi aktivními měsíci (např. zlato G/J/M/Q/V/Z). Živá svíčka
  se v okamžiku rollu sama přepne na nový kontrakt. Data TRADES s objemem,
  čas burzy podle trhu (CME Group Chicago, EUREX Frankfurt), bez filtru RTH;
  nadpis grafu ukazuje aktuální kontrakt (např. „E-mini S&P 500 · ESZ6“).
  Bez předplatného futures dat posílá TWS zpožděná data (štítek „zpožděno“).
  Nová replay session u futures předvyplní velikost kontraktu násobitelem
  (ES 50, GC 100…) a krok velikosti pozice 1 (celé kontrakty).
- Chybné knoty u dat MIDPOINT (forex, kovy, CFD): při otevření trhu po
  víkendu nebo bez likvidity jedna strana kotace na okamžik ulétne (např. ask
  stříbra 59,51 místo 48,6) a střed bid/ask udělá knot, za kterým není žádný
  obchod. Knot delší než 10× medián rozsahu okolních svíček (až 10 na každou
  stranu) a zároveň než 0,5 % ceny se zastřihne na tělo svíčky – v historii
  i v živé svíčce; skutečné zprávové pohyby (výplatnice v USA) zůstanou.
  Filtr běží při čtení historie (cache drží surová data, okolí se bere
  z cache i za okraji načteného úseku), takže platí i pro už uložené svíčky.
- Kvalifikované kontrakty (conId) se cachují – opakované dotazy scanneru
  a watchlistu nevolají qualifyContracts v TWS znovu.
- Historie, vyhledávání (matching symbols) i ceny watchlistu jdou přes TWS.
  Živá svíčka se odebírá průběžně (`reqHistoricalData` s `keepUpToDate`,
  aktualizace zhruba po 5 s); po výpadku TWS se odběr obnoví sám.
- Štítek „živě / zpožděno“ odpovídá předplatnému: při prvním otevření grafu
  instrumentu se zdroj krátce přihlásí k tickům (`reqMarketDataType(3)` +
  `reqMktData`, ne placený snapshot) a podle `marketDataType` / chyby
  o chybějícím předplatném pozná, zda TWS posílá živá, nebo zpožděná data;
  výsledek si do ukončení aplikace pamatuje. Když TWS průběžné bary odmítne,
  zdroj přejde na dotazování v intervalu (15 s intraday, 60 s denní) se
  štítkem „zpožděno“. Když TWS neběží, zdroj to nahlásí a ostatní zdroje
  fungují dál.
- Když nastavený clientId už používá jiný klient (např. druhá instance
  aplikace, chyba 326), zkusí se automaticky následující clientId (až 4 další).

## Struktura projektu

- `LICENSE` – text licence Apache License 2.0
- `main.py` – vstupní bod: CLI parametry (`--port`, `--native`, `--open`),
  registrace statických souborů, spuštění UI
- `run.sh` – zkrácené spuštění na macOS/Linux: příprava `.venv`, instalace
  závislostí při změně `requirements.txt` a předání parametrů do `main.py`
- `tickview/config.py` – cesty k runtime datům, cache DB, načítání
  konfigurace `config.yaml` a zápis sekce se zachováním komentářů
- `tickview/providers/base.py` – rozhraní DataProvider (search/history/stream/
  tickers/describe/close, pásmo burzy, druh instrumentu, tick ceny, velikost kontraktu, hlavní seance), Candle, StreamStatus
  (výpadek/obnovení streamu, případně živá/zpožděná data), SymbolInfo, kanonické timeframy, skládání
  2m/10m z kratších svíček (historie i živě), filtr chybných knotů, omezovač dotazů a společná
  smyčka pollingu živých dat
- `tickview/providers/binance.py` – zdroj Binance: vyhledání symbolů + historie
  přes veřejný REST, živý websocket stream, omezovač dotazů, exponenciální
  backoff po 429
- `tickview/providers/yahoo.py` – zdroj Yahoo Finance (yfinance): akcie, ETF,
  indexy, forex; historie v to_thread, živá aktualizace pollingem
- `tickview/providers/ibkr.py` – zdroj Interactive Brokers (TWS API, ib_async):
  americké akcie, forex IDEALPRO, spotové kovy, indexová a ropná CFD, kontinuální futures; napojení na TWS 127.0.0.1:7497, clientId
  30 (při obsazení další), průběžné bary keepUpToDate a zjištění živých /
  zpožděných dat podle předplatného
- `tickview/providers/ibkr_futures.py` – tabulka kontinuálních futures IBKR,
  symboly `ES1!` / `KOŘEN1!@BURZA`, sestavení řetězce kontraktů, výpočet
  rollu a rozdělení historie na úseky front month kontraktů
- `tickview/providers/alpaca.py` – volitelný zdroj Alpaca (americké akcie):
  historie feed=iex, sdílený živý websocket IEX (1m) / polling (delší
  timeframy), aktivace po zadání klíčů
- `tickview/providers/__init__.py` – registr zdrojů (nový zdroj = jeden řádek)
- `tickview/logic/cache.py` – SQLite cache svíček, nastavení, kreseb,
  alertů a jejich logu, seznamů symbolů (watchlistů), session replaye a evidence stažených intervalů; skládání historie z cache a sítě (se zastřižením chybných knotů z kotací)
  (ze sítě stahuje jen chybějící úseky) a „aspoň N posledních svíček“ se
  zvětšováním okna (graf i scanner)
- `tickview/indicators/api.py` – rozhraní indikátorů (jediný modul, který
  indikátor importuje): Param (schéma parametrů), Indicator, Context,
  Result, Line, Segment, Marker, Signal, vstupy scanneru (`scanner_params`),
  kategorie a `API_VERSION`
- `tickview/indicators/builtin.py` – 28 vestavěných indikátorů (pandas-ta)
  jako deklarace přes rozhraní indikátorů
- `tickview/indicators/registry.py` – registr indikátorů: vestavěné + loader
  vlastních z `custom/<název>/indicator.py` s izolací chyb
- `tickview/indicators/compute.py` – instance indikátorů (kontrola, načtení
  uložených, zachování nenačtených), kontext výpočtu (tick, druh
  instrumentu), payload pro graf (čáry, panely, úsečky, značky, legenda)
  se vzhledem výstupů podle záložky Style a výstupy indikátoru pro záložku
  Style (výpočet nad umělými svíčkami)
- `tickview/indicators/custom/<název>/` – vlastní indikátory: `indicator.py`,
  `README.md` s popisem indikátoru, testy (`tests/`) a další soubory (např.
  verze Pine skriptu)
- `tickview/logic/scanner.py` – logika scanneru: úlohy ze vstupů instancí
  indikátorů v rozložení, řádky ze signálů, data tabulky v grafu, texty
  a alert před zavřením svíčky (CloseAlertWindow)
- `tickview/logic/reminders.py` – časové připomínky: kontrola zadání, sloty
  časového okna (ReminderTracker)
- `tickview/logic/styles.py` – kontrola barev (`#rrggbb` / `rgba`), styl
  svíček a typy grafu, styl kreseb (včetně vzhledu textu a viditelnosti
  podle timeframu), šablony nastavení (Template), názvy a oblíbené nástroje
- `tickview/logic/alerts.py` – logika alertů: podmínky, triggery
  (AlertTracker), cena čáry kresby v čase, kontrola definice, zpráva se
  zástupnými texty a tělo webhooku
- `tickview/services/alerts.py` – AlertEngine: vyhodnocování aktivních
  alertů na pozadí nad živými streamy zdrojů, vypršení, log a ohlášení
  otevřeným stránkám
- `tickview/services/notify.py` – Telegram bot (sendMessage, zjištění chat
  id) a webhook
- `tickview/services/listeners.py` – posluchači otevřených stránek pro
  služby na pozadí (AlertEngine, ScannerService)
- `tickview/services/scanner.py` – ScannerService: úlohy z uloženého
  rozložení, přepočet signálů přes symboly ze vstupů indikátoru na pozadí
  a alert před zavřením svíčky
- `tickview/services/reminders.py` – ReminderService: časové připomínky na
  pozadí
- `tickview/logic/replay.py` – engine replaye: příkazy (market / limit /
  stop), plnění se spreadem, skluzem a gapy, SL / TP, částečné uzavření,
  komise, MAE / MFE, převod měn, velikost pozice podle rizika,
  deterministický přepočet session z příkazů, statistiky, skládání svíček
  grafu ze základních svíček a export CSV
- `tickview/services/replay.py` – ReplayController: základní svíčky session
  (přednačítání za kurzorem, budoucnost jen na serveru), přehrávání, krok,
  skok, krok zpět, příkazy uživatele a ukládání session
- `tickview/logic/sessions.py` – filtr hlavní obchodní seance (RTH) a seznam
  časových pásem pro nabídku
- `tickview/logic/layouts.py` – rozložení více grafů (Layout setup), volby
  synchronizace, kontrola uložené konfigurace a změna počtu grafů
- `tickview/logic/watchlists.py` – řazení symbolů watchlistu podle abecedy
- `tickview/logic/indicator_templates.py` – šablony indikátorů (uložení,
  přepsání, oblíbené, naposledy použité, popis)
- `tickview/ui/chart_pane.py` – jeden graf rozložení (ChartPane): symbol,
  timeframe, načtení historie, donačítání, živý stream, data replaye
  (historie do kurzoru, skládání svíčky ze základních svíček), indikátory,
  kresby viditelné na timeframu a čáry alertů
- `tickview/ui/lightweight_chart.py` – vlastní komponenta NiceGUI obalující
  Lightweight Charts (init, výměna dat, inkrementální svíčka, motiv,
  indikátory, kreslení, fronta událostí z grafu)
- `tickview/ui/color_button.py` + `color_button.js` – tlačítko barvy
  (vlastní komponenta NiceGUI), které otevírá výběr barvy
- `tickview/ui/icons.py` – SVG piktogramy lišty kreslení a typů grafu
- `tickview/ui/templates.py` – dialogy Settings (hlavička, tlačítka) a nabídka
  šablon „Template“
- `tickview/ui/param_form.py` – formulář parametrů indikátoru ze schématu
  (záložka Inputs v Settings indikátoru, pole symbolu s hledáním)
- `tickview/ui/plot_style_fields.py` – výstupy a Output values v záložce
  Style Settings indikátoru (zobrazení, druh vykreslení, Price line, přesnost)
- `tickview/ui/plot_style_button.py` + `plot_style_button.js` – tlačítko
  stylu výstupu (barva s průhledností, Thickness, Line style)
- `tickview/ui/visibility_fields.py` – pole záložky Visibility (kresby
  i indikátory)
- `tickview/ui/replay_panel.py` – UI replaye: lišta přehrávání, panel
  obchodů (Pozice, Příkazy, Historie, Statistiky), záložka sessions v pravém
  panelu, dialogy nové session, přejmenování, příkazu, úpravy obchodu, skoku
  a nastavení session
- `tickview/ui/reminders_dialog.py` – dialog Časové připomínky
- `tickview/ui/notify_fields.py` – pole notifikací (Telegram, oznámení,
  zvuk, webhook) pro časové připomínky
- `tickview/ui/main_window.py` – hlavní stránka: mřížka grafů a Layout
  setup, aktivní graf, hledání symbolu (dialog), pravý panel (watchlist,
  alerty – seznam, log, hledání, hromadné akce), dialog alertu, Telegram
  v Settings), timeframy, watchlist, nabídka, šablony a Settings
  indikátorů (Inputs, Style, Visibility), lišta kreslení, Settings kreseb
  (Text, Visibility) a grafu,
  stavová lišta se seancí a časovým pásmem
- `tickview/ui/static/lightweight_chart.js` – JS komponenta grafu (svíčky,
  objem, nadpis a legenda ve stylu TradingView, motivy, časové pásmo,
  inkrementální update, donačítání historie, panely indikátorů, úsečky
  a značky indikátorů (overlay sleduje i změnu měřítka cenové osy), tabulka
  scanneru, kontextová
  menu, kreslicí nástroje na překryvném canvasu včetně Long / Short position,
  replay (výběr startu, čáry a štítky obchodů a příkazů s tažením SL / TP),
  čáry alertů a zvuk alertu, plovoucí panel oblíbených
  nástrojů, styl svíček; více grafů na stránce – aktivní graf, společná
  lišta a klávesnice, synchronizace crosshairu a času)
- `tickview/ui/static/color_picker.js` – výběr barvy ve stylu TradingView
  (paleta, vlastní barvy, průhlednost, u výstupu indikátoru i tloušťka
  a styl čáry)
- `tickview/ui/static/dialog_tabs.js` – stálá velikost dialogů Settings
  při přepínání záložek
- `tickview/ui/static/resizers.js` – posuvné hrany panelů (šířka pravého
  panelu, výška panelu obchodů replaye) s uložením velikosti v prohlížeči
- `tickview/ui/static/style.css` – styly UI
- `tickview/ui/static/lightweight-charts.standalone.production.js` – vendored
  knihovna TradingView Lightweight Charts 4.2.3 (licence Apache-2.0)
- `tests/` – unit testy logiky (cache, indikátory, polling, omezovač dotazů,
  konfigurace, skládání timeframů, seance, časová pásma, forex, barvy,
  šablony a oblíbené nástroje; alerty, engine alertů, Telegram/webhook přes
  falešný HTTP transport a pozice Long/Short; rozložení grafů, viditelnost
  kreseb, styl textu, šablony indikátorů a kresby napříč timeframy;
  rozhraní a loader indikátorů, scanner nad testovacím indikátorem
  a časové připomínky; engine replaye – plnění, náklady, převod měn, krok
  zpět, statistiky); spuštění `python -m unittest` (spustí i testy
  vlastních indikátorů v `custom/<název>/tests/`)

## Licence

Copyright 2026 Petr Svoboda

Projekt je licencovaný pod [Apache License 2.0](LICENSE). Vendored knihovna
TradingView Lightweight Charts (`tickview/ui/static/lightweight-charts.standalone.production.js`)
má vlastní licenci Apache-2.0 (Copyright TradingView, Inc.), uvedenou v hlavičce souboru.
