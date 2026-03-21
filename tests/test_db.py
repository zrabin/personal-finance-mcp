import pytest
from personal_finance_mcp.db import Database


class TestSchema:
    def test_creates_tables(self, tmp_db):
        db = Database(tmp_db)
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {row[0] for row in tables}
        assert "accounts" in table_names
        assert "balances" in table_names
        assert "transactions" in table_names
        assert "sync_log" in table_names
        assert "enrollments" in table_names
        assert "schema_version" in table_names

    def test_schema_version_is_set(self, tmp_db):
        db = Database(tmp_db)
        row = db.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] >= 1

    def test_creates_db_directory(self, tmp_path):
        db_path = str(tmp_path / "subdir" / "test.db")
        db = Database(db_path)
        assert db.execute("SELECT 1").fetchone()[0] == 1


class TestAccounts:
    def test_upsert_account(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_123",
            source="teller",
            institution="Chase",
            name="Chase Checking",
            type="depository",
            subtype="checking",
            last_four="1234",
            enrollment_id="enr_456",
        )
        row = db.execute("SELECT * FROM accounts WHERE id = ?", ("acc_123",)).fetchone()
        assert row is not None

    def test_get_accounts(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository", subtype="checking",
        )
        db.upsert_account(
            id="acc_2", source="venmo", institution="Venmo",
            name="Venmo", type="depository", subtype="checking",
        )
        all_accounts = db.get_accounts()
        assert len(all_accounts) == 2
        teller_only = db.get_accounts(source="teller")
        assert len(teller_only) == 1


class TestTransactions:
    def test_insert_transaction(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository",
        )
        db.insert_transactions([{
            "id": "txn_1",
            "account_id": "acc_1",
            "amount": -25.50,
            "date": "2026-03-15",
            "description": "Coffee Shop",
            "category": "dining",
            "type": "card_payment",
            "status": "posted",
            "counterparty": "Blue Bottle",
            "source": "teller",
            "raw_data": "{}",
        }])
        row = db.execute("SELECT * FROM transactions WHERE id = ?", ("txn_1",)).fetchone()
        assert row is not None

    def test_insert_ignores_duplicates(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository",
        )
        txn = {
            "id": "txn_1", "account_id": "acc_1", "amount": -10.0,
            "date": "2026-03-15", "description": "Test", "source": "teller",
        }
        inserted_1 = db.insert_transactions([txn])
        inserted_2 = db.insert_transactions([txn])
        assert inserted_1 == 1
        assert inserted_2 == 0

    def test_get_transactions_with_filters(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository",
        )
        for i in range(5):
            db.insert_transactions([{
                "id": f"txn_{i}", "account_id": "acc_1",
                "amount": -(i + 1) * 10.0, "date": f"2026-03-{15 + i:02d}",
                "description": f"Purchase {i}", "category": "dining" if i < 3 else "groceries",
                "source": "teller",
            }])
        result = db.get_transactions(category="dining")
        assert result["total"] == 3
        assert len(result["transactions"]) == 3

        result = db.get_transactions(start_date="2026-03-17", end_date="2026-03-19")
        assert result["total"] == 3

        result = db.get_transactions(limit=2)
        assert len(result["transactions"]) == 2
        assert result["total"] == 5

    def test_get_transactions_search(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository",
        )
        db.insert_transactions([
            {"id": "txn_1", "account_id": "acc_1", "amount": -15.0,
             "date": "2026-03-15", "description": "Blue Bottle Coffee",
             "counterparty": "Blue Bottle", "source": "teller"},
            {"id": "txn_2", "account_id": "acc_1", "amount": -30.0,
             "date": "2026-03-15", "description": "Whole Foods Market",
             "counterparty": "Whole Foods", "source": "teller"},
        ])
        result = db.get_transactions(search="bottle")
        assert result["total"] == 1


