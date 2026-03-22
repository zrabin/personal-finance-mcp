"""SQLite database layer — schema, migrations, queries."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    institution TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    subtype TEXT,
    last_four TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    enrollment_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    available REAL,
    ledger REAL,
    as_of TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    amount REAL NOT NULL,
    date TEXT NOT NULL,
    description TEXT,
    category TEXT,
    type TEXT,
    status TEXT,
    counterparty TEXT,
    source TEXT NOT NULL,
    raw_data TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    accounts_synced INTEGER DEFAULT 0,
    transactions_synced INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'in_progress',
    error TEXT
);

CREATE TABLE IF NOT EXISTS enrollments (
    id TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    institution TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_transactions_account_id ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_balances_account_id ON balances(account_id);
CREATE INDEX IF NOT EXISTS idx_balances_as_of ON balances(as_of);
"""


class Database:
    """SQLite database wrapper with schema management."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path).expanduser())
        self._ensure_directory()
        is_new = not Path(self.db_path).exists()
        self.conn = sqlite3.connect(self.db_path)
        if is_new:
            os.chmod(self.db_path, 0o600)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _ensure_directory(self) -> None:
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()
        else:
            self._run_migrations(row[0])

    def _run_migrations(self, current_version: int) -> None:
        """Run sequential migrations from current_version to SCHEMA_VERSION."""
        # Future migrations go here: if current_version < 2: migrate_v1_to_v2()
        if current_version < SCHEMA_VERSION:
            self.conn.execute(
                "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
            )
            self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- Account methods ---

    def upsert_account(
        self,
        id: str,
        source: str,
        institution: str,
        name: str,
        type: str,
        subtype: str | None = None,
        last_four: str | None = None,
        enrollment_id: str | None = None,
        status: str = "open",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO accounts (id, source, institution, name, type, subtype,
               last_four, status, enrollment_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
               name=excluded.name, status=excluded.status, updated_at=excluded.updated_at""",
            (id, source, institution, name, type, subtype, last_four,
             status, enrollment_id, now, now),
        )
        self.conn.commit()

    def get_accounts(self, source: str | None = None) -> list[dict]:
        sql = """
            SELECT a.*, b.available, b.ledger, b.as_of
            FROM accounts a
            LEFT JOIN (
                SELECT account_id, available, ledger, as_of,
                    ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY as_of DESC) as rn
                FROM balances
            ) b ON a.id = b.account_id AND b.rn = 1
        """
        params: list = []
        if source:
            sql += " WHERE a.source = ?"
            params.append(source)
        rows = self.conn.execute(sql, params).fetchall()
        cols = [
            "id", "source", "institution", "name", "type", "subtype",
            "last_four", "status", "enrollment_id", "created_at", "updated_at",
            "available_balance", "ledger_balance", "balance_as_of",
        ]
        return [dict(zip(cols, row)) for row in rows]

    # --- Transaction methods ---

    def insert_transactions(self, transactions: list[dict]) -> int:
        inserted = 0
        for txn in transactions:
            now = datetime.now(timezone.utc).isoformat()
            try:
                self.conn.execute(
                    """INSERT OR IGNORE INTO transactions
                    (id, account_id, amount, date, description, category,
                     type, status, counterparty, source, raw_data, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        txn["id"], txn["account_id"], txn["amount"],
                        txn["date"], txn.get("description"), txn.get("category"),
                        txn.get("type"), txn.get("status"), txn.get("counterparty"),
                        txn["source"], txn.get("raw_data"), now,
                    ),
                )
                if self.conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()
        return inserted

    def get_transactions(
        self,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        search: str | None = None,
        exclude_descriptions: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        conditions = []
        params: list = []

        if exclude_descriptions:
            for pattern in exclude_descriptions:
                conditions.append("(description IS NULL OR description NOT LIKE ?)")
                params.append(f"%{pattern}%")
        if account_id:
            conditions.append("account_id = ?")
            params.append(account_id)
        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if min_amount is not None:
            conditions.append("amount >= ?")
            params.append(min_amount)
        if max_amount is not None:
            conditions.append("amount <= ?")
            params.append(max_amount)
        if search:
            conditions.append(
                "(description LIKE ? OR counterparty LIKE ?)"
            )
            params.extend([f"%{search}%", f"%{search}%"])

        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        count_sql = f"SELECT COUNT(*) FROM transactions{where}"
        total = self.conn.execute(count_sql, params).fetchone()[0]

        data_sql = f"SELECT * FROM transactions{where} ORDER BY date DESC"
        if limit > 0:
            data_sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        rows = self.conn.execute(data_sql, params).fetchall()
        cols = [
            "id", "account_id", "amount", "date", "description", "category",
            "type", "status", "counterparty", "source", "raw_data", "created_at",
        ]
        return {
            "transactions": [dict(zip(cols, row)) for row in rows],
            "total": total,
        }

    # --- Balance methods ---

    def save_balance(
        self,
        account_id: str,
        available: float | None = None,
        ledger: float | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO balances (account_id, available, ledger, as_of) VALUES (?, ?, ?, ?)",
            (account_id, available, ledger, now),
        )
        self.conn.commit()

    def get_balances(self, account_id: str | None = None) -> list[dict]:
        sql = """
            SELECT b.account_id, a.institution, a.name, a.last_four,
                   b.available, b.ledger, b.as_of
            FROM balances b
            JOIN accounts a ON b.account_id = a.id
            WHERE a.source != 'venmo'
            AND b.as_of = (
                SELECT MAX(b2.as_of) FROM balances b2
                WHERE b2.account_id = b.account_id
            )
        """
        params: list = []
        if account_id:
            sql += " AND b.account_id = ?"
            params.append(account_id)
        rows = self.conn.execute(sql, params).fetchall()
        cols = ["account_id", "institution", "name", "last_four",
                "available", "ledger", "as_of"]
        return [dict(zip(cols, row)) for row in rows]

    # --- Aggregation methods ---

    def get_spending_summary(
        self,
        start_date: str,
        end_date: str,
        account_id: str | None = None,
        exclude_transfers: bool = True,
        exclude_descriptions: list[str] | None = None,
    ) -> list[dict]:
        conditions = ["date >= ?", "date <= ?", "amount < 0"]
        params: list = [start_date, end_date]
        if exclude_transfers:
            conditions.append("type != 'transfer'")
        if exclude_descriptions:
            for pattern in exclude_descriptions:
                conditions.append("(description IS NULL OR description NOT LIKE ?)")
                params.append(f"%{pattern}%")
        if account_id:
            conditions.append("account_id = ?")
            params.append(account_id)
        where = " AND ".join(conditions)
        sql = f"""
            SELECT category, SUM(amount) as total, COUNT(*) as count
            FROM transactions WHERE {where}
            GROUP BY category ORDER BY total ASC
        """
        rows = self.conn.execute(sql, params).fetchall()
        return [{"category": r[0], "total": r[1], "count": r[2]} for r in rows]

    def get_cash_flow(
        self,
        start_date: str,
        end_date: str,
        account_id: str | None = None,
        exclude_descriptions: list[str] | None = None,
    ) -> dict:
        conditions = ["date >= ?", "date <= ?"]
        params: list = [start_date, end_date]
        if exclude_descriptions:
            for pattern in exclude_descriptions:
                conditions.append("(description IS NULL OR description NOT LIKE ?)")
                params.append(f"%{pattern}%")
        if account_id:
            conditions.append("account_id = ?")
            params.append(account_id)
        where = " AND ".join(conditions)
        sql = f"""
            SELECT
                COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) as income,
                COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) as expenses
            FROM transactions WHERE {where}
        """
        row = self.conn.execute(sql, params).fetchone()
        income, expenses = row[0], row[1]
        return {"income": income, "expenses": expenses, "net": income + expenses}

    def get_monthly_trend(
        self,
        months: int = 6,
        account_id: str | None = None,
        exclude_descriptions: list[str] | None = None,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if exclude_descriptions:
            for pattern in exclude_descriptions:
                conditions.append("(description IS NULL OR description NOT LIKE ?)")
                params.append(f"%{pattern}%")
        if account_id:
            conditions.append("account_id = ?")
            params.append(account_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"""
            SELECT
                strftime('%Y-%m', date) as month,
                COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) as income,
                COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) as expenses
            FROM transactions{where}
            GROUP BY month ORDER BY month DESC LIMIT ?
        """
        params.append(months)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {"month": r[0], "income": r[1], "expenses": r[2], "net": r[1] + r[2]}
            for r in rows
        ]

    # --- Enrollment methods ---

    def save_enrollment(
        self, enrollment_id: str, access_token: str, institution: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT OR REPLACE INTO enrollments
            (id, access_token, institution, created_at, status)
            VALUES (?, ?, ?, ?, 'active')""",
            (enrollment_id, access_token, institution, now),
        )
        self.conn.commit()

    def get_active_enrollments(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, access_token, institution, created_at FROM enrollments WHERE status = 'active'"
        ).fetchall()
        return [
            {"id": r[0], "access_token": r[1], "institution": r[2], "created_at": r[3]}
            for r in rows
        ]

    def disconnect_enrollment(self, enrollment_id: str) -> None:
        self.conn.execute(
            "UPDATE enrollments SET status = 'disconnected' WHERE id = ?",
            (enrollment_id,),
        )
        self.conn.commit()

    # --- Sync log methods ---

    def start_sync_log(self, source: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "INSERT INTO sync_log (source, started_at, status) VALUES (?, ?, 'in_progress')",
            (source, now),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def complete_sync_log(
        self,
        sync_id: int,
        accounts_synced: int = 0,
        transactions_synced: int = 0,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE sync_log SET completed_at = ?, accounts_synced = ?,
            transactions_synced = ?, status = ?, error = ? WHERE id = ?""",
            (now, accounts_synced, transactions_synced, status, error, sync_id),
        )
        self.conn.commit()

    def get_last_sync_date(self, source: str) -> str | None:
        row = self.conn.execute(
            """SELECT completed_at FROM sync_log
            WHERE source = ? AND status = 'success'
            ORDER BY completed_at DESC LIMIT 1""",
            (source,),
        ).fetchone()
        return row[0] if row else None
