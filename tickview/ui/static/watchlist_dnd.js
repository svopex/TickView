/*
 * Přesun symbolů a sekcí watchlistu tažením myši (HTML5 drag & drop).
 *
 * Řádky symbolů i záhlaví sekcí nesou atribut data-wl-entry
 * ("symbol:zdroj:SYMBOL" nebo "section:<id>") a draggable="true". Při tažení
 * se cílový řádek označí čarou nad (wl-drop-before) nebo pod sebou
 * (wl-drop-after) podle poloviny řádku, prázdné místo pod seznamem
 * (wl-drop-end) znamená konec seznamu. Po puštění jde do Pythonu globální
 * událost watchlist_drop {dragged, target, after}; target null = konec seznamu.
 */
(function () {
  const BODY = '.watchlist-body';
  const ENTRY = '[data-wl-entry]';
  const MARKS = ['wl-drop-before', 'wl-drop-after', 'wl-drop-end'];
  // Tažená položka (hodnota data-wl-entry) a řádek, který právě nese značku cíle
  let dragged = null;
  let marked = null;

  // Odstraní značku cíle (čáru nad / pod řádkem nebo konec seznamu)
  function clearMark() {
    if (marked) marked.classList.remove(...MARKS);
    marked = null;
  }

  // Cíl puštění pod kurzorem: řádek seznamu (ne tažený), nebo prázdné místo
  // pod seznamem (= konec seznamu)
  function dropTarget(event) {
    const body = event.target.closest && event.target.closest(BODY);
    if (!body || !dragged) return null;
    const entry = event.target.closest(ENTRY);
    if (entry && body.contains(entry)) {
      if (entry.dataset.wlEntry === dragged) return null;
      const rect = entry.getBoundingClientRect();
      return { element: entry, target: entry.dataset.wlEntry, after: event.clientY > rect.top + rect.height / 2 };
    }
    return { element: body, target: null, after: true };
  }

  document.addEventListener('dragstart', (event) => {
    const entry = event.target.closest && event.target.closest(ENTRY);
    if (!entry || !entry.closest(BODY)) return;
    dragged = entry.dataset.wlEntry;
    event.dataTransfer.effectAllowed = 'move';
    // Firefox bez dat tažení nespustí
    event.dataTransfer.setData('text/plain', dragged);
    entry.classList.add('wl-dragging');
  });

  // Nad platným cílem povolí puštění a vykreslí značku cíle
  document.addEventListener('dragover', (event) => {
    const drop = dropTarget(event);
    if (!drop) {
      clearMark();
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const mark = drop.target === null ? 'wl-drop-end' : drop.after ? 'wl-drop-after' : 'wl-drop-before';
    // dragover chodí neustále – třídy měníme jen při změně cíle nebo poloviny řádku
    if (marked === drop.element && marked.classList.contains(mark)) return;
    clearMark();
    drop.element.classList.add(mark);
    marked = drop.element;
  });

  document.addEventListener('drop', (event) => {
    const drop = dropTarget(event);
    clearMark();
    if (!drop) return;
    event.preventDefault();
    emitEvent('watchlist_drop', { dragged: dragged, target: drop.target, after: drop.after });
  });

  // Konec tažení (i zrušení klávesou Esc): úklid značek a stavu
  document.addEventListener('dragend', () => {
    clearMark();
    document.querySelectorAll('.wl-dragging').forEach((element) => element.classList.remove('wl-dragging'));
    dragged = null;
  });
})();
