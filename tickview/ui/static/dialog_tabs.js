/* TickView – dialogy Settings se záložkami (Inputs / Style / Visibility).
 *
 * Při přepnutí záložky se nemění velikost okna (jako v TradingView): před
 * přepnutím se aktuální výška karty zamkne jako minimální, takže kratší
 * záložka okno nezmenší. Okno se zvětší jen tehdy, když se obsah záložky
 * nevejde. Karta dialogu se při zavření odstraní z DOM, takže každé otevření
 * začíná s výškou podle první záložky.
 */
(function () {
  'use strict';

  // Zachytávací fáze: výška se zamkne dřív, než Vue přepne panel záložky
  document.addEventListener('click', function (event) {
    const tab = event.target.closest('.tv-settings .tv-tabs .q-tab');
    if (!tab) {
      return;
    }
    // offsetHeight nikdy není menší než min-height, takže minimum jen roste
    const card = tab.closest('.tv-settings');
    card.style.minHeight = card.offsetHeight + 'px';
  }, true);
})();
