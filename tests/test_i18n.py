"""Testy překladů UI (spuštění: python -m unittest)."""

import sys
import unittest
from pathlib import Path

from tickview import i18n

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import i18n_check  # noqa: E402


class I18nTest(unittest.TestCase):
    """Přepínání jazyka, překlad textů a úplnost slovníků."""

    def tearDown(self) -> None:
        # Testy nesmí ovlivnit ostatní (výchozí jazyk je čeština)
        i18n.set_language(i18n.DEFAULT_LANGUAGE)

    def test_language_switch(self) -> None:
        # Neznámý kód jazyka = výchozí čeština; česky se texty nemění
        self.assertEqual(i18n.set_language("de"), "cs")
        self.assertEqual(i18n.tr("Zavřít"), "Zavřít")
        self.assertEqual(i18n.set_language("en"), "en")
        self.assertEqual(i18n.tr("Zavřít"), "Close")
        # Text bez překladu zůstane v originále
        self.assertEqual(i18n.tr("Neexistující text"), "Neexistující text")

    def test_plural_translates_word(self) -> None:
        # plural vybírá český tvar podle počtu a anglicky ho přeloží
        self.assertEqual(i18n.plural(1, "graf", "grafy", "grafů"), "1 graf")
        self.assertEqual(i18n.plural(5, "graf", "grafy", "grafů"), "5 grafů")
        i18n.set_language("en")
        self.assertEqual(i18n.plural(1, "graf", "grafy", "grafů"), "1 chart")
        self.assertEqual(i18n.plural(3, "graf", "grafy", "grafů"), "3 charts")

    def test_tr_options_and_context(self) -> None:
        # Volby výběru: hodnoty zůstanou, popisky se přeloží; kontext odliší stejný text
        i18n.set_language("en")
        self.assertEqual(i18n.tr_options({"solid": "Zavřít"}), {"solid": "Close"})
        self.assertEqual(i18n.trc("weekday", "Ne"), "Sun")
        self.assertEqual(i18n.tr("Ne"), "No")

    def test_js_bootstrap(self) -> None:
        # Česky se slovník do stránky neposílá, anglicky jen slovník JS (bez ukončení </script>)
        self.assertIn("window.TV_I18N={}", i18n.js_bootstrap())
        i18n.set_language("en")
        script = i18n.js_bootstrap()
        self.assertIn('"Skrýt": "Hide"', script)
        self.assertNotIn('"Zavřít"', script)
        self.assertEqual(script.count("</script>"), 1)

    def test_translations_complete(self) -> None:
        # Žádný český text mimo tr()/N_()/tvTr() a všechny klíče mají překlad
        problems = i18n_check.run()
        self.assertEqual(problems, [], "\n".join(problems[:50]))


if __name__ == "__main__":
    unittest.main()
