import sqlite3
import os
from typing import List, Dict, Any


DB_PATH = os.getenv("DB_PATH", "finance.db")


class Database:
    def __init__(self):
        self.path = DB_PATH
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id        INTEGER PRIMARY KEY,
                    name      TEXT,
                    created   TEXT DEFAULT (date('now'))
                );

                CREATE TABLE IF NOT EXISTS records (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    type        TEXT NOT NULL CHECK(type IN ('income','expense')),
                    amount      REAL NOT NULL CHECK(amount > 0),
                    description TEXT DEFAULT '',
                    date        TEXT NOT NULL,
                    created_at  TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_records_user_date
                    ON records(user_id, date);
            """)

    # ── Users ──────────────────────────────────────────────────────────────

    def ensure_user(self, user_id: int, name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users(id, name) VALUES(?, ?)",
                (user_id, name)
            )

    # ── Records ────────────────────────────────────────────────────────────

    def add_record(self, user_id: int, rtype: str, amount: float,
                   description: str, date: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO records(user_id, type, amount, description, date) "
                "VALUES(?, ?, ?, ?, ?)",
                (user_id, rtype, amount, description.strip(), date)
            )
            return cur.lastrowid

    def delete_record(self, record_id: int, user_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM records WHERE id=? AND user_id=?",
                (record_id, user_id)
            )
            return cur.rowcount > 0

    def get_records_by_date(self, user_id: int, date: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM records WHERE user_id=? AND date=? ORDER BY created_at",
                (user_id, date)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_records_by_month(self, user_id: int, year: int, month: int) -> List[Dict]:
        month_str = f"{year}-{month:02d}"
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM records "
                "WHERE user_id=? AND date LIKE ? "
                "ORDER BY date, created_at",
                (user_id, f"{month_str}-%")
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Stats ──────────────────────────────────────────────────────────────

    def get_stats(self, user_id: int) -> Dict[str, Any]:
        with self._conn() as conn:
            totals = conn.execute("""
                SELECT
                    COUNT(*)                                  AS total_records,
                    COUNT(DISTINCT date)                      AS days,
                    COALESCE(SUM(CASE WHEN type='income'  THEN amount END), 0) AS total_income,
                    COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) AS total_expense
                FROM records WHERE user_id=?
            """, (user_id,)).fetchone()

            top = conn.execute("""
                SELECT description, SUM(amount) AS total
                FROM records
                WHERE user_id=? AND type='expense' AND description != ''
                GROUP BY description
                ORDER BY total DESC
                LIMIT 5
            """, (user_id,)).fetchall()

        return {
            "total_records": totals["total_records"],
            "days":          totals["days"],
            "total_income":  totals["total_income"],
            "total_expense": totals["total_expense"],
            "top_expenses":  [dict(r) for r in top],
        }
