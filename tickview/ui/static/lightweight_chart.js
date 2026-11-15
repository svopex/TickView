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
    const container = document.querySelector('[data-chart-id="' + id + '"]');
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

    // Překryvný canvas pro kreslení nad hlavním grafem
    const overlay = document.createElement('canvas');
    overlay.className = 'lwchart-overlay';
    mainHost.appendChild(overlay);

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
      overlay: {
        canvas: overlay,
        ctx: overlay.getContext('2d'),
        mode: null,
        pending: null,
        previewPoint: null,
        drawings: [],
      },
      elementId: id,
      paneCanvas: null,
      themeName,
      candles: [],
      byTime: new Map(),
      lastCandle: null,
      hovering: false,
      overlaySeries: [],
      panesMap: new Map(),
      syncing: false,
      lastIndicatorPayload: null,
      finishedQueue: [],
      lastClick: null,
    };
    state.set(id, s);

    // Legenda: svíčka pod kurzorem, mimo graf poslední svíčka
    chart.subscribeCrosshairMove((param) => {
      let bar = null;
      if (param.time !== undefined) bar = s.byTime.get(param.time) || null;
      s.hovering = bar !== null;
      renderLegend(s, bar || s.lastCandle);
    });

    // Panely indikátorů + překreslení kresby následují rozsah hlavního grafu
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      syncRange(s, chart, range);
      redrawOverlay(s);
    });

    // Přizpůsobení překryvného canvasu při změně velikosti hostitele
    const overlayResize = new ResizeObserver(() => redrawOverlay(s));
    overlayResize.observe(mainHost);

    // Kreslení: kliky na překryvném canvasu vytvářejí body kresby
    overlay.addEventListener('click', (event) => {
      s.lastClick = null;
      if (!s.overlay.mode) return;
      // Kliky mimo cenovou plochu (pravá osa, spodní časová osa) ignorujeme
      const pane = s.paneCanvas.getBoundingClientRect();
      if (
        event.clientX < pane.left ||
        event.clientX > pane.right ||
        event.clientY < pane.top ||
        event.clientY > pane.bottom
      ) {
        return;
      }
      const rect = overlay.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      // Převod pixel → (čas, cena) přes API grafu
      const time = s.chart.timeScale().coordinateToTime(x);
      const price = s.candleSeries.coordinateToPrice(y);
      s.lastClick = { x: x, y: y, time: time, price: price };
      if (time === null || price === null) return;
      handleDrawingClick(s, { time: Number(time), price: Number(price) });
    });
    overlay.addEventListener('mousemove', (event) => {
      if (!s.overlay.pending) return;
      const rect = overlay.getBoundingClientRect();
      s.overlay.previewPoint = {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      };
      redrawOverlay(s);
    });
    // Escape zruší rozepsanou kresbu
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && s.overlay.pending) {
        s.overlay.pending = null;
        s.overlay.previewPoint = null;
        redrawOverlay(s);
      }
    });

    renderLegend(s, null);
  }

  // Zpracuje klik při aktivním kreslení; hline a vline stačí jedním klikem
  function handleDrawingClick(s, point) {
    const kind = s.overlay.mode;
    if (kind === 'hline' || kind === 'vline') {
      finishDrawing(s, { kind: kind, points: [point] });
      return;
    }
    if (!s.overlay.pending) {
      s.overlay.pending = { kind: kind, points: [point] };
      redrawOverlay(s);
      return;
    }
    s.overlay.pending.points.push(point);
    finishDrawing(s, s.overlay.pending);
  }

  // Dokončí kresbu: přidá ji do seznamu, uloží payload pro backend a upozorní ho signálem
  function finishDrawing(s, drawing) {
    s.overlay.pending = null;
    s.overlay.previewPoint = null;
    s.overlay.drawings.push(drawing);
    redrawOverlay(s);
    // Hotové kresby se hromadí ve frontě a backend si je vyzvedává stránkovým
    // timerem přes popFinishedDrawing (DOM eventy z Vue komponenty nejsou
    // spolehlivé; fronta zvládne i více kreseb mezi dvěma ticky)
    s.finishedQueue.push({ kind: drawing.kind, points: drawing.points });
  }

  // Vyzvedne jednu dokončenou kresbu z fronty (nebo null, když je prázdná)
  function popFinishedDrawing(id) {
    const s = state.get(id);
    if (!s || !s.finishedQueue.length) return null;
    return s.finishedQueue.shift();
  }

  // Překreslí všechny kresby na překryvném canvasu
  function redrawOverlay(s) {
    const canvas = s.overlay.canvas;
    const ctx = s.overlay.ctx;
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // Kresby kreslíme jen do plochy grafu (bez pásu cenové a časové osy);
    // souřadnice z LWC API jsou relativní k plátnu grafu, overlay je na stejném místě
    s.paneCanvas = s.paneCanvas || s.mainHost.querySelector('canvas');
    const paneRect = s.paneCanvas ? s.paneCanvas.getBoundingClientRect() : null;
    const hostRect = canvas.getBoundingClientRect();
    const offsetX = paneRect ? paneRect.left - hostRect.left : 0;
    const offsetY = paneRect ? paneRect.top - hostRect.top : 0;
    const paneWidth = paneRect ? paneRect.width : width;
    const paneHeight = paneRect ? paneRect.height : height;

    ctx.save();
    ctx.beginPath();
    ctx.rect(offsetX, offsetY, paneWidth, paneHeight);
    ctx.clip();
    ctx.translate(offsetX, offsetY);
    const all = [...s.overlay.drawings];
    if (s.overlay.pending) all.push({ ...s.overlay.pending, preview: true });
    for (const drawing of all) {
      drawShape(ctx, s, drawing, paneWidth, paneHeight);
    }
    ctx.restore();
  }

  // Nakreslí jednu kresbu; preview = rozepsaná kresba sledující kurzor
  function drawShape(ctx, s, drawing, width, height) {
    const timeScale = s.chart.timeScale();
    const series = s.candleSeries;
    let points = drawing.points;
    // Druhý bod rozepsané kresby se přizpůsobí aktuální pozici kurzoru
    if (drawing.preview && s.overlay.previewPoint && points.length === 1) {
      const time = timeScale.coordinateToTime(s.overlay.previewPoint.x);
      const price = series.coordinateToPrice(s.overlay.previewPoint.y);
      if (time !== null && price !== null) {
        points = [points[0], { time: Number(time), price: Number(price) }];
      }
    }

    ctx.lineWidth = 1.5;
    ctx.strokeStyle = drawing.preview ? 'rgba(41, 98, 255, 0.8)' : '#2962ff';

    const toPixel = (point) => {
      const x = timeScale.timeToCoordinate(point.time);
      const y = series.priceToCoordinate(point.price);
      return x === null || y === null ? null : { x: x, y: y };
    };

    if (drawing.kind === 'hline') {
      const p = toPixel(points[0]);
      if (!p) return;
      ctx.beginPath();
      ctx.moveTo(0, p.y);
      ctx.lineTo(width, p.y);
      ctx.stroke();
      return;
    }
    if (drawing.kind === 'vline') {
      const x = timeScale.timeToCoordinate(points[0].time);
      if (x === null) return;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
      return;
    }
    if (points.length < 2) return;
    const p1 = toPixel(points[0]);
    const p2 = toPixel(points[1]);
    if (!p1 || !p2) return;
    if (drawing.kind === 'trend') {
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
      // Koncové body jako malé kroužky
      for (const p of [p1, p2]) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
        ctx.stroke();
      }
      return;
    }
    if (drawing.kind === 'ray' || drawing.kind === 'extended') {
      // Směrový vektor; prodloužíme na okraj canvasu (ray jen za druhý bod)
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const ts = [];
      if (dx !== 0) ts.push((0 - p1.x) / dx, (width - p1.x) / dx);
      if (dy !== 0) ts.push((0 - p1.y) / dy, (height - p1.y) / dy);
      if (!ts.length) return;
      const positive = ts.filter((t) => t >= 0);
      const negative = ts.filter((t) => t <= 0);
      const tEnd = Math.min(...(positive.length ? positive : [0]));
      const tStart = drawing.kind === 'extended' ? Math.max(...(negative.length ? negative : [0])) : 0;
      ctx.beginPath();
      ctx.moveTo(p1.x + tStart * dx, p1.y + tStart * dy);
      ctx.lineTo(p1.x + tEnd * dx, p1.y + tEnd * dy);
      ctx.stroke();
      return;
    }
    if (drawing.kind === 'rect') {
      const x = Math.min(p1.x, p2.x);
      const y = Math.min(p1.y, p2.y);
      const w = Math.abs(p2.x - p1.x);
      const h = Math.abs(p2.y - p1.y);
      ctx.fillStyle = 'rgba(41, 98, 255, 0.12)';
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
    }
  }

  // Zapne/vypne kreslicí mód (hline | trend | rect | null)
  function setDrawingMode(id, mode) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.mode = mode || null;
    s.overlay.pending = null;
    s.overlay.previewPoint = null;
    s.overlay.canvas.classList.toggle('drawing-active', !!mode);
    redrawOverlay(s);
  }

  // Nahradí kresby zobrazené k symbolu (načtené z backendu)
  function setDrawings(id, drawings) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.drawings = (drawings || []).map((d) => ({ kind: d.kind, points: d.points }));
    redrawOverlay(s);
  }

  // Synchronizuje viditelný rozsah mezi hlavním grafem a panely indikátorů
  function syncRange(s, source, range) {
    if (s.syncing || !range) return;
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
  }

  // Vytvoří panel indikátoru (RSI/MACD) pod hlavním grafem se synchronizovaným časem
  function addIndicatorPane(s, pane, theme) {
    const wrap = document.createElement('div');
    wrap.className = 'lwchart-pane lwchart-pane-' + pane.kind;
    const title = document.createElement('div');
    title.className = 'lwchart-pane-title';
    title.textContent = pane.title;
    const host = document.createElement('div');
    host.className = 'lwchart-pane-host';
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
    });

    const created = [];
    let baseSeries = null;
    for (const spec of pane.series) {
      let series;
      if (spec.type === 'histogram') {
        series = chart.addHistogramSeries({
          priceFormat: { type: 'price' },
          lastValueVisible: false,
          priceLineVisible: false,
        });
        series.setData(spec.data);
      } else {
        series = chart.addLineSeries({
          color: spec.color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        series.setData(spec.data);
      }
      if (!baseSeries && spec.type !== 'histogram') baseSeries = series;
      created.push(series);
    }

    // Úrovně (30/70 u RSI, 0 u MACD) jako přerušované cenové linky
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

    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => syncRange(s, chart, range));
    // Nový panel se rovná na rozsah hlavního grafu
    const mainRange = s.chart.timeScale().getVisibleLogicalRange();
    if (mainRange) chart.timeScale().setVisibleLogicalRange(mainRange);
    s.panesMap.set(pane.paneId, { chart, wrap, series: created });
  }

  // Nahradí všechny indikátory: overlay čáry + panely pod hlavním grafem
  function setIndicators(id, payload) {
    const s = state.get(id);
    if (!s) return;
    s.lastIndicatorPayload = payload;
    clearIndicators(id);
    const theme = THEMES[s.themeName];
    // Overlay čáry v hlavním grafu (SMA, EMA, Bollinger)
    for (const overlay of payload.overlays || []) {
      const series = s.chart.addLineSeries({
        color: overlay.color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      series.setData(overlay.data);
      s.overlaySeries.push(series);
    }
    // Samostatné panely (RSI, MACD)
    for (const pane of payload.panes || []) {
      addIndicatorPane(s, pane, theme);
    }
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
    // Panely indikátorů přegenerovat v novém motivu
    if (s.lastIndicatorPayload) setIndicators(id, s.lastIndicatorPayload);
  }

  // Diagnostika kreslení (používané při testování; vrací stav overlay vrstvy)
  function debugState(id) {
    const s = state.get(id);
    if (!s) return null;
    return {
      mode: s.overlay.mode,
      drawings: s.overlay.drawings.length,
      pending: !!s.overlay.pending,
      lastClick: s.lastClick,
      queue: s.finishedQueue.length,
    };
  }

  // Diagnostika: přístup k internímu stavu grafu (jen pro testy)
  function _chart(id) {
    return state.get(id)?.chart || null;
  }

  function _series(id) {
    return state.get(id)?.candleSeries || null;
  }

  return {
    init: init,
    setCandles: setCandles,
    updateCandle: updateCandle,
    setTheme: setTheme,
    setIndicators: setIndicators,
    clearIndicators: clearIndicators,
    setDrawingMode: setDrawingMode,
    setDrawings: setDrawings,
    popFinishedDrawing: popFinishedDrawing,
    debugState: debugState,
    _chart: _chart,
    _series: _series,
  };
})();
