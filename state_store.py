from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.path, timeout=15)

    def _init_db(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS kv (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS attempts (
                        signal_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        bar_time TEXT NOT NULL,
                        status TEXT NOT NULL,
                        order_ticket INTEGER,
                        deal_ticket INTEGER,
                        message TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_updated ON attempts(updated_at)")
        finally:
            conn.close()

    def get(self, key: str):
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def set(self, key: str, value: str):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, str(value), now),
                )
        finally:
            conn.close()

    def get_float(self, key: str):
        v = self.get(key)
        return None if v is None else float(v)

    def reserve_signal(self, signal_id: str, symbol: str, side: str, bar_time: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO attempts(signal_id,created_at,updated_at,symbol,side,bar_time,status) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (signal_id, now, now, symbol, side, bar_time, "RESERVED"),
                )
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def update_attempt(self, signal_id: str, status: str, order_ticket=None, deal_ticket=None, message=""):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE attempts SET updated_at=?, status=?, order_ticket=?, deal_ticket=?, message=? WHERE signal_id=?",
                    (now, status, order_ticket, deal_ticket, message, signal_id),
                )
        finally:
            conn.close()

    def last_success_time(self):
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT updated_at FROM attempts WHERE status IN ('PAPER','LIVE_DONE','LIVE_PARTIAL') "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return datetime.fromisoformat(row[0])
        finally:
            conn.close()
