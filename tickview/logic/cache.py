"""Logická vrstva: SQLite cache svíček a nastavení, skládání historie.

Blokující operace sqlite3 běží přes asyncio.to_thread, aby nikdy nezdržely UI.
"""

import asyncio
import json
import sqlite3
from typing import Optional

from tickview.providers.base import TIMEFRAME_MS, Candle, DataProvider, is_closed, utc_now_ms

_SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    source     TEXT    NOT NULL,
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    open_time  INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    PRIMARY KEY (source, symbol, timeframe, open_time)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drawings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT    NOT NULL,
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    points     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drawings
    ON drawings (source, symbol, timeframe);
CREATE TABLE IF NOT EXISTS watchlist (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source  TEXT    NOT NULL,
    symbol  TEXT    NOT NULL,
    added_at TEXT   NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, symbol)
);
-- Intervaly (UTC ms), které už byly stažené ze sítě a jsou v cache kompletní;
-- mezery mezi svíčkami uvnitř nich jsou skutečné (noc, víkend), ne díry
CREATE TABLE IF NOT EXISTS fetched_ranges (
    source     TEXT    NOT NULL,
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    start_ms   INTEGER NOT NULL,
    end_ms     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetched_ranges
    ON fetched_ranges (source, symbol, timeframe);
"""

# Migrace pro starší databáze: sloupce color a text v tabulce drawings (přidané
# dodatečně, CREATE TABLE IF NOT EXISTS je do existující tabulky nepřidá)
_MIGRATIONS = """
ALTER TABLE drawings ADD COLUMN color TEXT;
ALTER TABLE drawings ADD COLUMN text TEXT;
"""


class KlineCache:
    """Ukládá svíčky a nastavení do SQLite; operace jsou serializované zámkem."""

    def __init__(self, db_path):
        self._db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Otevře databázi a vytvoří schéma (volá se při startu aplikace)."""
        async with self._lock:
            # check_same_thread=False: operace posíláme do to_thread, vždy pod zámkem
            self._conn = await asyncio.to_thread(
                sqlite3.connect, self._db_path, check_same_thread=False
            )
            await asyncio.to_thread(self._conn.executescript, _SCHEMA)
            await asyncio.to_thread(self._migrate_sync)

    async def close(self) -> None:
        """Zavře databázi (při ukončení aplikace)."""
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    def _migrate_sync(self) -> None:
        """Spustí migrace, které musí odolat už existujícímu schématu."""
        # Každý ALTER zvlášť – už existující sloupec nesmí přeskočit zbytek
        for statement in _MIGRATIONS.strip().split(";"):
            if not statement.strip():
                continue
            try:
                self._conn.execute(statement)
            except sqlite3.OperationalError:
                # Sloupce už existují – migrace se přeskočí
                pass

    async def load(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
    ) -> list[Candle]:
        """Vrátí svíčky z cache vzestupně podle času, volitelně omezené intervalem."""
        async with self._lock:
            rows = await asyncio.to_thread(
                self._load_sync, source, symbol, timeframe, start_ms, end_ms
            )
        return [Candle(*row) for row in rows]

    def _load_sync(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        start_ms: Optional[int],
        end_ms: Optional[int],
    ) -> list[tuple]:
        cur = self._conn.execute(
            """
            SELECT open_time, open, high, low, close, volume
            FROM klines
            WHERE source = ? AND symbol = ? AND timeframe = ?
              AND (? IS NULL OR open_time >= ?)
              AND (? IS NULL OR open_time <= ?)
            ORDER BY open_time
            """,
            (source, symbol, timeframe, start_ms, start_ms, end_ms, end_ms),
        )
        return cur.fetchall()

    async def store(self, source: str, symbol: str, timeframe: str, candles: list[Candle]) -> None:
        """Uloží svíčky do cache (přepíše duplikáty se stejným open_time)."""
        if not candles:
            return
        async with self._lock:
            await asyncio.to_thread(
                self._store_sync, source, symbol, timeframe, candles
            )

    def _store_sync(
        self, source: str, symbol: str, timeframe: str, candles: list[Candle]
    ) -> None:
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO klines
              (source, symbol, timeframe, open_time, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    source,
                    symbol,
                    timeframe,
                    c.open_time,
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.volume,
                )
                for c in candles
            ],
        )
        self._conn.commit()

    async def fetched_ranges(
        self, source: str, symbol: str, timeframe: str
    ) -> list[tuple[int, int]]:
        """Vrátí stažené (kompletní) intervaly seřazené podle začátku."""
        async with self._lock:
            return await asyncio.to_thread(
                self._fetched_ranges_sync, source, symbol, timeframe
            )

    def _fetched_ranges_sync(self, source: str, symbol: str, timeframe: str) -> list[tuple[int, int]]:
        cur = self._conn.execute(
            """
            SELECT start_ms, end_ms FROM fetched_ranges
            WHERE source = ? AND symbol = ? AND timeframe = ?
            ORDER BY start_ms
            """,
            (source, symbol, timeframe),
        )
        return [(row[0], row[1]) for row in cur.fetchall()]

    async def add_fetched_range(
        self, source: str, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> None:
        """Zaznamená stažený interval; překrývající se a navazující intervaly sloučí."""
        async with self._lock:
            await asyncio.to_thread(
                self._add_fetched_range_sync, source, symbol, timeframe, start_ms, end_ms
            )

    def _add_fetched_range_sync(
        self, source: str, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> None:
        # Sloučení v Pythonu: intervaly seřadíme a spojíme ty, které se
        # překrývají nebo na sebe přímo navazují (konec + 1 ms = začátek)
        ranges = sorted(self._fetched_ranges_sync(source, symbol, timeframe) + [(start_ms, end_ms)])
        merged: list[list[int]] = []
        for range_start, range_end in ranges:
            if merged and range_start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], range_end)
            else:
                merged.append([range_start, range_end])
        # Výměna intervalů klíče v jedné transakci
        with self._conn:
            self._conn.execute(
                "DELETE FROM fetched_ranges WHERE source = ? AND symbol = ? AND timeframe = ?",
                (source, symbol, timeframe),
            )
            self._conn.executemany(
                """
                INSERT INTO fetched_ranges (source, symbol, timeframe, start_ms, end_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(source, symbol, timeframe, a, b) for a, b in merged],
            )

    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Vrátí uložené nastavení podle klíče."""
        async with self._lock:
            row = await asyncio.to_thread(self._get_setting_sync, key)
        return row[0] if row else default

    def _get_setting_sync(self, key: str):
        cur = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        return cur.fetchone()

    async def set_setting(self, key: str, value: str) -> None:
        """Uloží nastavení (key/value) do SQLite."""
        async with self._lock:
            await asyncio.to_thread(self._set_setting_sync, key, value)

    def _set_setting_sync(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        self._conn.commit()

    # ---------- kreslení ----------

    async def load_drawings(self, source: str, symbol: str, timeframe: str) -> list[dict]:
        """Vrátí kresby uložené k symbolu a timeframe, seřazené podle id."""
        async with self._lock:
            rows = await asyncio.to_thread(
                self._load_drawings_sync, source, symbol, timeframe
            )
        # body kresby jsou uložené jako JSON (čas v sekundách UTC + cena)
        return [
            {
                "id": row[0],
                "kind": row[1],
                "points": json.loads(row[2]),
                "color": row[3],
                "text": row[4],
            }
            for row in rows
        ]

    def _load_drawings_sync(self, source: str, symbol: str, timeframe: str):
        cur = self._conn.execute(
            """
            SELECT id, kind, points, color, text FROM drawings
            WHERE source = ? AND symbol = ? AND timeframe = ?
            ORDER BY id
            """,
            (source, symbol, timeframe),
        )
        return cur.fetchall()

    async def add_drawing(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        kind: str,
        points: list[dict],
        color: Optional[str] = None,
        text: Optional[str] = None,
    ) -> int:
        """Uloží jednu kresbu (typ + body + barva + volitelný text); vrátí její id."""
        async with self._lock:
            return await asyncio.to_thread(
                self._add_drawing_sync, source, symbol, timeframe, kind, points, color, text
            )

    def _add_drawing_sync(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        kind: str,
        points: list[dict],
        color: Optional[str],
        text: Optional[str],
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO drawings (source, symbol, timeframe, kind, points, color, text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source, symbol, timeframe, kind, json.dumps(points), color, text),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    async def update_drawing(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        drawing_id: int,
        points: list[dict],
        text: Optional[str] = None,
    ) -> None:
        """Aktualizuje body (a volitelně text) existující kresby po jejím tažení."""
        async with self._lock:
            await asyncio.to_thread(
                self._update_drawing_sync, source, symbol, timeframe, drawing_id, points, text
            )

    def _update_drawing_sync(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        drawing_id: int,
        points: list[dict],
        text: Optional[str],
    ) -> None:
        self._conn.execute(
            """
            UPDATE drawings SET points = ?, text = ?
            WHERE id = ? AND source = ? AND symbol = ? AND timeframe = ?
            """,
            (json.dumps(points), text, drawing_id, source, symbol, timeframe),
        )
        self._conn.commit()

    async def delete_drawing(self, source: str, symbol: str, timeframe: str, drawing_id: int) -> None:
        """Smaže jednu kresbu podle id (používá se pro mazání pravým klikem)."""
        async with self._lock:
            await asyncio.to_thread(
                self._delete_drawing_sync, source, symbol, timeframe, drawing_id
            )

    def _delete_drawing_sync(
        self, source: str, symbol: str, timeframe: str, drawing_id: int
    ) -> None:
        self._conn.execute(
            """
            DELETE FROM drawings
            WHERE id = ? AND source = ? AND symbol = ? AND timeframe = ?
            """,
            (drawing_id, source, symbol, timeframe),
        )
        self._conn.commit()

    # ---------- watchlist ----------

    async def watchlist_list(self) -> list[dict]:
        """Vrátí sledované symboly v pořadí přidání."""
        async with self._lock:
            rows = await asyncio.to_thread(self._watchlist_list_sync)
        return [{"source": row[0], "symbol": row[1]} for row in rows]

    def _watchlist_list_sync(self):
        cur = self._conn.execute(
            "SELECT source, symbol FROM watchlist ORDER BY id"
        )
        return cur.fetchall()

    async def watchlist_add(self, source: str, symbol: str) -> None:
        """Přidá symbol do watchlistu (duplicita se ignoruje)."""
        async with self._lock:
            await asyncio.to_thread(self._watchlist_add_sync, source, symbol)

    def _watchlist_add_sync(self, source: str, symbol: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO watchlist (source, symbol) VALUES (?, ?)",
            (source, symbol),
        )
        self._conn.commit()

    async def watchlist_remove(self, source: str, symbol: str) -> None:
        """Odebere symbol z watchlistu."""
        async with self._lock:
            await asyncio.to_thread(self._watchlist_remove_sync, source, symbol)

    def _watchlist_remove_sync(self, source: str, symbol: str) -> None:
        self._conn.execute(
            "DELETE FROM watchlist WHERE source = ? AND symbol = ?",
            (source, symbol),
        )
        self._conn.commit()


def uncovered_ranges(
    start_ms: int, end_ms: int, ranges: list[tuple[int, int]], step_ms: int
) -> list[tuple[int, int]]:
    """Vrátí části intervalu [start_ms, end_ms], které nepokrývají stažené intervaly.

    Mezeru na levém okraji kratší než jeden bar vynecháme: začátek požadavku
    obvykle neleží přesně na otevření svíčky a stahovali bychom prázdný proužek
    při každém načtení (nanejvýš jedna svíčka na okraji se doplní při posunu doleva).

    Parametry:
        start_ms, end_ms: požadovaný interval (UTC ms, včetně).
        ranges: stažené intervaly seřazené podle začátku.
        step_ms: délka baru timeframu v ms.
    """
    gaps: list[tuple[int, int]] = []
    cursor = start_ms
    for range_start, range_end in ranges:
        if range_end < cursor:
            continue
        if range_start > end_ms:
            break
        if range_start > cursor:
            gaps.append((cursor, range_start - 1))
        cursor = max(cursor, range_end + 1)
    if cursor <= end_ms:
        gaps.append((cursor, end_ms))
    if gaps and gaps[0][0] == start_ms and gaps[0][1] - gaps[0][0] + 1 < step_ms and gaps[0][1] < end_ms:
        gaps.pop(0)
    return gaps


async def fetch_history(
    provider: DataProvider,
    cache: KlineCache,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> list[Candle]:
    """Vrátí svíčky pro zobrazení; ze sítě stáhne jen úseky chybějící v cache.

    Chybějící úseky = části požadovaného intervalu mimo už stažené intervaly
    (tabulka fetched_ranges), takže se doplní i díry uprostřed cache (výpadek
    streamu, starší verze aplikace). Do cache ukládáme jen uzavřené svíčky –
    rozpracovaná (poslední) svíčka by v ní jinak zůstala v neúplné podobě.
    Za stažený se interval zaznamená jen tehdy, když zdroj něco vrátil
    (prázdná odpověď může být i chyba zdroje, např. yfinance chyby polyká),
    a bez úseku před současností, ve kterém zpožděný zdroj ještě nemusí mít
    všechna data (provider.data_delay_ms).
    """
    step_ms = TIMEFRAME_MS[timeframe]
    now_ms = utc_now_ms()
    ranges = await cache.fetched_ranges(provider.id, symbol, timeframe)
    verify_limit = now_ms - step_ms - provider.data_delay_ms
    # Rozpracované svíčky ze sítě (open_time -> svíčka) – jen do výsledku, ne do cache
    open_candles: dict[int, Candle] = {}
    for missing_start, missing_end in uncovered_ranges(start_ms, end_ms, ranges, step_ms):
        candles = await provider.history(symbol, timeframe, missing_start, missing_end)
        closed = [c for c in candles if is_closed(c, step_ms, now_ms)]
        open_candles.update(
            (c.open_time, c) for c in candles if not is_closed(c, step_ms, now_ms)
        )
        await cache.store(provider.id, symbol, timeframe, closed)
        verified_end = min(missing_end, verify_limit)
        if candles and verified_end >= missing_start:
            await cache.add_fetched_range(provider.id, symbol, timeframe, missing_start, verified_end)
    result = await cache.load(provider.id, symbol, timeframe, start_ms, end_ms)
    if open_candles:
        # Rozpracované svíčky z požadovaného intervalu doplníme do výsledku
        # (přepíší případný starší záznam z cache) a znovu seřadíme podle času
        merged = {c.open_time: c for c in result}
        merged.update(
            (open_time, c) for open_time, c in open_candles.items()
            if start_ms <= open_time <= end_ms
        )
        result = [merged[open_time] for open_time in sorted(merged)]
    return result
