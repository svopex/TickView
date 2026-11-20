// Skripty prezentačního webu TickView: mobilní menu, záložky instalace,
// kopírování příkazů, tlačítko pro e-mail a ilustrační svíčkový graf s živou simulací.

"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";

// Jazyk stránky podle <html lang>: anglická verze (en/index.html) má "en",
// cokoli jiného zůstává česky (výchozí index.html)
const IS_ENGLISH = (document.documentElement.lang || "").toLowerCase().startsWith("en");

// Texty generované skriptem v obou jazycích; vybírá se podle IS_ENGLISH
const TEXTS = {
  cs: {
    copy: "Kopírovat",
    copied: "Zkopírováno",
    copyFailed: "Nelze kopírovat",
    chartLabel: "Ukázkový svíčkový graf s generovanými daty",
    alert: "Alert",
    percentSuffix: " %",
  },
  en: {
    copy: "Copy",
    copied: "Copied",
    copyFailed: "Copy failed",
    chartLabel: "Sample candlestick chart with generated data",
    alert: "Alert",
    percentSuffix: "%",
  },
};
const T = IS_ENGLISH ? TEXTS.en : TEXTS.cs;

document.addEventListener("DOMContentLoaded", () => {
  initNavToggle();
  initInstallTabs();
  initCopyButtons();
  initEmailButtons();
  initHeroChart();
});

// Tlačítka „Poslat e-mail“: adresa je v data-email obrácená a v base64, aby ji
// nenašli roboti. Složí se až po kliknutí a otevře poštovního klienta; na stránce
// se nezobrazí.
function initEmailButtons() {
  for (const button of document.querySelectorAll(".email-btn")) {
    button.addEventListener("click", () => {
      const email = atob(button.dataset.email || "").split("").reverse().join("");
      if (email.includes("@")) {
        window.location.href = `mailto:${email}`;
      }
    });
  }
}

// Rozbalení navigace na mobilu; klik na odkaz menu zase zavře.
function initNavToggle() {
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.getElementById("site-nav");
  if (!toggle || !nav) return;

  const setOpen = (open) => {
    nav.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", String(open));
  };

  toggle.addEventListener("click", () => setOpen(!nav.classList.contains("is-open")));
  nav.addEventListener("click", (event) => {
    if (event.target.closest("a")) setOpen(false);
  });
}

// Přepínání záložek s postupem instalace (macOS/Linux × Windows),
// včetně ovládání šipkami podle vzoru ARIA tabs.
function initInstallTabs() {
  const tabs = Array.from(document.querySelectorAll(".tab-list .tab"));
  if (!tabs.length) return;

  const select = (selected) => {
    for (const tab of tabs) {
      const active = tab === selected;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
    }
  };

  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      // Šipky vlevo/vpravo přesouvají výběr cyklicky mezi záložkami
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const step = event.key === "ArrowRight" ? 1 : -1;
      const next = tabs[(index + step + tabs.length) % tabs.length];
      select(next);
      next.focus();
    });
  }

  // Výchozí záložka podle operačního systému návštěvníka
  if (/Win/i.test(navigator.platform || navigator.userAgent)) {
    const windowsTab = tabs.find((tab) => tab.id === "tab-windows");
    if (windowsTab) select(windowsTab);
  }
}

// Tlačítka „Kopírovat“ (Copy) u příkazů; text bere z elementu podle data-copy-target.
function initCopyButtons() {
  for (const button of document.querySelectorAll(".copy-btn")) {
    button.addEventListener("click", async () => {
      const source = document.getElementById(button.dataset.copyTarget);
      if (!source) return;
      try {
        await navigator.clipboard.writeText(source.textContent.trim());
        button.textContent = T.copied;
        button.classList.add("is-copied");
      } catch {
        // Schránka nemusí být dostupná (např. bez HTTPS) – uživatel zkopíruje ručně
        button.textContent = T.copyFailed;
      }
      setTimeout(() => {
        button.textContent = T.copy;
        button.classList.remove("is-copied");
      }, 1800);
    });
  }
}

// Deterministický generátor pseudonáhodných čísel (mulberry32),
// aby graf vypadal při každém načtení stejně.
// Parametr seed: celé číslo, výchozí stav generátoru.
function createRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}


// Periody klouzavých průměrů v ilustračním grafu (popisky jsou v index.html)
const EMA_PERIOD = 20;
const SMA_PERIOD = 50;

// Směr svíčky pro třídy CSS (…-up / …-down)
const direction = (candle) => (candle.close >= candle.open ? "up" : "down");

