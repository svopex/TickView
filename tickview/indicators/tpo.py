"""Indikátor TPO (Time Price Opportunity, Market Profile) podle TradingView.

Období (N dní, týdnů nebo měsíců v časovém pásmu burzy) se rozdělí na časové
bloky (písmena A–Z, a–z, pak znovu od A) a ceny na řádky pevné výšky (ticky
na řádek, nebo Auto). Každý blok zapíše své písmeno do všech řádků, kterými
cena během bloku prošla. Z počtu bloků v řádcích vzniká POC a Value Area,
z tvaru profilu single prints a poor high / low; volitelně i profil objemu.
Prodloužené úrovně ukončených profilů dávají při protnutí cenou signál
(alert na signál indikátoru), stejně jako alerty TPO v TradingView.
"""

import math
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

from tickview.indicators.api import (
    LINE_DOTTED,
    LINE_SOLID,
    N_,
    Context,
    Indicator,
    Param,
    Profile,
    ProfileLevel,
    ProfileStyle,
    ProfileZone,
    Result,
    Signal,
    tr,
)
from tickview.logic.styles import color_channels, format_color

# Počet písmen bloků (A–Z, a–z; po vyčerpání se opakují) a velkých písmen
LETTER_COUNT = 52
UPPERCASE_COUNT = 26

# Velikosti bloku (volba → délka v sekundách)
BLOCK_SIZES = {"5m": 300, "10m": 600, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}
# Jednotky periody profilu, režim velikosti řádku, prodloužení úrovní a umístění profilu objemu
PERIOD_UNITS = (("day", N_("Den")), ("week", N_("Týden")), ("month", N_("Měsíc")))
ROW_MODES = (("auto", "Auto"), ("manual", N_("Ručně")))
EXTEND_MODES = (("none", N_("Neprodlužovat")), ("cross", N_("Prodloužit do protnutí")))
PLACEMENTS = (("left", N_("Vlevo")), ("right", N_("Vpravo")))

# Auto velikost řádku: rozpětí posledních AUTO_BARS svíček na AUTO_ROWS řádků
AUTO_BARS = 300
AUTO_ROWS = 80
# Nejvyšší počet řádků jednoho profilu (menší velikost řádku = chyba výpočtu)
MAX_ROWS = 2000
# Tolerance zaokrouhlení při převodu ceny na řádek (0.3 / 0.1 = 2.9999…)
EPSILON = 1e-9

# Skupiny nastavení (jako záložky Style a Volume profile v TradingView)
G_STYLE = N_("Vzhled")
G_LINES = N_("Čáry a popisky")
G_VOLUME = N_("Profil objemu")

# Výchozí barvy přechodu bloků (A → Z, a → z) a doplňkových prvků
GRADIENT_DEFAULTS = ("#d63864", "#5bc75e", "#4fb8d4", "#6223f5")
POOR_COLOR = "#9c6ade"
SINGLE_PRINT_COLOR = "rgba(224, 64, 251, 0.15)"


def _extend(name: str, label: str) -> Param:
    """Výběr prodloužení úrovně za konec období (do protnutí cenou)."""
    return Param.select(name, "none", label, options=EXTEND_MODES, group=G_LINES)


