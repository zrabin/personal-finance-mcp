"""Teller API client — accounts, balances, transactions."""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any

import httpx


TELLER_API_BASE = "https://api.teller.io"


# Retry configuration for rate-limited requests
_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 1.0


class TellerAPIError(Exception):
    """Raised when Teller API returns an error."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Teller API error ({status_code}): {message}")


class TellerClient:
    """Client for the Teller API using certificate-based auth."""

    def __init__(self, certificate: str, private_key: str) -> None:
        self.certificate = certificate
        self.private_key = private_key

    def _get_client(self, access_token: str) -> httpx.AsyncClient:
        ssl_context = ssl.create_default_context()
        ssl_context.load_cert_chain(self.certificate, self.private_key)
        return httpx.AsyncClient(
            base_url=TELLER_API_BASE,
            auth=(access_token, ""),
            verify=ssl_context,
            timeout=30.0,
        )

    async def _request(
        self, access_token: str, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        async with self._get_client(access_token) as client:
            response = await client.request(method, path, **kwargs)
            return response

    async def _request_with_retry(
        self, access_token: str, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff retry on 429 responses."""
        for attempt in range(_MAX_RETRIES):
            response = await self._request(access_token, method, path, **kwargs)
            if response.status_code != 429:
                return response
            # Last attempt — don't sleep, just fall through to return
            if attempt < _MAX_RETRIES - 1:
                wait = _BASE_BACKOFF_SECONDS * (2 ** attempt)
                await asyncio.sleep(wait)
        # All retries exhausted — return the 429 so _check_response raises
        return response

    def _check_response(self, response: httpx.Response) -> None:
        if response.status_code == 429:
            raise TellerAPIError(429, "Rate limited — try again later")
        if response.status_code >= 400:
            try:
                body = response.json()
                msg = body.get("error", {}).get("message", response.text)
            except Exception:
                msg = response.text
            raise TellerAPIError(response.status_code, msg)

    async def get_accounts(self, access_token: str) -> list[dict]:
        response = await self._request_with_retry(access_token, "GET", "/accounts")
        self._check_response(response)
        raw = response.json()
        return [
            {
                "id": a["id"],
                "enrollment_id": a.get("enrollment_id"),
                "institution": a.get("institution", {}).get("name", "Unknown"),
                "name": a.get("name", ""),
                "type": a.get("type", ""),
                "subtype": a.get("subtype"),
                "last_four": a.get("last_four"),
                "status": a.get("status", "open"),
            }
            for a in raw
        ]

    async def get_account_balances(
        self, access_token: str, account_id: str
    ) -> dict:
        response = await self._request_with_retry(
            access_token, "GET", f"/accounts/{account_id}/balances"
        )
        self._check_response(response)
        raw = response.json()
        return {
            "available": float(raw["available"]) if raw.get("available") else None,
            "ledger": float(raw["ledger"]) if raw.get("ledger") else None,
        }

    async def get_transactions(
        self,
        access_token: str,
        account_id: str,
        from_date: str | None = None,
        count: int = 250,
    ) -> list[dict]:
        """Fetch transactions, handling pagination."""
        all_transactions: list[dict] = []
        params: dict[str, Any] = {"count": count}
        if from_date:
            params["from_date"] = from_date

        while True:
            response = await self._request_with_retry(
                access_token, "GET",
                f"/accounts/{account_id}/transactions",
                params=params,
            )
            self._check_response(response)
            raw = response.json()
            if not raw:
                break

            for t in raw:
                all_transactions.append(self._normalize_transaction(t))

            # Pagination: use last transaction ID
            if len(raw) < count:
                break
            params["from_id"] = raw[-1]["id"]

        return all_transactions

    def _normalize_transaction(self, raw: dict) -> dict:
        """Normalize a Teller transaction to unified schema."""
        # Teller API returns signed amounts: negative = money out, positive = money in
        amount = float(raw.get("amount", "0"))
        txn_type = raw.get("type", "")

        details = raw.get("details") or {}
        counterparty = raw.get("counterparty") or {}

        return {
            "id": raw["id"],
            "account_id": raw["account_id"],
            "amount": amount,
            "date": raw.get("date", ""),
            "description": raw.get("description", ""),
            "category": details.get("category"),
            "type": txn_type,
            "status": raw.get("status", ""),
            "counterparty": counterparty.get("name"),
            "source": "teller",
            "raw_data": json.dumps(raw),
        }
