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
  + objem, timeframy 1m/5m/15m/1h/4h/1D/1W (jen co zdroj podporuje), tmavý a
  světlý motiv, crosshair s OHLC legendou, cache historie v SQLite,
  donačítání starší historie při posunu grafu doleva (prázdné okno – noc,
  víkend – se postupně zvětšuje, pak se historie bere jako vyčerpaná),
  ukládání posledního symbolu/timeframu/motivu, živé svíčky Binance přes
  websocket (inkrementální update grafu, automatické znovupřipojení, štítek
  „živě / zpožděno“; výpadek a obnovení spojení se hlásí ve stavové liště a
  chybějící svíčky po výpadku se doplní z historie), akcie/ETF/indexy/forex
  přes Yahoo Finance s živým pollingem a hlášením zavřeného trhu, indikátory
  SMA, EMA, Bollinger Bands, RSI a MACD (RSI/MACD ve vlastních panelech se
  synchronizovaným časem, nastavitelné a kontrolované parametry, mazání
  čipem, průběžný přepočet z živých svíček nejvýš jednou za 2 s, aktivní
  indikátory se ukládají a přetrvávají restart), kreslení s lištou nástrojů 1:1 ve
  stylu TradingView: skupiny nástrojů s flyout nabídkami — trendová čára,
  polopřímka, přímka, trendová šipka, horizontální/svislá linie, horizontální
  polopřímka, kříž, Fibonacci retracement i extension (s popisky), paralelní
  kanál, pitchfork, obdélník, štětec, text, sticker (emoji), měřič; výběr
  kresby klikem s úchopy a tažením (celá kresba i jednotlivé body; výběr
  zůstává i po uložení), kresby lze umístit i mimo existující svíčky
  (vpravo do budoucna, přes víkendové mezery), mazání
  přes pravoklikové menu („Smazat objekt“), Del nebo koš, magnet na OHLC,
  tužka (nástroj zůstává aktivní), zámek a skrytí kreseb; text i sticker se
  zadávají v dialogu po kliknutí do grafu; paleta barev nových kreseb; kresby
  se ukládají k symbolu a timeframe (přetrvávají restart). Watchlist v levém
  panelu (přidat/odebrat, klik přepne graf a řádek se zvýrazní jako aktuální,
  poslední cena a denní změna – Binance živě po 5 s, Yahoo po 60 s, IBKR po
  30 s, Alpaca snapshots po 5 s).
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

- Živá data: timeframe 1m přes websocket IEX (minutové bary), delší
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

- Historie, vyhledávání (matching symbols) i ceny watchlistu jdou přes TWS;
  živá data se dotazují v intervalu (15 s intraday, 60 s denní), proto má
  zdroj štítek „zpožděno“. Když TWS neběží, zdroj to nahlásí a ostatní zdroje
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
  tickers/close), Candle, StreamStatus (výpadek/obnovení streamu),
  SymbolInfo, kanonické timeframy, omezovač dotazů a společná smyčka pollingu
  živých dat
- `tickview/providers/binance.py` – zdroj Binance: vyhledání symbolů + historie
  přes veřejný REST, živý websocket stream, omezovač dotazů, exponenciální
  backoff po 429
- `tickview/providers/yahoo.py` – zdroj Yahoo Finance (yfinance): akcie, ETF,
  indexy, forex; historie v to_thread, živá aktualizace pollingem
- `tickview/providers/ibkr.py` – zdroj Interactive Brokers (TWS API, ib_async):
  americké akcie; napojení na TWS 127.0.0.1:7497, clientId 30 (při obsazení
  další), polling živých dat
- `tickview/providers/alpaca.py` – volitelný zdroj Alpaca (americké akcie):
  historie feed=iex, sdílený živý websocket IEX (1m) / polling (delší
  timeframy), aktivace po zadání klíčů
- `tickview/providers/__init__.py` – registr zdrojů (nový zdroj = jeden řádek)
- `tickview/logic/cache.py` – SQLite cache svíček, nastavení, kreseb,
  watchlistu a evidence stažených intervalů; skládání historie z cache a sítě
  (ze sítě stahuje jen chybějící úseky)
- `tickview/logic/indicators.py` – výpočet SMA/EMA/BB/RSI/MACD přes pandas-ta,
  payload overlay čar a panelů pro graf, kontrola parametrů a načtení
  uložených indikátorů
- `tickview/ui/lightweight_chart.py` – vlastní komponenta NiceGUI obalující
  Lightweight Charts (init, výměna dat, inkrementální svíčka, motiv,
  indikátory, kreslení, fronta událostí z grafu)
- `tickview/ui/main_window.py` – hlavní stránka: vyhledání symbolu, timeframy,
  watchlist, indikátory, lišta kreslení, živý stream, dialogy, stavová lišta
- `tickview/ui/static/lightweight_chart.js` – JS komponenta grafu (svíčky,
  objem, OHLC legenda, motivy, inkrementální update, donačítání historie,
  panely indikátorů, kreslicí nástroje na překryvném canvasu)
- `tickview/ui/static/style.css` – styly UI
- `tickview/ui/static/lightweight-charts.standalone.production.js` – vendored
  knihovna TradingView Lightweight Charts 4.2.3 (licence Apache-2.0)
- `tests/` – unit testy logiky (cache, indikátory, polling, omezovač dotazů,
  konfigurace); spuštění `python -m unittest`