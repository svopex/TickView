"""Testy indikátoru TPO (spuštění: python -m unittest)."""

import unittest

from tickview.indicators.compute import compute_indicators, make_context, new_indicator, run_indicator, tail_payload
from tickview.indicators.registry import get_indicator
from tickview.indicators.tpo import auto_ticks_per_row, block_colors, value_area
from tickview.providers.base import Candle

# Půlnoc 14. 11. 2023 UTC v ms a délka 30m svíčky
DAY0 = 1_699_920_000_000
STEP = 30 * 60 * 1000
DAY = 24 * 60 * 60 * 1000


def _candle(open_time: int, high: float, low: float, close: float = None) -> Candle:
    # Svíčka s otevřením uprostřed rozpětí (závěr volitelný) a jednotkovým objemem
    middle = (high + low) / 2
    return Candle(open_time, middle, high, low, middle if close is None else close, 1.0)


def _two_days() -> list[Candle]:
    """Dva dny 30m svíček: první s profilem z testu, druhý protíná jeho úrovně.

    Řádky (tick 1, 1 tick na řádek) prvního dne: 10:A, 11:ABC, 12:AB,
    13:BD, 14:D, 15:DE – POC 11, Value Area 10–13 (VAH 14, VAL 10),
    poor high 15, single print 14–15.
    """
    day1 = [(12, 10), (13, 11), (11.5, 11), (15, 13), (15, 15)]
    candles = [_candle(DAY0 + index * STEP, high, low) for index, (high, low) in enumerate(day1)]
    candles[-1] = _candle(candles[-1].open_time, 15, 15, close=15)
    # Druhý den: první svíčka protne VAH a single print shora, druhá poor high zdola
    candles.append(_candle(DAY0 + DAY, 14.5, 13.5, close=14))
    candles.append(_candle(DAY0 + DAY + STEP, 16, 14.9))
    return candles


def _params(**changes) -> dict:
    # Výchozí parametry TPO s řádkem o jednom ticku a zadanými změnami
    return get_indicator("tpo").normalize_params({"row_size": "manual", "ticks_per_row": 1, **changes})


def _context(candles: list[Candle]):
    return make_context("test", "X", "30m", candles, tick=1.0)


class HelperTest(unittest.TestCase):
    """Pomocné výpočty: Auto velikost řádku, Value Area, barvy bloků."""

    def test_auto_ticks(self) -> None:
        # Rozpětí / tick / 80 zaokrouhlené na krok podle řádu (5, 50, 500…)
        self.assertEqual(auto_ticks_per_row(100 + 80 * 0.32, 100, 0.01), 30)
        self.assertEqual(auto_ticks_per_row(100 + 80 * 1.37, 100, 0.01), 150)
        self.assertEqual(auto_ticks_per_row(100.5, 100, 0.01), 1)

    def test_value_area(self) -> None:
        # Rozšiřování o vyšší ze sousedních řádků, při shodě o bližší POC
        self.assertEqual(value_area([1, 3, 2, 2, 1, 2], 70), (1, 0, 3))
        # Shodná hodnota i vzdálenost → vyšší řádek; prázdný profil = None
        self.assertEqual(value_area([1, 5, 1], 80), (1, 1, 2))
        self.assertIsNone(value_area([0, 0], 70))

    def test_block_colors(self) -> None:
        # Přechod A–Z končí druhou barvou i u kratšího profilu, a–z začíná třetí
        gradient = ("#000000", "#ffffff", "#ff0000", "#0000ff")
        colors = block_colors(30, gradient)
        self.assertEqual((colors[0], colors[25], colors[26], colors[29]), gradient)
        self.assertEqual(block_colors(3, gradient)[-1], "#ffffff")


