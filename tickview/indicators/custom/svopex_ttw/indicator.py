"""Svopex TTW – trendové linky, šablony a double bottom/top u svíčkových formací.

Převod výpočetního jádra Pine indikátoru „Svopex TTW“ (pine/svopex_ttw_v9.pine),
který vznikl z MQL5 indikátoru SvpTomTheWizard.mq5. Převod je 1:1 včetně
zvláštností z MQL (viz komentáře „zachováno z MQL“), aby výsledky odpovídaly
TradingView.

Indexy svíček jsou jako v Pine: 0 = poslední (rozpracovaná) svíčka, 1 =
předchozí atd. Svíčky mimo dostupnou historii mají hodnotu NaN – porovnání
s NaN je nepravdivé stejně jako porovnání s na v Pine.

Scanner, tabulka, alerty před zavřením svíčky a noční upozornění z Pine
skriptu sem nepatří – v TickView jsou to obecné funkce (záložka Scanner,
Časové připomínky), které používají signály tohoto indikátoru.
"""

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tickview.indicators.api import Context, Indicator, Param, Result, Segment, Signal

NAN = math.nan

# Pine má limit počtu čar; zachováno kvůli shodě s TradingView (při velkém
# počtu signálů se vykreslí stejné úsečky)
MAX_SEGMENTS = 490

# Délka formace -> název do signálu
FORMATION_LABELS = {1: "ONE", 2: "ENG", 3: "STAR"}

# Hranice timeframu, od které se půlnoční svíčky neřeší (H2 a výš)
MIDNIGHT_MAX_STEP_MS = 7_200_000

# Volby časového pásma pro hodinu půlnoční svíčky
TIMEZONE_OPTIONS = [
    ("Europe/Prague", "Praha (Europe/Prague)"),
    ("exchange", "Pásmo burzy symbolu"),
    ("UTC", "UTC"),
    ("Europe/London", "Londýn (Europe/London)"),
    ("America/New_York", "New York (America/New_York)"),
]

# Skupiny parametrů (sekce dialogu) – pořadí jako v Pine
G_SIGNAL = "Detekce signálu"
G_RANGE = "Rozsah zpracování"
G_SWING = "Swingy"
G_TOLERANCE = "Tolerance"
G_MIDNIGHT = "Půlnoční svíčky"
G_DISPLAY = "Zobrazení"


def pine_range(start: int, end: int) -> range:
    """Smyčka `for i = start to end` z Pine: včetně konce, při start > end sestupně."""
    return range(start, end + 1) if end >= start else range(start, end - 1, -1)


def _min(a: float, b: float) -> float:
    """math.min z Pine: NaN v kterémkoli argumentu dá NaN."""
    if a != a or b != b:
        return NAN
    return a if a < b else b


def _max(a: float, b: float) -> float:
    """math.max z Pine: NaN v kterémkoli argumentu dá NaN."""
    if a != a or b != b:
        return NAN
    return a if a > b else b


def _is_nan(value: float) -> bool:
    return value != value