// Vytvoří jednu svíčku navazující na předchozí cenu.
// Parametry: random – generátor, open – otevírací cena, step – pořadí svíčky (pro trend).
function createCandle(random, open, step) {
  // Náhodná procházka se sinusovým trendem, ať graf není jen šum
  const drift = (random() - 0.5) * 240 + Math.sin(step / 11) * 70;
  const close = open + drift;
  return {
    open,
    close,
    high: Math.max(open, close) + random() * 120,
    low: Math.min(open, close) - random() * 120,
    volume: 25 + random() * 60 + Math.abs(drift) / 3,
  };
}

// Klouzavé průměry nad zavíracími cenami; začátek bez dostatku dat je null.
// Parametry: candles – pole svíček, period – délka průměru.
function computeEma(candles, period) {
  const k = 2 / (period + 1);
  let ema = null;
  return candles.map((candle, index) => {
    ema = ema === null ? candle.close : candle.close * k + ema * (1 - k);
    return index >= period - 1 ? ema : null;
  });
}

function computeSma(candles, period) {
  let sum = 0;
  return candles.map((candle, index) => {
    sum += candle.close;
    if (index >= period) sum -= candles[index - period].close;
    return index >= period - 1 ? sum / period : null;
  });
}

// Formát ceny jako v aplikaci: dvě desetinná místa, oddělovače podle jazyka
// stránky (česky 64 200,00, anglicky 64,200.00).
const priceFormat = new Intl.NumberFormat(IS_ENGLISH ? "en-US" : "cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// Pomocník pro vytvoření SVG elementu s atributy (geometrie a třídy, žádné inline styly).
function svgElement(tag, attributes, parent) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (parent) parent.appendChild(element);
  return element;
}

