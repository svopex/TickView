"""Logická vrstva: SQLite cache svíček a nastavení, skládání historie.

Blokující operace sqlite3 běží přes asyncio.to_thread, aby nikdy nezdržely UI.
"""

import asyncio
import json
import sqlite3
from typing import Optional

from tickview.providers.base import Candle, DataProvider

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
"""

# Migrace pro starší databáze: sloupec color v tabulce drawings (přidaný dodatečně,
# CREATE TABLE IF NOT EXISTS ho do existující tabulky nepřidá)
_COLOR_MIGRATION = """
ALTER TABLE drawings ADD COLUMN color TEXT;
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

    def _migrate_sync(self) -> None:
        """Spustí migrace, které musí odolat už existujícímu schématu."""
        try:
            self._conn.executescript(_COLOR_MIGRATION)
        except sqlite3.OperationalError:
            # Sloupec color už existuje – migrace se přeskočí
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

    async def coverage(
        self, source: str, symbol: str, timeframe: str
    ) -> Optional[tuple[int, int]]:
        """Vrátí (min, max) open_time uložené v cache, nebo None, když je prázdná."""
        async with self._lock:
            row = await asyncio.to_thread(
                self._coverage_sync, source, symbol, timeframe
            )
        if row is None or row[0] is None:
            return None
        return (row[0], row[1])

    def _coverage_sync(self, source: str, symbol: str, timeframe: str):
        cur = self._conn.execute(
            """
            SELECT MIN(open_time), MAX(open_time)
            FROM klines
            WHERE source = ? AND symbol = ? AND timeframe = ?
            """,
            (source, symbol, timeframe),
        )
        return cur.fetchone()

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

    # ---------- kreslení (krok 5) ----------

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
            }
            for row in rows
        ]

    def _load_drawings_sync(self, source: str, symbol: str, timeframe: str):
        cur = self._conn.execute(
            """
            SELECT id, kind, points, color FROM drawings
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
    ) -> None:
        """Uloží jednu kresbu (typ + body + barva) k symbolu a timeframe."""
        async with self._lock:
            await asyncio.to_thread(
                self._add_drawing_sync, source, symbol, timeframe, kind, points, color
            )

    def _add_drawing_sync(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        kind: str,
        points: list[dict],
        color: Optional[str],
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO drawings (source, symbol, timeframe, kind, points, color)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source, symbol, timeframe, kind, json.dumps(points), color),
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

    async def clear_drawings(self, source: str, symbol: str, timeframe: str) -> None:
        """Smaže všechny kresby daného symbolu a timeframe."""
        async with self._lock:
            await asyncio.to_thread(self._clear_drawings_sync, source, symbol, timeframe)

    def _clear_drawings_sync(self, source: str, symbol: str, timeframe: str) -> None:
        self._conn.execute(
            "DELETE FROM drawings WHERE source = ? AND symbol = ? AND timeframe = ?",
            (source, symbol, timeframe),
        )
        self._conn.commit()

    # ---------- watchlist (krok 6) ----------

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


async def fetch_history(
    provider: DataProvider,
    cache: KlineCache,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> list[Candle]:
    """Vrátí svíčky pro zobrazení; ze sítě stáhne jen úseky chybějící v cache.

    Uvnitř pokryté oblasti předpokládáme souvislá data (klines z burzy bez děr);
    chybějící okraje doleieme od zdroje a uložíme.
    """
    coverage = await cache.coverage(provider.id, symbol, timeframe)
    missing: list[tuple[int, int]] = []
    if coverage is None:
        missing.append((start_ms, end_ms))
    else:
        cov_start, cov_end = coverage
        if start_ms < cov_start:
            missing.append((start_ms, min(cov_start - 1, end_ms)))
        if end_ms > cov_end:
            missing.append((max(cov_end + 1, start_ms), end_ms))
    for missing_start, missing_end in missing:
        if missing_end < missing_start:
            continue
        candles = await provider.history(symbol, timeframe, missing_start, missing_end)
        await cache.store(provider.id, symbol, timeframe, candles)
    return await cache.load(provider.id, symbol, timeframe, start_ms, end_ms)
