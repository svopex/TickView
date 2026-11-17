"""Rozhraní indikátorů TickView (jediný modul, který indikátor smí importovat).

Indikátor je třída odvozená od Indicator: popis pro nabídku (název,
kategorie, popis), schéma parametrů (seznam Param) a metoda compute, která
nad svíčkami jednoho symbolu a timeframu vrátí Result – čáry, úrovně panelu,
úsečky, značky a signály. Indikátor nezná UI, cache ani zdroje dat; vše, co
potřebuje vědět o instrumentu, dostane v Context.

Vestavěné indikátory jsou v tickview/indicators/builtin.py, vlastní mají
každý svůj adresář v tickview/indicators/custom/<id>/ se souborem
indicator.py, který definuje INDICATOR (instanci třídy indikátoru).
"""

import math
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

import pandas as pd

from tickview.logic.styles import normalize_color
# Kanonické timeframy zdrojů dat (volby parametru typu timeframe)
from tickview.providers.base import CANONICAL_TIMEFRAMES as TIMEFRAMES

# Verze rozhraní; indikátor s jinou verzí loader odmítne
API_VERSION = 1

# Kategorie nabídky indikátorů (pořadí = pořadí v levém sloupci dialogu)
CATEGORIES = {
    "ma": "Klouzavé průměry",
    "bands": "Pásma a kanály",
    "trend": "Trend",
    "osc": "Oscilátory",
    "volatility": "Volatilita",
    "volume": "Objem",
    "custom": "Vlastní",
}

# Barvy čar instancí (cyklicky podle pořadí přidání, pokud uživatel nezvolí vlastní)
PALETTE = ["#2962ff", "#ff9800", "#9c27b0", "#00bcd4", "#f7b500", "#e91e63"]
UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"
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
        label: popisek v dialogu.
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
        ValueError s českou zprávou pro uživatele.
        """
        label = self.label or self.name
        if value is None:
            return self.default
        if self.kind in ("int", "float"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{label}: zadejte číslo.") from None
            if not math.isfinite(number):
                raise ValueError(f"{label}: zadejte číslo.")
            low = -math.inf if self.min is None else self.min
            high = math.inf if self.max is None else self.max
            if self.kind == "int":
                # Celé číslo v povoleném rozsahu
                if not number.is_integer() or not low <= number <= high:
                    raise ValueError(f"{label}: zadejte celé číslo {_fmt(low)}–{_fmt(high)}.")
                return int(number)
            # Desetinné číslo v rozsahu; vyloučená dolní mez = musí být větší
            above_min = low < number if self.exclusive_min else low <= number
            if not above_min or number > high:
                if self.exclusive_min and low == 0:
                    raise ValueError(f"{label}: zadejte kladné číslo nejvýš {_fmt(high)}.")
                raise ValueError(f"{label}: zadejte číslo {_fmt(low)}–{_fmt(high)}.")
            return number
        if self.kind == "bool":
            return bool(value)
        if self.kind == "color":
            # Neplatná barva = výchozí
            return normalize_color(value) or self.default
        if self.kind in ("select", "timeframe"):
            allowed = [option[0] for option in self.options]
            if value not in allowed:
                raise ValueError(f"{label}: neplatná volba.")
            return value
        if self.kind == "symbol":
            # Symbol: bez mezer, zdroj (před dvojtečkou) malými písmeny
            text = str(value).strip()
            if any(char.isspace() for char in text) or len(text) > 60 or text.endswith(":"):
                raise ValueError(f"{label}: zadejte symbol ve tvaru zdroj:SYMBOL.")
            source, symbol = split_symbol(text)
            return f"{source.lower()}:{symbol}" if source else text
        # Text: oříznutý a omezený na rozumnou délku
        return str(value).strip()[:200]


def split_symbol(text: str) -> tuple[str, str]:
    """Rozloží "zdroj:SYMBOL" na (zdroj, symbol); bez prefixu je zdroj prázdný."""
    source, colon, symbol = text.partition(":")
    return (source, symbol) if colon else ("", text)


def _fmt(number: float) -> str:
    """Číslo do chybové zprávy bez zbytečné desetinné části."""
    return f"{number:g}"


# ---------- vstupy scanneru (jako skupina Scanner v Pine skriptu Svopex TTW) ----------

# Skupiny vstupů scanneru v dialogu nastavení
G_SCANNER = "Scanner"
G_SCANNER_ALERT = "Scanner – alert před zavřením svíčky"
G_SCANNER_SYMBOLS = "Scanner – symboly"
# Počet polí symbolů scanneru (jako v Pine) a nejvyšší sledovaný počet posledních svíček
SCANNER_SYMBOLS = 40
MAX_SCAN_BARS = 20
# Pozice tabulky scanneru v grafu a velikost písma (hodnoty jako v Pine)
TABLE_POSITIONS = ("top_right", "top_left", "middle_right", "bottom_right", "bottom_left", ("off", "nezobrazovat"))
TABLE_SIZES = ("tiny", "small", "normal", "large")


def scanner_params(default_symbols: Sequence[str] = (), timeframe: str = "1h") -> list["Param"]:
    """Vstupy scanneru, které indikátor se signály připojí ke svým parametrům.

    Scanner (TickView, tickview/services/scanner.py) spočítá indikátor se
    stejnými parametry pro každý vyplněný symbol na timeframu scanu, ukáže
    tabulku v grafu s indikátorem a volitelně pošle alert před zavřením
    svíčky – stejně jako request.security a table v Pine skriptu.

    Parametry:
        default_symbols: výchozí symboly ("zdroj:SYMBOL"), zbylá pole jsou prázdná.
        timeframe: výchozí timeframe scanu.
    """
    params = [
        Param.timeframe("scan_timeframe", timeframe, "Timeframe scanu", group=G_SCANNER),
        Param.int("scan_bars", 2, "Počet posledních svíček pro signál", min=1, max=MAX_SCAN_BARS, group=G_SCANNER,
                  tooltip="0 = běžící svíčka; signál starší než zadaný počet svíček se v tabulce neukáže."),
        Param.select("scan_table_position", "top_right", "Pozice tabulky", options=TABLE_POSITIONS,
                     group=G_SCANNER, tooltip="Při „nezobrazovat“ se symboly počítají jen kvůli alertu."),
        Param.bool("scan_only_hits", True, "Zobrazit pouze trhy se signálem", group=G_SCANNER),
        Param.select("scan_text_size", "normal", "Velikost textu", options=TABLE_SIZES, group=G_SCANNER),
        Param.bool("scan_strip_prefix", True, "Skrýt prefix zdroje", group=G_SCANNER),
        Param.bool("scan_alert", False, "Posílat alert před zavřením svíčky", group=G_SCANNER_ALERT,
                   tooltip="Jedna souhrnná zpráva se seznamem trhů se signálem na běžící svíčce timeframu scanu."),
        Param.int("scan_alert_minutes1", 10, "1. upozornění (min před zavřením)", min=0, max=1440,
                  group=G_SCANNER_ALERT, tooltip="0 = vypnuto; každý práh nejvýš jednou za svíčku."),
        Param.int("scan_alert_minutes2", 5, "2. upozornění (min před zavřením)", min=0, max=1440,
                  group=G_SCANNER_ALERT, tooltip="0 = vypnuto; každý práh nejvýš jednou za svíčku."),
        Param.bool("scan_alert_telegram", True, "Telegram", group=G_SCANNER_ALERT),
        Param.bool("scan_alert_toast", True, "Oznámení v aplikaci", group=G_SCANNER_ALERT),
        Param.bool("scan_alert_sound", False, "Zvuk", group=G_SCANNER_ALERT),
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


@dataclass
class Line:
    """Série přes všechny svíčky (čára nebo histogram).

    Atributy:
        name: název série (legenda).
        values: hodnoty, stejná délka jako svíčky (NaN = bez hodnoty).
        color: barva (None = barva instance; histogram se barví podle znaménka).
        type: "line" nebo "histogram".
        options: volby série Lightweight Charts (lineStyle, pointMarkersVisible…).
    """

    name: str
    values: Any
    color: Optional[str] = None
    type: str = "line"
    options: dict = field(default_factory=dict)


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
        label: krátký popis (např. typ formace).
    """

    time: int
    direction: str
    bars_ago: int
    label: str = ""


