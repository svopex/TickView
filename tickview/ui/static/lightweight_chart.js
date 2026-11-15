/* TickView – JS komponenta grafu založená na TradingView Lightweight Charts.
 *
 * Veškerá logika grafu je v tomto souboru; Python ji volá přes
 * window.TickViewChart.<funkce> (žádný inline JS).
 */
window.TickViewChart = (function () {
  'use strict';

  // Barvy grafu podle motivu (odpovídají stylu TradingView)
  const THEMES = {
    dark: {
      layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc' },
      grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
      border: '#2a2e39',
      up: '#26a69a',
      down: '#ef5350',
    },
    light: {
      layout: { background: { type: 'solid', color: '#ffffff' }, textColor: '#131722' },
      grid: { vertLines: { color: '#e0e3eb' }, horzLines: { color: '#e0e3eb' } },
      border: '#c9cdd4',
      up: '#26a69a',
      down: '#ef5350',
    },
  };

  // Stav jednotlivých instancí grafu (id kontejneru -> stav)
  const state = new Map();

  // Barva objemového sloupce podle směru svíčky (poloprůhledná)
  function volumeColor(candle) {
    return candle.close >= candle.open ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)';
  }

  // Cena s proměnným počtem desetinných míst podle řádu hodnoty
  function formatPrice(value) {
    if (!isFinite(value)) return '—';
    const abs = Math.abs(value);
    const maxDigits = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 5 : 8;
    return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: maxDigits });
  }

  // Objem zkráceně na K/M/B
  function formatVolume(value) {
    if (!isFinite(value)) return '—';
    if (value >= 1e9) return (value / 1e9).toFixed(2) + ' B';
    if (value >= 1e6) return (value / 1e6).toFixed(2) + ' M';
    if (value >= 1e3) return (value / 1e3).toFixed(2) + ' K';
    return value.toFixed(2);
  }

  // Legenda: OHLC, objem a změna svíčky v procentech
  function renderLegend(s, bar) {
    if (!bar) {
      s.legend.innerHTML = '';
      return;
    }
    const dir = bar.close >= bar.open ? 'up' : 'down';
    const changePct = bar.open ? ((bar.close - bar.open) / bar.open) * 100 : 0;
    const items = [
      ['O', formatPrice(bar.open)],
      ['H', formatPrice(bar.high)],
      ['L', formatPrice(bar.low)],
      ['C', formatPrice(bar.close)],
      ['V', formatVolume(bar.volume)],
    ];
    s.legend.innerHTML =
      items
        .map(([key, value]) => `<span class="ohlc-item">${key} <span class="ohlc-value">${value}</span></span>`)
        .join('') +
      `<span class="ohlc-item ${dir}">${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)} %</span>`;
  }

  // Vytvoří graf v kontejneru: hlavní panel se svíčkami + objem jako překryv
  function init(id, options) {
    const container = document.getElementById(id);
    if (!container || state.has(id)) return;
    const themeName = options && options.dark ? 'dark' : 'light';
    const theme = THEMES[themeName];

    container.innerHTML = '';

    // Legenda s OHLC hodnotami jako překryv nad grafem
    const legend = document.createElement('div');
    legend.className = 'lwchart-legend';
    container.appendChild(legend);

    // Sloupec panelů: hlavní graf + případné další panely (RSI/MACD v kroku 4)
    const panes = document.createElement('div');
    panes.className = 'lwchart-panes';
    container.appendChild(panes);

    const mainHost = document.createElement('div');
    mainHost.className = 'lwchart-host';
    panes.appendChild(mainHost);

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

    // Svíčky
    const candleSeries = chart.addCandlestickSeries({
      upColor: theme.up,
      downColor: theme.down,
      wickUpColor: theme.up,
      wickDownColor: theme.down,
      borderVisible: false,
    });

    // Objem jako překryvná série v dolní části hlavního panelu
    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    const s = {
      chart,
      candleSeries,
      volumeSeries,
      legend,
      panes,
      mainHost,
      themeName,
      candles: [],
      byTime: new Map(),
      lastCandle: null,
      hovering: false,
    };
    state.set(id, s);

    // Legenda: svíčka pod kurzorem, mimo graf poslední svíčka
    chart.subscribeCrosshairMove((param) => {
      let bar = null;
      if (param.time !== undefined) bar = s.byTime.get(param.time) || null;
      s.hovering = bar !== null;
      renderLegend(s, bar || s.lastCandle);
    });

    renderLegend(s, null);
  }

  // Nahradí celý dataset svíček (načtení historie)
  function setCandles(id, candles, opts) {
    const s = state.get(id);
    if (!s) return;
    // Lightweight Charts vyžaduje rostoucí časové značky bez duplicit
    const sorted = [...candles].sort((a, b) => a.time - b.time);
    const clean = [];
    s.byTime = new Map();
    for (const candle of sorted) {
      if (clean.length && clean[clean.length - 1].time === candle.time) {
        clean[clean.length - 1] = candle;
      } else {
        clean.push(candle);
      }
      s.byTime.set(candle.time, candle);
    }
    s.candles = clean;
    s.lastCandle = clean.length ? clean[clean.length - 1] : null;
    s.candleSeries.setData(clean);
    s.volumeSeries.setData(
      clean.map((candle) => ({ time: candle.time, value: candle.volume, color: volumeColor(candle) }))
    );
    // Zobrazit konec grafu (posledních N svíček), ne celou historii zmenšenou na minimum
    const visible = (opts && opts.visibleBars) || 150;
    s.chart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, clean.length - visible),
      to: clean.length + 4,
    });
    renderLegend(s, s.lastCandle);
  }

  // Inkrementální doplnění/aktualizace svíčky (živá data) – series.update, ne celý dataset
  function updateCandle(id, candle) {
    const s = state.get(id);
    if (!s || !s.candles.length) return;
    const last = s.candles[s.candles.length - 1];
    // Starší svíčku ignorujeme – graf se nevrací zpět v čase
    if (candle.time < last.time) return;
    s.candleSeries.update(candle);
    s.volumeSeries.update({ time: candle.time, value: candle.volume, color: volumeColor(candle) });
    if (candle.time === last.time) {
      s.candles[s.candles.length - 1] = candle;
    } else {
      s.candles.push(candle);
    }
    s.byTime.set(candle.time, candle);
    s.lastCandle = candle;
    if (!s.hovering) renderLegend(s, candle);
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
    s.candleSeries.applyOptions({
      upColor: theme.up,
      downColor: theme.down,
      wickUpColor: theme.up,
      wickDownColor: theme.down,
    });
    // Objemy překreslit, aby měly správné barvy po přepnutí motivu
    s.volumeSeries.setData(
      s.candles.map((candle) => ({ time: candle.time, value: candle.volume, color: volumeColor(candle) }))
    );
  }

  return { init: init, setCandles: setCandles, updateCandle: updateCandle, setTheme: setTheme };
})();
