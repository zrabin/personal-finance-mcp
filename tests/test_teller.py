import json
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from personal_finance_mcp.teller import TellerClient, TellerAPIError


@pytest.fixture
def teller_client(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("fake-cert")
    key.write_text("fake-key")
    return TellerClient(
        certificate=str(cert),
        private_key=str(key),
    )


class TestTellerClient:
    @pytest.mark.asyncio
    async def test_get_accounts(self, teller_client):
        mock_response = httpx.Response(
            200,
            json=[
                {
                    "id": "acc_123",
                    "enrollment_id": "enr_456",
                    "institution": {"name": "Chase"},
                    "name": "Chase Checking",
                    "type": "depository",
                    "subtype": "checking",
                    "last_four": "1234",
                    "status": "open",
                }
            ],
        )
        with patch.object(
            teller_client, "_request", return_value=mock_response
        ):
            accounts = await teller_client.get_accounts("tok_test")
            assert len(accounts) == 1
            assert accounts[0]["id"] == "acc_123"

    @pytest.mark.asyncio
    async def test_get_balances(self, teller_client):
        mock_response = httpx.Response(
            200,
            json={"available": "1500.50", "ledger": "1600.00"},
        )
        with patch.object(
            teller_client, "_request", return_value=mock_response
        ):
            balance = await teller_client.get_account_balances(
                "tok_test", "acc_123"
            )
            assert balance["available"] == 1500.50
            assert balance["ledger"] == 1600.00

    @pytest.mark.asyncio
    async def test_get_transactions(self, teller_client):
        mock_response = httpx.Response(
            200,
            json=[
                {
                    "id": "txn_1",
                    "account_id": "acc_123",
                    "amount": "25.50",
                    "date": "2026-03-15",
                    "description": "Coffee Shop",
                    "details": {"category": "dining"},
                    "type": "card_payment",
                    "status": "posted",
                    "counterparty": {"name": "Blue Bottle"},
                }
            ],
        )
        with patch.object(
            teller_client, "_request", return_value=mock_response
        ):
            transactions = await teller_client.get_transactions(
                "tok_test", "acc_123"
            )
            assert len(transactions) == 1
            assert transactions[0]["amount"] == -25.50  # card_payment = debit
            assert transactions[0]["category"] == "dining"

    @pytest.mark.asyncio
    async def test_api_error(self, teller_client):
        mock_response = httpx.Response(401, json={"error": {"message": "Unauthorized"}})
        with patch.object(
            teller_client, "_request", return_value=mock_response
        ):
            with pytest.raises(TellerAPIError, match="401"):
                await teller_client.get_accounts("bad_token")

    @pytest.mark.asyncio
    async def test_normalize_debit_types(self, teller_client):
        """Debit transaction types should produce negative amounts."""
        mock_response = httpx.Response(
            200,
            json=[
                {"id": "t1", "account_id": "a1", "amount": "100.00",
                 "date": "2026-01-01", "description": "Fee", "details": {},
                 "type": "fee", "status": "posted", "counterparty": {}},
                {"id": "t2", "account_id": "a1", "amount": "50.00",
                 "date": "2026-01-01", "description": "Deposit", "details": {},
                 "type": "deposit", "status": "posted", "counterparty": {}},
            ],
        )
        with patch.object(
            teller_client, "_request", return_value=mock_response
        ):
            txns = await teller_client.get_transactions("tok", "a1")
            assert txns[0]["amount"] == -100.0  # fee = debit
            assert txns[1]["amount"] == 50.0    # deposit = credit


class TestRetryOn429:
    @pytest.mark.asyncio
    async def test_retry_succeeds_after_429(self, teller_client):
        """Should retry on 429 and succeed when a subsequent attempt returns 200."""
        response_429 = httpx.Response(429, json={"error": {"message": "Rate limited"}})
        response_200 = httpx.Response(
            200,
            json=[{"id": "acc_1", "institution": {"name": "Chase"},
                   "name": "Checking", "type": "depository", "status": "open"}],
        )
        with patch.object(
            teller_client, "_request",
            new_callable=AsyncMock,
            side_effect=[response_429, response_200],
        ):
            with patch("personal_finance_mcp.teller.asyncio.sleep", new_callable=AsyncMock):
                accounts = await teller_client.get_accounts("tok_test")
                assert len(accounts) == 1
                assert teller_client._request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self, teller_client):
        """Should raise TellerAPIError(429) after all retries are exhausted."""
        response_429 = httpx.Response(429, json={"error": {"message": "Rate limited"}})
        with patch.object(
            teller_client, "_request",
            new_callable=AsyncMock,
            return_value=response_429,
        ):
            with patch("personal_finance_mcp.teller.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(TellerAPIError, match="429"):
                    await teller_client.get_accounts("tok_test")
                assert teller_client._request.call_count == 3

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self, teller_client):
        """Should use exponential backoff: 1s, 2s between retries."""
        response_429 = httpx.Response(429, json={"error": {"message": "Rate limited"}})
        with patch.object(
            teller_client, "_request",
            new_callable=AsyncMock,
            return_value=response_429,
        ):
            mock_sleep = AsyncMock()
            with patch("personal_finance_mcp.teller.asyncio.sleep", mock_sleep):
                with pytest.raises(TellerAPIError):
                    await teller_client.get_accounts("tok_test")
                # Should sleep twice (after 1st and 2nd attempts, not after 3rd)
                assert mock_sleep.call_count == 2
                mock_sleep.assert_any_call(1.0)
                mock_sleep.assert_any_call(2.0)