PARAMS = [
    # Vstupy (Inputs)
    Param.int("period", 1, N_("Perioda"), min=1, max=100),
    Param.select("period_unit", "day", N_("Jednotka periody"), options=PERIOD_UNITS),
    Param.select("block_size", "30m", N_("Velikost bloku"), options=tuple(BLOCK_SIZES),
                 tooltip=N_("Blok kratší než timeframe grafu se prodlouží na délku svíčky.")),
    Param.select("row_size", "auto", N_("Velikost řádku"), options=ROW_MODES),
    Param.int("ticks_per_row", 30, N_("Ticků na řádek"), min=1, max=1_000_000,
              tooltip=N_("Jen při ručně zadané velikosti řádku; Auto rozdělí rozpětí "
                         "posledních 300 svíček na 80 řádků.")),
    Param.int("va_percent", 70, N_("Value Area (%)"), min=1, max=100),
    # Vzhled bloků (Style → Appearance)
    Param.color("color1", GRADIENT_DEFAULTS[0], N_("Barva bloku A"), group=G_STYLE),
    Param.color("color2", GRADIENT_DEFAULTS[1], N_("Barva bloku Z"), group=G_STYLE),
    Param.color("color3", GRADIENT_DEFAULTS[2], N_("Barva bloku a"), group=G_STYLE),
    Param.color("color4", GRADIENT_DEFAULTS[3], N_("Barva bloku z"), group=G_STYLE),
    Param.bool("blocks", True, N_("Bloky"), group=G_STYLE),
    Param.bool("letters", True, N_("Písmena"), group=G_STYLE,
               tooltip=N_("Písmena se ukážou, jen když je v grafu dost místa (jinak bloky).")),
    Param.int("outside_opacity", 40, N_("Krytí mimo Value Area (%)"), min=0, max=100, group=G_STYLE),
    Param.bool("expand", False, N_("Rozložit bloky v čase"), group=G_STYLE,
               tooltip=N_("Blok se nakreslí v čase svého vzniku místo u levého okraje profilu.")),
    # Čáry a popisky (Style → Lines and labels)
    Param.bool("poc", True, "POC", group=G_LINES),
    _extend("poc_extend", N_("POC – prodloužení")),
    Param.bool("poor_high", False, "Poor high", group=G_LINES),
    _extend("poor_high_extend", N_("Poor high – prodloužení")),
    Param.color("poor_high_color", POOR_COLOR, N_("Poor high – barva"), group=G_LINES),
    Param.bool("poor_low", False, "Poor low", group=G_LINES),
    _extend("poor_low_extend", N_("Poor low – prodloužení")),
    Param.color("poor_low_color", POOR_COLOR, N_("Poor low – barva"), group=G_LINES),
    Param.bool("single_prints", False, "Single prints", group=G_LINES),
    _extend("single_prints_extend", N_("Single prints – prodloužení")),
    Param.color("single_prints_color", SINGLE_PRINT_COLOR, N_("Single prints – barva"), group=G_LINES),
    Param.bool("vah", True, "VAH", group=G_LINES),
    _extend("vah_extend", N_("VAH – prodloužení")),
    Param.bool("val", True, "VAL", group=G_LINES),
    _extend("val_extend", N_("VAL – prodloužení")),
    Param.bool("midpoint", False, N_("Střed TPO"), group=G_LINES),
    Param.bool("open", False, N_("Otevření"), group=G_LINES),
    Param.bool("close", False, N_("Uzavření"), group=G_LINES),
    Param.bool("ibr", False, N_("Počáteční rovnováha (IBR)"), group=G_LINES),
    Param.int("ibr_blocks", 2, N_("Počet bloků IBR"), min=1, max=52, group=G_LINES),
    # Profil objemu (Volume profile)
    Param.bool("volume", False, N_("Zobrazit profil objemu"), group=G_VOLUME),
    Param.bool("volume_values", False, N_("Hodnoty"), group=G_VOLUME),
    Param.color("volume_values_color", "#26a69a", N_("Barva hodnot"), group=G_VOLUME),
    Param.bool("volume_vah", False, "VAH", group=G_VOLUME),
    Param.bool("volume_val", False, "VAL", group=G_VOLUME),
    Param.color("volume_va_line_color", "#7b9cff", N_("Barva VAH a VAL"), group=G_VOLUME),
    Param.bool("volume_poc", True, "POC", group=G_VOLUME),
    Param.color("volume_poc_color", "#f23645", N_("Barva POC"), group=G_VOLUME),
    Param.color("volume_color", "rgba(41, 98, 255, 0.3)", N_("Objem"), group=G_VOLUME),
    Param.color("volume_va_color", "rgba(41, 98, 255, 0.7)", "Value Area", group=G_VOLUME),
    Param.select("volume_placement", "left", N_("Umístění"), options=PLACEMENTS, group=G_VOLUME),
]


