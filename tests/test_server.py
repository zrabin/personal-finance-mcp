import json
import pytest
from unittest.mock import AsyncMock, patch
from personal_finance_mcp.server import create_server
from personal_finance_mcp.db import Database


@pytest.fixture
def db(tmp_db):
    database = Database(tmp_db)
    database.upsert_account(
        id="acc_1", source="teller", institution="Chase",
        name="Chase Checking", type="depository", subtype="checking",
        last_four="1234", enrollment_id="enr_1",
    )
    database.upsert_account(
        id="venmo_default", source="venmo", institution="Venmo",
        name="Venmo", type="depository", subtype="checking",
    )
    database.save_balance("acc_1", available=2500.0, ledger=2600.0)
    database.insert_transactions([
        {"id": "t1", "account_id": "acc_1", "amount": -50.0,
         "date": "2026-03-10", "description": "Restaurant",
         "category": "dining", "type": "card_payment",
         "status": "posted", "counterparty": "Sushi Place", "source": "teller"},
        {"id": "t2", "account_id": "acc_1", "amount": 3000.0,
         "date": "2026-03-01", "description": "Payroll",
         "category": "income", "type": "deposit",
         "status": "posted", "source": "teller"},
        {"id": "t3", "account_id": "acc_1", "amount": -120.0,
         "date": "2026-03-05", "description": "Grocery Store",
         "category": "groceries", "type": "card_payment",
         "status": "posted", "counterparty": "Whole Foods", "source": "teller"},
        {"id": "t4", "account_id": "acc_1", "amount": -500.0,
         "date": "2026-03-15", "description": "Credit Card Payment",
         "category": "transfer", "type": "transfer",
         "status": "posted", "source": "teller"},
    ])
    return database


class TestServerCreation:
    def test_creates_server_with_tools(self, tmp_db):
        server = create_server(tmp_db)
        assert server is not None


class TestToolHandlers:
    """Test tool handlers via the internal handler functions."""

    def test_get_accounts(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_accounts
        result = _handle_get_accounts(db, {})
        assert result["count"] == 2

    def test_get_accounts_filter_source(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_accounts
        result = _handle_get_accounts(db, {"source": "teller"})
        assert result["count"] == 1
        assert result["accounts"][0]["institution"] == "Chase"

    def test_get_balances(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_balances
        result = _handle_get_balances(db, {})
        assert result["count"] == 1
        assert result["balances"][0]["available"] == 2500.0

    def test_get_balances_excludes_venmo(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_balances
        result = _handle_get_balances(db, {})
        account_ids = [b["account_id"] for b in result["balances"]]
        assert "venmo_default" not in account_ids

    def test_get_transactions(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_transactions
        result = _handle_get_transactions(db, {})
        assert result["total"] == 4

    def test_get_transactions_with_search(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_transactions
        result = _handle_get_transactions(db, {"search": "sushi"})
        assert result["total"] == 1

    def test_get_transactions_with_date_range(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_transactions
        result = _handle_get_transactions(db, {
            "start_date": "2026-03-01", "end_date": "2026-03-10"
        })
        assert result["total"] == 3

    def test_get_spending_summary(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_spending_summary
        result = _handle_get_spending_summary(db, {
            "start_date": "2026-03-01", "end_date": "2026-03-31"
        })
        # Should exclude transfers by default
        categories = {c["category"] for c in result["categories"]}
        assert "transfer" not in categories
        assert "dining" in categories
        assert "groceries" in categories

    def test_get_spending_summary_include_transfers(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_spending_summary
        result = _handle_get_spending_summary(db, {
            "start_date": "2026-03-01", "end_date": "2026-03-31",
            "exclude_transfers": False,
        })
        categories = {c["category"] for c in result["categories"]}
        assert "transfer" in categories

    def test_get_cash_flow(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_cash_flow
        result = _handle_get_cash_flow(db, {
            "start_date": "2026-03-01", "end_date": "2026-03-31"
        })
        assert result["income"] == 3000.0
        assert result["expenses"] == -670.0  # 50 + 120 + 500
        assert result["net"] == 2330.0

    def test_get_monthly_trend(self, db, tmp_db):
        from personal_finance_mcp.server import _handle_get_monthly_trend
        result = _handle_get_monthly_trend(db, {"months": 1})
        assert len(result["months"]) == 1
        assert result["months"][0]["month"] == "2026-03"

    def test_import_venmo_csv(self, db, tmp_db):
        import os
        from personal_finance_mcp.server import _handle_import_venmo
        fixture_path = os.path.join(
            os.path.dirname(__file__), "fixtures", "venmo_standard.csv"
        )
        result = _handle_import_venmo(db, {"file_path": fixture_path})
        assert result["status"] == "success"
        assert result["new_transactions"] == 3
        # Import again — should deduplicate
        result2 = _handle_import_venmo(db, {"file_path": fixture_path})
        assert result2["new_transactions"] == 0
        assert result2["duplicates_skipped"] == 3
