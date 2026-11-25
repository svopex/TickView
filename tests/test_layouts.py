"""Testy rozložení více grafů, viditelnosti kreseb, stylu textu a šablon indikátorů."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests import STATIC_CSS
from tickview.logic.cache import KlineCache
from tickview.logic.indicator_templates import (
    delete_indicator_template,
    describe_indicator_template,
    favorite_indicator_templates,
    find_indicator_template,
    load_indicator_templates,
    recent_indicator_templates,
    save_indicator_template,
    template_indicators,
    update_indicator_template,
)
from tickview.indicators.compute import new_indicator
from tickview.logic.layouts import LAYOUT_SECTIONS, LAYOUTS, load_layout, normalize_layout, resize_panes
from tickview.logic.styles import (
    TIMEFRAMES,
    drawing_visible,
    normalize_drawing_style,
    normalize_text_options,
    normalize_visibility,
    timeframe_unit,
)


class LayoutTest(unittest.TestCase):
    """Konfigurace rozložení: kontrola, doplnění grafů, změna počtu grafů."""

    def test_layout_ids_have_css(self) -> None:
        # Každé rozložení má v style.css svou třídu mřížky (jinak by se grafy nerozložily)
        css = STATIC_CSS.read_text()
        for layout_id in LAYOUTS:
            self.assertIn(f".layout-{layout_id} ", css.replace(",", " ").replace("{", " {"), layout_id)
        # Počty v sekcích nabídky odpovídají rozložením
        for count, items in LAYOUT_SECTIONS:
            for layout_id, _ in items:
                self.assertEqual(LAYOUTS[layout_id], count)
        self.assertEqual(max(LAYOUTS.values()), 16)

    def test_normalize_layout_from_legacy(self) -> None:
        # Bez uložené konfigurace: jeden graf ze starších nastavení
        legacy = {"symbol": "yahoo:AAPL", "timeframe": "5m", "chart_type": "line", "indicators": [new_indicator("rsi")]}
        layout = load_layout(None, legacy)
        self.assertEqual(layout["layout"], "1")
        self.assertEqual(len(layout["panes"]), 1)
        self.assertEqual(layout["panes"][0]["symbol"], "yahoo:AAPL")
        self.assertEqual(layout["panes"][0]["timeframe"], "5m")
        self.assertEqual(layout["panes"][0]["chart_type"], "line")
        self.assertEqual(layout["panes"][0]["indicators"][0]["kind"], "rsi")
        self.assertEqual(layout["sync"], {"symbol": False, "interval": False, "crosshair": False, "time": False, "date_range": False})

    def test_normalize_layout_fills_and_trims(self) -> None:
        raw = {
            "layout": "4",
            "sync": {"symbol": 1, "crosshair": True, "bogus": True},
            "active": 7,
            "panes": [
                {"symbol": "binance:ETHUSDT", "timeframe": "15m", "chart_type": "nope"},
                {"symbol": "bad", "timeframe": "3m"},
            ],
        }
        layout = normalize_layout(raw)
        self.assertEqual(len(layout["panes"]), 4)
        # Neplatný symbol / timeframe převezme první graf, chybějící grafy jsou jeho kopie
        self.assertEqual([pane["symbol"] for pane in layout["panes"]], ["binance:ETHUSDT"] * 4)
        self.assertEqual(layout["panes"][1]["timeframe"], "15m")
        self.assertEqual(layout["panes"][0]["chart_type"], "candles")
        self.assertTrue(layout["sync"]["symbol"])
        self.assertTrue(layout["sync"]["crosshair"])
        self.assertNotIn("bogus", layout["sync"])
        # Aktivní graf mimo rozsah = první
        self.assertEqual(layout["active"], 0)
        # Neznámé rozložení = jeden graf; poškozený JSON nevadí
        self.assertEqual(normalize_layout({"layout": "99"})["layout"], "1")
        self.assertEqual(load_layout("{x", None)["layout"], "1")
        self.assertEqual(normalize_layout({"layout": "2v", "active": True})["active"], 0)

    def test_resize_panes(self) -> None:
        panes = [
            {"symbol": "binance:BTCUSDT", "timeframe": "1h", "chart_type": "candles", "indicators": [new_indicator("sma")]},
            {"symbol": "yahoo:MSFT", "timeframe": "1D", "chart_type": "line", "indicators": []},
        ]
        # Zvětšení: nové grafy převezmou symbol, timeframe a typ aktivního grafu, bez indikátorů
        grown = resize_panes(panes, "4", active=1)
        self.assertEqual(len(grown), 4)
        self.assertEqual(grown[0]["symbol"], "binance:BTCUSDT")
        self.assertEqual(grown[2], {"symbol": "yahoo:MSFT", "timeframe": "1D", "chart_type": "line", "indicators": []})
        # Zmenšení: zůstanou první grafy
        shrunk = resize_panes(grown, "1", active=3)
        self.assertEqual(len(shrunk), 1)
        self.assertEqual(shrunk[0]["indicators"][0]["kind"], "sma")


class VisibilityTest(unittest.TestCase):
    """Viditelnost kreseb podle timeframu (záložka Visibility)."""

    def test_every_timeframe_has_unit(self) -> None:
        for timeframe in TIMEFRAMES:
            self.assertIsNotNone(timeframe_unit(timeframe), timeframe)
        self.assertEqual(timeframe_unit("15m"), ("minutes", 15))
        self.assertEqual(timeframe_unit("4h"), ("hours", 4))
        self.assertEqual(timeframe_unit("3M"), ("months", 3))
        self.assertIsNone(timeframe_unit("3X"))

    def test_default_visible_everywhere(self) -> None:
        for timeframe in TIMEFRAMES:
            self.assertTrue(drawing_visible({}, timeframe))
            self.assertTrue(drawing_visible({"visibility": {}}, timeframe))
            self.assertTrue(drawing_visible(None, timeframe))

    def test_unit_disabled_and_ranges(self) -> None:
        options = {"visibility": {"hours": {"enabled": False}, "minutes": {"from": 5, "to": 15}}}
        self.assertFalse(drawing_visible(options, "1h"))
        self.assertFalse(drawing_visible(options, "4h"))
        self.assertFalse(drawing_visible(options, "1m"))
        self.assertFalse(drawing_visible(options, "2m"))
        self.assertTrue(drawing_visible(options, "5m"))
        self.assertTrue(drawing_visible(options, "15m"))
        self.assertTrue(drawing_visible(options, "1D"))
        self.assertTrue(drawing_visible(options, "1W"))
        # Jednotka bez pravidla a neznámý timeframe = viditelná
        self.assertTrue(drawing_visible(options, "3M"))
        self.assertTrue(drawing_visible(options, "3X"))

    def test_normalize_visibility(self) -> None:
        # Meze se omezí na rozsah jednotky a prohozené se srovnají
        values = normalize_visibility({"minutes": {"from": 99, "to": "3"}, "days": {"from": "x", "to": -5}})
        self.assertEqual(values["minutes"], {"enabled": True, "from": 3, "to": 59})
        self.assertEqual(values["days"], {"enabled": True, "from": 1, "to": 1})
        self.assertEqual(values["weeks"], {"enabled": True, "from": 1, "to": 52})
        # Styl kresby nese normalizovanou viditelnost
        style = normalize_drawing_style({"options": {"visibility": {"hours": {"enabled": False}}}}, "trend")
        self.assertFalse(style["options"]["visibility"]["hours"]["enabled"])


class TextStyleTest(unittest.TestCase):
    """Vzhled textové kresby (dialog Text jako v TradingView)."""

    def test_text_options(self) -> None:
        options = normalize_text_options(
            {"bold": 1, "italic": False, "background": True, "backgroundColor": "nope", "borderColor": "#ABCDEF", "wrap": "yes"}
        )
        self.assertTrue(options["bold"])
        self.assertFalse(options["italic"])
        self.assertTrue(options["background"])
        # Neplatná barva = výchozí, platná se sjednotí
        self.assertEqual(options["backgroundColor"], "rgba(41, 98, 255, 0.25)")
        self.assertEqual(options["borderColor"], "#abcdef")
        self.assertFalse(options["border"])
        self.assertTrue(options["wrap"])

    def test_text_style_only_for_text(self) -> None:
        text = normalize_drawing_style({"options": {"bold": True, "fontSize": 20}}, "text")
        self.assertTrue(text["options"]["bold"])
        self.assertEqual(text["options"]["fontSize"], 20)
        # Kresba bez textu i popisku (fib) vzhled písma nemá
        self.assertNotIn("bold", normalize_drawing_style({"options": {"bold": True}}, "fib")["options"])

    def test_label_style_for_lines(self) -> None:
        # Popisek čáry: barva textu výchozí podle čáry, neplatné zarovnání = výchozí
        line = normalize_drawing_style({"color": "#ff0000", "options": {"textVAlign": "x", "textHAlign": "right"}}, "trend")
        self.assertEqual(line["options"]["textColor"], "#ff0000")
        self.assertEqual(line["options"]["textVAlign"], "top")
        self.assertEqual(line["options"]["textHAlign"], "right")
        self.assertNotIn("textColor", normalize_drawing_style({}, "fib")["options"])

    def test_rect_extend_only_for_rect(self) -> None:
        # Prodloužení obdélníku se převede na bool a jiné kresby ho nemají
        rect = normalize_drawing_style({"options": {"extendRight": 1}}, "rect")
        self.assertTrue(rect["options"]["extendRight"])
        self.assertFalse(rect["options"]["extendLeft"])
        self.assertNotIn("extendRight", normalize_drawing_style({"options": {"extendRight": True}}, "trend")["options"])


class IndicatorTemplatesTest(unittest.TestCase):
    """Šablony indikátorů: uložení, přepsání, oblíbené, naposledy použité."""

    def test_save_load_and_describe(self) -> None:
        indicators = [new_indicator("ema", {"length": 50}), new_indicator("rsi")]
        templates = save_indicator_template([], "  Moje   sada ", indicators, symbol="binance:BTCUSDT", timeframe="1h", now_ms=10)
        self.assertEqual(templates[0]["name"], "Moje sada")
        self.assertEqual(describe_indicator_template(templates[0]), "EMA 50, RSI 14 · BTCUSDT · 1h")
        # Uložení a načtení přes JSON zachová obsah
        loaded = load_indicator_templates(json.dumps(templates))
        self.assertEqual(loaded, templates)
        # Neplatné položky se vynechají
        self.assertEqual(load_indicator_templates('[{"name": ""}, 5, {"name": "A", "symbol": "x", "timeframe": "9m"}]')[0]["symbol"], None)
        self.assertEqual(load_indicator_templates("{"), [])
        with self.assertRaises(ValueError):
            save_indicator_template([], "  ", indicators)

    def test_overwrite_favorite_recent_delete(self) -> None:
        templates = save_indicator_template([], "Alfa", [new_indicator("sma")], now_ms=1)
        templates = save_indicator_template(templates, "beta", [], now_ms=2)
        templates = update_indicator_template(templates, "ALFA", favorite=True)
        # Přepsání stejného názvu (bez ohledu na velikost písmen) zachová hvězdičku
        templates = save_indicator_template(templates, "alfa", [new_indicator("rsi")], now_ms=3)
        self.assertEqual(len(templates), 2)
        alfa = find_indicator_template(templates, "Alfa")
        self.assertTrue(alfa["favorite"])
        self.assertEqual(alfa["indicators"][0]["kind"], "rsi")
        self.assertEqual([t["name"] for t in favorite_indicator_templates(templates)], ["alfa"])
        self.assertEqual([t["name"] for t in recent_indicator_templates(templates)], ["alfa", "beta"])
        self.assertEqual(describe_indicator_template(find_indicator_template(templates, "beta")), "Bez indikátorů")
        # Použití šablony dá indikátorům nová id
        copies = template_indicators(alfa)
        self.assertNotEqual(copies[0]["id"], alfa["indicators"][0]["id"])
        self.assertEqual(copies[0]["params"], alfa["indicators"][0]["params"])
        self.assertEqual([t["name"] for t in delete_indicator_template(templates, "ALFA")], ["beta"])


class DrawingsPerSymbolTest(unittest.TestCase):
    """Kresby patří k symbolu (všechny timeframy), ne k jednomu timeframu."""

    def test_drawings_across_timeframes(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as folder:
                cache = KlineCache(Path(folder) / "t.db")
                await cache.init()
                first = await cache.add_drawing("binance", "BTCUSDT", "1h", "hline", [{"time": 1, "price": 2.0}])
                await cache.add_drawing("binance", "BTCUSDT", "5m", "text", [{"time": 3, "price": 4.0}], "#ffffff", "Ahoj")
                await cache.add_drawing("binance", "ETHUSDT", "1h", "hline", [{"time": 1, "price": 9.0}])
                drawings = await cache.load_drawings("binance", "BTCUSDT")
                self.assertEqual([d["kind"] for d in drawings], ["hline", "text"])
                # Úprava a smazání fungují bez ohledu na timeframe zobrazení
                await cache.update_drawing("binance", "BTCUSDT", first, [{"time": 5, "price": 6.0}])
                await cache.update_drawing_style("binance", "BTCUSDT", first, "#ff0000", None, {"visibility": {"hours": {"enabled": False}}})
                drawings = await cache.load_drawings("binance", "BTCUSDT")
                self.assertEqual(drawings[0]["points"], [{"time": 5, "price": 6.0}])
                self.assertFalse(drawing_visible(drawings[0]["options"], "1h"))
                # Kresbu jiného symbolu id s cizím symbolem nesmaže
                await cache.delete_drawing("binance", "ETHUSDT", first)
                self.assertEqual(len(await cache.load_drawings("binance", "BTCUSDT")), 2)
                await cache.delete_drawing("binance", "BTCUSDT", first)
                self.assertEqual(len(await cache.load_drawings("binance", "BTCUSDT")), 1)
                await cache.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
