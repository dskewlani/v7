"""
Persistent key-value storage for the trading terminal.

Uses Neon/PostgreSQL when DATABASE_URL is configured and psycopg2 is installed.
Falls back to in-memory storage when no database is available.
"""

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime

try:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("storage")


def _get_db_url() -> str:
    try:
        import streamlit as st
        url = st.secrets.get("DATABASE_URL", "")
        if url:
            return str(url)
    except Exception:
        pass
    return os.environ.get("DATABASE_URL", "")


DATABASE_URL = _get_db_url()
_pool = None
_schema_initialised = False
_mem = {}


def _use_fallback() -> bool:
    return not PSYCOPG2_AVAILABLE or not DATABASE_URL


def _get_pool():
    global _pool
    if _use_fallback():
        return None
    if _pool is None:
        try:
            _pool = ThreadedConnectionPool(minconn=1, maxconn=5, dsn=DATABASE_URL)
        except Exception as exc:
            log.error("[Storage] connection pool failed: %s", exc)
            return None
    return _pool


@contextmanager
def _conn():
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("No database connection available.")
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def _init_schema() -> None:
    global _schema_initialised
    if _schema_initialised:
        return
    if _use_fallback():
        _schema_initialised = True
        return

    ddl = """
        CREATE TABLE IF NOT EXISTS kv_store (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_kv_updated ON kv_store (updated_at DESC);

        CREATE TABLE IF NOT EXISTS trade_log (
            id SERIAL PRIMARY KEY,
            category TEXT NOT NULL,
            symbol TEXT,
            pnl NUMERIC,
            win BOOLEAN,
            strength NUMERIC,
            rec TEXT,
            trade_date DATE DEFAULT CURRENT_DATE,
            logged_at TIMESTAMPTZ DEFAULT NOW(),
            meta TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tradelog_date ON trade_log (trade_date DESC);

        CREATE TABLE IF NOT EXISTS daily_pnl (
            trade_date DATE PRIMARY KEY,
            pnl NUMERIC NOT NULL DEFAULT 0,
            trades INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
    except Exception as exc:
        log.error("[Storage] schema init failed: %s", exc)
    finally:
        _schema_initialised = True


def save(key: str, data) -> None:
    _init_schema()
    if _use_fallback():
        _mem[key] = data
        return

    try:
        serialised = json.dumps(data, default=str)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO kv_store (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    (key, serialised),
                )
    except Exception as exc:
        log.error("[Storage] save(%s): %s", key, exc)


def load(key: str, default=None):
    _init_schema()
    if _use_fallback():
        return _mem.get(key, default)

    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM kv_store WHERE key = %s", (key,))
                row = cur.fetchone()
                return default if row is None else json.loads(row[0])
    except Exception as exc:
        log.error("[Storage] load(%s): %s", key, exc)
        return default


def append_record(key: str, record: dict) -> None:
    existing = load(key, default=[])
    if not isinstance(existing, list):
        existing = []
    rec = dict(record)
    rec["_saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing.append(rec)
    save(key, existing)


def delete(key: str) -> None:
    _init_schema()
    if _use_fallback():
        _mem.pop(key, None)
        return

    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kv_store WHERE key = %s", (key,))
    except Exception as exc:
        log.error("[Storage] delete(%s): %s", key, exc)