class TestAggregations:
    @pytest.fixture(autouse=True)
    def setup_data(self, tmp_db):
        self.db = Database(tmp_db)
        self.db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository",
        )
        transactions = [
            {"id": "t1", "account_id": "acc_1", "amount": -50.0,
             "date": "2026-01-15", "category": "dining", "type": "card_payment", "source": "teller"},
            {"id": "t2", "account_id": "acc_1", "amount": -30.0,
             "date": "2026-01-20", "category": "groceries", "type": "card_payment", "source": "teller"},
            {"id": "t3", "account_id": "acc_1", "amount": 3000.0,
             "date": "2026-01-15", "category": "income", "type": "deposit", "source": "teller"},
            {"id": "t4", "account_id": "acc_1", "amount": -500.0,
             "date": "2026-01-25", "category": "transfer", "type": "transfer", "source": "teller"},
            {"id": "t5", "account_id": "acc_1", "amount": -75.0,
             "date": "2026-02-10", "category": "dining", "type": "card_payment", "source": "teller"},
            {"id": "t6", "account_id": "acc_1", "amount": 3000.0,
             "date": "2026-02-15", "category": "income", "type": "deposit", "source": "teller"},
        ]
        self.db.insert_transactions(transactions)

    def test_spending_summary(self):
        result = self.db.get_spending_summary("2026-01-01", "2026-01-31")
        assert len(result) == 2  # dining + groceries (transfer excluded by default)
        assert result[0]["category"] == "dining"
        assert result[0]["total"] == -50.0

    def test_spending_summary_include_transfers(self):
        result = self.db.get_spending_summary(
            "2026-01-01", "2026-01-31", exclude_transfers=False
        )
        assert len(result) == 3  # dining + groceries + transfer

    def test_cash_flow(self):
        result = self.db.get_cash_flow("2026-01-01", "2026-01-31")
        assert result["income"] == 3000.0
        assert result["expenses"] == -580.0  # 50 + 30 + 500
        assert result["net"] == 2420.0

    def test_monthly_trend(self):
        result = self.db.get_monthly_trend(months=2)
        assert len(result) == 2
        # Most recent month first
        assert result[0]["month"] == "2026-02"
        assert result[0]["income"] == 3000.0
        assert result[0]["expenses"] == -75.0


class TestBalances:
    def test_save_and_get_balance(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository",
        )
        db.save_balance("acc_1", available=1500.0, ledger=1600.0)
        balances = db.get_balances()
        assert len(balances) == 1
        assert balances[0]["available"] == 1500.0
        assert balances[0]["ledger"] == 1600.0

    def test_get_balances_excludes_venmo(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository",
        )
        db.upsert_account(
            id="acc_2", source="venmo", institution="Venmo",
            name="Venmo", type="depository",
        )
        db.save_balance("acc_1", available=1000.0)
        balances = db.get_balances()
        assert len(balances) == 1
        assert balances[0]["account_id"] == "acc_1"

    def test_balance_history(self, tmp_db):
        db = Database(tmp_db)
        db.upsert_account(
            id="acc_1", source="teller", institution="Chase",
            name="Checking", type="depository",
        )
        db.save_balance("acc_1", available=1000.0)
        db.save_balance("acc_1", available=1500.0)
        # get_balances returns only latest
        balances = db.get_balances()
        assert balances[0]["available"] == 1500.0


class TestEnrollments:
    def test_save_enrollment(self, tmp_db):
        db = Database(tmp_db)
        db.save_enrollment("enr_1", "tok_abc", "Chase")
        enrollments = db.get_active_enrollments()
        assert len(enrollments) == 1
        assert enrollments[0]["access_token"] == "tok_abc"

    def test_disconnect_enrollment(self, tmp_db):
        db = Database(tmp_db)
        db.save_enrollment("enr_1", "tok_abc", "Chase")
        db.disconnect_enrollment("enr_1")
        enrollments = db.get_active_enrollments()
        assert len(enrollments) == 0


class TestSyncLog:
    def test_log_sync(self, tmp_db):
        db = Database(tmp_db)
        sync_id = db.start_sync_log("teller")
        db.complete_sync_log(sync_id, accounts_synced=2, transactions_synced=50)
        row = db.execute("SELECT * FROM sync_log WHERE id = ?", (sync_id,)).fetchone()
        assert row is not None

    def test_last_sync_date(self, tmp_db):
        db = Database(tmp_db)
        assert db.get_last_sync_date("teller") is None
        sync_id = db.start_sync_log("teller")
        db.complete_sync_log(sync_id, accounts_synced=1, transactions_synced=10)
        assert db.get_last_sync_date("teller") is not None
