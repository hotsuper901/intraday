"""Tiny SQLite layer. Bars are 5-minute OHLCV candles keyed by (ticker, ts)."""
import sqlite3
import threading
import time
from pathlib import Path

from . import config

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db() -> None:
    with _lock, _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS bars (
                ticker TEXT NOT NULL,
                ts     INTEGER NOT NULL,
                open   REAL,
                high   REAL,
                low    REAL,
                close  REAL,
                volume INTEGER,
                PRIMARY KEY (ticker, ts)
            );
            CREATE TABLE IF NOT EXISTS meta (
                ticker       TEXT PRIMARY KEY,
                name         TEXT,
                prev_close   REAL,
                session_open INTEGER,
                updated_at   INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_bars_ticker ON bars(ticker);
            """
        )


def upsert_bars(ticker: str, bars: list[dict]) -> None:
    """bars: list of dicts with ts, open, high, low, close, volume."""
    if not bars:
        return
    with _lock, _connect() as con:
        con.executemany(
            """
            INSERT OR REPLACE INTO bars (ticker, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    ticker,
                    int(b["ts"]),
                    b["open"],
                    b["high"],
                    b["low"],
                    b["close"],
                    int(b["volume"] or 0),
                )
                for b in bars
            ],
        )
        # Keep storage bounded: only the most recent N bars per ticker.
        con.execute(
            """
            DELETE FROM bars WHERE ticker = ? AND ts < (
                SELECT COALESCE(MAX(ts), 0) - ? FROM bars WHERE ticker = ?
            )
            """,
            (ticker, config.MAX_BARS_PER_TICKER * 300, ticker),
        )
        con.execute(
            "UPDATE meta SET updated_at = ? WHERE ticker = ?",
            (int(time.time()), ticker),
        )


def upsert_meta(ticker: str, name: str | None, prev_close: float | None, session_open: int | None) -> None:
    with _lock, _connect() as con:
        con.execute(
            """
            INSERT INTO meta (ticker, name, prev_close, session_open, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name = COALESCE(excluded.name, meta.name),
                prev_close = COALESCE(excluded.prev_close, meta.prev_close),
                session_open = COALESCE(excluded.session_open, meta.session_open),
                updated_at = excluded.updated_at
            """,
            (ticker, name, prev_close, session_open, int(time.time())),
        )


def bars_for(ticker: str, limit: int = 80) -> list[dict]:
    with _lock, _connect() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT ts, open, high, low, close, volume FROM bars "
            "WHERE ticker = ? ORDER BY ts DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def all_meta() -> dict[str, dict]:
    with _lock, _connect() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM meta").fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def latest_ts(ticker: str) -> int | None:
    with _lock, _connect() as con:
        row = con.execute("SELECT MAX(ts) FROM bars WHERE ticker = ?", (ticker,)).fetchone()
    return row[0] if row and row[0] is not None else None


def bar_count() -> int:
    with _lock, _connect() as con:
        return con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
