/*
 * Paleta barev vlaječek symbolů ve watchlistu (jako v TradingView).
 *
 * Řádek symbolu (.watch-row s data-wl-entry "symbol:zdroj:SYMBOL") má vlevo
 * vlaječku .wl-flag (barva v atributu data-flag, klik řeší Python). Po chvíli
 * nad vlaječkou se vedle ní ukáže paleta barev; klik na barvu pošle do Pythonu
 * událost watchlist_flag {key, color} – klik na barvu, kterou symbol už má
 * (zvýrazněná), vlaječku odebere. Barvy palety v pořadí FLAG_COLORS dává Python
 * v atributu data-flag-colors seznamu. Paleta je jediný prvek v <body> mimo Vue.
 */
(function () {
  const ROW_FLAG = '.watch-row[data-wl-entry] .wl-flag';
  const SHOW_DELAY = 350;
  const HIDE_DELAY = 250;
  // Paleta (vytvoří se při prvním použití), vlaječka, ke které je otevřená, a časovače
  let palette = null;
  let paletteFlag = null;
  let showTimer = null;
  let hideTimer = null;

  // Vytvoří paletu s tečkami barev (jednou, mimo prvky spravované Vue)
  function ensurePalette() {
    if (palette) return palette;
    palette = document.createElement('div');
    palette.className = 'wl-flag-palette';
    const colors = document.querySelector('[data-flag-colors]').dataset.flagColors.split(' ');
    colors.forEach((color) => {
      const dot = document.createElement('div');
      dot.className = 'wl-flag-dot';
      dot.dataset.flag = color;
      palette.appendChild(dot);
    });
    // Klik na barvu: nastavení / odebrání vlaječky symbolu, ke kterému je paleta otevřená
    palette.addEventListener('click', (event) => {
      const dot = event.target.closest('[data-flag]');
      if (!dot || !paletteFlag || !paletteFlag.isConnected) return;
      const key = paletteFlag.closest('[data-wl-entry]').dataset.wlEntry.slice('symbol:'.length);
      emitEvent('watchlist_flag', { key: key, color: dot.dataset.flag });
      hidePalette();
    });
    palette.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    palette.addEventListener('mouseleave', scheduleHide);
    document.body.appendChild(palette);
    return palette;
  }

  // Otevře paletu vpravo vedle vlaječky a zvýrazní současnou barvu symbolu
  function showPalette(flag) {
    const element = ensurePalette();
    paletteFlag = flag;
    element.querySelectorAll('[data-flag]').forEach((dot) => {
      dot.classList.toggle('selected', dot.dataset.flag === flag.dataset.flag);
    });
    element.classList.add('open');
    // Poloha až po zobrazení (výška palety je známá teprve teď); svisle na střed vlaječky
    const rect = flag.getBoundingClientRect();
    element.style.left = rect.right + 6 + 'px';
    element.style.top = rect.top + rect.height / 2 - element.offsetHeight / 2 + 'px';
  }

  function hidePalette() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    if (palette) palette.classList.remove('open');
    paletteFlag = null;
  }

  // Zavření s prodlevou – myš musí stihnout přejet z vlaječky na paletu
  function scheduleHide() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hidePalette, HIDE_DELAY);
  }

  // Najetí na vlaječku řádku: po prodlevě paleta (už otevřená jen zůstane)
  document.addEventListener('mouseover', (event) => {
    const flag = event.target.closest && event.target.closest(ROW_FLAG);
    if (!flag) return;
    clearTimeout(hideTimer);
    if (paletteFlag === flag) return;
    clearTimeout(showTimer);
    showTimer = setTimeout(() => showPalette(flag), SHOW_DELAY);
  });

  document.addEventListener('mouseout', (event) => {
    const flag = event.target.closest && event.target.closest(ROW_FLAG);
    if (flag && !flag.contains(event.relatedTarget)) scheduleHide();
  });

  // Klik na vlaječku (přepnutí v Pythonu) i tažení řádku (static/watchlist_dnd.js)
  // paletu zavře; klik ve fázi capture – Python ho na vlaječce zastaví (.stop)
  document.addEventListener(
    'click',
    (event) => {
      if (event.target.closest && event.target.closest(ROW_FLAG)) hidePalette();
    },
    true
  );
  document.addEventListener('dragstart', hidePalette);
})();