# ---------- výpočetní pomocné funkce ----------


def auto_ticks_per_row(high: float, low: float, tick: float) -> int:
    """Ticky na řádek v režimu Auto (postup TradingView).

    Rozpětí v tickách se rozdělí na AUTO_ROWS řádků a zaokrouhlí na násobek
    kroku podle řádu: do 100 na 5, do 1 000 na 50, do 10 000 na 500…

    Parametry:
        high, low: nejvyšší a nejnižší cena posledních AUTO_BARS svíček.
        tick: nejmenší změna ceny instrumentu.
    """
    row_ticks = (high - low) / tick / AUTO_ROWS if tick > 0 else 0
    if not math.isfinite(row_ticks) or row_ticks <= 1:
        return 1
    increment = 5 * 10 ** max(0, math.ceil(math.log10(row_ticks)) - 2)
    return max(1, round(row_ticks / increment) * increment)


def value_area(values, percent: float) -> Optional[tuple[int, int, int]]:
    """POC a Value Area profilu (algoritmus TradingView); None = prázdný profil.

    POC je řádek s nejvyšší hodnotou (při shodě nejblíž středu profilu, pak
    vyšší). Value Area se rozšiřuje od POC vždy o jeden řádek – nad, nebo
    pod ní, podle vyšší hodnoty; při shodě o řádek blíž POC, při stejné
    vzdálenosti o vyšší – dokud nedosáhne percent % součtu.

    Parametry:
        values: hodnoty řádků zdola nahoru (počty bloků, objem).
        percent: podíl Value Area v procentech.

    Vrací (poc, nejnižší řádek VA, nejvyšší řádek VA).
    """
    values = np.asarray(values, dtype=float)
    total = values.sum()
    if not len(values) or total <= 0:
        return None
    middle = (len(values) - 1) / 2
    candidates = np.flatnonzero(values == values.max())
    poc = int(max(candidates, key=lambda row: (-abs(row - middle), row)))
    target = total * percent / 100
    # Smyčka nad floaty Pythonu (čtení prvků numpy po jednom je pomalé)
    values = values.tolist()
    low = high = poc
    accumulated = values[poc]
    last = len(values) - 1
    while accumulated < target and (low > 0 or high < last):
        # Chybějící řádek (okraj profilu) má hodnotu −1, takže vyhraje druhá strana
        above = values[high + 1] if high < last else -1.0
        below = values[low - 1] if low > 0 else -1.0
        if above > below or (above == below and high + 1 - poc <= poc - (low - 1)):
            high += 1
            accumulated += above
        else:
            low -= 1
            accumulated += below
    return poc, low, high


def _local_seconds(times: np.ndarray, timezone: str) -> np.ndarray:
    """Časy (s UTC) jako sekundy místního času burzy (pro hranice dní a bloků)."""
    index = pd.to_datetime(times, unit="s", utc=True)
    try:
        index = index.tz_convert(timezone)
    except Exception:
        # Neznámé časové pásmo = UTC
        pass
    # Převod přes datetime64[s] nezávisí na rozlišení času v pandas (ns / µs)
    return index.tz_localize(None).to_numpy().astype("datetime64[s]").astype(np.int64)


def _period_ordinals(local: np.ndarray, unit: str) -> np.ndarray:
    """Pořadové číslo dne, týdne (od pondělí) nebo měsíce každé svíčky v místním čase."""
    days = local // 86400
    if unit == "week":
        # 1. 1. 1970 byl čtvrtek – posun o 3 dny dá týdny začínající pondělím
        return (days + 3) // 7
    if unit == "month":
        return days.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64)
    return days


