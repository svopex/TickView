"""Testy COT reportu: trhy, čas zveřejnění, data a indikátory (spuštění: python -m unittest)."""

import asyncio
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tickview.indicators.compute import (
    candles_frame,
    compute_indicators,
    make_context,
    new_indicator,
    sample_lines,
    tail_payload,
)
from tickview.indicators.registry import get_indicator
from tickview.logic.cache import KlineCache
from tickview.logic.cot import (
    cot_index,
    market_for_symbol,
    needs_refresh,
    parse_rows,
    release_day,
    release_time_ms,
    resolve_market,
)
from tickview.providers.base import Candle
from tickview.services import datafeeds

# Oficiální harmonogram zveřejnění COT reportu CFTC na rok 2026 (* = posun o svátek)
CFTC_RELEASES_2026 = [
    "01-05", "01-09", "01-16", "01-23", "01-30", "02-06", "02-13", "02-20", "02-27",
    "03-06", "03-13", "03-20", "03-27", "04-03", "04-10", "04-17", "04-24",
    "05-01", "05-08", "05-15", "05-22", "05-29", "06-05", "06-12", "06-22", "06-26",
    "07-06", "07-10", "07-17", "07-24", "07-31", "08-07", "08-14", "08-21", "08-28",
    "09-04", "09-11", "09-18", "09-25", "10-02", "10-09", "10-16", "10-23", "10-30",
    "11-06", "11-16", "11-20", "11-30", "12-04", "12-11", "12-18", "12-28",
]

DAY_MS = 86_400_000


def api_row(report: str, comm=(100, 150), noncomm=(80, 50), nonrept=(40, 20), change=(5, -3), oi=1000) -> dict:
    """Řádek API Legacy reportu (hodnoty jako texty, stejně jako vrací Socrata)."""
    return {
        "report_date_as_yyyy_mm_dd": f"{report}T00:00:00.000",
        "contract_market_name": "NASDAQ MINI",
        "open_interest_all": str(oi),
        "comm_positions_long_all": str(comm[0]), "comm_positions_short_all": str(comm[1]),
        "noncomm_positions_long_all": str(noncomm[0]), "noncomm_positions_short_all": str(noncomm[1]),
        "nonrept_positions_long_all": str(nonrept[0]), "nonrept_positions_short_all": str(nonrept[1]),
        "change_in_comm_long_all": str(change[0]), "change_in_comm_short_all": str(change[1]),
        "change_in_noncomm_long_all": "0", "change_in_noncomm_short_all": "0",
        "change_in_nonrept_long_all": "0", "change_in_nonrept_short_all": "0",
    }


def daily_candles(start: date, count: int) -> list[Candle]:
    """Denní svíčky od půlnoci UTC zadaného dne."""
    first = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    return [Candle(first + index * DAY_MS, 100, 101, 99, 100, 1) for index in range(count)]


def candle_index(candles: list[Candle], day: date) -> int:
    """Pořadí denní svíčky daného dne."""
    target = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    return next(index for index, candle in enumerate(candles) if candle.open_time == target)


class ReleaseTimeTests(unittest.TestCase):
    def test_matches_cftc_schedule_2026(self):
        # Report je k úterku týdne před zveřejněním (3 dny před zveřejněním leží v týdnu reportu)
        for text in CFTC_RELEASES_2026:
            release = date.fromisoformat(f"2026-{text}")
            anchor = release - timedelta(days=3)
            report = anchor - timedelta(days=anchor.weekday()) + timedelta(days=1)
            self.assertEqual(release_day(report), release, f"report {report}")

    def test_release_time_is_1530_new_york(self):
        # Report z 3. 11. 2026 vyšel v pátek 6. 11.; v New Yorku už platí zimní čas (UTC−5)
        moment = datetime.fromtimestamp(release_time_ms(date(2026, 11, 3)) / 1000, tz=timezone.utc)
        self.assertEqual(moment, datetime(2026, 11, 6, 20, 30, tzinfo=timezone.utc))

    def test_needs_refresh(self):
        report = date(2026, 11, 10)
        fetched = release_time_ms(report) + 1000
        # Před dalším zveřejněním se nestahuje, po něm ano (s odkladem mezi pokusy)
        self.assertFalse(needs_refresh(report, fetched, fetched + DAY_MS))
        next_release = release_time_ms(report + timedelta(days=7))
        self.assertTrue(needs_refresh(report, fetched, next_release + 1))
        self.assertFalse(needs_refresh(report, next_release, next_release + 60_000))
        self.assertTrue(needs_refresh(None, 0, fetched))


