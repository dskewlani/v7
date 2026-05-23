"""
storage.py — ProTrader Terminal v7
All 14 Blocks Enhancement: Block 7 (Infrastructure) + Block 13 (Journal Intelligence)
- Persistent key-value storage: Neon/PostgreSQL with in-memory fallback
- Structured trade_log table (Block 7b)
- Multi-user key prefixing (Block 7d)
- Trade note/attachment support (Block 13a)
- Pattern recognition data storage (Block 13b)
- Export/import helpers (Block 14d)
"""

import json
import logging
import os
import csv
import io
from contextlib import contextmanager
from datetime import datetime, date

try:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("storage")

# ─── Structured logger for Block 14c ─────────────────────────────────────────
_app_log_buffer = []   # in-memory ring buffer, last 200 entries

def app_log(level: str, category: str, message: str, meta: dict = None):
    """Structured application log for the admin dashboard."""
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level.upper(),
        "category": category,
        "message": message,
        "meta": meta or {},
    }
    _app_log_buffer.append(entry)
    if len(_app_log_buffer) > 200:
        _app_log_buffer.pop(0)

def get_app_logs(last_n: int = 100) -> list:
    return list(reversed(_app_log_buffer[-last_n:]))


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

# ─── Multi-user: current user prefix ─────────────────────────────────────────
_current_user_id: str = "default"

def set_current_user(user_id: str):
    global _current_user_id
    _current_user_id = user_id or "default"

def get_user_storage_key(user_id: str, key: str) -> str:
    """Block 7d: prefix all storage keys with user_id."""
    uid = (user_id or "default").replace(":", "_").replace("/", "_")
    return f"u:{uid}:{key}"

