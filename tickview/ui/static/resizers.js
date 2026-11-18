/*
 * Posuvné hrany panelů (pravý panel, panel obchodů replaye).
 *
 * Prvek .pane-resizer na hraně panelu nese název (data-resize) a meze
 * velikosti (data-resize-min v px, data-resize-max jako podíl okna);
 * .pane-resizer-left = levá hrana mění šířku, .pane-resizer-top = horní
 * hrana mění výšku. Velikost se drží v CSS proměnné --<název>-size na
 * kořeni dokumentu (překreslení prvků NiceGUI ji nepřepíše) a pamatuje se
 * v prohlížeči (localStorage). Jádro tažení (window.TickViewResize.drag)
 * používají i hrany panelů indikátorů v lightweight_chart.js.
 */
(function () {
  // Klíč uložené velikosti v localStorage (za prefixem je název panelu)
  const STORAGE_PREFIX = 'tickview.paneSize.';

  // Nastaví velikost panelu přes CSS proměnnou (grafy se přizpůsobí samy – autoSize)
  function applySize(name, size) {
    document.documentElement.style.setProperty('--' + name + '-size', size + 'px');
  }

  // Uložené velikosti panelů při načtení stránky (bez localStorage = výchozí velikosti z CSS)
  try {
    for (let index = 0; index < window.localStorage.length; index++) {
      const key = window.localStorage.key(index);
      const size = Number(window.localStorage.getItem(key));
      if (key.startsWith(STORAGE_PREFIX) && size > 0) applySize(key.slice(STORAGE_PREFIX.length), size);
    }
  } catch (error) {
    // Soukromý režim / zakázané úložiště – platí výchozí velikosti
  }

  // Společné jádro tažení hrany (i pro hrany panelů indikátorů v lightweight_chart.js):
  // pointer capture, kurzor přes celou stránku, změny nejvýš jednou za snímek.
  // onMove(posun) dostane posun myši od začátku tažení v px (svisle nebo
  // vodorovně), onEnd() se zavolá po puštění tlačítka
  function drag(resizer, event, vertical, onMove, onEnd) {
    event.preventDefault();
    const start = vertical ? event.clientY : event.clientX;
    let delta = 0;
    let frame = 0;
    resizer.setPointerCapture(event.pointerId);
    document.body.classList.add(vertical ? 'pane-resizing-y' : 'pane-resizing-x');

    function move(moveEvent) {
      delta = (vertical ? moveEvent.clientY : moveEvent.clientX) - start;
      if (!frame) {
        frame = requestAnimationFrame(() => {
          frame = 0;
          onMove(delta);
        });
      }
    }

    function up() {
      resizer.removeEventListener('pointermove', move);
      resizer.removeEventListener('pointerup', up);
      resizer.removeEventListener('pointercancel', up);
      cancelAnimationFrame(frame);
      onMove(delta);
      document.body.classList.remove('pane-resizing-y', 'pane-resizing-x');
      onEnd();
    }

    resizer.addEventListener('pointermove', move);
    resizer.addEventListener('pointerup', up);
    resizer.addEventListener('pointercancel', up);
  }

  window.TickViewResize = { drag: drag };

  // Tažení hrany: velikost = počáteční velikost panelu ± posun myši, v mezích hrany
  document.addEventListener('pointerdown', (event) => {
    const resizer = event.target.closest && event.target.closest('.pane-resizer[data-resize]');
    if (!resizer || event.button !== 0) return;
    const name = resizer.dataset.resize;
    const vertical = resizer.classList.contains('pane-resizer-top');
    const min = Number(resizer.dataset.resizeMin) || 120;
    const maxShare = Number(resizer.dataset.resizeMax) || 0.75;
    const rect = resizer.parentElement.getBoundingClientRect();
    const startSize = vertical ? rect.height : rect.width;
    let size = startSize;
    drag(
      resizer,
      event,
      vertical,
      (delta) => {
        // Hrana je nahoře / vlevo: posun nahoru / doleva panel zvětšuje
        const windowSize = vertical ? window.innerHeight : window.innerWidth;
        size = Math.round(Math.max(min, Math.min(startSize - delta, windowSize * maxShare)));
        applySize(name, size);
      },
      () => {
        try {
          window.localStorage.setItem(STORAGE_PREFIX + name, String(size));
        } catch (error) {
          // Bez úložiště platí velikost jen do obnovení stránky
        }
      }
    );
  });
})();
