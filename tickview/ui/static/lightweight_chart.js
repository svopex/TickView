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

  // Úrovně Fibonacci retracementu
  const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

  // Stav jednotlivých instancí grafu (id kontejneru -> stav)
  const state = new Map();

  // Definice skupin nástrojů pro flyout nabídky, když je přijme Python dřív než init
  const pendingToolGroups = new Map();

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
        color: '#2962ff',
        magnet: false,
        hidden: false,
        selected: -1,
        drag: null,
        measure: null,
      },
      elementId: id,
      toolGroups: pendingToolGroups.get(id) || [],
      paneCanvas: null,
      themeName,
      candles: [],
      byTime: new Map(),
      lastCandle: null,
      hovering: false,
      keepTool: false,
      locked: false,
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

    // Kreslení (aktivní nástroj): kliky na překryvném canvasu vytvářejí body
    overlay.addEventListener('click', (event) => {
      s.lastClick = null;
      if (!s.overlay.mode || s.overlay.mode === 'cursor') return;
      const pane = getPaneGeometry(s);
      const clickX = event.clientX - pane.hostLeft;
      const clickY = event.clientY - pane.hostTop;
      // Kliky mimo cenovou plochu (pravá osa, spodní časová osa) ignorujeme
      if (clickX < pane.offsetX || clickX > pane.offsetX + pane.width) return;
      if (clickY < pane.offsetY || clickY > pane.offsetY + pane.height) return;
      const point = snapPoint(s, clickX - pane.offsetX, clickY - pane.offsetY);
      s.lastClick = point;
      if (s.overlay.mode === 'text') {
        // Textový nástroj: anchory pošleme Pythonu, dialog se zeptá na text
        s.finishedQueue.push({
          type: 'text_request',
          time: point.time,
          price: point.price,
        });
        setDrawingMode(id, null);
        return;
      }
    if (s.overlay.mode === 'measure') {
      handleMeasureClick(s, point);
      return;
    }
      handleDrawingClick(s, point);
    });
    overlay.addEventListener('mousemove', (event) => {
      if (!s.overlay.pending && s.overlay.mode !== 'measure') return;
      const pane = getPaneGeometry(s);
      const x = event.clientX - pane.hostLeft - pane.offsetX;
      const y = event.clientY - pane.hostTop - pane.offsetY;
      const time = s.chart.timeScale().coordinateToTime(x);
      const price = s.candleSeries.coordinateToPrice(y);
      if (time === null || price === null) return;
      s.overlay.previewPoint = { time: Number(time), price: Number(price) };
      // Štětec: body se při tažení přidávají do rozepsané kresby
      if (s.overlay.mode === 'brush' && s.overlay.pending) {
        const points = s.overlay.pending.points;
        const last = points[points.length - 1];
        if (Math.abs(last.time - Number(time)) > 0 || Math.abs(last.price - Number(price)) > 0) {
          // Body přidáváme až po ~6 px, aby JSON kresby nebyly zbytečně velké
          const lastPixel = pointToPixel(s, last);
          if (!lastPixel || Math.hypot(x - lastPixel.x, y - lastPixel.y) > 6) {
            points.push({ time: Number(time), price: Number(price) });
          }
        }
      }
      redrawOverlay(s);
    });
    // Escape zruší rozepsanou kresbu / měřič / označení (jen mimo vstupní pole)
    document.addEventListener('keydown', (event) => {
      const tag = (event.target.tagName || '').toUpperCase();
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (event.key === 'Escape') {
        s.overlay.pending = null;
        s.overlay.previewPoint = null;
        s.measure = null;
        selectDrawing(s, -1);
        redrawOverlay(s);
      } else if ((event.key === 'Delete' || event.key === 'Backspace') && s.overlay.selected >= 0) {
        // Smazání označené kresby klávesou Delete
        deleteSelected(s);
      }
    });

    // Výběr a editace kreseb v režimu kurzoru: zachytávání na hostu, aby graf
    // nezačal panovat, když míříme na kresbu (když jsou kresby uzamčené, necháme
    // graf panovat bez zásahu)
    mainHost.addEventListener(
      'mousedown',
      (event) => {
        if (event.button !== 0 || s.overlay.mode || s.locked) return;
        const pane = getPaneGeometry(s);
        const x = event.clientX - pane.hostLeft - pane.offsetX;
        const y = event.clientY - pane.hostTop - pane.offsetY;
        const hit = hitHandle(s, x, y);
        if (hit) {
          event.stopPropagation();
          event.preventDefault();
          s.overlay.selected = hit.index;
          s.overlay.drag = { handle: hit.handle, lastX: x, lastY: y, moved: false };
          redrawOverlay(s);
          return;
        }
        const index = hitTest(s, x, y);
        if (index >= 0) {
          event.stopPropagation();
          event.preventDefault();
          s.overlay.selected = index;
          s.overlay.drag = { index: index, handle: 'body', lastX: x, lastY: y, moved: false };
          redrawOverlay(s);
        } else if (s.overlay.selected >= 0) {
          selectDrawing(s, -1);
          redrawOverlay(s);
        }
      },
      true
    );
    window.addEventListener('mousemove', (event) => {
      const drag = s.overlay.drag;
      if (!drag) return;
      const pane = getPaneGeometry(s);
      const x = event.clientX - pane.hostLeft - pane.offsetX;
      const y = event.clientY - pane.hostTop - pane.offsetY;
      dragDrawing(s, drag, x, y, event);
    });
    window.addEventListener('mouseup', () => {
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
        }
      }
      s.overlay.drag = null;
    });

    // Pravý klik: nad kresbou ji vybere a otevře menu se „Smazat objekt“;
    // mimo kresbu jen zruší výběr (žádné okamžité mazání)
    mainHost.addEventListener(
      'contextmenu',
      (event) => {
        if (s.overlay.mode || s.locked) return;
        const pane = getPaneGeometry(s);
        const x = event.clientX - pane.hostLeft - pane.offsetX;
        const y = event.clientY - pane.hostTop - pane.offsetY;
        const index = hitTest(s, x, y);
        if (index < 0) {
          selectDrawing(s, -1);
          redrawOverlay(s);
          return;
        }
        event.preventDefault();
        selectDrawing(s, index);
        redrawOverlay(s);
        showContextMenu(s, event.clientX, event.clientY);
      },
      true
    );
    // Dvojklik na textovou kresbu otevře editaci textu
    mainHost.addEventListener(
      'dblclick',
      (event) => {
        if (s.overlay.mode) return;
        const pane = getPaneGeometry(s);
        const x = event.clientX - pane.hostLeft - pane.offsetX;
        const y = event.clientY - pane.hostTop - pane.offsetY;
        const drawing = s.overlay.drawings[s.overlay.selected];
        if (drawing && drawing.kind === 'text' && drawing.text) {
          s.finishedQueue.push({
            type: 'text_edit',
            id: drawing.id,
            text: drawing.text,
          });
        } else {
          const index = hitTest(s, x, y);
          const target = s.overlay.drawings[index];
          if (target && target.kind === 'text' && target.id) {
            s.finishedQueue.push({ type: 'text_edit', id: target.id, text: target.text });
          }
        }
      },
      true
    );

    // Lišta nástrojů: skupiny otevírají flyout nabídku (jako v TradingView),
    // akční tlačítka (magnet/tužka/lock/oko/koš) posíláme do fronty událostí
    const toolbar = document.querySelector('.drawing-toolbar');
    if (toolbar) {
      toolbar.addEventListener('click', (event) => {
        const groupButton = event.target.closest('[data-tool-group]');
        const actionButton = event.target.closest('[data-tool-action]');
        if (groupButton) {
          openToolFlyout(s, groupButton.dataset.toolGroup, groupButton);
          return;
        }
        if (actionButton) {
          s.finishedQueue.push({ type: 'action', action: actionButton.dataset.toolAction });
        }
      });
    }
    // Klik mimo lištu a flyout nabídku flyout zavře; klik mimo kontextové menu ho zavře
    document.addEventListener('click', (event) => {
      if (!event.target.closest('.drawing-toolbar') && !event.target.closest('.tool-flyout')) {
        closeFlyout();
      }
      if (!event.target.closest('.drawing-contextmenu')) {
        closeContextMenu();
      }
    });

    renderLegend(s, null);
  }

  // Pravoklikové menu nad kresbou: odebrání kresby a všech kreseb
  function showContextMenu(s, clientX, clientY) {
    closeContextMenu();
    const menu = document.createElement('div');
    menu.className = 'drawing-contextmenu';
    const items = [
      ['Smazat objekt', () => { deleteSelected(s); redrawOverlay(s); }],
    ];
    for (const [label, handler] of items) {
      const item = document.createElement('div');
      item.className = 'drawing-contextmenu-item';
      item.textContent = label;
      item.addEventListener('click', () => {
        handler();
        closeContextMenu();
      });
      menu.appendChild(item);
    }
    menu.style.left = clientX + 'px';
    menu.style.top = clientY + 'px';
    document.body.appendChild(menu);
  }

  // Zavře pravoklikové menu (pokud existuje)
  function closeContextMenu() {
    document.querySelectorAll('.drawing-contextmenu').forEach((menu) => menu.remove());
  }

  // Odeberie všechny kresby (lokálně + fronta mazání pro backend)
  function clearAllDrawings(s) {
    for (const drawing of s.overlay.drawings) {
      if (drawing.id) s.finishedQueue.push({ type: 'delete', id: drawing.id });
    }
    s.overlay.drawings = [];
    s.overlay.selected = -1;
    redrawOverlay(s);
  }

  // Flyout nabídka skupiny nástrojů (pozice u tlačítka, jako v TradingView)
  function openToolFlyout(s, groupId, anchorButton) {
    closeFlyout();
    const group = (s.toolGroups || []).find((g) => g.id === groupId);
    if (!group) return;
    if (group.tools.length === 1) {
      // Skupina s jediným nástrojem se aktivuje rovnou přes frontu
      s.finishedQueue.push({ type: 'tool', mode: group.tools[0].mode });
      return;
    }
    const flyout = document.createElement('div');
    flyout.className = 'tool-flyout';
    for (const tool of group.tools) {
      const item = document.createElement('div');
      item.className = 'tool-flyout-item' + (s.overlay.mode === tool.mode ? ' active' : '');
      item.innerHTML = '<span class="material-icons tool-flyout-icon">' + tool.icon + '</span><span>' + tool.tooltip + '</span>';
      item.addEventListener('click', () => {
        s.finishedQueue.push({ type: 'tool', mode: tool.mode });
        closeFlyout();
      });
      flyout.appendChild(item);
    }
    const area = anchorButton.closest('.chart-area');
    flyout.style.left = anchorButton.offsetLeft + anchorButton.offsetWidth + 2 + 'px';
    flyout.style.top = anchorButton.offsetTop + 'px';
    area.appendChild(flyout);
  }

  // Zavře otevřenou flyout nabídku (pokud existuje)
  function closeFlyout() {
    document.querySelectorAll('.tool-flyout').forEach((flyout) => flyout.remove());
  }

  // Geometrie plátna grafu vůči hostu (offsety os) – cache se obnovuje při volání
  function getPaneGeometry(s) {
    s.paneCanvas = s.paneCanvas || s.mainHost.querySelector('canvas');
    const paneRect = s.paneCanvas ? s.paneCanvas.getBoundingClientRect() : null;
    const hostRect = s.mainHost.getBoundingClientRect();
    return {
      hostLeft: hostRect.left,
      hostTop: hostRect.top,
      offsetX: paneRect ? paneRect.left - hostRect.left : 0,
      offsetY: paneRect ? paneRect.top - hostRect.top : 0,
      width: paneRect ? paneRect.width : s.mainHost.clientWidth,
      height: paneRect ? paneRect.height : s.mainHost.clientHeight,
    };
  }

  // Převede (čas, cena) na pixely v souřadnicích plátna grafu
  function pointToPixel(s, point) {
    const px = s.chart.timeScale().timeToCoordinate(point.time);
    const py = s.candleSeries.priceToCoordinate(point.price);
    return px === null || py === null ? null : { x: px, y: py };
  }

  // Převod pixel → (čas, cena); s magnetem přichytí k času/ceně nejbližší svíčky
  function snapPoint(s, x, y) {
    const time = s.chart.timeScale().coordinateToTime(x);
    let price = s.candleSeries.coordinateToPrice(y);
    if (time === null || price === null) return null;
    let snappedTime = Number(time);
    if (s.overlay.magnet) {
      // Najdeme nejbližší svíčku podle času (časy v s.candles jsou v sekundách)
      let nearest = null;
      for (const candle of s.candles) {
        if (!nearest || Math.abs(candle.time - time) < Math.abs(nearest.time - time)) {
          nearest = candle;
        }
      }
      if (nearest) {
        // Přichytíme cenu k nejbližší OHLC hodnotě svíčky (tolerance ~12 px)
        const candidates = [nearest.open, nearest.high, nearest.low, nearest.close];
        let best = null;
        for (const candidate of candidates) {
          const py = s.candleSeries.priceToCoordinate(candidate);
          if (py === null) continue;
          if (!best || Math.abs(py - y) < Math.abs(best.py - y)) {
            best = { price: candidate, py: py };
          }
        }
        if (best && Math.abs(best.py - y) <= 12) price = best.price;
        snappedTime = nearest.time;
      }
    }
    return { time: snappedTime, price: Number(price) };
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
      s.byTime.set(candle.time, candle);
    }
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

  // ---------- kreslení (modelováno po TradingView) ----------

  // Zapne/vypne kreslicí mód (hline | vline | trend | ray | extended | rect |
  // brush | fib | text | measure | null = kurzor)
  function setDrawingMode(id, mode) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.mode = mode || null;
    s.overlay.pending = null;
    s.overlay.previewPoint = null;
    if (s.overlay.mode !== 'measure') s.measure = null;
    // Aktivní kreslicí nástroj = crosshair kurzor na overlay (v kurzoru je crosshair grafu)
    s.overlay.canvas.classList.toggle('drawing-active', !!mode && mode !== 'cursor');
    redrawOverlay(s);
  }

  // Nastaví definice skupin nástrojů pro flyout nabídky (přijde z Pythonu;
  // může přijít před init – uchová se do doby, než graf vznikne)
  function setToolGroups(id, groups) {
    const s = state.get(id);
    if (!s) {
      pendingToolGroups.set(id, groups);
      return;
    }
    s.toolGroups = groups;
  }

  // Nastaví barvu nových kreseb (paleta v liště nástrojů)
  function setDrawingColor(id, color) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.color = color || '#2962ff';
  }

  // Zapne/vypne magnet (přichytávání k OHLC nejbližší svíčky)
  function setMagnet(id, on) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.magnet = !!on;
  }

  // Skryje/zobrazí všechny kresby (ikona oka)
  function setDrawingsHidden(id, hidden) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.hidden = !!hidden;
    redrawOverlay(s);
  }

  // Nahradí kresby zobrazené k symbolu (načtené z backendu)
  function setDrawings(id, drawings) {
    const s = state.get(id);
    if (!s) return;
    s.overlay.drawings = (drawings || []).map((d) => ({
      id: d.id,
      kind: d.kind,
      points: d.points,
      color: d.color || '#2962ff',
      text: d.text || null,
    }));
    s.overlay.selected = -1;
    redrawOverlay(s);
  }

  // Přidá/aktualizuje textovou kresbu (po potvrzení v dialogu)
  function addText(id, time, price, text) {
    const s = state.get(id);
    if (!s) return;
    const drawing = { kind: 'text', points: [{ time: Number(time), price: Number(price) }], color: s.overlay.color, text: text };
    s.overlay.drawings.push(drawing);
    redrawOverlay(s);
    s.finishedQueue.push({ type: 'add', ...drawing });
  }

  function updateText(id, drawingId, text) {
    const s = state.get(id);
    if (!s) return;
    const drawing = s.overlay.drawings.find((d) => d.id === drawingId);
    if (!drawing) return;
    drawing.text = text;
    redrawOverlay(s);
    s.finishedQueue.push({ type: 'update', id: drawingId, points: drawing.points, text: text });
  }

  // Smaže označenou kresbu (nebo všechny, když není nic označené); vrátí počet
  function trashDrawings(id) {
    const s = state.get(id);
    if (!s || s.locked) return 0;
    if (s.overlay.selected >= 0) {
      removeDrawing(s, s.overlay.selected);
      selectDrawing(s, -1);
      return 1;
    }
    // Smazat vše: každá kresba s id jde do fronty pro backend
    for (const drawing of s.overlay.drawings) {
      if (drawing.id) s.finishedQueue.push({ type: 'delete', id: drawing.id });
    }
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
    if (drawing.id) s.finishedQueue.push({ type: 'delete', id: drawing.id });
    if (s.overlay.selected === index) s.overlay.selected = -1;
    redrawOverlay(s);
    return true;
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

  // Počet kliků potřebných pro každý nástroj (1 = jednoklikové, 3 = kanál/pitchfork)
  const DRAWING_CLICKS = {
    hline: 1,
    vline: 1,
    hray: 1,
    cross: 1,
    channel: 3,
    pitchfork: 3,
    brush: 0,
    text: 1,
    sticker: 1,
    measure: 2,
  };

  // Zpracuje klik při aktivním kreslení; jednoklikové nástroje hline, vline,
  // hray a cross stačí jedním klikem; kanál a pitchfork potřebují tři kliky;
  // štětec sbírá body při tažení a klikem se ukončí; ostatní jsou dvouklikové
  function handleDrawingClick(s, point) {
    const kind = s.overlay.mode;
    const needed = DRAWING_CLICKS[kind] !== undefined ? DRAWING_CLICKS[kind] : 2;
    if (!s.overlay.pending) {
      if (needed === 1) {
        finishDrawing(s, { kind: kind, points: [point] });
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
    const colored = { kind: drawing.kind, points: drawing.points, color: s.overlay.color };
    s.overlay.drawings.push(colored);
    redrawOverlay(s);
    // Hotové kresby se hromadí ve frontě a backend si je vyzvedává stránkovým
    // timerem přes popFinishedDrawing (DOM eventy z Vue komponenty nejsou
    // spolehlivé; fronta zvládne i více kreseb mezi dvěma ticky)
    s.finishedQueue.push({ type: 'add', ...colored });
    // TradingView: bez zapnutého režimu tužky se po nakreslení vrací kurzor
    if (!s.keepTool) {
      s.overlay.mode = null;
      s.overlay.canvas.classList.remove('drawing-active');
      s.finishedQueue.push({ type: 'tool', mode: null });
    }
  }

  // Zapne/vypne režim tužky (nástroj zůstává vybraný i po nakreslení)
  function setKeepTool(id, on) {
    const s = state.get(id);
    if (!s) return;
    s.keepTool = !!on;
  }

  // Zamče/odemkne kresby (zabrání editaci i mazání)
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
    const drawing = {
      kind: 'sticker',
      points: [{ time: Number(time), price: Number(price) }],
      color: s.overlay.color,
      text: emoji,
    };
    s.overlay.drawings.push(drawing);
    redrawOverlay(s);
    s.finishedQueue.push({ type: 'add', ...drawing });
  }

  // Vyzvedne jednu událost kreslení z fronty (nebo null, když je prázdná)
  function popFinishedDrawing(id) {
    const s = state.get(id);
    if (!s || !s.finishedQueue.length) return null;
    return s.finishedQueue.shift();
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

  // Překrytí: najde koncový bod (úchop) blízko kurzoru u označené kresby
  function hitHandle(s, x, y) {
    const index = s.overlay.selected;
    if (index < 0) return null;
    const drawing = s.overlay.drawings[index];
    if (!drawing || drawing.kind === 'brush') return null;
    const pixels = drawing.points.map((point) => pointToPixel(s, point));
    if (pixels.some((p) => p === null)) return null;
    for (let i = 0; i < pixels.length; i++) {
      if (Math.hypot(x - pixels[i].x, y - pixels[i].y) <= 8) {
        return { index: index, handle: i };
      }
    }
    return null;
  }

  // Překrytí: najde kresbu pod kurzorem (index v s.overlay.drawings) nebo -1
  function hitTest(s, x, y) {
    const timeScale = s.chart.timeScale();
    const series = s.candleSeries;
    const toPixel = (point) => {
      const px = timeScale.timeToCoordinate(point.time);
      const py = series.priceToCoordinate(point.price);
      return px === null || py === null ? null : { x: px, y: py };
    };
    const threshold = 6;
    for (let i = s.overlay.drawings.length - 1; i >= 0; i--) {
      const drawing = s.overlay.drawings[i];
      const points = drawing.points.map(toPixel);
      if (points.some((p) => p === null)) continue;
      if (drawing.kind === 'hline') {
        if (Math.abs(y - points[0].y) <= threshold) return i;
      } else if (drawing.kind === 'vline') {
        if (Math.abs(x - points[0].x) <= threshold) return i;
      } else if (drawing.kind === 'text') {
        if (Math.hypot(x - points[0].x, y - points[0].y) <= 14) return i;
      } else if (drawing.kind === 'rect') {
        const [a, b] = points;
        const x1 = Math.min(a.x, b.x);
        const x2 = Math.max(a.x, b.x);
        const y1 = Math.min(a.y, b.y);
        const y2 = Math.max(a.y, b.y);
        // Okraje obdélníku (ne vnitřek, aby se dalo kreslit přes něj)
        if (
          (Math.abs(x - x1) <= threshold || Math.abs(x - x2) <= threshold) && y >= y1 - threshold && y <= y2 + threshold
        ) return i;
        if (
          (Math.abs(y - y1) <= threshold || Math.abs(y - y2) <= threshold) && x >= x1 - threshold && x <= x2 + threshold
        ) return i;
      } else if (drawing.kind === 'fib') {
        const [a, b] = points;
        const x1 = Math.min(a.x, b.x);
        const x2 = Math.max(a.x, b.x);
        for (const level of FIB_LEVELS) {
          const yLevel = b.y + (a.y - b.y) * level;
          if (x >= x1 - threshold && x <= x2 + threshold && Math.abs(y - yLevel) <= threshold) return i;
        }
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
        const ddx = b.x - a.x;
        const ddy = b.y - a.y;
        const len = Math.hypot(ddx, ddy) || 1;
        const nx = -ddy / len;
        const ny = ddx / len;
        const perp = (c.x - a.x) * nx + (c.y - a.y) * ny;
        const offX = nx * perp;
        const offY = ny * perp;
        if (distToSegment(x, y, a.x + offX, a.y + offY, b.x + offX, b.y + offY) <= threshold) return i;
      } else if (drawing.kind === 'fibext') {
        const [a, b] = points;
        const x1 = Math.min(a.x, b.x);
        const x2 = Math.max(a.x, b.x);
        for (const level of [0, 0.618, 1, 1.618, 2.618]) {
          const yLevel = b.y + (a.y - b.y) * level;
          if (x >= x1 - threshold && x <= x2 + threshold && Math.abs(y - yLevel) <= threshold) return i;
        }
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
  function dragDrawing(s, drag, x, y) {
    const drawing = s.overlay.drawings[drag.index];
    if (!drawing) return;
    const snapped = snapPoint(s, x, y);
    if (!snapped) return;
    if (drag.handle === 'body') {
      // Posun celé kresby o rozdíl pixelů (přepočet přes čas/cenu)
      const prevTime = s.chart.timeScale().coordinateToTime(drag.lastX);
      const prevPrice = s.candleSeries.coordinateToPrice(drag.lastY);
      const newTime = s.chart.timeScale().coordinateToTime(x);
      const newPrice = s.candleSeries.coordinateToPrice(y);
      if (prevTime === null || newTime === null || prevPrice === null || newPrice === null) return;
      const dTime = Number(newTime) - Number(prevTime);
      const dPrice = Number(newPrice) - Number(prevPrice);
      for (const point of drawing.points) {
        point.time = Number(point.time) + dTime;
        point.price = point.price + dPrice;
      }
    } else {
      // Posun koncového bodu (úchop 0/1)
      const point = drawing.points[drag.handle];
      if (point) {
        point.time = snapped.time;
        point.price = snapped.price;
      }
    }
    drag.lastX = x;
    drag.lastY = y;
    drag.moved = true;
    redrawOverlay(s);
  }

  // Nakreslí úchopové čtverečky koncových bodů označené kresby
  function renderHandles(ctx, s, drawing) {
    if (s.overlay.selected !== s.overlay.drawings.indexOf(drawing)) return;
    if (drawing.kind === 'brush' || drawing.kind === 'text') return;
    const timeScale = s.chart.timeScale();
    const series = s.candleSeries;
    for (const point of drawing.points) {
      const px = timeScale.timeToCoordinate(point.time);
      const py = series.priceToCoordinate(point.price);
      if (px === null || py === null) continue;
      ctx.fillStyle = s.themeName === 'dark' ? '#d1d4dc' : '#131722';
      ctx.strokeStyle = drawing.color || '#2962ff';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.rect(px - 3.5, py - 3.5, 7, 7);
      ctx.fill();
      ctx.stroke();
    }
  }

  // Nakreslí měřič: rámeček + statistiky (Δcena, %, počet barů)
  function renderMeasure(ctx, s, paneWidth, paneHeight) {
    const measure = s.measure;
    if (!measure || !measure.points || measure.points.length === 0) return;
    const pixels = measure.points.map((point) => pointToPixel(s, point));
    if (pixels.some((p) => p === null)) return;
    const a = pixels[0];
    const b = pixels.length > 1 ? pixels[1] : s.overlay.previewPoint ? pointToPixel(s, s.overlay.previewPoint) : null;
    if (!b) return;
    const x = Math.min(a.x, b ? b.x : a.x);
    const y = Math.min(a.y, b ? b.y : a.y);
    const w = Math.abs((b ? b.x : a.x) - a.x) || 1;
    const h = Math.abs((b ? b.y : a.y) - a.y) || 1;
    ctx.save();
    ctx.fillStyle = 'rgba(41, 98, 255, 0.15)';
    ctx.fillRect(x, y, w, h);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = '#2962ff';
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);
    if (measure.points.length > 1) {
      // Statistiky: změna ceny a %, počet barů mezi body
      const p1 = measure.points[0];
      const p2 = measure.points[1];
      const diff = p2.price - p1.price;
      const pct = p1.price ? (diff / p1.price) * 100 : 0;
      let bars = 0;
      let idx1 = -1;
      let idx2 = -1;
      s.candles.forEach((candle, index) => {
        if (idx1 < 0 && candle.open_time >= p1.time) idx1 = index;
        if (idx2 < 0 && candle.open_time >= p2.time) idx2 = index;
      });
      if (idx1 >= 0 && idx2 >= 0) bars = Math.abs(idx2 - idx1);
      const label =
        (diff >= 0 ? '+' : '') + formatPrice(diff) + ' (' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + ' %) · ' + bars + ' barů';
      ctx.font = '12px sans-serif';
      ctx.textBaseline = 'top';
      ctx.fillStyle = s.themeName === 'dark' ? '#d1d4dc' : '#131722';
      ctx.fillText(label, x + 4, y + 4);
    }
    ctx.restore();
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
    const timeScale = s.chart.timeScale();
    const series = s.candleSeries;
    let points = drawing.points;
    // Druhý bod rozepsané kresby se přizpůsobí aktuální pozici kurzoru
    if (drawing.preview && s.overlay.previewPoint && points.length === 1) {
      points = [points[0], { time: s.overlay.previewPoint.time, price: s.overlay.previewPoint.price }];
    }

    const color = drawing.color || '#2962ff';
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = drawing.preview ? color : color;

    const toPixel = (point) => {
      const px = timeScale.timeToCoordinate(point.time);
      const py = series.priceToCoordinate(point.price);
      return px === null || py === null ? null : { x: px, y: py };
    };

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
      const x = timeScale.timeToCoordinate(points[0].time);
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
      ctx.fillStyle = color;
      ctx.font = '13px sans-serif';
      ctx.textBaseline = 'bottom';
      ctx.fillText(drawing.text || 'Text', p.x + 4, p.y - 4);
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
      const ddx = p2.x - p1.x;
      const ddy = p2.y - p1.y;
      const len = Math.hypot(ddx, ddy) || 1;
      const nx = -ddy / len;
      const ny = ddx / len;
      const perp = (thirdPixel.x - p1.x) * nx + (thirdPixel.y - p1.y) * ny;
      const dx = nx * perp;
      const dy = ny * perp;
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.moveTo(p1.x + dx, p1.y + dy);
      ctx.lineTo(p2.x + dx, p2.y + dy);
      ctx.stroke();
      ctx.fillStyle = 'rgba(41, 98, 255, 0.12)';
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
      const extLevels = [0, 0.618, 1, 1.618, 2.618];
      const x1 = Math.min(p1.x, p2.x);
      const x2 = Math.max(p1.x, p2.x);
      ctx.font = '11px sans-serif';
      ctx.textBaseline = 'bottom';
      for (const level of extLevels) {
        const y = p2.y + (p1.y - p2.y) * level;
        const price = points[1].price + (points[0].price - points[1].price) * level;
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.moveTo(x1, y);
        ctx.lineTo(x2, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.fillText(level.toFixed(3).replace(/0+$/, '').replace(/\.$/, '') + ' (' + formatPrice(price) + ')', x1 + 2, y - 1);
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
      const x = Math.min(p1.x, p2.x);
      const y = Math.min(p1.y, p2.y);
      const w = Math.abs(p2.x - p1.x);
      const h = Math.abs(p2.y - p1.y);
      ctx.fillStyle = 'rgba(41, 98, 255, 0.12)';
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
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
        ctx.fillText(level.toFixed(3).replace(/0+$/, '').replace(/\.$/, '') + ' (' + formatPrice(price) + ')', x1 + 2, y - 1);
      }
      renderHandles(ctx, s, drawing);
      return;
    }
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
    const pane = getPaneGeometry(s);
    ctx.save();
    ctx.beginPath();
    ctx.rect(pane.offsetX, pane.offsetY, pane.width, pane.height);
    ctx.clip();
    ctx.translate(pane.offsetX, pane.offsetY);
    if (!s.overlay.hidden) {
      const all = [...s.overlay.drawings];
      if (s.overlay.pending) all.push({ ...s.overlay.pending, preview: true, color: s.overlay.color });
      for (const drawing of all) {
        drawShape(ctx, s, drawing, pane.width, pane.height);
      }
      renderMeasure(ctx, s, pane.width, pane.height);
    }
    ctx.restore();
  }

  // Diagnostika: přístup k internímu stavu grafu (jen pro testy)
  function debugState(id) {
    const s = state.get(id);
    if (!s) return null;
    return {
      mode: s.overlay.mode,
      drawings: s.overlay.drawings.length,
      pending: !!s.overlay.pending,
      selected: s.overlay.selected,
      lastClick: s.lastClick,
      queue: s.finishedQueue.length,
      magnet: s.overlay.magnet,
      hidden: s.overlay.hidden,
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
    setDrawingColor: setDrawingColor,
    setMagnet: setMagnet,
    setDrawingsHidden: setDrawingsHidden,
    addText: addText,
    updateText: updateText,
    trashDrawings: trashDrawings,
    setToolGroups: setToolGroups,
    setKeepTool: setKeepTool,
    setLocked: setLocked,
    addSticker: addSticker,
    popFinishedDrawing: popFinishedDrawing,
    debugState: debugState,
    _chart: _chart,
    _series: _series,
  };
})();
