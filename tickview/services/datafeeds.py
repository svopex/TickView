"""Externí data pro indikátory (Context.data) – zatím COT report CFTC.

Indikátor řekne v data_requirements, jaká data potřebuje (klíč "cot:<kód trhu>");
tento modul je načte a připojí ke Context. Data v paměti se použijí hned
a zastaralá se obnovují na pozadí; čeká se jen na data, která ještě nejsou
(scanner, alerty, replay), graf nečeká vůbec a po načtení se přepočítá.
Služby se nastaví při startu aplikace (configure); bez nich jsou data prázdná.
"""

import asyncio
import logging
from dataclasses import replace
from typing import Awaitable, Callable, Iterable, Optional

from tickview.indicators.api import Context, Indicator
from tickview.indicators.compute import context_for
from tickview.logic.cot import COT_KEY_PREFIX
from tickview.services.cot import CotService

logger = logging.getLogger(__name__)

_cot: Optional[CotService] = None
# Klíče, které se právě načítají na pozadí (klíč -> úloha), a běžící úlohy
# (odkaz drží úlohu naživu až do konce)
_loading: dict[str, asyncio.Task] = {}


def configure(cache) -> None:
    """Nastaví zdroje externích dat nad databází aplikace (volá se při startu)."""
    global _cot
    _cot = CotService(cache)
    _loading.clear()


def requirements(pairs: Iterable[tuple[Indicator, dict]], ctx: Context) -> list[str]:
    """Klíče dat, která potřebují indikátory [(indikátor, parametry)] (bez duplicit).

    Chyba v data_requirements jednoho indikátoru se zapíše do logu a nic dalšího neovlivní.
    """
    keys: list[str] = []
    for indicator, params in pairs:
        if indicator is None:
            continue
        try:
            wanted = indicator.data_requirements(params, ctx)
        except Exception:
            logger.exception("Požadavek na data indikátoru %s selhal", indicator.id)
            continue
        keys.extend(key for key in wanted if isinstance(key, str) and key not in keys)
    return keys


def _cot_code(key: str) -> Optional[str]:
    """Kód trhu z klíče "cot:<kód>" (jiný klíč nebo bez služby = None)."""
    return key[len(COT_KEY_PREFIX):] if _cot is not None and key.startswith(COT_KEY_PREFIX) else None


def cached(keys: Iterable[str]) -> dict:
    """Data klíčů, která jsou v paměti (bez čekání)."""
    data = {}
    for key in keys:
        code = _cot_code(key)
        frame = _cot.cached(code) if code else None
        if frame is not None:
            data[key] = frame
    return data


def pending(keys: Iterable[str]) -> list[str]:
    """Klíče, jejichž data je potřeba načíst nebo obnovit (mimo odklad po chybě)."""
    return [key for key in keys if (code := _cot_code(key)) and _cot.wants_load(code)]


async def load(keys: Iterable[str]) -> dict:
    """Načte data klíčů (paměť → databáze → API); nedostupná data ve výsledku chybí."""
    data = {}
    for key in keys:
        code = _cot_code(key)
        if not code:
            continue
        try:
            frame = await _cot.get(code)
        except Exception:
            # Chyba databáze nebo zpracování nesmí shodit výpočet indikátorů
            logger.exception("Data %s nelze načíst", key)
            continue
        if frame is not None:
            data[key] = frame
    return data


def load_in_background(keys: Iterable[str], on_done: Optional[Callable[[], Awaitable[None]]] = None) -> None:
    """Načte klíče na pozadí (už načítané klíče se přeskočí) a pak zavolá on_done.

    Parametry:
        keys: klíče k načtení nebo obnovení.
        on_done: async callback po načtení (např. přepočet indikátorů grafu).
    """
    new = [key for key in keys if key not in _loading]
    if not new:
        return

    async def run() -> None:
        try:
            await load(new)
            if on_done is not None:
                await on_done()
        finally:
            for key in new:
                _loading.pop(key, None)

    task = asyncio.create_task(run())
    for key in new:
        _loading[key] = task


async def attach(ctx: Context, pairs: Iterable[tuple[Indicator, dict]]) -> Context:
    """Context s daty indikátorů: chybějící data počká, zastaralá obnoví na pozadí."""
    keys = requirements(pairs, ctx)
    if not keys:
        return ctx
    data = cached(keys)
    missing = [key for key in keys if key not in data]
    if missing:
        data.update(await load(missing))
    load_in_background([key for key in pending(keys) if key not in missing])
    return replace(ctx, data=data)


def attach_cached(ctx: Context, pairs: Iterable[tuple[Indicator, dict]]) -> tuple[Context, list[str]]:
    """Context s daty z paměti a klíče, které je potřeba dočíst nebo obnovit (graf nečeká)."""
    keys = requirements(pairs, ctx)
    if not keys:
        return ctx, []
    return replace(ctx, data=cached(keys)), pending(keys)


async def context_with_data(provider, symbol: str, timeframe: str, candles, pairs: list[tuple[Indicator, dict]],
                            **options) -> Context:
    """Context výpočtu (context_for) s externími daty indikátorů – scanner, alerty, replay.

    Parametry:
        provider, symbol, timeframe, candles: viz context_for.
        pairs: indikátory s parametry, pro které se data připojí.
        options: live, signal_bars (viz context_for).
    """
    return await attach(context_for(provider, symbol, timeframe, candles, **options), pairs)
