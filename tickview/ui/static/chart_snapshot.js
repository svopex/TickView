/* Snímek grafu (nabídka fotoaparátu v horní liště, jako Chart snapshot v
   TradingView): Download image uloží PNG se všemi grafy rozložení, Copy image
   ho zkopíruje do schránky. Kliky na položky nabídky (třídy snapshot-download /
   snapshot-copy) a klávesové zkratky se obsluhují přímo v prohlížeči, ne přes
   Python: zápis do schránky musí proběhnout v rámci gesta uživatele (Safari
   a WKWebView nativního okna ho jinak odmítnou). Obrázek skládá
   window.TickViewChart.snapshot(). */
(function () {
  'use strict';

  // Krátká notifikace Quasaru (stejná jako ui.notify v Pythonu)
  function notify(message, type) {
    window.Quasar.Notify.create({ message: message, type: type });
  }

  // Chybová notifikace s textem výjimky
  function notifyError(error) {
    const detail = (error && error.message) || String(error);
    notify(tvTr('Snímek grafu se nepodařilo vytvořit: {error}').replace('{error}', detail), 'negative');
  }

  // PNG z canvasu jako Promise<Blob>
  function toBlob(canvas) {
    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('canvas.toBlob'))), 'image/png');
    });
  }

  // PNG snímku aktivního rozložení jako Promise<{blob, name}>; chyby skládání
  // (i chybějící graf) skončí zamítnutou Promise
  function snapshotPng() {
    return Promise.resolve().then(() => {
      const shot = window.TickViewChart && window.TickViewChart.snapshot();
      if (!shot) throw new Error('no chart');
      return toBlob(shot.canvas).then((blob) => ({ blob: blob, name: shot.name }));
    });
  }

  // Download image: uloží PNG přes dočasný odkaz <a download> (jako ui.download)
  function downloadImage() {
    snapshotPng()
      .then((png) => {
        const url = URL.createObjectURL(png.blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = png.name;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        // Uvolnění URL až po předání souboru prohlížeči
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      })
      .catch(notifyError);
  }

  // Copy image: zápis PNG do schránky; ClipboardItem dostane Promise hned
  // (synchronně v gestu uživatele), obrázek se dopočítá až potom. Schránka
  // vyžaduje zabezpečený kontext (https nebo localhost)
  function copyImage() {
    if (!navigator.clipboard || !navigator.clipboard.write || !window.ClipboardItem) {
      notify(tvTr('Schránka pro obrázky není dostupná (vyžaduje https nebo localhost)'), 'negative');
      return;
    }
    const blob = snapshotPng().then((png) => png.blob);
    navigator.clipboard
      .write([new ClipboardItem({ 'image/png': blob })])
      .then(() => notify(tvTr('Obrázek grafu zkopírován do schránky'), 'positive'))
      .catch(notifyError);
  }

  // Klik na položku nabídky snímku (fáze capture – ještě v gestu uživatele)
  document.addEventListener(
    'click',
    (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      if (target.closest('.snapshot-download')) downloadImage();
      else if (target.closest('.snapshot-copy')) copyImage();
    },
    true
  );

  // Zkratky jako v TradingView: ⌥⌘S / Ctrl+Alt+S stáhne obrázek, ⇧⌘S /
  // Ctrl+Shift+S ho zkopíruje; event.code, protože Alt na macOS mění znak klávesy
  document.addEventListener('keydown', (event) => {
    if (event.code !== 'KeyS' || event.repeat || !(event.metaKey || event.ctrlKey)) return;
    if (event.altKey && !event.shiftKey) {
      event.preventDefault();
      downloadImage();
    } else if (event.shiftKey && !event.altKey) {
      event.preventDefault();
      copyImage();
    }
  });
})();