class MarketTests(unittest.TestCase):
    def test_symbols(self):
        cases = {
            ("NQ1!", "futures"): ("209742", False),
            ("VIX1!@CFE", "futures"): ("1170E1", False),
            ("GC=F", "futures"): ("088691", False),
            ("EUR.USD", "forex"): ("099741", False),
            ("EURUSD=X", "forex"): ("099741", False),
            ("JPY=X", "forex"): ("097741", True),
            ("USD.JPY", "forex"): ("097741", True),
            ("XAUUSD", "commodity"): ("088691", False),
            ("IBUS500", "index"): ("13874A", False),
            ("BTCUSDT", "crypto"): ("133741", False),
        }
        for (symbol, kind), (code, invert) in cases.items():
            market = market_for_symbol(symbol, kind)
            self.assertIsNotNone(market, symbol)
            self.assertEqual((market.code, market.invert), (code, invert), symbol)

    def test_symbols_without_cot(self):
        # Eurex, křížové páry a akcie COT nemají
        for symbol, kind in (("FDAX1!", "futures"), ("EUR.JPY", "forex"), ("AAPL", "stock"), ("", "")):
            self.assertIsNone(market_for_symbol(symbol, kind), symbol)

    def test_manual_code(self):
        self.assertEqual(resolve_market("088691", "AAPL").code, "088691")
        self.assertIsNone(resolve_market("x'; drop", "NQ1!"))
        # Ruční kód stejného trhu si ponechá obrácení podle symbolu
        self.assertTrue(resolve_market("097741", "USD.JPY", "forex").invert)
        self.assertFalse(resolve_market("099741", "USD.JPY", "forex").invert)


