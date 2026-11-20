"""Indikátory COT reportu CFTC: čisté pozice skupiny obchodníků a COT Index.

Data (Legacy report, jen futures) dodá TickView v ctx.data pod klíčem
"cot:<kód trhu>" (Indicator.data_requirements). Trh se odvodí ze symbolu grafu
(futures, forex, kovy, indexová CFD) nebo se zadá ručně kódem CFTC. Hodnoty
reportu platí v grafu od okamžiku zveřejnění (pátek 15:30 New York), aby
replay ani signály neviděly report dřív, než skutečně vyšel; volitelně od data
reportu (úterý) jako v TradingView. Výpočty (COT Index) běží nad celou týdenní
řadou reportů, takže délka okna nezávisí na tom, kolik svíček má graf.

Pevné texty pro uživatele (popisky, poznámky, řádky tabulky) jsou označené N_
a překládají se při zobrazení; texty s hodnotami se překládají přes tr už při
výpočtu (výpočet běží při každém vykreslení grafu).
"""

import math
from datetime import date, datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from tickview.indicators.api import (
    N_,
    TABLE_POSITIONS,
    TABLE_SIZES,
    Context,
    Indicator,
    Line,
    Param,
    Result,
    Signal,
    Table,
    tr,
)
from tickview.logic.cot import GROUP_OPTIONS, GROUPS, CotMarket, cot_index, cot_key, resolve_market

# Barvy skupin: čistá pozice (panel pozic) a COT Index
POSITION_COLORS = {"commercials": "#00bcd4", "noncommercials": "#ff9800", "nonreportables": "#e91e63"}
INDEX_COLORS = {"commercials": "#2962ff", "noncommercials": "#ff9800", "nonreportables": "#ef5350"}
# Průhlednost histogramu týdenní změny a pozadí extrému COT Indexu
CHANGE_ALPHA = 0.55
BACKGROUND_ALPHA = 0.14

# Skupiny parametrů v dialogu nastavení
G_DATA = N_("Data COT")
G_TABLE = N_("Tabulka")

# Nadpis bloku poznámek tabulky
GUIDE_TITLE = N_("Průvodce interpretací")
# Průvodce interpretací čistých pozic jednotlivých skupin
POSITION_GUIDES = {
    "commercials": [
        N_("Commercials jsou typicky hedgeři (výrobci, zpracovatelé, banky)."),
        N_("Kladná net pozice: víc longů než shortů; záporná: víc shortů."),
        N_("Obchodují proti trendu – kontrariánský indikátor."),
        N_("Extrémy pozice často předcházejí obratu trhu."),
    ],
    "noncommercials": [
        N_("Non-Commercials jsou velcí spekulanti a fondy."),
        N_("Kladná net pozice: víc longů než shortů; záporná: víc shortů."),
        N_("Obvykle následují trend – indikátor momenta."),
        N_("Extrémní pozice ukazují přeplněný obchod a riziko obratu."),
    ],
    "nonreportables": [
        N_("Non-reportables jsou drobní obchodníci pod hranicí hlášení."),
        N_("Kladná net pozice: víc longů než shortů; záporná: víc shortů."),
        N_("Často obchodují proti institucím – kontrariánský indikátor."),
        N_("Extrémy pozice bývají u vrcholů a propadů trhu."),
    ],
}
# Průvodce COT Indexem podle skupiny (vysoký / nízký index); zástupné symboly
# se doplní po překladu při výpočtu
INDEX_GUIDES = {
    "commercials": N_("Commercials > {upper}: potenciální býčí obrat; < {lower}: potenciální medvědí tlak."),
    "noncommercials": N_("Non-Commercials > {upper}: přeplněné longy fondů; < {lower}: přeplněné shorty."),
    "nonreportables": N_("Non-reportables > {upper}: drobní překoupení (medvědí); < {lower}: přeprodaní (býčí)."),
}
# Řádek poznámek u obráceného směru (pár USD/xxx proti futures na xxx/USD)
INVERT_NOTE = N_("Pozice jsou obrácené: pár USD/xxx roste, když xxx slábne.")


