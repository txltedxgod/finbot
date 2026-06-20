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
                    lang      TEXT DEFAULT 'ru',
                    currency  TEXT DEFAULT 'RUB',
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
            # migrate existing DBs that don't have lang/currency columns yet
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            if "lang" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'ru'")
            if "currency" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN currency TEXT DEFAULT 'RUB'")
            # migrate records: add category column if missing
            rcols = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
            if "category" not in rcols:
                conn.execute("ALTER TABLE records ADD COLUMN category TEXT DEFAULT ''")
            if "currency" not in rcols:
                conn.execute("ALTER TABLE records ADD COLUMN currency TEXT DEFAULT ''")
            # custom per-user categories
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_categories (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id  INTEGER NOT NULL,
                    name     TEXT NOT NULL,
                    created  TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, name)
                )
            """)
            # per-user hidden predefined (system) categories
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hidden_categories (
                    user_id  INTEGER NOT NULL,
                    key      TEXT NOT NULL,
                    UNIQUE(user_id, key)
                )
            """)

    # ── Users ──────────────────────────────────────────────────────────────

    def ensure_user(self, user_id: int, name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users(id, name) VALUES(?, ?)",
                (user_id, name)
            )

    def get_user(self, user_id: int) -> Dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_user_lang(self, user_id: int, lang: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET lang=? WHERE id=?", (lang, user_id)
            )

    def set_user_currency(self, user_id: int, currency: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET currency=? WHERE id=?", (currency, user_id)
            )

    def is_setup_done(self, user_id: int) -> bool:
        """Returns True if user has already chosen lang+currency."""
        user = self.get_user(user_id)
        if not user:
            return False
        # lang and currency are always set (with defaults), but we track
        # onboarding via a separate flag encoded as lang != NULL
        return user.get("lang") is not None and user.get("currency") is not None

    # ── Records ────────────────────────────────────────────────────────────

    def add_record(self, user_id: int, rtype: str, amount: float,
                   description: str, date: str, category: str = "",
                   currency: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO records(user_id, type, amount, description, date, category, currency) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (user_id, rtype, amount, description.strip(), date,
                 (category or "").strip(), (currency or "").strip())
            )
            return cur.lastrowid

    # ── Custom categories ──────────────────────────────────────────────────

    def add_custom_category(self, user_id: int, name: str) -> int:
        name = (name or "").strip()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_categories(user_id, name) VALUES(?, ?)",
                (user_id, name)
            )
            row = conn.execute(
                "SELECT id FROM user_categories WHERE user_id=? AND name=?",
                (user_id, name)
            ).fetchone()
            return row["id"] if row else 0

    def get_custom_categories(self, user_id: int) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name FROM user_categories WHERE user_id=? ORDER BY created",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_custom_category_name(self, user_id: int, cat_id: int):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT name FROM user_categories WHERE user_id=? AND id=?",
                (user_id, cat_id)
            ).fetchone()
        return row["name"] if row else None

    def delete_custom_category(self, user_id: int, cat_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM user_categories WHERE user_id=? AND id=?",
                (user_id, cat_id)
            )
            return cur.rowcount > 0

    # ── Hidden (system) categories ─────────────────────────────────────────

    def hide_category(self, user_id: int, key: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO hidden_categories(user_id, key) VALUES(?, ?)",
                (user_id, key)
            )

    def unhide_category(self, user_id: int, key: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM hidden_categories WHERE user_id=? AND key=?",
                (user_id, key)
            )

    def get_hidden_categories(self, user_id: int) -> set:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key FROM hidden_categories WHERE user_id=?",
                (user_id,)
            ).fetchall()
        return {r["key"] for r in rows}

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

    def get_expense_records(self, user_id: int) -> List[Dict]:
        """All expense records, newest first (for per-category drilldown)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM records WHERE user_id=? AND type='expense' "
                "ORDER BY date DESC, created_at DESC",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_category_totals(self, user_id: int, default_currency: str = "") -> List[Dict]:
        """Expense totals grouped by category + currency (empty currency -> default)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category, COALESCE(NULLIF(currency,''), ?) AS cur, "
                "SUM(amount) AS total, COUNT(*) AS cnt "
                "FROM records WHERE user_id=? AND type='expense' "
                "GROUP BY category, cur ORDER BY total DESC",
                (default_currency, user_id)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self, user_id: int, default_currency: str = "") -> Dict[str, Any]:
        with self._conn() as conn:
            meta = conn.execute(
                "SELECT COUNT(*) AS total_records, COUNT(DISTINCT date) AS days "
                "FROM records WHERE user_id=?",
                (user_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT type, COALESCE(NULLIF(currency,''), ?) AS cur, SUM(amount) AS total "
                "FROM records WHERE user_id=? GROUP BY type, cur",
                (default_currency, user_id)
            ).fetchall()

        income_by_cur: Dict[str, float] = {}
        expense_by_cur: Dict[str, float] = {}
        for r in rows:
            bucket = income_by_cur if r["type"] == "income" else expense_by_cur
            bucket[r["cur"]] = bucket.get(r["cur"], 0) + (r["total"] or 0)

        return {
            "total_records":  meta["total_records"],
            "days":           meta["days"],
            "income_by_cur":  income_by_cur,
            "expense_by_cur": expense_by_cur,
        }