def _ordinal_start(ordinal: int, unit: str) -> int:
    """Začátek dne, týdne nebo měsíce s pořadovým číslem ordinal v místních sekundách."""
    if unit == "week":
        return (ordinal * 7 - 3) * 86400
    if unit == "month":
        return int(np.datetime64(ordinal, "M").astype("datetime64[s]").astype(np.int64))
    return ordinal * 86400


def _mix(first: tuple, second: tuple, ratio: float) -> str:
    """Barva v poměru ratio (0–1) mezi dvěma barvami (r, g, b, a) – přechod barev bloků."""
    return format_color(*(a + (b - a) * ratio for a, b in zip(first, second)))


@lru_cache(maxsize=256)
def block_colors(count: int, gradient: tuple[str, str, str, str]) -> tuple[str, ...]:
    """Barvy bloků profilu: A–Z přechodem prvních dvou barev, a–z posledních dvou.

    Přechod se roztáhne na skutečný počet bloků (profil A–M končí druhou
    barvou); po vyčerpání písmen se barvy opakují stejně jako písmena.
    Výsledek závisí jen na počtu bloků a barvách, proto je v cache (profily
    se přepočítávají při každé živé změně).
    """
    first, second, third, fourth = (color_channels(color) or (0, 0, 0, 1.0) for color in gradient)
    upper = min(count, UPPERCASE_COUNT)
    lower = min(max(count - UPPERCASE_COUNT, 0), UPPERCASE_COUNT)
    colors = []
    for rank in range(count):
        letter = rank % LETTER_COUNT
        if letter < UPPERCASE_COUNT:
            colors.append(_mix(first, second, letter / (upper - 1) if upper > 1 else 0))
        else:
            index = letter - UPPERCASE_COUNT
            colors.append(_mix(third, fourth, index / (lower - 1) if lower > 1 else 0))
    return tuple(colors)


def _rows_of(prices: np.ndarray, row_height: float) -> np.ndarray:
    """Cena → globální index řádku (řádky sousedních profilů lícují)."""
    return np.floor(prices / row_height + EPSILON).astype(np.int64)


def _range_sum(bottoms: np.ndarray, tops: np.ndarray, weights, count: int) -> np.ndarray:
    """Součet vah přes řádky: každá váha se přičte do řádků bottom–top (rozdílové pole).

    Parametry:
        bottoms, tops: dolní a horní řádek každého rozsahu (0 … count − 1).
        weights: váha každého rozsahu (do každého jeho řádku), nebo číslo pro všechny.
        count: počet řádků.
    """
    weights = np.broadcast_to(np.asarray(weights, dtype=float), bottoms.shape)
    diff = np.zeros(count + 1)
    np.add.at(diff, bottoms, weights)
    np.add.at(diff, tops + 1, -weights)
    return np.cumsum(diff[:-1])


def _runs(rows: np.ndarray) -> list[tuple[int, int]]:
    """Souvislé úseky řádků (vzestupně seřazené indexy) jako dvojice (první, poslední)."""
    runs: list[tuple[int, int]] = []
    for row in rows.tolist():
        if runs and runs[-1][1] == row - 1:
            runs[-1] = (runs[-1][0], row)
        else:
            runs.append((row, row))
    return runs