def _data_params() -> list[Param]:
    """Společné parametry dat COT (trh, obrácení směru, zarovnání v čase)."""
    return [
        Param.string("market", "", N_("Kód trhu CFTC"), group=G_DATA,
                     tooltip=N_("Prázdné = podle symbolu grafu (futures, forex, kovy, indexová CFD). "
                                "Jinak šestimístný kód trhu z COT reportu, např. 209742 (Nasdaq mini).")),
        Param.select("invert", "auto", N_("Obrátit směr"), group=G_DATA,
                     options=(("auto", N_("Automaticky (pár USD/xxx)")), ("off", N_("Ne")), ("on", N_("Ano"))),
                     tooltip=N_("Pár USD/JPY se pohybuje opačně než futures na jen (6J) – pozice se obrátí.")),
        Param.select("align", "release", N_("Platnost reportu od"), group=G_DATA,
                     options=(("release", N_("Zveřejnění (pátek 15:30 NY)")), ("report", N_("Data reportu (úterý)"))),
                     tooltip=N_("Report popisuje stav k úterku, ale vychází až v pátek. Zveřejnění = bez pohledu "
                                "do budoucnosti (replay, signály); datum reportu = jako v TradingView.")),
    ]


def _table_params() -> list[Param]:
    """Společné parametry tabulky (pozice, průvodce interpretací, velikost textu)."""
    return [
        Param.select("table_position", "top_left", N_("Pozice tabulky"), options=TABLE_POSITIONS, group=G_TABLE,
                     tooltip=N_("Vlevo tabulka nezakrývá nejnovější data ani popisky čar u cenové osy.")),
        Param.select("guide", "always", N_("Průvodce interpretací"), group=G_TABLE,
                     options=(("always", N_("Vždy")), ("hover", N_("Po najetí myší na tabulku")),
                              ("off", N_("Nezobrazovat")))),
        Param.select("table_size", "small", N_("Velikost textu"), options=TABLE_SIZES, group=G_TABLE),
    ]


def _with_alpha(color: str, alpha: float) -> str:
    """Barva #rrggbb s průhledností jako rgba(…)."""
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def _format_number(value: float, signed: bool = False) -> str:
    """Celé číslo s mezerami po tisících ("−49 815", u signed "+13 455"); NaN = "–"."""
    if value is None or not math.isfinite(value):
        return "–"
    text = f"{abs(value):,.0f}".replace(",", " ")
    if value < 0:
        return f"−{text}"
    return f"+{text}" if signed and value > 0 else text


def _format_decimal(value: float, signed: bool = False) -> str:
    """Číslo s jedním desetinným místem a desetinnou čárkou ("21,7", u signed "+10,1"); NaN = "–"."""
    if value is None or not math.isfinite(value):
        return "–"
    return f"{value:{'+' if signed else ''}.1f}".replace(".", ",")


def _format_date(day: date) -> str:
    return f"{day.day}. {day.month}. {day.year}"


def _report_text(reports: pd.DataFrame, index: int) -> str:
    """Datum reportu a den zveřejnění do tabulky ("10. 11. 2026 (vyšel 13. 11.)")."""
    report = reports["report"].iloc[index]
    released = datetime.fromtimestamp(reports["release_ms"].iloc[index] / 1000, tz=timezone.utc).date()
    return tr("{report} (vyšel {released})").format(report=_format_date(report),
                                                    released=f"{released.day}. {released.month}.")