@dataclass
class Result:
    """Výstup výpočtu indikátoru.

    Atributy:
        lines: série přes svíčky (průměry, oscilátory…).
        levels: vodorovné úrovně panelu (30/70 u RSI…).
        segments: úsečky (trendové linky…).
        markers: značky u svíček.
        signals: signály pro scanner a alerty.
    """

    lines: list[Line] = field(default_factory=list)
    levels: list[float] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)


class Indicator:
    """Základ indikátoru; potomek doplní popis, parametry a compute.

    Atributy třídy:
        id: jednoznačný klíč (ukládá se do nastavení – neměnit).
        title: zkratka do legendy ("SMA").
        name: celý název do nabídky.
        category: klíč CATEGORIES.
        description: krátký popis do nabídky.
        overlay: True = kreslí se přes hlavní graf, False = vlastní panel.
        params: schéma parametrů.
        title_params: parametry, jejichž hodnoty se připojí k názvu v legendě
            (None = všechny číselné).
        uses_color: instance má hlavní barvu (pole Barva v dialogu).
        has_signals: indikátor vrací signály (alert na signál, scanner).
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
    uses_color: bool = True
    has_signals: bool = False
    api_version: int = API_VERSION

    def validate(self, params: dict) -> None:
        """Kontrola vztahů mezi parametry (např. rychlá < pomalá); chyba = ValueError."""

    def required_bars(self, params: dict) -> int:
        """Kolik svíček historie výpočet potřebuje (0 = bez zvláštního požadavku).

        Graf načte aspoň tolik svíček, scanner stáhne dost historie.
        """
        return 0

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

    def legend_title(self, params: dict) -> str:
        """Název instance do legendy, např. "SMA 20" nebo "BB 20 2"."""
        if self.title_params is None:
            names = [param.name for param in self.params if param.kind in ("int", "float")]
        else:
            names = list(self.title_params)
        values = []
        for name in names:
            value = params.get(name)
            values.append(f"{value:g}" if isinstance(value, float) else str(value))
        return " ".join([self.title or self.name, *values]).strip()
