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

PROGRESS.md (povinné – práce může být kdykoli přerušena)
Pracuješ s omezeným kreditem a můžeš být přerušen uprostřed kroku. Jiný agent
pak musí navázat jen podle souboru PROGRESS.md v kořeni projektu a kódu,
bez znalosti této konverzace.
- Na začátku: pokud PROGRESS.md existuje, přečti ho celý, ověř, že aplikace
  jde spustit, a pokračuj od sekce „Další krok“. Nezačínej znovu a nepřepisuj
  hotové části. Pokud neexistuje, vytvoř ho jako první věc, ještě před kódem.
- Aktualizuj ho průběžně: po každé dokončené dílčí části (ne až na konci
  kroku) a vždy před započetím větší změny. Nejdřív zapiš, pak pokračuj.
- Udržuj ho krátký a aktuální: přepisuj stav, nepřidávej deník.
- Povinná struktura:
  ## Zadání – úplná kopie tohoto zadání (zapiš jednou, neměň)
  ## Stav kroků – seznam kroků 1–7 s označením hotovo / rozpracováno / čeká
  ## Rozpracováno – co přesně se právě dělá, které soubory jsou dotčené,
     co je napůl hotové a v jakém stavu
  ## Další krok – jedna konkrétní akce, kterou má udělat ten, kdo navazuje
  ## Jak spustit – příkazy pro instalaci a spuštění, ověřené
  ## Struktura projektu – soubory a k čemu slouží (jedna řádka na soubor)
  ## Rozhodnutí – technická rozhodnutí a důvod (verze knihoven, formát dat,
     schéma databáze, odchylky od zadání)
  ## Známé problémy – chyby, obcházení, co nefunguje
- Po každém dokončeném kroku udělej git commit se zprávou „Krok N: popis“
  a zapiš to do PROGRESS.md.
- Nikdy nenechávej projekt v nespustitelném stavu bez poznámky v sekci
  „Rozpracováno“, co je rozbité a jak to dokončit.
- Šetři kredit: nevypisuj celé soubory, pokud měníš jen část; neprováděj
  refaktoring hotových částí, pokud to zadání nevyžaduje.

VÝSTUP
- Kompletní zdrojový kód se strukturou složek, requirements.txt, README
  s postupem spuštění na všech třech systémech, a aktuální PROGRESS.md.

## Instalace a spuštění

Vyžadován Python 3.11+.

Všechny systémy (Windows, macOS, Linux):

    python3 -m venv .venv
    # Windows: .venv\Scripts\activate
    # macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt
    python main.py

- Aplikace běží v prohlížeči na http://localhost:8080 (adresa se vypíše do konzole).
- Jiný port: `python main.py --port 8090`.
- Automatické otevření prohlížeče: `python main.py --open`.
- Nativní okno (ui.run(native=True)): `pip install pywebview` a `python main.py --native`.

### Data a nastavení

- Cache svíček a nastavení: `data/tickview.db` (SQLite, vytvoří se samo;
  složka `data/` není verzována).
- API klíče (volitelné, později pro Alpaca): `data/config.local.json` –
  nikdy se necommituje.

### Stav funkcí

- Hotovo: vyhledání symbolů v jednom poli s autocomplete (Enter přidá do
  watchlistu, klik na návrh načte graf), statické svíčkové grafy
  + objem, timeframy 1m/5m/15m/1h/4h/1D/1W (jen co zdroj podporuje), tmavý a
  světlý motiv, crosshair s OHLC legendou, cache historie v SQLite,
  ukládání posledního symbolu/timeframu/motivu, živé svíčky Binance přes
  websocket (inkrementální update grafu, automatické znovupřipojení, štítek
  „živě / zpožděno“), akcie/ETF/indexy/forex přes Yahoo Finance s živým
  pollingem a hlášením zavřeného trhu, indikátory SMA, EMA, Bollinger Bands,
  RSI a MACD (RSI/MACD ve vlastních panelech se synchronizovaným časem,
  nastavitelné parametry, mazání čipem), kreslení s lištou nástrojů ve stylu
  TradingView: horizontální i svislá linie, trendová čára, polopřímka,
  oboustranná přímka, obdélník, štětec, Fibonacci retracement (s popisky),
  paleta barev nových kreseb, mazání kresby pravým klikem a smazání všeho;
  kresby se ukládají k symbolu a timeframe (přetrvávají restart), watchlist
  v levém panelu (přidat/odebrat, klik přepne graf, poslední cena a denní
  změna – Binance živě po 5 s, Yahoo denní změna a Alpaca snapshots s obnovou
  po 60/5 s).
- Připravuje se: kreslení ve stylu TradingView (vertikální lišta nástrojů),
  poté volitelné vylepšení podle zpětné vazby.

### Alpaca (volitelný zdroj živých akcií)

- Zdroj se aktivuje zadáním API klíčů (plán Basic zdarma stačí – používá se
  IEX feed). Dvě cesty:
  1. V aplikaci: ozubené kolečko v horní liště → vložit Key a Secret → Uložit
     → restart aplikace.
  2. Přímo: doplnit `data/config.local.json` a restartovat:

         {
           "alpaca": {
             "endpoint": "https://paper-api.alpaca.markets/v2",
             "api_key": "PK...",
             "secret": "..."
           }
         }

- S neplatnými klíči se zdroj chová korektně: chybu 401 zobrazí ve stavové
  liště a zůstanou funkční ostatní zdroje.

## Struktura projektu

- `main.py` – vstupní bod: CLI parametry (`--port`, `--native`, `--open`),
  registrace statických souborů, spuštění UI
- `tickview/config.py` – cesty k runtime datům, cache DB a lokální konfiguraci
  (načítání/ukládání API klíčů)
- `tickview/providers/base.py` – rozhraní DataProvider (search/history/stream),
  Candle, SymbolInfo, kanonické timeframy
- `tickview/providers/binance.py` – zdroj Binance: vyhledání symbolů + historie
  přes veřejný REST, živý websocket stream, omezovač dotazů, exponenciální
  backoff po 429
- `tickview/providers/yahoo.py` – zdroj Yahoo Finance (yfinance): akcie, ETF,
  indexy, forex; historie v to_thread, živá aktualizace pollingem
- `tickview/providers/alpaca.py` – volitelný zdroj Alpaca (americké akcie):
  historie feed=iex, živý websocket IEX, aktivace po zadání klíčů
- `tickview/providers/__init__.py` – registr zdrojů (nový zdroj = jeden řádek)
- `tickview/logic/cache.py` – SQLite cache svíček, nastavení, kreseb a
  watchlistu; skládání historie z cache a sítě (ze sítě stahuje jen chybějící
  úseky)
- `tickview/logic/indicators.py` – výpočet SMA/EMA/BB/RSI/MACD přes pandas-ta,
  payload overlay čar a panelů pro graf
- `tickview/ui/lightweight_chart.py` – vlastní komponenta NiceGUI obalující
  Lightweight Charts (init, výměna dat, inkrementální svíčka, motiv)
- `tickview/ui/main_window.py` – hlavní stránka: vyhledání symbolu, timeframy,
  motiv, stavová lišta
- `tickview/ui/static/lightweight_chart.js` – JS komponenta grafu (svíčky,
  objem, OHLC legenda, motivy, inkrementální update)
- `tickview/ui/static/style.css` – styly UI
- `tickview/ui/static/lightweight-charts.standalone.production.js` – vendored
  knihovna TradingView Lightweight Charts 4.2.3 (licence Apache-2.0)
- `PROGRESS.md` – stav práce pro navázání bez znalosti konverzace