class _Candles:
    """Sloupce svíček jako pole numpy (výpočet profilů a hledání protnutí)."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.time = df["time"].to_numpy(dtype=np.int64)
        self.open = df["open"].to_numpy(dtype=float)
        self.high = df["high"].to_numpy(dtype=float)
        self.low = df["low"].to_numpy(dtype=float)
        self.close = df["close"].to_numpy(dtype=float)
        self.volume = df["volume"].to_numpy(dtype=float)

    def __len__(self) -> int:
        return len(self.time)

    def first_cross(self, low: float, high: float, start: int) -> Optional[int]:
        """Index první svíčky od start, která zasáhla ceny low–high (None = žádná)."""
        hits = np.flatnonzero((self.low[start:] <= high) & (self.high[start:] >= low))
        return int(hits[0]) + start if len(hits) else None


class TpoIndicator(Indicator):
    """TPO (Market Profile) – profil každého období vlevo, volitelně profil objemu vpravo."""

    id = "tpo"
    title = "TPO"
    name = "Time Price Opportunity"
    category = "volume"
    description = N_("Market Profile: čas strávený na cenových úrovních (bloky A–Z), POC a Value Area.")
    overlay = True
    params = PARAMS
    has_signals = True

    def legend_title(self, params: dict, with_inputs: bool = True) -> str:
        """Název do legendy jako v TradingView, např. „TPO 1 Den 30m Auto 70“."""
        if not with_inputs:
            return "TPO"
        unit = tr(dict(PERIOD_UNITS)[params["period_unit"]])
        rows = "Auto" if params["row_size"] == "auto" else f"{tr('Ručně')} {params['ticks_per_row']}"
        return f"TPO {params['period']} {unit} {params['block_size']} {rows} {params['va_percent']}"

    def compute(self, df: pd.DataFrame, params: dict, ctx: Context) -> Result:
        if df.empty:
            return Result()
        candles = _Candles(df)
        # Délka svíčky z kontextu, jinak z dat; blok nesmí být kratší než svíčka
        step = ctx.step_ms // 1000
        if not step:
            step = int(np.median(np.diff(candles.time))) if len(candles) > 1 else 60
        block_seconds = max(BLOCK_SIZES[params["block_size"]], step)
        tick = ctx.tick if ctx.tick > 0 else 0.01
        if params["row_size"] == "manual":
            ticks = params["ticks_per_row"]
        else:
            ticks = auto_ticks_per_row(candles.high[-AUTO_BARS:].max(), candles.low[-AUTO_BARS:].min(), tick)
        row_height = ticks * tick

        # Hranice profilů: změna pořadového čísla období (N dní / týdnů / měsíců)
        local = _local_seconds(candles.time, ctx.exchange_timezone or "UTC")
        groups = _period_ordinals(local, params["period_unit"]) // params["period"]
        bounds = np.flatnonzero(np.diff(groups)) + 1
        starts = [0, *bounds.tolist()]
        stops = [*bounds.tolist(), len(candles)]
        slots = local // block_seconds

        result = Result(profile_style=ProfileStyle(
            letters=params["letters"], blocks=params["blocks"], outside_opacity=params["outside_opacity"] / 100,
            expand=params["expand"], volume_color=params["volume_color"],
            volume_va_color=params["volume_va_color"], volume_placement=params["volume_placement"],
            volume_values=params["volume_values"], volume_values_color=params["volume_values_color"],
        ))
        for number, (first, stop) in enumerate(zip(starts, stops)):
            live = number == len(starts) - 1
            if live:
                previous_bars = stops[-2] - starts[-2] if len(starts) > 1 else None
                period_end = self._period_end(int(groups[-1]), int(local[-1]) - int(candles.time[-1]), params)
                end = self._live_end(candles, first, previous_bars, period_end, step)
            else:
                # Ukončený profil končí první svíčkou dalšího (v grafu sousední svíčka)
                end = int(candles.time[stop])
            profile, signals = self._profile(candles, first, stop, end, live, slots, block_seconds, row_height,
                                             params)
            result.profiles.append(profile)
            result.signals.extend(signals)
        result.signals.sort(key=lambda signal: signal.time)
        return result

    @staticmethod
    def _period_end(group: int, utc_offset: int, params: dict) -> int:
        """Teoretický konec období group (s UTC) – začátek dalšího období v místním čase.

        Parametry:
            group: pořadové číslo období (ordinal dne / týdne / měsíce // perioda).
            utc_offset: posun místního času burzy proti UTC v sekundách (u poslední svíčky).
            params: parametry indikátoru (perioda a její jednotka).
        """
        return _ordinal_start((group + 1) * params["period"], params["period_unit"]) - utc_offset

    @staticmethod
    def _live_end(candles: _Candles, first: int, previous_bars: Optional[int], period_end: int, step: int) -> int:
        """Konec rozpracovaného (posledního) profilu v čase.

        Profil zabere tolik svíček jako předchozí (akcie jen seanci, ne celý
        den), nejvýš do teoretického konce období; aspoň za poslední svíčku.

        Parametry:
            candles: všechny svíčky.
            first: index první svíčky rozpracovaného profilu.
            previous_bars: počet svíček předchozího profilu (None = první profil).
            period_end: teoretický konec období (s UTC).
            step: délka svíčky v sekundách.
        """
        last_time = int(candles.time[-1])
        end = period_end
        if previous_bars is not None:
            end = min(period_end, last_time + (previous_bars - (len(candles) - first) + 1) * step)
        return max(end, last_time + step)

    def _profile(self, candles: _Candles, first: int, stop: int, end: int, live: bool, slots: np.ndarray,
                 block_seconds: int, row_height: float, params: dict) -> tuple[Profile, list[Signal]]:
        """Profil jednoho období a signály protnutí jeho prodloužených úrovní.

        Parametry:
            candles: všechny svíčky.
            first, stop: rozsah indexů svíček období [first, stop).
            end: konec období v čase (s UTC).
            live: rozpracovaný profil (bez prodloužení a signálů).
            slots: číslo časového bloku každé svíčky (místní čas // délka bloku).
            block_seconds: délka bloku.
            row_height: výška řádku v ceně.
            params: parametry indikátoru.
        """
        high = candles.high[first:stop]
        low = candles.low[first:stop]
        # Bloky = souvislé úseky svíček se stejným číslem bloku (časy rostou)
        block_first = np.concatenate(([0], np.flatnonzero(np.diff(slots[first:stop])) + 1))
        block_high = np.maximum.reduceat(high, block_first)
        block_low = np.minimum.reduceat(low, block_first)
        bottom_rows = _rows_of(block_low, row_height)
        base = int(bottom_rows.min())
        bottom_rows -= base
        top_rows = _rows_of(block_high, row_height) - base
        count = int(top_rows.max()) + 1
        if count > MAX_ROWS:
            raise ValueError(tr("TPO: příliš mnoho řádků – zvětšete počet ticků na řádek."))
        # Počet bloků v každém řádku
        counts = _range_sum(bottom_rows, top_rows, 1, count)
        poc, va_low, va_high = value_area(counts, params["va_percent"])
        row_low = base * row_height

        def bottom_of(row: float) -> float:
            # Dolní hrana řádku v ceně (horní hrana = dolní hrana dalšího řádku)
            return row_low + row * row_height

        start = int(candles.time[first])
        signals: list[Signal] = []

        def extended_end(name: str, low_price: float, high_price: float, label: str, default: int = end
                         ) -> Optional[int]:
            # Konec úrovně / pásma: bez prodloužení (a u rozpracovaného profilu)
            # default, jinak svíčka protnutí (se signálem), nebo pravý okraj grafu (None)
            if live or params[f"{name}_extend"] != "cross":
                return default
            index = candles.first_cross(low_price, high_price, stop)
            if index is None:
                return None
            # Směr protnutí podle závěru předchozí svíčky vůči úrovni
            direction = "long" if candles.close[index - 1] < low_price else "short"
            signals.append(Signal(int(candles.time[index]), direction, len(candles) - 1 - index, f"TPO {label}"))
            return int(candles.time[index])

        profile_high, profile_low = float(high.max()), float(low.min())
        # Prodlužitelné úrovně (název, popisek, cena, barva, styl, konec bez prodloužení,
        # podmínka): VAH / VAL na okrajích Value Area, POC uprostřed řádku (neprodloužený
        # jen zvýrazněný řádek s popiskem – čára nulové délky), poor high / low = krajní
        # řádek s více než jedním blokem (bez jasného odmítnutí)
        extendable = (
            ("vah", "VAH", bottom_of(va_high + 1), None, LINE_DOTTED, end, True),
            ("val", "VAL", bottom_of(va_low), None, LINE_DOTTED, end, True),
            ("poc", "POC", bottom_of(poc + 0.5), None, LINE_SOLID, start, True),
            ("poor_high", "Poor high", profile_high, params["poor_high_color"], LINE_SOLID, end, counts[-1] > 1),
            ("poor_low", "Poor low", profile_low, params["poor_low_color"], LINE_SOLID, end, counts[0] > 1),
        )
        levels = [
            ProfileLevel(price, color, label, extended_end(name, price, price, label, default), style)
            for name, label, price, color, style, default, shown in extendable
            if params[name] and shown
        ]
        # Střed, otevření a uzavření profilu (jen popisek)
        for name, label, price in (("midpoint", N_("Střed TPO"), (profile_high + profile_low) / 2),
                                   ("open", N_("Otevření"), float(candles.open[first])),
                                   ("close", N_("Uzavření"), float(candles.close[stop - 1]))):
            if params[name]:
                levels.append(ProfileLevel(price, None, label, start, LINE_SOLID))

        # Single prints: nekrajní řádky s jediným blokem, souvislé úseky jako pásma
        zones: list[ProfileZone] = []
        if params["single_prints"] and count > 2:
            singles = np.flatnonzero(counts[1:-1] == 1) + 1
            for bottom, top in _runs(singles):
                zone_low, zone_high = bottom_of(bottom), bottom_of(top + 1)
                zones.append(ProfileZone(zone_low, zone_high, params["single_prints_color"],
                                         extended_end("single_prints", zone_low, zone_high, "single print")))

        # Počáteční rovnováha: rozpětí prvních N bloků
        initial_balance = None
        if params["ibr"]:
            blocks = min(params["ibr_blocks"], len(block_first))
            initial_balance = (float(block_low[:blocks].min()), float(block_high[:blocks].max()))

        gradient = tuple(params[f"color{index}"] for index in range(1, 5))
        profile = Profile(
            start=start, end=end, row_low=row_low, row_height=row_height,
            block_rows=list(zip(bottom_rows.tolist(), top_rows.tolist())),
            block_times=candles.time[first + block_first].tolist(), block_seconds=block_seconds,
            block_colors=list(block_colors(len(block_first), gradient)), value_area=(va_low, va_high),
            poc=poc if params["poc"] else None, levels=levels, zones=zones, initial_balance=initial_balance,
        )
        if params["volume"]:
            self._volume_profile(profile, candles, first, stop, base, count, params)
        return profile, signals

    @staticmethod
    def _volume_profile(profile: Profile, candles: _Candles, first: int, stop: int, base: int, count: int,
                        params: dict) -> None:
        """Doplní do profilu profil objemu: objem svíčky rovnoměrně do řádků jejího rozpětí.

        Bez objemu (forex MIDPOINT, indexy) se profil objemu nekreslí.
        """
        row_height = profile.row_height
        bottoms = _rows_of(candles.low[first:stop], row_height) - base
        tops = _rows_of(candles.high[first:stop], row_height) - base
        shares = np.maximum(candles.volume[first:stop], 0) / (tops - bottoms + 1)
        volumes = _range_sum(bottoms, tops, shares, count)
        area = value_area(volumes, params["va_percent"])
        if area is None:
            return
        poc, va_low, va_high = area
        profile.volumes = volumes.tolist()
        profile.volume_value_area = (va_low, va_high)
        # Čáry přes profil objemu: POC uprostřed řádku, VAH / VAL na okrajích Value Area
        for name, row, color in (
            ("volume_poc", poc + 0.5, params["volume_poc_color"]),
            ("volume_vah", va_high + 1, params["volume_va_line_color"]),
            ("volume_val", va_low, params["volume_va_line_color"]),
        ):
            if params[name]:
                profile.volume_levels.append(ProfileLevel(profile.row_low + row * row_height, color,
                                                          style=LINE_SOLID))


TPO_INDICATORS: list[Indicator] = [TpoIndicator()]
