# TickView – PROGRESS.md

## Zadání – úplná kopie tohoto zadání (zapiš jednou, neměň)

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
- Kompletní zdrojový kód se strukturou složek, requirements.txt, README
  s postupem spuštění na všech třech systémech, a aktuální PROGRESS.md.

## Stav kroků

1. Kostra projektu + Binance + statický graf – hotovo (commit „Krok 1“)
2. Živá data (websocket) – hotovo (commit „Krok 2“)
3. yfinance – hotovo (commit „Krok 3“)
4. Indikátory – hotovo (commit „Krok 4“)
5. Kreslení – hotovo (commit „Krok 5“)
6. Watchlist – hotovo (commit „Krok 6“)
7. Alpaca – hotovo (implementace, commit „Krok 7“); živá data čekají na platné API klíče

## Rozpracováno

- Nic – krok 7 implementován; blokuje jen neplatné API klíče Alpaca (viz Známé problémy).

## Další krok

- Ověřit živá data Alpaca při otevřeném trhu (AAPL v intraday timeframe, IEX
  websocket) – kód hotový, víkend bez obchodování neumožnil potvrdit toky baru.
- Volitelně doladit kreslení podle zpětné vazby (barvy kreseb, mazání po
  jedné, popisky) – lišta ve stylu TradingView je nasazená.

## Jak spustit

- Ověřeno na macOS (Homebrew Python 3.13.7); na Windows/Linux stačí python3 3.11+:

      python3 -m venv .venv
      source .venv/bin/activate      # Windows: .venv\Scripts\activate
      pip install -r requirements.txt
      python main.py

- Aplikace v prohlížeči: http://localhost:8080 (jiný port: --port N).
- Nativní okno: pip install pywebview a python main.py --native.
- Automatické otevření prohlížeče: python main.py --open.

## Struktura projektu

- CLAUDE.md – instrukce projektu (aktualizace README, commit messages)
- README.md – zadání + dokumentace (instalace, spuštění, struktura)
- PROGRESS.md – tento soubor (stav práce pro navázání bez konverzace)
- requirements.txt – Python závislosti
- main.py – vstupní bod: CLI parametry, registrace statických souborů, ui.run
- tickview/config.py – cesty k datům, cache DB, lokální konfiguraci (API klíče)
- tickview/providers/__init__.py – registr dostupných datových zdrojů
- tickview/providers/base.py – rozhraní DataProvider, Candle, SymbolInfo, timeframy
- tickview/providers/binance.py – zdroj Binance: vyhledání + historie (REST), rate limiter, backoff 429
- tickview/logic/cache.py – SQLite cache svíček + nastavení; skládání historie z cache a sítě
- tickview/ui/lightweight_chart.py – vlastní komponenta NiceGUI obalující Lightweight Charts
- tickview/ui/main_window.py – hlavní stránka: vyhledání, timeframy, motiv, stavová lišta
- tickview/ui/static/style.css – styly UI
- tickview/ui/static/lightweight_chart.js – JS komponenta grafu (init, data, legenda, motiv)
- tickview/ui/static/lightweight-charts.standalone.production.js – vendored knihovna TradingView Lightweight Charts

## Rozhodnutí

- Python pro vývoj a testy na tomto stroji: /opt/homebrew/bin/python3.13
  (systémový /usr/bin/python3 je 3.9.6, zadání vyžaduje 3.11+).
- TradingView Lightweight Charts: vendored verze 4.2.3 do
  tickview/ui/static/ (v5 mění API; v4 API je stabilní a známé). Licence
  Apache-2.0, hlavička souboru zůstává zachována.
- pandas-ta: verzí 0.4.71b0 (na PyPI jako aktuální) import bez problémů i s
  numpy 2.x – zadání zůstává bez odchylky, indikátory budou přes pandas-ta.
- Knihovny nainstalované v testovacím venv: nicegui 4.0.0, pandas 3.1.1,
  numpy 2.5.4, pandas-ta 0.4.71b0; requirements.txt piny: nicegui>=4,<5,
  pandas>=3,<4, pandas-ta>=0.4.71b0.
- Statické soubory (/static) posílá prohlížeči cache-busting přes verzi
  STATIC_VERSION v URL (tickview/ui/main_window.py); při změně CSS/JS zvýšit.
- Stránka má explicitní pozadí podle motivu (.body--light/.body--dark ve
  style.css včetně color-scheme) – bez toho kreslí prohlížeč za průhlednými
  vrstvami tmavý canvas a světý texty horní lišty nejsou vidět.
- SQLite spojení: check_same_thread=False + asyncio.Lock (operace v to_thread),
  takže nikdy neblokuje UI smyčku.
- Vyhledávání Binance: řazení – nejdřív prefix, páry na USDT před ostatními.
- Živý stream (krok 2): BinanceProvider.stream() je async generátor nad
  websockets s exponenciálním znovupřipojováním (1→30 s); UI drží asyncio.Task
  na klienta, ruší ho při přepnutí symbolu/timeframu i při odpojení klienta
  (context.client.on_disconnect).
