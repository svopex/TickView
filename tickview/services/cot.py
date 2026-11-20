"""Stahování COT reportu z API CFTC s uložením do SQLite.

Data trhu se drží v paměti (DataFrame) a v tabulce cot_reports; z API se
stáhnou celá znovu, jen když už měl vyjít další report (logic/cot.needs_refresh).
Po chybě API se další pokus o stejný trh odloží, aby výpočet indikátorů
(živý přepočet každé 2 s) API nezahltil.
"""

import asyncio
import logging
from typing import Optional

import httpx
import pandas as pd

from tickview.config import load_config
from tickview.i18n import tr
from tickview.logic.cot import API_FIELDS, API_URL, MARKET_CODE_RE, needs_refresh, parse_rows
from tickview.providers.base import utc_now_ms

logger = logging.getLogger(__name__)

# Časový limit dotazu na API a horní mez počtu řádků (Legacy od roku 1986 ≈ 2 100 týdnů)
REQUEST_TIMEOUT_S = 30.0
ROW_LIMIT = 10000
# Za jak dlouho se po chybě API zkusí trh stáhnout znovu (ms)
FAILURE_RETRY_MS = 10 * 60 * 1000


class CotService:
    """COT report jednotlivých trhů: paměť → SQLite → API CFTC.

    Parametry:
        cache: KlineCache (metody load_cot / save_cot).
    """

    def __init__(self, cache) -> None:
        self.cache = cache
        # Kód trhu -> (reporty, čas stažení v ms); čas poslední chyby API; zámky trhů
        self._frames: dict[str, tuple[pd.DataFrame, int]] = {}
        self._failed_at: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Trhy, které se už zkusily načíst z databáze
        self._db_checked: set[str] = set()

    def cached(self, code: str) -> Optional[pd.DataFrame]:
        """Reporty trhu v paměti (bez čekání), nebo None."""
        entry = self._frames.get(code)
        return entry[0] if entry else None

    def wants_load(self, code: str) -> bool:
        """Je potřeba trh načíst (z databáze nebo z API)? False i během odkladu po chybě."""
        if code not in self._db_checked:
            return True
        now_ms = utc_now_ms()
        if now_ms - self._failed_at.get(code, -FAILURE_RETRY_MS) < FAILURE_RETRY_MS:
            return False
        frame, fetched_at = self._frames.get(code, (None, 0))
        last = frame["report"].iloc[-1] if frame is not None and not frame.empty else None
        return needs_refresh(last, fetched_at, now_ms)

    async def get(self, code: str) -> Optional[pd.DataFrame]:
        """Reporty trhu: z paměti, jinak z databáze, a když jsou zastaralé, z API.

        Chyba API se zapíše do logu a vrátí se dosavadní data (nebo None).
        """
        lock = self._locks.setdefault(code, asyncio.Lock())
        async with lock:
            # Souběžné požadavky na stejný trh: druhý už najde hotová data
            if code not in self._db_checked:
                self._db_checked.add(code)
                stored = await self.cache.load_cot(code)
                if stored is not None:
                    self._frames[code] = (await asyncio.to_thread(parse_rows, stored[1]), stored[0])
            if self.wants_load(code):
                await self._fetch(code)
        return self.cached(code)

    async def _fetch(self, code: str) -> None:
        """Stáhne celou historii trhu z API CFTC a uloží ji (chyba = log a odklad)."""
        now = utc_now_ms()
        try:
            rows = await fetch_market_rows(code)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("COT report trhu %s nelze stáhnout: %s", code, exc)
            self._failed_at[code] = now
            return
        self._failed_at.pop(code, None)
        # Rozbor řádků celé historie ve vlákně (smyčka událostí nečeká)
        self._frames[code] = (await asyncio.to_thread(parse_rows, rows), now)
        await self.cache.save_cot(code, now, rows)


async def fetch_market_rows(code: str) -> list[dict]:
    """Řádky Legacy reportu (jen futures) trhu z API CFTC vzestupně podle data.

    Volitelný app token z config.yaml (cot.app_token) zvedá limit dotazů API.
    Kód trhu musí projít MARKET_CODE_RE (vkládá se do podmínky dotazu).
    """
    if not MARKET_CODE_RE.match(code):
        raise ValueError(tr("neplatný kód trhu {code}").format(code=repr(code)))
    token = str((load_config().get("cot") or {}).get("app_token") or "").strip()
    headers = {"X-App-Token": token} if token else {}
    # Dotaz SoQL: vybrané sloupce jednoho trhu seřazené podle data reportu
    params = {
        "$select": ",".join(API_FIELDS),
        "$where": f"cftc_contract_market_code='{code}'",
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": str(ROW_LIMIT),
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        response = await client.get(API_URL, params=params, headers=headers)
        response.raise_for_status()
        rows = response.json()
    if not isinstance(rows, list):
        raise ValueError(tr("neočekávaná odpověď API"))
    return [row for row in rows if isinstance(row, dict)]
