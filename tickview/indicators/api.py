"""Rozhraní indikátorů TickView (jediný modul, který indikátor smí importovat).

Indikátor je třída odvozená od Indicator: popis pro nabídku (název,
kategorie, popis), schéma parametrů (seznam Param) a metoda compute, která
nad svíčkami jednoho symbolu a timeframu vrátí Result – čáry, úrovně panelu,
úsečky, značky, signály a profily TPO. Indikátor nezná UI, cache ani zdroje dat; vše, co
potřebuje vědět o instrumentu, dostane v Context.

Vestavěné indikátory jsou v tickview/indicators/builtin.py, vlastní mají
každý svůj adresář v tickview/indicators/custom/<id>/ se souborem
indicator.py, který definuje INDICATOR (instanci třídy indikátoru).
"""

import math
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

import pandas as pd

# Překlady textů pro uživatele (re-export: indikátor smí importovat jen API);
# N_ označí text v deklaraci (popisky, názvy), tr přeloží text sestavený při výpočtu
from tickview.i18n import N_, tr  # noqa: F401
from tickview.logic.styles import normalize_color
# Kanonické timeframy zdrojů dat (volby parametru typu timeframe) a rozklad
# symbolu "zdroj:SYMBOL" (re-export pro indikátory, které smí importovat jen API)
from tickview.providers.base import CANONICAL_TIMEFRAMES as TIMEFRAMES
from tickview.providers.base import split_symbol  # noqa: F401

# Verze rozhraní; indikátor s jinou verzí loader odmítne
API_VERSION = 1

# Kategorie nabídky indikátorů (pořadí = pořadí v levém sloupci dialogu)
# (popisky se překládají až při zobrazení)
CATEGORIES = {
    "ma": N_("Klouzavé průměry"),
    "bands": N_("Pásma a kanály"),
    "trend": N_("Trend"),
    "osc": N_("Oscilátory"),
    "volatility": N_("Volatilita"),
    "volume": N_("Objem"),
    "cot": "COT report",
    "custom": N_("Vlastní"),
}

# Barvy čar instancí (cyklicky podle pořadí přidání, pokud uživatel nezvolí vlastní)
PALETTE = ["#2962ff", "#ff9800", "#9c27b0", "#00bcd4", "#f7b500", "#e91e63"]
UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"


def with_alpha(color: str, alpha: float) -> str:
    """Barva #rrggbb s průhledností jako rgba(…) (sloupce objemu, pozadí COT)."""
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {alpha})"

# Barva signálních / doplňkových čar (MACD signál, %D, …)
SIGNAL_COLOR = "#ff6d00"

# Styl čáry úsečky / série (odpovídá LineStyle v Lightweight Charts)
LINE_SOLID = 0
LINE_DOTTED = 1
LINE_DASHED = 2