- Uzavřené svíčky do cache se poznají bez příznaku: open_time + step_ms <= now;
  rozepsaná svíčka se do cache neukládá, aby ji příští fetch z cache neposkytl
  jako hotovou historii.
- UI operace volané z background tasků (stream) musí vstupovat do slotu prvku
  („with element:“) – jinak NiceGUI vyhodí chybu slot stacku a zpráva se
  nepošle do prohlížeče.
- Yahoo Finance (krok 3): vyhledávání přes yf.Search (raw HTTP API na search
  vrací 429, yfinance řeší cookies/impersonaci sám); historie přes yf.download
  v asyncio.to_thread; živá aktualizace pollingem (15 s intraday, 60 s denní).
- Yahoo data bývají zpožděná → provider má is_live=False a štítek zůstává
  „zpožděno“ i při živém polling streamu; Binance má is_live=True → „živě“.
- Yahoo nemá 4h svíčky → timeframes zdroje obsahují jen podporované (1m, 5m,
  15m, 1h, 1D, 1W); bez 4h.
- Zavřený trh: consume_stream hlásí „trh je pravděpodobně zavřený“, když je
  nejnovější svíčka už uzavřená (open_time + step <= now) – hlásí se jen jednou
  na načtení.
- Indikátory (krok 4): výpočet přes pandas-ta (sma/ema/bbands/rsi/macd);
  sloupce BB/MACD se hledají prefixem, protože názvy obsahují parametry
  (BBL_20_2.0_2.0, MACDh_12_26_9). Payload pro JS: overlays (čáry v hlavním
  grafu) + panes (RSI 110 px, MACD 140 px; úrovně 30/70 resp. 0 jako price
  lines). Panely se synchronizují přes visibleLogicalRangeChange s ochranným
  příznakem syncing; časová osa viditelná jen v hlavním grafu. NaN hodnoty se
  zahazují. Barvy z cyklické palety. UI: dialog s parametry (přestavuje pole
  dle typu), aktivní indikátory jako čipy s odstraněním, přepočet po každém
  načtení dat.
- Kreslení ve stylu TradingView (krok 5 + rozšíření dle zpětné vazby, lišta 1:1):
  vertikální lišta vlevo od grafu se SKUPINAMI a flyout nabídkami (jako v TV):
  kurzor | čáry (trend, polopřímka, přímka, trendová šipka) | horizontální a
  svislé linie (hline, vline, horizontální polopřímka, kříž) | Fibonacci
  (retracement + extension) | paralelní kanál (3 kliky) | pitchfork (3 kliky) |
  tvary (obdélník, štětec) | text + sticker (emoji) | měřič | magnet | tužka
  (nástroj zůstává aktivní) | zámek | oko | koš + paleta 5 barev nových
  kreseb. Převody px↔(čas,cena) přes timeScale.coordinateToTime /
  series.coordinateToPrice; kreslí se jen do plochy grafu (clip vůči pane
  canvas, kliky v pásu os se ignorují). hline/vline/text/sticker = 1 klik,
  kanál a pitchfork = 3 kliky, ostatní 2 kliky, štětec taháním (body každých
  ~6 px), preview sleduje kurzor, Esc ruší. Fibonacci: úrovně
  0/0.236/0.382/0.5/0.618/0.786/1 s popisky ceny; extension
  0/0.618/1/1.618/2.618. Výběr a editace: klik v režimu kurzoru označí kresbu
  (úchopové čtverečky), tažení úchopu mění body, tažení těla posouvá celou
  kresbu (zachytávání mousedown na hostu v capture fázi, aby graf nepanoval);
  mouseup uloží update do DB (cache.update_drawing). Mazání: Del nad
  označenou, pravý klik pod kurzorem otevře KONTEXTOVÉ MENU s „Odebrat
  kresbu“ a „Odebrat všechny kresby“ (klik nad kresbou ji nejprve vybere),
  koš = označená nebo všechny. Magnet:
  přichytí bod k času nejbližší svíčky a ceně k nejbližší OHLC (±12 px). Oko
  skryje/zobrazí kresby. Zámek zakáže editaci i mazání. Měřič: Δcena, % a
  počet barů (jen náhled, neukládá se). Text: klik → dialog v Pythonu →
  addText; editace dvojklikem (text_edit). Sticker: klik → dialog s emoji →
  addSticker. Barva/text se ukládají v DB (sloupce color a text v drawings,
  migrace každý ALTER zvlášť, protože executescript u prvního konfliktu
  přeruší zbytek). Body kresby se ukládají jako JSON do tabulky drawings
  (source+symbol+timeframe). JS→Python kanál: stránkový timer 0,5 s vyzvedává
  frontu událostí (typ tool/action/add/update/delete/text_request/text_edit/
  sticker_request) přes popFinishedDrawing (DOM CustomEvent z Vue komponenty
  do NiceGUI neprojde spolehlivě; fronta zvládne více událostí mezi ticky).
  Kontejner grafu se hledá přes data-chart-id atribut – vlastní id prvku
  necháváme NiceGUI (přepis id rozbil event binding). Definice skupin posílá
  Python přes setToolGroups (JS si je uloží i před init grafu).
