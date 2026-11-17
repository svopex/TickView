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

- Hotovo: vyhledání symbolů v jednom poli s autocomplete (návrhy napříč
  zdroji, max 12 se řazením přesná shoda > prefix > výskyt; klik na návrh
  přidá do watchlistu, jen když tam ještě není, a načte graf; Enter přidá
  bez přepnutí grafu; šipky/Esc ovládají nabídku; do watchlistu lze přidávat
  i tlačítkem +), svíčkové grafy
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
  svíček nejvýš jednou za 2 s, aktivní indikátory se ukládají a přetrvávají
  restart), kreslení s lištou nástrojů 1:1 ve
  stylu TradingView: skupiny nástrojů s flyout nabídkami — trendová čára,
  polopřímka, přímka, trendová šipka, horizontální/svislá linie, horizontální
  polopřímka, kříž, Fibonacci retracement i extension (s popisky), paralelní
  kanál, pitchfork, obdélník, štětec, text, sticker (emoji), měřič; výběr
  kresby klikem s úchopy a tažením (celá kresba i jednotlivé body; výběr
  zůstává i po uložení), kresby lze umístit i mimo existující svíčky
  (vpravo do budoucna, přes víkendové mezery), mazání
  přes pravoklikové menu („Smazat objekt“), Del nebo koš, magnet na OHLC,
  tužka (nástroj zůstává aktivní), zámek a skrytí kreseb; text i sticker se
  zadávají v dialogu po kliknutí do grafu; tlačítko barvy nových kreseb
  (výběr barvy ve stylu TradingView, volba se ukládá); kresby
  se ukládají k symbolu a timeframe (přetrvávají restart). Watchlist v levém
  panelu (přidat/odebrat, klik přepne graf a řádek se zvýrazní jako aktuální,
  poslední cena a denní změna – Binance živě po 5 s, Yahoo po 60 s, IBKR po
  30 s, Alpaca snapshots po 5 s).
- Nadpis grafu ve stylu TradingView: „Název · timeframe · burza“ (název firmy
  / páru a burzu dodá zdroj), barevná tečka stavu dat (zelená živě, oranžová
  zpožděno), O/H/L/C obarvené podle směru svíčky, změna proti předchozímu
  závěru (absolutně i v %), řádek Vol (skrytý u instrumentů bez objemu –
  forex, indexy) a pod ním řádky indikátorů s hodnotami pod kurzorem. Přesnost
  cen (osa i legenda) se určuje z dat (forex 5 míst, akcie 2).
- Indikátory: tlačítko „Indikátory“ otevře nabídku jako v TradingView –
  hledání, kategorie (klouzavé průměry, pásma a kanály, trend, oscilátory,
  volatilita, objem, aktivní) a seznam s popisem; klik indikátor přidá
  s výchozími parametry. K dispozici SMA, EMA, WMA, HMA, VWMA, VWAP, Bollinger
  Bands, Keltner, Donchian, Ichimoku, Parabolic SAR, Supertrend, ADX, RSI,
  MACD, Stochastic, Stoch RSI, CCI, Williams %R, Momentum, ROC, Awesome
  Oscillator, TRIX, TSI, ATR, OBV, MFI a CMF. Řádek indikátoru v legendě (i
  v titulku panelu) má při najetí myší oko (skrýt/zobrazit), ozubené kolečko
  (Settings – parametry a barva) a křížek (odebrat).
- Pravý klik a dvojklik (jako v TradingView): na kresbě menu „Settings…“
  (barva, šířka a styl čáry, u textu obsah a velikost písma) a „Smazat
  objekt“, dvojklik rovnou Settings; na čáře indikátoru, v jeho panelu nebo na
  řádku legendy menu „Settings…“ (parametry), „Skrýt“, „Odebrat indikátor“,
  dvojklik rovnou Settings; jinde v grafu „Settings…“ grafu: barvy těla,
  okrajů a knotů svíček pro růst / pokles (každý prvek lze vypnout),
  obarvení svíček podle předchozího závěru a zobrazení objemu (Volume; objem
  lze skrýt i okem v řádku Vol). Změny v Settings grafu se hned ukazují
  v grafu, uloží se tlačítkem Ok, Cancel / zavření je vrátí.
- Výběr barvy jako v TradingView (Settings grafu, indikátoru, kresby a barva
  nových kreseb v liště): řada šedých, hlavní barvy, šest řad odstínů,
  vlastní barvy (tlačítko + otevře systémový výběr; ukládají se v prohlížeči)
  a průhlednost posuvníkem nebo v % – průhledná barva se ukládá jako
  `rgba(…)`, plná jako `#rrggbb`.
- Šablony (Template) jako v TradingView v dialozích Settings grafu,
  indikátoru (pro typ indikátoru) a kresby (pro typ kresby): „Apply defaults“
  (výchozí hodnoty), „Save as…“ (pojmenovaná šablona; stejný název přepíše),
  pod čarou uložené šablony – klik je použije, křížek smaže. U indikátorů
  navíc „Save as default“ – nově přidaný indikátor daného typu převezme
  parametry a barvu ze šablony (a „Apply defaults“ ji použije); „Reset
  default“ vrátí tovární hodnoty. Šablony se ukládají do SQLite.
- Oblíbené nástroje: ve flyout nabídce nástrojů je u každého nástroje
  hvězdička, u skupin s jediným nástrojem (kurzor, měřič) pravý klik na
  tlačítko lišty. Oblíbené nástroje se ukazují v plovoucím panelu nad grafem
  (výchozí pozice nahoře uprostřed), který lze přetáhnout za úchop ⋮⋮;
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
- Do cache se ukládají jen uzavřené svíčky; rozpracovaná poslední svíčka se
  při každém načtení stahuje znovu. Cache si pamatuje skutečně stažené
  intervaly, takže se doplní i díry uprostřed uložených dat.