def _user_key(key: str) -> str:
    return get_user_storage_key(_current_user_id, key)


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
            user_id TEXT NOT NULL DEFAULT 'default',
            category TEXT NOT NULL,
            symbol TEXT,
            trade_type TEXT,
            entry_price NUMERIC,
            exit_price NUMERIC,
            qty INTEGER,
            pnl NUMERIC,
            win BOOLEAN,
            strength NUMERIC,
            rec TEXT,
            trade_date DATE DEFAULT CURRENT_DATE,
            entry_time TIMESTAMPTZ,
            exit_time TIMESTAMPTZ DEFAULT NOW(),
            logged_at TIMESTAMPTZ DEFAULT NOW(),
            note TEXT,
            meta JSONB
        );
        CREATE INDEX IF NOT EXISTS idx_tradelog_date ON trade_log (trade_date DESC);
        CREATE INDEX IF NOT EXISTS idx_tradelog_user ON trade_log (user_id, trade_date DESC);

        CREATE TABLE IF NOT EXISTS daily_pnl (
            trade_date DATE,
            user_id TEXT NOT NULL DEFAULT 'default',
            pnl NUMERIC NOT NULL DEFAULT 0,
            trades INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (trade_date, user_id)
        );

        CREATE TABLE IF NOT EXISTS price_alerts (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            target_price NUMERIC NOT NULL,
            note TEXT,
            triggered BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            triggered_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_user ON price_alerts (user_id, triggered);

        CREATE TABLE IF NOT EXISTS user_auth (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            pin_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            last_login TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS trade_notes (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            trade_id TEXT NOT NULL,
            note TEXT,
            screenshot_b64 TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_notes_trade ON trade_notes (trade_id);
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
        app_log("INFO", "storage", "Schema initialised successfully")
    except Exception as exc:
        log.error("[Storage] schema init failed: %s", exc)
        app_log("ERROR", "storage", f"Schema init failed: {exc}")
    finally:
        _schema_initialised = True


# ─── Core KV Store ───────────────────────────────────────────────────────────

def save(key: str, data, user_scoped: bool = True) -> None:
    """Save data. If user_scoped=True, key is prefixed with current user."""
    _init_schema()
    final_key = _user_key(key) if user_scoped else key
    if _use_fallback():
        _mem[final_key] = data
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
                    (final_key, serialised),
                )
    except Exception as exc:
        log.error("[Storage] save(%s): %s", final_key, exc)
        app_log("ERROR", "storage", f"save({final_key}) failed: {exc}")


def load(key: str, default=None, user_scoped: bool = True):
    """Load data. If user_scoped=True, key is prefixed with current user."""
    _init_schema()
    final_key = _user_key(key) if user_scoped else key
    if _use_fallback():
        return _mem.get(final_key, default)
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM kv_store WHERE key = %s", (final_key,))
                row = cur.fetchone()
                return default if row is None else json.loads(row[0])
    except Exception as exc:
        log.error("[Storage] load(%s): %s", final_key, exc)
        return default


def append_record(key: str, record: dict, user_scoped: bool = True) -> None:
    existing = load(key, default=[], user_scoped=user_scoped)
    if not isinstance(existing, list):
        existing = []
    rec = dict(record)
    rec["_saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing.append(rec)
    save(key, existing, user_scoped=user_scoped)


def delete(key: str, user_scoped: bool = True) -> None:
    _init_schema()
    final_key = _user_key(key) if user_scoped else key
    if _use_fallback():
        _mem.pop(final_key, None)
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kv_store WHERE key = %s", (final_key,))
    except Exception as exc:
        log.error("[Storage] delete(%s): %s", final_key, exc)


# ─── Block 7b: Structured Trade Log ──────────────────────────────────────────

def log_trade(
    category: str,
    symbol: str,
    trade_type: str,
    entry_price: float,
    exit_price: float,
    qty: int,
    pnl: float,
    win: bool,
    strength: float,
    rec: str,
    note: str = "",
    meta: dict = None,
    user_id: str = None,
) -> None:
    """Block 7b: Write a closed trade to the structured trade_log table."""
    _init_schema()
    uid = user_id or _current_user_id
    if _use_fallback():
        # Fallback: append to kv_store list
        append_record("structured_trade_log", {
            "user_id": uid, "category": category, "symbol": symbol,
            "trade_type": trade_type, "entry_price": entry_price,
            "exit_price": exit_price, "qty": qty, "pnl": pnl, "win": win,
            "strength": strength, "rec": rec, "note": note, "meta": meta or {},
        })
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trade_log
                    (user_id, category, symbol, trade_type, entry_price, exit_price,
                     qty, pnl, win, strength, rec, note, meta, exit_time)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    """,
                    (uid, category, symbol, trade_type, entry_price, exit_price,
                     qty, pnl, win, strength, rec, note,
                     json.dumps(meta or {}, default=str)),
                )
        # Update daily_pnl aggregate
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO daily_pnl (trade_date, user_id, pnl, trades, updated_at)
                        VALUES (CURRENT_DATE, %s, %s, 1, NOW())
                        ON CONFLICT (trade_date, user_id) DO UPDATE
                        SET pnl = daily_pnl.pnl + EXCLUDED.pnl,
                            trades = daily_pnl.trades + 1,
                            updated_at = NOW()
                        """,
                        (uid, pnl),
                    )
        except Exception:
            pass
        app_log("INFO", "trade", f"Logged {rec} {symbol} P&L ₹{pnl:+.0f}", {"category": category})
    except Exception as exc:
        log.error("[Storage] log_trade: %s", exc)
        app_log("ERROR", "trade", f"log_trade failed: {exc}")


def get_trade_history_sql(user_id: str = None, days: int = 90) -> list:
    """Fetch structured trade history from PostgreSQL."""
    _init_schema()
    uid = user_id or _current_user_id
    if _use_fallback():
        return load("structured_trade_log", default=[], user_scoped=False)
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, category, symbol, trade_type, entry_price, exit_price,
                           qty, pnl, win, strength, rec, trade_date, logged_at, note
                    FROM trade_log
                    WHERE user_id = %s AND trade_date >= CURRENT_DATE - INTERVAL '%s days'
                    ORDER BY logged_at DESC
                    """,
                    (uid, days),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        log.error("[Storage] get_trade_history_sql: %s", exc)
        return []


def get_daily_pnl_history(user_id: str = None, days: int = 60) -> list:
    """Fetch daily P&L aggregates for heatmap calendar (Block 6d)."""
    _init_schema()
    uid = user_id or _current_user_id
    if _use_fallback():
        return []
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT trade_date, pnl, trades FROM daily_pnl
                    WHERE user_id = %s AND trade_date >= CURRENT_DATE - INTERVAL '%s days'
                    ORDER BY trade_date
                    """,
                    (uid, days),
                )
                return [{"date": str(r[0]), "pnl": float(r[1]), "trades": int(r[2])}
                        for r in cur.fetchall()]
    except Exception as exc:
        log.error("[Storage] get_daily_pnl_history: %s", exc)
        return []


# ─── Block 8: Price Alerts ────────────────────────────────────────────────────

def save_alert(symbol: str, alert_type: str, target_price: float,
               note: str = "", user_id: str = None) -> int:
    """Save a price alert. Returns alert id."""
    uid = user_id or _current_user_id
    _init_schema()
    if _use_fallback():
        alerts = load("price_alerts", default=[], user_scoped=False)
        alert = {
            "id": len(alerts) + 1, "user_id": uid, "symbol": symbol,
            "alert_type": alert_type, "target_price": target_price,
            "note": note, "triggered": False,
        }
        alerts.append(alert)
        save("price_alerts", alerts, user_scoped=False)
        return alert["id"]
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO price_alerts (user_id, symbol, alert_type, target_price, note)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                    """,
                    (uid, symbol, alert_type, target_price, note),
                )
                return cur.fetchone()[0]
    except Exception as exc:
        log.error("[Storage] save_alert: %s", exc)
        return -1


def get_active_alerts(user_id: str = None) -> list:
    uid = user_id or _current_user_id
    _init_schema()
    if _use_fallback():
        return [a for a in load("price_alerts", default=[], user_scoped=False)
                if a.get("user_id") == uid and not a.get("triggered")]
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, symbol, alert_type, target_price, note FROM price_alerts "
                    "WHERE user_id=%s AND triggered=FALSE ORDER BY created_at",
                    (uid,),
                )
                return [{"id": r[0], "symbol": r[1], "alert_type": r[2],
                         "target_price": float(r[3]), "note": r[4]}
                        for r in cur.fetchall()]
    except Exception as exc:
        log.error("[Storage] get_active_alerts: %s", exc)
        return []


def mark_alert_triggered(alert_id: int, user_id: str = None) -> None:
    uid = user_id or _current_user_id
    _init_schema()
    if _use_fallback():
        alerts = load("price_alerts", default=[], user_scoped=False)
        for a in alerts:
            if a.get("id") == alert_id and a.get("user_id") == uid:
                a["triggered"] = True
        save("price_alerts", alerts, user_scoped=False)
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE price_alerts SET triggered=TRUE, triggered_at=NOW() WHERE id=%s AND user_id=%s",
                    (alert_id, uid),
                )
    except Exception as exc:
        log.error("[Storage] mark_alert_triggered: %s", exc)


def delete_alert(alert_id: int, user_id: str = None) -> None:
    uid = user_id or _current_user_id
    _init_schema()
    if _use_fallback():
        alerts = [a for a in load("price_alerts", default=[], user_scoped=False)
                  if not (a.get("id") == alert_id and a.get("user_id") == uid)]
        save("price_alerts", alerts, user_scoped=False)
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM price_alerts WHERE id=%s AND user_id=%s", (alert_id, uid))
    except Exception as exc:
        log.error("[Storage] delete_alert: %s", exc)


# ─── Block 7d: Multi-User Auth ────────────────────────────────────────────────

def _hash_pin(pin: str) -> str:
    import hashlib
    return hashlib.sha256(pin.encode()).hexdigest()


def create_user(username: str, pin: str) -> bool:
    """Create a new user. Returns True on success."""
    _init_schema()
    user_id = username.lower().strip()
    pin_hash = _hash_pin(pin)
    if _use_fallback():
        users = load("users", default={}, user_scoped=False)
        if username in users:
            return False
        users[username] = {"user_id": user_id, "pin_hash": pin_hash}
        save("users", users, user_scoped=False)
        return True
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_auth (user_id, username, pin_hash) VALUES (%s,%s,%s) "
                    "ON CONFLICT DO NOTHING RETURNING user_id",
                    (user_id, username, pin_hash),
                )
                return cur.fetchone() is not None
    except Exception:
        return False


def verify_user(username: str, pin: str) -> str | None:
    """Verify credentials. Returns user_id or None."""
    _init_schema()
    pin_hash = _hash_pin(pin)
    if _use_fallback():
        users = load("users", default={}, user_scoped=False)
        u = users.get(username, {})
        return u.get("user_id") if u.get("pin_hash") == pin_hash else None
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id FROM user_auth WHERE username=%s AND pin_hash=%s",
                    (username, pin_hash),
                )
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE user_auth SET last_login=NOW() WHERE user_id=%s", (row[0],))
                return row[0] if row else None
    except Exception:
        return None


def list_users() -> list:
    """List all usernames (admin use)."""
    _init_schema()
    if _use_fallback():
        return list(load("users", default={}, user_scoped=False).keys())
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT username FROM user_auth ORDER BY created_at")
                return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


# ─── Block 13a: Trade Notes ───────────────────────────────────────────────────

def save_trade_note(trade_id: str, note: str, screenshot_b64: str = "",
                    user_id: str = None) -> None:
    """Save text note and optional screenshot for a trade."""
    uid = user_id or _current_user_id
    _init_schema()
    if _use_fallback():
        notes = load("trade_notes", default={}, user_scoped=False)
        notes[trade_id] = {"note": note, "screenshot": screenshot_b64,
                           "user_id": uid, "ts": datetime.now().isoformat()}
        save("trade_notes", notes, user_scoped=False)
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trade_notes (user_id, trade_id, note, screenshot_b64)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (uid, trade_id, note, screenshot_b64),
                )
    except Exception as exc:
        log.error("[Storage] save_trade_note: %s", exc)


def get_trade_note(trade_id: str, user_id: str = None) -> dict:
    uid = user_id or _current_user_id
    _init_schema()
    if _use_fallback():
        return load("trade_notes", default={}, user_scoped=False).get(trade_id, {})
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT note, screenshot_b64, created_at FROM trade_notes "
                    "WHERE trade_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 1",
                    (trade_id, uid),
                )
                row = cur.fetchone()
                if row:
                    return {"note": row[0], "screenshot": row[1], "ts": str(row[2])}
    except Exception:
        pass
    return {}


# ─── Block 14d: Export / Import ──────────────────────────────────────────────

def export_trades_csv(trade_history: list) -> str:
    """Export trade history list to CSV string."""
    if not trade_history:
        return ""
    fields = ["symbol", "type", "mode", "entry", "cmp", "target", "sl",
              "qty", "pnl", "brokerage", "strength", "rec", "date",
              "exit_time", "category"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for t in trade_history:
        row = {f: t.get(f, "") for f in fields}
        row["category"] = t.get("category", "equity")
        writer.writerow(row)
    return buf.getvalue()


def import_trades_csv(csv_text: str) -> list:
    """Parse CSV text back to trade dicts."""
    trades = []
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            trade = dict(row)
            for num_field in ["entry", "cmp", "target", "sl", "qty", "pnl", "brokerage", "strength"]:
                try:
                    trade[num_field] = float(trade[num_field]) if trade.get(num_field) else 0.0
                except Exception:
                    trade[num_field] = 0.0
            trades.append(trade)
    except Exception as exc:
        log.error("[Storage] import_trades_csv: %s", exc)
    return trades


def export_portfolio_csv(portfolio: list, segment: str = "equity") -> str:
    """Export open portfolio to CSV."""
    if not portfolio:
        return ""
    fields = ["symbol", "type", "entry", "cmp", "target", "sl", "qty",
              "lots", "lot_size", "pnl", "brokerage", "strength", "rec", "date"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for p in portfolio:
        writer.writerow({f: p.get(f, "") for f in fields})
    return buf.getvalue()