- Watchlist (krok 6): tabulka watchlist (source+symbol, unique) v SQLite;
  UI levý panel, přidání tlačítkem + (aktuální symbol) nebo Enterem ze sloučeného
  vyhledávání, odebrání ×, klik na řádek přepne graf. Ceny: Binance
  /ticker/24hr batch dotaz (json pole bez mezer) každých 5 s; Yahoo denní
  svíčky s in-memory TTL 60 s a Alpaca snapshots (latestTrade/prevDailyBar,
  feed=iex) s TTL 5 s; změna se barví zeleně/červeně.
- Paralelní kanál – posun definuje JEN kolmá složka vektoru p1→třetí bod
  (šířka kanálu): dřív se používal celý vektor (p3−p1) a při posunu kurzoru
  podél přímky se druhá linie „natahovala“ 2× rychleji. Render i hitTest
  počítají posun stejnou projekcí na normálu přímky.
- Pravý klik: nad kresbou ji VYBERE a otevře kontextové menu se „Smazat
  objekt“ (žádné okamžité mazání); mimo kresbu jen zruší výběr. Mazání je
  blokované, když jsou kresby uzamčené.
- Počty kliků nástrojů (oprava kanálu/pitchforku): handleDrawingClick bere
  počet kliků z mapy DRAWING_CLICKS (hline/vline/hray/cross/text/sticker = 1,
  kanál a pitchfork = 3, štětec = tahání + klik, ostatní = 2). Dříve se
  VŠE dokončilo po 2. kliku, takže kanál/pitchfork ukládaly jen 2 body a
  kreslily se jako obyčejná přímka („nefunguje“). Staré 2bodové kanály se
  vykreslí jako přímka (data nelze doplnit).
- Alpaca (krok 7): market data přes https://data.alpaca.markets/v2/stocks/bars
  s feed=iex (zdarma v plánu Basic – IEX pokrývá část objemu trhu, nikoli celý
  SIP feed); seznam instrumentů z nakonfigurovaného trading endpointu (paper
  účet → https://paper-api.alpaca.markets/v2/assets – live host api.alpaca.markets
  papírové klíče odmítá); autentizace hlavičkami APCA-API-KEY-ID a
  APCA-API-SECRET-KEY; živá data wss://stream.data.alpaca.markets/v2/iex
  (auth + subscribe bars, automatické znovupřipojení). Provider se do seznamu
  přidá jen když má klíče (configured). Klíče se zadávají v UI přes ozubené
  kolečko → ukládají se do data/config.local.json; aktivace až po restartu.
  Historie se stránkuje přes next_page_token.
- Alpaca (krok 7): uživatel zadal endpoint https://paper-api.alpaca.markets/v2
  a api_key PKPCQ5NKWKFVLY5N7EKU3WNK4N, uloženo v data/config.local.json
  (neverzováno); chybí secret key – po jeho zadání zdroj povolíme. Pro tržní
  data se použije https://data.alpaca.markets/v2 + websocket IEX (paper API
  endpoint slouží jen pro obchodování, aplikace je read-only).
- NiceGUI: handlery on_value_change volané s event objektem – pokud handler má
  poziciální parametr, dostane event; bool hodnoty bereme přes lambda
  (event.value), jinak selže serializace zprávy do prohlížeče.
- Časy: v DB a poskytovatelích v UTC [ms]; do grafu se předávají sekundy UTC,
  Lightweight Charts je zobrazuje v místním čase.
- SQLite schéma: tabulky klines (PK source+symbol+timeframe+open_time) a
  settings (key/value). Blokující sqlite3 operace běží přes asyncio.to_thread.
- Rate limiter Binance: minimální mezera 150 ms mezi REST dotazy; po 429
  exponenciální backoff 1→60 s s respektováním hlavičky Retry-After.
- UI texty a komentáře v češtině, identifikátory v angličtině.

## Známé problémy

- Alpaca: vyřešeno – API vyžaduje hlavičku APCA-API-SECRET-KEY (s „-KEY“);
  s hlavičkou APCA-API-SECRET dle novějších docs vrací 401 unauthorized.
  Ověřeno: účet PA3ZYD2CZ (ACTIVE), historie AAPL 1D/4h z IEX feedu funguje.
  Živý websocket půjde ověřit až při otevřeném trhu (víkend = bez baru).
- Testováno headless prohlížečem v sandboxu (Chrome panel): načtení dat,
  přepínání timeframe/symbolu, motivy, nastavení i interakce fungují;
  nativní režim (--native) nebyl ověřen (vyžaduje okenní systém).
- Quasar QSelect nabídku výsledků nelze v headless testu spolehlivě otevřít
  (skrytý vnitřní input s opacity 0) – klik funguje v běžném prohlížeči
  (ověřeno v kroku 1); samotný tok vyhledání a výběru je ověřen.
- Horní lišta se na úzkém okně zalamuje na dva řádky (flex-wrap) – úmyslné,
  aby byly všechny prvky dostupné bez horizontálního skrollování.