class SvopexTTW(Indicator):
    """Svopex TTW: detekce formací ONE / ENG / STAR a k nim trendových linek, šablon a double bottom/top."""

    id = "svopex_ttw"
    title = "Svopex TTW"
    name = "Svopex TTW"
    description = "Trendové linky, šablony a double bottom/top u formací ONE / ENG / STAR (převod z Pine)."
    uses_color = False
    has_signals = True
    # V legendě jen název (parametrů je mnoho)
    title_params = ()

    params = [
        # Detekce svíčkové formace (ONE / ENG / STAR)
        Param.float("min_candle_length", 10, "Minimální délka svíčky (poměr k průměrné)", min=0, max=1000,
                    step=1, group=G_SIGNAL),
        Param.float("max_candle_length", 45, "Maximální délka svíčky (poměr k průměrné)", min=0, max=1000,
                    step=1, exclusive_min=True, group=G_SIGNAL),
        Param.bool("eng_close_beyond_previous", True, "ENG/STAR: uzavření nad high předchozí svíčky", group=G_SIGNAL),
        Param.float("star_ratio", 1, "STAR: poměr velikosti 1. a 3. svíčky ku 2.", min=0, max=100, group=G_SIGNAL),
        Param.bool("from_extreme", True, "Brát signály pouze zdola (long) / shora (short)", group=G_SIGNAL),
        Param.float("from_extreme_coef", 0.9, "Koeficient pro „brát zdola“", min=0, max=1, step=0.05,
                    group=G_SIGNAL),
        Param.int("from_extreme_bars", 180, "Počet svíček pro test „brát zdola“", min=1, max=2000, group=G_SIGNAL),
        Param.bool("show_shorts", True, "Zobrazovat i shorty", group=G_SIGNAL),
        # Rozsah zpracování a délky formací
        Param.int("bars_to_process", 360, "Počet svíček, které se zpracovávají", min=10, max=2000, group=G_RANGE,
                  tooltip="Zároveň perioda průměrné velikosti svíčky – graf musí mít aspoň tolik svíček."),
        Param.int("max_signal_length", 90, "Maximální délka signálu (svíčky)", min=1, max=1000, group=G_RANGE),
        Param.int("min_formation_length", 10, "Minimální délka formace (svíčky)", min=0, max=1000, group=G_RANGE),
        Param.int("min_gap", 3, "Minimální mezera mezi doteky", min=0, max=100, group=G_RANGE),
        Param.int("max_segments_per_signal", 2, "Maximální počet úseček pro jeden signál", min=1, max=50,
                  group=G_RANGE),
        # Swingy
        Param.int("swing_bars", 2, "Počet svíček pro minor swing", min=1, max=50, group=G_SWING),
        Param.float("swing_height_mult", 0.8, "Násobek průměrné výšky svíčky pro swing", min=0, max=10,
                    group=G_SWING),
        Param.bool("use_major_swing", True, "Použít major swing indikátor pro startovní svíčku", group=G_SWING),
        Param.int("major_swing_width", 9, "Šířka major swingu", min=1, max=100, group=G_SWING),
        Param.int("minor_swing_width", 4, "Šířka minor swingu", min=1, max=100, group=G_SWING),
        # Tolerance
        Param.float("trendline_miss", 0.07, "Maximální míra nedosednutí na trendlinku", min=0, max=10, step=0.01,
                    group=G_TOLERANCE),
        Param.float("segment_cross_tolerance", 0.15, "Povolené protnutí úsečky svíčkou", min=0, max=10, step=0.01,
                    group=G_TOLERANCE),
        Param.float("double_bottom_tolerance", 0.25, "Maximální odchylka u double bottom", min=0, max=10, step=0.01,
                    group=G_TOLERANCE),
        Param.int("min_double_bottom_length", 10, "Minimální délka formace double bottom", min=0, max=1000,
                  group=G_TOLERANCE),
        Param.bool("first_touches_on_wicks", True, "První dva doteky na knotech", group=G_TOLERANCE),
        Param.bool("last_touch_on_wicks", True, "Poslední dotek na knotech", group=G_TOLERANCE),
        # Půlnoční svíčky
        Param.bool("skip_midnight_candles", True, "Vynechat půlnoční svíčky", group=G_MIDNIGHT,
                   tooltip="Na timeframu od H2 výš se půlnoční svíčky neřeší."),
        Param.int("midnight_hour", 23, "Hodina půlnoční svíčky", min=0, max=23, group=G_MIDNIGHT),
        Param.select("midnight_timezone", "Europe/Prague", "Časové pásmo hodiny", options=TIMEZONE_OPTIONS,
                     group=G_MIDNIGHT,
                     tooltip="V Pine se hodina počítá v pásmu burzy symbolu. Zvolte pásmo, ve kterém má "
                             "půlnoční (rolloverová) svíčka nastavenou hodinu – jinak se vynechají jiné svíčky "
                             "než v TradingView."),
        # Zobrazení
        Param.bool("show_trendline", True, "Zobraz trendlinku", group=G_DISPLAY),
        Param.bool("show_template", True, "Zobraz šablonu", group=G_DISPLAY),
        Param.bool("show_double_top_bottom", True, "Zobraz double top / double bottom", group=G_DISPLAY),
        Param.bool("two_color_line", False, "Dvoubarevná linka signálu", group=G_DISPLAY),
        Param.int("line_width", 2, "Tloušťka čar", min=1, max=4, group=G_DISPLAY),
        Param.color("trendline_long_color", "#4caf50", "Barva trendline long", group=G_DISPLAY),
        Param.color("trendline_short_color", "#f23645", "Barva trendline short", group=G_DISPLAY),
        Param.color("double_bottom_color", "#00ced1", "Barva double bottom", group=G_DISPLAY),
        Param.color("double_top_color", "#00ced1", "Barva double top", group=G_DISPLAY),
        Param.color("two_color_middle_color", "#b8860b", "Barva prostřední části dvoubarevné linky",
                    group=G_DISPLAY),
    ]

    def required_bars(self, params: dict) -> int:
        # Průměrná velikost svíčky potřebuje bars_to_process svíček; nejstarší
        # testovaná formace sahá ještě o délku signálu a šířku swingu dál
        return params["bars_to_process"] + params["max_signal_length"] + params["major_swing_width"] + 5

    def compute(self, df, params: dict, ctx: Context) -> Result:
        """Vrátí úsečky (trendlinky, šablony, double bottom/top) a signály formací."""
        return _Computation(df, params, ctx).run()