class CotIndicator(Indicator):
    """Společný základ indikátorů COT: trh, data, zarovnání reportů na svíčky a tabulka."""

    category = "cot"
    overlay = False
    has_tables = True

    def market(self, params: dict, ctx: Context) -> Optional[CotMarket]:
        """Trh COT podle parametru (ruční kód) nebo symbolu grafu."""
        return resolve_market(params["market"], ctx.symbol, ctx.kind)

    def data_requirements(self, params: dict, ctx: Context) -> list[str]:
        market = self.market(params, ctx)
        return [cot_key(market.code)] if market else []

    def prepare(self, df: pd.DataFrame, params: dict, ctx: Context):
        """Společný úvod výpočtu: trh, reporty, znaménko a index reportu u každé svíčky.

        Vrací (market, reports, sign, idx, status); idx je None, když se nepočítá
        (bez trhu, bez dat nebo bez svíček) – status pak nese řádky tabulky
        s důvodem (symbol bez COT, načítání), jinak je status None.
        """
        market = self.market(params, ctx)
        reports = ctx.data.get(cot_key(market.code)) if market else None
        status = self.status_rows(market, reports)
        if status is not None or not len(df):
            return market, reports, 1, None, status or []
        return market, reports, self.sign(params, market), self.align(df, ctx, reports, params["align"]), None

    @staticmethod
    def sign(params: dict, market: CotMarket) -> int:
        """Znaménko pozic: −1 = obrátit (ručně, nebo automaticky u páru USD/xxx)."""
        invert = params["invert"] == "on" or (params["invert"] == "auto" and market.invert)
        return -1 if invert else 1

    @staticmethod
    def align(df: pd.DataFrame, ctx: Context, reports: pd.DataFrame, by: str) -> np.ndarray:
        """Index reportu platného u každé svíčky (−1 = žádný).

        Report platí pro svíčku, během které vyšel (zveřejnění, případně půlnoc
        dne reportu UTC, je před zavřením svíčky) – na denním grafu tedy už
        páteční svíčka, na týdenním týden zveřejnění.

        Parametry:
            df: svíčky (sloupec time v s UTC).
            ctx: kontext (délka svíčky step_ms).
            reports: reporty vzestupně (sloupce release_ms, report_ms).
            by: "release" = od zveřejnění, "report" = od data reportu.
        """
        times = df["time"].to_numpy(dtype="int64")
        step_s = ctx.step_ms // 1000
        if not step_s and len(times) > 1:
            # Neznámý timeframe: délka svíčky z typického rozestupu časů
            step_s = int(np.median(np.diff(times)))
        keys = reports["release_ms" if by == "release" else "report_ms"].to_numpy(dtype="int64") // 1000
        return np.searchsorted(keys, times + step_s, side="left") - 1

    @staticmethod
    def spread(values: np.ndarray, idx: np.ndarray) -> np.ndarray:
        """Hodnoty reportů rozprostřené na svíčky podle indexů (bez reportu = NaN)."""
        out = np.full(len(idx), np.nan)
        valid = idx >= 0
        out[valid] = values[idx[valid]]
        return out

    @staticmethod
    def report_starts(idx: np.ndarray) -> np.ndarray:
        """Příznak svíček, na kterých začíná platit nový report."""
        previous = np.concatenate(([-1], idx[:-1]))
        return (idx >= 0) & (idx != previous)

    def table(self, params: dict, ctx: Context, title: str, rows: list[tuple[str, str]], notes: list[str],
              color: Optional[str], sign: int) -> list[Table]:
        """Tabulka indikátoru podle parametrů (pozice „nezobrazovat“ nebo výpočet jen
        pro signály scanneru a alertů = bez tabulky); obrácené pozice přidají poznámku."""
        if params["table_position"] == "off" or ctx.signal_bars is not None:
            return []
        if sign < 0:
            notes = notes + [INVERT_NOTE]
        return [Table(title, rows, position=params["table_position"], size=params["table_size"], color=color,
                      notes=notes, notes_title=GUIDE_TITLE, notes_mode=params["guide"])]

    @staticmethod
    def status_rows(market: Optional[CotMarket], reports) -> Optional[list[tuple[str, str]]]:
        """Řádky tabulky, když data nejsou (symbol bez COT, načítání); None = data jsou."""
        if market is None:
            return [(N_("Trh"), N_("symbol nemá COT")), (N_("Tip"), N_("zadejte kód trhu CFTC"))]
        if reports is None:
            return [(N_("Trh"), market.code), ("Data", N_("načítám COT report…"))]
        if reports.empty:
            return [(N_("Trh"), market.code), ("Data", N_("trh nemá data"))]
        return None

    @staticmethod
    def base_rows(ctx: Context, market: CotMarket, reports: pd.DataFrame) -> list[tuple[str, str]]:
        """Úvodní řádky tabulky: trh a symbol."""
        name = reports["market"].iloc[-1] or market.code
        return [(N_("Trh"), f"{name} ({market.code})"), ("Symbol", ctx.symbol or "–")]


