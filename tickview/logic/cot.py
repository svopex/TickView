"""COT report CFTC (Commitments of Traders): trhy, mapování symbolů a čas zveřejnění.

Data jsou z veřejného API CFTC (Socrata, publicreporting.cftc.gov) – Legacy
report jen futures: pozice Commercials (hedgeři), Non-commercials (velcí
spekulanti, fondy) a Non-reportables (drobní obchodníci) k úterku, zveřejněné
v pátek v 15:30 New York (svátek v USA od úterý do pátku ho posune o pracovní den).
Modul obsahuje jen čisté funkce a tabulky (bez sítě a databáze), aby šel testovat.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from tickview.providers.ibkr_futures import CONTINUOUS_SUFFIX

# Legacy report jen futures (dataset Socrata) a adresa jeho API
LEGACY_DATASET = "6dca-aqww"
API_URL = f"https://publicreporting.cftc.gov/resource/{LEGACY_DATASET}.json"

# Skupiny obchodníků Legacy reportu: klíč -> (název, sloupec long, sloupec short,
# sloupec týdenní změny long, sloupec týdenní změny short)
GROUPS: dict[str, tuple[str, str, str, str, str]] = {
    "commercials": ("Commercials", "comm_positions_long_all", "comm_positions_short_all",
                    "change_in_comm_long_all", "change_in_comm_short_all"),
    "noncommercials": ("Non-Commercials", "noncomm_positions_long_all", "noncomm_positions_short_all",
                       "change_in_noncomm_long_all", "change_in_noncomm_short_all"),
    "nonreportables": ("Non-reportables", "nonrept_positions_long_all", "nonrept_positions_short_all",
                       "change_in_nonrept_long_all", "change_in_nonrept_short_all"),
}
# Volby skupiny pro parametr indikátoru [(klíč, popisek)]
GROUP_OPTIONS = tuple((key, spec[0]) for key, spec in GROUPS.items())

# Sloupce, které se z API stahují (datum reportu, název trhu, open interest a pozice skupin)
API_FIELDS = (
    "report_date_as_yyyy_mm_dd",
    "contract_market_name",
    "open_interest_all",
    *(column for spec in GROUPS.values() for column in spec[1:]),
)

# Prefix klíče dat COT reportu v Context.data ("cot:209742")
COT_KEY_PREFIX = "cot:"

# Kód trhu CFTC: 6 znaků (číslice, velká písmena, "+" u konsolidovaných trhů);
# ověřuje se před vložením do dotazu API
MARKET_CODE_RE = re.compile(r"^[0-9A-Z+]{6}$")

# Zveřejnění reportu: pátek týdne reportu v 15:30 New York
RELEASE_TIMEZONE = ZoneInfo("America/New_York")
RELEASE_TIME = time(15, 30)
# Data bez nového reportu se znovu stáhnou nejdřív po této době (svátek,
# zpožděné zveřejnění) a nejpozději po týdnu (opravy starších reportů)
REFRESH_RETRY_MS = 30 * 60 * 1000
REFRESH_MAX_AGE_MS = 7 * 24 * 3600 * 1000


@dataclass(frozen=True)
class CotMarket:
    """Trh COT reportu pro symbol grafu.

    Atributy:
        code: kód trhu CFTC ("209742").
        invert: pozice obrátit (pár USD/xxx proti futures na xxx/USD, např. USDJPY z 6J).
    """

    code: str
    invert: bool = False


def cot_key(code: str) -> str:
    """Klíč dat trhu v Context.data ("cot:209742")."""
    return COT_KEY_PREFIX + code


# Kódy trhů CFTC (Legacy, jen futures) podle kořene futures; ověřené proti API.
# Eurex (FDAX, FESX, FGBL) CFTC nevykazuje, Nikkei v USD (NKD) v reportu není
COT_CODES: dict[str, str] = {
    # Akciové indexy (mini a micro kontrakty mají vlastní trh)
    "ES": "13874A", "MES": "13874U", "NQ": "209742", "MNQ": "209747", "YM": "124603", "MYM": "124608",
    "RTY": "239742", "M2K": "239747", "VX": "1170E1", "VIX": "1170E1",
    # Kovy
    "GC": "088691", "MGC": "088695", "SI": "084691", "HG": "085692", "PL": "076651",
    # Energie (MCL nemá vlastní trh – bere se WTI)
    "CL": "067651", "MCL": "067651", "BZ": "06765T", "NG": "023651",
    # Dluhopisy
    "ZT": "042601", "ZF": "044601", "ZN": "043602", "ZB": "020601",
    # Zemědělské komodity
    "ZC": "002602", "ZS": "005602", "ZW": "001602",
    # Měny
    "6E": "099741", "6B": "096742", "6J": "097741", "6A": "232741", "6C": "090741", "6S": "092741",
    "6N": "112741", "6M": "095741",
    # Krypto CME
    "BTC": "133741", "MBT": "133742", "ETH": "146021", "MET": "146022",
}

# Měna (nebo kov) proti USD -> kořen futures, jehož COT odpovídá páru xxx/USD
CURRENCY_ROOTS = {
    "EUR": "6E", "GBP": "6B", "JPY": "6J", "AUD": "6A", "CAD": "6C", "CHF": "6S", "NZD": "6N", "MXN": "6M",
    "XAU": "GC", "XAG": "SI",
}
# Kořen futures pro symboly jiných zdrojů: CFD a spotové kovy IB, indexy Yahoo
SYMBOL_ROOTS = {
    "IBUS500": "ES", "IBUST100": "NQ", "IBUS30": "YM", "IBUSOIL": "CL", "IBUKOIL": "BZ",
    "^GSPC": "ES", "^SPX": "ES", "^NDX": "NQ", "^DJI": "YM", "^RUT": "RTY", "^VIX": "VX",
}
# Krypto: základ symbolu -> kořen futures CME
CRYPTO_ROOTS = {"BTC": "BTC", "XBT": "BTC", "ETH": "ETH"}


def _market(root: Optional[str], invert: bool = False) -> Optional[CotMarket]:
    """Trh COT podle kořene futures (neznámý kořen = None)."""
    return CotMarket(COT_CODES[root], invert) if root in COT_CODES else None


# Kontinuální futures (případně s burzou) a futures Yahoo: kořen před "1!" / "=F"
FUTURES_SYMBOL_RE = re.compile(rf"^([A-Z0-9]+?)(?:{re.escape(CONTINUOUS_SUFFIX)}(?:@[A-Z0-9]+)?|=F)$")
# Krypto: základ symbolu před kotací (BTCUSDT, BTC-USD, BTC/USD)
CRYPTO_SYMBOL_RE = re.compile(r"^([A-Z]+?)(?:[-/]|USDT?$|USDC$|EUR$)")


def market_for_symbol(symbol: str, kind: str = "") -> Optional[CotMarket]:
    """Trh COT pro symbol grafu, nebo None (symbol nemá COT).

    Rozpozná kontinuální futures ("NQ1!", "VIX1!@CFE"), futures Yahoo ("GC=F"),
    forex a spotové kovy proti USD ("EUR.USD", "EURUSD=X", "JPY=X", "XAUUSD"),
    CFD a indexy ("IBUS500", "^GSPC") a krypto ("BTCUSDT", "BTC-USD").

    Parametry:
        symbol: symbol zdroje bez prefixu zdroje.
        kind: druh instrumentu ze zdroje ("forex", "crypto"…).
    """
    text = symbol.strip().upper()
    if not text:
        return None
    futures = FUTURES_SYMBOL_RE.match(text)
    if futures:
        return _market(futures.group(1))
    if text in SYMBOL_ROOTS:
        return _market(SYMBOL_ROOTS[text])
    if kind == "crypto":
        base = CRYPTO_SYMBOL_RE.match(text)
        return _market(CRYPTO_ROOTS.get(base.group(1)) if base else None)
    pair = _currency_pair(text)
    if pair is None:
        return None
    base, quote = pair
    # xxx/USD odpovídá futures přímo, USD/xxx obráceně (USDJPY roste, když jen slábne)
    if quote == "USD":
        return _market(CURRENCY_ROOTS.get(base))
    if base == "USD":
        return _market(CURRENCY_ROOTS.get(quote), invert=True)
    return None


def _currency_pair(text: str) -> Optional[tuple[str, str]]:
    """Měnový pár ze symbolu: "EUR.USD", "EURUSD=X", "EUR/USD", "XAUUSD" → (EUR, USD); "JPY=X" → (USD, JPY)."""
    # Yahoo: "JPY=X" je kurz USD/JPY, "EURUSD=X" pár
    if text.endswith("=X"):
        text = text[:-2]
        if len(text) == 3 and text.isalpha():
            return "USD", text
    letters = re.sub(r"[./\-_ ]", "", text)
    if len(letters) == 6 and letters.isalpha():
        return letters[:3], letters[3:]
    return None


def resolve_market(manual_code: str, symbol: str, kind: str = "") -> Optional[CotMarket]:
    """Trh COT pro indikátor: ručně zadaný kód (s automatickým obrácením podle symbolu), jinak podle symbolu.

    Parametry:
        manual_code: kód trhu CFTC z parametru indikátoru (prázdný = automaticky).
        symbol, kind: symbol grafu a druh instrumentu.
    Vrací None, když symbol trh nemá nebo je ruční kód neplatný.
    """
    code = manual_code.strip().upper()
    if not code:
        return market_for_symbol(symbol, kind)
    if not MARKET_CODE_RE.match(code):
        return None
    auto = market_for_symbol(symbol, kind)
    # Ruční kód stejného trhu si ponechá obrácení odvozené ze symbolu (USDJPY)
    return auto if auto and auto.code == code else CotMarket(code)


# ---------- čas zveřejnění ----------


@lru_cache(maxsize=None)
def us_federal_holidays(year: int) -> frozenset[date]:
    """Federální svátky USA (dny volna úřadů včetně náhradních), kdy CFTC nepracuje –
    od začátku roku do poloviny ledna dalšího roku (týden reportu přes Nový rok)."""
    days = USFederalHolidayCalendar().holidays(start=date(year, 1, 1), end=date(year + 1, 1, 15))
    return frozenset(day.date() for day in days)


def _business_day(day: date, holidays: frozenset[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


@lru_cache(maxsize=None)
def release_day(report_date: date) -> date:
    """Den zveřejnění reportu: pátek týdne reportu; každý svátek od úterý do
    pátku toho týdne ho posune o další pracovní den (Díkůvzdání → pondělí).
    Svátek v pondělí zveřejnění neposouvá (podle harmonogramu CFTC)."""
    monday = report_date - timedelta(days=report_date.weekday())
    holidays = us_federal_holidays(monday.year)
    day = monday + timedelta(days=4)
    for offset in range(1, 5):
        if monday + timedelta(days=offset) not in holidays:
            continue
        # Svátek v týdnu reportu: o pracovní den později (přes víkend a další svátky)
        day += timedelta(days=1)
        while not _business_day(day, holidays):
            day += timedelta(days=1)
    return day


def release_time_ms(report_date: date) -> int:
    """Okamžik zveřejnění reportu (ms UTC): den zveřejnění v 15:30 New York."""
    local = datetime.combine(release_day(report_date), RELEASE_TIME, tzinfo=RELEASE_TIMEZONE)
    return int(local.timestamp() * 1000)


def needs_refresh(last_report: Optional[date], fetched_at_ms: int, now_ms: int) -> bool:
    """Mají se data trhu znovu stáhnout z API?

    Ano, když chybí, když už měl vyjít další report (a od posledního stažení
    uplynulo aspoň REFRESH_RETRY_MS), nebo jsou starší než REFRESH_MAX_AGE_MS.

    Parametry:
        last_report: datum posledního reportu v datech (None = žádná data).
        fetched_at_ms: kdy se data naposledy stahovala (ms UTC).
        now_ms: aktuální čas (ms UTC).
    """
    if last_report is None:
        return True
    age = now_ms - fetched_at_ms
    if age >= REFRESH_MAX_AGE_MS:
        return True
    next_release = release_time_ms(last_report + timedelta(days=7))
    return now_ms >= next_release and age >= REFRESH_RETRY_MS


# ---------- data ----------


def _number(value) -> float:
    """Číslo z odpovědi API (texty "12345"); chybějící hodnota = NaN."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def parse_rows(rows: list[dict]) -> pd.DataFrame:
    """Řádky API → DataFrame reportů vzestupně podle data.

    Sloupce: report (datum reportu), report_ms (půlnoc dne reportu UTC),
    release_ms (okamžik zveřejnění), market (název trhu), oi (open interest)
    a pro každou skupinu <skupina>_long, _short, _net, _change (týdenní změna net).
    Řádek bez platného data se vynechá, duplicitní datum platí poslední.
    """
    records = []
    for row in rows:
        try:
            report = datetime.fromisoformat(str(row["report_date_as_yyyy_mm_dd"])[:10]).date()
        except (KeyError, ValueError):
            continue
        record = {
            "report": report,
            "report_ms": int(datetime.combine(report, time(), tzinfo=timezone.utc).timestamp() * 1000),
            "release_ms": release_time_ms(report),
            "market": str(row.get("contract_market_name") or "").strip(),
            "oi": _number(row.get("open_interest_all")),
        }
        for key, (_, long_col, short_col, change_long_col, change_short_col) in GROUPS.items():
            long, short = _number(row.get(long_col)), _number(row.get(short_col))
            record[f"{key}_long"] = long
            record[f"{key}_short"] = short
            record[f"{key}_net"] = long - short
            record[f"{key}_change"] = _number(row.get(change_long_col)) - _number(row.get(change_short_col))
        records.append(record)
    columns = ["report", "report_ms", "release_ms", "market", "oi"] + [
        f"{key}_{part}" for key in GROUPS for part in ("long", "short", "net", "change")
    ]
    frame = pd.DataFrame(records, columns=columns)
    if frame.empty:
        return frame
    return frame.sort_values("report").drop_duplicates("report", keep="last").reset_index(drop=True)


def cot_index(net: pd.Series, lookback: int) -> pd.Series:
    """COT Index (Larry Williams): poloha čisté pozice v rozsahu posledních N reportů, 0–100.

    Parametry:
        net: čisté pozice skupiny po reportech (vzestupně).
        lookback: počet reportů (týdnů) okna.
    Okno s nulovým rozsahem (všechny hodnoty stejné) dá 50.
    """
    low = net.rolling(lookback, min_periods=lookback).min()
    high = net.rolling(lookback, min_periods=lookback).max()
    span = high - low
    index = (net - low) / span.where(span != 0) * 100
    return index.where(span != 0, 50.0).where(span.notna())