class _Computation:
    """Jeden výpočet Svopex TTW nad svíčkami (stav a cache pomocných testů).

    Parametry:
        df: svíčky vzestupně (time, open, high, low, close).
        p: normalizované parametry.
        ctx: kontext výpočtu (timeframe, druh instrumentu, tick, pásmo burzy).
    """

    def __init__(self, df, p: dict, ctx: Context) -> None:
        self.p = p
        self.ctx = ctx
        n = len(df)
        self.n = n
        # Rezerva NaN za nejstarší svíčkou: smyčky smí sahat za historii (jako na v Pine)
        pad = p["bars_to_process"] + p["max_signal_length"] + p["from_extreme_bars"] + p["major_swing_width"] \
            + p["minor_swing_width"] + p["swing_bars"] + 10
        tail = [NAN] * pad

        # Obrácené řady: index 0 = poslední svíčka (Pine série open[idx], high[idx]…)
        def reverse(column: str) -> list[float]:
            return [float(value) for value in df[column].to_numpy()[::-1]] + tail

        self.open = reverse("open")
        self.high = reverse("high")
        self.low = reverse("low")
        self.close = reverse("close")
        self.time = [int(value) for value in df["time"].to_numpy()[::-1]]
        self.mid = [(h + l) / 2 for h, l in zip(self.high, self.low)]
        self.midnight = self._midnight_flags()
        self.acs = self._average_candle_size()
        # Forex na H1 má v MQL vlastní minimální / maximální velikost svíčky (v tickách)
        self.forex_h1 = ctx.timeframe == "1h" and ctx.kind == "forex"
        # Cache testů, které závisí jen na indexu (počítají se opakovaně ve smyčkách)
        self._swing_major: dict[tuple, bool] = {}
        self._swing_minor: dict[tuple, bool] = {}
        self._extreme_range: dict[int, tuple] = {}
        self._formation: dict[tuple, int] = {}

    # ---------- příprava ----------

    def _average_candle_size(self) -> float:
        """ta.sma(high - low, bars_to_process) na poslední svíčce; málo dat = NaN (jako v Pine)."""
        length = self.p["bars_to_process"]
        if self.n < length:
            return NAN
        return sum(self.high[i] - self.low[i] for i in range(length)) / length

    def _midnight_flags(self) -> list[bool]:
        """f_pulnoc pro každou svíčku: hodina půlnoční svíčky mimo pondělí (na TF pod H2)."""
        p = self.p
        flags = [False] * self.n
        if not p["skip_midnight_candles"] or (self.ctx.step_ms and self.ctx.step_ms >= MIDNIGHT_MAX_STEP_MS):
            return flags
        tz_name = self.ctx.exchange_timezone if p["midnight_timezone"] == "exchange" else p["midnight_timezone"]
        try:
            zone = ZoneInfo(tz_name)
        except Exception:
            zone = timezone.utc
        for idx, time_s in enumerate(self.time):
            local = datetime.fromtimestamp(time_s, tz=zone)
            # Pondělní půlnoc se bere jako validní svíčka
            flags[idx] = local.hour == p["midnight_hour"] and local.weekday() != 0
        return flags

    def is_midnight(self, idx: int) -> bool:
        return 0 <= idx < self.n and self.midnight[idx]

    # ---------- velikost svíčky a poloha v grafu ----------

    def min_len(self) -> float:
        """f_minLen: minimální velikost svíčky pro signál."""
        p = self.p
        if self.forex_h1:
            return self.ctx.tick * p["min_candle_length"] * 10
        return self.acs * 3 / p["max_candle_length"] * p["min_candle_length"]

    def max_len(self) -> float:
        """f_maxLen: maximální velikost svíčky pro signál."""
        if self.forex_h1:
            return self.ctx.tick * self.p["max_candle_length"] * 10
        return self.acs * 3

    def prumer(self, idx: int, long_short: int) -> float:
        """f_prumer: hranice dolní (long) / horní (short) části rozsahu středů svíček.

        Rozsah středů za from_extreme_bars svíček od idx; NaN (mimo historii)
        se ignoruje jako na v Pine.
        """
        if idx not in self._extreme_range:
            hi = lo = self.mid[idx]
            for value in self.mid[idx:idx + self.p["from_extreme_bars"]]:
                if value > hi:
                    hi = value
                if value < lo:
                    lo = value
            self._extreme_range[idx] = (hi, lo)
        hi, lo = self._extreme_range[idx]
        coef = self.p["from_extreme_coef"]
        return (hi - lo) * coef + lo if long_short == 0 else (hi - lo) * (1 - coef) + lo

    # ---------- swingy ----------

    @staticmethod
    def _extreme(series: list[float], idx: int, per: int, lowest: bool) -> bool:
        """Je bar idx extrémem v okně 2*per? (MQL iLowest/iHighest(..., per*2, i) == i + per).

        Při shodě rozhoduje novější bar (vlevo ostrá nerovnost, vpravo neostrá).
        """
        if idx < per + 1:
            return False
        value = series[idx]
        for j in pine_range(idx - per, idx - 1):
            if (series[j] <= value) if lowest else (series[j] >= value):
                return False
        for j in pine_range(idx + 1, idx + per - 1):
            if (series[j] < value) if lowest else (series[j] > value):
                return False
        return True

    def maj_swing_low(self, idx: int) -> float:
        """f_majSwingLow: cena major swingu (0 = swing neexistuje)."""
        return self.low[idx] if self._extreme(self.low, idx, self.p["major_swing_width"], True) else 0.0

    def maj_swing_high(self, idx: int) -> float:
        """f_majSwingHigh: cena major swingu (0 = swing neexistuje)."""
        return self.high[idx] if self._extreme(self.high, idx, self.p["major_swing_width"], False) else 0.0

    def is_swing_major(self, idx: int, long_short: int) -> bool:
        """f_isSwingMajor: výrazný swing pro startovní dotek úsečky.

        Okno minor swingu je podmnožinou okna major swingu, proto stačí test
        s užším oknem (optimalizace z Pine v3). Pro open se testuje maximum a
        pro close minimum – zachováno z MQL.
        """
        key = (idx, long_short)
        if key not in self._swing_major:
            per = min(self.p["major_swing_width"], self.p["minor_swing_width"])
            if self.p["first_touches_on_wicks"]:
                result = self._extreme(self.low if long_short == 0 else self.high, idx, per, long_short == 0)
            else:
                result = self._extreme(self.open, idx, per, False) or self._extreme(self.close, idx, per, True)
            self._swing_major[key] = result
        return self._swing_major[key]

    def is_swing_minor(self, idx: int, long_short: int) -> bool:
        """f_isSwingMinor: méně výrazný swing pro prostřední dotek úsečky."""
        key = (idx, long_short)
        if key in self._swing_minor:
            return self._swing_minor[key]
        p = self.p
        o, h, l, c = self.open, self.high, self.low, self.close
        margin = self.acs * p["swing_height_mult"]
        bars = pine_range(0, p["swing_bars"] - 1)
        good = False
        if long_short == 0:
            price = l[idx] if p["first_touches_on_wicks"] else _min(c[idx], o[idx])
            for i in bars:
                left = h[idx + i + 1]
                right = h[idx - (i + 1)] if idx - (i + 1) >= 0 else NAN
                # Swing je validní, pokud low svíčky + násobek průměrné velikosti je nižší než high okolní svíčky
                if price + margin < right or price + margin < left:
                    good = True
            # Okolní svíčky nesmí v žádném případě překročit extrém testované svíčky
            for i in bars:
                left = l[idx + i + 1]
                right = l[idx - (i + 1)] if idx - (i + 1) >= 0 else NAN
                if (not _is_nan(left) and left < l[idx]) or (not _is_nan(right) and right < l[idx]):
                    good = False
        else:
            price = h[idx] if p["first_touches_on_wicks"] else _max(o[idx], c[idx])
            for i in bars:
                left = l[idx + i + 1]
                right = l[idx - (i + 1)] if idx - (i + 1) >= 0 else NAN
                # Swing je validní, pokud high svíčky - násobek průměrné velikosti je vyšší než low okolní svíčky
                if price - margin > right or price - margin > left:
                    good = True
            for i in bars:
                left = h[idx + i + 1]
                right = h[idx - (i + 1)] if idx - (i + 1) >= 0 else NAN
                if (not _is_nan(left) and left > h[idx]) or (not _is_nan(right) and right > h[idx]):
                    good = False
        self._swing_minor[key] = good
        return good

    # ---------- formace ----------

    def formation_long(self, idx: int) -> int:
        """f_formaceLong: délka long formace na idx (0 = žádná, 1 = ONE, 2 = ENG, 3 = STAR)."""
        p = self.p
        o1, o2, o3 = self.open[idx], self.open[idx + 1], self.open[idx + 2]
        c1, c2, c3 = self.close[idx], self.close[idx + 1], self.close[idx + 2]
        l1, l2, l3 = self.low[idx], self.low[idx + 1], self.low[idx + 2]
        h1, h2 = self.high[idx], self.high[idx + 1]
        min_len, max_len = self.min_len(), self.max_len()
        result = 0

        # ONE – jedna silná svíčka s dlouhým spodním knotem
        r_one = abs(c1 - l1)
        if min_len <= r_one <= max_len:
            if c1 > o1 and (c1 - o1) * (1 / 0.6) > (h1 - l1) and (h1 - c1) * 2 < (o1 - l1):
                result = 1

        # Morning Star – tělo prostřední svíčky musí být výrazně menší než tělo první a třetí
        r_star = _max(_max(abs(c1 - l1), abs(c1 - l2)), abs(c1 - l3))
        if min_len <= r_star <= max_len:
            base = (
                c3 < o3 and c1 > o1
                and abs(c2 - o2) * p["star_ratio"] < abs(c1 - o1)
                and abs(c2 - o2) * p["star_ratio"] < abs(c3 - o3)
                and (c1 > h2 if p["eng_close_beyond_previous"] else c1 > _max(o2, c2))
                and c1 > o2 and o3 > c2 and o3 > o2 and c2 < o3 - ((o3 - c3) / 2) and c1 != h1
            )
            # Test „brát zdola“ až po cenových podmínkách – je drahý
            if base and (not p["from_extreme"] or self.prumer(idx, 0) > c1):
                result = 3

        # Buy engulfing
        r_eng = _max(abs(c1 - l1), abs(c1 - l2))
        if min_len <= r_eng <= max_len:
            base = (
                c2 < o2 and c1 > o1
                and (c1 > h2 if p["eng_close_beyond_previous"] else c1 > o2)
                and abs(c1 - o1) > abs(o2 - c2) and c1 != h1
            )
            if base and (not p["from_extreme"] or self.prumer(idx, 0) > c1):
                result = 2
        return result

    def formation_short(self, idx: int) -> int:
        """f_formaceShort: délka short formace na idx (0 = žádná formace)."""
        p = self.p
        o1, o2, o3 = self.open[idx], self.open[idx + 1], self.open[idx + 2]
        c1, c2, c3 = self.close[idx], self.close[idx + 1], self.close[idx + 2]
        l1, l2 = self.low[idx], self.low[idx + 1]
        h1, h2, h3 = self.high[idx], self.high[idx + 1], self.high[idx + 2]
        min_len, max_len = self.min_len(), self.max_len()
        result = 0

        # ONE – jedna silná svíčka s dlouhým horním knotem
        r_one = abs(c1 - h1)
        if min_len <= r_one <= max_len:
            if c1 < o1 and (o1 - c1) * (1 / 0.6) > (h1 - l1) and (c1 - l1) * 2 < (h1 - o1):
                result = 1

        # Evening Star
        r_star = _max(_max(abs(c1 - h1), abs(c1 - h2)), abs(c1 - h3))
        if min_len <= r_star <= max_len:
            base = (
                c3 > o3 and c1 < o1
                and abs(c2 - o2) * p["star_ratio"] < abs(c1 - o1)
                and abs(c2 - o2) * p["star_ratio"] < abs(c3 - o3)
                and (c1 < l2 if p["eng_close_beyond_previous"] else c1 < _min(o2, c2))
                and c1 < o2 and o3 < c2 and o3 < o2 and c2 > o3 + (c3 - o3) / 2 and c1 != l1
            )
            if base and (not p["from_extreme"] or self.prumer(idx, 1) < c1):
                result = 3

        # Sell engulfing
        r_eng = _max(abs(c1 - h1), abs(c1 - h2))
        if min_len <= r_eng <= max_len:
            base = (
                c2 > o2 and c1 < o1
                and (c1 < l2 if p["eng_close_beyond_previous"] else c1 < o2)
                and abs(o1 - c1) > abs(c2 - o2) and c1 != l1
            )
            if base and (not p["from_extreme"] or self.prumer(idx, 1) < c1):
                result = 2
        return result

    def formation(self, idx: int, long_short: int) -> int:
        """f_formace: délka formace podle směru (cache – trendlinky i double bottom ji testují znovu)."""
        key = (idx, long_short)
        if key not in self._formation:
            self._formation[key] = self.formation_long(idx) if long_short == 0 else self.formation_short(idx)
        return self._formation[key]

    # ---------- úsečky ----------

    def exist_space(self, start: int, middle: int, tested: int) -> bool:
        """f_existSpace: mezi doteky úsečky musí být aspoň minimální mezera."""
        gap = self.p["min_gap"]
        return start - middle > gap and middle - tested > gap

    def line_touches(self, start: int, middle: int, tested: int, length: int, long_short: int) -> tuple:
        """f_linkaProtina: dosedne trendlinka na testovanou svíčku (nejvýš do její poloviny)?

        Vrací (protíná, cena linky na testované svíčce, cena startovního
        doteku, cena prostředního doteku).
        """
        p = self.p
        o, h, l, c = self.open, self.high, self.low, self.close
        miss = p["trendline_miss"]
        wicks = p["first_touches_on_wicks"]
        o1, o2, c1, c2 = o[tested], o[tested + 1], c[tested], c[tested + 1]
        l1, l2, h1, h2 = l[tested], l[tested + 1], h[tested], h[tested + 1]

        if long_short == 0:
            price_start = l[start] if wicks else _min(o[start], c[start])
            price_middle = l[middle] if wicks else _min(o[middle], c[middle])
            # Kam směřuje linka – cena na testované svíčce a na svíčce o jednu starší
            slope = (price_middle - price_start) / float(middle - start)
            line1 = price_start + slope * float(tested - start)
            line2 = price_start + slope * float(tested + 1 - start)
            if p["last_touch_on_wicks"]:
                lo1 = l1 - (h1 - l1) * miss
                lo2 = l2 - (h1 - l1) * miss
            else:
                # Protnout můžeme až do poloviny knotu - miss; lo2 počítá knot
                # z testované svíčky (c1/o1), ne z předchozí – zachováno z MQL
                lo1 = _min(c1, o1) - (_min(c1, o1) - l1) / 2 - (h1 - l1) * miss
                lo2 = _min(c2, o2) - (_min(c1, o1) - l1) / 2 - (h1 - l1) * miss
            touches = (lo1 <= line1 <= l1 + (h1 - l1) / 2) or (
                length > 1 and lo2 <= line2 <= l2 + (h2 - l2) / 2
            )
        else:
            price_start = h[start] if wicks else _max(o[start], c[start])
            price_middle = h[middle] if wicks else _max(o[middle], c[middle])
            slope = (price_middle - price_start) / float(middle - start)
            line1 = price_start + slope * float(tested - start)
            line2 = price_start + slope * float(tested + 1 - start)
            if p["last_touch_on_wicks"]:
                hi1 = h1 + (h1 - l1) * miss
                hi2 = h2 + (h1 - l1) * miss
            else:
                hi1 = _max(c1, o1) + (h1 - _max(c1, o1)) / 2 + (h1 - l1) * miss
                hi2 = _max(c2, o2) + (h2 - _max(c2, o2)) / 2 + (h1 - l1) * miss
            touches = (hi1 >= line1 >= h1 - (h1 - l1) / 2) or (
                length > 1 and hi2 >= line2 >= h2 - (h2 - l2) / 2
            )
        return touches, line1, price_start, price_middle

    def segment_clear(self, start: int, tested: int, line_price: float, middle: int, length: int,
                      long_short: int) -> bool:
        """f_useckaNeniProtnuta: úsečku mezi startovním a testovaným dotekem neprotíná žádná svíčka.

        Úsečka smí svíčku protnout jen o toleranci (násobek průměrné velikosti
        svíčky); prostřední dotek a půlnoční svíčky se nepočítají.
        """
        p = self.p
        from_idx = start - 1
        to_idx = tested + length
        if from_idx < to_idx:
            return True
        wicks = p["first_touches_on_wicks"]
        tolerance = self.acs * p["segment_cross_tolerance"]
        o, h, l, c = self.open, self.high, self.low, self.close
        span = float(tested - start)
        if long_short == 0:
            price_start = l[start] if wicks else _min(o[start], c[start])
            for idx in pine_range(from_idx, to_idx):
                if idx == middle or self.is_midnight(idx):
                    continue
                price_low = l[idx] if wicks else _min(o[idx], c[idx])
                line_now = price_start + (line_price - price_start) / span * float(idx - start)
                if line_now > price_low + tolerance:
                    return False
        else:
            price_start = h[start] if wicks else _max(o[start], c[start])
            for idx in pine_range(from_idx, to_idx):
                if idx == middle or self.is_midnight(idx):
                    continue
                price_high = h[idx] if wicks else _max(o[idx], c[idx])
                line_now = price_start + (line_price - price_start) / span * float(idx - start)
                if line_now < price_high - tolerance:
                    return False
        return True

    @staticmethod
    def is_trendline(price_start: float, line_price: float, long_short: int) -> bool:
        """f_isTrendLine: úsečka směřuje proti směru signálu (klasická trendlinka)."""
        return (price_start <= line_price and long_short == 0) or (price_start >= line_price and long_short != 0)

    @staticmethod
    def is_template(price_start: float, line_price: float, long_short: int) -> bool:
        """f_isSablona: úsečka směřuje ve směru signálu (šablona)."""
        return (price_start >= line_price and long_short == 0) or (price_start <= line_price and long_short != 0)

    # ---------- double bottom / top ----------

    def double_bottom(self, tested: int, start: int, long_short: int) -> tuple:
        """f_testDoubleBottom: vrací (nalezeno, cena levelu).

        Druhá varianta (level na open/close startovní svíčky) se testuje jen,
        když startovní svíčka je major swing (podmínka p1 > 0) – zachováno z MQL.
        """
        tol = self.acs * self.p["double_bottom_tolerance"]
        low_t, high_t, open_t = self.low[tested], self.high[tested], self.open[tested]

        def near(level: float, extreme: float) -> bool:
            # Extrém nebo open testované svíčky v toleranci od levelu
            return (level - tol < extreme < level + tol) or (level - tol < open_t < level + tol)

        if long_short == 0:
            p1 = self.maj_swing_low(start)
            if p1 > 0 and (near(p1, low_t) or (low_t <= p1 and open_t >= p1)):
                return True, p1
            p2 = _min(self.open[start], self.close[start])
            if p1 > 0 and (near(p2, low_t) or (low_t <= p2 and open_t >= p2)):
                return True, p2
        else:
            p1 = self.maj_swing_high(start)
            if p1 > 0 and (near(p1, high_t) or (high_t >= p1 and open_t <= p1)):
                return True, p1
            p2 = _max(self.open[start], self.close[start])
            if p1 > 0 and (near(p2, high_t) or (high_t >= p2 and open_t <= p2)):
                return True, p2
        return False, 0.0

    def double_bottom_blocked(self, tested: int, start: int, length: int, long_short: int, level: float) -> bool:
        """f_dbJeSvickaVSignalu: v cestě double bottom signálu leží tělo některé svíčky."""
        from_idx = tested + length
        to_idx = start - 1
        if from_idx > to_idx:
            return False
        for idx in range(from_idx, to_idx + 1):
            if self.is_midnight(idx):
                continue
            if long_short == 0 and level > _min(self.close[idx], self.open[idx]):
                return True
            if long_short != 0 and level < _max(self.close[idx], self.open[idx]):
                return True
        return False

    # ---------- hlavní výpočet ----------

    def run(self) -> Result:
        """Hlavní výpočet (blok barstate.islast v Pine): úsečky a signály."""
        p = self.p
        result = Result()
        if _is_nan(self.acs):
            # Málo svíček pro průměrnou velikost – Pine v takovém případě nic nekreslí
            return result
        test_count = min(p["bars_to_process"], self.n)
        if self.ctx.signal_bars is not None:
            # Scanner potřebuje jen signály posledních svíček
            test_count = min(test_count, self.ctx.signal_bars)
        directions = (0, 1) if p["show_shorts"] else (0,)
        # Signály: (index svíčky, směr) -> délka formace; plní je obě části výpočtu
        hits: dict[tuple, int] = {}
        segments: list[Segment] = []
        width = p["line_width"]

        def add_segment(start: int, price_start: float, end: int, price_end: float, color: str) -> None:
            if len(segments) < MAX_SEGMENTS:
                segments.append(Segment(self.time[start], price_start, self.time[end], price_end, color, width))

        # --- Trendlinky a šablony (draw_line v MQL) ---
        for long_short in directions:
            color = p["trendline_long_color"] if long_short == 0 else p["trendline_short_color"]
            for tested in range(test_count):
                length = self.formation(tested, long_short)
                if length == 0:
                    continue
                counter = 0
                # Startovní dotek úsečky hledáme směrem do minulosti
                for start in pine_range(tested + length, tested + p["max_signal_length"]):
                    if counter >= p["max_segments_per_signal"]:
                        break
                    if start >= self.n:
                        break
                    start_ok = self.is_swing_major(start, long_short) if p["use_major_swing"] \
                        else self.is_swing_minor(start, long_short)
                    if not start_ok or start - 1 < tested + 1:
                        continue
                    # Prostřední dotek úsečky (od startovního směrem k testované svíčce)
                    for middle in pine_range(start - 1, tested + 1):
                        if counter >= p["max_segments_per_signal"]:
                            break
                        if self.is_midnight(start) or self.is_midnight(middle) or self.is_midnight(tested):
                            continue
                        if not self.is_swing_minor(middle, long_short):
                            continue
                        touches, line_price, price_start, price_middle = self.line_touches(
                            start, middle, tested, length, long_short
                        )
                        if not touches or not self.exist_space(start, middle, tested):
                            continue
                        if not self.segment_clear(start, tested, line_price, middle, length, long_short):
                            continue
                        if abs(start - tested) <= p["min_formation_length"]:
                            continue
                        # Filtr na typ úsečky – trendlinka nebo šablona
                        type_ok = (p["show_trendline"] and self.is_trendline(price_start, line_price, long_short)) \
                            or (p["show_template"] and self.is_template(price_start, line_price, long_short))
                        if not type_ok:
                            continue
                        counter += 1
                        hits[(tested, long_short)] = length
                        add_segment(start, price_start, tested, line_price, color)
                        if p["two_color_line"]:
                            add_segment(start, price_start, middle, price_middle, p["two_color_middle_color"])

        # --- Double bottom / double top levely (draw_line_db v MQL) ---
        if p["show_double_top_bottom"]:
            for long_short in directions:
                color = p["double_bottom_color"] if long_short == 0 else p["double_top_color"]
                for tested in range(test_count):
                    length = self.formation(tested, long_short)
                    if length == 0:
                        continue
                    for start in pine_range(tested + length, tested + p["max_signal_length"]):
                        if start >= self.n:
                            break
                        if self.is_midnight(start) or self.is_midnight(tested):
                            continue
                        found, level = self.double_bottom(tested, start, long_short)
                        if not found:
                            continue
                        if not self.double_bottom_blocked(tested, start, length, long_short, level) \
                                and abs(start - tested) > p["min_double_bottom_length"]:
                            hits[(tested, long_short)] = length
                            add_segment(start, level, tested, level, color)
                        # U double bottom bereme pouze první nalezený level (doubleBottomDone v MQL)
                        break

        result.segments = segments
        # Signály seřazené od nejnovější svíčky (scanner bere nejčerstvější signál směru)
        result.signals = [
            Signal(self.time[tested], "long" if long_short == 0 else "short", tested, FORMATION_LABELS.get(length, ""))
            for (tested, long_short), length in sorted(hits.items())
        ]
        return result


INDICATOR = SvopexTTW()