class CotPositions(CotIndicator):
    """Čistá pozice (long − short) zvolené skupiny a její týdenní změna."""

    id = "cot_positions"
    title = "COT"
    name = N_("COT – pozice skupiny")
    description = N_("Čistá pozice skupiny obchodníků z COT reportu CFTC (Commercials, Non-Commercials, "
                     "Non-reportables) a její týdenní změna.")
    params = [
        Param.select("group", "commercials", N_("Skupina"), options=GROUP_OPTIONS),
        *_data_params(),
        *_table_params(),
    ]

    def legend_title(self, params: dict, with_inputs: bool = True) -> str:
        return f"{self.title} {GROUPS[params.get('group', 'commercials')][0]}"

    def compute(self, df: pd.DataFrame, params: dict, ctx: Context) -> Result:
        group = params["group"]
        label = GROUPS[group][0]
        color = POSITION_COLORS[group]
        market, reports, sign, idx, rows = self.prepare(df, params, ctx)
        net = np.full(len(df), np.nan)
        change = np.full(len(df), np.nan)
        if idx is not None:
            net = self.spread(reports[f"{group}_net"].to_numpy() * sign, idx)
            # Týdenní změna jen na svíčce, kde nový report začíná platit
            starts = self.report_starts(idx)
            change[starts] = reports[f"{group}_change"].to_numpy()[idx[starts]] * sign
            rows = self.base_rows(ctx, market, reports) + self.value_rows(reports, int(idx[-1]), group, sign)
        lines = [
            Line("Net", net, color=color, type="step", options={"lineWidth": 2, "title": label}),
            Line(N_("Týdenní změna"), change, color=_with_alpha(color, CHANGE_ALPHA), type="histogram"),
        ]
        return Result(lines=lines, levels=[0.0],
                      tables=self.table(params, ctx, f"COT {label}", rows, POSITION_GUIDES[group], color, sign))

    @staticmethod
    def value_rows(reports: pd.DataFrame, index: int, group: str, sign: int) -> list[tuple[str, str]]:
        """Řádky hodnot reportu platného u poslední svíčky (obrácené pozice prohodí long a short)."""
        if index < 0:
            return [("Report", N_("graf končí před prvním reportem"))]
        row = reports.iloc[index]
        net = row[f"{group}_net"] * sign
        long, short = row[f"{group}_long"], row[f"{group}_short"]
        if sign < 0:
            long, short = short, long
        oi = row["oi"]
        return [
            (N_("Čistá pozice"), _format_number(net)),
            (N_("Týdenní změna"), _format_number(row[f"{group}_change"] * sign, signed=True)),
            ("Long / Short", f"{_format_number(long)} / {_format_number(short)}"),
            ("Net / open interest", f"{_format_decimal(net / oi * 100, signed=True)} %" if oi else "–"),
            ("Report", _report_text(reports, index)),
        ]