class TpoTest(unittest.TestCase):
    """Profily období, úrovně, prodloužení a signály."""

    def test_profile_rows_and_levels(self) -> None:
        candles = _two_days()
        result = run_indicator(get_indicator("tpo"), _params(poor_high=True, single_prints=True),
                               candles, _context(candles))
        self.assertEqual(len(result.profiles), 2)
        profile = result.profiles[0]
        # Bloky A–E s rozsahy řádků od nejnižšího řádku (cena 10)
        self.assertEqual(len(profile.block_colors), 5)
        self.assertEqual(profile.block_rows, [(0, 2), (1, 3), (1, 1), (3, 5), (5, 5)])
        self.assertEqual((profile.row_low, profile.poc, profile.value_area), (10, 1, (0, 3)))
        levels = {level.label: level for level in profile.levels}
        self.assertEqual((levels["VAH"].price, levels["VAL"].price, levels["POC"].price), (14, 10, 11.5))
        self.assertEqual(levels["Poor high"].price, 15)
        self.assertEqual([(zone.low, zone.high) for zone in profile.zones], [(14, 15)])
        # Ukončený profil končí první svíčkou dalšího dne; bez prodloužení úrovně do konce období
        self.assertEqual(profile.end, (DAY0 + DAY) // 1000)
        self.assertEqual(levels["VAH"].end, profile.end)
        self.assertEqual(result.signals, [])

    def test_extended_levels_signal_on_cross(self) -> None:
        candles = _two_days()
        params = _params(poor_high=True, single_prints=True, vah_extend="cross", poor_high_extend="cross",
                         single_prints_extend="cross", val_extend="cross")
        result = run_indicator(get_indicator("tpo"), params, candles, _context(candles))
        signals = {(signal.label, signal.direction, signal.bars_ago) for signal in result.signals}
        self.assertEqual(signals, {("TPO VAH", "short", 1), ("TPO single print", "short", 1),
                                   ("TPO Poor high", "long", 0)})
        # Protnutá úroveň končí svíčkou protnutí, neprotnutá (VAL) u pravého okraje grafu
        levels = {level.label: level for level in result.profiles[0].levels}
        self.assertEqual(levels["VAH"].end, (DAY0 + DAY) // 1000)
        self.assertIsNone(levels["VAL"].end)

    def test_week_period_merges_days(self) -> None:
        # Týdenní profil spojí oba dny (úterý a středa jednoho týdne)
        candles = _two_days()
        result = run_indicator(get_indicator("tpo"), _params(period_unit="week"), candles, _context(candles))
        self.assertEqual(len(result.profiles), 1)
        self.assertEqual(len(result.profiles[0].block_rows), 7)

    def test_too_small_rows_fail(self) -> None:
        # Příliš mnoho řádků v profilu = chyba výpočtu (jako v TradingView)
        candles = [_candle(DAY0, 10_000, 1)]
        with self.assertRaises(ValueError):
            run_indicator(get_indicator("tpo"), _params(), candles, _context(candles))

    def test_payload_and_tail(self) -> None:
        # Payload pro JS graf: profily v hlavním grafu s přeloženými popisky
        # a vzhledem ve skupině instance; živý přepočet posílá profily celé
        candles = _two_days() + [_candle(DAY0 + 2 * DAY, 15, 14), _candle(DAY0 + 3 * DAY, 15, 14)]
        spec = new_indicator("tpo", {"row_size": "manual", "ticks_per_row": 1, "volume": True})
        payload = compute_indicators([spec], candles, _context(candles))
        group = payload["groups"][0]
        self.assertEqual(group["title"], "TPO 1 Den 30m Ručně 1 70")
        self.assertEqual(group["profileStyle"]["outside_opacity"], 0.4)
        self.assertEqual(len(payload["profiles"]), 4)
        first = payload["profiles"][0]
        self.assertEqual(first["blockRows"][0], [0, 2])
        self.assertEqual({level["label"] for level in first["levels"]}, {"VAH", "VAL", "POC"})
        self.assertEqual(len(first["volumes"]), 6)
        self.assertEqual(tail_payload(payload)["profiles"], payload["profiles"])


if __name__ == "__main__":
    unittest.main()