@dataclass(frozen=True)
class Param:
    """Jeden parametr indikátoru (pole v dialogu nastavení).

    Atributy:
        name: klíč parametru v uloženém nastavení (neměnit – rozbije uložená nastavení).
        kind: typ pole – "int", "float", "bool", "color", "select", "string", "timeframe",
            "symbol" (symbol "zdroj:SYMBOL" nebo jen "SYMBOL" = zdroj grafu).
        default: výchozí hodnota.
        label: popisek v dialogu (text označený N_ se při zobrazení přeloží).
        group: skupina (sekce dialogu); prázdná = bez nadpisu.
        tooltip: nápověda k poli.
        min, max, step: rozsah a krok číselných polí.
        exclusive_min: dolní mez desetinného čísla je vyloučená (násobky musí být kladné).
        options: volby výběru [(hodnota, popisek)] u typu "select".
        scanner: vstup scanneru (scanner_params) – výpočet indikátoru neovlivňuje.
    """

    name: str
    kind: str
    default: Any
    label: str = ""
    group: str = ""
    tooltip: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    exclusive_min: bool = False
    options: tuple = ()
    scanner: bool = False

    # Továrny jednotlivých typů – kratší deklarace v indikátorech

    @staticmethod
    def int(name: str, default: int, label: str = "", *, min: float = 1, max: float = 1000, **extra) -> "Param":
        """Celé číslo (periody, počty svíček)."""
        return Param(name, "int", default, label, min=min, max=max, step=1, **extra)

    @staticmethod
    def float(name: str, default: float, label: str = "", *, min: float = 0, max: float = 10, step: float = 0.1,
              **extra) -> "Param":
        """Desetinné číslo (násobky, koeficienty)."""
        return Param(name, "float", default, label, min=min, max=max, step=step, **extra)

    @staticmethod
    def bool(name: str, default: bool, label: str = "", **extra) -> "Param":
        """Přepínač ano/ne."""
        return Param(name, "bool", default, label, **extra)

    @staticmethod
    def color(name: str, default: str, label: str = "", **extra) -> "Param":
        """Barva #rrggbb nebo rgba(…) s průhledností."""
        return Param(name, "color", default, label, **extra)

    @staticmethod
    def select(name: str, default: str, label: str = "", *, options: Sequence, **extra) -> "Param":
        """Výběr z voleb; options = [(hodnota, popisek)] nebo [hodnota]."""
        pairs = tuple((item, str(item)) if not isinstance(item, (tuple, list)) else (item[0], item[1]) for item in options)
        return Param(name, "select", default, label, options=pairs, **extra)

    @staticmethod
    def string(name: str, default: str, label: str = "", **extra) -> "Param":
        """Krátký text."""
        return Param(name, "string", default, label, **extra)

    @staticmethod
    def timeframe(name: str, default: str, label: str = "", **extra) -> "Param":
        """Timeframe aplikace (1m … 1W)."""
        return Param(name, "timeframe", default, label, options=tuple((tf, tf) for tf in TIMEFRAMES), **extra)

    @staticmethod
    def symbol(name: str, default: str, label: str = "", **extra) -> "Param":
        """Symbol jako input.symbol v Pine: "zdroj:SYMBOL", jen "SYMBOL" (zdroj grafu) nebo prázdné."""
        return Param(name, "symbol", default, label, **extra)

    def normalize(self, value: Any) -> Any:
        """Ověří a převede hodnotu z dialogu nebo z uloženého nastavení.

        Chybějící hodnota (None) se nahradí výchozí; neplatná vyhodí
        ValueError se zprávou pro uživatele v jazyce rozhraní.
        """
        label = tr(self.label or self.name)
        if value is None:
            return self.default
        if self.kind in ("int", "float"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(tr("{label}: zadejte číslo.").format(label=label)) from None
            if not math.isfinite(number):
                raise ValueError(tr("{label}: zadejte číslo.").format(label=label))
            low = -math.inf if self.min is None else self.min
            high = math.inf if self.max is None else self.max
            if self.kind == "int":
                # Celé číslo v povoleném rozsahu
                if not number.is_integer() or not low <= number <= high:
                    raise ValueError(tr("{label}: zadejte celé číslo {low}–{high}.").format(
                        label=label, low=_fmt(low), high=_fmt(high)))
                return int(number)
            # Desetinné číslo v rozsahu; vyloučená dolní mez = musí být větší
            above_min = low < number if self.exclusive_min else low <= number
            if not above_min or number > high:
                if self.exclusive_min and low == 0:
                    raise ValueError(tr("{label}: zadejte kladné číslo nejvýš {high}.").format(
                        label=label, high=_fmt(high)))
                raise ValueError(tr("{label}: zadejte číslo {low}–{high}.").format(
                    label=label, low=_fmt(low), high=_fmt(high)))
            return number
        if self.kind == "bool":
            return bool(value)
        if self.kind == "color":
            # Neplatná barva = výchozí
            return normalize_color(value) or self.default
        if self.kind in ("select", "timeframe"):
            allowed = [option[0] for option in self.options]
            if value not in allowed:
                raise ValueError(tr("{label}: neplatná volba.").format(label=label))
            return value
        if self.kind == "symbol":
            # Symbol: bez mezer, zdroj (před dvojtečkou) malými písmeny
            text = str(value).strip()
            if any(char.isspace() for char in text) or len(text) > 60 or text.endswith(":"):
                raise ValueError(tr("{label}: zadejte symbol ve tvaru zdroj:SYMBOL.").format(label=label))
            source, symbol = split_symbol(text)
            return f"{source}:{symbol}" if source else symbol
        # Text: oříznutý a omezený na rozumnou délku
        return str(value).strip()[:200]


def _fmt(number: float) -> str:
    """Číslo do chybové zprávy bez zbytečné desetinné části."""
    return f"{number:g}"


# ---------- vstupy scanneru (jako request.security a table v Pine) ----------

# Skupiny vstupů scanneru v dialogu nastavení
G_SCANNER = "Scanner"
G_SCANNER_ALERT = N_("Scanner – alert před zavřením svíčky")
G_SCANNER_SYMBOLS = N_("Scanner – symboly")
# Počet polí symbolů scanneru (jako v Pine) a nejvyšší sledovaný počet posledních svíček
SCANNER_SYMBOLS = 40
MAX_SCAN_BARS = 20
# Pozice tabulky scanneru v grafu a velikost písma (hodnoty jako v Pine)
TABLE_POSITIONS = ("top_right", "top_left", "middle_right", "bottom_right", "bottom_left", ("off", N_("nezobrazovat")))
TABLE_SIZES = ("tiny", "small", "normal", "large")


def scanner_params(default_symbols: Sequence[str] = (), timeframe: str = "1h", alert: bool = False) -> list["Param"]:
    """Vstupy scanneru, které indikátor se signály připojí ke svým parametrům.

    Scanner (TickView, tickview/services/scanner.py) spočítá indikátor se
    stejnými parametry pro každý vyplněný symbol na timeframu scanu, ukáže
    tabulku v grafu s indikátorem a volitelně pošle alert před zavřením
    svíčky – stejně jako request.security a table v Pine.

    Parametry:
        default_symbols: výchozí symboly ("zdroj:SYMBOL"), zbylá pole jsou prázdná.
        timeframe: výchozí timeframe scanu.
        alert: alert před zavřením svíčky ve výchozím stavu zapnutý.
    """
    params = [
        Param.timeframe("scan_timeframe", timeframe, N_("Timeframe scanu"), group=G_SCANNER),
        Param.int("scan_bars", 2, N_("Počet posledních svíček pro signál"), min=1, max=MAX_SCAN_BARS, group=G_SCANNER,
                  tooltip=N_("0 = běžící svíčka; signál starší než zadaný počet svíček se v tabulce neukáže.")),
        Param.select("scan_table_position", "top_right", N_("Pozice tabulky"), options=TABLE_POSITIONS,
                     group=G_SCANNER, tooltip=N_("Při „nezobrazovat“ se symboly počítají jen kvůli alertu.")),
        Param.bool("scan_only_hits", True, N_("Zobrazit pouze trhy se signálem"), group=G_SCANNER),
        Param.select("scan_text_size", "normal", N_("Velikost textu"), options=TABLE_SIZES, group=G_SCANNER),
        Param.bool("scan_strip_prefix", True, N_("Skrýt prefix zdroje"), group=G_SCANNER),
        Param.bool("scan_alert", alert, N_("Posílat alert před zavřením svíčky"), group=G_SCANNER_ALERT,
                   tooltip=N_("Jedna souhrnná zpráva se seznamem trhů se signálem na běžící svíčce timeframu scanu. "
                              "Odejde přes alert na tento indikátor s podmínkou „Any alert() function call“ "
                              "(Alert → Condition), kde se nastavují i notifikace (Telegram, toast, zvuk, webhook).")),
        Param.int("scan_alert_minutes1", 10, N_("1. upozornění (min před zavřením)"), min=0, max=1440,
                  group=G_SCANNER_ALERT, tooltip=N_("0 = vypnuto; každý práh nejvýš jednou za svíčku.")),
        Param.int("scan_alert_minutes2", 5, N_("2. upozornění (min před zavřením)"), min=0, max=1440,
                  group=G_SCANNER_ALERT, tooltip=N_("0 = vypnuto; každý práh nejvýš jednou za svíčku.")),
    ]
    symbols = list(default_symbols)[:SCANNER_SYMBOLS]
    symbols += [""] * (SCANNER_SYMBOLS - len(symbols))
    params += [Param.symbol(f"scan_symbol_{index}", symbol, f"Symbol {index}", group=G_SCANNER_SYMBOLS)
               for index, symbol in enumerate(symbols, start=1)]
    # Příznak vstupu scanneru: has_scanner a klíč úlohy scanneru (parametry výpočtu bez nich)
    return [replace(param, scanner=True) for param in params]


@dataclass(frozen=True)
class Context:
    """Informace o instrumentu a výpočtu, které indikátor dostane od TickView.

    Atributy:
        symbol: symbol zdroje (např. "EURUSD=X").
        source: id zdroje (např. "yahoo").
        timeframe: kanonický timeframe ("1h").
        step_ms: délka svíčky v ms.
        kind: druh instrumentu ("forex", "stock", "crypto", "index", "fund", "futures", "" = neznámý).
        tick: nejmenší změna ceny odhadnutá z dat (forex 0.00001, akcie 0.01).
        exchange_timezone: IANA časové pásmo burzy instrumentu.
        live: True = živý přepočet z rozpracované svíčky (indikátor smí výpočet zkrátit).
        signal_bars: scanner potřebuje jen signály posledních N svíček (None = vše
            pro graf); indikátor pak smí vynechat kreslení a starší svíčky.
        data: externí data podle Indicator.data_requirements ({klíč: DataFrame});
            klíč chybí, dokud se data nenačetla (nebo nejsou dostupná).
    """

    symbol: str = ""
    source: str = ""
    timeframe: str = ""
    step_ms: int = 0
    kind: str = ""
    tick: float = 0.01
    exchange_timezone: str = "UTC"
    live: bool = False
    signal_bars: Optional[int] = None
    data: dict = field(default_factory=dict, compare=False)


@dataclass
class Line:
    """Série přes všechny svíčky (čára nebo histogram).

    Atributy:
        name: název série (legenda).
        values: hodnoty, stejná délka jako svíčky (NaN = bez hodnoty).
        color: barva (None = barva instance; histogram se barví podle znaménka).
        type: výchozí druh vykreslení – "line" nebo "histogram", případně další
            druh z PLOT_TYPES ("linebr" = čára přerušená v místech bez hodnoty,
            "circles" = jen body, "step", "area", "columns"…); uživatel ho může
            v záložce Style změnit.
        options: volby série Lightweight Charts; výchozí tloušťku a styl čáry
            v záložce Style určují "lineWidth" (1–4) a "lineStyle" (LINE_*).
            Sloupce mohou mít vlastní cenovou osu ("priceScaleId") s okraji
            ("scaleMargins", např. objem v dolní části hlavního grafu).
        colors: barvy jednotlivých sloupců histogramu (stejná délka jako
            values; None = podle znaménka); použijí se, když uživatel
            v záložce Style nezvolí jednu barvu.
    """

    name: str
    values: Any
    color: Optional[str] = None
    type: str = "line"
    options: dict = field(default_factory=dict)
    colors: Any = None


@dataclass
class Segment:
    """Úsečka mezi dvěma body grafu (čas v sekundách UTC, cena)."""

    time1: int
    price1: float
    time2: int
    price2: float
    color: str
    width: int = 1
    style: int = LINE_SOLID


@dataclass
class Marker:
    """Značka u svíčky (šipka, kolečko) s volitelným textem.

    Atributy:
        time: čas svíčky (s UTC).
        position: "above", "below" nebo "in" (vůči svíčce).
        shape: "arrowUp", "arrowDown", "circle", "square".
        color: barva.
        text: popisek u značky.
    """

    time: int
    position: str
    shape: str
    color: str
    text: str = ""


@dataclass
class Signal:
    """Signál indikátoru pro scanner a alerty.

    Atributy:
        time: čas svíčky signálu (s UTC).
        direction: "long" nebo "short".
        bars_ago: index svíčky od konce (0 = poslední, rozpracovaná).
        label: krátký popis (např. název vzoru).
    """

    time: int
    direction: str
    bars_ago: int
    label: str = ""


@dataclass
class Table:
    """Tabulka v grafu indikátoru (jako table v Pine): řádky popisek – hodnota
    a volitelný blok poznámek (např. průvodce interpretací) vedle nich.

    Atributy:
        title: nadpis tabulky (první řádek).
        rows: řádky [(popisek, hodnota)].
        position: pozice v panelu indikátoru (u overlay indikátoru v hlavním
            grafu) – hodnota z TABLE_POSITIONS.
        size: velikost textu – hodnota z TABLE_SIZES.
        color: barva nadpisu (None = výchozí).
        notes: řádky poznámek.
        notes_title: nadpis bloku poznámek.
        notes_mode: "always" (poznámky vždy), "hover" (jen po najetí myší na
            tabulku), "off" (bez poznámek).
    """

    title: str
    rows: list[tuple[str, str]] = field(default_factory=list)
    position: str = "top_right"
    size: str = "normal"
    color: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    notes_title: str = ""
    notes_mode: str = "always"


@dataclass
class ProfileLevel:
    """Vodorovná úroveň profilu (POC, VAH, VAL, poor high…) od začátku období.

    Atributy:
        price: cena úrovně.
        color: barva čáry a popisku (None = barva textu grafu podle motivu).
        label: popisek vlevo od profilu ("POC", "VAH"…); prázdný = bez popisku.
        end: konec čáry (čas v s UTC); None = až k pravému okraji grafu
            (prodloužená úroveň, kterou cena zatím neprotnula).
        style: styl čáry (LINE_*).
    """

    price: float
    color: Optional[str] = None
    label: str = ""
    end: Optional[int] = None
    style: int = LINE_DOTTED


@dataclass
class ProfileZone:
    """Cenové pásmo profilu od začátku období (single prints).

    Atributy:
        low, high: dolní a horní cena pásma.
        color: barva výplně (obvykle průhledná).
        end: konec pásma (čas v s UTC); None = až k pravému okraji grafu.
    """

    low: float
    high: float
    color: str
    end: Optional[int] = None


@dataclass
class Profile:
    """Profil TPO (Market Profile) jednoho období: bloky s písmeny po cenových
    řádcích, Value Area, úrovně, pásma a volitelný profil objemu.

    Řádek i pokrývá ceny row_low + i × row_height až row_low + (i + 1) × row_height;
    řádky jsou zdola nahoru. Blok = časový úsek období; jeho pořadí je index
    do block_rows, block_times a block_colors a určuje písmeno (A–Z, a–z, pak
    znovu od A). Blok leží ve všech řádcích svého rozsahu, v řádku jsou bloky
    podle pořadí. Vzhled všech profilů indikátoru určuje Result.profile_style.

    Atributy:
        start, end: začátek a konec období (čas v s UTC).
        row_low: dolní cena nejnižšího řádku.
        row_height: výška řádku v ceně.
        block_rows: (dolní, horní) řádek každého bloku – ceny, kterými trh
            během bloku prošel.
        block_times: začátek každého bloku (s UTC).
        block_seconds: délka bloku v sekundách (rozložení bloků v čase).
        block_colors: barva bloku.
        value_area: (nejnižší, nejvyšší) řádek Value Area.
        poc: řádek POC zvýrazněný barvou textu grafu (None = bez zvýraznění).
        levels: úrovně s popisky (POC, VAH, VAL, poor high / low, střed…).
        zones: pásma (single prints).
        initial_balance: (dolní, horní) cena počáteční rovnováhy – svislá čára
            barvou textu grafu vlevo od profilu (None = nekreslí se).
        volumes: objem v každém řádku (None = bez profilu objemu).
        volume_value_area: (nejnižší, nejvyšší) řádek Value Area objemu.
        volume_levels: čáry přes profil objemu (POC, VAH, VAL objemu).
    """

    start: int
    end: int
    row_low: float
    row_height: float
    block_rows: list[tuple[int, int]]
    block_times: list[int]
    block_seconds: int
    block_colors: list[str]
    value_area: tuple[int, int]
    poc: Optional[int] = None
    levels: list[ProfileLevel] = field(default_factory=list)
    zones: list[ProfileZone] = field(default_factory=list)
    initial_balance: Optional[tuple[float, float]] = None
    volumes: Optional[list[float]] = None
    volume_value_area: Optional[tuple[int, int]] = None
    volume_levels: list[ProfileLevel] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileStyle:
    """Vzhled profilů TPO jednoho indikátoru (záložky Style a Volume profile v TradingView).

    Atributy:
        letters: písmena v blocích (jen při dost velkém bloku, jinak bloky).
        blocks: barevné bloky.
        outside_opacity: krytí řádků mimo Value Area (0–1).
        expand: bloky v čase svého vzniku místo u levého okraje profilu.
        volume_color, volume_va_color: barva řádků profilu objemu mimo / ve Value Area.
        volume_placement: zarovnání sloupců profilu objemu – "left" nebo "right".
        volume_values: hodnoty objemu u řádků.
        volume_values_color: barva hodnot objemu.
    """

    letters: bool = True
    blocks: bool = True
    outside_opacity: float = 0.4
    expand: bool = False
    volume_color: str = "rgba(41, 98, 255, 0.3)"
    volume_va_color: str = "rgba(41, 98, 255, 0.7)"
    volume_placement: str = "left"
    volume_values: bool = False
    volume_values_color: str = "#26a69a"


@dataclass
class Result:
    """Výstup výpočtu indikátoru.

    Atributy:
        lines: série přes svíčky (průměry, oscilátory…).
        levels: vodorovné úrovně panelu (30/70 u RSI…).
        segments: úsečky (trendové linky…).
        markers: značky u svíček.
        signals: signály pro scanner a alerty.
        tables: tabulky v grafu indikátoru (Indicator.has_tables).
        background: barva pozadí panelu u každé svíčky (jako bgcolor v Pine;
            stejná délka jako svíčky, None = bez barvy); jen u indikátoru
            ve vlastním panelu.
        profiles: profily TPO v hlavním grafu.
        profile_style: vzhled profilů.
    """

    lines: list[Line] = field(default_factory=list)
    levels: list[float] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    background: Optional[Sequence[Optional[str]]] = None
    profiles: list[Profile] = field(default_factory=list)
    profile_style: ProfileStyle = field(default_factory=ProfileStyle)


class Indicator:
    """Základ indikátoru; potomek doplní popis, parametry a compute.

    Atributy třídy:
        id: jednoznačný klíč (ukládá se do nastavení – neměnit).
        title: zkratka do legendy ("SMA").
        name: celý název do nabídky (texty pro uživatele lze označit N_ – přeloží se
            při zobrazení; neoznačené se zobrazí beze změny).
        category: klíč CATEGORIES.
        description: krátký popis do nabídky.
        overlay: True = kreslí se přes hlavní graf, False = vlastní panel.
        params: schéma parametrů.
        title_params: parametry, jejichž hodnoty se připojí k názvu v legendě
            (None = všechny číselné).
        has_signals: indikátor vrací signály (alert na signál, scanner).
        has_lines: indikátor vrací úsečky (Result.segments) – záložka Style
            nabídne přepínač Lines.
        has_tables: indikátor vrací tabulky (Result.tables) – záložka Style
            nabídne přepínač Tables (u indikátoru se scannerem automaticky).
        api_version: verze rozhraní, pro kterou je indikátor napsaný.
    """

    id: str = ""
    title: str = ""
    name: str = ""
    category: str = "custom"
    description: str = ""
    overlay: bool = True
    params: list[Param] = []
    title_params: Optional[tuple] = None
    has_signals: bool = False
    has_lines: bool = False
    has_tables: bool = False
    api_version: int = API_VERSION

    def validate(self, params: dict) -> None:
        """Kontrola vztahů mezi parametry (např. rychlá < pomalá); chyba = ValueError."""

    def required_bars(self, params: dict) -> int:
        """Kolik svíček historie výpočet potřebuje (0 = bez zvláštního požadavku).

        Graf načte aspoň tolik svíček, scanner stáhne dost historie.
        """
        return 0

    def data_requirements(self, params: dict, ctx: Context) -> list[str]:
        """Externí data, která výpočet potřebuje (klíče Context.data), např. ["cot:209742"].

        TickView je načte (na pozadí) a předá v ctx.data; dokud nejsou
        k dispozici, compute dostane ctx.data bez daného klíče.
        """
        return []

    def compute(self, df: pd.DataFrame, params: dict, ctx: Context) -> Result:
        """Spočítá indikátor.

        Parametry:
            df: svíčky vzestupně (sloupce time v s UTC, open, high, low, close, volume).
            params: normalizované parametry.
            ctx: informace o instrumentu a výpočtu.
        """
        raise NotImplementedError

    # ---------- pomocné metody (TickView je volá, indikátor je nepřepisuje) ----------

    @property
    def has_scanner(self) -> bool:
        """Indikátor má vstupy scanneru (připojil scanner_params)."""
        return any(param.scanner for param in self.params)

    def defaults(self) -> dict:
        """Výchozí hodnoty všech parametrů."""
        return {param.name: param.default for param in self.params}

    def normalize_params(self, values: dict) -> dict:
        """Ověří parametry podle schématu; neplatná hodnota vyhodí ValueError.

        Neznámé klíče se zahodí, chybějící doplní výchozími.
        """
        source = values if isinstance(values, dict) else {}
        params = {param.name: param.normalize(source.get(param.name)) for param in self.params}
        self.validate(params)
        return params

    def legend_title(self, params: dict, with_inputs: bool = True) -> str:
        """Název instance do legendy, např. "SMA 20" nebo "BB 20 2".

        Parametry:
            params: parametry instance.
            with_inputs: připojit hodnoty vstupů (False = jen název, vypnuté
                „Inputs in status line“ v záložce Style).
        """
        if not with_inputs:
            names = []
        elif self.title_params is None:
            names = [param.name for param in self.params if param.kind in ("int", "float")]
        else:
            names = list(self.title_params)
        values = []
        for name in names:
            value = params.get(name)
            values.append(f"{value:g}" if isinstance(value, float) else str(value))
        # Zkratka nebo celý název (označený N_) v jazyce rozhraní
        return " ".join([tr(self.title or self.name), *values]).strip()
