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
-- Pojmenované seznamy symbolů (více watchlistů jako v TradingView)
CREATE TABLE IF NOT EXISTS watchlists (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT    NOT NULL
);
-- Symboly seznamů; stejný symbol může být ve více seznamech
CREATE TABLE IF NOT EXISTS watchlist_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id  INTEGER NOT NULL,
    source   TEXT    NOT NULL,
    symbol   TEXT    NOT NULL,
    UNIQUE (list_id, source, symbol)
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
-- Alerty (jako v TradingView): stav ve sloupcích, definice (podmínka, cíl,
-- trigger, expirace, zpráva, notifikace) jako JSON ve sloupci spec
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    timeframe     TEXT    NOT NULL,
    spec          TEXT    NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    status        TEXT    NOT NULL DEFAULT 'active',
    created_at    INTEGER NOT NULL,
    last_fired_at INTEGER,
    fire_count    INTEGER NOT NULL DEFAULT 0
);
-- Historie spuštěných alertů (log) včetně výsledku odeslání notifikací
CREATE TABLE IF NOT EXISTS alert_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   INTEGER NOT NULL,
    fired_at   INTEGER NOT NULL,
    source     TEXT    NOT NULL,
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    price      REAL    NOT NULL,
    message    TEXT    NOT NULL,
    delivery   TEXT
);
"""

# Název seznamu, který vznikne při prvním spuštění (a do kterého se převede
# původní jediný watchlist)
DEFAULT_WATCHLIST_NAME = "Watchlist"

# Migrace pro starší databáze: sloupce color, text a options (JSON se šířkou
# a stylem čáry) v tabulce drawings – přidané dodatečně, CREATE TABLE IF NOT
# EXISTS je do existující tabulky nepřidá
_MIGRATIONS = """
ALTER TABLE drawings ADD COLUMN color TEXT;
ALTER TABLE drawings ADD COLUMN text TEXT;
ALTER TABLE drawings ADD COLUMN options TEXT;
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
        # Bez seznamů (první spuštění nebo databáze s jediným watchlistem):
        # vytvoříme výchozí seznam a převedeme do něj symboly původní tabulky
        if self._conn.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0] == 0:
            cur = self._conn.execute("INSERT INTO watchlists (name) VALUES (?)", (DEFAULT_WATCHLIST_NAME,))
            self._conn.execute(
                "INSERT OR IGNORE INTO watchlist_items (list_id, source, symbol) "
                "SELECT ?, source, symbol FROM watchlist ORDER BY id",
                (cur.lastrowid,),
            )
        self._conn.commit()

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

    async def get_settings_prefix(self, prefix: str) -> dict[str, str]:
        """Vrátí všechna nastavení, jejichž klíč začíná prefixem (jedním dotazem)."""
        async with self._lock:
            rows = await asyncio.to_thread(self._get_settings_prefix_sync, prefix)
        return dict(rows)

    def _get_settings_prefix_sync(self, prefix: str):
        # substr místo LIKE – prefix může obsahovat znaky _ a %, které LIKE bere jako zástupné
        return self._conn.execute(
            "SELECT key, value FROM settings WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)
        ).fetchall()

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

    # Kresby patří k symbolu (jako v TradingView) a jsou vidět na všech
    # timeframech podle své viditelnosti; sloupec timeframe drží jen
    # timeframe, na kterém kresba vznikla

    async def load_drawings(self, source: str, symbol: str) -> list[dict]:
        """Vrátí kresby uložené k symbolu (všechny timeframy), seřazené podle id."""
        rows = await self._run(self._load_drawings_sync, source, symbol)
        # body kresby jsou uložené jako JSON (čas v sekundách UTC + cena)
        return [
            {
                "id": row[0],
                "kind": row[1],
                "points": json.loads(row[2]),
                "color": row[3],
                "text": row[4],
                # Vzhled kresby (šířka, styl čáry); starší kresby ho nemají
                "options": json.loads(row[5]) if row[5] else {},
            }
            for row in rows
        ]

    def _load_drawings_sync(self, source: str, symbol: str):
        cur = self._conn.execute(
            """
            SELECT id, kind, points, color, text, options FROM drawings
            WHERE source = ? AND symbol = ?
            ORDER BY id
            """,
            (source, symbol),
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
        options: Optional[dict] = None,
    ) -> int:
        """Uloží jednu kresbu (typ + body + barva + volitelný text a volby); vrátí její id.

        Parametry:
            timeframe: timeframe, na kterém kresba vznikla (zobrazení řídí viditelnost v options).
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._add_drawing_sync, source, symbol, timeframe, kind, points, color, text, options
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
        options: Optional[dict],
    ) -> int:
        # Volby (šířka, styl čáry, písmo, viditelnost) jako JSON; bez voleb NULL jako u starších kreseb
        cur = self._conn.execute(
            """
            INSERT INTO drawings (source, symbol, timeframe, kind, points, color, text, options)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source, symbol, timeframe, kind, json.dumps(points), color, text, json.dumps(options) if options else None),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    async def update_drawing(
        self,
        source: str,
        symbol: str,
        drawing_id: int,
        points: list[dict],
        text: Optional[str] = None,
    ) -> None:
        """Aktualizuje body (a volitelně text) existující kresby symbolu po jejím tažení."""
        await self._run(
            self._execute_sync,
            """
            UPDATE drawings SET points = ?, text = ?
            WHERE id = ? AND source = ? AND symbol = ?
            """,
            (json.dumps(points), text, drawing_id, source, symbol),
        )

    async def update_drawing_style(
        self,
        source: str,
        symbol: str,
        drawing_id: int,
        color: Optional[str],
        text: Optional[str],
        options: dict,
    ) -> None:
        """Uloží nastavení kresby z dialogu Settings (barva, text, styl čáry, písmo, viditelnost)."""
        await self._run(
            self._execute_sync,
            """
            UPDATE drawings SET color = ?, text = ?, options = ?
            WHERE id = ? AND source = ? AND symbol = ?
            """,
            (color, text, json.dumps(options), drawing_id, source, symbol),
        )

    async def delete_drawing(self, source: str, symbol: str, drawing_id: int) -> None:
        """Smaže jednu kresbu symbolu podle id (pravý klik, Del, koš)."""
        await self._run(
            self._execute_sync,
            "DELETE FROM drawings WHERE id = ? AND source = ? AND symbol = ?",
            (drawing_id, source, symbol),
        )

    async def get_drawing(self, drawing_id: int) -> Optional[dict]:
        """Vrátí kresbu podle id {"id", "source", "symbol", "timeframe", "kind", "points"}, nebo None."""
        row = await self._run(
            lambda: self._conn.execute(
                "SELECT id, source, symbol, timeframe, kind, points FROM drawings WHERE id = ?", (drawing_id,)
            ).fetchone()
        )
        if not row:
            return None
        return {
            "id": row[0],
            "source": row[1],
            "symbol": row[2],
            "timeframe": row[3],
            "kind": row[4],
            "points": json.loads(row[5]),
        }

    # ---------- watchlisty (pojmenované seznamy symbolů) ----------

    async def _run(self, func, *args):
        # Synchronní operace nad databází ve vlákně, pod zámkem
        async with self._lock:
            return await asyncio.to_thread(func, *args)

    async def watchlists(self) -> list[dict]:
        """Vrátí seznamy symbolů [{"id", "name", "count"}] v pořadí vytvoření."""
        rows = await self._run(self._watchlists_sync)
        return [{"id": row[0], "name": row[1], "count": row[2]} for row in rows]

    def _watchlists_sync(self):
        # Počet symbolů každého seznamu jedním dotazem (LEFT JOIN – i prázdné seznamy)
        return self._conn.execute(
            """
            SELECT w.id, w.name, COUNT(i.id)
            FROM watchlists w LEFT JOIN watchlist_items i ON i.list_id = w.id
            GROUP BY w.id ORDER BY w.id
            """
        ).fetchall()

    async def watchlist_create(self, name: str, copy_from: Optional[int] = None) -> int:
        """Vytvoří seznam; s copy_from do něj zkopíruje symboly jiného seznamu. Vrátí id."""
        return await self._run(self._watchlist_create_sync, name, copy_from)

    def _watchlist_create_sync(self, name: str, copy_from: Optional[int]) -> int:
        cur = self._conn.execute("INSERT INTO watchlists (name) VALUES (?)", (name,))
        list_id = int(cur.lastrowid)
        if copy_from is not None:
            # Kopie zachová pořadí symbolů původního seznamu
            self._conn.execute(
                "INSERT INTO watchlist_items (list_id, source, symbol) "
                "SELECT ?, source, symbol FROM watchlist_items WHERE list_id = ? ORDER BY id",
                (list_id, copy_from),
            )
        self._conn.commit()
        return list_id

    async def watchlist_rename(self, list_id: int, name: str) -> None:
        """Přejmenuje seznam."""
        await self._run(self._execute_sync, "UPDATE watchlists SET name = ? WHERE id = ?", (name, list_id))

    async def watchlist_clear(self, list_id: int) -> None:
        """Odebere ze seznamu všechny symboly."""
        await self._run(self._execute_sync, "DELETE FROM watchlist_items WHERE list_id = ?", (list_id,))

    async def watchlist_delete(self, list_id: int) -> None:
        """Smaže seznam i s jeho symboly."""
        await self._run(self._watchlist_delete_sync, list_id)

    def _watchlist_delete_sync(self, list_id: int) -> None:
        self._conn.execute("DELETE FROM watchlist_items WHERE list_id = ?", (list_id,))
        self._conn.execute("DELETE FROM watchlists WHERE id = ?", (list_id,))
        self._conn.commit()

    def _execute_sync(self, sql: str, params: tuple) -> None:
        # Jeden zápisový příkaz s potvrzením
        self._conn.execute(sql, params)
        self._conn.commit()

    async def watchlist_list(self, list_id: int) -> list[dict]:
        """Vrátí symboly seznamu v pořadí přidání."""
        rows = await self._run(self._watchlist_list_sync, list_id)
        return [{"source": row[0], "symbol": row[1]} for row in rows]

    def _watchlist_list_sync(self, list_id: int):
        return self._conn.execute(
            "SELECT source, symbol FROM watchlist_items WHERE list_id = ? ORDER BY id", (list_id,)
        ).fetchall()

    async def watchlist_add(self, list_id: int, source: str, symbol: str) -> None:
        """Přidá symbol do seznamu (duplicita se ignoruje)."""
        await self._run(
            self._execute_sync,
            "INSERT OR IGNORE INTO watchlist_items (list_id, source, symbol) VALUES (?, ?, ?)",
            (list_id, source, symbol),
        )

    async def watchlist_remove(self, list_id: int, source: str, symbol: str) -> None:
        """Odebere symbol ze seznamu."""
        await self._run(
            self._execute_sync,
            "DELETE FROM watchlist_items WHERE list_id = ? AND source = ? AND symbol = ?",
            (list_id, source, symbol),
        )

    # ---------- alerty ----------

    # Sloupce tabulky alerts v pořadí, v jakém je čte _alert_from_row
    _ALERT_COLUMNS = "id, source, symbol, timeframe, spec, active, status, created_at, last_fired_at, fire_count"

    @staticmethod
    def _alert_from_row(row) -> dict:
        # Řádek tabulky alerts → slovník alertu (definice ze sloupce spec + stav)
        alert = json.loads(row[4])
        alert.update(
            {
                "id": row[0],
                "source": row[1],
                "symbol": row[2],
                "timeframe": row[3],
                "active": bool(row[5]),
                "status": row[6],
                "created_at": row[7],
                "last_fired_at": row[8],
                "fire_count": row[9],
            }
        )
        return alert

    async def alerts(self, active_only: bool = False) -> list[dict]:
        """Vrátí alerty (nejnovější první); active_only = jen aktivní (pro vyhodnocení)."""
        where = "WHERE active = 1" if active_only else ""
        rows = await self._run(
            lambda: self._conn.execute(f"SELECT {self._ALERT_COLUMNS} FROM alerts {where} ORDER BY id DESC").fetchall()
        )
        return [self._alert_from_row(row) for row in rows]

    async def get_alert(self, alert_id: int) -> Optional[dict]:
        """Vrátí alert podle id, nebo None."""
        row = await self._run(
            lambda: self._conn.execute(f"SELECT {self._ALERT_COLUMNS} FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        )
        return self._alert_from_row(row) if row else None

    async def save_alert(self, spec: dict, alert_id: Optional[int] = None, now_ms: Optional[int] = None) -> int:
        """Uloží definici alertu (normalize_alert); nový alert i upravený se (znovu) aktivuje.

        Parametry:
            spec: definice alertu včetně source, symbol a timeframe.
            alert_id: id upravovaného alertu (None = nový).
            now_ms: čas vytvoření (výchozí aktuální čas).
        """
        definition = {key: value for key, value in spec.items() if key not in ("source", "symbol", "timeframe")}
        payload = json.dumps(definition)
        created_at = now_ms if now_ms is not None else utc_now_ms()

        def save_sync() -> int:
            if alert_id is None:
                cur = self._conn.execute(
                    "INSERT INTO alerts (source, symbol, timeframe, spec, created_at) VALUES (?, ?, ?, ?, ?)",
                    (spec["source"], spec["symbol"], spec["timeframe"], payload, created_at),
                )
                new_id = int(cur.lastrowid)
            else:
                # Úprava alert znovu zapne (jako v TradingView po uložení změn)
                self._conn.execute(
                    "UPDATE alerts SET source = ?, symbol = ?, timeframe = ?, spec = ?, active = 1, status = 'active' "
                    "WHERE id = ?",
                    (spec["source"], spec["symbol"], spec["timeframe"], payload, alert_id),
                )
                new_id = alert_id
            self._conn.commit()
            return new_id

        return await self._run(save_sync)

    async def set_alert_state(
        self,
        alert_id: int,
        active: bool,
        status: str,
        fired_at: Optional[int] = None,
    ) -> None:
        """Změní stav alertu; s fired_at zaznamená i spuštění (čas a počet)."""
        if fired_at is None:
            sql = "UPDATE alerts SET active = ?, status = ? WHERE id = ?"
            params: tuple = (int(active), status, alert_id)
        else:
            sql = "UPDATE alerts SET active = ?, status = ?, last_fired_at = ?, fire_count = fire_count + 1 WHERE id = ?"
            params = (int(active), status, fired_at, alert_id)
        await self._run(self._execute_sync, sql, params)

    async def delete_alert(self, alert_id: int) -> None:
        """Smaže alert (záznamy v logu zůstanou)."""
        await self._run(self._execute_sync, "DELETE FROM alerts WHERE id = ?", (alert_id,))

    async def add_alert_log(self, entry: dict) -> None:
        """Zapíše spuštění alertu do logu.

        Parametry:
            entry: {"alert_id", "fired_at", "source", "symbol", "timeframe",
                "price", "message", "delivery"} – delivery je seznam výsledků
                odeslání notifikací (uloží se jako JSON).
        """
        await self._run(
            self._execute_sync,
            "INSERT INTO alert_log (alert_id, fired_at, source, symbol, timeframe, price, message, delivery) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry["alert_id"],
                entry["fired_at"],
                entry["source"],
                entry["symbol"],
                entry["timeframe"],
                entry["price"],
                entry["message"],
                json.dumps(entry.get("delivery") or []),
            ),
        )

    async def alert_log(self, limit: int = 200) -> list[dict]:
        """Vrátí posledních limit spuštění alertů (nejnovější první)."""
        rows = await self._run(
            lambda: self._conn.execute(
                "SELECT alert_id, fired_at, source, symbol, timeframe, price, message, delivery "
                "FROM alert_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        )
        return [
            {
                "alert_id": row[0],
                "fired_at": row[1],
                "source": row[2],
                "symbol": row[3],
                "timeframe": row[4],
                "price": row[5],
                "message": row[6],
                "delivery": json.loads(row[7] or "[]"),
            }
            for row in rows
        ]

    async def clear_alert_log(self) -> None:
        """Vymaže log spuštěných alertů."""
        await self._run(self._execute_sync, "DELETE FROM alert_log", ())


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