- Při ukončení aplikace se zavřou síťová spojení zdrojů i databáze.

### Alpaca (volitelný zdroj živých akcií)

- Zdroj se aktivuje zadáním API klíčů (plán Basic zdarma stačí – používá se
  IEX feed); bez klíčů se do aplikace vůbec nepřidá. Dvě cesty:
  1. V aplikaci: ozubené kolečko v horní liště → vložit Key a Secret → Uložit
     → restart aplikace.
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
  clientId `30` – lze přepsat v `config.yaml`:

      ibkr:
        host: 127.0.0.1
        port: 7497
        client_id: 30

- Forex IDEALPRO: dotaz typu `EURUSD`, `EUR/USD` nebo jen `EUR` nabídne
  páry IBKR (symbol `EUR.USD`, data MIDPOINT, bez objemu) – návrhy fungují
  i bez běžící TWS, data se pak stahují z TWS.
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

- `main.py` – vstupní bod: CLI parametry (`--port`, `--native`, `--open`),
  registrace statických souborů, spuštění UI
- `run.sh` – zkrácené spuštění na macOS/Linux: příprava `.venv`, instalace
  závislostí při změně `requirements.txt` a předání parametrů do `main.py`
- `tickview/config.py` – cesty k runtime datům, cache DB, načítání
  konfigurace `config.yaml` a zápis sekce se zachováním komentářů
- `tickview/providers/base.py` – rozhraní DataProvider (search/history/stream/
  tickers/describe/close, pásmo burzy, hlavní seance), Candle, StreamStatus
  (výpadek/obnovení streamu, případně živá/zpožděná data), SymbolInfo, kanonické timeframy, skládání
  2m/10m z kratších svíček (historie i živě), omezovač dotazů a společná
  smyčka pollingu živých dat
- `tickview/providers/binance.py` – zdroj Binance: vyhledání symbolů + historie
  přes veřejný REST, živý websocket stream, omezovač dotazů, exponenciální
  backoff po 429
- `tickview/providers/yahoo.py` – zdroj Yahoo Finance (yfinance): akcie, ETF,
  indexy, forex; historie v to_thread, živá aktualizace pollingem
- `tickview/providers/ibkr.py` – zdroj Interactive Brokers (TWS API, ib_async):
  americké akcie a forex IDEALPRO; napojení na TWS 127.0.0.1:7497, clientId
  30 (při obsazení další), průběžné bary keepUpToDate a zjištění živých /
  zpožděných dat podle předplatného
- `tickview/providers/alpaca.py` – volitelný zdroj Alpaca (americké akcie):
  historie feed=iex, sdílený živý websocket IEX (1m) / polling (delší
  timeframy), aktivace po zadání klíčů
- `tickview/providers/__init__.py` – registr zdrojů (nový zdroj = jeden řádek)
- `tickview/logic/cache.py` – SQLite cache svíček, nastavení, kreseb,
  watchlistu a evidence stažených intervalů; skládání historie z cache a sítě
  (ze sítě stahuje jen chybějící úseky)
- `tickview/logic/indicators.py` – definice (kategorie, popis, parametry)
  a výpočet 28 indikátorů přes pandas-ta, payload overlay čar, panelů
  a skupin legendy pro graf, kontrola parametrů a načtení uložených indikátorů
- `tickview/logic/styles.py` – kontrola barev (`#rrggbb` / `rgba`), styl
  svíček grafu, šablony nastavení (Template) a oblíbené nástroje
- `tickview/logic/sessions.py` – filtr hlavní obchodní seance (RTH) a seznam
  časových pásem pro nabídku
- `tickview/ui/lightweight_chart.py` – vlastní komponenta NiceGUI obalující
  Lightweight Charts (init, výměna dat, inkrementální svíčka, motiv,
  indikátory, kreslení, fronta událostí z grafu)
- `tickview/ui/color_button.py` + `color_button.js` – tlačítko barvy
  (vlastní komponenta NiceGUI), které otevírá výběr barvy
- `tickview/ui/templates.py` – nabídka šablon „Template“ pro dialogy Settings
- `tickview/ui/main_window.py` – hlavní stránka: vyhledání symbolu, timeframy,
  watchlist, nabídka a Settings indikátorů, lišta kreslení, Settings kreseb
  a grafu, živý stream, stavová lišta se seancí a časovým pásmem
- `tickview/ui/static/lightweight_chart.js` – JS komponenta grafu (svíčky,
  objem, nadpis a legenda ve stylu TradingView, motivy, časové pásmo,
  inkrementální update, donačítání historie, panely indikátorů, kontextová
  menu, kreslicí nástroje na překryvném canvasu, plovoucí panel oblíbených
  nástrojů, styl svíček)
- `tickview/ui/static/color_picker.js` – výběr barvy ve stylu TradingView
  (paleta, vlastní barvy, průhlednost)
- `tickview/ui/static/style.css` – styly UI
- `tickview/ui/static/lightweight-charts.standalone.production.js` – vendored
  knihovna TradingView Lightweight Charts 4.2.3 (licence Apache-2.0)
- `tests/` – unit testy logiky (cache, indikátory, polling, omezovač dotazů,
  konfigurace, skládání timeframů, seance, časová pásma, forex, barvy,
  šablony a oblíbené nástroje);
  spuštění `python -m unittest`