// Ilustrační graf v úvodu: svíčky, objem, EMA, SMA, čára alertu,
// poslední cena, crosshair s OHLC legendou a simulované živé doplňování.
function initHeroChart() {
  const container = document.getElementById("hero-chart");
  const legend = document.getElementById("legend-values");
  if (!container || !legend) return;

  const CANDLE_COUNT = 80;
  const TICKS_PER_CANDLE = 10;
  const AXIS_WIDTH = 72;
  const random = createRandom(20261118);

  // Políčka legendy podle data-field (open, high, low, close, change)
  const legendFields = Object.fromEntries(
    Array.from(legend.querySelectorAll("[data-field]"), (element) => [element.dataset.field, element]),
  );

  // Počáteční historie – delší o periodu SMA, aby průměr měl data od začátku
  const candles = [];
  let price = 64200;
  for (let step = 0; step < CANDLE_COUNT + SMA_PERIOD; step++) {
    const candle = createCandle(random, price, step);
    candles.push(candle);
    price = candle.close;
  }

  // pointer: poloha kurzoru nad grafem {x, y} nebo null; layout: rozměry posledního vykreslení
  const state = { candles, step: candles.length, tick: 0, pointer: null, alertPrice: null, layout: null };

  // Graf se kreslí do vlastní skupiny, kterou překreslení vymění; crosshair
  // nad ní vzniká jen jednou a při překreslení se jen posouvá
  const svg = svgElement("svg", { role: "img", "aria-label": T.chartLabel }, container);
  const chartGroup = svgElement("g", {}, svg);
  const crosshair = svgElement("g", { visibility: "hidden" }, svg);
  const crossVertical = svgElement("line", { class: "crosshair-line", y1: 0 }, crosshair);
  const crossHorizontal = svgElement("line", { class: "crosshair-line", x1: 0 }, crosshair);
  const crossTag = createAxisTag(crosshair, "crosshair");

  // Štítek ceny na pravé ose (poslední cena, crosshair).
  // Parametry: parent – skupina SVG, prefix – předpona tříd (…-tag, …-text).
  function createAxisTag(parent, prefix) {
    return {
      rect: svgElement("rect", { class: `${prefix}-tag`, width: AXIS_WIDTH - 4, height: 20, rx: 3 }, parent),
      text: svgElement("text", { class: `${prefix}-text` }, parent),
    };
  }

  // Umístí štítek na ose do výšky positionY a vypíše cenu.
  function placeAxisTag(tag, positionY, value) {
    const { plotWidth } = state.layout;
    tag.rect.setAttribute("x", plotWidth + 2);
    tag.rect.setAttribute("y", positionY - 10);
    tag.text.setAttribute("x", plotWidth + 8);
    tag.text.setAttribute("y", positionY + 4);
    tag.text.textContent = priceFormat.format(value);
  }

  // Index svíčky pod kurzorem; null mimo plochu svíček (i nad cenovou osou)
  function hoveredIndex() {
    const { pointer, layout } = state;
    if (!pointer || !layout || pointer.x >= layout.plotWidth) return null;
    return Math.min(layout.visible.length - 1, Math.max(0, Math.floor(pointer.x / layout.spacing)));
  }

  // Úplné překreslení grafu podle aktuálních rozměrů kontejneru
  function render() {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (!width || !height) return;

    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    chartGroup.replaceChildren();

    const visible = state.candles.slice(-CANDLE_COUNT);
    const offset = state.candles.length - visible.length;
    const ema = computeEma(state.candles, EMA_PERIOD).slice(offset);
    const sma = computeSma(state.candles, SMA_PERIOD).slice(offset);

    // Rozsah cen a objemu přes viditelné svíčky (cena i s průměrem), s okrajem nahoře a dole
    let min = Infinity;
    let max = -Infinity;
    let maxVolume = 0;
    for (const [index, candle] of visible.entries()) {
      min = Math.min(min, candle.low, sma[index] ?? Infinity);
      max = Math.max(max, candle.high, sma[index] ?? -Infinity);
      maxVolume = Math.max(maxVolume, candle.volume);
    }
    const padding = (max - min) * 0.08;
    min -= padding;
    max += padding;

    // Čára alertu stojí na pevné ceně v horní části rozsahu; když ji cena
    // simulací opustí z viditelného rozsahu, přesune se zpět do grafu
    if (state.alertPrice === null || state.alertPrice < min || state.alertPrice > max) {
      state.alertPrice = min + (max - min) * 0.82;
    }

    const plotWidth = width - AXIS_WIDTH;
    const priceHeight = height * 0.78;
    const volumeTop = height * 0.8;
    const volumeHeight = height * 0.18;
    const spacing = plotWidth / visible.length;
    const bodyWidth = Math.max(1, spacing * 0.62);

    // Převod cena ↔ svislá souřadnice; layout si pamatuje i obrácený převod pro crosshair
    const y = (value) => ((max - value) / (max - min)) * priceHeight;
    const x = (index) => index * spacing + spacing / 2;
    const toPrice = (positionY) => max - (positionY / priceHeight) * (max - min);
    state.layout = { height, plotWidth, spacing, priceHeight, visible, toPrice };

    // Vodorovná mřížka a popisky cenové osy
    for (let i = 1; i <= 5; i++) {
      const level = max - ((max - min) * i) / 6;
      const lineY = y(level);
      svgElement("line", { class: "grid-line", x1: 0, x2: plotWidth, y1: lineY, y2: lineY }, chartGroup);
      const label = svgElement("text", { class: "axis-label", x: plotWidth + 8, y: lineY + 4 }, chartGroup);
      label.textContent = priceFormat.format(level);
    }

    // Objem ve spodní části a svíčky – knot a tělo (tělo aspoň 1 px, aby doji nezmizela)
    for (const [index, candle] of visible.entries()) {
      const dir = direction(candle);
      const centerX = x(index);
      const barHeight = (candle.volume / maxVolume) * volumeHeight;
      svgElement("rect", {
        class: `vol-${dir}`,
        x: centerX - bodyWidth / 2,
        y: volumeTop + volumeHeight - barHeight,
        width: bodyWidth,
        height: barHeight,
      }, chartGroup);
      svgElement("line", {
        class: `wick-${dir}`,
        x1: centerX, x2: centerX, y1: y(candle.high), y2: y(candle.low),
      }, chartGroup);
      const top = y(Math.max(candle.open, candle.close));
      svgElement("rect", {
        class: `body-${dir}`,
        x: centerX - bodyWidth / 2,
        y: top,
        width: bodyWidth,
        height: Math.max(1, y(Math.min(candle.open, candle.close)) - top),
      }, chartGroup);
    }

    // Klouzavé průměry jako lomené čáry (vynechá úsek bez hodnot)
    const drawAverage = (values, className) => {
      const points = values
        .map((value, index) => (value === null ? null : `${x(index).toFixed(1)},${y(value).toFixed(1)}`))
        .filter(Boolean);
      if (points.length > 1) svgElement("polyline", { class: `ma-line ${className}`, points: points.join(" ") }, chartGroup);
    };
    drawAverage(ema, "ma-line-fast");
    drawAverage(sma, "ma-line-slow");

    // Čára alertu se štítkem vlevo
    const alertY = y(state.alertPrice);
    svgElement("line", { class: "alert-line", x1: 0, x2: plotWidth, y1: alertY, y2: alertY }, chartGroup);
    svgElement("rect", { class: "alert-tag", x: 8, y: alertY - 9, width: 46, height: 18, rx: 3 }, chartGroup);
    svgElement("text", { class: "alert-text", x: 15, y: alertY + 4 }, chartGroup).textContent = T.alert;

    // Poslední cena – tečkovaná čára a štítek na ose v barvě poslední svíčky
    const last = visible[visible.length - 1];
    const lastY = y(last.close);
    const lastGroup = svgElement("g", { class: `last-${direction(last)}` }, chartGroup);
    svgElement("line", { class: "last-line", x1: 0, x2: plotWidth, y1: lastY, y2: lastY }, lastGroup);
    placeAxisTag(createAxisTag(lastGroup, "last"), lastY, last.close);

    updateCrosshair();
    updateLegend();
  }

  // Posun crosshairu na svíčku pod kurzorem (mimo svíčky se skryje)
  function updateCrosshair() {
    const index = hoveredIndex();
    if (index === null) {
      crosshair.setAttribute("visibility", "hidden");
      return;
    }
    const { height, plotWidth, spacing, priceHeight, toPrice } = state.layout;
    const centerX = index * spacing + spacing / 2;
    const pointerY = Math.min(Math.max(state.pointer.y, 0), priceHeight);

    crosshair.setAttribute("visibility", "visible");
    crossVertical.setAttribute("x1", centerX);
    crossVertical.setAttribute("x2", centerX);
    crossVertical.setAttribute("y2", height);
    crossHorizontal.setAttribute("x2", plotWidth);
    crossHorizontal.setAttribute("y1", pointerY);
    crossHorizontal.setAttribute("y2", pointerY);
    placeAxisTag(crossTag, pointerY, toPrice(pointerY));
  }

  // OHLC legenda – svíčka pod kurzorem, jinak poslední; barvu určuje třída kontejneru
  function updateLegend() {
    const visible = state.layout?.visible;
    if (!visible) return;
    const candle = visible[hoveredIndex() ?? visible.length - 1];
    const change = ((candle.close - candle.open) / candle.open) * 100;

    for (const field of ["open", "high", "low", "close"]) {
      legendFields[field].textContent = priceFormat.format(candle[field]);
    }
    // Procentní změna: znaménko vždy, mezera před % podle jazyka (česky „1.23 %“, anglicky „1.23%“)
    legendFields.change.textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)}${T.percentSuffix}`;
    legend.classList.toggle("is-up", direction(candle) === "up");
    legend.classList.toggle("is-down", direction(candle) === "down");
  }

  // Pohyb myši / prstu nad grafem; legenda se mění jen při přechodu na jinou svíčku
  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    const previousIndex = hoveredIndex();
    state.pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    updateCrosshair();
    if (hoveredIndex() !== previousIndex) updateLegend();
  });

  svg.addEventListener("pointerleave", () => {
    state.pointer = null;
    updateCrosshair();
    updateLegend();
  });

  // Simulace běží jen u grafu ve výřezu stránky (po odscrollování stojí)
  let inViewport = true;
  new IntersectionObserver(([entry]) => {
    inViewport = entry.isIntersecting;
  }).observe(container);

  // Simulace živých dat: poslední svíčka se průběžně mění, po několika
  // ticích vznikne nová. Při omezení animací nebo skryté záložce stojí.
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reducedMotion) {
    setInterval(() => {
      if (document.hidden || !inViewport) return;
      const last = state.candles[state.candles.length - 1];
      state.tick += 1;

      if (state.tick >= TICKS_PER_CANDLE) {
        // Nová svíčka začíná na poslední zavírací ceně; nejstarší se zahodí
        state.tick = 0;
        state.step += 1;
        const open = last.close;
        state.candles.push({ open, close: open, high: open, low: open, volume: 25 + random() * 60 });
        state.candles.shift();
      } else {
        // Pohyb ceny v rámci běžící svíčky
        last.close += (random() - 0.5) * 70 + Math.sin(state.step / 11) * 6;
        last.high = Math.max(last.high, last.close);
        last.low = Math.min(last.low, last.close);
        last.volume += random() * 6;
      }
      render();
    }, 800);
  }

  // Vykreslení při startu i při změně šířky (ResizeObserver volá render hned po observe)
  new ResizeObserver(render).observe(container);
}
