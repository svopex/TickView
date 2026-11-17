# Svopex TTW

Vlastní indikátor TickView (`id = "svopex_ttw"`, kategorie Vlastní). Obecná
pravidla vlastních indikátorů (rozhraní, načítání, testy) popisuje hlavní
`README.md` projektu.

## Co dělá

- Převod výpočetního jádra Pine skriptu (`pine/svopex_ttw_v9.pine`, původně
  MQL5 SvpTomTheWizard) 1:1 včetně zvláštností z MQL – formace ONE / ENG /
  STAR, swingy, trendlinky a šablony, double bottom / top, vynechání
  půlnočních svíček.
- Kreslí úsečky do grafu (trendové linky, šablony; v záložce Style je
  přepínač Lines) a vrací signály LONG / SHORT s indexem svíčky a typem
  formace jako štítkem signálu. Čáry nevrací, takže Style nemá výstupy ani
  Output values.
- Potřebuje aspoň tolik svíček, kolik je „Počet svíček, které se
  zpracovávají“ (výchozí 360), jinak nic nevykreslí (jako v TradingView).
- Hodina půlnoční svíčky se počítá v nastaveném pásmu (výchozí
  Europe/Prague; v Pine je to pásmo burzy symbolu – pro shodu s TradingView
  je potřeba zvolit pásmo, ve kterém má rolloverová svíčka nastavenou
  hodinu).
- U forexu na 1h se minimální a maximální velikost svíčky počítá z ticku
  (forex z Yahoo 0.00001, u JPY párů 0.001; ostatní zdroje odhad z dat).

## Scanner, alerty a replay

Indikátor připojuje obecné vstupy scanneru (`scanner_params` z rozhraní
indikátorů), takže funguje se scannerem, alertem na signál i automatickou
pauzou replaye při signálu. Proti obecným výchozím hodnotám:

- alert scanneru před zavřením svíčky je ve výchozím stavu zapnutý,
- pole symbolů jsou předvyplněná symboly z Pine skriptu (forex, kovy,
  indexová CFD a ropa z Interactive Brokers, bitcoin z Binance); seznam je
  v `DEFAULT_SCAN_SYMBOLS` v `indicator.py`.

Tabulka scanneru má hlavičku „Svopex TTW (1h) | Signál (svíčka)“, zástupný
text `{{signal}}` alertu na signál dává např. „LONG ENG“.

Noční upozornění z Pine skriptu (uzavření pozic před noční seancí) jsou
v TickView obecné Časové připomínky, nezávislé na indikátoru.

## Soubory

- `indicator.py` – indikátor (třída `SvopexTTW`, proměnná `INDICATOR`)
- `pine/` – verze původního Pine skriptu (TickView je nenačítá)
- `tests/` – testy indikátoru, hlavně shoda s doslovným přepisem Pine
  (`ttw_reference.py`)