class DataTests(unittest.TestCase):
    def test_parse_rows(self):
        frame = parse_rows([api_row("2026-11-10"), api_row("2026-11-03"), {"bad": 1}])
        self.assertEqual(list(frame["report"]), [date(2026, 11, 3), date(2026, 11, 10)])
        self.assertEqual(frame["commercials_net"].iloc[-1], -50)
        self.assertEqual(frame["commercials_change"].iloc[-1], 8)
        self.assertEqual(frame["noncommercials_net"].iloc[-1], 30)

    def test_cot_index(self):
        frame = parse_rows([api_row(f"2026-0{month}-01", comm=(value, 0)) for month, value in
                            ((1, 10), (2, 30), (3, 20), (4, 30))])
        index = cot_index(frame["commercials_net"], 3)
        self.assertTrue(math.isnan(index.iloc[1]))
        self.assertEqual(list(index.iloc[2:]), [50.0, 100.0])


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        # Dva reporty: úterý 3. a 10. 11. 2026 (zveřejněné v pátek 6. a v pondělí 16. 11. – Veterans Day)
        self.reports = parse_rows([api_row("2026-11-03", comm=(100, 150)),
                                   api_row("2026-11-10", comm=(155, 205), change=(4, -9))])
        self.candles = daily_candles(date(2026, 11, 1), 20)

    def compute(self, kind, params=None, symbol="NQ1!", data=True):
        spec = new_indicator(kind, params)
        ctx = make_context("ibkr", symbol, "1D", self.candles, kind="futures")
        if data:
            ctx = replace(ctx, data={"cot:209742": self.reports})
        return spec, compute_indicators([spec], self.candles, ctx)

    def test_release_alignment(self):
        _, payload = self.compute("cot_positions")
        net = payload["panes"][0]["series"][0]["data"]
        change = payload["panes"][0]["series"][1]["data"]
        # Report z 3. 11. platí od páteční svíčky 6. 11. (vyšel v 15:30 NY), ve čtvrtek ještě ne
        self.assertNotIn("value", net[candle_index(self.candles, date(2026, 11, 5))])
        self.assertEqual(net[candle_index(self.candles, date(2026, 11, 6))]["value"], -50)
        # Report z 10. 11. až od pondělí 16. 11.; týdenní změna jen na svíčce začátku platnosti
        self.assertEqual(net[candle_index(self.candles, date(2026, 11, 13))]["value"], -50)
        self.assertEqual(net[candle_index(self.candles, date(2026, 11, 16))]["value"], -50)
        self.assertEqual(change[candle_index(self.candles, date(2026, 11, 16))]["value"], 13)
        self.assertNotIn("value", change[candle_index(self.candles, date(2026, 11, 17))])
        table = payload["tables"][0]
        self.assertEqual(table["pane"], payload["panes"][0]["paneId"])
        self.assertIn(["Týdenní změna", "+13"], table["rows"])
        self.assertEqual(table["notesMode"], "always")
        self.assertEqual(table["position"], "top_left")

    def test_report_alignment(self):
        _, payload = self.compute("cot_positions", {"align": "report"})
        net = payload["panes"][0]["series"][0]["data"]
        # Od data reportu: úterní svíčka 3. 11. už report má, pondělní ne
        self.assertNotIn("value", net[candle_index(self.candles, date(2026, 11, 2))])
        self.assertEqual(net[candle_index(self.candles, date(2026, 11, 3))]["value"], -50)

    def test_invert_and_notes(self):
        _, payload = self.compute("cot_positions", {"invert": "on", "guide": "off"})
        net = payload["panes"][0]["series"][0]["data"]
        self.assertEqual(net[-1]["value"], 50)
        self.assertEqual(payload["tables"][0]["notes"], [])

    def test_without_data(self):
        # Bez dat čáry existují (prázdné) a tabulka hlásí načítání; bez trhu radu s kódem
        _, payload = self.compute("cot_index", data=False)
        self.assertEqual(len(payload["panes"][0]["series"]), 2)
        self.assertIn(["Data", "načítám COT report…"], payload["tables"][0]["rows"])
        _, payload = self.compute("cot_positions", symbol="AAPL", data=False)
        self.assertIn(["Trh", "symbol nemá COT"], payload["tables"][0]["rows"])
        self.assertEqual([line.name for line in sample_lines(get_indicator("cot_positions"),
                                                             get_indicator("cot_positions").defaults())],
                         ["Net", "Týdenní změna"])

    def test_table_hidden_by_style_and_position(self):
        spec = new_indicator("cot_positions")
        spec["style"] = {"tables": False}
        ctx = replace(make_context("ibkr", "NQ1!", "1D", self.candles), data={"cot:209742": self.reports})
        self.assertEqual(compute_indicators([spec], self.candles, ctx)["tables"], [])
        _, payload = self.compute("cot_positions", {"table_position": "off"})
        self.assertEqual(payload["tables"], [])

    def test_index_background_and_signals(self):
        # Čistá pozice Commercials stoupá a pak skočí na nové maximum → index 100 a signál LONG
        rows = [api_row((date(2025, 1, 7) + timedelta(weeks=week)).isoformat(), comm=(100 + week % 3, 100))
                for week in range(60)]
        rows.append(api_row((date(2025, 1, 7) + timedelta(weeks=60)).isoformat(), comm=(500, 100)))
        self.reports = parse_rows(rows)
        self.candles = daily_candles(date(2025, 1, 1), 450)
        # Bez zapnutého pozadí (výchozí stav) panel pozadí nemá
        _, payload = self.compute("cot_index", {"lookback": 26})
        self.assertNotIn("background", payload["panes"][0])
        spec, payload = self.compute("cot_index", {"lookback": 26, "background": True})
        pane = payload["panes"][0]
        self.assertEqual(len(pane["background"]), len(self.candles))
        self.assertTrue(any("color" in point for point in pane["background"]))
        result = get_indicator("cot_index").compute(
            candles_frame(self.candles),
            spec["params"],
            replace(make_context("ibkr", "NQ1!", "1D", self.candles), data={"cot:209742": self.reports}),
        )
        self.assertEqual(result.signals[-1].direction, "long")
        self.assertIn("Commercials", result.signals[-1].label)
        # Živý přepočet posílá jen konec pozadí, tabulky celé
        tail = tail_payload(payload)
        self.assertEqual(len(tail["panes"][0]["background"]), 3)
        self.assertEqual(tail["tables"], payload["tables"])

    def test_index_validation(self):
        with self.assertRaises(ValueError):
            new_indicator("cot_index", {"lower": 80, "upper": 20})
        with self.assertRaises(ValueError):
            new_indicator("cot_index", {"group2": "commercials"})


class ServiceTests(unittest.TestCase):
    def test_load_caches_and_backs_off(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as folder:
                cache = KlineCache(Path(folder) / "test.db")
                await cache.init()
                datafeeds.configure(cache)
                rows = [api_row("2026-01-06")]
                keys = ["cot:209742"]
                with mock.patch("tickview.services.cot.fetch_market_rows", return_value=rows) as fetch:
                    self.assertEqual(datafeeds.pending(keys), keys)
                    data = await datafeeds.load(keys)
                    self.assertEqual(data["cot:209742"]["commercials_net"].iloc[-1], -50)
                    # Druhé načtení z paměti, bez dotazu na API
                    await datafeeds.load(keys)
                    self.assertEqual(fetch.call_count, 1)
                # Nová služba (restart aplikace) načte data z databáze; data jsou
                # zastaralá, takže se zkusí API, které selže
                await cache.save_cot("209742", 0, rows)
                datafeeds.configure(cache)
                with mock.patch("tickview.services.cot.fetch_market_rows", side_effect=ValueError("down")):
                    data = await datafeeds.load(keys)
                self.assertIn("cot:209742", data)
                # Po chybě API se trh hned znovu nezkouší
                self.assertEqual(datafeeds.pending(keys), [])
                await cache.close()
            datafeeds._cot = None

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