class CotIndex(CotIndicator):
    """COT Index (Larry Williams) dvou skupin s úrovněmi extrémů, pozadím a signály."""

    id = "cot_index"
    title = "COT Index"
    name = "COT Index"
    description = N_("Poloha čisté pozice skupiny v rozsahu posledních N týdnů (0–100) podle Larryho Williamse; "
                     "extrémy nad a pod úrovněmi zvýrazní pozadí a dají signál.")
    params = [
        Param.int("lookback", 52, N_("Lookback (týdny)"), min=4, max=520),
        Param.select("group1", "commercials", N_("Skupina 1 (signály)"), options=GROUP_OPTIONS),
        Param.select("group2", "nonreportables", N_("Skupina 2"), options=(*GROUP_OPTIONS, ("none", "–"))),
        Param.int("upper", 80, N_("Horní extrém"), min=1, max=99),
        Param.int("lower", 20, N_("Dolní extrém"), min=1, max=99),
        Param.bool("background", False, N_("Zvýraznit extrémy pozadím")),
        *_data_params(),
        *_table_params(),
    ]
    title_params = ("lookback",)
    has_signals = True

    def validate(self, params: dict) -> None:
        if params["lower"] >= params["upper"]:
            raise ValueError(tr("Dolní extrém musí být menší než horní."))
        if params["group1"] == params["group2"]:
            raise ValueError(tr("Skupina 2 musí být jiná než skupina 1."))

    def groups(self, params: dict) -> list[str]:
        """Zobrazené skupiny (skupina 2 je volitelná)."""
        return [params["group1"]] + ([params["group2"]] if params["group2"] != "none" else [])

    def compute(self, df: pd.DataFrame, params: dict, ctx: Context) -> Result:
        groups = self.groups(params)
        upper, lower = params["upper"], params["lower"]
        market, reports, sign, idx, rows = self.prepare(df, params, ctx)
        values = {group: np.full(len(df), np.nan) for group in groups}
        # Poznámky s hodnotami: nejdřív překlad šablony, pak doplnění hodnot
        notes = [tr(INDEX_GUIDES[group]).format(upper=upper, lower=lower) for group in groups]
        notes.append(tr("Index = poloha čisté pozice v rozsahu posledních {weeks} týdnů (0–100).").format(
            weeks=params["lookback"]))
        background = None
        signals: list[Signal] = []
        if idx is not None:
            # Index nad celou řadou reportů (obrácené pozice = obrácený index)
            weekly = {group: cot_index(reports[f"{group}_net"] * sign, params["lookback"]).to_numpy()
                      for group in groups}
            values = {group: self.spread(weekly[group], idx) for group in groups}
            # Pozadí jen pro graf (scanner a alerty potřebují jen signály)
            if params["background"] and ctx.signal_bars is None:
                background = self.extreme_background(values, groups, upper, lower)
            signals = self.signals(df, idx, weekly[groups[0]], groups[0], upper, lower)
            last = int(idx[-1])
            weeks = tr("{weeks} týdnů").format(weeks=params["lookback"])
            rows = self.base_rows(ctx, market, reports) + [("Lookback", weeks)]
            rows += [(GROUPS[group][0], _format_decimal(weekly[group][last] if last >= 0 else math.nan))
                     for group in groups]
            rows.append(("Report", _report_text(reports, last) if last >= 0 else "–"))
        lines = [
            Line(GROUPS[group][0], values[group], color=INDEX_COLORS[group], type="step",
                 options={"lineWidth": 2, "title": GROUPS[group][0]})
            for group in groups
        ]
        return Result(lines=lines, levels=[float(lower), float(upper)], signals=signals, background=background,
                      tables=self.table(params, ctx, "COT Index", rows, notes, INDEX_COLORS[groups[0]], sign))

    @staticmethod
    def extreme_background(values: dict, groups: list[str], upper: int, lower: int) -> list[Optional[str]]:
        """Barva pozadí svíček, kde je skupina v extrému (přednost má skupina 1)."""
        colors: list[Optional[str]] = [None] * len(values[groups[0]])
        for group in reversed(groups):
            series = values[group]
            extreme = (series >= upper) | (series <= lower)
            color = _with_alpha(INDEX_COLORS[group], BACKGROUND_ALPHA)
            for position in np.flatnonzero(extreme):
                colors[position] = color
        return colors

    @staticmethod
    def signals(df: pd.DataFrame, idx: np.ndarray, weekly: np.ndarray, group: str, upper: int,
                lower: int) -> list[Signal]:
        """Signály při vstupu skupiny 1 do extrému (na svíčce, kde report začne platit).

        Commercials nad horní úrovní = LONG (hedgeři nakupují), pod dolní = SHORT;
        spekulanti a drobní obchodníci kontrariánsky (nad horní = SHORT).
        """
        signals = []
        times = df["time"].to_numpy(dtype="int64")
        count = len(idx)
        contrarian = group != "commercials"
        for position in np.flatnonzero(CotIndicator.report_starts(idx)):
            report = int(idx[position])
            if report < 1:
                continue
            value, previous = weekly[report], weekly[report - 1]
            if not (math.isfinite(value) and math.isfinite(previous)):
                continue
            if value >= upper > previous:
                high = True
            elif value <= lower < previous:
                high = False
            else:
                continue
            direction = "short" if high == contrarian else "long"
            label = f"COT {GROUPS[group][0]} {value:.0f}"
            signals.append(Signal(int(times[position]), direction, count - 1 - int(position), label))
        return signals


COT_INDICATORS: list[Indicator] = [CotPositions(), CotIndex()]
