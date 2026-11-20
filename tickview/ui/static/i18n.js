/* Překlady v JS: tvTr("český text") vrátí překlad podle jazyka stránky.
   Slovník (window.TV_I18N) a jazyk (window.TV_LANG) vkládá do hlavičky stránky
   Python (tickview.i18n.js_bootstrap) před ostatními skripty; česky je slovník
   prázdný a funkce vrací originál. Název tvTr (ne tr) kvůli proměnným řádků
   tabulek v lightweight_chart.js. */
(function () {
  'use strict';

  window.tvTr = function tvTr(text) {
    const words = window.TV_I18N || {};
    return Object.prototype.hasOwnProperty.call(words, text) ? words[text] : text;
  };
})();
