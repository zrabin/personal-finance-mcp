import pytest
from pathlib import Path
from personal_finance_mcp.venmo import parse_venmo_csv, VenmoParseError

FIXTURES = Path(__file__).parent / "fixtures"


class TestVenmoParser:
    def test_parse_standard_csv(self):
        transactions = parse_venmo_csv(str(FIXTURES / "venmo_standard.csv"))
        assert len(transactions) == 3

    def test_transaction_fields(self):
        transactions = parse_venmo_csv(str(FIXTURES / "venmo_standard.csv"))
        t = transactions[0]
        assert t["id"] == "t_venmo_001"
        assert t["amount"] == -5.50
        assert t["date"] == "2026-01-15"
        assert t["description"] == "Coffee"
        assert t["counterparty"] == "Blue Bottle Coffee"
        assert t["type"] == "Payment"
        assert t["source"] == "venmo"

    def test_positive_amount(self):
        transactions = parse_venmo_csv(str(FIXTURES / "venmo_standard.csv"))
        charge = transactions[1]
        assert charge["amount"] == 25.00
        assert charge["counterparty"] == "Jane Doe"

    def test_comma_in_amount(self):
        transactions = parse_venmo_csv(str(FIXTURES / "venmo_standard.csv"))
        rent = transactions[2]
        assert rent["amount"] == -1200.00

    def test_edge_cases_skips_empty_rows(self):
        transactions = parse_venmo_csv(str(FIXTURES / "venmo_edge_cases.csv"))
        assert len(transactions) == 2  # empty row skipped

    def test_missing_counterparty(self):
        transactions = parse_venmo_csv(str(FIXTURES / "venmo_edge_cases.csv"))
        t = transactions[0]
        assert t["counterparty"] is None or t["counterparty"] == ""

    def test_file_not_found(self):
        with pytest.raises(VenmoParseError, match="not found"):
            parse_venmo_csv("/nonexistent/file.csv")

    def test_returns_account_info(self):
        transactions = parse_venmo_csv(str(FIXTURES / "venmo_standard.csv"))
        # All transactions should have account_id set to venmo account
        assert all(t["account_id"] == "venmo_default" for t in transactions)
