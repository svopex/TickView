/*
 * Posuvné hrany panelů (pravý panel, panel obchodů replaye).
 *
 * Prvek .pane-resizer na hraně panelu nese název (data-resize) a meze
 * velikosti (data-resize-min v px, data-resize-max jako podíl okna);
 * .pane-resizer-left = levá hrana mění šířku, .pane-resizer-top = horní
 * hrana mění výšku. Velikost se drží v CSS proměnné --<název>-size na
 * kořeni dokumentu (překreslení prvků NiceGUI ji nepřepíše) a pamatuje se
 * v prohlížeči (localStorage).
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

  // Tažení hrany: velikost = počáteční velikost panelu ± posun myši, v mezích
  // hrany; CSS proměnná se zapíše nejvýš jednou za snímek
  document.addEventListener('pointerdown', (event) => {
    const resizer = event.target.closest && event.target.closest('.pane-resizer[data-resize]');
    if (!resizer || event.button !== 0) return;
    event.preventDefault();
    const name = resizer.dataset.resize;
    const vertical = resizer.classList.contains('pane-resizer-top');
    const min = Number(resizer.dataset.resizeMin) || 120;
    const maxShare = Number(resizer.dataset.resizeMax) || 0.75;
    const rect = resizer.parentElement.getBoundingClientRect();
    const start = vertical ? event.clientY : event.clientX;
    const startSize = vertical ? rect.height : rect.width;
    let size = startSize;
    let frame = 0;
    resizer.setPointerCapture(event.pointerId);
    document.body.classList.add(vertical ? 'pane-resizing-y' : 'pane-resizing-x');

    function onMove(moveEvent) {
      // Hrana je nahoře / vlevo: posun nahoru / doleva panel zvětšuje
      const delta = (vertical ? moveEvent.clientY : moveEvent.clientX) - start;
      const windowSize = vertical ? window.innerHeight : window.innerWidth;
      size = Math.round(Math.max(min, Math.min(startSize - delta, windowSize * maxShare)));
      if (!frame) {
        frame = requestAnimationFrame(() => {
          frame = 0;
          applySize(name, size);
        });
      }
    }

    function onUp() {
      resizer.removeEventListener('pointermove', onMove);
      resizer.removeEventListener('pointerup', onUp);
      resizer.removeEventListener('pointercancel', onUp);
      cancelAnimationFrame(frame);
      applySize(name, size);
      document.body.classList.remove('pane-resizing-y', 'pane-resizing-x');
      try {
        window.localStorage.setItem(STORAGE_PREFIX + name, String(size));
      } catch (error) {
        // Bez úložiště platí velikost jen do obnovení stránky
      }
    }

    resizer.addEventListener('pointermove', onMove);
    resizer.addEventListener('pointerup', onUp);
    resizer.addEventListener('pointercancel', onUp);
  });
})();
