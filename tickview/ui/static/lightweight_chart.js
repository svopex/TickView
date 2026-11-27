/* TickView – JS komponenta grafu založená na TradingView Lightweight Charts.
 *
 * Veškerá logika grafu je v tomto souboru; Python ji volá přes
 * window.TickViewChart.<funkce> (žádný inline JS). Kreslení je modelované
 * po TradingView: výběr kresby klikem, tažení za koncové body i za celou
 * kresbu, mazání vybrané kresby (Del / koš), magnet na OHLC, hide, měřič.
 */
window.TickViewChart = (function () {
  'use strict';

  // Barvy grafu podle motivu (odpovídají stylu TradingView)
  const THEMES = {
    dark: {
      layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc' },
      grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
      border: '#2a2e39',
    },
    light: {
      layout: { background: { type: 'solid', color: '#ffffff' }, textColor: '#131722' },
      grid: { vertLines: { color: '#e0e3eb' }, horzLines: { color: '#e0e3eb' } },
      border: '#c9cdd4',
    },
  };

  // Kolečka na čarách zvýrazněného / označeného indikátoru (jako v TradingView):
  // nejmenší rozestup v px, poloměr a barva úchopů kreseb
  const SELECTION_MARKER_SPACING_PX = 60;
  const SELECTION_MARKER_RADIUS = 3.5;
  const HANDLE_COLOR = '#2962ff';

  // Úrovně Fibonacci retracementu
  const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  // Úrovně Fibonacci extension (za první bod včetně 1.618 a 2.618)
  const FIBEXT_LEVELS = [0, 0.618, 1, 1.618, 2.618];

  // Kolik svíček od levého okraje dat stačí k vyžádání starší historie
  const OLDER_THRESHOLD_BARS = 30;
  // Pauza před dalším pokusem, když donačtení starších dat selhalo (ms)
  const OLDER_RETRY_MS = 10000;
  // Jak blízko (px) musí být kurzor u kresby, čáry alertu nebo indikátoru, aby ji zasáhl
  const HIT_TOLERANCE_PX = 6;

  // Typy kurzoru jako v TradingView: režim nástroje z lišty → typ kurzoru;
  // všechny jsou režimem kurzoru (výběr a tažení kreseb), liší se vzhledem,
  // guma klikem kresbu smaže
  const CURSOR_TYPES = {
    cursor: 'cross',
    cursor_dot: 'dot',
    cursor_arrow: 'arrow',
    cursor_demo: 'demo',
    cursor_magic: 'magic',
    eraser: 'eraser',
  };
  // Barva čar crosshairu (výchozí v Lightweight Charts); u jiných typů než
  // kříž jsou čáry průhledné (popisky na osách zůstávají)
  const CROSSHAIR_LINE_COLOR = '#9598A1';
  // Prezentace: poloměr kruhu kolem kurzoru, délka a poloměr vlny po kliku
  const DEMO_RADIUS = 18;
  const DEMO_RIPPLE_MS = 600;
  const DEMO_RIPPLE_RADIUS = 34;
  // Magie (jako v TradingView): po kliku z místa kliku vybuchne dávka emoji
  // do všech stran, zpomalí odporem vzduchu, padají a mizí; životnost
  // částice (ms), gravitace (px/s²), odpor (1/s – dolet ≈ rychlost / odpor),
  // rozsah počáteční rychlosti (px/s), částic na jeden klik a strop počtu
  const MAGIC_EMOJIS = ['🍉', '🍌', '🍄', '🍑', '🍇', '🥝', '🍓', '🍒', '🍎', '💵', '💩'];
  const MAGIC_PARTICLE_MS = 2600;
  const MAGIC_GRAVITY = 260;
  const MAGIC_DRAG = 1.6;
  const MAGIC_SPEED_MIN = 120;
  const MAGIC_SPEED_MAX = 900;
  const MAGIC_PER_CLICK = 70;
  const MAGIC_MAX_PARTICLES = 420;
  const MAGIC_FONT = '18px "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif';

  // Stav jednotlivých instancí grafu (id kontejneru -> stav)
  const state = new Map();

  // Aktivní graf rozložení (více grafů v okně): lišta nástrojů, klávesnice
  // a panel oblíbených patří jemu; přepíná se kliknutím do grafu
  let activeId = null;
  // Synchronizace mezi grafy rozložení (crosshair, čas, časový rozsah)
  let layoutSync = { crosshair: false, time: false, date_range: false };
  // Společné posluchače dokumentu (lišta, klávesnice, hodiny) se registrují jednou
  let globalsBound = false;

  // Definice skupin nástrojů pro flyout nabídky (společné všem grafům)
  let toolGroups = [];
  // Oblíbené nástroje (plovoucí panel společný všem grafům): pořadí, pozice
  // v px vůči oknu, viditelnost a prvek panelu
  const fav = { modes: [], pos: null, visible: true, el: null };
  // SVG piktogramy nástrojů (název -> SVG z icons.py); společné všem grafům
  let toolIcons = {};
  // Druhy kreseb, na které lze nastavit alert (posílá Python z logic/alerts.py)
  let alertLineKinds = [];
  // Konfigurace kreslení z Pythonu (setDrawingConfig): druhy se stylem
  // a s popiskem, šířky a styly čar s popisky, šablony {druh: {název: styl}}
  let drawingConfig = { kinds: [], lineKinds: [], labelKinds: [], widths: [], lineStyles: [], templates: {} };
  // Šablona zvolená u nástroje pro nové objekty {druh: název} (bez položky =
  // výchozí šablona typu)
  const toolTemplate = {};
  // Plovoucí lišta kresby: prvek, klíč a kresba posledního vykreslení
  // (přestaví se jen při změně)
  const drawingBar = { el: null, key: '', drawing: null };

  // Neviditelná barva (vypnuté tělo svíčky)
  const TRANSPARENT = 'rgba(0, 0, 0, 0)';

  // Typy hlavní série (jako v TradingView) → druh série Lightweight Charts
  const CHART_TYPE_SERIES = {
    bars: 'Bar',
    candles: 'Candlestick',
    hollow: 'Candlestick',
    heikin: 'Candlestick',
    highlow: 'Candlestick',
    line: 'Line',
    line_markers: 'Line',
    step: 'Line',
    area: 'Area',
    baseline: 'Baseline',
    columns: 'Histogram',
  };
  // Barva čárových typů (Line, Area) jako v TradingView
  const LINE_COLOR = '#2962ff';

  // Volby svíčkové série podle stylu grafu (tělo, okraje, knoty); úplný
  // styl (CHART_DEFAULTS) posílá Python při init i po změně v Settings
  function candleOptions(style) {
    return {
      upColor: style.body ? style.body_up : TRANSPARENT,
      downColor: style.body ? style.body_down : TRANSPARENT,
      borderVisible: !!style.borders,
      borderUpColor: style.border_up,
      borderDownColor: style.border_down,
      wickVisible: !!style.wick,
      wickUpColor: style.wick_up,
      wickDownColor: style.wick_down,
    };
  }

  // Volby hlavní série podle typu grafu a stylu (barvy růstu / poklesu
  // ze Settings grafu se používají i u sloupců, barů a baseline); přesnost
  // ceny podle instrumentu se přidá ke všem typům
  function mainSeriesOptions(s) {
    const decimals = s.priceDecimals || 2;
    const priceFormat = { type: 'price', precision: decimals, minMove: Math.pow(10, -decimals) };
    return Object.assign(typeSeriesOptions(s), { priceFormat });
  }

  // Polovina cenového rozpětí načtených svíček (základna typu Baseline)
  function baselinePrice(s) {
    let low = Infinity;
    let high = -Infinity;
    for (const candle of s.candles) {
      low = Math.min(low, candle.low);
      high = Math.max(high, candle.high);
    }
    return s.candles.length ? (low + high) / 2 : 0;
  }

  // Volby série specifické pro typ grafu (bez přesnosti ceny)
  function typeSeriesOptions(s) {
    const style = s.chartStyle;
    switch (s.chartType) {
      case 'hollow':
        // Rostoucí svíčka jen obrysem, klesající plná
        return Object.assign(candleOptions(style), { upColor: TRANSPARENT, borderVisible: true });
      case 'highlow':
        // Jen rozpětí high–low (barvu nese každý bod podle směru svíčky)
        return { borderVisible: false, wickVisible: false };
      case 'bars':
        return { upColor: style.body_up, downColor: style.body_down, thinBars: false };
      case 'line':
      case 'line_markers':
      case 'step':
        return {
          color: LINE_COLOR,
          lineWidth: 2,
          lineType: s.chartType === 'step' ? LightweightCharts.LineType.WithSteps : LightweightCharts.LineType.Simple,
          pointMarkersVisible: s.chartType === 'line_markers',
        };
      case 'area':
        return { lineColor: LINE_COLOR, lineWidth: 2, topColor: withAlpha(LINE_COLOR, 0.28), bottomColor: withAlpha(LINE_COLOR, 0.05) };
      case 'baseline':
        // Základna v polovině cenového rozpětí dat; nad ní barva růstu, pod ní poklesu
        return {
          baseValue: { type: 'price', price: baselinePrice(s) },
          topLineColor: style.body_up,
          topFillColor1: withAlpha(style.body_up, 0.28),
          topFillColor2: withAlpha(style.body_up, 0.05),
          bottomLineColor: style.body_down,
          bottomFillColor1: withAlpha(style.body_down, 0.05),
          bottomFillColor2: withAlpha(style.body_down, 0.28),
          lineWidth: 2,
        };
      case 'columns':
        return { color: style.body_up };
      default:
        return candleOptions(style);
    }
  }

  // Potřebuje typ / styl vlastní barvy bodů (pak změna stylu vyžaduje setData)?
  function usesPointColors(s) {
    return s.chartStyle.color_by_prev_close || s.chartType === 'columns' || s.chartType === 'highlow';
  }

  // Bod hlavní série pro svíčku podle typu grafu.
  // Parametry: candle – svíčka, previous – předchozí svíčka, previousPoint –
  // předchozí bod série (Heikin Ashi z něj počítá open)
  function mainPoint(s, candle, previous, previousPoint) {
    const style = s.chartStyle;
    // Směr: podle předchozího závěru (volba Settings), jinak open → close
    const up = style.color_by_prev_close && previous ? candle.close >= previous.close : candle.close >= candle.open;
    switch (s.chartType) {
      case 'line':
      case 'line_markers':
      case 'step':
      case 'area':
      case 'baseline':
        return { time: candle.time, value: candle.close };
      case 'columns':
        // Sloupce jako v TradingView: barva podle předchozího závěru
        return {
          time: candle.time,
          value: candle.close,
          color: !previous || candle.close >= previous.close ? style.body_up : style.body_down,
        };
      case 'highlow': {
        const color = up ? style.body_up : style.body_down;
        return { time: candle.time, open: candle.low, high: candle.high, low: candle.low, close: candle.high, color, borderColor: color };
      }
      case 'heikin': {
        // Heikin Ashi: close = průměr OHLC, open = střed předchozí HA svíčky
        const close = (candle.open + candle.high + candle.low + candle.close) / 4;
        const open = previousPoint ? (previousPoint.open + previousPoint.close) / 2 : (candle.open + candle.close) / 2;
        return { time: candle.time, open, high: Math.max(candle.high, open, close), low: Math.min(candle.low, open, close), close };
      }
      case 'bars':
        return style.color_by_prev_close && previous ? Object.assign({}, candle, { color: up ? style.body_up : style.body_down }) : candle;
      default: {
        // Svíčky (i hollow): vlastní barvy jen při obarvení podle předchozího závěru
        if (!style.color_by_prev_close || !previous) return candle;
        const hollowUp = s.chartType === 'hollow' && up;
        return Object.assign({}, candle, {
          color: style.body && !hollowUp ? (up ? style.body_up : style.body_down) : TRANSPARENT,
          borderColor: up ? style.border_up : style.border_down,
          wickColor: up ? style.wick_up : style.wick_down,
        });
      }
    }
  }

  // Body hlavní série ze s.candles (uloží je do s.mainPoints pro živé doplňování)
  function mainData(s) {
    const points = [];
    s.candles.forEach((candle, index) => {
      points.push(mainPoint(s, candle, s.candles[index - 1], points[index - 1]));
    });
    s.mainPoints = points;
    return points;
  }

  // Vytvoří (nebo po změně typu nahradí) hlavní sérii grafu a naplní ji daty
  function createMainSeries(s) {
    if (s.candleSeries) s.chart.removeSeries(s.candleSeries);
    const kind = CHART_TYPE_SERIES[s.chartType] || 'Candlestick';
    s.candleSeries = s.chart['add' + kind + 'Series'](mainSeriesOptions(s));
    s.candleSeries.setData(mainData(s));
    // Změnu měřítka cenové osy (tažení osy, autoscale při nových datech) graf
    // žádnou událostí neohlásí; prázdné primitivum série ale dostane
    // updateAllViews při každém překreslení grafu – overlay (úsečky indikátorů,
    // kresby) se tak pohybuje společně s grafem
    s.candleSeries.attachPrimitive({ updateAllViews: () => scheduleOverlayRedraw(s) });
    // Čáry alertů i značky indikátorů patřily odstraněné sérii – na nové je vytvoříme znovu
    s.alertPriceLines = [];
    applyAlertLines(s);
    applyIndicatorMarkers(s);
  }

  // Počet desetinných míst cen instrumentu odhadnutý z dat (90. percentil
  // počtu míst u OHLC posledních svíček, 2–8) – forex 5, akcie 2, krypto různě
  function detectPriceDecimals(candles) {
    const counts = [];
    for (const candle of candles.slice(-300)) {
      for (const value of [candle.open, candle.high, candle.low, candle.close]) {
        const text = Number(value).toFixed(8).replace(/0+$/, '');
        const dot = text.indexOf('.');
        counts.push(dot < 0 ? 0 : text.length - dot - 1);
      }
    }
    if (!counts.length) return 2;
    counts.sort((a, b) => a - b);
    const decimals = counts[Math.floor((counts.length - 1) * 0.9)];
    return Math.min(8, Math.max(2, decimals));
  }

  // Formátovače čísel s pevným počtem desetinných míst (cache podle počtu
  // míst – toLocaleString by Intl.NumberFormat stavěl při každém volání)
  const numberFormats = new Map();

  function formatFixed(value, digits) {
    if (!numberFormats.has(digits)) {
      numberFormats.set(digits, new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }));
    }
    return numberFormats.get(digits).format(value);
  }

  // Nejmenší krok ceny instrumentu (tick) podle přesnosti dat: 0.01, 0.00001…
  function priceTick(s) {
    return 1 / Math.pow(10, s.priceDecimals || 2);
  }

  // Zaokrouhlí cenu na přesnost instrumentu (alert z menu, nová pozice)
  function roundPrice(s, price) {
    const factor = Math.pow(10, s.priceDecimals || 2);
    return Math.round(price * factor) / factor;
  }

  // Počet ticků cenového rozdílu (měřič, štítky pozice)
  function priceTicks(s, diff) {
    return Math.round(diff / priceTick(s));
  }

  // Cena instrumentu s pevným počtem desetinných míst (legenda, měřič, Fibonacci)
  function formatInstrumentPrice(s, value) {
    if (!isFinite(value)) return '—';
    return formatFixed(value, s.priceDecimals || 2);
  }

  // Objem zkráceně na K/M/B
  function formatVolume(value) {
    if (!isFinite(value)) return '—';
    if (value >= 1e9) return (value / 1e9).toFixed(2) + ' B';
    if (value >= 1e6) return (value / 1e6).toFixed(2) + ' M';
    if (value >= 1e3) return (value / 1e3).toFixed(2) + ' K';
    // Malý objem: celé kusy bez desetinných míst (akcie, futures), zlomky na 2 místa (krypto)
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }

  // ---------- časové pásmo zobrazení (UTC / burza / místní / město) ----------

  // Pojmenované volby formátování data/času (klíč cache formátovačů)
  const DATE_OPTIONS = {
    crosshair: {
      weekday: 'short', day: 'numeric', month: 'short', year: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    },
    year: { year: 'numeric' },
    month: { month: 'short' },
    day: { day: 'numeric' },
    time: { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' },
    seconds: { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' },
    offset: { timeZoneName: 'shortOffset' },
  };

  // Jazyk názvů dnů a měsíců podle jazyka stránky (window.TV_LANG z js_bootstrap);
  // česky „po 17. lis“, anglicky bez tečky za dnem „Mon 17 Nov“ jako v TradingView
  const DATE_LOCALE = window.TV_LANG === 'en' ? 'en-US' : 'cs-CZ';
  const DAY_SUFFIX = window.TV_LANG === 'en' ? '' : '.';

  // Cache formátovačů Intl podle pásma a pojmenovaných voleb (vytváření je drahé)
  const dateFormatters = new Map();

  // Formátovač data/času pro pásmo; neplatné pásmo → UTC
  function dateFormatter(timeZone, name) {
    const key = timeZone + '|' + name;
    if (!dateFormatters.has(key)) {
      let formatter;
      try {
        formatter = new Intl.DateTimeFormat(DATE_LOCALE, Object.assign({ timeZone: timeZone }, DATE_OPTIONS[name]));
      } catch (err) {
        formatter = new Intl.DateTimeFormat(DATE_LOCALE, Object.assign({ timeZone: 'UTC' }, DATE_OPTIONS[name]));
      }
      dateFormatters.set(key, formatter);
    }
    return dateFormatters.get(key);
  }

  // Části data v pásmu (weekday, day, month, year, hour, minute, second…)
  function dateParts(timeZone, seconds, name) {
    const parts = {};
    for (const part of dateFormatter(timeZone, name).formatToParts(new Date(seconds * 1000))) {
      parts[part.type] = part.value;
    }
    return parts;
  }

  // Popisek crosshairu na časové ose: „po 17. lis '26 04:15“ (denní bez času)
  function formatCrosshairTime(timeZone, time, daily) {
    const p = dateParts(timeZone, time, 'crosshair');
    const date = p.weekday + ' ' + p.day + DAY_SUFFIX + ' ' + p.month + " '" + p.year;
    return daily ? date : date + '  ' + p.hour + ':' + p.minute;
  }

  // Popisky značek časové osy podle typu (rok, měsíc, den, čas)
  function formatTickMark(timeZone, time, type) {
    const TickMarkType = LightweightCharts.TickMarkType || {};
    if (type === TickMarkType.Year) return dateParts(timeZone, time, 'year').year;
    if (type === TickMarkType.Month) return dateParts(timeZone, time, 'month').month;
    if (type === TickMarkType.DayOfMonth) return dateParts(timeZone, time, 'day').day + DAY_SUFFIX;
    const withSeconds = type === TickMarkType.TimeWithSeconds;
    const p = dateParts(timeZone, time, withSeconds ? 'seconds' : 'time');
    return p.hour + ':' + p.minute + (withSeconds ? ':' + p.second : '');
  }

  // Posun pásma ve tvaru „UTC+1“ (jako v TradingView)
  function utcOffsetLabel(timeZone, date) {
    const parts = dateFormatter(timeZone, 'offset').formatToParts(date);
    const name = (parts.find((part) => part.type === 'timeZoneName') || {}).value || 'GMT';
    const offset = name.replace(/^GMT/, '').replace(/^UTC/, '');
    return 'UTC' + offset;
  }

  // Nastaví formátování času grafu podle pásma; denní a týdenní svíčky
  // (půlnoc UTC) se ukazují vždy v UTC, jinak by spadly na předchozí den
  function applyTimezone(s) {
    const daily = s.stepSeconds >= 86400;
    const zone = daily ? 'UTC' : s.timezone;
    s.chart.applyOptions({
      localization: { timeFormatter: (time) => formatCrosshairTime(zone, time, daily) },
      timeScale: { tickMarkFormatter: (time, type) => formatTickMark(zone, time, type) },
    });
    updateClock(s);
  }

  // Hodiny ve stavové liště: aktuální čas ve zvoleném pásmu + posun
  function updateClock(s) {
    if (!s) return;
    const now = new Date();
    const p = dateParts(s.timezone, now.getTime() / 1000, 'seconds');
    const text = p.hour + ':' + p.minute + ':' + p.second + ' ' + utcOffsetLabel(s.timezone, now);
    document.querySelectorAll('.tz-clock-text').forEach((node) => {
      if (node.textContent !== text) node.textContent = text;
    });
  }

  // Zvolí pásmo zobrazení: 'local' (prohlížeč), 'exchange' (burza
  // instrumentu), 'UTC' nebo IANA název města
  function setTimezone(id, setting, exchangeTimezone) {
    const s = state.get(id);
    if (!s) return;
    let zone = setting || 'local';
    if (zone === 'local') zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    else if (zone === 'exchange') zone = exchangeTimezone || 'UTC';
    // Neplatné pásmo nevadí – dateFormatter pro něj použije UTC
    s.timezone = zone;
    applyTimezone(s);
  }

  // Vytvoří DOM prvek se třídou a volitelným textem
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Tlačítko v řádku legendy (ikona Material); klik pošle událost do Pythonu
  function legendButton(s, icon, title, event) {
    const button = el('span', 'material-icons legend-ind-btn', icon);
    button.title = title;
    button.addEventListener('click', (e) => {
      e.stopPropagation();
      s.finishedQueue.push(event);
    });
    return button;
  }

  // Pravý klik a dvojklik na řádek legendy (menu / Settings)
  function bindLegendRow(row, onContextMenu, onDoubleClick) {
    row.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      event.stopPropagation();
      onContextMenu(event);
    });
    row.addEventListener('dblclick', (event) => {
      event.stopPropagation();
      onDoubleClick();
    });
  }

  // Popisky stavu dat v nadpisu (tečka za názvem, jako v TradingView)
  const STATUS_TITLES = {
    live: tvTr('Živá data'),
    delayed: tvTr('Zpožděná data / polling'),
    none: tvTr('Bez živých dat'),
    replay: tvTr('Replay (minulost)'),
  };

  // Sestaví kostru legendy: nadpis tickeru + OHLC + změna, řádek objemu a
  // kontejner řádků indikátorů; hodnoty se pak jen přepisují (renderLegend)
  function buildLegend(s) {
    const legend = s.legend;
    legend.innerHTML = '';
    const els = {};
    const main = el('div', 'legend-row legend-main');
    els.title = el('span', 'legend-title');
    els.status = el('span', 'legend-status none');
    els.status.title = STATUS_TITLES.none;
    main.appendChild(els.title);
    main.appendChild(els.status);
    const ohlc = el('span', 'legend-ohlc');
    for (const key of ['O', 'H', 'L', 'C']) {
      const item = el('span', 'legend-item');
      item.appendChild(el('span', 'legend-key', key));
      els[key] = el('span', 'legend-val');
      item.appendChild(els[key]);
      ohlc.appendChild(item);
    }
    els.change = el('span', 'legend-val legend-change');
    ohlc.appendChild(els.change);
    main.appendChild(ohlc);
    legend.appendChild(main);
    els.indicators = el('div', 'legend-indicators');
    legend.appendChild(els.indicators);
    s.legendEls = els;
    renderLegendTitle(s);
  }

  // Nadpis tickeru: „Název · timeframe · burza“ (title atribut = symbol a zdroj)
  function renderLegendTitle(s) {
    const info = s.symbolInfo;
    if (!s.legendEls) return;
    if (!info) {
      s.legendEls.title.textContent = '';
      return;
    }
    s.legendEls.title.textContent = [info.name || info.symbol, info.timeframe, info.exchange].filter(Boolean).join(' · ');
    s.legendEls.title.title = info.symbol + (info.source ? ' · ' + info.source : '');
  }

  // Index svíčky s daným časem v s.candles (binární hledání); -1 když není
  function indexOfTime(s, time) {
    let lo = 0;
    let hi = s.candles.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const t = s.candles[mid].time;
      if (t === time) return mid;
      if (t < time) lo = mid + 1;
      else hi = mid - 1;
    }
    return -1;
  }

  // Legenda: OHLC svíčky (barva podle směru), změna proti předchozímu závěru
  // a hodnoty indikátorů (i objemu – indikátor Volume) v daném čase
  function renderLegend(s, bar) {
    const els = s.legendEls;
    if (!els) return;
    const fields = [els.O, els.H, els.L, els.C, els.change];
    if (!bar) {
      fields.forEach((node) => (node.textContent = ''));
      renderIndicatorValues(s, null);
      return;
    }
    const index = indexOfTime(s, bar.time);
    const previous = index > 0 ? s.candles[index - 1] : null;
    const base = previous ? previous.close : bar.open;
    const change = bar.close - base;
    const changePct = base ? (change / base) * 100 : 0;
    const dir = bar.close >= bar.open ? 'up' : 'down';
    els.O.textContent = formatInstrumentPrice(s, bar.open);
    els.H.textContent = formatInstrumentPrice(s, bar.high);
    els.L.textContent = formatInstrumentPrice(s, bar.low);
    els.C.textContent = formatInstrumentPrice(s, bar.close);
    els.change.textContent =
      (change >= 0 ? '+' : '') + formatInstrumentPrice(s, change) + ' (' + (changePct >= 0 ? '+' : '') + changePct.toFixed(2) + '%)';
    // Barva hodnot OHLC podle směru svíčky, změna podle znaménka
    for (const node of [els.O, els.H, els.L, els.C]) {
      node.classList.toggle('up', dir === 'up');
      node.classList.toggle('down', dir === 'down');
    }
    els.change.classList.toggle('up', change >= 0);
    els.change.classList.toggle('down', change < 0);
    renderIndicatorValues(s, bar.time);
  }

  // Nastaví nadpis tickeru (název, symbol, burza, timeframe, zdroj)
  function setSymbolInfo(id, info) {
    const s = state.get(id);
    if (!s) return;
    s.symbolInfo = info || null;
    renderLegendTitle(s);
  }

  // Stav dat v nadpisu: 'live' (zelená), 'delayed' (oranžová), 'none' (šedá)
  function setLegendStatus(id, status) {
    const s = state.get(id);
    if (!s || !s.legendEls) return;
    const value = STATUS_TITLES[status] ? status : 'none';
    s.legendEls.status.className = 'legend-status ' + value;
    s.legendEls.status.title = STATUS_TITLES[value];
  }

  // ---------- legenda indikátorů (řádky s hodnotami a ovládáním) ----------

  // Vytvoří řádek indikátoru: název, hodnoty sérií a tlačítka (oko,
  // nastavení, odebrání) – kliky jdou přes frontu událostí do Pythonu
  function buildIndicatorRow(s, group, series) {
    const row = el('div', 'legend-ind' + (group.visible ? '' : ' legend-ind-hidden'));
    row.dataset.group = group.id;
    row.appendChild(el('span', 'legend-ind-title', group.title));
    const values = [];
    for (const spec of series) {
      // Hodnota výstupu se v legendě neukáže při vypnutém „Values in status line“
      // nebo u skrytého výstupu (záložka Style)
      const hidden = group.values === false || spec.visible === false;
      const value = el('span', 'legend-ind-val' + (hidden ? ' legend-ind-val-hidden' : ''));
      value.style.color = spec.color || '';
      row.appendChild(value);
      values.push(value);
    }
    const actions = el('span', 'legend-ind-actions');
    const buttons = [
      ['toggle', group.visible ? 'visibility' : 'visibility_off', group.visible ? tvTr('Skrýt') : tvTr('Zobrazit')],
      ['settings', 'settings', 'Settings…'],
      ['remove', 'close', tvTr('Odebrat')],
    ];
    for (const [action, icon, title] of buttons) {
      actions.appendChild(legendButton(s, icon, title, { type: 'indicator', action: action, id: group.id }));
    }
    row.appendChild(actions);
    // Dvojklik na řádek otevře Settings, pravý klik menu indikátoru (a označí ho)
    bindLegendRow(
      row,
      (event) => {
        selectIndicator(s, group.id);
        showContextMenu(event.clientX, event.clientY, indicatorMenuItems(s, group.id));
      },
      () => s.finishedQueue.push({ type: 'indicator', action: 'settings', id: group.id })
    );
    // Najetí na řádek zvýrazní čáry indikátoru, klik (mimo tlačítka) ho označí;
    // přes stejnou frontu jako pohyb nad grafem, aby ho naplánované vyhodnocení
    // pohybu (kurzor právě přejel z grafu na legendu) hned nepřepsalo
    row.addEventListener('mouseenter', () => throttleHover(s, () => setHover(s, null, group.id)));
    row.addEventListener('mouseleave', () => throttleHover(s, () => setIndicatorHover(s, null)));
    row.addEventListener('click', (event) => {
      if (event.target.closest('.legend-ind-btn')) return;
      event.stopPropagation();
      selectIndicator(s, group.id);
    });
    return { row, values };
  }

  // Přestaví řádky indikátorů v legendě a v titulcích panelů podle payloadu
  function rebuildIndicatorLegend(s, payload) {
    if (!s.legendEls) return;
    s.legendEls.indicators.innerHTML = '';
    s.indicatorRows = [];
    for (const group of payload.groups || []) {
      const overlaySeries = (payload.overlays || []).filter((overlay) => overlay.group === group.id);
      const pane = group.pane ? (payload.panes || []).find((p) => p.paneId === group.pane) : null;
      const valueSeries = pane ? pane.series : overlaySeries;
      const built = buildIndicatorRow(s, group, valueSeries);
      const existingPane = pane ? s.panesMap.get(pane.paneId) : null;
      if (existingPane) {
        existingPane.title.innerHTML = '';
        existingPane.title.appendChild(built.row);
      } else {
        s.legendEls.indicators.appendChild(built.row);
      }
      // Výstupy ve formátu objemu (indikátor Volume) se v legendě zkracují na K/M/B
      const volumeFormat = valueSeries.map((spec) => isVolumeFormat(spec));
      s.indicatorRows.push({
        group: group.id, row: built.row, values: built.values, precision: group.precision, volumeFormat: volumeFormat,
      });
    }
    // Označený / zvýrazněný indikátor, který z grafu zmizel, se zapomene
    const ids = new Set((payload.groups || []).map((group) => group.id));
    if (s.indicatorSelected && !ids.has(s.indicatorSelected)) s.indicatorSelected = null;
    if (s.indicatorHover && !ids.has(s.indicatorHover)) s.indicatorHover = null;
    applyLegendHighlight(s);
    renderIndicatorValues(s, null);
  }

  // Hodnoty indikátorů v legendě pro daný čas (null = poslední svíčka)
  function renderIndicatorValues(s, time) {
    if (!s.indicatorRows) return;
    const at = time === null || time === undefined ? (s.lastCandle ? s.lastCandle.time : null) : time;
    for (const row of s.indicatorRows) {
      const maps = s.indicatorValues.get(row.group) || [];
      row.values.forEach((node, index) => {
        const value = maps[index] && at !== null ? maps[index].get(at) : undefined;
        if (value === undefined) node.textContent = '';
        else if (row.volumeFormat[index]) node.textContent = formatVolume(value);
        else node.textContent = formatIndicatorValue(s, value, row.precision);
      });
    }
  }

  // Počet desetinných míst hodnoty indikátoru: od 10 výš stačí 2 (RSI 49.25),
  // menší hodnoty s přesností instrumentu (forex MACD 0.00006, EMA 1.10030)
  function indicatorDecimals(s, magnitude) {
    return magnitude >= 10 ? 2 : Math.max(2, s.priceDecimals || 2);
  }

  // Hodnota indikátoru: s přesností ze záložky Style (Precision), jinak velké
  // hodnoty (OBV) zkráceně a ostatní s indicatorDecimals
  function formatIndicatorValue(s, value, precision) {
    if (precision !== null && precision !== undefined) return formatFixed(value, precision);
    const magnitude = Math.abs(value);
    if (magnitude >= 1e5) return (value < 0 ? '−' : '') + formatVolume(magnitude);
    return formatFixed(value, indicatorDecimals(s, magnitude));
  }

  // Krytí výplně plochy (Area) indikátoru vůči barvě čáry
  const AREA_FILL_ALPHA = 0.3;

  // Formát cen série indikátoru: přesnost ze záložky Style, jinak výchozí počet míst
  function indicatorPriceFormat(precision, fallback) {
    const digits = precision !== null && precision !== undefined ? precision : fallback;
    return { type: 'price', precision: digits, minMove: Math.pow(10, -digits) };
  }

  // Největší absolutní hodnota sérií panelu (přesnost cenové osy panelu)
  function paneMagnitude(pane) {
    let magnitude = 0;
    for (const spec of pane.series) {
      for (const point of spec.data) {
        if (point.value !== undefined) magnitude = Math.max(magnitude, Math.abs(point.value));
      }
    }
    return magnitude;
  }

  // Formát cen sérií panelu: Precision ze záložky Style, jinak podle velikosti
  // hodnot (MACD na forexu potřebuje 5 míst, RSI 2)
  function panePriceFormat(s, pane) {
    return indicatorPriceFormat(pane.precision, indicatorDecimals(s, paneMagnitude(pane)));
  }

  // Formát cen overlay série: Precision skupiny, jinak přesnost instrumentu
  function overlayPriceFormat(s, payload, groupId) {
    const group = (payload.groups || []).find((g) => g.id === groupId);
    return indicatorPriceFormat(group ? group.precision : null, s.priceDecimals || 2);
  }

  // Zapamatuje si hodnoty sérií indikátorů podle času (pro legendu);
  // tail=true jen doplní/přepíše poslední body
  function storeIndicatorValues(s, payload) {
    if (!payload.tail) s.indicatorValues = new Map();
    const store = (group, index, data) => {
      if (!s.indicatorValues.has(group)) s.indicatorValues.set(group, []);
      const maps = s.indicatorValues.get(group);
      if (!maps[index]) maps[index] = new Map();
      for (const point of data) {
        if (point.value !== undefined) maps[index].set(point.time, point.value);
      }
    };
    // Pořadí overlay série ve skupině drží overlayMeta (viz rebuildIndicators)
    (payload.overlays || []).forEach((overlay, i) => store(overlay.group, s.overlayMeta[i].index, overlay.data));
    for (const pane of payload.panes || []) {
      pane.series.forEach((spec, index) => store(pane.group, index, spec.data));
    }
  }

  // Vytvoří graf v kontejneru: hlavní panel se svíčkami + objem jako překryv
  function init(id, options) {
    const container = document.querySelector('[data-chart-id="' + id + '"]');
    if (!container || state.has(id)) return;
    const themeName = options && options.dark ? 'dark' : 'light';
    const theme = THEMES[themeName];

    container.innerHTML = '';

    // Legenda s OHLC hodnotami jako překryv nad grafem
    const legend = document.createElement('div');
    legend.className = 'lwchart-legend';
    container.appendChild(legend);

    // Sloupec panelů: hlavní graf + případné další panely (RSI/MACD)
    const panes = document.createElement('div');
    panes.className = 'lwchart-panes';
    container.appendChild(panes);

    const mainHost = document.createElement('div');
    mainHost.className = 'lwchart-host';
    panes.appendChild(mainHost);

    // Překryvný canvas pro kreslení nad hlavním grafem
    const overlay = document.createElement('canvas');
    overlay.className = 'lwchart-overlay';
    mainHost.appendChild(overlay);
    // Canvas efektů kurzoru (prezentace, magie) nad kresbami: animace
    // překresluje jen tuto vrstvu, ne všechny kresby
    const fxCanvas = document.createElement('canvas');
    fxCanvas.className = 'lwchart-cursor-fx';
    mainHost.appendChild(fxCanvas);

    const chart = LightweightCharts.createChart(mainHost, {
      autoSize: true,
      layout: theme.layout,
      grid: theme.grid,
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: theme.border },
      timeScale: {
        borderColor: theme.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
      },
    });

    const s = {
      chart,
      // Hlavní série (typ grafu – svíčky, čára, …); vzniká v createMainSeries
      candleSeries: null,
      mainPoints: [],
      chartType: options.chartType || 'candles',
      legend,
      container,
      panes,
      mainHost,
      overlay: {
        canvas: overlay,
        ctx: overlay.getContext('2d'),
        mode: null,
        pending: null,
        previewPoint: null,
        drawings: [],
        // Magnet: 'off' | 'weak' (jen v blízkosti OHLC) | 'strong' (vždy na OHLC)
        magnet: 'off',
        hidden: false,
        selected: -1,
        drag: null,
        // Bod pod kurzorem při aktivním nástroji (po přichycení magnetem)
        hover: null,
        // Kresba pod kurzorem v režimu kurzoru – ukáže úchopy (a u pozice štítky Target/Stop)
        hovered: null,
      },
      // Zvolený typ kurzoru (režim z CURSOR_TYPES) a efekty prezentace / magie:
      // pozice kurzoru (kruh prezentace), vlny po kliku a částice emoji
      // (obojí seřazené podle času vzniku)
      cursorMode: 'cursor',
      cursorFx: { canvas: fxCanvas, ctx: fxCanvas.getContext('2d'), pointer: null, ripples: [], particles: [], frame: 0 },
      // Indikátor pod kurzorem (čára v grafu, panel, řádek legendy) a označený
      // indikátor (id skupiny) – kolečka na čarách a zvýrazněný řádek legendy
      indicatorHover: null,
      indicatorSelected: null,
      // Cenové úrovně alertů aktuálního symbolu (čárkované čáry s popiskem na ose)
      alertLines: [],
      alertPriceLines: [],
      // Nadpis tickeru a prvky legendy (buildLegend)
      symbolInfo: null,
      legendEls: null,
      // Řádky indikátorů v legendě a jejich hodnoty podle času
      indicatorRows: [],
      indicatorValues: new Map(),
      elementId: id,
      // Výchozí styl nových kreseb podle typu {druh: {color, options}} –
      // výchozí šablony ze Settings kresby (posílá Python)
      drawingDefaults: {},
      // Styl svíček (Settings grafu)
      chartStyle: options.chartStyle,
      // Kurzor je nad grafem (jen takový graf předává synchronizaci ostatním)
      pointerInside: false,
      // Pozorovatelé velikosti (odpojí se při odstranění grafu z rozložení)
      observers: [],
      themeName,
      candles: [],
      byTime: new Map(),
      lastCandle: null,
      hovering: false,
      keepTool: false,
      locked: false,
      overlaySeries: [],
      panesMap: new Map(),
      // Tabulky indikátorů v grafu (klíč skupina:pořadí → {el, json, host})
      indicatorTables: new Map(),
      syncing: false,
      // Graf panelu, nad kterým je kurzor (posouvání rozsahu z panelu)
      paneUnderPointer: null,
      // Časové pásmo zobrazení (IANA); výchozí místní čas prohlížeče
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      // Má instrument nějaký objem? (měřič pak ukazuje objem úseku)
      hasVolume: true,
      // Overlay série indikátorů: skupina a pořadí série (hit-test čar)
      overlayMeta: [],
      lastIndicatorPayload: null,
      indicatorStructure: null,
      // Úsečky a značky indikátorů (kreslí se pod kresbami uživatele)
      indicatorSegments: [],
      indicatorMarkers: [],
      // Čas posledního bodu sérií indikátorů (živé doplňování přes update())
      indicatorLastTime: -Infinity,
      finishedQueue: [],
      measure: null,
      // Donačítání starší historie při posunu doleva
      olderLoading: false,
      olderExhausted: false,
      olderRetryAt: 0,
      // Délka baru timeframu v sekundách (posílá Python v setCandles)
      stepSeconds: 60,
      // Lokální id nových kreseb, dokud jim Python nepřiřadí id z databáze
      nextLocalId: 1,
      // Replay: příkazy a obchody session (čáry, štítky, šipky), oblasti
      // štítků a čar pro kliknutí / tažení a právě tažená úroveň
      replay: null,
      replayStatic: null,
      replayHits: [],
      replayDrag: null,
    };
    state.set(id, s);
    if (!activeId) activeId = id;
    createMainSeries(s);

    // Legenda: svíčka pod kurzorem, mimo graf poslední svíčka; crosshair
    // se promítne do panelů a případně do ostatních grafů rozložení
    chart.subscribeCrosshairMove((param) => onStackCrosshairMove(s, chart, param));
    // Šířka časové osy = šířka grafu bez cenové osy: změna šířky cenové osy
    // (i resize) srovná osy hlavního grafu a panelů
    chart.timeScale().subscribeSizeChange(() => scheduleScaleAlign(s));

    // Panely indikátorů + překreslení kresby následují rozsah hlavního grafu;
    // posun grafu uživatelem se případně promítne do ostatních grafů
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      syncRange(s, chart, range);
      redrawOverlay(s);
      maybeRequestOlder(s, range);
      syncLayoutRange(s, range);
    });

    // Klik do grafu ho udělá aktivním (rozložení více grafů); Python se to
    // dozví vlastní událostí pointerdown na prvku grafu
    container.addEventListener('pointerdown', () => markActive(id), true);
    container.addEventListener('mouseenter', () => (s.pointerInside = true));
    container.addEventListener('mouseleave', () => releasePointer(s));

    // Přizpůsobení překryvného canvasu při změně velikosti hostitele
    const overlayResize = new ResizeObserver(() => redrawOverlay(s));
    overlayResize.observe(mainHost);
    s.observers.push(overlayResize);

    // Kreslení (aktivní nástroj): kliky na překryvném canvasu vytvářejí body
    overlay.addEventListener('click', (event) => {
      if (!s.overlay.mode || s.overlay.mode === 'cursor') return;
      const pane = getPaneGeometry(s);
      const clickX = event.clientX - pane.hostLeft;
      const clickY = event.clientY - pane.hostTop;
      // Kliky mimo cenovou plochu (pravá osa, spodní časová osa) ignorujeme
      if (clickX < pane.offsetX || clickX > pane.offsetX + pane.width) return;
      if (clickY < pane.offsetY || clickY > pane.offsetY + pane.height) return;
      const snapped = snapPoint(s, clickX - pane.offsetX, clickY - pane.offsetY, effectiveMagnet(s, event));
      // Klik mimo rozsah dat (čas nebo cenu nelze určit) ignorujeme
      if (!snapped) return;
      const point = cleanPoint(snapped);
      if (s.overlay.mode === 'text' || s.overlay.mode === 'sticker') {
        // Text a sticker: anchor pošleme Pythonu, dialog se zeptá na text / emoji
        s.finishedQueue.push({
          type: s.overlay.mode + '_request',
          time: point.time,
          price: point.price,
          // Šablona zvolená u nástroje Text vyplní dialog (jinak výchozí šablona)
          style: toolTemplate[s.overlay.mode] ? defaultStyle(s, s.overlay.mode) : null,
        });
        returnToCursor(s);
        return;
      }
      if (s.overlay.mode === 'measure') {
        handleMeasureClick(s, point);
        return;
      }
      if (s.overlay.mode === REPLAY_PICK_MODE) {
        // Výběr startu replaye: čas svíčky pod kurzorem pošleme Pythonu
        s.finishedQueue.push({ type: 'replay_pick', time: point.time });
        exitTool(s);
        return;
      }
      handleDrawingClick(s, point);
    });
    overlay.addEventListener('mousemove', (event) => {
      if (!s.overlay.mode) return;
      const { x, y } = localPoint(s, event);
      const isBrush = s.overlay.mode === 'brush';
      // Štětec kreslí volně (bez přichycení na bary), ostatní nástroje jako klik
      const point = isBrush ? pixelToPoint(s, x, y) : snapPoint(s, x, y, effectiveMagnet(s, event));
      if (!point) {
        s.overlay.hover = null;
        clearCrosshair(s);
        redrawOverlay(s);
        return;
      }
      // Overlay zachytává myš, takže crosshair grafu vedeme ručně na
      // (přichycený) bod – s magnetem je vidět, kam se bod přichytí
      s.overlay.hover = point;
      moveCrosshair(s, point);
      s.overlay.previewPoint = point;
      // Štětec: body se při tažení přidávají do rozepsané kresby
      if (isBrush && s.overlay.pending) {
        const points = s.overlay.pending.points;
        const lastPixel = pointToPixel(s, points[points.length - 1]);
        // Body přidáváme až po ~6 px, aby JSON kresby nebyly zbytečně velké
        if (!lastPixel || Math.hypot(x - lastPixel.x, y - lastPixel.y) > 6) {
          points.push(cleanPoint(point));
        }
      }
      redrawOverlay(s);
    });
    // Kurzor opustil graf: skryjeme crosshair i značku magnetu
    overlay.addEventListener('mouseleave', () => {
      s.overlay.hover = null;
      clearCrosshair(s);
      redrawOverlay(s);
    });
    // Pravý klik při kreslení: zruší rozepsanou kresbu, jinak ukončí nástroj
    overlay.addEventListener('contextmenu', (event) => {
      if (!s.overlay.mode) return;
      event.preventDefault();
      if (s.overlay.pending || s.measure) {
        s.overlay.pending = null;
        s.measure = null;
        redrawOverlay(s);
      } else {
        exitTool(s);
      }
    });
    // Výběr a editace kreseb v režimu kurzoru: zachytávání na hostu, aby graf
    // nezačal panovat, když míříme na kresbu (když jsou kresby uzamčené, necháme
    // graf panovat bez zásahu)
    mainHost.addEventListener(
      'mousedown',
      (event) => {
        if (event.button !== 0 || s.overlay.mode) return;
        const { x, y } = localPoint(s, event);
        if (startReplayInteraction(s, x, y)) {
          event.stopPropagation();
          event.preventDefault();
          return;
        }
        const type = cursorType(s);
        if (type === 'demo') addDemoRipple(s, x, y);
        else if (type === 'magic') spawnMagicParticles(s, x, y);
        if (s.locked) return;
        // Guma: klik na kresbu ji smaže (bez tažení)
        if (type === 'eraser') {
          const index = hitTest(s, x, y);
          if (index >= 0) {
            event.stopPropagation();
            event.preventDefault();
            removeDrawing(s, index);
            setHover(s, null, null);
          }
          return;
        }
        // Úchop táhne bod / hranu, tělo kresby celou kresbu
        const hit = hitDrawing(s, x, y);
        if (hit) {
          event.stopPropagation();
          event.preventDefault();
          s.overlay.selected = hit.index;
          s.overlay.drag = startDrag(s, hit.index, hit.handle, x, y);
          selectIndicator(s, null);
          redrawOverlay(s);
          return;
        }
        // Klik na čáru indikátoru ho označí, klik jinam zruší označení kresby
        // i indikátoru (graf se dál posouvá – událost nezastavujeme)
        if (s.overlay.selected >= 0) selectDrawing(s, -1);
        selectIndicator(s, hitIndicator(s, x, y));
      },
      true
    );
    // Najetí myší v režimu kurzoru (jako v TradingView): kresba ukáže úchopy
    // (pozice i štítky Target / Stop), čára indikátoru kolečka a zvýrazněný řádek legendy
    mainHost.addEventListener('mousemove', (event) => {
      if (s.overlay.mode) return;
      // Kruh prezentace sleduje kurzor i při tažení (každý pohyb, bez throttlingu)
      const type = cursorType(s);
      if (type === 'demo') {
        const { x, y } = localPoint(s, event);
        s.cursorFx.pointer = { x: x, y: y };
        animateCursorFx(s);
      }
      if (s.overlay.drag) return;
      throttleHover(s, () => {
        const { x, y } = localPoint(s, event);
        const replayHit = hitReplay(s, x, y);
        const hit = s.locked ? null : hitDrawing(s, x, y);
        const drawing = hit ? s.overlay.drawings[hit.index] : null;
        setHover(s, drawing, drawing ? null : hitIndicator(s, x, y));
        // Kurzor: čára replaye (tažení SL / TP / ceny příkazu) a tlačítko ✕
        // mají přednost před kresbou
        const replayCursor = replayHit ? (replayHit.field ? 'ns-resize' : 'pointer') : null;
        // Guma kresbu jen zvýrazní, kurzor zůstává gumou
        const drawingCursor = hit && type !== 'eraser' ? hit.cursor : null;
        setHostCursor(mainHost, replayCursor || drawingCursor);
      });
    });
    mainHost.addEventListener('mouseleave', () => {
      s.cursorFx.pointer = null;
      animateCursorFx(s);
      throttleHover(s, () => {
        setHover(s, null, null);
        setHostCursor(mainHost, null);
      });
    });

    // Pravý klik v režimu kurzoru (jako v TradingView): nad kresbou ji vybere
    // a nabídne Settings / smazání, nad čárou alertu jeho úpravu a smazání,
    // nad čárou indikátoru jeho Settings, skrytí a odebrání, jinde Settings
    // grafu (např. zobrazení objemu)
    mainHost.addEventListener(
      'contextmenu',
      (event) => {
        if (s.overlay.mode) return;
        event.preventDefault();
        const { x, y } = localPoint(s, event);
        const index = s.locked ? -1 : hitTest(s, x, y);
        selectDrawing(s, index);
        redrawOverlay(s);
        if (index >= 0) {
          showContextMenu(event.clientX, event.clientY, drawingMenuItems(s, s.overlay.drawings[index]));
          return;
        }
        const alertLine = hitAlertLine(s, y);
        if (alertLine) {
          showContextMenu(event.clientX, event.clientY, alertMenuItems(s, alertLine));
          return;
        }
        const group = hitIndicator(s, x, y);
        selectIndicator(s, group);
        if (group) {
          showContextMenu(event.clientX, event.clientY, indicatorMenuItems(s, group));
          return;
        }
        showContextMenu(event.clientX, event.clientY, chartMenuItems(s, s.candleSeries.coordinateToPrice(y)));
      },
      true
    );
    // Dvojklik na kresbu nebo čáru indikátoru otevře jeho Settings
    mainHost.addEventListener(
      'dblclick',
      (event) => {
        if (s.overlay.mode) return;
        const { x, y } = localPoint(s, event);
        const index = s.locked ? -1 : hitTest(s, x, y);
        const drawing = s.overlay.drawings[index];
        if (drawing) {
          event.stopPropagation();
          // V replayi dvojklik na pozici zadá obchod, jinak otevře Settings kresby
          if (s.replay && isPosition(drawing) && drawing.id) requestPositionOrder(s, drawing);
          else requestDrawingSettings(s, drawing);
          return;
        }
        const group = hitIndicator(s, x, y);
        if (group) {
          event.stopPropagation();
          s.finishedQueue.push({ type: 'indicator', action: 'settings', id: group });
        }
      },
      true
    );

    bindGlobals();
    buildLegend(s);
    renderLegend(s, null);
    // Formátování času podle pásma (hodiny ve stavové liště tikají v bindGlobals)
    applyTimezone(s);
    markActive(activeId);
  }

  // Posluchače společné všem grafům (registrují se jednou): lišta nástrojů,
  // klávesnice, zavírání nabídek, změna velikosti okna a hodiny; vše míří
  // na aktivní graf rozložení
  function bindGlobals() {
    if (globalsBound) return;
    globalsBound = true;
    // Lišta nástrojů: skupiny otevírají flyout nabídku (jako v TradingView),
    // akční tlačítka (magnet/tužka/lock/oko/koš) posíláme do fronty událostí
    document.addEventListener('click', (event) => {
      const s = activeState();
      const toolbar = event.target.closest('.drawing-toolbar');
      if (toolbar && s) {
        const groupButton = event.target.closest('[data-tool-group]');
        const actionButton = event.target.closest('[data-tool-action]');
        if (groupButton) {
          openToolFlyout(s, groupButton.dataset.toolGroup, groupButton);
        } else if (actionButton && actionButton.dataset.toolAction === 'magnet') {
          // Magnet má nabídku režimů (slabý / silný / vypnuto) jako v TradingView
          openMagnetFlyout(s, actionButton);
        } else if (actionButton) {
          s.finishedQueue.push({ type: 'action', action: actionButton.dataset.toolAction });
        }
      }
      // Klik mimo lištu a flyout nabídku flyout zavře; klik mimo kontextové menu ho zavře
      if (!toolbar && !event.target.closest('.tool-flyout')) closeFlyout();
      if (!event.target.closest('.drawing-contextmenu')) closeContextMenu();
    });
    // Pravý klik na skupinu s jediným nástrojem (kurzor, měřič…) nabídne
    // přidání do / odebrání z oblíbených (ostatní mají hvězdičku ve flyoutu)
    document.addEventListener('contextmenu', (event) => {
      const groupButton = event.target.closest('.drawing-toolbar [data-tool-group]');
      if (!groupButton) return;
      const group = toolGroups.find((g) => g.id === groupButton.dataset.toolGroup);
      const tools = group ? groupTools(group) : [];
      if (tools.length !== 1) return;
      event.preventDefault();
      closeFlyout();
      showContextMenu(event.clientX, event.clientY, [favoriteMenuItem(tools[0].mode)]);
    });
    // Escape zruší rozepsanou kresbu / měřič, ukončí aktivní nástroj (návrat
    // na kurzor jako v TradingView) a zruší označení (jen mimo vstupní pole)
    document.addEventListener('keydown', (event) => {
      const s = activeState();
      const tag = (event.target.tagName || '').toUpperCase();
      if (!s || tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (event.key === 'Escape') {
        if (s.overlay.mode) exitTool(s);
        for (const other of state.values()) {
          selectDrawing(other, -1);
          selectIndicator(other, null);
          redrawOverlay(other);
        }
      } else if ((event.key === 'Delete' || event.key === 'Backspace') && (s.overlay.selected >= 0 || s.indicatorSelected)) {
        // Delete smaže označenou kresbu, jinak odebere označený indikátor (jako v TradingView)
        if (s.overlay.selected >= 0) {
          deleteSelected(s);
        } else {
          s.finishedQueue.push({ type: 'indicator', action: 'remove', id: s.indicatorSelected });
          selectIndicator(s, null);
        }
      } else if (
        /^[a-z0-9]$/i.test(event.key) &&
        !event.ctrlKey && !event.metaKey && !event.altKey &&
        !document.querySelector('.q-dialog')
      ) {
        // Písmeno / číslice nad grafem otevře hledání symbolu s napsaným textem;
        // znaky napsané do otevření dialogu se připojí k čekající události
        const pending = s.finishedQueue.find((e) => e.type === 'symbol_search');
        if (pending) pending.text += event.key.toUpperCase();
        else s.finishedQueue.push({ type: 'symbol_search', text: event.key.toUpperCase() });
      }
    });
    // Tažení kresby (úchop nebo celá kresba) v grafu, kde začalo; posluchače
    // okna jsou společné, aby odebraný graf nezůstal v jejich uzávěrách
    window.addEventListener('mousemove', (event) => {
      for (const s of state.values()) {
        if (s.replayDrag) dragReplayLevel(s, event);
        const drag = s.overlay.drag;
        if (!drag) continue;
        const { x, y } = localPoint(s, event);
        dragDrawing(s, drag, x, y, effectiveMagnet(s, event));
      }
    });
    window.addEventListener('mouseup', () => {
      for (const s of state.values()) {
        finishReplayDrag(s);
        finishDrag(s);
      }
    });
    // Po změně velikosti okna i mřížky grafů zůstane panel oblíbených celý viditelný
    window.addEventListener('resize', () => positionFavorites());
    const grid = document.querySelector('.chart-grid');
    if (grid) new ResizeObserver(() => positionFavorites()).observe(grid);
    // Tikající hodiny ve stavové liště v pásmu aktivního grafu
    setInterval(() => updateClock(activeState()), 1000);
  }

  // Konec tažení kresby: změnu bodů pošle k uložení (jen když se reálně posunula)
  function finishDrag(s) {
    const drag = s.overlay.drag;
    if (!drag) return;
    // Po tažení uložíme změnu (jen když se pozice reálně změnily)
    if (drag.moved && drag.index >= 0) {
      const drawing = s.overlay.drawings[drag.index];
      if (drawing && drawing.id) {
        s.finishedQueue.push({
          type: 'update',
          id: drawing.id,
          points: drawing.points,
          text: drawing.text || null,
        });
      } else if (drawing) {
        // Kresba ještě nemá id: čeká-li "add" ve frontě, posunuté body dáme
        // rovnou do něj, jinak se uloží po přiřazení id (assignDrawingId)
        const event = s.finishedQueue.find((e) => e.type === 'add' && e.localId === drawing.localId);
        if (event) event.points = drawing.points;
        else drawing.dirty = true;
      }
    }
    s.overlay.drag = null;
  }

  // Stav aktivního grafu (bez aktivního první existující graf, jinak null)
  function activeState() {
    return state.get(activeId) || state.values().next().value || null;
  }

  // Kurzor opustil graf s (nebo graf zmizel): crosshair, který s vysílal do
  // ostatních grafů, schováme
  function releasePointer(s) {
    if (layoutSync.crosshair && s.pointerInside) forEachOther(s, (other) => clearCrosshair(other));
    s.pointerInside = false;
  }

  // Projde ostatní grafy rozložení (kromě s)
  function forEachOther(s, fn) {
    for (const other of state.values()) {
      if (other !== s) fn(other);
    }
  }

  // Označí aktivní graf (rámeček v rozložení více grafů, cíl lišty a klávesnice)
  function markActive(id) {
    if (!state.has(id)) return;
    const changed = activeId !== id;
    activeId = id;
    for (const [key, s] of state) s.container.classList.toggle('chart-active', key === id);
    if (changed) {
      // Panel oblíbených zvýrazní nástroj nového aktivního grafu, lišta
      // kresby ukáže jeho výběr
      renderFavorites();
      syncDrawingBar();
      updateClock(activeState());
    }
  }

  // Volby synchronizace rozložení {crosshair, time, date_range} (symbol a
  // interval synchronizuje Python)
  function setLayoutSync(id, sync) {
    layoutSync = Object.assign({}, layoutSync, sync || {});
    if (!layoutSync.crosshair) for (const s of state.values()) clearCrosshair(s);
  }

  // Synchronizace crosshairu: pohyb kurzoru v grafu s ukáže crosshair ve
  // stejném čase v ostatních grafech (na svíčce, která ten čas obsahuje)
  function syncCrosshair(s, param) {
    // Vysílá jen graf pod kurzorem – grafy, kterým se crosshair nastavil,
    // kurzor nemají, takže zpětná vazba nevznikne
    if (!layoutSync.crosshair || !s.pointerInside || state.size < 2) return;
    forEachOther(s, (other) => {
      if (typeof other.chart.setCrosshairPosition !== 'function') return;
      // Svíčka obsahující čas = poslední svíčka s časem ≤ čas (časy jsou celé sekundy)
      const index = param.time === undefined ? -1 : candleIndexAt(other, param.time);
      if (index < 0) {
        clearCrosshair(other);
        return;
      }
      const candle = other.candles[index];
      moveCrosshair(other, { price: candle.close, time: candle.time });
    });
  }

  // Synchronizace času mezi grafy rozložení: Time srovná pravý okraj (každý
  // graf si drží své přiblížení), Date range převezme celý časový rozsah.
  // Předává jen graf pod kurzorem (posun / zoom uživatelem)
  function syncLayoutRange(s, range) {
    // Vysílá jen graf pod kurzorem (posunuté grafy kurzor nemají – bez zpětné vazby)
    if (!(layoutSync.time || layoutSync.date_range) || !range || !s.pointerInside) return;
    if (state.size < 2 || !s.candles.length) return;
    const fromTime = logicalToTime(s, range.from);
    const toTime = logicalToTime(s, range.to);
    forEachOther(s, (other) => {
      if (!other.candles.length) return;
      const timeScale = other.chart.timeScale();
      const to = timeToLogical(other, toTime);
      let from = timeToLogical(other, fromTime);
      if (!layoutSync.date_range) {
        const current = timeScale.getVisibleLogicalRange();
        if (!current) return;
        from = to - (current.to - current.from);
      }
      if (from === null || to === null || to <= from) return;
      timeScale.setVisibleLogicalRange({ from: from, to: to });
    });
  }

  // Odstraní graf z rozložení (menší počet grafů): graf, panely, pozorovatele a stav
  function destroy(id) {
    const s = state.get(id);
    if (!s) return;
    cancelAnimationFrame(s.overlayFrame);
    clearIndicators(id);
    s.observers.forEach((observer) => observer.disconnect());
    try {
      s.chart.remove();
    } catch (err) {
      // Graf už byl odstraněn – nic nerozbíjíme
    }
    // Odebraný prvek mouseleave nevyšle – synchronizovaný crosshair schováme ručně
    releasePointer(s);
    state.delete(id);
    if (activeId === id) activeId = state.keys().next().value || null;
    markActive(activeId);
  }

  // Vyzvedne čekající události všech grafů jedním dotazem {id grafu: [události]}
  function popAllEvents() {
    const result = {};
    for (const [id, s] of state) {
      if (s.finishedQueue.length) result[id] = s.finishedQueue.splice(0, s.finishedQueue.length);
    }
    return result;
  }

  // Pozice myši v souřadnicích cenové plochy hlavního grafu
  function localPoint(s, event) {
    const pane = getPaneGeometry(s);
    return { x: event.clientX - pane.hostLeft - pane.offsetX, y: event.clientY - pane.hostTop - pane.offsetY };
  }

  // Naplní kontextové menu (i podmenu) položkami [popisek, handler, ikona,
  // třída, podmenu, akce]; null = oddělovač, položka s podmenu ho ukáže při
  // najetí, akce [ikona, tooltip, handler] je ikona vpravo (koš u šablony)
  function fillContextMenu(menu, items) {
    for (const entry of items) {
      if (!entry) {
        menu.appendChild(el('div', 'drawing-contextmenu-separator'));
        continue;
      }
      const [label, handler, icon, extraClass, submenu, action] = entry;
      const item = el('div', 'drawing-contextmenu-item' + (extraClass ? ' ' + extraClass : ''));
      item.appendChild(el('span', 'material-icons drawing-contextmenu-icon', icon || ''));
      item.appendChild(el('span', 'drawing-contextmenu-label', label));
      menu.appendChild(item);
      if (action) {
        const [actionIcon, actionTitle, actionHandler] = action;
        const button = toolIcon(actionIcon, 'drawing-contextmenu-action');
        button.title = actionTitle;
        button.addEventListener('click', (event) => {
          event.stopPropagation();
          closeContextMenu();
          actionHandler();
        });
        item.appendChild(button);
      }
      if (submenu) {
        item.classList.add('has-submenu');
        item.appendChild(el('span', 'material-icons drawing-contextmenu-arrow', 'chevron_right'));
        const sub = el('div', 'drawing-contextmenu drawing-contextmenu-sub');
        fillContextMenu(sub, submenu);
        item.appendChild(sub);
        // Podmenu u pravého okraje okna se otevře doleva, u dolního okraje se posune nahoru
        item.addEventListener('mouseenter', () => {
          sub.classList.remove('flip');
          sub.style.top = '';
          const rect = sub.getBoundingClientRect();
          if (rect.right > window.innerWidth - 4) sub.classList.add('flip');
          const overflow = rect.bottom - (window.innerHeight - 4);
          if (overflow > 0) sub.style.top = sub.offsetTop - overflow + 'px';
        });
        // Klik na samotnou položku s podmenu menu nezavírá
        item.addEventListener('click', (event) => event.stopPropagation());
        continue;
      }
      item.addEventListener('click', (event) => {
        event.stopPropagation();
        closeContextMenu();
        handler();
      });
    }
  }

  // Kontextové menu na pozici kurzoru (položky viz fillContextMenu)
  function showContextMenu(clientX, clientY, items) {
    closeContextMenu();
    const menu = el('div', 'drawing-contextmenu');
    fillContextMenu(menu, items);
    document.body.appendChild(menu);
    // Menu nesmí přetéct přes pravý/dolní okraj okna
    const rect = menu.getBoundingClientRect();
    menu.style.left = Math.min(clientX, window.innerWidth - rect.width - 4) + 'px';
    menu.style.top = Math.min(clientY, window.innerHeight - rect.height - 4) + 'px';
  }

  // Požádá Python o dialog Settings kresby (jen uložené kresby mají id)
  function requestDrawingSettings(s, drawing) {
    if (!drawing.id) return;
    s.finishedQueue.push({
      type: 'drawing_settings',
      id: drawing.id,
      kind: drawing.kind,
      color: drawing.color,
      text: drawing.text || null,
      options: drawing.options || {},
      points: drawing.points,
      // Přesnost cen instrumentu (zaokrouhlení cen pozice v dialogu)
      decimals: s.priceDecimals || 2,
      // FRVP: čísla barů rozsahu pro záložku Coordinates
      bars: isFrvp(drawing) ? frvpBars(s, drawing) : null,
    });
  }


  // Požádá Python o dialog alertu na čáře kresby
  function requestDrawingAlert(s, drawing) {
    s.finishedQueue.push({ type: 'alert_request', drawingId: drawing.id, kind: drawing.kind });
  }

  // Smaže vybranou kresbu a překreslí graf
  function deleteSelectedDrawing(s) {
    deleteSelected(s);
    redrawOverlay(s);
  }

  // Požádá Python o dialog příkazu replaye z úrovní kresby pozice (vstup, cíl, stop)
  function requestPositionOrder(s, drawing) {
    s.finishedQueue.push({ type: 'replay_position_order', id: drawing.id, kind: drawing.kind, points: drawing.points });
  }

  // Podmenu Template kresby (jako v TradingView): uložení stylu jako šablony,
  // výchozí šablona typu a uložené šablony typu s košem; vše vyřídí Python.
  // long = delší popisky nabídky šablon v liště kresby
  function drawingTemplateItems(s, drawing, long) {
    const send = (action, name) => () =>
      s.finishedQueue.push(drawingPayload(drawing, { type: 'drawing_template', action: action, name: name || null }));
    const items = [
      [long ? 'Save Drawing Template As…' : 'Save as…', send('save')],
      [long ? 'Apply Default Drawing Template' : 'Apply default', send('default')],
    ];
    const names = Object.keys(templatesOf(drawing.kind));
    if (names.length) items.push(null);
    for (const name of names) items.push([name, send('apply', name), '', '', null, deleteTemplateAction(s, drawing.kind, name)]);
    return items;
  }

  // Položky menu kresby: Settings, alert (u čar), Template a smazání
  function drawingMenuItems(s, drawing) {
    const items = [];
    // Long / Short position v replayi: zadání obchodu z úrovní kresby
    if (s.replay && isPosition(drawing) && drawing.id) {
      items.push([tvTr('Zadat obchod…'), () => requestPositionOrder(s, drawing), 'shopping_cart']);
    }
    items.push(['Settings…', () => requestDrawingSettings(s, drawing), 'settings']);
    if (alertLineKinds.includes(drawing.kind) && drawing.id) {
      items.push([tvTr('Přidat alert…'), () => requestDrawingAlert(s, drawing), 'alarm_add']);
    }
    // Šablony stylu jen u uložených kreseb se stylem (sticker je emoji)
    if (drawing.id && drawingConfig.kinds.includes(drawing.kind)) {
      items.push(['Template', null, 'style', '', drawingTemplateItems(s, drawing)]);
    }
    items.push([tvTr('Smazat objekt'), () => deleteSelectedDrawing(s), 'delete', 'danger']);
    return items;
  }

  // Cena leží svisle do HIT_TOLERANCE_PX od kurzoru (zásah čáry alertu / indikátoru)
  function nearPrice(s, price, y) {
    const py = s.candleSeries.priceToCoordinate(price);
    return py !== null && Math.abs(py - y) <= HIT_TOLERANCE_PX;
  }

  // Čára cenového alertu blízko kurzoru, nebo null
  function hitAlertLine(s, y) {
    return (s.alertLines || []).find((line) => line.id && nearPrice(s, line.price, y)) || null;
  }

  // Položky menu čáry alertu: úprava a smazání (vyřídí Python podle id alertu)
  function alertMenuItems(s, line) {
    const push = (action) => () => s.finishedQueue.push({ type: 'alert_action', action: action, id: line.id });
    return [
      [tvTr('Upravit alert…'), push('edit'), 'edit'],
      [tvTr('Smazat alert'), push('delete'), 'delete', 'danger'],
    ];
  }

  // Položky menu indikátoru: Settings, skrytí/zobrazení a odebrání
  function indicatorMenuItems(s, groupId) {
    const payload = s.lastIndicatorPayload || {};
    const visible = groupVisible(payload, groupId);
    const group = (payload.groups || []).find((item) => item.id === groupId);
    const push = (action) => () => s.finishedQueue.push({ type: 'indicator', action: action, id: groupId });
    const items = [['Settings…', push('settings'), 'settings']];
    // Indikátor se signály (has_signals) nabízí alert na signál
    if (group && group.signals) items.push([tvTr('Přidat alert…'), push('alert'), 'alarm_add']);
    items.push([visible ? tvTr('Skrýt') : tvTr('Zobrazit'), push('toggle'), visible ? 'visibility_off' : 'visibility']);
    items.push([tvTr('Odebrat indikátor'), push('remove'), 'delete', 'danger']);
    return items;
  }

  // Položky menu grafu: alert na ceně pod kurzorem (když je známá) a Settings grafu
  function chartMenuItems(s, price) {
    const items = [];
    if (price !== null && price !== undefined && isFinite(price) && s.candles.length) {
      const rounded = roundPrice(s, Number(price));
      items.push([tvTr('Přidat alert na {price}').replace('{price}', formatInstrumentPrice(s, rounded)), () => s.finishedQueue.push({ type: 'alert_request', price: rounded }), 'alarm_add']);
    }
    items.push(['Settings…', () => s.finishedQueue.push({ type: 'chart_settings' }), 'settings']);
    return items;
  }

  // Vyhodnocení najetí myší nejvýš jednou za snímek (myš posílá i několik
  // událostí za snímek a hit-test prochází všechny kresby); platí poslední volání
  function throttleHover(s, fn) {
    const pending = !!s.hoverTask;
    s.hoverTask = fn;
    if (pending) return;
    requestAnimationFrame(() => {
      const task = s.hoverTask;
      s.hoverTask = null;
      if (task && state.get(s.elementId) === s) task();
    });
  }

  // Kurzor myši nad grafem v režimu kurzoru (nad kresbou pointer, nad úchopem
  // move / resize, nad čárou replaye ns-resize) – přes atribut data-cursor
  // a CSS; null = výchozí kurzor grafu; DOM se mění jen při změně kurzoru
  function setHostCursor(host, cursor) {
    if ((host.dataset.cursor || null) === cursor) return;
    if (cursor) host.dataset.cursor = cursor;
    else delete host.dataset.cursor;
  }

  // Kresba a indikátor pod kurzorem; při změně překreslí úchopy, kolečka a legendu
  function setHover(s, drawing, group) {
    if (drawing !== s.overlay.hovered) {
      s.overlay.hovered = drawing;
      scheduleOverlayRedraw(s);
    }
    setIndicatorHover(s, group);
  }

  // Zvýrazněný nebo označený indikátor (key = 'indicatorHover' / 'indicatorSelected',
  // group = id skupiny nebo null); při změně překreslí kolečka a legendu
  function setIndicatorState(s, key, group) {
    if (group === s[key]) return;
    s[key] = group;
    applyLegendHighlight(s);
    scheduleOverlayRedraw(s);
  }

  // Indikátor pod kurzorem (čára, panel, řádek legendy) – null = žádný
  function setIndicatorHover(s, group) {
    setIndicatorState(s, 'indicatorHover', group);
  }

  // Označí indikátor (id skupiny, null = zrušit označení); označení trvá do
  // kliknutí jinam nebo Esc, Delete indikátor odebere
  function selectIndicator(s, group) {
    setIndicatorState(s, 'indicatorSelected', group);
  }

  // Zvýraznění řádků legendy: rámeček označeného, podbarvení zvýrazněného
  function applyLegendHighlight(s) {
    for (const row of s.indicatorRows || []) {
      row.row.classList.toggle('legend-ind-selected', row.group === s.indicatorSelected);
      row.row.classList.toggle('legend-ind-hover', row.group === s.indicatorHover);
    }
  }

  // Kroužek s výplní barvou pozadí grafu (kolečka indikátoru, úchopy kreseb,
  // značka magnetu); barvu obrysu a tloušťku nastaví volající
  function drawRing(ctx, s, x, y, radius) {
    ctx.fillStyle = THEMES[s.themeName].layout.background.color;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }

  // Připraví canvas nad grafem ke kreslení: velikost podle devicePixelRatio,
  // měřítko a vyčištění; vrací {ctx, width, height} v CSS px
  function prepareCanvas(canvas, ctx) {
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
  }

  // Skupiny indikátorů, které mají kolečka na čarách (zvýrazněná a označená)
  function highlightedGroups(s) {
    return [s.indicatorHover, s.indicatorSelected].filter(Boolean);
  }

  // Kolečka na čarách zvýrazněných / označených indikátorů v daném grafu
  // (jako v TradingView): v pravidelném rozestupu podle svíček, aby se při
  // posunu grafu nepřeskupovala.
  // Parametry: ctx – kontext canvasu v souřadnicích plochy grafu, chart – graf
  // Lightweight Charts, width – šířka plochy grafu v px (panel se skrytou
  // časovou osou ji z časové osy nezjistí), entries – [{group, index, series, spec}]
  function drawSelectionMarkers(ctx, s, chart, width, entries) {
    const groups = highlightedGroups(s);
    if (!groups.length || !s.candles.length || width <= 0) return;
    const timeScale = chart.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    if (!range || range.to <= range.from) return;
    // Krok v barech tak, aby kolečka byla aspoň SELECTION_MARKER_SPACING_PX od sebe
    const barWidth = width / (range.to - range.from);
    const step = Math.max(1, Math.ceil(SELECTION_MARKER_SPACING_PX / Math.max(barWidth, 0.01)));
    const first = Math.max(0, Math.ceil(Math.max(range.from, 0) / step) * step);
    const last = Math.min(s.candles.length - 1, Math.floor(range.to));
    const payload = s.lastIndicatorPayload || {};
    ctx.save();
    ctx.setLineDash([]);
    ctx.lineWidth = 1.5;
    for (const entry of entries) {
      if (!groups.includes(entry.group) || entry.spec.visible === false || !groupVisible(payload, entry.group)) continue;
      const values = (s.indicatorValues.get(entry.group) || [])[entry.index];
      if (!values) continue;
      ctx.strokeStyle = entry.spec.color || HANDLE_COLOR;
      for (let index = first; index <= last; index += step) {
        const value = values.get(s.candles[index].time);
        if (value === undefined) continue;
        const x = timeScale.logicalToCoordinate(index);
        const y = entry.series.priceToCoordinate(value);
        if (x !== null && y !== null) drawRing(ctx, s, x, y, SELECTION_MARKER_RADIUS);
      }
    }
    ctx.restore();
  }

  // Série overlay indikátorů hlavního grafu pro drawSelectionMarkers
  function overlayMarkerEntries(s) {
    const overlays = (s.lastIndicatorPayload || {}).overlays || [];
    return (s.overlayMeta || []).map((meta, position) => ({
      group: meta.group,
      index: meta.index,
      series: s.overlaySeries[position],
      spec: overlays[position] || {},
    }));
  }

  // Série panelu indikátoru pro drawSelectionMarkers / hitIndicatorEntries
  function paneMarkerEntries(pane, existing) {
    return pane.series.map((spec, index) => ({ group: pane.group, index, series: existing.series[index], spec }));
  }

  // Překreslí kolečka v panelech indikátorů (vlastní canvas nad každým panelem);
  // panel bez zvýrazněného indikátoru jen vyčistí, pokud v něm něco bylo
  function redrawPaneMarkers(s) {
    const groups = highlightedGroups(s);
    const panes = (s.lastIndicatorPayload || {}).panes || [];
    for (const pane of panes) {
      const existing = s.panesMap.get(pane.paneId);
      if (!existing || !existing.markers) continue;
      const active = groups.includes(pane.group);
      if (!active && !existing.markersDrawn) continue;
      const { ctx, width, height } = prepareCanvas(existing.markers, existing.markers.getContext('2d'));
      existing.markersDrawn = active;
      if (!active) continue;
      // Kolečka jen v ploše panelu (bez cenové osy vpravo)
      const plotWidth = width - existing.chart.priceScale('right').width();
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, plotWidth, height);
      ctx.clip();
      drawSelectionMarkers(ctx, s, existing.chart, plotWidth, paneMarkerEntries(pane, existing));
      ctx.restore();
    }
  }

  // Indikátor, jehož čára vede blízko kurzoru, ze sérií grafu (hlavní graf
  // nebo panel); skrytá skupina (oko v legendě) ani skrytý výstup (záložka
  // Style) se netrefí. Vrací id skupiny nebo null.
  // Parametry: chart – graf Lightweight Charts, entries – [{group, index, series, spec}]
  function hitIndicatorEntries(s, chart, entries, x, y) {
    const logical = chart.timeScale().coordinateToLogical(x);
    const candle = logical === null ? null : s.candles[Math.round(logical)];
    if (!candle) return null;
    const payload = s.lastIndicatorPayload || {};
    for (const entry of entries) {
      if (!entry.series || entry.spec.visible === false || !groupVisible(payload, entry.group)) continue;
      const values = (s.indicatorValues.get(entry.group) || [])[entry.index];
      const value = values ? values.get(candle.time) : undefined;
      if (value === undefined) continue;
      const py = entry.series.priceToCoordinate(value);
      if (py !== null && Math.abs(py - y) <= HIT_TOLERANCE_PX) return entry.group;
    }
    return null;
  }

  // Najde overlay indikátor, jehož čára vede blízko kurzoru (id skupiny / null)
  function hitIndicator(s, x, y) {
    return hitIndicatorEntries(s, s.chart, overlayMarkerEntries(s), x, y);
  }

  // Zavře pravoklikové menu (pokud existuje)
  function closeContextMenu() {
    document.querySelectorAll('.drawing-contextmenu').forEach((menu) => menu.remove());
  }

  // Nástroje skupiny bez nadpisů sekcí ({section: "…"} jen člení flyout)
  function groupTools(group) {
    return (group.tools || []).filter((tool) => tool.mode);
  }

  // Všechny nástroje lišty v pořadí skupin (pořadí v panelu oblíbených)
  function allTools() {
    return toolGroups.flatMap(groupTools);
  }

  // Událost výběru nástroje: klik na už aktivní nástroj ho vypne (návrat na kurzor)
  function toolEvent(s, mode) {
    if (CURSOR_TYPES[mode]) return { type: 'tool', mode: mode };
    return { type: 'tool', mode: s.overlay.mode === mode ? null : mode };
  }

  // Je nástroj právě aktivní? (bez kreslicího režimu zvolený typ kurzoru)
  function toolActive(s, mode) {
    return (s ? s.overlay.mode || s.cursorMode : 'cursor') === mode;
  }

  // Typ kurzoru grafu ('cross', 'dot', 'arrow', 'demo', 'magic', 'eraser')
  function cursorType(s) {
    return CURSOR_TYPES[s.cursorMode] || 'cross';
  }

  // Volby crosshairu podle typu kurzoru: čáry jen u kříže (u ostatních
  // průhledné, popisky na osách zůstávají)
  function crosshairLineOptions(s) {
    const color = cursorType(s) === 'cross' ? CROSSHAIR_LINE_COLOR : 'rgba(0, 0, 0, 0)';
    return { vertLine: { color: color }, horzLine: { color: color } };
  }

  // Projeví typ kurzoru: atribut data-cursor-type kontejneru (CSS kurzor nad
  // grafem i panely), čáry crosshairu a vyčištění efektů prezentace / magie
  function applyCursorType(s) {
    s.container.dataset.cursorType = cursorType(s);
    const crosshair = crosshairLineOptions(s);
    s.chart.applyOptions({ crosshair: crosshair });
    for (const pane of s.panesMap.values()) pane.chart.applyOptions({ crosshair: crosshair });
    s.cursorFx.ripples = [];
    s.cursorFx.particles = [];
    s.cursorFx.pointer = null;
    animateCursorFx(s);
  }

  // Přidá vlnu po kliku (prezentace) a spustí animaci
  function addDemoRipple(s, x, y) {
    s.cursorFx.ripples.push({ x: x, y: y, t: performance.now() });
    animateCursorFx(s);
  }

  // Předvykreslená emoji magie (emoji → canvas): barevné emoji je drahé
  // kreslit textem v každém snímku, sprite se jen otáčí a škáluje přes drawImage
  const magicSprites = new Map();
  // Velikost spritu v CSS px (písmo 18 px + rezerva) a jeho rozlišení
  const MAGIC_SPRITE_PX = 24;

  // Sprite emoji (vytvoří se při prvním použití v rozlišení obrazovky)
  function magicSprite(emoji) {
    let sprite = magicSprites.get(emoji);
    if (sprite) return sprite;
    const dpr = window.devicePixelRatio || 1;
    sprite = document.createElement('canvas');
    sprite.width = sprite.height = Math.ceil(MAGIC_SPRITE_PX * dpr);
    const ctx = sprite.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.font = MAGIC_FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(emoji, MAGIC_SPRITE_PX / 2, MAGIC_SPRITE_PX / 2);
    magicSprites.set(emoji, sprite);
    return sprite;
  }

  // Magie: po kliku vypustí z místa kliku dávku emoji do všech stran
  // (s mírným tahem vzhůru) s náhodnou rychlostí a rotací; nejstarší
  // částice nad stropem MAGIC_MAX_PARTICLES zahodí
  function spawnMagicParticles(s, x, y) {
    const fx = s.cursorFx;
    const now = performance.now();
    for (let k = 0; k < MAGIC_PER_CLICK; k++) {
      const angle = Math.random() * Math.PI * 2;
      const speed = MAGIC_SPEED_MIN + Math.random() * (MAGIC_SPEED_MAX - MAGIC_SPEED_MIN);
      fx.particles.push({
        x: x,
        y: y,
        t: now,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 120,
        rotation: (Math.random() - 0.5) * 1.2,
        spin: (Math.random() - 0.5) * 6,
        scale: 0.75 + Math.random() * 0.5,
        emoji: MAGIC_EMOJIS[Math.floor(Math.random() * MAGIC_EMOJIS.length)],
      });
    }
    if (fx.particles.length > MAGIC_MAX_PARTICLES) fx.particles.splice(0, fx.particles.length - MAGIC_MAX_PARTICLES);
    animateCursorFx(s);
  }

  // Animace efektů kurzoru: překresluje jejich vrstvu po snímcích, dokud
  // efekty doznívají (vlny, částice); kruh prezentace se překreslí s pohybem myši
  function animateCursorFx(s) {
    const fx = s.cursorFx;
    if (fx.frame) return;
    fx.frame = requestAnimationFrame(() => {
      fx.frame = 0;
      if (state.get(s.elementId) !== s) return;
      const now = performance.now();
      // Body jsou seřazené podle času – doznělé jsou na začátku pole
      while (fx.ripples.length && now - fx.ripples[0].t >= DEMO_RIPPLE_MS) fx.ripples.shift();
      while (fx.particles.length && now - fx.particles[0].t >= MAGIC_PARTICLE_MS) fx.particles.shift();
      renderCursorFx(s, now);
      if (fx.ripples.length || fx.particles.length) animateCursorFx(s);
    });
  }

  // Vykreslí efekty kurzoru do jejich vrstvy (jen plocha grafu, souřadnice
  // jako kresby): u prezentace průsvitný kruh kolem kurzoru a rozpínající se
  // vlny po kliku, u magie letící emoji (průhlednost přes globalAlpha)
  function renderCursorFx(s, now) {
    const fx = s.cursorFx;
    if (!fx.canvas.clientWidth || !fx.canvas.clientHeight) return;
    const { ctx } = prepareCanvas(fx.canvas, fx.ctx);
    if (!fx.pointer && !fx.ripples.length && !fx.particles.length) return;
    const pane = getPaneGeometry(s);
    ctx.save();
    ctx.beginPath();
    ctx.rect(pane.offsetX, pane.offsetY, pane.width, pane.height);
    ctx.clip();
    ctx.translate(pane.offsetX, pane.offsetY);
    ctx.strokeStyle = HANDLE_COLOR;
    if (fx.pointer) {
      ctx.fillStyle = HANDLE_COLOR;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(fx.pointer.x, fx.pointer.y, DEMO_RADIUS, 0, Math.PI * 2);
      ctx.globalAlpha = 0.18;
      ctx.fill();
      ctx.globalAlpha = 0.5;
      ctx.stroke();
    }
    ctx.lineWidth = 2;
    for (const ripple of fx.ripples) {
      const progress = (now - ripple.t) / DEMO_RIPPLE_MS;
      ctx.globalAlpha = 0.8 * (1 - progress);
      ctx.beginPath();
      ctx.arc(ripple.x, ripple.y, DEMO_RADIUS / 2 + (DEMO_RIPPLE_RADIUS - DEMO_RADIUS / 2) * progress, 0, Math.PI * 2);
      ctx.stroke();
    }
    // Částice magie: poloha z rychlosti, odporu a gravitace (vrh s odporem
    // úměrným rychlosti: dolet v/k·(1−e^(−kt)), pád ustálenou rychlostí g/k),
    // otáčení, krátké „vyskočení“ na začátku a zmizení v poslední třetině
    // života; transformace se pro každou částici skládá od základní matice
    const base = ctx.getTransform();
    const half = MAGIC_SPRITE_PX / 2;
    for (const particle of fx.particles) {
      const age = now - particle.t;
      const dt = age / 1000;
      const life = age / MAGIC_PARTICLE_MS;
      const scale = particle.scale * Math.min(1, age / 120);
      ctx.globalAlpha = life < 0.66 ? 1 : Math.max(0, (1 - life) / 0.34);
      ctx.setTransform(base);
      const glide = (1 - Math.exp(-MAGIC_DRAG * dt)) / MAGIC_DRAG;
      const fall = (MAGIC_GRAVITY / MAGIC_DRAG) * (dt - glide);
      ctx.translate(particle.x + particle.vx * glide, particle.y + particle.vy * glide + fall);
      ctx.rotate(particle.rotation + particle.spin * dt);
      ctx.scale(scale, scale);
      ctx.drawImage(magicSprite(particle.emoji), -half, -half, MAGIC_SPRITE_PX, MAGIC_SPRITE_PX);
    }
    ctx.restore();
  }


  // Flyout nabídka skupiny nástrojů (pozice u tlačítka, jako v TradingView):
  // nadpisy sekcí, nástroje a u každého hvězdička pro oblíbené
  function openToolFlyout(s, groupId, anchorButton) {
    closeFlyout();
    const group = toolGroups.find((g) => g.id === groupId);
    if (!group) return;
    const tools = groupTools(group);
    if (tools.length === 1) {
      // Skupina s jediným nástrojem se aktivuje rovnou přes frontu
      s.finishedQueue.push(toolEvent(s, tools[0].mode));
      return;
    }
    const flyout = el('div', 'tool-flyout');
    for (const tool of group.tools) {
      if (!tool.mode) {
        // Sekce bez nadpisu = oddělovač (např. před gumou ve skupině kurzoru)
        flyout.appendChild(tool.section ? el('div', 'tool-flyout-section', tool.section) : el('div', 'drawing-contextmenu-separator'));
        continue;
      }
      const item = flyoutItem(tool.icon, tool.tooltip, toolActive(s, tool.mode), toolEvent(s, tool.mode), s);
      item.appendChild(favoriteStar(tool.mode));
      flyout.appendChild(item);
    }
    placeFlyout(flyout, anchorButton);
  }

  // Hvězdička položky flyoutu: přidá / odebere nástroj z oblíbených (nevybírá ho)
  function favoriteStar(mode) {
    const star = el('span', 'tv-icon-wrap tool-flyout-star');
    const paint = () => {
      const on = fav.modes.includes(mode);
      star.innerHTML = toolIcons[on ? 'star' : 'star_border'] || '';
      star.classList.toggle('on', on);
      star.title = on ? tvTr('Odebrat z oblíbených') : tvTr('Přidat do oblíbených');
    };
    paint();
    star.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleFavorite(mode);
      paint();
    });
    return star;
  }

  // Položka kontextového menu pro přidání / odebrání nástroje z oblíbených
  function favoriteMenuItem(mode) {
    const on = fav.modes.includes(mode);
    return [on ? tvTr('Odebrat z oblíbených') : tvTr('Přidat do oblíbených'), () => toggleFavorite(mode), on ? 'star' : 'star_border'];
  }

  // Přepne nástroj v oblíbených; seznam se drží v pořadí lišty a ukládá v Pythonu
  function toggleFavorite(mode) {
    const wanted = fav.modes.includes(mode) ? fav.modes.filter((item) => item !== mode) : fav.modes.concat([mode]);
    fav.modes = allTools().map((tool) => tool.mode).filter((item) => wanted.includes(item));
    renderFavorites();
    const s = activeState();
    if (s) s.finishedQueue.push({ type: 'favorites', modes: fav.modes.slice() });
  }

  // Plovoucí panel oblíbených nástrojů nad grafem (jako v TradingView; jeden
  // pro všechny grafy rozložení, nástroje míří na aktivní graf): úchop pro
  // přesun a tlačítka nástrojů; bez oblíbených nebo skrytý se nezobrazí
  function renderFavorites() {
    const tools = allTools().filter((tool) => fav.modes.includes(tool.mode));
    let panel = fav.el;
    if (!tools.length || !fav.visible || !state.size) {
      if (panel) panel.classList.add('hidden');
      positionDrawingBar();
      return;
    }
    if (!panel) {
      panel = el('div', 'fav-toolbar');
      const handle = el('span', 'material-icons fav-handle', 'drag_indicator');
      handle.title = tvTr('Přesunout panel');
      panel.appendChild(handle);
      attachFavoritesDrag(panel, handle);
      // Panel je v body s position: fixed – lze ho přesunout kamkoli v okně
      // (i nad horní lištu), ne jen v ploše grafu
      document.body.appendChild(panel);
      fav.el = panel;
    }
    panel.classList.remove('hidden');
    panel.querySelectorAll('.fav-tool').forEach((node) => node.remove());
    const active = activeState();
    for (const tool of tools) {
      const button = el('button', 'fav-tool' + (toolActive(active, tool.mode) ? ' active' : ''));
      button.type = 'button';
      button.title = tool.tooltip;
      button.dataset.mode = tool.mode;
      button.appendChild(toolIcon(tool.icon));
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        closeFlyout();
        const s = activeState();
        if (s) s.finishedQueue.push(toolEvent(s, tool.mode));
      });
      // Pravý klik na nástroj v panelu ho z oblíbených odebere
      button.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        event.stopPropagation();
        showContextMenu(event.clientX, event.clientY, [favoriteMenuItem(tool.mode)]);
      });
      panel.appendChild(button);
    }
    positionFavorites();
  }

  // Zvýrazní v panelu oblíbených nástroj aktivního grafu (bez přestavby panelu)
  function paintFavoriteTools() {
    if (!fav.el) return;
    const s = activeState();
    fav.el.querySelectorAll('.fav-tool').forEach((button) => {
      button.classList.toggle('active', toolActive(s, button.dataset.mode));
    });
  }

  // Obdélník plochy grafů v okně (výchozí umístění plovoucích panelů)
  function chartAreaRect() {
    const area = document.querySelector('.chart-grid') || (activeState() || {}).container;
    return area ? area.getBoundingClientRect() : { left: 0, top: 0, width: window.innerWidth };
  }

  // Umístí panel oblíbených: uložená pozice, jinak nahoře uprostřed plochy
  // grafů; vždy celý uvnitř okna (po zúžení okna se posune dovnitř)
  function positionFavorites() {
    const panel = fav.el;
    if (!panel || panel.classList.contains('hidden')) {
      positionDrawingBar();
      return;
    }
    // Pozice v px vůči oknu; bez uložené pozice nahoře uprostřed plochy grafů
    const maxLeft = Math.max(0, window.innerWidth - panel.offsetWidth);
    const maxTop = Math.max(0, window.innerHeight - panel.offsetHeight);
    const rect = chartAreaRect();
    const pos = fav.pos || { left: rect.left + (rect.width - panel.offsetWidth) / 2, top: rect.top + 6 };
    panel.style.left = Math.round(Math.min(maxLeft, Math.max(0, pos.left))) + 'px';
    panel.style.top = Math.round(Math.min(maxTop, Math.max(0, pos.top))) + 'px';
    // Lišta kresby se drží pod panelem
    positionDrawingBar();
  }

  // Tažení panelu oblíbených za úchop; konečná pozice se uloží v Pythonu
  function attachFavoritesDrag(panel, handle) {
    let start = null;
    handle.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      event.stopPropagation();
      handle.setPointerCapture(event.pointerId);
      const rect = panel.getBoundingClientRect();
      start = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
      panel.classList.add('dragging');
    });
    handle.addEventListener('pointermove', (event) => {
      if (!start) return;
      fav.pos = { left: start.left + event.clientX - start.x, top: start.top + event.clientY - start.y };
      positionFavorites();
    });
    const finish = () => {
      if (!start) return;
      start = null;
      panel.classList.remove('dragging');
      // Uložíme skutečnou (ořezanou) pozici, ne pozici kurzoru mimo okno
      const rect = panel.getBoundingClientRect();
      fav.pos = { left: Math.round(rect.left), top: Math.round(rect.top) };
      const s = activeState();
      if (s) s.finishedQueue.push({ type: 'favorites_position', left: fav.pos.left, top: fav.pos.top });
    };
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
  }

  // Prvek s SVG piktogramem nástroje (icons.py); neznámý název = prázdný prvek
  function toolIcon(name, className) {
    const node = el('span', 'tv-icon-wrap' + (className ? ' ' + className : ''));
    node.innerHTML = toolIcons[name] || '';
    return node;
  }

  // Konfigurace kreslení z Pythonu {kinds, lineKinds, labelKinds, widths, lineStyles,
  // templates: {druh: {název: styl}}} (po načtení a po každé změně šablon;
  // společná všem grafům). Zvolená šablona nástroje, která mezitím zmizela,
  // se vrátí na výchozí
  function setDrawingConfig(id, payload) {
    const data = payload && typeof payload === 'object' ? payload : {};
    const list = (value) => (Array.isArray(value) ? value : []);
    drawingConfig = {
      kinds: list(data.kinds),
      lineKinds: list(data.lineKinds),
      labelKinds: list(data.labelKinds),
      widths: list(data.widths),
      lineStyles: list(data.lineStyles),
      templates: data.templates && typeof data.templates === 'object' ? data.templates : {},
    };
    for (const kind of Object.keys(toolTemplate)) {
      if (!templatesOf(kind)[toolTemplate[kind]]) delete toolTemplate[kind];
    }
    syncDrawingBar(true);
  }

  // Uložené šablony druhu kresby {název: styl}
  function templatesOf(kind) {
    return drawingConfig.templates[kind] || {};
  }

  // Akce koše u uložené šablony v nabídce (smazání vyřídí Python)
  function deleteTemplateAction(s, kind, name) {
    return ['trash', tvTr('Smazat šablonu'), () => s.finishedQueue.push({ type: 'drawing_template', action: 'delete', kind: kind, name: name })];
  }

  // Cíl lišty kresby v aktivním grafu: aktivní kreslicí nástroj se stylem
  // (výběr šablony nového objektu), jinak vybraná uložená kresba; jinak null
  function drawingBarTarget(s) {
    if (!s) return null;
    const mode = s.overlay.mode;
    if (mode) return drawingConfig.kinds.includes(mode) ? { kind: mode, drawing: null } : null;
    const drawing = !s.overlay.hidden && s.overlay.selected >= 0 ? s.overlay.drawings[s.overlay.selected] : null;
    return drawing && drawing.id ? { kind: drawing.kind, drawing: drawing } : null;
  }

  // Plovoucí lišta kresby pod panelem oblíbených (jako v TradingView): při
  // aktivním nástroji výběr šablony nového objektu, u vybrané kresby rychlé
  // změny stylu. Volá se při každém překreslení aktivního grafu – lištu
  // přestaví jen změna cíle (force = vždy, např. po změně šířky v liště)
  function syncDrawingBar(force) {
    const s = activeState();
    const target = drawingBarTarget(s);
    const key = target ? [activeId, target.kind, toolTemplate[target.kind] || ''].join('|') : '';
    const drawing = target ? target.drawing : null;
    if (!force && key === drawingBar.key && drawing === drawingBar.drawing) return;
    drawingBar.key = key;
    drawingBar.drawing = drawing;
    if (!target) {
      if (drawingBar.el) drawingBar.el.classList.add('hidden');
      return;
    }
    if (!drawingBar.el) {
      drawingBar.el = el('div', 'fav-toolbar drawing-bar');
      document.body.appendChild(drawingBar.el);
    }
    const bar = drawingBar.el;
    bar.replaceChildren();
    if (drawing) fillSelectionBar(bar, s, drawing);
    else fillToolBar(bar, s, target.kind);
    bar.classList.remove('hidden');
    positionDrawingBar();
  }

  // Tlačítko lišty kresby (ikona, tooltip); handler dostane tlačítko jako
  // kotvu nabídky. stopPropagation: globální klik by nabídku hned zavřel
  function barButton(bar, icon, title, onClick) {
    const button = el('button', 'fav-tool drawing-bar-button');
    button.type = 'button';
    button.title = title;
    button.appendChild(icon);
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      onClick(button);
    });
    bar.appendChild(button);
    return button;
  }

  // Ikona s barevným proužkem pod ní (barva čáry / textu jako v TradingView)
  function colorIcon(iconName, color) {
    const node = el('span', 'drawing-bar-color');
    node.appendChild(toolIcon(iconName));
    const strip = el('span', 'drawing-bar-colorbar');
    strip.style.background = color;
    node.appendChild(strip);
    return node;
  }

  // SVG náhled čáry dané šířky a stylu (tlačítka šířky a stylu čáry)
  function linePreview(width, lineStyle) {
    const node = el('span', 'tv-icon-wrap');
    const dash = (LINE_DASHES[lineStyle] || []).join(' ');
    node.innerHTML = '<svg viewBox="0 0 28 28" fill="none" stroke="currentColor"><path d="M4 14h20" stroke-width="' +
      (Number(width) || 1) + '"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + '/></svg>';
    return node;
  }

  // Nabídka pod tlačítkem lišty (položky viz fillContextMenu)
  function showMenuBelow(button, items) {
    const rect = button.getBoundingClientRect();
    showContextMenu(rect.left, rect.bottom + 4, items);
  }

  // Událost pro Python s identitou a stylem kresby (extra = typ a akce)
  function drawingPayload(drawing, extra) {
    return Object.assign({
      id: drawing.id,
      kind: drawing.kind,
      color: drawing.color,
      text: drawing.text || null,
      options: drawing.options || {},
    }, extra);
  }

  // Uloží styl vybrané kresby změněný v liště (graf ho už zobrazuje)
  function queueDrawingStyle(s, drawing) {
    s.finishedQueue.push(drawingPayload(drawing, { type: 'drawing_style' }));
  }

  // Změní jednu volbu stylu vybrané kresby (šířka, styl čáry) a uloží ji
  function setDrawingOption(s, drawing, key, value) {
    drawing.options = Object.assign({}, drawing.options, { [key]: value });
    redrawOverlay(s);
    queueDrawingStyle(s, drawing);
    syncDrawingBar(true);
  }

  // Výběr barvy z lišty: náhled hned v grafu i v proužku tlačítka, uložení
  // po zavření panelu (jen při změně); druhý klik na tlačítko panel zavře
  function pickDrawingColor(s, drawing, anchor, read, write) {
    const picker = window.TickViewColorPicker;
    if (!picker) return;
    if (picker.isOpenFor(anchor)) {
      picker.close();
      return;
    }
    const before = read();
    const strip = anchor.querySelector('.drawing-bar-colorbar');
    picker.open(
      anchor,
      before,
      (color) => {
        write(color);
        if (strip) strip.style.background = color;
        redrawOverlay(s);
      },
      () => {
        if (read() !== before) queueDrawingStyle(s, drawing);
      }
    );
  }

  // Lišta vybrané kresby: šablony, barva čáry a textu, šířka a styl čáry,
  // Settings, alert, smazání a další volby (menu pravého kliku)
  function fillSelectionBar(bar, s, drawing) {
    const kind = drawing.kind;
    const options = drawing.options || {};
    const hasLine = drawingConfig.lineKinds.includes(kind);
    if (drawingConfig.kinds.includes(kind)) {
      barButton(bar, toolIcon('templates'), 'Templates', (button) => showMenuBelow(button, drawingTemplateItems(s, drawing, true)));
    }
    // Barva kresby: u čar barva čáry, u textu barva textu
    if (hasLine || kind === 'text') {
      barButton(bar, colorIcon(hasLine ? 'pencil' : 'text', drawing.color), hasLine ? 'Line color' : 'Text color', (button) =>
        pickDrawingColor(s, drawing, button, () => drawing.color, (color) => { drawing.color = color; })
      );
    }
    if (drawingConfig.labelKinds.includes(kind)) {
      // Barva popisku čáry (bez vlastní barvy má barvu čáry)
      const textColor = () => (drawing.options || {}).textColor || drawing.color;
      barButton(bar, colorIcon('text', textColor()), 'Text color', (button) =>
        pickDrawingColor(s, drawing, button, textColor, (color) => {
          drawing.options = Object.assign({}, drawing.options, { textColor: color });
        })
      );
    }
    // Šířka a styl čáry s nabídkou hodnot (aktuální má fajfku)
    if (hasLine) {
      const width = Number(options.width) || 1.5;
      const widthButton = barButton(bar, linePreview(Math.min(width, 3), 'solid'), 'Line width', (button) =>
        showMenuBelow(button, drawingConfig.widths.map(([value, label]) =>
          [label, () => setDrawingOption(s, drawing, 'width', value), Number(value) === width ? 'check' : '']))
      );
      widthButton.classList.add('drawing-bar-width');
      widthButton.appendChild(el('span', 'drawing-bar-label', width + 'px'));
      const lineStyle = options.lineStyle || 'solid';
      barButton(bar, linePreview(1.5, lineStyle), 'Line style', (button) =>
        showMenuBelow(button, drawingConfig.lineStyles.map(([value, label]) =>
          [label, () => setDrawingOption(s, drawing, 'lineStyle', value), value === lineStyle ? 'check' : '']))
      );
    }
    barButton(bar, toolIcon('settings'), 'Settings…', () => requestDrawingSettings(s, drawing));
    if (alertLineKinds.includes(kind)) {
      barButton(bar, toolIcon('alarm_add'), tvTr('Přidat alert…'), () => requestDrawingAlert(s, drawing));
    }
    barButton(bar, toolIcon('trash'), tvTr('Smazat objekt'), () => deleteSelectedDrawing(s));
    barButton(bar, toolIcon('more'), 'More', (button) => showMenuBelow(button, drawingMenuItems(s, drawing)));
  }

  // Lišta aktivního nástroje: výběr šablony pro další nové objekty; zvolená
  // (ne výchozí) šablona je na tlačítku zvýrazněná a v tooltipu
  function fillToolBar(bar, s, kind) {
    const chosen = toolTemplate[kind];
    const button = barButton(bar, toolIcon('templates'), chosen ? 'Templates: ' + chosen : 'Templates', (anchor) =>
      showMenuBelow(anchor, toolTemplateItems(s, kind))
    );
    button.classList.toggle('active', !!chosen);
  }

  // Nabídka šablon aktivního nástroje: výchozí šablona typu a uložené šablony
  // (zvolená má fajfku, koš ji smaže); volba platí pro další nové objekty
  function toolTemplateItems(s, kind) {
    const choose = (name) => () => {
      if (name) toolTemplate[kind] = name;
      else delete toolTemplate[kind];
      // Překreslení ukáže rozepsanou kresbu v novém stylu a přestaví lištu
      // (zvolená šablona je v jejím klíči)
      redrawOverlay(s);
    };
    const items = [['Apply Default Drawing Template', choose(null)]];
    const names = Object.keys(templatesOf(kind));
    if (names.length) items.push(null);
    for (const name of names) {
      items.push([name, choose(name), toolTemplate[kind] === name ? 'check' : '', '', null, deleteTemplateAction(s, kind, name)]);
    }
    return items;
  }

  // Umístí lištu kresby pod panel oblíbených (bez něj nahoře uprostřed
  // plochy grafů); vždy celou uvnitř okna
  function positionDrawingBar() {
    const bar = drawingBar.el;
    if (!bar || bar.classList.contains('hidden')) return;
    let left;
    let top;
    if (fav.el && !fav.el.classList.contains('hidden')) {
      const rect = fav.el.getBoundingClientRect();
      left = rect.left;
      top = rect.bottom + 4;
    } else {
      const rect = chartAreaRect();
      left = rect.left + (rect.width - bar.offsetWidth) / 2;
      top = rect.top + 6;
    }
    const maxLeft = Math.max(0, window.innerWidth - bar.offsetWidth);
    const maxTop = Math.max(0, window.innerHeight - bar.offsetHeight);
    bar.style.left = Math.round(Math.min(maxLeft, Math.max(0, left))) + 'px';
    bar.style.top = Math.round(Math.min(maxTop, Math.max(0, top))) + 'px';
  }

  // SVG ikony nástrojů z Pythonu (posílají se jednou, i před init grafu)
  function setIcons(id, icons) {
    toolIcons = icons || {};
  }

  // Druhy kreseb s nabídkou „Přidat alert…“ (posílají se jednou, i před init grafu)
  function setAlertKinds(id, kinds) {
    alertLineKinds = Array.isArray(kinds) ? kinds : [];
  }

  // Položka flyout nabídky (ikona + popisek); klik pošle událost a zavře nabídku
  function flyoutItem(icon, label, active, event, s) {
    const item = el('div', 'tool-flyout-item' + (active ? ' active' : ''));
    item.appendChild(toolIcon(icon, 'tool-flyout-icon'));
    item.appendChild(el('span', '', label));
    item.addEventListener('click', () => {
      s.finishedQueue.push(event);
      closeFlyout();
    });
    return item;
  }

  // Umístí flyout nabídku vpravo vedle tlačítka lišty
  function placeFlyout(flyout, anchorButton) {
    const area = anchorButton.closest('.chart-area');
    flyout.style.left = anchorButton.offsetLeft + anchorButton.offsetWidth + 2 + 'px';
    flyout.style.top = anchorButton.offsetTop + 'px';
    area.appendChild(flyout);
  }

  // Režimy magnetu pro nabídku (id, ikona, popis)
  const MAGNET_MODES = [
    ['weak', 'magnet', tvTr('Slabý magnet – přichytí bod k O/H/L/C, když je kurzor blízko')],
    ['strong', 'magnet', tvTr('Silný magnet – bod vždy na nejbližší O/H/L/C svíčky')],
    ['off', 'magnet_off', tvTr('Vypnout magnet')],
  ];

  // Flyout s režimy magnetu; výběr jde přes frontu do Pythonu (uloží se)
  function openMagnetFlyout(s, anchorButton) {
    closeFlyout();
    const flyout = el('div', 'tool-flyout');
    for (const [mode, icon, label] of MAGNET_MODES) {
      flyout.appendChild(flyoutItem(icon, label, s.overlay.magnet === mode, { type: 'action', action: 'magnet', mode: mode }, s));
    }
    const hint = el('div', 'tool-flyout-hint', tvTr('Podržením Ctrl/⌘ při kreslení magnet dočasně přepnete.'));
    flyout.appendChild(hint);
    placeFlyout(flyout, anchorButton);
  }

  // Zavře otevřenou flyout nabídku (pokud existuje)
  function closeFlyout() {
    document.querySelectorAll('.tool-flyout').forEach((flyout) => flyout.remove());
  }

  // Geometrie cenové plochy grafu vůči hostu: vlevo případná levá cenová osa,
  // vpravo pravá osa, dole časová osa (rozměry bere přímo z API grafu)
  function getPaneGeometry(s) {
    const hostRect = s.mainHost.getBoundingClientRect();
    const timeScale = s.chart.timeScale();
    return {
      hostLeft: hostRect.left,
      hostTop: hostRect.top,
      offsetX: s.chart.priceScale('left').width(),
      offsetY: 0,
      width: timeScale.width(),
      height: Math.max(0, hostRect.height - timeScale.height()),
    };
  }

  // Převede (čas, cena) na pixely v souřadnicích plátna grafu
  // Kresby ukládají čas v sekundách UTC; graf ale umí převést na pixely jen
  // časy existujících svíček. Čas proto převádíme na logický index (pořadí
  // svíčky): uvnitř dat lineárně mezi sousedními svíčkami (díry – víkend,
  // noc), mimo data (vpravo do budoucna, vlevo do minulosti) krokem timeframu.

  // Čas (s) → logický index; null, když graf nemá data
  function timeToLogical(s, time) {
    const candles = s.candles;
    const n = candles.length;
    if (!n) return null;
    if (time <= candles[0].time) return (time - candles[0].time) / s.stepSeconds;
    if (time >= candles[n - 1].time) return n - 1 + (time - candles[n - 1].time) / s.stepSeconds;
    // Binární hledání svíček lo/hi, mezi kterými čas leží
    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (candles[mid].time <= time) lo = mid;
      else hi = mid;
    }
    return lo + (time - candles[lo].time) / (candles[hi].time - candles[lo].time);
  }

  // Logický index → čas (s, celé číslo); inverze timeToLogical
  function logicalToTime(s, logical) {
    const candles = s.candles;
    const n = candles.length;
    if (!n) return null;
    if (logical <= 0) return Math.round(candles[0].time + logical * s.stepSeconds);
    if (logical >= n - 1) return Math.round(candles[n - 1].time + (logical - (n - 1)) * s.stepSeconds);
    const lo = Math.floor(logical);
    const fraction = logical - lo;
    return Math.round(candles[lo].time + fraction * (candles[lo + 1].time - candles[lo].time));
  }

  // Čas → x souřadnice v plátně grafu (i mimo existující svíčky)
  // Lightweight Charts převádí spolehlivě jen celé logické indexy (zlomkový
  // index vrací chybně) – souřadnici zlomku proto interpolujeme mezi
  // sousedními celými indexy (čas kresby z 1h grafu uvnitř denní svíčky)
  function timeToX(s, time) {
    const logical = timeToLogical(s, time);
    if (logical === null) return null;
    const timeScale = s.chart.timeScale();
    const base = Math.floor(logical);
    const left = timeScale.logicalToCoordinate(base);
    if (left === null || logical === base) return left;
    const right = timeScale.logicalToCoordinate(base + 1);
    return right === null ? left : left + (right - left) * (logical - base);
  }

  // Převede (čas, cena) na pixely v souřadnicích plátna grafu
  function pointToPixel(s, point) {
    const px = timeToX(s, point.time);
    const py = s.candleSeries.priceToCoordinate(point.price);
    return px === null || py === null ? null : { x: px, y: py };
  }

  // Převod pixel → (čas, cena) bez přichycení (štětec)
  function pixelToPoint(s, x, y) {
    const logical = s.chart.timeScale().coordinateToLogical(x);
    const price = s.candleSeries.coordinateToPrice(y);
    if (logical === null || price === null) return null;
    const time = logicalToTime(s, logical);
    return time === null ? null : { time: time, price: Number(price) };
  }

  // Tolerance slabého magnetu v pixelech (vzdálenost kurzoru od O/H/L/C)
  const WEAK_MAGNET_PX = 25;

  // Režim magnetu pro danou událost myši: Ctrl/⌘ ho dočasně přepne
  // (vypnutý → silný, zapnutý → vypnutý), jako v TradingView
  function effectiveMagnet(s, event) {
    const mode = s.overlay.magnet;
    if (event && (event.ctrlKey || event.metaKey)) return mode === 'off' ? 'strong' : 'off';
    return mode;
  }

  // Převod pixel → (čas, cena): čas se přichytí na nejbližší bar (i vpravo za
  // daty); s magnetem se cena přichytí k nejbližší OHLC hodnotě svíčky –
  // silný vždy, slabý jen v toleranci WEAK_MAGNET_PX. Výsledek má příznak
  // snapped (bod je přichycený) pro značku magnetu.
  function snapPoint(s, x, y, magnet) {
    const logical = s.chart.timeScale().coordinateToLogical(x);
    let price = s.candleSeries.coordinateToPrice(y);
    if (logical === null || price === null || !s.candles.length) return null;
    const index = Math.round(logical);
    const candle = s.candles[index];
    let snapped = false;
    if (magnet && magnet !== 'off' && candle) {
      // Nejbližší OHLC hodnota svíčky pod kurzorem
      let best = null;
      for (const candidate of [candle.open, candle.high, candle.low, candle.close]) {
        const py = s.candleSeries.priceToCoordinate(candidate);
        if (py === null) continue;
        if (!best || Math.abs(py - y) < Math.abs(best.py - y)) best = { price: candidate, py: py };
      }
      if (best && (magnet === 'strong' || Math.abs(best.py - y) <= WEAK_MAGNET_PX)) {
        price = best.price;
        snapped = true;
      }
    }
    return { time: logicalToTime(s, index), price: Number(price), snapped: snapped };
  }

  // Bod kresby bez pomocných příznaků (do kreseb a databáze jen čas + cena)
  function cleanPoint(point) {
    return { time: point.time, price: point.price };
  }

  // Posune crosshair grafu na bod (jen u existující svíčky – LWC jinak neumí)
  function moveCrosshair(s, point) {
    if (typeof s.chart.setCrosshairPosition !== 'function') return;
    if (s.byTime.has(point.time)) {
      s.chart.setCrosshairPosition(point.price, point.time, s.candleSeries);
      syncPaneCrosshairs(s, s.chart, point.time);
    } else clearCrosshair(s);
  }

  // Skryje ručně nastavený crosshair grafu (i svislou čáru v panelech)
  function clearCrosshair(s) {
    if (typeof s.chart.clearCrosshairPosition !== 'function') return;
    s.chart.clearCrosshairPosition();
    for (const pane of s.panesMap.values()) pane.chart.clearCrosshairPosition();
  }

  // Událost crosshairMove od uživatele (pohyb myši, odchod z grafu)? LWC
  // po změně dat / voleb znovu vyšle událost i za programově nastavený
  // crosshair (bez sourceEvent) – takový graf nesmí převzít roli zdroje
  function isUserCrosshairMove(param) {
    return param.time === undefined || !!param.sourceEvent;
  }

  // Pohyb crosshairu v hlavním grafu nebo panelu (source) grafu s: legenda
  // ukazuje svíčku pod kurzorem (mimo graf poslední svíčku), svislá čára se
  // promítne do ostatních grafů s a případně do ostatních grafů rozložení
  function onStackCrosshairMove(s, source, param) {
    if (!isUserCrosshairMove(param)) {
      // Znovu vyslaný programový crosshair: hlavní graf jen obnoví legendu
      // (živá svíčka pod kurzorem), roli zdroje nepřebírá
      if (source === s.chart) renderHoverLegend(s, param.time);
      return;
    }
    // Pohyb uvnitř stejné svíčky (i svislý) nic nového neukáže
    const last = s.lastCrosshair;
    if (last && last.source === source && last.time === param.time) return;
    s.lastCrosshair = { source: source, time: param.time };
    renderHoverLegend(s, param.time);
    syncPaneCrosshairs(s, source, param.time);
    syncCrosshair(s, param);
  }

  // Legenda se svíčkou v čase time (undefined / bez svíčky = poslední svíčka)
  function renderHoverLegend(s, time) {
    const bar = time === undefined ? null : s.byTime.get(time) || null;
    s.hovering = bar !== null;
    renderLegend(s, bar || s.lastCandle);
  }

  // Grafy (hlavní / panely) s dočasně skrytou vodorovnou čarou crosshairu
  const hiddenHorzLines = new WeakSet();

  // Vodorovná čára crosshairu: jen v grafu pod kurzorem, převzatý crosshair
  // v hlavním grafu a panelech ukazuje jen svislou čáru času (applyOptions
  // se volá jen při změně, ne při každém pohybu myši)
  function setHorzLineVisible(chart, visible) {
    if (hiddenHorzLines.has(chart) !== visible) return;
    chart.applyOptions({ crosshair: { horzLine: { visible: visible, labelVisible: visible } } });
    if (visible) hiddenHorzLines.delete(chart);
    else hiddenHorzLines.add(chart);
  }

  // Svislá čára crosshairu přes hlavní graf a panely indikátorů: čas pod
  // kurzorem ve zdrojovém grafu (source) se ukáže v ostatních grafech
  // grafu s. Programové nastavení crosshairu v LWC událost crosshairMove
  // nevyvolá, takže zpětná vazba nevzniká; time undefined = schovat
  function syncPaneCrosshairs(s, source, time) {
    if (typeof s.chart.setCrosshairPosition !== 'function') return;
    setHorzLineVisible(source, true);
    // Hlavní graf (zdrojem je panel): jen na existující svíčce
    if (source !== s.chart) {
      const bar = time === undefined ? null : s.byTime.get(time);
      if (bar) {
        setHorzLineVisible(s.chart, false);
        s.chart.setCrosshairPosition(bar.close, time, s.candleSeries);
      } else {
        s.chart.clearCrosshairPosition();
      }
    }
    for (const pane of s.panesMap.values()) {
      if (pane.chart === source) continue;
      const series = pane.series[0] || pane.background;
      if (time === undefined || !series) {
        pane.chart.clearCrosshairPosition();
        continue;
      }
      setHorzLineVisible(pane.chart, false);
      // Cena je lhostejná (vodorovná čára je skrytá); série bez dat LWC odmítne výjimkou
      try {
        pane.chart.setCrosshairPosition(0, time, series);
      } catch (err) {
        pane.chart.clearCrosshairPosition();
      }
    }
  }

  // Ukončí aktivní nástroj (Esc, pravý klik, vypnutí tužky, opakovaný klik
  // na nástroj): zruší rozepsanou kresbu a vrátí kurzor, Python zvýrazní lištu
  function exitTool(s) {
    s.overlay.pending = null;
    s.overlay.hover = null;
    s.measure = null;
    clearCrosshair(s);
    setDrawingMode(s.elementId, null);
    s.finishedQueue.push({ type: 'tool', mode: null });
  }

  // Provede změnu dat bez synchronizace rozsahu mezi grafy: panel se starými
  // daty (jiná délka historie) by jinak hlavní graf odsunul mimo data
  function withoutSync(s, fn) {
    const previous = s.syncing;
    s.syncing = true;
    try {
      fn();
    } finally {
      s.syncing = previous;
    }
  }

  // Srovná všechny panely indikátorů na aktuální rozsah hlavního grafu
  function alignPanesToMain(s) {
    const range = s.chart.timeScale().getVisibleLogicalRange();
    if (!range) return;
    withoutSync(s, () => {
      for (const pane of s.panesMap.values()) pane.chart.timeScale().setVisibleLogicalRange(range);
    });
  }

  // Hlavní graf a grafy panelů indikátorů grafu s
  function stackCharts(s) {
    return [s.chart].concat(Array.from(s.panesMap.values(), (pane) => pane.chart));
  }

  // Stejná šířka pravé cenové osy hlavního grafu a panelů: každý graf má
  // jinak široké popisky (120.00 vs −80 000.00), takže by časová plocha
  // začínala jinde a svíčky i svislá čára crosshairu by pod sebou neseděly.
  // Osy se srovnají na nejširší (minimumWidth), nejvýš jednou za snímek
  function scheduleScaleAlign(s) {
    if (s.scaleAlignPending) return;
    s.scaleAlignPending = true;
    requestAnimationFrame(() => {
      s.scaleAlignPending = false;
      alignScaleWidths(s);
    });
  }

  // Srovná šířky pravých cenových os na nejširší (minimumWidth jen roste –
  // zúžit osy dovolí až resetScaleWidths)
  function alignScaleWidths(s) {
    // Graf mezitím zmizel (odstraněný graf by na volání LWC spadl)
    if (state.get(s.elementId) !== s || !s.panesMap.size) return;
    const charts = stackCharts(s);
    const widths = charts.map((chart) => chart.priceScale('right').width());
    const widest = Math.max(...widths);
    if (!widest || widths.every((width) => width === widest)) return;
    for (const chart of charts) chart.applyOptions({ rightPriceScale: { minimumWidth: widest } });
    s.alignedScaleWidth = widest;
  }

  // Zapomene srovnanou šířku os (nový symbol / nová sada panelů – osy se
  // smí zúžit) a srovná je znovu
  function resetScaleWidths(s) {
    if (s.alignedScaleWidth) {
      for (const chart of stackCharts(s)) chart.applyOptions({ rightPriceScale: { minimumWidth: 0 } });
      s.alignedScaleWidth = 0;
    }
    scheduleScaleAlign(s);
  }

  // Synchronizuje viditelný rozsah mezi hlavním grafem a panely indikátorů
  // Panel smí rozsah předat dál jen při interakci uživatele (myš nad
  // panelem); jeho asynchronní události po výměně dat by jinak hlavní graf
  // posunuly na starý rozsah
  function syncRange(s, source, range) {
    if (s.syncing || !range) return;
    if (source !== s.chart && s.paneUnderPointer !== source) return;
    s.syncing = true;
    try {
      if (s.chart !== source) s.chart.timeScale().setVisibleLogicalRange(range);
      for (const pane of s.panesMap.values()) {
        if (pane.chart !== source) pane.chart.timeScale().setVisibleLogicalRange(range);
      }
    } finally {
      s.syncing = false;
    }
  }

  // Odstraní overlay čáry i panely indikátorů
  function clearIndicators(id) {
    const s = state.get(id);
    if (!s) return;
    for (const series of s.overlaySeries) s.chart.removeSeries(series);
    s.overlaySeries = [];
    for (const pane of s.panesMap.values()) {
      try {
        pane.chart.remove();
      } catch (err) {
        // Graf už byl odstraněn – nic nerozbíjíme
      }
      pane.wrap.remove();
    }
    s.panesMap.clear();
    s.paneUnderPointer = null;
  }

  // Je výstup indikátoru ve formátu objemu (indikátor Volume)?
  function isVolumeFormat(spec) {
    const format = spec.options && spec.options.priceFormat;
    return !!format && format.type === 'volume';
  }

  // Volby série indikátoru: volby z payloadu (barva, tloušťka, styl, popisek na
  // ose, cenová linka podle záložky Style), viditelnost skupiny i výstupu a formát cen
  function indicatorSeriesOptions(spec, visible, priceFormat) {
    // Okraje cenové osy (scaleMargins) nejsou volbou série – nastaví je createIndicatorSeries
    const { scaleMargins, ...seriesOptions } = spec.options || {};
    const options = Object.assign(seriesOptions, { visible: visible && spec.visible !== false });
    // Plocha: výplň z barvy čáry, průhlednost klesá k dolnímu okraji
    if (spec.series === 'area' && spec.color) {
      options.topColor = withAlpha(spec.color, AREA_FILL_ALPHA);
      options.bottomColor = withAlpha(spec.color, 0);
    }
    // Formát deklarovaný výstupem (objem) má přednost před přesností ze záložky Style
    if (priceFormat && !seriesOptions.priceFormat) options.priceFormat = priceFormat;
    return options;
  }

  // Vytvoří sérii indikátoru v daném grafu podle druhu série z payloadu:
  // sloupce (Histogram, Columns), plocha (Area) nebo čára (čáry, schody, body);
  // starší payload bez druhu série rozlišuje jen histogram a čáru
  function createIndicatorSeries(chart, spec, visible, priceFormat) {
    const options = indicatorSeriesOptions(spec, visible, priceFormat);
    const kind = spec.series || (spec.type === 'histogram' ? 'histogram' : 'line');
    const base = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    let series;
    if (kind === 'histogram') {
      series = chart.addHistogramSeries(Object.assign({ lastValueVisible: false, priceLineVisible: false }, options));
    } else if (kind === 'area') {
      series = chart.addAreaSeries(Object.assign(base, options));
    } else {
      series = chart.addLineSeries(Object.assign(base, { color: spec.color }, options));
    }
    // Série na vlastní ose (objem v dolní části grafu): okraje osy z deklarace
    const margins = spec.options && spec.options.scaleMargins;
    if (margins && options.priceScaleId) chart.priceScale(options.priceScaleId).applyOptions({ scaleMargins: margins });
    return series;
  }

  // Viditelnost skupiny indikátoru podle payloadu (skrytý = oko v legendě)
  function groupVisible(payload, groupId) {
    const group = (payload.groups || []).find((g) => g.id === groupId);
    return !group || group.visible !== false;
  }

  // Nejmenší výška panelu indikátoru a hlavního grafu při tažení hrany panelu (px)
  const MIN_PANE_HEIGHT = 40;
  const MIN_MAIN_HEIGHT = 80;
  // Klíč uložené výšky panelu v localStorage (za prefixem je id instance indikátoru)
  const PANE_HEIGHT_PREFIX = 'tickview.indicatorPane.';

  // Výška panelu indikátoru přes CSS proměnnou (null = výchozí výška z CSS podle typu)
  function setPaneHeight(wrap, height) {
    if (height === null) {
      wrap.classList.remove('lwchart-pane-sized');
      wrap.style.removeProperty('--lwchart-pane-height');
      return;
    }
    wrap.style.setProperty('--lwchart-pane-height', height + 'px');
    wrap.classList.add('lwchart-pane-sized');
  }

  // Uložená výška panelu instance indikátoru (null = výchozí); bez úložiště null
  function storedPaneHeight(group) {
    try {
      const height = Number(window.localStorage.getItem(PANE_HEIGHT_PREFIX + group));
      return height > 0 ? height : null;
    } catch (err) {
      return null;
    }
  }

  // Zapamatuje výšku panelu (null = smaže, panel bude mít výchozí výšku)
  function storePaneHeight(group, height) {
    try {
      if (height === null) window.localStorage.removeItem(PANE_HEIGHT_PREFIX + group);
      else window.localStorage.setItem(PANE_HEIGHT_PREFIX + group, String(height));
    } catch (err) {
      // Soukromý režim / zakázané úložiště – výška platí jen do obnovení stránky
    }
  }

  // Táhlo na horní hraně panelu indikátoru (jako v TradingView): tažení nahoru
  // panel zvětší na úkor prvku nad ním – hlavního grafu (do MIN_MAIN_HEIGHT),
  // nebo panelu nad ním (do MIN_PANE_HEIGHT, součet výšek zůstane); dvojklik
  // vrátí výchozí výšku. Grafy se přizpůsobí samy (autoSize); jádro tažení
  // je společné s ostatními hranami panelů (resizers.js)
  function attachPaneResizer(wrap, group) {
    const resizer = el('div', 'pane-resizer pane-resizer-top');
    wrap.appendChild(resizer);
    resizer.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      const above = wrap.previousElementSibling;
      const abovePane = above && above.classList.contains('lwchart-pane') ? above : null;
      const startHeight = wrap.getBoundingClientRect().height;
      const aboveHeight = above ? above.getBoundingClientRect().height : 0;
      const total = startHeight + aboveHeight;
      const maxHeight = total - (abovePane ? MIN_PANE_HEIGHT : MIN_MAIN_HEIGHT);
      let height = startHeight;
      window.TickViewResize.drag(
        resizer,
        event,
        true,
        (delta) => {
          // Hrana je nahoře: posun nahoru panel zvětšuje; hlavní graf se přizpůsobí flexem
          height = Math.round(Math.max(MIN_PANE_HEIGHT, Math.min(startHeight - delta, Math.max(startHeight, maxHeight))));
          setPaneHeight(wrap, height);
          if (abovePane) setPaneHeight(abovePane, total - height);
        },
        () => {
          storePaneHeight(group, height);
          if (abovePane) storePaneHeight(abovePane.dataset.group, total - height);
        }
      );
    });
    // Dvojklik na hranu = výchozí výška (dvojklik v panelu jinak otevře Settings)
    resizer.addEventListener('dblclick', (event) => {
      event.stopPropagation();
      setPaneHeight(wrap, null);
      storePaneHeight(group, null);
    });
  }

  // Posun myši (px), od kterého je stisk na titulku panelu tažením, ne klikem
  const PANE_DRAG_THRESHOLD = 5;

  // Přeřazení panelu indikátoru tažením za titulek: po posunu o pár px se
  // panel zprůhlední a čára ukáže nové místo; po puštění pošle Pythonu
  // {action: 'move', id, before} (before = skupina panelu, před který se
  // panel vloží, null = na konec). Stisk bez tažení zůstane klikem (označení,
  // dvojklik na Settings), tlačítka řádku legendy se netáhnou. Sdílené
  // TickViewResize.drag se nehodí – pointer capture hned při stisku by
  // klik a dvojklik na řádku legendy zrušil
  function attachPaneReorder(s, wrap, title, group) {
    title.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || event.target.closest('.legend-ind-btn')) return;
      const startY = event.clientY;
      // Panely a jejich středy (během tažení se nehýbou) se změří při začátku tažení
      let wraps = null;
      let middles = null;
      // Panel, před který se vloží (null = na konec); undefined = pořadí beze změny
      let before;
      const move = (moveEvent) => {
        // Tažení začne až po prahu (jinak by se z kliku stalo přeřazení)
        if (!wraps) {
          if (Math.abs(moveEvent.clientY - startY) < PANE_DRAG_THRESHOLD) return;
          wraps = paneWraps(s);
          middles = wraps.map((node) => {
            const rect = node.getBoundingClientRect();
            return rect.top + rect.height / 2;
          });
          wrap.classList.add('pane-dragging');
          document.body.classList.add('pane-reordering');
          const selection = window.getSelection();
          if (selection) selection.removeAllRanges();
        }
        const target = paneDropTarget(wraps, middles, wrap, moveEvent.clientY);
        if (target !== before) {
          before = target;
          showPaneDrop(wraps, before);
        }
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', up);
        if (!wraps) return;
        wrap.classList.remove('pane-dragging');
        document.body.classList.remove('pane-reordering');
        showPaneDrop(wraps, undefined);
        // Klik, který po puštění tlačítka následuje, už nic neoznačuje
        // (po pointercancel klik nepřijde – zachytávání se po chvilce zruší)
        const swallowClick = (clickEvent) => clickEvent.stopPropagation();
        window.addEventListener('click', swallowClick, { capture: true, once: true });
        setTimeout(() => window.removeEventListener('click', swallowClick, true), 0);
        if (before === undefined) return;
        s.finishedQueue.push({ type: 'indicator', action: 'move', id: group, before: before ? before.dataset.group : null });
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    });
  }

  // Panely indikátorů grafu s v pořadí zobrazení (DOM)
  function paneWraps(s) {
    return Array.from(s.panes.children).filter((node) => node.classList.contains('lwchart-pane'));
  }

  // Panel, před který se tažený panel wrap vloží: první, jehož střed je pod
  // kurzorem (null = za poslední panel); undefined, když se pořadí nezmění
  // (vložení před sebe nebo před následující panel)
  function paneDropTarget(wraps, middles, wrap, clientY) {
    const index = middles.findIndex((middle) => clientY < middle);
    const before = index < 0 ? null : wraps[index];
    const next = wraps[wraps.indexOf(wrap) + 1] || null;
    return before === wrap || before === next ? undefined : before;
  }

  // Čára nového místa taženého panelu: horní hrana panelu before, null =
  // spodní hrana posledního panelu, undefined = skrýt
  function showPaneDrop(wraps, before) {
    for (const node of wraps) node.classList.remove('pane-drop-before', 'pane-drop-after');
    if (before) before.classList.add('pane-drop-before');
    else if (before === null && wraps.length) wraps[wraps.length - 1].classList.add('pane-drop-after');
  }

  // Vytvoří panel indikátoru (oscilátory, objem…) pod hlavním grafem se
  // synchronizovaným časem; titulek panelu nese řádek legendy indikátoru
  function addIndicatorPane(s, pane, theme, visible) {
    const wrap = el('div', 'lwchart-pane lwchart-pane-' + pane.kind);
    wrap.dataset.group = pane.group;
    setPaneHeight(wrap, storedPaneHeight(pane.group));
    attachPaneResizer(wrap, pane.group);
    const title = el('div', 'lwchart-pane-title', pane.title);
    attachPaneReorder(s, wrap, title, pane.group);
    const host = el('div', 'lwchart-pane-host');
    // Canvas koleček zvýrazněného / označeného indikátoru nad panelem
    const markers = el('canvas', 'lwchart-pane-markers');
    host.appendChild(markers);
    wrap.appendChild(title);
    wrap.appendChild(host);
    s.panes.appendChild(wrap);

    const chart = LightweightCharts.createChart(host, {
      autoSize: true,
      layout: theme.layout,
      grid: theme.grid,
      rightPriceScale: { borderColor: theme.border },
      // Časová osa je vidět jen v hlavním grafu; panely se synchronizují rozsahem
      timeScale: { borderColor: theme.border, visible: false },
      crosshair: crosshairLineOptions(s),
    });

    const priceFormat = panePriceFormat(s, pane);
    // Pozadí panelu (bgcolor indikátoru): sloupce na vlastní skryté ose přes
    // celou výšku, vytvořené před ostatními sériemi, aby byly pod nimi
    let background = null;
    if (pane.background) {
      background = chart.addHistogramSeries({
        priceScaleId: 'background',
        lastValueVisible: false,
        priceLineVisible: false,
        visible: visible,
      });
      chart.priceScale('background').applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
      background.setData(pane.background);
    }
    const created = [];
    let baseSeries = null;
    for (const spec of pane.series) {
      const series = createIndicatorSeries(chart, spec, visible, priceFormat);
      series.setData(spec.data);
      if (!baseSeries && spec.series !== 'histogram' && spec.type !== 'histogram') baseSeries = series;
      created.push(series);
    }

    // Úrovně (30/70 u RSI, 0 u MACD…) jako přerušované cenové linky
    const levelColor = s.themeName === 'dark' ? 'rgba(209, 212, 220, 0.4)' : 'rgba(19, 23, 34, 0.4)';
    for (const level of pane.levels || []) {
      const target = baseSeries || created[0];
      if (target) {
        target.createPriceLine({
          price: level,
          color: levelColor,
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: false,
        });
      }
    }

    // Posun panelu jde přes syncRange do hlavního grafu, jehož odběr rozsahu
    // překreslí overlay i kolečka v panelech
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => syncRange(s, chart, range));
    // Najetí na čáru indikátoru v panelu ji zvýrazní, klik na ni ho označí
    // (klik jinam v panelu označení zruší)
    const paneHit = (event) => {
      const rect = host.getBoundingClientRect();
      const entry = s.panesMap.get(pane.paneId);
      if (!entry) return null;
      return hitIndicatorEntries(s, entry.chart, paneMarkerEntries(pane, entry), event.clientX - rect.left, event.clientY - rect.top);
    };
    host.addEventListener('mousemove', (event) => throttleHover(s, () => setIndicatorHover(s, paneHit(event))));
    host.addEventListener('mouseleave', () => throttleHover(s, () => setIndicatorHover(s, null)));
    host.addEventListener(
      'mousedown',
      (event) => {
        if (event.button !== 0) return;
        if (s.overlay.selected >= 0) {
          selectDrawing(s, -1);
          scheduleOverlayRedraw(s);
        }
        selectIndicator(s, paneHit(event));
      },
      true
    );
    // Pravý klik a dvojklik v panelu patří indikátoru panelu (Settings…)
    wrap.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      selectIndicator(s, pane.group);
      showContextMenu(event.clientX, event.clientY, indicatorMenuItems(s, pane.group));
    });
    wrap.addEventListener('dblclick', () => s.finishedQueue.push({ type: 'indicator', action: 'settings', id: pane.group }));
    // Panel pod kurzorem (jen ten smí posouvat hlavní graf – viz syncRange)
    wrap.addEventListener('mouseenter', () => (s.paneUnderPointer = chart));
    wrap.addEventListener('mouseleave', () => {
      if (s.paneUnderPointer === chart) s.paneUnderPointer = null;
    });
    // Pohyb kurzoru v panelu: svislá čára času v hlavním grafu i ostatních
    // panelech a legenda se svíčkou pod kurzorem (jako v hlavním grafu);
    // změna šířky cenové osy panelu srovná osy všech grafů
    chart.subscribeCrosshairMove((param) => onStackCrosshairMove(s, chart, param));
    chart.timeScale().subscribeSizeChange(() => scheduleScaleAlign(s));
    // Nový panel se rovná na rozsah hlavního grafu
    const mainRange = s.chart.timeScale().getVisibleLogicalRange();
    if (mainRange) chart.timeScale().setVisibleLogicalRange(mainRange);
    s.panesMap.set(pane.paneId, {
      chart,
      wrap,
      host,
      title,
      series: created,
      background,
      markers,
      markersDrawn: false,
    });
  }

  // Pozice značky indikátoru → pozice značky Lightweight Charts
  const MARKER_POSITIONS = { above: 'aboveBar', below: 'belowBar', in: 'inBar' };
  // Styl úsečky indikátoru (0 plná, 1 tečkovaná, 2 čárkovaná) → přerušování čáry
  // (stejné vzory jako LINE_DASHES u kreseb – ta konstanta je definovaná až níže)
  const SEGMENT_DASHES = { 0: [], 1: [1.5, 3], 2: [6, 4] };

  // Značky viditelných indikátorů na hlavní sérii (seřazené podle času, jak LWC vyžaduje)
  function applyIndicatorMarkers(s) {
    if (!s.candleSeries) return;
    const payload = s.lastIndicatorPayload || {};
    const markers = (s.indicatorMarkers || [])
      .filter((marker) => groupVisible(payload, marker.group))
      .map((marker) => ({
        time: marker.time,
        position: MARKER_POSITIONS[marker.position] || 'aboveBar',
        shape: marker.shape,
        color: marker.color,
        text: marker.text || '',
      }))
      .concat(replayMarkers(s))
      .sort((a, b) => a.time - b.time);
    s.candleSeries.setMarkers(markers);
  }

  // Úsečky viditelných indikátorů na překryvném canvasu (pod kresbami uživatele;
  // skrytí kreseb je neskrývá – patří indikátoru, ne kresbám)
  function drawIndicatorSegments(ctx, s) {
    const segments = s.indicatorSegments || [];
    if (!segments.length) return;
    // Skryté skupiny jednou za překreslení (ne hledání skupiny pro každou úsečku)
    const hidden = new Set(
      ((s.lastIndicatorPayload || {}).groups || []).filter((group) => group.visible === false).map((group) => group.id)
    );
    ctx.save();
    for (const segment of segments) {
      if (hidden.has(segment.group)) continue;
      const from = pointToPixel(s, { time: segment.time1, price: segment.price1 });
      const to = pointToPixel(s, { time: segment.time2, price: segment.price2 });
      if (!from || !to) continue;
      ctx.lineWidth = segment.width || 1;
      ctx.strokeStyle = segment.color;
      ctx.setLineDash(SEGMENT_DASHES[segment.style] || []);
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.stroke();
    }
    ctx.restore();
  }

  // Přehled scanneru v grafu (tabulka jako table v Pine): hlavička,
  // řádky symbol + stav obarvené podle směru, nebo poznámka; null = skrýt
  function setOverview(id, data) {
    const s = state.get(id);
    if (!s) return;
    if (!data) {
      if (s.overviewEl) s.overviewEl.remove();
      s.overviewEl = null;
      return;
    }
    if (!s.overviewEl) {
      s.overviewEl = el('div');
      s.mainHost.appendChild(s.overviewEl);
    }
    const box = s.overviewEl;
    box.className = 'lwchart-overview overview-' + data.position + ' overview-' + data.size;
    box.innerHTML = '';
    const table = el('table');
    const head = el('tr', 'overview-head');
    head.appendChild(el('th', '', data.title));
    head.appendChild(el('th', '', data.header));
    table.appendChild(head);
    // Bez řádků jeden řádek s poznámkou (scanner vypnutý, žádný signál…)
    const rows = data.rows.length ? data.rows : [{ symbol: data.note, text: '-', kind: 'none' }];
    for (const row of rows) {
      const tr = el('tr');
      tr.appendChild(el('td', 'overview-symbol', row.symbol));
      tr.appendChild(el('td', 'overview-status overview-' + row.kind, row.text));
      table.appendChild(tr);
    }
    box.appendChild(table);
  }

  // Tabulka indikátoru (jako table v Pine): nadpis, řádky popisek – hodnota
  // a vedle nich blok poznámek (průvodce interpretací) – vždy, nebo jen po
  // najetí myší na tabulku (notesMode "hover", značka ⓘ v nadpisu). Tabulka
  // v panelu má vlastní pozice (bez legendy a časové osy hlavního grafu)
  function buildIndicatorTable(table) {
    const place = (table.pane ? 'ind-pane-' : 'overview-') + table.position;
    const box = el('div', 'lwchart-ind-table ' + place + ' overview-' + table.size + ' notes-' + table.notesMode);
    if (table.color) box.style.setProperty('--ind-table-accent', table.color);
    const main = el('table', 'ind-table-main');
    const head = el('tr', 'ind-table-head');
    const title = el('th', '', table.title);
    title.colSpan = 2;
    if (table.notes.length && table.notesMode === 'hover') title.appendChild(el('span', 'ind-table-hint', ' ⓘ'));
    head.appendChild(title);
    main.appendChild(head);
    for (const [label, value] of table.rows) {
      const tr = el('tr');
      tr.appendChild(el('td', 'ind-table-label', label));
      tr.appendChild(el('td', 'ind-table-value', value));
      main.appendChild(tr);
    }
    box.appendChild(main);
    if (table.notes.length) {
      const notes = el('div', 'ind-table-notes');
      notes.appendChild(el('div', 'ind-table-notes-title', table.notesTitle));
      for (const note of table.notes) notes.appendChild(el('div', 'ind-table-note', note));
      box.appendChild(notes);
    }
    return box;
  }

  // Tabulky indikátorů v panelech a hlavním grafu; nezměněná tabulka zůstane
  // (živý přepočet nepřekresluje DOM a nezavře průvodce pod myší), panel
  // přestavěný po změně indikátorů dostane tabulku znovu
  function renderIndicatorTables(s, payload) {
    const groupsPayload = payload.tail ? s.lastIndicatorPayload || payload : payload;
    const seen = new Set();
    const perGroup = new Map();
    for (const table of payload.tables || []) {
      const index = perGroup.get(table.group) || 0;
      perGroup.set(table.group, index + 1);
      const key = table.group + ':' + index;
      const paneEntry = table.pane ? s.panesMap.get(table.pane) : null;
      const host = table.pane ? paneEntry && paneEntry.host : s.mainHost;
      if (!host) continue;
      seen.add(key);
      const json = JSON.stringify(table);
      let entry = s.indicatorTables.get(key);
      if (!entry || entry.json !== json || entry.host !== host || !entry.el.isConnected) {
        if (entry) entry.el.remove();
        entry = { el: buildIndicatorTable(table), json: json, host: host };
        host.appendChild(entry.el);
        s.indicatorTables.set(key, entry);
      }
      entry.el.classList.toggle('hidden', !groupVisible(groupsPayload, table.group));
    }
    for (const [key, entry] of s.indicatorTables) {
      if (seen.has(key)) continue;
      entry.el.remove();
      s.indicatorTables.delete(key);
    }
  }

  // Nahradí všechny indikátory: overlay čáry + panely pod hlavním grafem
  function setIndicators(id, payload) {
    const s = state.get(id);
    if (!s) return;
    // Úplný payload si pamatujeme pro přestavbu panelů po změně motivu
    if (!payload.tail) s.lastIndicatorPayload = payload;
    // Stejná sada čar a panelů (typicky živý přepočet nebo změna barvy či
    // viditelnosti) → jen nová data a volby v existujících sériích; panely se
    // nepřestavují a graf nebliká. Payload s příznakem "tail" (živý přepočet)
    // obsahuje jen poslední body, které se do sérií doplní přes update(); ten
    // umí jen poslední nebo novější bod, proto starší body vynecháme
    const structure = indicatorStructure(payload);
    const lastTime = s.indicatorLastTime;
    let rebuilt = false;
    withoutSync(s, () => {
      rebuilt = applyIndicatorPayload(s, payload, structure, lastTime);
    });
    if (!rebuilt) return;
    // Úsečky a značky indikátorů přicházejí vždy celé (i v živém přepočtu)
    s.indicatorSegments = payload.segments || [];
    s.indicatorMarkers = payload.markers || [];
    applyIndicatorMarkers(s);
    renderIndicatorTables(s, payload);
    redrawOverlay(s);
    // Panely po nových datech srovnáme na rozsah hlavního grafu
    alignPanesToMain(s);
    storeIndicatorValues(s, payload);
    // Řádky legendy se přestaví jen u úplného payloadu (názvy, viditelnost)
    if (!payload.tail) rebuildIndicatorLegend(s, payload);
    else if (!s.hovering) renderIndicatorValues(s, null);
    const payloadLastTime = indicatorPayloadLastTime(payload);
    s.indicatorLastTime = payload.tail ? Math.max(lastTime, payloadLastTime) : payloadLastTime;
  }

  // Promítne payload indikátorů do sérií (jen data / nebo celá přestavba);
  // vrací false, když zkrácený payload k jiné sadě indikátorů nelze použít
  function applyIndicatorPayload(s, payload, structure, lastTime) {
    if (structure === s.indicatorStructure) {
      const apply = (series, spec, visible, priceFormat) => {
        if (payload.tail) {
          spec.data.filter((point) => point.time >= lastTime).forEach((point) => series.update(point));
          return;
        }
        series.setData(spec.data);
        // Volby ze záložky Style (barva, tloušťka, popisky, přesnost…) se mění bez přestavby
        series.applyOptions(indicatorSeriesOptions(spec, visible, priceFormat));
      };
      (payload.overlays || []).forEach((overlay, index) =>
        apply(
          s.overlaySeries[index],
          overlay,
          groupVisible(payload, overlay.group),
          payload.tail ? null : overlayPriceFormat(s, payload, overlay.group)
        )
      );
      for (const pane of payload.panes || []) {
        const existing = s.panesMap.get(pane.paneId);
        const priceFormat = payload.tail ? null : panePriceFormat(s, pane);
        pane.series.forEach((spec, index) =>
          apply(existing.series[index], spec, groupVisible(payload, pane.group), priceFormat)
        );
        if (existing.background && pane.background) {
          if (payload.tail) {
            pane.background.filter((point) => point.time >= lastTime).forEach((point) => existing.background.update(point));
          } else {
            existing.background.setData(pane.background);
            existing.background.applyOptions({ visible: groupVisible(payload, pane.group) });
          }
        }
      }
    } else if (payload.tail) {
      // Zkrácený payload k jiné sadě indikátorů (mezitím změněné) nelze použít
      return false;
    } else {
      rebuildIndicators(s, payload);
    }
    return true;
  }

  // Čas posledního bodu v payloadu indikátorů (−∞, když je prázdný)
  function indicatorPayloadLastTime(payload) {
    const series = (payload.overlays || []).concat(...(payload.panes || []).map((pane) => pane.series));
    const data = series.length ? series[0].data : [];
    return data.length ? data[data.length - 1].time : -Infinity;
  }

  // Otisk struktury indikátorů (skupiny a typy čar, panely a jejich série)
  function indicatorStructure(payload) {
    const overlays = (payload.overlays || []).map((overlay) => overlay.group + ':' + overlay.name + ':' + overlay.type);
    const panes = (payload.panes || []).map(
      (pane) =>
        pane.paneId + ':' + pane.series.map((spec) => spec.name + ':' + spec.type).join(',') + ':' + (pane.levels || []).join(',') +
        (pane.background ? ':bg' : '')
    );
    return JSON.stringify([overlays, panes]);
  }

  // Odstraní a znovu vytvoří všechny čáry a panely indikátorů (i po změně motivu)
  function rebuildIndicators(s, payload) {
    const id = s.elementId;
    s.indicatorStructure = indicatorStructure(payload);
    clearIndicators(id);
    const theme = THEMES[s.themeName];
    // Overlay čáry v hlavním grafu (průměry, pásma, trend); overlayMeta
    // (skupina + pořadí série ve skupině) slouží k nalezení čáry pod kurzorem
    s.overlayMeta = [];
    const perGroup = new Map();
    for (const overlay of payload.overlays || []) {
      const index = perGroup.get(overlay.group) || 0;
      perGroup.set(overlay.group, index + 1);
      s.overlayMeta.push({ group: overlay.group, index: index });
      const series = createIndicatorSeries(
        s.chart,
        overlay,
        groupVisible(payload, overlay.group),
        overlayPriceFormat(s, payload, overlay.group)
      );
      series.setData(overlay.data);
      s.overlaySeries.push(series);
    }
    // Samostatné panely (oscilátory, objem…)
    for (const pane of payload.panes || []) {
      addIndicatorPane(s, pane, theme, groupVisible(payload, pane.group));
    }
    resetScaleWidths(s);
  }

  // Nahradí celý dataset svíček (načtení historie)
  // Seřadí svíčky podle času a odstraní duplicity (platí poslední výskyt) –
  // Lightweight Charts vyžaduje rostoucí časové značky bez duplicit
  function sortUniqueCandles(candles) {
    const clean = [];
    for (const candle of [...candles].sort((a, b) => a.time - b.time)) {
      if (clean.length && clean[clean.length - 1].time === candle.time) clean[clean.length - 1] = candle;
      else clean.push(candle);
    }
    return clean;
  }

  // Nastaví svíčky grafu ze s.candles
  function applySeriesData(s) {
    s.candleSeries.setData(mainData(s));
  }

  function setCandles(id, candles, opts) {
    const s = state.get(id);
    if (!s) return;
    const clean = sortUniqueCandles(candles);
    s.byTime = new Map(clean.map((candle) => [candle.time, candle]));
    s.candles = clean;
    s.lastCandle = clean.length ? clean[clean.length - 1] : null;
    s.stepSeconds = (opts && opts.stepSeconds) || 60;
    // Denní/intraday timeframe mění formátování času (viz applyTimezone)
    applyTimezone(s);
    // Nový dataset (jiný symbol/timeframe): starší historii lze znovu donačítat
    s.olderLoading = false;
    s.olderExhausted = false;
    s.olderRetryAt = 0;
    // Přesnost cenové osy podle instrumentu (forex 1.10025 místo 1.10)
    s.priceDecimals = detectPriceDecimals(clean);
    s.candleSeries.applyOptions(mainSeriesOptions(s));
    // Nový symbol / timeframe: cenová osa znovu v automatickém měřítku (jako
    // v TradingView) – ruční měřítko z předchozího symbolu by graf nechalo
    // v rozsahu cen, kde nová data nejsou vidět
    resetPriceScales(s);
    resetScaleWidths(s);
    // Instrument bez objemu (forex MIDPOINT, indexy): měřič objem neukazuje
    s.hasVolume = clean.some((candle) => candle.volume > 0);
    // Zobrazit konec grafu (posledních N svíček), ne celou historii zmenšenou
    // na minimum; panely se srovnají až s novými daty indikátorů
    const visible = (opts && opts.visibleBars) || 150;
    withoutSync(s, () => {
      // Overlay indikátory drží časy předchozího datasetu; časová osa je
      // sjednocením všech sérií, takže by po přepočtu indikátorů posunuly
      // logické indexy (graf mimo data) – vyprázdníme je do příchodu nových
      for (const series of s.overlaySeries) series.setData([]);
      for (const pane of s.panesMap.values()) {
        pane.series.forEach((series) => series.setData([]));
        if (pane.background) pane.background.setData([]);
      }
      s.indicatorLastTime = -Infinity;
      applySeriesData(s);
      s.chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, clean.length - visible),
        to: clean.length + 4,
      });
    });
    renderLegend(s, s.lastCandle);
  }

  // Zapne automatické měřítko cenové osy hlavního grafu i panelů indikátorů
  function resetPriceScales(s) {
    for (const chart of stackCharts(s)) chart.priceScale('right').applyOptions({ autoScale: true });
  }

  // Požádá Python o starší svíčky, když se graf posunul blízko začátku dat
  function maybeRequestOlder(s, range) {
    if (!range || !s.candles.length || s.olderLoading || s.olderExhausted) return;
    if (range.from > OLDER_THRESHOLD_BARS || Date.now() < s.olderRetryAt) return;
    s.olderLoading = true;
    s.finishedQueue.push({ type: 'load_older', before: s.candles[0].time });
  }

  // Přidá starší svíčky na začátek grafu; viditelný rozsah se posune tak,
  // aby graf zůstal na stejném místě. Prázdný seznam = nic nového
  // (exhausted: starší data nejsou, jinak se to zkusí znovu za chvíli)
  function prependCandles(id, candles, exhausted) {
    const s = state.get(id);
    if (!s) return;
    s.olderLoading = false;
    const first = s.candles.length ? s.candles[0].time : Infinity;
    const older = sortUniqueCandles(candles.filter((candle) => candle.time < first));
    if (!older.length) {
      if (exhausted) s.olderExhausted = true;
      else s.olderRetryAt = Date.now() + OLDER_RETRY_MS;
      return;
    }
    const range = s.chart.timeScale().getVisibleLogicalRange();
    s.candles = older.concat(s.candles);
    for (const candle of older) s.byTime.set(candle.time, candle);
    withoutSync(s, () => {
      applySeriesData(s);
      if (range) {
        s.chart.timeScale().setVisibleLogicalRange({ from: range.from + older.length, to: range.to + older.length });
      }
    });
    redrawOverlay(s);
  }

  // Inkrementální doplnění/aktualizace svíčky (živá data) – series.update, ne celý dataset
  function updateCandle(id, candle) {
    const s = state.get(id);
    if (!s || !s.candles.length) return;
    const last = s.candles[s.candles.length - 1];
    // Starší svíčku ignorujeme – graf se nevrací zpět v čase
    if (candle.time < last.time) return;
    // Předchozí svíčka a bod série (obarvení podle předchozího závěru, Heikin Ashi)
    const replace = candle.time === last.time;
    const count = s.candles.length;
    const previous = replace ? s.candles[count - 2] : last;
    const previousPoint = s.mainPoints[replace ? count - 2 : count - 1];
    const point = mainPoint(s, candle, previous, previousPoint);
    s.candleSeries.update(point);
    if (replace) {
      s.mainPoints[count - 1] = point;
      s.candles[count - 1] = candle;
    } else {
      s.mainPoints.push(point);
      s.candles.push(candle);
    }
    s.byTime.set(candle.time, candle);
    s.lastCandle = candle;
    if (!s.hovering) renderLegend(s, candle);
  }

  // Dávka živých svíček (např. doplněná díra po výpadku + nová svíčka) jedním voláním
  function updateCandles(id, candles) {
    for (const candle of candles) updateCandle(id, candle);
  }

  // Přepnutí tmavého/světlého motivu grafu
  function setTheme(id, dark) {
    const s = state.get(id);
    if (!s) return;
    s.themeName = dark ? 'dark' : 'light';
    const theme = THEMES[s.themeName];
    s.chart.applyOptions({
      layout: theme.layout,
      grid: theme.grid,
      rightPriceScale: { borderColor: theme.border },
      timeScale: { borderColor: theme.border },
    });
    s.candleSeries.applyOptions(mainSeriesOptions(s));
    // Svíčky a objemy překreslit, aby měly správné barvy po přepnutí motivu
    applySeriesData(s);
    // Panely indikátorů přegenerovat v novém motivu
    if (s.lastIndicatorPayload) {
      rebuildIndicators(s, s.lastIndicatorPayload);
      rebuildIndicatorLegend(s, s.lastIndicatorPayload);
    }
  }

  // ---------- kreslení (modelováno po TradingView) ----------

  // Zapne/vypne kreslicí mód (hline | vline | trend | ray | extended | rect |
  // brush | fib | text | frvp | measure | null = kurzor)
  function setDrawingMode(id, mode) {
    const s = state.get(id);
    if (!s) return;
    // Typ kurzoru (Nitkový kříž, Tečka, …, Guma) = režim bez kreslení; null
    // (návrat po dokončení kresby) ponechá naposledy zvolený typ
    if (CURSOR_TYPES[mode]) {
      if (s.cursorMode !== mode) {
        s.cursorMode = mode;
        applyCursorType(s);
      }
      mode = null;
    }
    s.overlay.mode = mode || null;
    s.overlay.pending = null;
    s.overlay.previewPoint = null;
    if (s.overlay.mode !== 'measure') s.measure = null;
    // Aktivní kreslicí nástroj = crosshair kurzor na overlay (v kurzoru je crosshair grafu)
    s.overlay.canvas.classList.toggle('drawing-active', !!mode);
    redrawOverlay(s);
    // Zvýraznění aktivního nástroje v panelu oblíbených (bez přestavby panelu)
    // a lišta kresby (výběr šablony nového objektu)
    paintFavoriteTools();
    syncDrawingBar();
  }

  // Oblíbené nástroje z nastavení: seznam režimů, uložená pozice panelu
  // ({left, top} v px vůči oknu, null = nahoře uprostřed grafu) a viditelnost
  // (Python je už zkontroloval – normalize_favorites / normalize_panel_position)
  function setFavorites(id, modes, position, visible) {
    fav.modes = Array.isArray(modes) ? modes : [];
    fav.pos = position;
    fav.visible = !!visible;
    renderFavorites();
  }

  // Zobrazí / skryje plovoucí panel oblíbených (tlačítko hvězdy v liště)
  function setFavoritesVisible(id, visible) {
    fav.visible = !!visible;
    renderFavorites();
  }

  // Styl svíček ze Settings grafu (barvy těla, okrajů, knotů, obarvení podle
  // předchozího závěru) – použije se hned, svíčky se překreslí
  function setChartStyle(id, style) {
    const s = state.get(id);
    if (!s) return;
    // Vlastní barvy bodů nese jen setData; bez nich stačí volby série –
    // objem ani svíčky se znovu neposílají
    const perBar = usesPointColors(s) || style.color_by_prev_close;
    s.chartStyle = style;
    s.candleSeries.applyOptions(mainSeriesOptions(s));
    if (perBar) s.candleSeries.setData(mainData(s));
  }

  // Typ grafu (Bars, Candles, Line, Area, …) – hlavní série se vymění
  function setChartType(id, type) {
    const s = state.get(id);
    if (!s || !CHART_TYPE_SERIES[type] || s.chartType === type) return;
    s.chartType = type;
    createMainSeries(s);
    redrawOverlay(s);
  }

  // Nastaví definice skupin nástrojů pro flyout nabídky (přijde z Pythonu;
  // může přijít i před init – skupiny jsou společné všem grafům)
  function setToolGroups(id, groups) {
    toolGroups = Array.isArray(groups) ? groups : [];
    renderFavorites();
  }

  // Výchozí styl nových kreseb podle typu (výchozí šablony ze Settings kresby)
  function setDrawingDefaults(id, defaults) {
    const s = state.get(id);
    if (!s) return;
    s.drawingDefaults = defaults || {};
  }

  // Nastaví režim magnetu: 'off' | 'weak' | 'strong'
  function setMagnet(id, mode) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.magnet = mode === 'weak' || mode === 'strong' ? mode : 'off';
    redrawOverlay(s);
  }

  // Skryje/zobrazí všechny kresby (ikona oka)
  function setDrawingsHidden(id, hidden) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.hidden = !!hidden;
    redrawOverlay(s);
  }

  // Nahradí kresby zobrazené k symbolu (načtené z backendu)
  // Nahradí kresby zobrazené k symbolu (načtené z backendu při načtení symbolu)
  function setDrawings(id, drawings) {
    const s = state.get(id);
    if (!s) return;
    const selectedId = (s.overlay.drawings[s.overlay.selected] || {}).id;
    s.overlay.drawings = (drawings || []).map((d) => ({
      id: d.id,
      kind: d.kind,
      points: d.points,
      color: d.color || '#2962ff',
      text: d.text || null,
      options: d.options || {},
    }));
    // Vybraná kresba zůstane vybraná i po novém načtení (změna stylu, šablona)
    s.overlay.selected = selectedId ? s.overlay.drawings.findIndex((d) => d.id === selectedId) : -1;
    s.overlay.drag = null;
    s.overlay.hovered = null;
    redrawOverlay(s);
  }

  // Styl nové kresby daného typu: šablona zvolená u nástroje, jinak výchozí
  // šablona typu, jinak tovární
  function defaultStyle(s, kind) {
    const chosen = toolTemplate[kind] && templatesOf(kind)[toolTemplate[kind]];
    return chosen || s.drawingDefaults[kind] || { color: '#2962ff', options: {} };
  }

  // Přidá novou kresbu do grafu a do fronty k uložení; id z databáze jí
  // dodatečně přiřadí Python přes assignDrawingId (podle lokálního id).
  // Barvu a volby (šířka, styl čáry, písmo) převezme z výchozího stylu typu
  function queueNewDrawing(s, drawing) {
    const style = defaultStyle(s, drawing.kind);
    drawing.color = drawing.color || style.color;
    drawing.options = Object.assign({}, style.options, drawing.options || {});
    drawing.localId = s.nextLocalId++;
    s.overlay.drawings.push(drawing);
    redrawOverlay(s);
    s.finishedQueue.push({
      type: 'add',
      localId: drawing.localId,
      kind: drawing.kind,
      points: drawing.points,
      color: drawing.color,
      options: drawing.options,
      text: drawing.text || null,
    });
  }

  // Přidá textovou kresbu po potvrzení dialogu Text (barva a volby písma,
  // pozadí, rámečku a viditelnosti z dialogu)
  function addText(id, time, price, text, color, options) {
    const s = state.get(id);
    if (!s) return;
    queueNewDrawing(s, {
      kind: 'text',
      points: [{ time: Number(time), price: Number(price) }],
      text: text,
      color: color || null,
      options: options || {},
    });
  }

  // Smaže označenou kresbu (nebo všechny, když není nic označené); vrátí počet
  function trashDrawings(id) {
    const s = state.get(id);
    if (!s || s.locked) return 0;
    if (deleteSelected(s)) return 1;
    // Smazat vše: každá kresba jde do fronty pro backend
    for (const drawing of s.overlay.drawings) queueDeletion(s, drawing);
    const count = s.overlay.drawings.length;
    s.overlay.drawings = [];
    s.overlay.selected = -1;
    redrawOverlay(s);
    return count;
  }

  // Smaže kresbu podle indexu (lokálně + fronta pro backend); vrátí true při smazání
  function removeDrawing(s, index) {
    const drawing = s.overlay.drawings[index];
    if (!drawing) return false;
    s.overlay.drawings.splice(index, 1);
    queueDeletion(s, drawing);
    if (s.overlay.selected === index) s.overlay.selected = -1;
    redrawOverlay(s);
    return true;
  }

  // Smazání kresby pro backend: uložená jde do fronty jako "delete"; u dosud
  // neuložené se zruší čekající "add" (už vyzvednutou smaže assignDrawingId)
  function queueDeletion(s, drawing) {
    if (drawing.id) s.finishedQueue.push({ type: 'delete', id: drawing.id });
    else s.finishedQueue = s.finishedQueue.filter((e) => !(e.type === 'add' && e.localId === drawing.localId));
  }

  // Označí kresbu (index) nebo zruší označení (-1)
  function selectDrawing(s, index) {
    s.overlay.selected = index;
  }

  // Smaže označenou kresbu; vrátí true, když se smazala
  function deleteSelected(s) {
    if (s.overlay.selected >= 0) {
      removeDrawing(s, s.overlay.selected);
      s.overlay.selected = -1;
      return true;
    }
    return false;
  }

  // Počet kliků potřebných pro každý nástroj (1 = jednoklikové, 3 = kanál/pitchfork,
  // ostatní 2); text, sticker a měřič mají vlastní obsluhu kliku
  const DRAWING_CLICKS = {
    hline: 1,
    vline: 1,
    hray: 1,
    cross: 1,
    long: 1,
    short: 1,
    channel: 3,
    pitchfork: 3,
    brush: 0,
  };

  // Zpracuje klik při aktivním kreslení; jednoklikové nástroje hline, vline,
  // hray a cross stačí jedním klikem; kanál a pitchfork potřebují tři kliky;
  // štětec sbírá body při tažení a klikem se ukončí; ostatní jsou dvouklikové
  function handleDrawingClick(s, point) {
    const kind = s.overlay.mode;
    const needed = DRAWING_CLICKS[kind] !== undefined ? DRAWING_CLICKS[kind] : 2;
    if (!s.overlay.pending) {
      if (needed === 1) {
        // Pozice dostane z kliku vstup a výchozí cíl, stop a šířku
        finishDrawing(s, { kind: kind, points: POSITION_KINDS[kind] ? positionPoints(s, point, kind) : [point] });
        return;
      }
      s.overlay.pending = { kind: kind, points: [point] };
      redrawOverlay(s);
      return;
    }
    // Štětec: body se sbírají při tažení, klik ukončí kresbu
    if (kind === 'brush') {
      s.overlay.pending.points.push(point);
      finishDrawing(s, s.overlay.pending);
      return;
    }
    const points = s.overlay.pending.points;
    // Další body přidáváme, dokud není dosažen počet kliků nástroje
    points.push(point);
    if (points.length >= needed) {
      finishDrawing(s, s.overlay.pending);
      return;
    }
    redrawOverlay(s);
  }

  // Klik měřiče: 1. klik start, 2. klik finální měřič (zůstane zobrazený),
  // 3. klik ho zruší (jako v TradingView – měřič se neukládá)
  function handleMeasureClick(s, point) {
    if (!s.measure) {
      s.measure = { points: [point] };
      redrawOverlay(s);
      return;
    }
    if (s.measure.points.length === 1) {
      s.measure.points.push(point);
      redrawOverlay(s);
      return;
    }
    s.measure = null;
    redrawOverlay(s);
  }

  // Dokončí kresbu: přidá ji do seznamu, uloží payload pro backend a upozorní ho signálem
  function finishDrawing(s, drawing) {
    s.overlay.pending = null;
    s.overlay.previewPoint = null;
    // Hotové kresby se hromadí ve frontě a backend si je vyzvedává stránkovým
    // timerem přes popAllEvents (DOM eventy z Vue komponenty nejsou spolehlivé;
    // fronta zvládne i více kreseb mezi dvěma ticky)
    queueNewDrawing(s, { kind: drawing.kind, points: drawing.points });
    if (POSITION_KINDS[drawing.kind]) s.overlay.selected = s.overlay.drawings.length - 1;
    returnToCursor(s);
  }

  // TradingView: bez zapnutého režimu tužky se po nakreslení vrací kurzor
  // (Python se o tom dozví událostí "tool" a zvýrazní skupinu kurzoru)
  function returnToCursor(s) {
    if (s.keepTool) return;
    setDrawingMode(s.elementId, null);
    s.finishedQueue.push({ type: 'tool', mode: null });
  }

  // Zapne/vypne režim tužky (nástroj zůstává vybraný i po nakreslení);
  // vypnutí tužky ukončí i právě aktivní nástroj (návrat na kurzor)
  function setKeepTool(id, on) {
    const s = state.get(id);
    if (!s) return;
    s.keepTool = !!on;
    if (!s.keepTool && s.overlay.mode) exitTool(s);
  }

  // Zamkne/odemkne kresby (zabrání editaci i mazání)
  function setLocked(id, on) {
    const s = state.get(id);
    if (!s) return;
    s.locked = !!on;
    if (s.locked) {
      s.overlay.drag = null;
      selectDrawing(s, -1);
      redrawOverlay(s);
    }
  }

  // Přidá sticker (emoji kresba, velké písmo)
  function addSticker(id, time, price, emoji) {
    const s = state.get(id);
    if (!s) return;
    queueNewDrawing(s, {
      kind: 'sticker',
      points: [{ time: Number(time), price: Number(price) }],
      text: emoji,
    });
  }

  // Python uložil novou kresbu: přiřadí jí id z databáze. Kresba mezitím
  // smazaná se smaže i v databázi, posunutá se uloží s novými body
  function assignDrawingId(id, localId, drawingId) {
    const s = state.get(id);
    if (!s) return;
    const drawing = s.overlay.drawings.find((d) => d.localId === localId);
    if (!drawing) {
      s.finishedQueue.push({ type: 'delete', id: drawingId });
      return;
    }
    drawing.id = drawingId;
    if (drawing.dirty) {
      drawing.dirty = false;
      s.finishedQueue.push({ type: 'update', id: drawingId, points: drawing.points, text: drawing.text || null });
    }
  }

  // Úchopy kresby v pixelech [{x, y, handle, cursor?}] (null = bod mimo graf):
  // body kresby a obdélníku mají úchop {time, price} = index bodu, jehož čas /
  // cenu tažení mění (null = rozměr se nemění); pozice má vlastní číselné
  // úchopy (vstup, cíl, stop, šířka)
  function drawingHandles(s, drawing) {
    if (isPosition(drawing)) return positionHandles(s, drawing);
    if (isFrvp(drawing)) return frvpHandles(s, drawing);
    if (drawing.kind === 'rect' && drawing.points.length >= 2) return rectHandles(s, drawing);
    return drawing.points.map((point, index) => {
      const pixel = pointToPixel(s, point);
      return pixel ? { x: pixel.x, y: pixel.y, handle: { time: index, price: index } } : null;
    });
  }

  // Úchopy obdélníku jako v TradingView: 4 rohy (mění čas i cenu) a 4 středy
  // hran (mění jen jeden rozměr); úchopy jsou v bodech kresby i u prodlouženého
  // obdélníku; cursor = kurzor myši nad úchopem
  function rectHandles(s, drawing) {
    const a = pointToPixel(s, drawing.points[0]);
    const b = pointToPixel(s, drawing.points[1]);
    if (!a || !b) return [];
    const midX = (a.x + b.x) / 2;
    const midY = (a.y + b.y) / 2;
    // Rohy v bodech a, b leží na jedné úhlopříčce, zbylé dva na druhé: leží-li
    // a vlevo nahoře / vpravo dole od b, je jejich kurzor nwse, jinak nesw
    const pointsDiagonal = (a.x <= b.x) === (a.y <= b.y) ? 'nwse-resize' : 'nesw-resize';
    const otherDiagonal = pointsDiagonal === 'nwse-resize' ? 'nesw-resize' : 'nwse-resize';
    return [
      { x: a.x, y: a.y, handle: { time: 0, price: 0 }, cursor: pointsDiagonal },
      { x: b.x, y: b.y, handle: { time: 1, price: 1 }, cursor: pointsDiagonal },
      { x: b.x, y: a.y, handle: { time: 1, price: 0 }, cursor: otherDiagonal },
      { x: a.x, y: b.y, handle: { time: 0, price: 1 }, cursor: otherDiagonal },
      { x: midX, y: a.y, handle: { time: null, price: 0 }, cursor: 'ns-resize' },
      { x: midX, y: b.y, handle: { time: null, price: 1 }, cursor: 'ns-resize' },
      { x: a.x, y: midY, handle: { time: 0, price: null }, cursor: 'ew-resize' },
      { x: b.x, y: midY, handle: { time: 1, price: null }, cursor: 'ew-resize' },
    ];
  }

  // Zda bod (px, py) leží v rovnoběžníku origin + u·e1 + v·e2, kde 0 ≤ u ≤ 1
  // a 0 ≤ v ≤ vMax (vMax = Infinity → pás nekonečný ve směru e2, pitchfork);
  // u, v z rozkladu vektoru do báze e1, e2 (Cramerovo pravidlo)
  function inParallelogram(px, py, origin, e1, e2, vMax) {
    const det = e1.x * e2.y - e1.y * e2.x;
    if (Math.abs(det) < 1e-9) return false;
    const dx = px - origin.x;
    const dy = py - origin.y;
    const u = (dx * e2.y - dy * e2.x) / det;
    const v = (e1.x * dy - e1.y * dx) / det;
    return u >= 0 && u <= 1 && v >= 0 && v <= vMax;
  }

  // Vzdálenost bodu od úsečky (pro test, zda kurzor míří na kresbu)
  function distToSegment(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len2 = dx * dx + dy * dy;
    let t = len2 ? ((px - x1) * dx + (py - y1) * dy) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
  }

  // Poloměr zásahu úchopu v px
  const HANDLE_HIT_PX = 8;

  // Nejbližší úchop kresby v dosahu HANDLE_HIT_PX od kurzoru, jinak null
  function nearestHandle(s, drawing, x, y) {
    let best = null;
    let bestDistance = HANDLE_HIT_PX;
    for (const h of drawingHandles(s, drawing)) {
      if (!h) continue;
      const distance = Math.hypot(x - h.x, y - h.y);
      if (distance <= bestDistance) {
        best = h;
        bestDistance = distance;
      }
    }
    return best;
  }

  // Překrytí: co je pod kurzorem – úchop označené kresby, kresby s úchopy
  // ukázanými najetím nebo kresby pod kurzorem (úchopy jdou chytit hned
  // prvním klikem, jako v TradingView), jinak tělo kresby pod kurzorem.
  // Vrací {index, handle (úchop nebo 'body'), cursor} nebo null.
  function hitDrawing(s, x, y) {
    const index = hitTest(s, x, y);
    const checked = new Set();
    for (const candidate of [s.overlay.selected, s.overlay.drawings.indexOf(s.overlay.hovered), index]) {
      if (candidate < 0 || checked.has(candidate)) continue;
      checked.add(candidate);
      const drawing = s.overlay.drawings[candidate];
      if (!drawing || drawing.kind === 'brush') continue;
      const handle = nearestHandle(s, drawing, x, y);
      if (handle) return { index: candidate, handle: handle.handle, cursor: handle.cursor || 'move' };
    }
    return index >= 0 ? { index: index, handle: 'body', cursor: 'pointer' } : null;
  }

  // Překrytí: najde kresbu pod kurzorem (index v s.overlay.drawings) nebo -1
  function hitTest(s, x, y) {
    const threshold = HIT_TOLERANCE_PX;
    for (let i = s.overlay.drawings.length - 1; i >= 0; i--) {
      const drawing = s.overlay.drawings[i];
      const points = drawing.points.map((point) => pointToPixel(s, point));
      if (points.some((p) => p === null)) continue;
      if (isPosition(drawing)) {
        // Pozice: kdekoli v obdélníku zón cíle a stopu
        const box = positionBox(s, drawing);
        if (box && x >= box.left - 3 && x <= box.right + 3 && y >= box.top - 3 && y <= box.bottom + 3) return i;
      } else if (isFrvp(drawing)) {
        // FRVP: kdekoli v boxu profilu; bez svíček v rozsahu jen u svislých okrajů
        const geometry = frvpGeometry(s, drawing, drawing.points);
        if (!geometry) continue;
        if (!geometry.profile) {
          if (Math.abs(x - geometry.left) <= threshold || Math.abs(x - geometry.right) <= threshold) return i;
        } else if (
          x >= geometry.left - threshold && x <= geometry.right + threshold &&
          y >= geometry.top - threshold && y <= geometry.bottom + threshold
        ) return i;
      } else if (drawing.kind === 'hline') {
        if (Math.abs(y - points[0].y) <= threshold) return i;
      } else if (drawing.kind === 'vline') {
        if (Math.abs(x - points[0].x) <= threshold) return i;
      } else if (drawing.kind === 'text') {
        // Celý obdélník textu (text se kreslí vpravo nad kotevním bodem)
        const box = textLayout(s, drawing, points[0]);
        if (x >= box.x - 3 && x <= box.x + box.w + 3 && y >= box.y - 3 && y <= box.y + box.h + 3) return i;
      } else if (drawing.kind === 'rect') {
        // Okraje i vnitřek obdélníku (klik dovnitř táhne celou kresbu jako
        // v TradingView); prodloužená strana sahá až k okraji grafu
        // (nekonečno stačí pro rozsah)
        if (points.length < 2) continue;
        const box = rectBounds(points[0], points[1], drawing.options || {}, Infinity);
        if (
          x >= box.left - threshold && x <= box.right + threshold &&
          y >= box.top - threshold && y <= box.bottom + threshold
        ) return i;
      } else if (drawing.kind === 'fib' || drawing.kind === 'fibext') {
        // Pás mezi krajními úrovněmi (čáry úrovní i prostor mezi nimi)
        if (points.length < 2) continue;
        const [a, b] = points;
        const x1 = Math.min(a.x, b.x);
        const x2 = Math.max(a.x, b.x);
        // Úrovně jsou seřazené vzestupně – krajní jsou první a poslední
        const levels = fibLevels(drawing.kind);
        const yA = b.y + (a.y - b.y) * levels[0];
        const yB = b.y + (a.y - b.y) * levels[levels.length - 1];
        if (
          x >= x1 - threshold && x <= x2 + threshold &&
          y >= Math.min(yA, yB) - threshold && y <= Math.max(yA, yB) + threshold
        ) return i;
      } else if (drawing.kind === 'brush') {
        for (let k = 1; k < points.length; k++) {
          if (distToSegment(x, y, points[k - 1].x, points[k - 1].y, points[k].x, points[k].y) <= threshold) return i;
        }
      } else if (drawing.kind === 'arrow') {
        const [a, b] = points;
        if (distToSegment(x, y, a.x, a.y, b.x, b.y) <= threshold) return i;
      } else if (drawing.kind === 'hray') {
        const p0 = points[0];
        if (x >= p0.x - threshold && Math.abs(y - p0.y) <= threshold) return i;
      } else if (drawing.kind === 'cross') {
        const p0 = points[0];
        if (Math.abs(y - p0.y) <= threshold || Math.abs(x - p0.x) <= threshold) return i;
      } else if (drawing.kind === 'sticker') {
        if (Math.hypot(x - points[0].x, y - points[0].y) <= 16) return i;
      } else if (drawing.kind === 'channel') {
        // Obě přímky kanálu (posun = kolmá složka od třetího bodu, stejně jako
        // při kreslení) + vnitřek (klik dovnitř kanálu ho také vybere)
        const [a, b, c] = points;
        if (distToSegment(x, y, a.x, a.y, b.x, b.y) <= threshold) return i;
        if (!c) continue;
        const off = channelOffset(a, b, c);
        if (distToSegment(x, y, a.x + off.x, a.y + off.y, b.x + off.x, b.y + off.y) <= threshold) return i;
        if (inParallelogram(x, y, a, { x: b.x - a.x, y: b.y - a.y }, off, 1)) return i;
      } else if (drawing.kind === 'pitchfork') {
        // Tři linie: A→M(B,C) a rovnoběžky z B a C (testujeme viditelnou část);
        // při dvoubodové (rozpracované) kresbě testujeme jen spojnici A→B
        const [a, b, c] = points;
        if (!c) {
          if (distToSegment(x, y, a.x, a.y, b.x, b.y) <= threshold) return i;
        } else {
          const midX = (b.x + c.x) / 2;
          const midY = (b.y + c.y) / 2;
          if (distToSegment(x, y, a.x, a.y, midX, midY) <= threshold) return i;
          if (distToSegment(x, y, b.x, b.y, midX + (b.x - a.x), midY + (b.y - a.y)) <= threshold) return i;
          if (distToSegment(x, y, c.x, c.y, midX + (c.x - a.x), midY + (c.y - a.y)) <= threshold) return i;
          // Prostor mezi krajními linkami (z B a C ve směru A→střed BC)
          if (inParallelogram(x, y, b, { x: c.x - b.x, y: c.y - b.y }, { x: midX - a.x, y: midY - a.y }, Infinity)) return i;
        }
      } else {
        // trend / ray / extended: vzdálenost k úsečce mezi body
        const [a, b] = points;
        if (distToSegment(x, y, a.x, a.y, b.x, b.y) <= threshold) return i;
      }
    }
    return -1;
  }

  // Tažení kresby: úchop = posun koncového bodu, body = posun celé kresby
  // Začátek tažení: kresba, úchop ({time, price}, číslo u pozice nebo 'body'), výchozí pozice
  // kurzoru a kopie bodů – posun se vždy počítá od začátku tažení (bez driftu)
  function startDrag(s, index, handle, x, y) {
    const drawing = s.overlay.drawings[index];
    return {
      index: index,
      handle: handle,
      startX: x,
      startY: y,
      origin: drawing ? drawing.points.map((point) => ({ time: point.time, price: point.price })) : [],
      moved: false,
    };
  }

  // Tažení kresby: úchop = posun koncového bodu, body = posun celé kresby
  function dragDrawing(s, drag, x, y, magnet) {
    const drawing = s.overlay.drawings[drag.index];
    if (!drawing) return;
    if (drag.handle === 'body') {
      // Posun celé kresby o celé bary (logický index) a o rozdíl ceny
      const timeScale = s.chart.timeScale();
      const startLogical = timeScale.coordinateToLogical(drag.startX);
      const newLogical = timeScale.coordinateToLogical(x);
      const startPrice = s.candleSeries.coordinateToPrice(drag.startY);
      const newPrice = s.candleSeries.coordinateToPrice(y);
      if (startLogical === null || newLogical === null || startPrice === null || newPrice === null) return;
      const dBars = Math.round(newLogical - startLogical);
      const dPrice = newPrice - startPrice;
      // Pozice drží ceny na přesnosti instrumentu (dialog pak ukazuje 529.95, ne 529.9512…)
      const round = isPosition(drawing) ? (price) => roundPrice(s, price) : (price) => price;
      drawing.points = drag.origin.map((point) => ({
        time: logicalToTime(s, timeToLogical(s, point.time) + dBars),
        price: round(point.price + dPrice),
      }));
    } else {
      const snapped = snapPoint(s, x, y, magnet);
      if (!snapped) return;
      if (isPosition(drawing)) {
        // Úchop pozice: vstup, cíl, stop nebo pravý okraj (šířka)
        dragPositionHandle(s, drawing, drag.handle, snapped);
      } else {
        // Úchop bodu: čas a / nebo cena bodů na bar a cenu pod kurzorem (bod
        // kresby mění obojí, střed hrany obdélníku jen jeden rozměr)
        const { time, price } = drag.handle;
        if (time !== null && drawing.points[time]) drawing.points[time] = { ...drawing.points[time], time: snapped.time };
        if (price !== null && drawing.points[price]) drawing.points[price] = { ...drawing.points[price], price: snapped.price };
      }
    }
    drag.moved = true;
    redrawOverlay(s);
  }

  // Výchozí velikost písma textových kreseb (px); lze změnit v Settings kresby
  const TEXT_SIZE = 13;
  // Šířka řádku textu se zapnutým zalomením (Text wrap) v px
  const TEXT_WRAP_WIDTH = 240;

  // Hranice obdélníku v pixelech z protilehlých rohů a, b; prodloužená strana
  // (Extend left / right) sahá k levému okraji (0) / k paneWidth
  function rectBounds(a, b, options, paneWidth) {
    return {
      left: options.extendLeft ? 0 : Math.min(a.x, b.x),
      right: options.extendRight ? paneWidth : Math.max(a.x, b.x),
      top: Math.min(a.y, b.y),
      bottom: Math.max(a.y, b.y),
    };
  }

  // Odsazení popisku od čáry / okraje obdélníku a výška řádku vůči písmu
  const LABEL_GAP = 4;
  const LABEL_LINE_HEIGHT = 1.25;

  // Textový popisek kresby (záložka Text v Settings): u čar podél čáry
  // (natočený, nikdy vzhůru nohama) nad / na / pod čarou a na začátku /
  // uprostřed / na konci; u obdélníku uvnitř, u Fibonacci a štětce nad /
  // uprostřed / pod jejich ohraničením (labelGeometry)
  function drawLabel(ctx, s, drawing, paneWidth, paneHeight) {
    if (!drawing.text || drawing.preview || !drawingConfig.labelKinds.includes(drawing.kind)) return;
    const pixels = drawing.points.map((point) => pointToPixel(s, point));
    if (!pixels.length || pixels.some((pixel) => !pixel)) return;
    const options = drawing.options || {};
    const lineHeight = textSize(drawing) * LABEL_LINE_HEIGHT;
    const lines = String(drawing.text).split('\n');
    const total = lines.length * lineHeight;
    const hAlign = options.textHAlign || 'center';
    const vAlign = options.textVAlign || 'top';
    ctx.save();
    ctx.font = textFont(drawing);
    ctx.fillStyle = options.textColor || drawing.color || '#2962ff';
    ctx.textBaseline = 'middle';
    ctx.textAlign = hAlign;
    // x kotvy textu a horní hrana bloku řádků (u čáry v natočených souřadnicích)
    let x;
    let top;
    const geometry = labelGeometry(drawing, pixels, options, vAlign, paneWidth, paneHeight);
    if (geometry.box) {
      // Ohraničení: uvnitř (obdélník) se text drží u hrany uvnitř, jinak nad
      // horní / pod dolní hranou (posun o výšku textu ven)
      const { box, inside } = geometry;
      const outside = inside ? 0 : total + 2 * LABEL_GAP;
      x = hAlign === 'left' ? box.left + LABEL_GAP : hAlign === 'right' ? box.right - LABEL_GAP : (box.left + box.right) / 2;
      if (vAlign === 'top') top = box.top + LABEL_GAP - outside;
      else if (vAlign === 'bottom') top = box.bottom - LABEL_GAP - total + outside;
      else top = (box.top + box.bottom - total) / 2;
    } else {
      const [start, end] = geometry.segment;
      const length = Math.hypot(end.x - start.x, end.y - start.y);
      ctx.translate(start.x, start.y);
      ctx.rotate(Math.atan2(end.y - start.y, end.x - start.x));
      x = hAlign === 'left' ? LABEL_GAP : hAlign === 'right' ? length - LABEL_GAP : length / 2;
      top = vAlign === 'top' ? -LABEL_GAP - total : vAlign === 'bottom' ? LABEL_GAP : -total / 2;
    }
    lines.forEach((line, index) => ctx.fillText(line, x, top + lineHeight * (index + 0.5)));
    ctx.restore();
  }

  // Kde se kreslí popisek kresby (pixely plátna): {segment: [začátek, konec]}
  // u čar, {box, inside} u kreseb s plošným ohraničením
  function labelGeometry(drawing, pixels, options, vAlign, paneWidth, paneHeight) {
    const kind = drawing.kind;
    const p = pixels[0];
    if (kind === 'hline' || kind === 'cross') return { segment: [{ x: 0, y: p.y }, { x: paneWidth, y: p.y }] };
    if (kind === 'hray') return { segment: [p, { x: paneWidth, y: p.y }] };
    if (kind === 'vline') return { segment: [{ x: p.x, y: paneHeight }, { x: p.x, y: 0 }] };
    if (kind === 'rect') return { box: rectBounds(pixels[0], pixels[1], options, paneWidth), inside: true };
    // Štětec: ohraničení všech bodů tahu
    if (kind === 'brush') return { box: pointsBounds(pixels), inside: false };
    if (kind === 'fib' || kind === 'fibext') {
      // Fibonacci: šířka mezi body, výška od nejvyšší po nejnižší úroveň
      // (úrovně jsou seřazené, krajní jsou první a poslední)
      const [a, b] = pixels;
      const levels = fibLevels(kind);
      const levelY = (level) => b.y + (a.y - b.y) * level;
      return { box: pointsBounds([{ x: a.x, y: levelY(levels[0]) }, { x: b.x, y: levelY(levels[levels.length - 1]) }]), inside: false };
    }
    if (kind === 'channel' && pixels.length > 2) {
      // Kanál: popisek nad horní čarou, pod dolní čarou, uprostřed na ose
      // (druhá čára je posunutá o kolmou složku vektoru k třetímu bodu)
      const [a, b, c] = pixels;
      const off = channelOffset(a, b, c);
      const shift = (factor) => [
        { x: a.x + off.x * factor, y: a.y + off.y * factor },
        { x: b.x + off.x * factor, y: b.y + off.y * factor },
      ];
      // Horní je posunutá čára, když posun míří nahoru (záporné y); Top bere
      // horní čáru, Bottom dolní, Middle osu kanálu
      const upperFactor = off.y < 0 ? 1 : 0;
      let factor = 0.5;
      if (vAlign === 'top') factor = upperFactor;
      else if (vAlign === 'bottom') factor = 1 - upperFactor;
      return { segment: leftToRight(shift(factor)) };
    }
    if (kind === 'pitchfork' && pixels.length > 2) {
      // Pitchfork: podél střední linie z A ke středu B–C
      const [a, b, c] = pixels;
      return { segment: leftToRight([a, { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 }]) };
    }
    return { segment: leftToRight(pixels.slice(0, 2)) };
  }

  // Posun druhé čáry kanálu p1→p2: jen kolmá složka vektoru p1→třetí bod
  // (šířka kanálu; posun podél přímky šířku nemění) – kreslení, výběr i popisek
  function channelOffset(p1, p2, third) {
    const length = Math.hypot(p2.x - p1.x, p2.y - p1.y) || 1;
    const nx = -(p2.y - p1.y) / length;
    const ny = (p2.x - p1.x) / length;
    const perp = (third.x - p1.x) * nx + (third.y - p1.y) * ny;
    return { x: nx * perp, y: ny * perp };
  }

  // Úrovně Fibonacci retracementu / extension podle druhu kresby
  function fibLevels(kind) {
    return kind === 'fibext' ? FIBEXT_LEVELS : FIB_LEVELS;
  }

  // Ohraničení bodů {left, right, top, bottom} (smyčka – štětec může mít
  // tisíce bodů, rozbalení do Math.min by bylo zbytečně drahé)
  function pointsBounds(points) {
    const box = { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity };
    for (const point of points) {
      if (point.x < box.left) box.left = point.x;
      if (point.x > box.right) box.right = point.x;
      if (point.y < box.top) box.top = point.y;
      if (point.y > box.bottom) box.bottom = point.y;
    }
    return box;
  }

  // Úsečka zleva doprava (svislá zdola nahoru), aby text nebyl vzhůru nohama
  function leftToRight([a, b]) {
    return a.x < b.x || (a.x === b.x && a.y > b.y) ? [a, b] : [b, a];
  }

  // Písmo textové kresby podle její velikosti, tučnosti a kurzívy (sdílí kreslení i výběr)
  function textFont(drawing) {
    const options = drawing.options || {};
    return (options.italic ? 'italic ' : '') + (options.bold ? 'bold ' : '') + textSize(drawing) + 'px ' + LABEL_FONT;
  }

  function textSize(drawing) {
    return Number((drawing.options || {}).fontSize) || TEXT_SIZE;
  }

  // Řádky textu: konce řádků z dialogu, se zapnutým zalomením navíc po slovech
  // do šířky TEXT_WRAP_WIDTH (příliš dlouhé slovo zůstane na řádku samo)
  function textLines(ctx, drawing) {
    const lines = String(drawing.text || 'Text').split('\n');
    if (!(drawing.options || {}).wrap) return lines;
    const wrapped = [];
    for (const line of lines) {
      let current = '';
      for (const word of line.split(' ')) {
        const candidate = current ? current + ' ' + word : word;
        if (current && ctx.measureText(candidate).width > TEXT_WRAP_WIDTH) {
          wrapped.push(current);
          current = word;
        } else {
          current = candidate;
        }
      }
      wrapped.push(current);
    }
    return wrapped;
  }

  // Rozvržení textové kresby v pixelech plátna: obdélník (x, y = levý horní
  // roh v kotevním bodě), řádky, výška řádku a vnitřní okraj (větší
  // s pozadím nebo rámečkem, jako v TradingView)
  function textLayout(s, drawing, pixel) {
    const ctx = s.overlay.ctx;
    const options = drawing.options || {};
    ctx.save();
    ctx.font = textFont(drawing);
    const lines = textLines(ctx, drawing);
    const width = Math.max(...lines.map((line) => ctx.measureText(line).width));
    ctx.restore();
    const lineHeight = Math.round(textSize(drawing) * 1.3);
    const pad = options.background || options.border ? 6 : 2;
    return {
      x: pixel.x,
      y: pixel.y,
      w: width + 2 * pad,
      h: lines.length * lineHeight + 2 * pad,
      lines: lines,
      lineHeight: lineHeight,
      pad: pad,
    };
  }

  // Nakreslí text: volitelné pozadí a rámeček, pak řádky textu
  function drawText(ctx, s, drawing, pixel) {
    const options = drawing.options || {};
    const box = textLayout(s, drawing, pixel);
    ctx.save();
    ctx.setLineDash([]);
    if (options.background) {
      ctx.fillStyle = options.backgroundColor || 'rgba(41, 98, 255, 0.25)';
      ctx.fillRect(box.x, box.y, box.w, box.h);
    }
    if (options.border) {
      ctx.lineWidth = 1;
      ctx.strokeStyle = options.borderColor || '#787b86';
      ctx.strokeRect(box.x + 0.5, box.y + 0.5, box.w - 1, box.h - 1);
    }
    ctx.fillStyle = drawing.color || '#2962ff';
    ctx.font = textFont(drawing);
    ctx.textBaseline = 'middle';
    box.lines.forEach((line, index) => {
      ctx.fillText(line, box.x + box.pad, box.y + box.pad + box.lineHeight * (index + 0.5));
    });
    ctx.restore();
  }

  // Přerušování čar podle stylu kresby (solid / dashed / dotted)
  const LINE_DASHES = { solid: [], dashed: [6, 4], dotted: [1.5, 3] };

  // Barva kresby (#rrggbb nebo rgba) s průhledností výplně obdélníku a kanálu;
  // vlastní průhlednost barvy se násobí průhledností výplně
  function withAlpha(color, alpha) {
    const parsed = window.TickViewColorPicker.parseColor(color);
    if (!parsed) return 'rgba(41, 98, 255, ' + alpha + ')';
    return 'rgba(' + parsed.r + ', ' + parsed.g + ', ' + parsed.b + ', ' + Math.round(parsed.a * alpha * 1000) / 1000 + ')';
  }

  // Nakreslí úchopy označené kresby nebo kresby pod kurzorem (jako v TradingView):
  // kolečka v bodech kresby, u pozice čtverečky na úrovních Target / Stop;
  // text dostane přerušovaný rámeček (tažením za text se posouvá)
  function renderHandles(ctx, s, drawing) {
    const selected = s.overlay.selected === s.overlay.drawings.indexOf(drawing);
    if (!selected && s.overlay.hovered !== drawing) return;
    if (drawing.kind === 'text') {
      const pixel = pointToPixel(s, drawing.points[0]);
      if (!pixel) return;
      const box = textLayout(s, drawing, pixel);
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.strokeStyle = drawing.color || '#2962ff';
      ctx.strokeRect(box.x - 2, box.y - 2, box.w + 4, box.h + 4);
      ctx.restore();
      return;
    }
    if (drawing.kind === 'brush') return;
    // Úchopy vždy plnou čarou (i u přerušované kresby), výplň barvou pozadí grafu
    ctx.save();
    ctx.setLineDash([]);
    ctx.fillStyle = THEMES[s.themeName].layout.background.color;
    ctx.strokeStyle = HANDLE_COLOR;
    ctx.lineWidth = 1.5;
    const position = isPosition(drawing);
    for (const pixel of drawingHandles(s, drawing)) {
      if (!pixel) continue;
      // Pozice: úrovně Target (1) a Stop (2) čtverečkem, ostatní body kolečkem
      if (position && (pixel.handle === 1 || pixel.handle === 2)) {
        ctx.beginPath();
        ctx.rect(pixel.x - 4.5, pixel.y - 4.5, 9, 9);
        ctx.fill();
        ctx.stroke();
      } else {
        drawRing(ctx, s, pixel.x, pixel.y, 5);
      }
    }
    ctx.restore();
  }

  // Barvy měřiče jako v TradingView: růst modře, pokles červeně
  const MEASURE_UP = '#2962ff';
  const MEASURE_DOWN = '#f23645';

  // Délka intervalu v sekundách zkráceně: „3d 4h“, „12h 15m“, „45m“
  function formatDuration(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days) return days + 'd' + (hours ? ' ' + hours + 'h' : '');
    if (hours) return hours + 'h' + (minutes ? ' ' + minutes + 'm' : '');
    return minutes + 'm';
  }

  // Úsečka s otevřenou šipkou na konci (osy měřiče)
  function drawArrowLine(ctx, x1, y1, x2, y2) {
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const size = 6;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.moveTo(x2 - size * Math.cos(angle - Math.PI / 5), y2 - size * Math.sin(angle - Math.PI / 5));
    ctx.lineTo(x2, y2);
    ctx.lineTo(x2 - size * Math.cos(angle + Math.PI / 5), y2 - size * Math.sin(angle + Math.PI / 5));
    ctx.stroke();
  }

  // Index první svíčky s časem ≥ time (binární hledání v seřazených svíčkách)
  function firstCandleIndex(s, time) {
    let low = 0;
    let high = s.candles.length;
    while (low < high) {
      const middle = (low + high) >> 1;
      if (s.candles[middle].time < time) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  // Řádky štítku měřiče: Δcena (Δ %) Δticky / počet barů a čas / objem
  function measureLines(s, p1, p2) {
    const diff = p2.price - p1.price;
    const pct = p1.price ? (diff / p1.price) * 100 : 0;
    const ticks = priceTicks(s, diff);
    // Počet barů = rozdíl logických indexů (i mimo rozsah dat)
    const bars = Math.round(timeToLogical(s, p2.time) - timeToLogical(s, p1.time));
    const lines = [
      formatInstrumentPrice(s, diff) + ' (' + pct.toFixed(2) + '%) ' + ticks,
      bars + ' bars, ' + formatDuration(Math.abs(p2.time - p1.time)),
    ];
    // Objem svíček v měřeném úseku (instrumenty bez objemu řádek nemají)
    if (s.hasVolume) {
      const to = Math.max(p1.time, p2.time);
      let volume = 0;
      // Sčítá se jen měřený úsek – měřič se překresluje při každém pohybu grafu
      for (let index = firstCandleIndex(s, Math.min(p1.time, p2.time)); index < s.candles.length; index++) {
        if (s.candles[index].time > to) break;
        volume += s.candles[index].volume;
      }
      lines.push('Vol ' + formatVolume(volume));
    }
    return lines;
  }

  // Nakreslí měřič jako v TradingView: obdélník v barvě směru, šipky středem
  // (cena svisle, čas vodorovně) a štítek se statistikami pod / nad obdélníkem
  function renderMeasure(ctx, s, paneWidth, paneHeight) {
    const measure = s.measure;
    if (!measure || !measure.points || measure.points.length === 0) return;
    const p1 = measure.points[0];
    const p2 = measure.points[1] || s.overlay.previewPoint;
    if (!p2) return;
    const a = pointToPixel(s, p1);
    const b = pointToPixel(s, p2);
    if (!a || !b) return;
    const up = p2.price >= p1.price;
    const color = up ? MEASURE_UP : MEASURE_DOWN;
    const left = Math.min(a.x, b.x);
    const top = Math.min(a.y, b.y);
    const width = Math.abs(b.x - a.x);
    const height = Math.abs(b.y - a.y);
    const centerX = (a.x + b.x) / 2;
    const centerY = (a.y + b.y) / 2;
    ctx.save();
    ctx.fillStyle = withAlpha(color, 0.2);
    ctx.fillRect(left, top, Math.max(width, 1), Math.max(height, 1));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    // Šipky míří od prvního bodu ke druhému (krátké osy se nekreslí)
    if (height > 8) drawArrowLine(ctx, centerX, a.y, centerX, b.y);
    if (width > 8) drawArrowLine(ctx, a.x, centerY, b.x, centerY);

    // Štítek se statistikami: růst nad obdélníkem, pokles pod ním
    drawLabelBox(ctx, measureLines(s, p1, p2), centerX, up ? top : top + height, up, color, MEASURE_LABEL, paneWidth, paneHeight);
    ctx.restore();
  }

  // Rozměry štítků: měřič (větší) a pozice Long / Short
  const LABEL_FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif';
  const MEASURE_LABEL = { font: '13px ' + LABEL_FONT, lineHeight: 20, padX: 10, padY: 7, radius: 4, gap: 8 };
  const POSITION_LABEL = { font: '12px ' + LABEL_FONT, lineHeight: 16, padX: 8, padY: 4, radius: 3, gap: 4 };

  // Štítek jako v TradingView: plný zaoblený obdélník s bílým textem,
  // vodorovně na středu centerX, nad (above) nebo pod úrovní y; vždy celý
  // uvnitř grafu. size = rozměry (MEASURE_LABEL / POSITION_LABEL)
  function drawLabelBox(ctx, lines, centerX, y, above, color, size, paneWidth, paneHeight) {
    ctx.font = size.font;
    const width = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 2 * size.padX;
    const height = lines.length * size.lineHeight + 2 * size.padY;
    const boxX = Math.min(Math.max(2, centerX - width / 2), paneWidth - width - 2);
    const boxY = Math.min(Math.max(2, above ? y - height - size.gap : y + size.gap), paneHeight - height - 2);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(boxX, boxY, width, height, size.radius);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    lines.forEach((line, index) => ctx.fillText(line, boxX + width / 2, boxY + size.padY + size.lineHeight * (index + 0.5)));
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  // Šipka na konci přímky (trojúhelník ve směru čáry)
  function drawArrowHead(ctx, from, to, size) {
    const angle = Math.atan2(to.y - from.y, to.x - from.x);
    ctx.beginPath();
    ctx.moveTo(to.x, to.y);
    ctx.lineTo(to.x - size * Math.cos(angle - Math.PI / 6), to.y - size * Math.sin(angle - Math.PI / 6));
    ctx.lineTo(to.x - size * Math.cos(angle + Math.PI / 6), to.y - size * Math.sin(angle + Math.PI / 6));
    ctx.closePath();
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fill();
  }

  // Nakreslí jednu kresbu; preview = rozepsaná kresba sledující kurzor
  function drawShape(ctx, s, drawing, paneWidth, paneHeight) {
    let points = drawing.points;
    // Druhý bod rozepsané kresby se přizpůsobí aktuální pozici kurzoru
    if (drawing.preview && s.overlay.previewPoint && points.length === 1) {
      points = [points[0], { time: s.overlay.previewPoint.time, price: s.overlay.previewPoint.price }];
    }

    const color = drawing.color || '#2962ff';
    const options = drawing.options || {};
    // Šířka a styl čáry z nastavení kresby (výchozí 1.5 px plná)
    ctx.lineWidth = Number(options.width) || 1.5;
    ctx.setLineDash(LINE_DASHES[options.lineStyle] || []);
    ctx.strokeStyle = color;

    const toPixel = (point) => pointToPixel(s, point);

    if (isPosition(drawing)) {
      drawPosition(ctx, s, drawing, paneWidth, paneHeight);
      renderHandles(ctx, s, drawing);
      return;
    }
    if (isFrvp(drawing)) {
      drawFrvp(ctx, s, drawing, points, paneWidth, paneHeight);
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'hline') {
      const p = toPixel(points[0]);
      if (!p) return;
      ctx.beginPath();
      ctx.moveTo(0, p.y);
      ctx.lineTo(paneWidth, p.y);
      ctx.stroke();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'vline') {
      const x = timeToX(s, points[0].time);
      if (x === null) return;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, paneHeight);
      ctx.stroke();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'text') {
      const p = toPixel(points[0]);
      if (!p) return;
      drawText(ctx, s, drawing, p);
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'hray') {
      // Horizontální polopřímka: od bodu doprava až po okraj grafu
      const p = toPixel(points[0]);
      if (!p) return;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(paneWidth, p.y);
      ctx.stroke();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'cross') {
      // Kříž: horizontální + svislá linie přes celou plochu grafu
      const p = toPixel(points[0]);
      if (!p) return;
      ctx.beginPath();
      ctx.moveTo(0, p.y);
      ctx.lineTo(paneWidth, p.y);
      ctx.moveTo(p.x, 0);
      ctx.lineTo(p.x, paneHeight);
      ctx.stroke();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'sticker') {
      // Emoji/sticker: velký znak na anchoru (vizuál jako v TradingView)
      const p = toPixel(points[0]);
      if (!p) return;
      ctx.font = '26px sans-serif';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'center';
      ctx.fillText(drawing.text || '😀', p.x, p.y);
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
      renderHandles(ctx, s, drawing);
      return;
    }
    if (points.length < 2) {
      renderHandles(ctx, s, drawing);
      return;
    }
    const p1 = toPixel(points[0]);
    const p2 = toPixel(points[1]);
    if (!p1 || !p2) return;
    if (drawing.kind === 'trend') {
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'arrow') {
      // Trendová čára se šipkou na konci (druhý bod)
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
      drawArrowHead(ctx, p1, p2, 10);
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'channel') {
      // Paralelní kanál: přímka p1→p2 a druhá rovnoběžná; posun dává Jen KOLMÁ
      // složka vektoru p1→třetí bod (šířka kanálu) – posun podél přímky šířku
      // nemění, takže se kanál „nestahuje“ rychleji než kurzor
      const third = points.length > 2 ? points[2] : s.overlay.previewPoint;
      const thirdPixel = third ? toPixel(third) : null;
      if (!thirdPixel) {
        // Zatím jen první přímka (čekáme na třetí bod)
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
        renderHandles(ctx, s, drawing);
        return;
      }
      const { x: dx, y: dy } = channelOffset(p1, p2, thirdPixel);
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.moveTo(p1.x + dx, p1.y + dy);
      ctx.lineTo(p2.x + dx, p2.y + dy);
      ctx.stroke();
      ctx.fillStyle = withAlpha(color, 0.12);
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.lineTo(p2.x + dx, p2.y + dy);
      ctx.lineTo(p1.x + dx, p1.y + dy);
      ctx.closePath();
      ctx.fill();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'pitchfork') {
      // Pitchfork: tři body A, B, C; tři rovnoběžné linie ve směru A→střed BC
      const third = points.length > 2 ? points[2] : (drawing.preview && s.overlay.previewPoint ? s.overlay.previewPoint : null);
      const p3 = third ? toPixel(third) : null;
      if (!p3) {
        // Zatím spojnice A→B a B→C jako vodítko během kreslení
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        if (drawing.preview && s.overlay.previewPoint) {
          const pp = toPixel(s.overlay.previewPoint);
          if (pp) ctx.lineTo(pp.x, pp.y);
        }
        ctx.stroke();
        renderHandles(ctx, s, drawing);
        return;
      }
      const midX = (p2.x + p3.x) / 2;
      const midY = (p2.y + p3.y) / 2;
      const dirX = midX - p1.x;
      const dirY = midY - p1.y;
      // Každou linii (z A, B, C) prodloužíme ve směru A→střed až na okraj plochy
      const extend = (startX, startY) => {
        let factor = 1;
        if (dirX > 0) factor = Math.max(factor, (paneWidth - startX) / dirX);
        if (dirX < 0) factor = Math.max(factor, (0 - startX) / dirX);
        if (dirY > 0) factor = Math.max(factor, (paneHeight - startY) / dirY);
        if (dirY < 0) factor = Math.max(factor, (0 - startY) / dirY);
        return { x: startX + dirX * (factor + 0.1), y: startY + dirY * (factor + 0.1) };
      };
      ctx.beginPath();
      for (const start of [
        { x: p1.x, y: p1.y },
        { x: p2.x, y: p2.y },
        { x: p3.x, y: p3.y },
      ]) {
        const end = extend(start.x, start.y);
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(end.x, end.y);
      }
      ctx.stroke();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'fibext') {
      // Fibonacci extension: úrovně za první bod (včetně 1.618/2.618)
      const x1 = Math.min(p1.x, p2.x);
      const x2 = Math.max(p1.x, p2.x);
      ctx.font = '11px sans-serif';
      ctx.textBaseline = 'bottom';
      for (const level of FIBEXT_LEVELS) {
        const y = p2.y + (p1.y - p2.y) * level;
        const price = points[1].price + (points[0].price - points[1].price) * level;
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.moveTo(x1, y);
        ctx.lineTo(x2, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.fillText(level.toFixed(3).replace(/0+$/, '').replace(/\.$/, '') + ' (' + formatInstrumentPrice(s, price) + ')', x1 + 2, y - 1);
      }
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'ray' || drawing.kind === 'extended') {
      // Směrový vektor; prodloužíme na okraj canvasu (ray jen za druhý bod)
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const ts = [];
      if (dx !== 0) ts.push((0 - p1.x) / dx, (paneWidth - p1.x) / dx);
      if (dy !== 0) ts.push((0 - p1.y) / dy, (paneHeight - p1.y) / dy);
      if (!ts.length) return;
      const positive = ts.filter((t) => t >= 0);
      const negative = ts.filter((t) => t <= 0);
      const tEnd = Math.min(...(positive.length ? positive : [0]));
      const tStart = drawing.kind === 'extended' ? Math.max(...(negative.length ? negative : [0])) : 0;
      ctx.beginPath();
      ctx.moveTo(p1.x + tStart * dx, p1.y + tStart * dy);
      ctx.lineTo(p1.x + tEnd * dx, p1.y + tEnd * dy);
      ctx.stroke();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'rect') {
      // Prodloužený obdélník (Extend left / right) sahá až k okraji grafu
      const { left, right, top, bottom } = rectBounds(p1, p2, options, paneWidth);
      ctx.fillStyle = withAlpha(color, 0.12);
      ctx.fillRect(left, top, right - left, bottom - top);
      // Obrys: vodorovné hrany vždy, svislou hranu jen na neprodloužené straně
      ctx.beginPath();
      ctx.moveTo(left, top);
      ctx.lineTo(right, top);
      ctx.moveTo(left, bottom);
      ctx.lineTo(right, bottom);
      if (!options.extendLeft) {
        ctx.moveTo(left, top);
        ctx.lineTo(left, bottom);
      }
      if (!options.extendRight) {
        ctx.moveTo(right, top);
        ctx.lineTo(right, bottom);
      }
      ctx.stroke();
      renderHandles(ctx, s, drawing);
      return;
    }
    if (drawing.kind === 'brush') {
      // Štětec: kresba jako lomená čára přes všechny nasbírané body
      if (points.length < 2) return;
      ctx.beginPath();
      let started = false;
      for (const point of points) {
        const pixel = toPixel(point);
        if (!pixel) continue;
        if (!started) {
          ctx.moveTo(pixel.x, pixel.y);
          started = true;
        } else {
          ctx.lineTo(pixel.x, pixel.y);
        }
      }
      if (started) ctx.stroke();
      return;
    }
    if (drawing.kind === 'fib') {
      // Fibonacci retracement: úrovně mezi dvěma body, popisky vlevo
      const x1 = Math.min(p1.x, p2.x);
      const x2 = Math.max(p1.x, p2.x);
      ctx.font = '11px sans-serif';
      ctx.textBaseline = 'bottom';
      for (const level of FIB_LEVELS) {
        // Úroveň 0 je u druhého bodu, 1 u prvního (jako v TradingView)
        const y = p2.y + (p1.y - p2.y) * level;
        const price = points[1].price + (points[0].price - points[1].price) * level;
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.moveTo(x1, y);
        ctx.lineTo(x2, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.fillText(level.toFixed(3).replace(/0+$/, '').replace(/\.$/, '') + ' (' + formatInstrumentPrice(s, price) + ')', x1 + 2, y - 1);
      }
      renderHandles(ctx, s, drawing);
      return;
    }
  }

  // ---------- Long / Short position (jako v TradingView) ----------

  // Druhy kresby pozice; body: [vstup {čas začátku, cena vstupu},
  // cíl {čas konce, cena cíle}, stop {čas konce, cena stopu}]
  const POSITION_KINDS = { long: true, short: true };
  // Barvy zón a štítků pozice jako v TradingView
  const POSITION_PROFIT = '#089981';
  const POSITION_LOSS = '#f23645';
  const POSITION_NEUTRAL = '#787b86';

  function isPosition(drawing) {
    return !!(drawing && POSITION_KINDS[drawing.kind]);
  }

  // Body nové pozice po kliknutí: vstup v místě kliku, cíl a stop ve
  // vzdálenosti ~8 % výšky grafu (risk/reward 1:1), šířka ~15 % viditelných barů
  function positionPoints(s, point, kind) {
    const range = s.chart.timeScale().getVisibleLogicalRange();
    const bars = Math.max(8, Math.round(range ? (range.to - range.from) * 0.15 : 20));
    const end = logicalToTime(s, timeToLogical(s, point.time) + bars);
    const pane = getPaneGeometry(s);
    const top = s.candleSeries.coordinateToPrice(0);
    const bottom = s.candleSeries.coordinateToPrice(pane.height);
    const span = top !== null && bottom !== null ? Math.abs(top - bottom) : Math.abs(point.price) * 0.02;
    const tick = priceTick(s);
    const distance = Math.max(tick, roundPrice(s, span * 0.08));
    const entry = roundPrice(s, point.price);
    const sign = kind === 'long' ? 1 : -1;
    return [
      { time: point.time, price: entry },
      { time: end, price: roundPrice(s, entry + sign * distance) },
      { time: end, price: roundPrice(s, entry - sign * distance) },
    ];
  }

  // Obdélník pozice v pixelech (levý/pravý okraj, y vstupu, cíle a stopu)
  function positionBox(s, drawing) {
    const [start, target, stop] = drawing.points;
    if (!start || !target || !stop) return null;
    const x1 = timeToX(s, start.time);
    const x2 = timeToX(s, target.time);
    const yEntry = s.candleSeries.priceToCoordinate(start.price);
    const yTarget = s.candleSeries.priceToCoordinate(target.price);
    const yStop = s.candleSeries.priceToCoordinate(stop.price);
    if ([x1, x2, yEntry, yTarget, yStop].some((value) => value === null)) return null;
    return {
      x1, x2, yEntry, yTarget, yStop,
      left: Math.min(x1, x2),
      right: Math.max(x1, x2),
      top: Math.min(yTarget, yStop),
      bottom: Math.max(yTarget, yStop),
    };
  }

  // Úchopy pozice: 0 vstup, 1 cíl, 2 stop (na levém okraji), 3 šířka (pravý okraj u vstupu)
  function positionHandles(s, drawing) {
    const box = positionBox(s, drawing);
    if (!box) return [];
    return [
      { x: box.x1, y: box.yEntry, handle: 0 },
      { x: box.x1, y: box.yTarget, handle: 1 },
      { x: box.x1, y: box.yStop, handle: 2 },
      { x: box.x2, y: box.yEntry, handle: 3 },
    ];
  }

  // Tažení úchopu pozice; ceny zůstávají ve správném pořadí (long: stop <
  // vstup < cíl, short obráceně) a konec je vždy aspoň bar za začátkem
  function dragPositionHandle(s, drawing, handle, rawSnapped) {
    const points = drawing.points;
    const long = drawing.kind === 'long';
    const tick = priceTick(s);
    // Ceny úrovní vždy na přesnost instrumentu (tick), jako v TradingView
    const snapped = { time: rawSnapped.time, price: roundPrice(s, rawSnapped.price) };
    if (handle === 0) {
      const low = Math.min(points[1].price, points[2].price) + tick;
      const high = Math.max(points[1].price, points[2].price) - tick;
      if (high < low) return;
      const latest = logicalToTime(s, timeToLogical(s, points[1].time) - 1);
      points[0] = { time: Math.min(snapped.time, latest), price: Math.min(high, Math.max(low, snapped.price)) };
    } else if (handle === 1) {
      const price = long ? Math.max(snapped.price, points[0].price + tick) : Math.min(snapped.price, points[0].price - tick);
      points[1] = { time: points[1].time, price: price };
    } else if (handle === 2) {
      const price = long ? Math.min(snapped.price, points[0].price - tick) : Math.max(snapped.price, points[0].price + tick);
      points[2] = { time: points[2].time, price: price };
    } else if (handle === 3) {
      const earliest = logicalToTime(s, timeToLogical(s, points[0].time) + 1);
      const time = Math.max(snapped.time, earliest);
      points[1] = { time: time, price: points[1].price };
      points[2] = { time: time, price: points[2].price };
    }
  }

  // Výpočet pozice: množství podle rizika a simulace obchodu nad svíčkami.
  // Obchod se aktivuje první svíčkou od začátku pozice, která zasáhne cenu
  // vstupu; uzavře ho první svíčka, která zasáhne stop nebo cíl (při zásahu
  // obou ve stejné svíčce konzervativně stop); jinak je otevřený s P&L
  // podle závěru poslední svíčky v rozsahu pozice.
  function positionStats(s, drawing) {
    const [start, target, stop] = drawing.points;
    const long = drawing.kind === 'long';
    const entry = start.price;
    // Parametry výpočtu z kresby; chybějící doplní výchozí styl typu (normalizuje Python)
    const options = Object.assign({}, defaultStyle(s, drawing.kind).options, drawing.options || {});
    const account = Number(options.accountSize) || 0;
    const reward = Math.abs(target.price - entry);
    const risk = Math.abs(entry - stop.price);
    const riskAmount = options.riskUnit === 'amount' ? Number(options.risk) || 0 : (account * (Number(options.risk) || 0)) / 100;
    const lot = Number(options.lotSize) || 1;
    const qty = risk > 0 ? riskAmount / risk / lot : 0;
    const from = Math.min(start.time, target.time);
    const to = Math.max(start.time, target.time);
    let activated = null;
    let closed = null;
    let last = null;
    for (let index = firstCandleIndex(s, from); index < s.candles.length; index++) {
      const candle = s.candles[index];
      if (candle.time > to) break;
      if (!activated) {
        if (candle.low > entry || candle.high < entry) continue;
        activated = candle;
      }
      const hitStop = long ? candle.low <= stop.price : candle.high >= stop.price;
      const hitTarget = long ? candle.high >= target.price : candle.low <= target.price;
      if (hitStop || hitTarget) {
        closed = { time: candle.time, price: hitStop ? stop.price : target.price };
        break;
      }
      last = candle;
    }
    const exit = closed ? closed.price : activated && last ? last.close : null;
    const pnl = exit === null ? null : long ? exit - entry : entry - exit;
    return {
      long, entry, account, reward, risk, riskAmount, lot, qty,
      activated, closed, exit, pnl,
      exitTime: closed ? closed.time : last ? last.time : null,
      ratio: risk > 0 ? reward / risk : 0,
    };
  }

  // Množství pozice: velká čísla celá (forex 4464285), malá na 4 místa (krypto 0.2)
  function formatQty(value) {
    if (!isFinite(value)) return '—';
    if (Math.abs(value) >= 100) return String(Math.round(value));
    return String(Number(value.toFixed(4)));
  }

  // Částka účtu na 2 desetinná místa bez zbytečných nul (252500, 9875.5)
  function formatAmount(value) {
    return isFinite(value) ? String(Number(value.toFixed(2))) : '—';
  }

  // Nakreslí pozici: zóna cíle (zelená) a stopu (červená), čára vstupu,
  // průběh obchodu (sytější zóna a čárkovaná spojnice vstup → výstup) a štítky;
  // Target / Stop jen u označené pozice nebo pod kurzorem (jako v TradingView)
  function drawPosition(ctx, s, drawing, paneWidth, paneHeight) {
    const box = positionBox(s, drawing);
    if (!box) return;
    const width = Math.max(1, box.right - box.left);
    const stats = positionStats(s, drawing);
    ctx.save();
    ctx.setLineDash([]);
    ctx.fillStyle = withAlpha(POSITION_PROFIT, 0.2);
    ctx.fillRect(box.left, Math.min(box.yEntry, box.yTarget), width, Math.abs(box.yTarget - box.yEntry));
    ctx.fillStyle = withAlpha(POSITION_LOSS, 0.2);
    ctx.fillRect(box.left, Math.min(box.yEntry, box.yStop), width, Math.abs(box.yStop - box.yEntry));
    // Průběh obchodu od aktivace do uzavření / poslední svíčky
    if (stats.activated && stats.exit !== null && stats.exitTime !== null) {
      const startX = timeToX(s, stats.activated.time);
      const endX = timeToX(s, stats.exitTime);
      const exitY = s.candleSeries.priceToCoordinate(stats.exit);
      if (startX !== null && endX !== null && exitY !== null) {
        const fromX = Math.max(box.left, Math.min(startX, endX));
        const toX = Math.min(box.right, Math.max(startX, endX));
        ctx.fillStyle = withAlpha(stats.pnl >= 0 ? POSITION_PROFIT : POSITION_LOSS, 0.25);
        ctx.fillRect(fromX, Math.min(box.yEntry, exitY), Math.max(1, toX - fromX), Math.abs(exitY - box.yEntry));
        ctx.strokeStyle = POSITION_NEUTRAL;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(startX, box.yEntry);
        ctx.lineTo(endX, exitY);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
    // Čára vstupu přes celou šířku pozice
    ctx.strokeStyle = POSITION_NEUTRAL;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(box.left, box.yEntry);
    ctx.lineTo(box.right, box.yEntry);
    ctx.stroke();

    // Štítky: střed (P&L, množství, risk/reward) vždy, Target / Stop jen při výběru / najetí
    const centerX = (box.left + box.right) / 2;
    const percent = (diff) => (stats.entry ? ((diff / stats.entry) * 100).toFixed(2) : '0.00');
    const centerLines = [];
    const qtyText = 'Qty: ' + formatQty(stats.qty);
    if (stats.pnl !== null) {
      const sign = stats.pnl >= 0 ? '' : '−';
      const pnlText = sign + formatInstrumentPrice(s, Math.abs(stats.pnl));
      centerLines.push((stats.closed ? 'Closed P&L: ' : 'Open P&L: ') + pnlText + ', ' + qtyText);
    } else {
      centerLines.push(qtyText);
    }
    centerLines.push('Risk/reward ratio: ' + Number(stats.ratio.toFixed(2)));
    const centerColor = stats.pnl === null ? POSITION_NEUTRAL : stats.pnl >= 0 ? POSITION_PROFIT : POSITION_LOSS;
    // Long: střední štítek pod vstupem (v zóně stopu), short nad ním
    drawLabelBox(ctx, centerLines, centerX, box.yEntry, !stats.long, centerColor, POSITION_LABEL, paneWidth, paneHeight);
    const selected = s.overlay.drawings.indexOf(drawing) === s.overlay.selected;
    if (selected || s.overlay.hovered === drawing) {
      // „Target: Δcena (Δ %) ticky, Amount: účet ± výsledek“, u stopu se znaménkem minus
      const levelText = (title, diff, sign) =>
        title + ': ' + formatInstrumentPrice(s, diff) + ' (' + percent(diff) + '%) ' + priceTicks(s, diff) +
        ', Amount: ' + formatAmount(stats.account + sign * stats.qty * stats.lot * diff);
      drawLabelBox(ctx, [levelText('Target', stats.reward, 1)], centerX, box.yTarget, stats.long, POSITION_PROFIT, POSITION_LABEL, paneWidth, paneHeight);
      drawLabelBox(ctx, [levelText('Stop', stats.risk, -1)], centerX, box.yStop, !stats.long, POSITION_LOSS, POSITION_LABEL, paneWidth, paneHeight);
    }
    ctx.restore();
  }

  // ---------- Fixed range volume profile (FRVP jako v TradingView) ----------

  // Body kresby: [začátek rozsahu {čas, cena kliku}, konec rozsahu {čas, cena
  // kliku}] – ceny se nepoužívají, svislý rozsah boxu je low–high svíček
  const FRVP_KIND = 'frvp';
  // Nejvyšší počet řádků profilu (stejně jako FRVP_MAX_ROWS v Pythonu)
  const FRVP_MAX_ROWS = 1000;
  // Písmo hodnot objemu u řádků a nejmenší výška řádku, do které se vejdou (px)
  const FRVP_VALUE_FONT = '10px ' + LABEL_FONT;
  const FRVP_VALUE_MIN_ROW = 9;

  function isFrvp(drawing) {
    return !!drawing && drawing.kind === FRVP_KIND;
  }

  // Volby FRVP kresby; chybějící doplní výchozí styl typu (normalizuje Python)
  function frvpOptions(s, drawing) {
    return Object.assign({}, defaultStyle(s, FRVP_KIND).options, drawing.options || {});
  }

  // Přidá objem svíčky do řádků profilu: rovnoměrně podle překryvu rozsahu
  // low–high svíčky s řádkem (svíčka bez rozpětí celá do řádku své ceny);
  // růstová svíčka (close ≥ open) do up, klesající do down
  function addFrvpCandle(profile, candle) {
    const volume = Number(candle.volume) || 0;
    if (volume <= 0) return;
    const target = candle.close >= candle.open ? profile.up : profile.down;
    const { low, rowHeight, count } = profile;
    const rowOf = (price) => Math.min(count - 1, Math.max(0, Math.floor((price - low) / rowHeight)));
    const span = candle.high - candle.low;
    if (!(span > 0)) {
      target[rowOf(candle.close)] += volume;
      return;
    }
    const last = rowOf(candle.high);
    for (let row = rowOf(candle.low); row <= last; row++) {
      const rowLow = low + row * rowHeight;
      const overlap = Math.min(candle.high, rowLow + rowHeight) - Math.max(candle.low, rowLow);
      if (overlap > 0) target[row] += (volume * overlap) / span;
    }
  }

  // POC a Value Area z řádků profilu: POC = řádek s největším objemem, Value
  // Area se od POC rozšiřuje vždy o sousední řádek s větším objemem (nad / pod),
  // dokud nepokryje valueArea % celkového objemu. Vrací ceny {poc, vah, val}
  // a krajní řádky Value Area {vaLow, vaHigh}; bez objemu null
  function frvpLevels(profile, valueArea) {
    const { up, down, low, rowHeight, count } = profile;
    const volumeAt = (row) => up[row] + down[row];
    let total = 0;
    let pocRow = 0;
    for (let row = 0; row < count; row++) {
      const volume = volumeAt(row);
      total += volume;
      if (volume > volumeAt(pocRow)) pocRow = row;
    }
    if (!(total > 0)) return null;
    const target = (total * valueArea) / 100;
    let vaLow = pocRow;
    let vaHigh = pocRow;
    let covered = volumeAt(pocRow);
    while (covered < target && (vaLow > 0 || vaHigh < count - 1)) {
      const above = vaHigh < count - 1 ? volumeAt(vaHigh + 1) : -1;
      const below = vaLow > 0 ? volumeAt(vaLow - 1) : -1;
      if (above >= below) covered += volumeAt(++vaHigh);
      else covered += volumeAt(--vaLow);
    }
    return {
      vaLow, vaHigh,
      poc: low + (pocRow + 0.5) * rowHeight,
      vah: low + (vaHigh + 1) * rowHeight,
      val: low + vaLow * rowHeight,
    };
  }

  // Spočítá profil svíček first..last: rozsah low–high, mřížku řádků (počet
  // řádků, nebo ticky na řádek – nejvýš FRVP_MAX_ROWS), objem up / down po
  // řádcích, POC a Value Area; s vyvíjejícími se úrovněmi navíc POC / VAH /
  // VAL po každé svíčce (developing). Rozsah bez svíček = null
  function computeFrvp(s, first, last, options, developing) {
    if (last < first) return null;
    let low = Infinity;
    let high = -Infinity;
    for (let index = first; index <= last; index++) {
      low = Math.min(low, s.candles[index].low);
      high = Math.max(high, s.candles[index].high);
    }
    if (!isFinite(low) || !isFinite(high)) return null;
    const rowSize = Math.max(1, Math.round(Number(options.rowSize)));
    let count;
    let rowHeight;
    if (options.rowsLayout === 'ticks') {
      // Ticks Per Row: pevná výška řádku, poslední řádek může přesahovat high
      rowHeight = rowSize * priceTick(s);
      count = Math.max(1, Math.ceil((high - low) / rowHeight - 1e-9));
      if (count > FRVP_MAX_ROWS) {
        count = FRVP_MAX_ROWS;
        rowHeight = (high - low) / count;
      }
    } else {
      count = Math.min(FRVP_MAX_ROWS, rowSize);
      rowHeight = (high - low) / count;
    }
    // Svíčky bez rozpětí (high = low) – jeden řádek o velikosti ticku
    if (!(rowHeight > 0)) {
      rowHeight = priceTick(s);
      count = 1;
    }
    const profile = { low, high, rowHeight, count, up: new Float64Array(count), down: new Float64Array(count) };
    const valueArea = Number(options.valueArea);
    profile.developing = developing ? [] : null;
    for (let index = first; index <= last; index++) {
      const candle = s.candles[index];
      addFrvpCandle(profile, candle);
      if (developing) {
        const levels = frvpLevels(profile, valueArea);
        if (levels) profile.developing.push({ time: candle.time, poc: levels.poc, vah: levels.vah, val: levels.val });
      }
    }
    profile.levels = frvpLevels(profile, valueArea);
    return profile;
  }

  // Profil kresby s cache na objektu kresby: přepočet jen při změně rozsahu,
  // voleb výpočtu nebo dat v rozsahu (živá poslední svíčka, donačtená
  // historie) – kreslení probíhá při každém pohybu myši a grafu
  function frvpProfile(s, drawing, points, options) {
    const from = Math.min(points[0].time, points[1].time);
    const to = Math.max(points[0].time, points[1].time);
    const first = firstCandleIndex(s, from);
    const last = candleIndexAt(s, to);
    const developing = !!((options.devPoc || {}).visible || (options.devVa || {}).visible);
    const lastCandle = s.candles[last];
    const key = [
      first, last, (s.candles[first] || {}).time, lastCandle ? [lastCandle.time, lastCandle.high, lastCandle.low,
        lastCandle.close, lastCandle.volume].join(',') : '',
      options.rowsLayout, options.rowSize, options.valueArea, developing, s.priceDecimals,
    ].join('|');
    if (!drawing.frvpCache || drawing.frvpCache.key !== key) {
      drawing.frvpCache = { key: key, profile: computeFrvp(s, first, last, options, developing) };
    }
    return drawing.frvpCache.profile;
  }

  // Geometrie FRVP v pixelech: x obou bodů, levý / pravý okraj, horní / dolní
  // okraj boxu (high / low profilu; bez svíček v rozsahu střed grafu) a profil
  function frvpGeometry(s, drawing, points) {
    if (points.length < 2) return null;
    const x1 = timeToX(s, points[0].time);
    const x2 = timeToX(s, points[1].time);
    if (x1 === null || x2 === null) return null;
    const options = frvpOptions(s, drawing);
    const profile = frvpProfile(s, drawing, points, options);
    let top = null;
    let bottom = null;
    if (profile) {
      top = s.candleSeries.priceToCoordinate(profile.high);
      bottom = s.candleSeries.priceToCoordinate(profile.low);
    }
    if (top === null || bottom === null) {
      const middle = getPaneGeometry(s).height / 2;
      top = middle;
      bottom = middle;
    }
    return { x1, x2, left: Math.min(x1, x2), right: Math.max(x1, x2), top, bottom, profile, options };
  }

  // Úchopy FRVP jako v TradingView: začátek vlevo nahoře (high), konec vpravo
  // dole (low); tažení mění jen čas bodu (svislý rozsah určují svíčky)
  function frvpHandles(s, drawing) {
    const geometry = frvpGeometry(s, drawing, drawing.points);
    if (!geometry) return [];
    return [
      { x: geometry.x1, y: geometry.top, handle: { time: 0, price: null }, cursor: 'ew-resize' },
      { x: geometry.x2, y: geometry.bottom, handle: { time: 1, price: null }, cursor: 'ew-resize' },
    ];
  }

  // Nastaví tah podle čáry profilu {visible, color, width, lineStyle};
  // vrátí false, když je čára skrytá
  function applyFrvpStroke(ctx, line) {
    if (!line || !line.visible) return false;
    ctx.strokeStyle = line.color;
    ctx.lineWidth = Number(line.width) || 1;
    ctx.setLineDash(LINE_DASHES[line.lineStyle] || []);
    return true;
  }

  // Vodorovná čára úrovně profilu (POC, VAH, VAL) podle stylu čáry z voleb
  function drawFrvpLevel(ctx, s, line, price, left, right) {
    const y = s.candleSeries.priceToCoordinate(price);
    if (y === null || !applyFrvpStroke(ctx, line)) return;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
  }

  // Schodovitá čára vyvíjející se úrovně (POC / VAH / VAL po každé svíčce)
  // od první svíčky po pravý okraj boxu; xs = x svíček kroků (sdílené čarami)
  function drawFrvpDeveloping(ctx, s, line, developing, xs, field, right) {
    if (!developing.length || !applyFrvpStroke(ctx, line)) return;
    ctx.beginPath();
    let lastY = null;
    developing.forEach((step, index) => {
      const x = xs[index];
      const y = s.candleSeries.priceToCoordinate(step[field]);
      if (x === null || y === null) return;
      if (lastY === null) ctx.moveTo(x, y);
      else {
        ctx.lineTo(x, lastY);
        ctx.lineTo(x, y);
      }
      lastY = y;
    });
    if (lastY !== null) ctx.lineTo(right, lastY);
    ctx.stroke();
  }

  // Histogram profilu: řádek po řádku od levého / pravého okraje boxu; délka
  // podle objemu řádku vůči největšímu (Delta: |up − down|), barvy růstu
  // a poklesu (uvnitř Value Area sytější); Up/Down kreslí up a down za sebou
  function drawFrvpHistogram(ctx, s, geometry) {
    const { profile, options, left, right } = geometry;
    const { up, down, low, high, rowHeight, count, levels } = profile;
    const mode = options.volumeMode;
    const valueOf = (row) => (mode === 'delta' ? Math.abs(up[row] - down[row]) : up[row] + down[row]);
    let max = 0;
    for (let row = 0; row < count; row++) max = Math.max(max, valueOf(row));
    if (!(max > 0)) return;
    const histogramWidth = ((right - left) * Number(options.profileWidth)) / 100;
    const scale = histogramWidth / max;
    const fromRight = options.placement === 'right';
    // Úsek řádku [start, start + length) od okraje boxu směrem dovnitř
    const fillSegment = (start, length, y, height, color) => {
      if (length <= 0) return;
      ctx.fillStyle = color;
      ctx.fillRect(fromRight ? right - start - length : left + start, y, length, height);
    };
    ctx.font = FRVP_VALUE_FONT;
    ctx.textBaseline = 'middle';
    // Horní hrana řádku je dolní hranou dalšího – převod ceny jednou na hranu
    let yBottom = s.candleSeries.priceToCoordinate(low);
    for (let row = 0; row < count; row++) {
      const yTop = s.candleSeries.priceToCoordinate(Math.min(high, low + (row + 1) * rowHeight));
      const rowBottom = yBottom;
      yBottom = yTop;
      if (yTop === null || rowBottom === null) continue;
      const rowSpan = rowBottom - yTop;
      // Mezera 1 px mezi řádky, když jsou dost vysoké (jako v TradingView)
      const height = Math.max(1, rowSpan > 3 ? rowSpan - 1 : rowSpan);
      const inValueArea = row >= levels.vaLow && row <= levels.vaHigh;
      const upColor = inValueArea ? options.vaUpColor : options.upColor;
      const downColor = inValueArea ? options.vaDownColor : options.downColor;
      let length;
      if (mode === 'updown') {
        fillSegment(0, up[row] * scale, yTop, height, upColor);
        fillSegment(up[row] * scale, down[row] * scale, yTop, height, downColor);
        length = (up[row] + down[row]) * scale;
      } else {
        length = valueOf(row) * scale;
        fillSegment(0, length, yTop, height, mode === 'delta' && down[row] > up[row] ? downColor : upColor);
      }
      // Hodnota objemu řádku (Values) za koncem sloupce
      if (options.values && rowSpan >= FRVP_VALUE_MIN_ROW && valueOf(row) > 0) {
        const value = mode === 'delta' ? up[row] - down[row] : valueOf(row);
        ctx.fillStyle = options.valuesColor;
        ctx.textAlign = fromRight ? 'right' : 'left';
        const text = (value < 0 ? '−' : '') + formatVolume(Math.abs(value));
        ctx.fillText(text, fromRight ? right - length - 3 : left + length + 3, yTop + rowSpan / 2);
      }
    }
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  // Nakreslí FRVP: box rozsahu, histogram, vyvíjející se POC / VA a čáry
  // POC, VAH, VAL (Extend Right je prodlouží k pravému okraji grafu); bez
  // svíček v rozsahu jen svislé okraje, bez objemu box s upozorněním
  function drawFrvp(ctx, s, drawing, points, paneWidth, paneHeight) {
    const geometry = frvpGeometry(s, drawing, points);
    if (!geometry) return;
    const { profile, options, left, right, top, bottom } = geometry;
    ctx.save();
    ctx.setLineDash([]);
    if (!profile) {
      ctx.strokeStyle = HANDLE_COLOR;
      ctx.lineWidth = 1;
      ctx.setLineDash(LINE_DASHES.dashed);
      ctx.beginPath();
      ctx.moveTo(left, 0);
      ctx.lineTo(left, paneHeight);
      ctx.moveTo(right, 0);
      ctx.lineTo(right, paneHeight);
      ctx.stroke();
      ctx.restore();
      return;
    }
    ctx.fillStyle = options.boxColor;
    ctx.fillRect(left, top, Math.max(1, right - left), Math.max(1, bottom - top));
    const levels = profile.levels;
    if (!levels) {
      ctx.fillStyle = POSITION_NEUTRAL;
      ctx.font = FRVP_VALUE_FONT;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(tvTr('Bez dat objemu'), (left + right) / 2, (top + bottom) / 2);
      ctx.restore();
      return;
    }
    if (options.profile) drawFrvpHistogram(ctx, s, geometry);
    if (profile.developing) {
      // x svíček jednou pro všechny tři vyvíjející se čáry
      const xs = profile.developing.map((step) => timeToX(s, step.time));
      for (const [line, field] of [['devVa', 'vah'], ['devVa', 'val'], ['devPoc', 'poc']]) {
        drawFrvpDeveloping(ctx, s, options[line], profile.developing, xs, field, right);
      }
    }
    const lineEnd = options.extendRight ? paneWidth : right;
    for (const field of ['vah', 'val', 'poc']) drawFrvpLevel(ctx, s, options[field], levels[field], left, lineEnd);
    ctx.restore();
  }

  // Pořadová čísla barů bodů FRVP (záložka Coordinates) od první načtené svíčky
  function frvpBars(s, drawing) {
    return drawing.points.slice(0, 2).map((point) => Math.round(timeToLogical(s, point.time)));
  }

  // Čísla barů → časy bodů (Python je uloží z dialogu Settings); bez dat null
  function barsToTimes(id, bars) {
    const s = state.get(id);
    if (!s || !s.candles.length || !Array.isArray(bars)) return null;
    return bars.map((bar) => logicalToTime(s, Math.round(Number(bar) || 0)));
  }

  // ---------- replay (obchody a příkazy session, výběr startu) ----------

  // Režim overlaye pro výběr startu replaye (klik do grafu = čas startu)
  const REPLAY_PICK_MODE = 'replay_pick';
  // Barvy jako u měřiče (buy modrá, sell červená) a pozice (zisk / ztráta)
  const REPLAY_BUY = MEASURE_UP;
  const REPLAY_SELL = MEASURE_DOWN;
  const REPLAY_LABEL = { font: '11px ' + LABEL_FONT, height: 18, padX: 6 };

  // Živá data replaye z Pythonu: {currency, orders, trades} (mění se s cenou);
  // null = graf není v replayi (zruší i statická data a taženou úroveň)
  function setReplay(id, payload) {
    const s = state.get(id);
    if (!s) return;
    s.replay = payload || null;
    if (!s.replay) {
      s.replayStatic = null;
      s.replayDrag = null;
      applyIndicatorMarkers(s);
    }
    redrawOverlay(s);
  }

  // Statická data replaye: {closed, markers} – uzavřené obchody a šipky (mění se jen s obchody)
  function setReplayStatic(id, payload) {
    const s = state.get(id);
    if (!s) return;
    s.replayStatic = payload || null;
    applyIndicatorMarkers(s);
    redrawOverlay(s);
  }

  // Python zpracoval taženou úroveň (přijal nebo odmítl) – čára se vrátí na data
  function endReplayDrag(id) {
    const s = state.get(id);
    if (!s) return;
    s.replayDrag = null;
    redrawOverlay(s);
  }

  // Index svíčky, která obsahuje čas (s) – poslední svíčka s časem ≤ čas; -1 = žádná
  function candleIndexAt(s, time) {
    return firstCandleIndex(s, time + 1) - 1;
  }

  // Šipky vstupů a výstupů replaye na svíčkách grafu (čas = svíčka, která ho obsahuje)
  function replayMarkers(s) {
    if (!s.replayStatic || !s.candles.length) return [];
    const result = [];
    for (const marker of s.replayStatic.markers || []) {
      const index = candleIndexAt(s, marker.time);
      if (index < 0) continue;
      const buy = marker.side === 'buy';
      result.push({
        time: s.candles[index].time,
        position: buy ? 'belowBar' : 'aboveBar',
        shape: buy ? 'arrowUp' : 'arrowDown',
        color: marker.entry ? (buy ? REPLAY_BUY : REPLAY_SELL) : POSITION_NEUTRAL,
        text: marker.text || '',
      });
    }
    return result;
  }

  // Částka v měně účtu se znaménkem (štítky čar replaye)
  function formatMoney(value, currency) {
    if (value === null || value === undefined || !isFinite(value)) return '';
    const sign = value > 0 ? '+' : value < 0 ? '−' : '';
    return sign + formatAmount(Math.abs(value)) + ' ' + (currency || '');
  }

  // Úroveň (cena) s ohledem na právě taženou úroveň
  function replayLevel(s, id, field, price) {
    const drag = s.replayDrag;
    return drag && drag.id === id && drag.field === field ? drag.price : price;
  }

  // Štítek čáry replaye končící u pravého okraje (nebo u right): text v rámečku
  // barvy čáry a tlačítko ✕ (akce pro Python); oblast ✕ zapíše do s.replayHits
  // a vrátí levý okraj štítku (štítky blízkých čar se řadí vedle sebe)
  function drawReplayLabel(ctx, s, text, y, color, paneWidth, closeAction, right) {
    ctx.font = REPLAY_LABEL.font;
    const height = REPLAY_LABEL.height;
    const width = ctx.measureText(text).width + 2 * REPLAY_LABEL.padX + height;
    const x = Math.max(2, (right === undefined ? paneWidth - 8 : right) - width);
    const top = y - height / 2;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(x, top, width, height, 2);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x + REPLAY_LABEL.padX, y);
    const closeX = x + width - height;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(closeX, top + 3);
    ctx.lineTo(closeX, top + height - 3);
    ctx.stroke();
    ctx.fillText('✕', closeX + 5, y);
    ctx.textBaseline = 'alphabetic';
    s.replayHits.push({ x1: closeX, x2: closeX + height, y1: top, y2: top + height, action: closeAction });
    return x;
  }

  // Vodorovná čára úrovně replaye od x1 po pravý okraj (čárkovaná / plná)
  function drawReplayLine(ctx, x1, x2, y, color, dashed) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.setLineDash(dashed ? LINE_DASHES.dashed : []);
    ctx.beginPath();
    ctx.moveTo(x1, y);
    ctx.lineTo(x2, y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // SL / TP úroveň obchodu nebo příkazu (tažení, ✕ = odebrat); štítek blízko
  // štítku vstupu se odsune vlevo od něj, aby se nepřekrývaly
  function drawReplayStopTarget(ctx, s, item, field, entry, direction, x1, paneWidth, entryLabel) {
    const price = replayLevel(s, item.id, field, item[field]);
    if (price === null || price === undefined) return;
    const y = s.candleSeries.priceToCoordinate(price);
    if (y === null) return;
    const color = field === 'tp' ? POSITION_PROFIT : POSITION_LOSS;
    drawReplayLine(ctx, x1, paneWidth, y, color, true);
    const amount = (price - entry) * direction * (item.value_per_point || 0);
    const label = (field === 'tp' ? 'TP ' : 'SL ') + formatInstrumentPrice(s, price) + '  ' +
      formatMoney(amount, s.replay.currency);
    const overlaps = Math.abs(entryLabel.y - y) < REPLAY_LABEL.height + 2;
    drawReplayLabel(ctx, s, label, y, color, paneWidth, { type: 'replay_modify', id: item.id, field: field, price: null },
      overlaps ? entryLabel.left - 6 : undefined);
    s.replayHits.push({ id: item.id, field: field, y: y, x1: x1, x2: paneWidth });
  }

  // Čára vstupu otevřeného obchodu nebo ceny čekajícího příkazu se štítkem
  // a ✕, pod ní SL a TP; dragField = úroveň, kterou jde táhnout (cena příkazu)
  function drawReplayEntry(ctx, s, item, price, x1, label, labelColor, closeAction, dashed, dragField, paneWidth) {
    const y = s.candleSeries.priceToCoordinate(price);
    if (y === null) return;
    const long = item.side === 'buy';
    drawReplayLine(ctx, x1, paneWidth, y, long ? REPLAY_BUY : REPLAY_SELL, dashed);
    const entryLabel = { y: y, left: drawReplayLabel(ctx, s, label, y, labelColor, paneWidth, closeAction) };
    if (dragField) s.replayHits.push({ id: item.id, field: dragField, y: y, x1: x1, x2: paneWidth });
    for (const field of ['sl', 'tp']) {
      drawReplayStopTarget(ctx, s, item, field, price, long ? 1 : -1, x1, paneWidth, entryLabel);
    }
  }

  // Obchody a příkazy session: spojnice uzavřených obchodů, čáry otevřených
  // obchodů (vstup, SL, TP) a čekajících příkazů se štítky a tlačítky ✕
  function drawReplay(ctx, s, paneWidth) {
    s.replayHits = [];
    const replay = s.replay;
    if (!replay || !s.candles.length) return;
    ctx.save();
    // Uzavřené obchody: čárkovaná spojnice vstup → výstup (zelená zisk, červená ztráta)
    for (const trade of (s.replayStatic && s.replayStatic.closed) || []) {
      const from = pointToPixel(s, { time: trade.entry_time, price: trade.entry });
      const to = pointToPixel(s, { time: trade.exit_time, price: trade.exit });
      if (!from || !to) continue;
      ctx.strokeStyle = trade.pnl >= 0 ? POSITION_PROFIT : POSITION_LOSS;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // Otevřené obchody: plná čára vstupu od času vstupu, štítek s P&L (✕ = zavřít)
    for (const trade of replay.trades || []) {
      const label = '#' + trade.id + ' ' + (trade.side === 'buy' ? 'LONG ' : 'SHORT ') + trade.qty + '  ' +
        formatMoney(trade.pnl, replay.currency);
      drawReplayEntry(ctx, s, trade, trade.entry, Math.max(0, timeToX(s, trade.entry_time) || 0), label,
        trade.pnl >= 0 ? POSITION_PROFIT : POSITION_LOSS, { type: 'replay_close', id: trade.id }, false, null, paneWidth);
    }
    // Čekající příkazy: čárkovaná čára ceny (táhnout lze cenu, SL i TP; ✕ = zrušit)
    for (const order of replay.orders || []) {
      const price = replayLevel(s, order.id, 'price', order.price);
      const label = '#' + order.id + ' ' + (order.side === 'buy' ? 'BUY ' : 'SELL ') + order.type.toUpperCase() + ' ' +
        order.qty + ' @ ' + formatInstrumentPrice(s, price);
      drawReplayEntry(ctx, s, order, price, 0, label, order.side === 'buy' ? REPLAY_BUY : REPLAY_SELL,
        { type: 'replay_cancel', id: order.id }, true, 'price', paneWidth);
    }
    ctx.restore();
  }

  // Výběr startu replaye: svislá čára na svíčce pod kurzorem a zastínění
  // budoucnosti vpravo od ní (to se po startu skryje)
  function drawReplayPick(ctx, s, paneWidth, paneHeight) {
    const hover = s.overlay.hover;
    if (s.overlay.mode !== REPLAY_PICK_MODE || !hover) return;
    const x = timeToX(s, hover.time);
    if (x === null) return;
    ctx.save();
    ctx.fillStyle = withAlpha(REPLAY_BUY, 0.12);
    ctx.fillRect(x, 0, Math.max(0, paneWidth - x), paneHeight);
    ctx.strokeStyle = REPLAY_BUY;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, paneHeight);
    ctx.stroke();
    drawLabelBox(ctx, [tvTr('Start replaye')], x, 24, false, REPLAY_BUY, POSITION_LABEL, paneWidth, paneHeight);
    ctx.restore();
  }

  // Posune graf tak, aby svíčka obsahující čas (s) byla uprostřed při stejném přiblížení
  function showTime(id, time) {
    const s = state.get(id);
    if (!s || !s.candles.length) return;
    const index = Math.max(0, candleIndexAt(s, time));
    const timeScale = s.chart.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    const width = range ? range.to - range.from : 100;
    timeScale.setVisibleLogicalRange({ from: index - width / 2, to: index + width / 2 });
  }

  // Tlačítko (✕) nebo čára replaye pod kurzorem; tlačítka mají přednost
  function hitReplay(s, x, y) {
    if (!s.replay) return null;
    const button = s.replayHits.find((hit) => hit.action && x >= hit.x1 && x <= hit.x2 && y >= hit.y1 && y <= hit.y2);
    if (button) return button;
    return s.replayHits.find((hit) => hit.field && x >= hit.x1 && x <= hit.x2 && Math.abs(hit.y - y) <= HIT_TOLERANCE_PX) || null;
  }

  // Klik na tlačítko replaye (zavřít / zrušit / odebrat SL nebo TP), nebo
  // začátek tažení úrovně; vrací true, když událost patřila replayi
  function startReplayInteraction(s, x, y) {
    const hit = hitReplay(s, x, y);
    if (!hit) return false;
    if (hit.action) {
      s.finishedQueue.push(Object.assign({}, hit.action));
      return true;
    }
    const price = s.candleSeries.coordinateToPrice(y);
    if (price === null) return false;
    s.replayDrag = { id: hit.id, field: hit.field, price: roundPrice(s, Number(price)), moved: false };
    redrawOverlay(s);
    return true;
  }

  // Tažení úrovně replaye: cena na celé ticky podle polohy myši
  function dragReplayLevel(s, event) {
    if (s.replayDrag.sent) return;
    const { y } = localPoint(s, event);
    const price = s.candleSeries.coordinateToPrice(y);
    if (price === null) return;
    s.replayDrag.price = roundPrice(s, Number(price));
    s.replayDrag.moved = true;
    scheduleOverlayRedraw(s);
  }

  // Konec tažení: novou úroveň pošle Pythonu; čára drží taženou cenu, dokud
  // Python změnu nezpracuje (endReplayDrag)
  function finishReplayDrag(s) {
    const drag = s.replayDrag;
    if (!drag || drag.sent) return;
    if (drag.moved) {
      drag.sent = true;
      s.finishedQueue.push({ type: 'replay_modify', id: drag.id, field: drag.field, price: drag.price });
    } else {
      s.replayDrag = null;
      redrawOverlay(s);
    }
  }

  // ---------- alerty (čáry na grafu, zvuk) ----------

  // Barva čar alertů (jako hodiny alertu v TradingView)
  const ALERT_COLOR = '#ff9800';

  // Cenové úrovně aktivních alertů aktuálního symbolu: [{price, title}]
  function setAlertLines(id, lines) {
    const s = state.get(id);
    if (!s) return;
    const next = Array.isArray(lines) ? lines : [];
    // Obnova seznamu alertů posílá čáry i beze změny – přestavíme je jen při změně
    if (JSON.stringify(next) === JSON.stringify(s.alertLines)) return;
    s.alertLines = next;
    applyAlertLines(s);
  }

  // Nahradí čáry alertů na hlavní sérii (čárkovaná čára s popiskem na cenové ose)
  function applyAlertLines(s) {
    if (!s.candleSeries) return;
    for (const line of s.alertPriceLines || []) s.candleSeries.removePriceLine(line);
    s.alertPriceLines = (s.alertLines || []).map((line) =>
      s.candleSeries.createPriceLine({
        price: line.price,
        color: ALERT_COLOR,
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: line.title || 'Alert',
      })
    );
  }

  // Odroluje prvek stránky (řádek výsledků hledání) do viditelné části seznamu
  function scrollIntoView(elementId) {
    const node = document.getElementById(elementId);
    if (node) node.scrollIntoView({ block: 'nearest' });
  }

  // Sdílený zvukový kontext (prohlížeče omezují počet současných kontextů)
  let alertAudio = null;

  // Krátký dvojitý tón při spuštění alertu (Web Audio – bez zvukového souboru)
  function playSound() {
    try {
      if (!alertAudio) alertAudio = new (window.AudioContext || window.webkitAudioContext)();
      const audio = alertAudio;
      if (audio.state === 'suspended') audio.resume();
      [0, 0.22].forEach((offset) => {
        const oscillator = audio.createOscillator();
        const gain = audio.createGain();
        oscillator.frequency.value = 880;
        gain.gain.setValueAtTime(0.25, audio.currentTime + offset);
        gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + offset + 0.18);
        oscillator.connect(gain).connect(audio.destination);
        oscillator.start(audio.currentTime + offset);
        oscillator.stop(audio.currentTime + offset + 0.2);
      });
    } catch (error) {
      // Prohlížeč bez Web Audio (nebo zablokovaný zvuk) – alert ukáže aspoň toast
    }
  }

  // Překreslení overlaye v příštím snímku (více požadavků v jednom snímku = jedno překreslení)
  function scheduleOverlayRedraw(s) {
    if (s.overlayFrame) return;
    s.overlayFrame = requestAnimationFrame(() => {
      s.overlayFrame = 0;
      if (state.get(s.elementId) === s) redrawOverlay(s);
    });
  }

  // Překreslí všechny kresby na překryvném canvasu
  function redrawOverlay(s) {
    const canvas = s.overlay.canvas;
    if (!canvas.clientWidth || !canvas.clientHeight) return;
    const { ctx } = prepareCanvas(canvas, s.overlay.ctx);

    // Kresby kreslíme jen do plochy grafu (bez pásu cenové a časové osy);
    // souřadnice z LWC API jsou relativní k plátnu grafu, overlay je na stejném místě
    const pane = getPaneGeometry(s);
    ctx.save();
    ctx.beginPath();
    ctx.rect(pane.offsetX, pane.offsetY, pane.width, pane.height);
    ctx.clip();
    ctx.translate(pane.offsetX, pane.offsetY);
    drawIndicatorSegments(ctx, s);
    drawReplay(ctx, s, pane.width);
    drawReplayPick(ctx, s, pane.width, pane.height);
    if (highlightedGroups(s).length) drawSelectionMarkers(ctx, s, s.chart, pane.width, overlayMarkerEntries(s));
    if (!s.overlay.hidden) {
      const all = [...s.overlay.drawings];
      // Rozepsaná kresba už ve stylu, jaký dostane po dokončení
      if (s.overlay.pending) all.push({ ...s.overlay.pending, ...defaultStyle(s, s.overlay.pending.kind), preview: true });
      for (const drawing of all) {
        drawShape(ctx, s, drawing, pane.width, pane.height);
        drawLabel(ctx, s, drawing, pane.width, pane.height);
      }
      renderMeasure(ctx, s, pane.width, pane.height);
    }
    renderMagnetMarker(ctx, s);
    ctx.restore();
    redrawPaneMarkers(s);
    // Lišta kresby sleduje výběr a nástroj aktivního grafu
    if (s === activeState()) syncDrawingBar();
  }

  // Značka přichycení magnetem: kroužek na O/H/L/C, kam se bod kresby uloží
  function renderMagnetMarker(ctx, s) {
    const hover = s.overlay.hover;
    if (!s.overlay.mode || !hover || !hover.snapped) return;
    const pixel = pointToPixel(s, hover);
    if (!pixel) return;
    ctx.save();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = defaultStyle(s, s.overlay.mode).color;
    drawRing(ctx, s, pixel.x, pixel.y, 4.5);
    ctx.restore();
  }

  // ---------- snímek grafu (Chart snapshot: Download image / Copy image) ----------

  // Prvky HTML překryvu, které do snímku nepatří (tlačítka řádků legendy – ikony Material)
  const SNAPSHOT_SKIP = '.legend-ind-actions, .material-icons';

  // Vykreslí canvas source do snímku na místo prvku node (CSS px vůči origin);
  // bitmapa se přeškáluje na velikost prvku, takže nezáleží na pixel ratio
  function drawSnapshotCanvas(ctx, source, node, origin) {
    if (!source || !node) return;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height || !source.width || !source.height) return;
    ctx.drawImage(source, rect.left - origin.left, rect.top - origin.top, rect.width, rect.height);
  }

  // Překreslí HTML překryv grafu (legenda, titulky panelů, tabulky indikátorů)
  // do snímku: podbarvení prvků (i kulatá tečka stavu dat) a jejich přímé
  // texty na stejném místě s barvou a písmem z CSS. Rekurze předává
  // průhlednost předků; skrytý nebo vynechaný prvek přeskočí celý podstrom
  function drawSnapshotDom(ctx, node, origin, parentOpacity = 1, range = document.createRange()) {
    if (!node || node.matches(SNAPSHOT_SKIP)) return;
    const style = getComputedStyle(node);
    const opacity = parentOpacity * (parseFloat(style.opacity) || 0);
    if (style.display === 'none' || !opacity) return;
    // visibility se dědí, ale potomek ji může vrátit na visible – proto jen
    // přeskočí kreslení prvku, ne jeho podstrom
    if (style.visibility === 'visible') {
      ctx.save();
      ctx.globalAlpha = opacity;
      // Podbarvení se zaoblením rohů (roundRect zmenší příliš velký poloměr, 50 % = kruh)
      const background = window.TickViewColorPicker.parseColor(style.backgroundColor);
      const rect = background && background.a ? node.getBoundingClientRect() : null;
      if (rect && rect.width && rect.height) {
        ctx.fillStyle = style.backgroundColor;
        ctx.beginPath();
        const x = rect.left - origin.left;
        const y = rect.top - origin.top;
        if (ctx.roundRect) ctx.roundRect(x, y, rect.width, rect.height, parseFloat(style.borderTopLeftRadius) || 0);
        else ctx.rect(x, y, rect.width, rect.height);
        ctx.fill();
      }
      // Texty: pozici každého textového uzlu změří Range (svislý střed řádku)
      ctx.font = [style.fontStyle, style.fontWeight, style.fontSize, style.fontFamily].join(' ');
      ctx.fillStyle = style.color;
      ctx.textBaseline = 'middle';
      for (const child of node.childNodes) {
        if (child.nodeType !== Node.TEXT_NODE || !child.textContent.trim()) continue;
        range.selectNodeContents(child);
        const box = range.getBoundingClientRect();
        if (!box.width) continue;
        ctx.fillText(child.textContent.replace(/\s+/g, ' ').trim(), box.left - origin.left, box.top + box.height / 2 - origin.top);
      }
      ctx.restore();
    }
    for (const child of node.children) drawSnapshotDom(ctx, child, origin, opacity, range);
  }

  // Kolik grafů rozložení se jmenovitě uvede v názvu souboru snímku
  const SNAPSHOT_NAME_CHARTS = 4;

  // Část názvu souboru bez znaků nevhodných pro soubory („GC=F“ → „GC-F“, „^VIX“ → „VIX“)
  function fileNamePart(text) {
    return String(text || '').replace(/[^A-Za-z0-9.]+/g, '-').replace(/^-+|-+$/g, '');
  }

  // Název souboru snímku podle obsahu a času (místní čas):
  // jeden graf  TickView_BTCUSDT_1D_Binance_2026-11-27_10-34-09.png,
  // více grafů  TickView_BTCUSDT-1D_XAUUSD-1h_2026-11-27_10-34-09.png
  // (aktivní graf první, nad SNAPSHOT_NAME_CHARTS jen počet zbylých „+N“)
  function snapshotName(charts) {
    const active = activeState();
    const ordered = [active, ...charts.filter((s) => s !== active)].filter((s) => s && s.symbolInfo);
    let content;
    if (ordered.length === 1) {
      const info = ordered[0].symbolInfo;
      content = [info.symbol, info.timeframe, info.exchange].map(fileNamePart);
    } else {
      // Stejný symbol a timeframe ve více grafech se uvede jen jednou
      const labels = [...new Set(ordered.map((s) => [s.symbolInfo.symbol, s.symbolInfo.timeframe].map(fileNamePart).join('-')))];
      content = labels.slice(0, SNAPSHOT_NAME_CHARTS);
      if (labels.length > SNAPSHOT_NAME_CHARTS) content.push('+' + (labels.length - SNAPSHOT_NAME_CHARTS));
    }
    const now = new Date();
    const pad = (value) => String(value).padStart(2, '0');
    const date = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
    const time = pad(now.getHours()) + '-' + pad(now.getMinutes()) + '-' + pad(now.getSeconds());
    return ['TickView', ...content.filter(Boolean), date, time].join('_') + '.png';
  }

  // Sestaví snímek všech viditelných grafů rozložení jako v TradingView:
  // svíčky, osy a panely indikátorů (LWC takeScreenshot), kresby, legendy
  // a tabulky; vrací {canvas, name} (canvas v rozlišení obrazovky) nebo null
  function snapshot() {
    const charts = Array.from(state.values()).filter((s) => s.container.offsetWidth && s.container.offsetHeight);
    if (!charts.length) return null;
    // Obdélník kolem všech grafů (rozložení více grafů v okně)
    const rects = charts.map((s) => s.container.getBoundingClientRect());
    const origin = { left: Math.min(...rects.map((r) => r.left)), top: Math.min(...rects.map((r) => r.top)) };
    const width = Math.max(...rects.map((r) => r.right)) - origin.left;
    const height = Math.max(...rects.map((r) => r.bottom)) - origin.top;
    const dpr = window.devicePixelRatio || 1;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    // Pozadí podle motivu (i mezery mezi grafy rozložení; motiv mají všechny grafy stejný)
    ctx.fillStyle = THEMES[charts[0].themeName].layout.background.color;
    ctx.fillRect(0, 0, width, height);
    charts.forEach((s) => {
      // Pořadí vrstev jako na obrazovce: graf, kresby, panely, HTML překryvy nahoře
      drawSnapshotCanvas(ctx, s.chart.takeScreenshot(), s.chart.chartElement(), origin);
      drawSnapshotCanvas(ctx, s.overlay.canvas, s.overlay.canvas, origin);
      for (const pane of s.panesMap.values()) {
        drawSnapshotCanvas(ctx, pane.chart.takeScreenshot(), pane.chart.chartElement(), origin);
        drawSnapshotCanvas(ctx, pane.markers, pane.markers, origin);
        drawSnapshotDom(ctx, pane.title, origin);
      }
      for (const table of s.indicatorTables.values()) drawSnapshotDom(ctx, table.el, origin);
      drawSnapshotDom(ctx, s.legend, origin);
    });
    return { canvas, name: snapshotName(charts) };
  }

  return {
    init: init,
    destroy: destroy,
    snapshot: snapshot,
    setActive: markActive,
    setLayoutSync: setLayoutSync,
    popAllEvents: popAllEvents,
    setCandles: setCandles,
    prependCandles: prependCandles,
    updateCandles: updateCandles,
    assignDrawingId: assignDrawingId,
    barsToTimes: barsToTimes,
    setTheme: setTheme,
    setIndicators: setIndicators,
    setOverview: setOverview,
    setTimezone: setTimezone,
    setSymbolInfo: setSymbolInfo,
    setLegendStatus: setLegendStatus,
    setDrawingMode: setDrawingMode,
    setDrawings: setDrawings,
    setDrawingDefaults: setDrawingDefaults,
    setMagnet: setMagnet,
    setDrawingsHidden: setDrawingsHidden,
    addText: addText,
    trashDrawings: trashDrawings,
    setToolGroups: setToolGroups,
    setIcons: setIcons,
    setFavorites: setFavorites,
    setFavoritesVisible: setFavoritesVisible,
    setChartStyle: setChartStyle,
    setChartType: setChartType,
    setKeepTool: setKeepTool,
    setLocked: setLocked,
    addSticker: addSticker,
    setAlertLines: setAlertLines,
    setReplay: setReplay,
    setReplayStatic: setReplayStatic,
    endReplayDrag: endReplayDrag,
    showTime: showTime,
    setAlertKinds: setAlertKinds,
    setDrawingConfig: setDrawingConfig,
    playSound: playSound,
    scrollIntoView: scrollIntoView,
  };
})();
