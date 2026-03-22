import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from personal_finance_mcp.server import create_server, _handle_sync
from personal_finance_mcp.config import Config
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


class TestSyncCreditCardSignFlip:
    """Credit card transactions should have signs flipped during sync."""

    @pytest.mark.asyncio
    async def test_credit_card_charges_become_negative(self, tmp_db):
        db = Database(tmp_db)
        db.save_enrollment("enr_1", "tok_test", "Chase")

        mock_accounts = [
            {"id": "cc_1", "enrollment_id": "enr_1",
             "institution": "Chase", "name": "Chase Sapphire",
             "type": "credit", "subtype": "credit_card",
             "last_four": "5678", "status": "open"},
        ]
        # Teller returns charges as positive on credit cards
        mock_transactions = [
            {"id": "cc_t1", "account_id": "cc_1", "amount": 75.0,
             "date": "2026-03-10", "description": "Restaurant",
             "category": "dining", "type": "card_payment",
             "status": "posted", "counterparty": "Sushi Place", "source": "teller"},
            {"id": "cc_t2", "account_id": "cc_1", "amount": -500.0,
             "date": "2026-03-15", "description": "Payment Thank You",
             "category": "transfer", "type": "ach",
             "status": "posted", "counterparty": None, "source": "teller"},
        ]

        config = MagicMock(spec=Config)
        config.teller_certificate = "cert.pem"
        config.teller_private_key = "key.pem"

        with patch("personal_finance_mcp.server.TellerClient") as MockClient:
            instance = MockClient.return_value
            instance.get_accounts = AsyncMock(return_value=mock_accounts)
            instance.get_account_balances = AsyncMock(
                return_value={"available": None, "ledger": 1200.0}
            )
            instance.get_transactions = AsyncMock(return_value=mock_transactions)

            result = await _handle_sync(config, db)

        assert result["status"] == "success"
        txns = db.get_transactions(account_id="cc_1")["transactions"]
        # Charge should be flipped: 75.0 → -75.0 (money out)
        charge = next(t for t in txns if t["id"] == "cc_t1")
        assert charge["amount"] == -75.0
        # Payment should be flipped: -500.0 → 500.0 (money in to card)
        payment = next(t for t in txns if t["id"] == "cc_t2")
        assert payment["amount"] == 500.0

    @pytest.mark.asyncio
    async def test_depository_accounts_not_flipped(self, tmp_db):
        db = Database(tmp_db)
        db.save_enrollment("enr_1", "tok_test", "Chase")

        mock_accounts = [
            {"id": "chk_1", "enrollment_id": "enr_1",
             "institution": "Chase", "name": "Chase Checking",
             "type": "depository", "subtype": "checking",
             "last_four": "1234", "status": "open"},
        ]
        mock_transactions = [
            {"id": "chk_t1", "account_id": "chk_1", "amount": -50.0,
             "date": "2026-03-10", "description": "Coffee",
             "category": "dining", "type": "card_payment",
             "status": "posted", "counterparty": "Starbucks", "source": "teller"},
        ]

        config = MagicMock(spec=Config)
        config.teller_certificate = "cert.pem"
        config.teller_private_key = "key.pem"

        with patch("personal_finance_mcp.server.TellerClient") as MockClient:
            instance = MockClient.return_value
            instance.get_accounts = AsyncMock(return_value=mock_accounts)
            instance.get_account_balances = AsyncMock(
                return_value={"available": 2500.0, "ledger": 2600.0}
            )
            instance.get_transactions = AsyncMock(return_value=mock_transactions)

            result = await _handle_sync(config, db)

        txns = db.get_transactions(account_id="chk_1")["transactions"]
        assert txns[0]["amount"] == -50.0  # unchanged
