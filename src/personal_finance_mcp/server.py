"""MCP server — tool registration and handlers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from personal_finance_mcp.config import Config, TellerConfigError
from personal_finance_mcp.db import Database
from personal_finance_mcp.teller import TellerClient, TellerAPIError
from personal_finance_mcp.venmo import parse_venmo_csv, VenmoParseError, VENMO_ACCOUNT_ID
from personal_finance_mcp.enroll.handler import run_enrollment


def create_server(db_path: str | None = None) -> Server:
    """Create and configure the MCP server."""
    config = Config()
    if db_path:
        config.db_path = db_path
    db = Database(config.db_path)
    server = Server("personal-finance")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="sync",
                description="Sync latest transactions and balances from connected bank accounts (Teller)",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="import_venmo_csv",
                description="Import transactions from a Venmo CSV export file",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the Venmo CSV file",
                        },
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="get_accounts",
                description="List all connected financial accounts with current balances",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["teller", "venmo"],
                            "description": "Filter by data source",
                        },
                    },
                },
            ),
            Tool(
                name="get_balances",
                description="Get current balances for Teller-connected accounts",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "Specific account ID (optional, returns all if omitted)",
                        },
                    },
                },
            ),
            Tool(
                name="get_transactions",
                description="Query transactions with flexible filters",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string", "description": "Filter by account"},
                        "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                        "category": {"type": "string", "description": "Filter by category"},
                        "min_amount": {"type": "number", "description": "Minimum amount"},
                        "max_amount": {"type": "number", "description": "Maximum amount"},
                        "search": {"type": "string", "description": "Search description and counterparty"},
                        "limit": {"type": "integer", "description": "Max results (default 50, 0=no limit)"},
                        "offset": {"type": "integer", "description": "Skip N results"},
                    },
                },
            ),
            Tool(
                name="get_spending_summary",
                description="Get spending breakdown by category for a time period",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                        "account_id": {"type": "string", "description": "Filter by account"},
                        "exclude_transfers": {"type": "boolean", "description": "Exclude transfers (default true)"},
                    },
                    "required": ["start_date", "end_date"],
                },
            ),
            Tool(
                name="get_cash_flow",
                description="Get income vs expenses summary for a time period",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                        "account_id": {"type": "string", "description": "Filter by account"},
                    },
                    "required": ["start_date", "end_date"],
                },
            ),
            Tool(
                name="get_monthly_trend",
                description="Get month-over-month income and spending trends",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "months": {"type": "integer", "description": "Number of months (default 6)"},
                        "account_id": {"type": "string", "description": "Filter by account"},
                    },
                },
            ),
            Tool(
                name="enroll_account",
                description="Connect a new bank account via Teller Connect (opens browser)",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "sync":
                result = await _handle_sync(config, db)
            elif name == "import_venmo_csv":
                result = _handle_import_venmo(db, arguments)
            elif name == "get_accounts":
                result = _handle_get_accounts(db, arguments)
            elif name == "get_balances":
                result = _handle_get_balances(db, arguments)
            elif name == "get_transactions":
                result = _handle_get_transactions(db, arguments)
            elif name == "get_spending_summary":
                result = _handle_get_spending_summary(db, arguments)
            elif name == "get_cash_flow":
                result = _handle_get_cash_flow(db, arguments)
            elif name == "get_monthly_trend":
                result = _handle_get_monthly_trend(db, arguments)
            elif name == "enroll_account":
                result = await _handle_enroll(config, db)
            else:
                result = {"error": f"Unknown tool: {name}"}
        except TellerConfigError as e:
            result = {"error": str(e)}
        except TellerAPIError as e:
            result = {"error": str(e)}
        except VenmoParseError as e:
            result = {"error": str(e)}
        except Exception as e:
            result = {"error": f"Unexpected error: {e}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


async def _handle_sync(config: Config, db: Database) -> dict:
    config.validate_teller()
    client = TellerClient(config.teller_certificate, config.teller_private_key)
    enrollments = db.get_active_enrollments()

    if not enrollments:
        return {"error": "No active enrollments. Use enroll_account first."}

    sync_id = db.start_sync_log("teller")
    total_accounts = 0
    total_transactions = 0
    errors: list[str] = []

    # Determine sync start date
    last_sync = db.get_last_sync_date("teller")
    from_date = None
    if last_sync:
        last_dt = datetime.fromisoformat(last_sync)
        from_date = (last_dt - timedelta(days=3)).strftime("%Y-%m-%d")

    for enrollment in enrollments:
        token = enrollment["access_token"]
        try:
            accounts = await client.get_accounts(token)
            for account in accounts:
                db.upsert_account(
                    id=account["id"],
                    source="teller",
                    institution=account["institution"],
                    name=account["name"],
                    type=account["type"],
                    subtype=account.get("subtype"),
                    last_four=account.get("last_four"),
                    enrollment_id=account.get("enrollment_id"),
                    status=account.get("status", "open"),
                )
                total_accounts += 1

                # Fetch balances
                try:
                    balance = await client.get_account_balances(token, account["id"])
                    db.save_balance(
                        account["id"],
                        available=balance.get("available"),
                        ledger=balance.get("ledger"),
                    )
                except TellerAPIError as e:
                    errors.append(f"Balance fetch failed for {account['name']}: {e}")

                # Fetch transactions
                try:
                    transactions = await client.get_transactions(
                        token, account["id"], from_date=from_date
                    )
                    inserted = db.insert_transactions(transactions)
                    total_transactions += inserted
                except TellerAPIError as e:
                    errors.append(f"Transaction fetch failed for {account['name']}: {e}")

        except TellerAPIError as e:
            if e.status_code == 401:
                db.disconnect_enrollment(enrollment["id"])
                errors.append(
                    f"Enrollment {enrollment['id']} disconnected. "
                    "Use enroll_account to reconnect."
                )
            else:
                errors.append(f"Error syncing enrollment {enrollment['id']}: {e}")

    status = "success" if not errors else "partial"
    error_msg = "; ".join(errors) if errors else None
    db.complete_sync_log(
        sync_id,
        accounts_synced=total_accounts,
        transactions_synced=total_transactions,
        status=status,
        error=error_msg,
    )

    result: dict[str, Any] = {
        "status": status,
        "accounts_synced": total_accounts,
        "new_transactions": total_transactions,
    }
    if errors:
        result["errors"] = errors
    return result


def _handle_import_venmo(db: Database, arguments: dict) -> dict:
    file_path = arguments["file_path"]
    transactions = parse_venmo_csv(file_path)

    if not transactions:
        return {"status": "success", "message": "No transactions found in CSV"}

    db.upsert_account(
        id=VENMO_ACCOUNT_ID,
        source="venmo",
        institution="Venmo",
        name="Venmo",
        type="depository",
        subtype="checking",
    )

    inserted = db.insert_transactions(transactions)
    return {
        "status": "success",
        "total_in_file": len(transactions),
        "new_transactions": inserted,
        "duplicates_skipped": len(transactions) - inserted,
    }


def _handle_get_accounts(db: Database, arguments: dict) -> dict:
    source = arguments.get("source")
    accounts = db.get_accounts(source=source)
    return {"accounts": accounts, "count": len(accounts)}


def _handle_get_balances(db: Database, arguments: dict) -> dict:
    account_id = arguments.get("account_id")
    balances = db.get_balances(account_id=account_id)
    return {"balances": balances, "count": len(balances)}


def _handle_get_transactions(db: Database, arguments: dict) -> dict:
    return db.get_transactions(
        account_id=arguments.get("account_id"),
        start_date=arguments.get("start_date"),
        end_date=arguments.get("end_date"),
        category=arguments.get("category"),
        min_amount=arguments.get("min_amount"),
        max_amount=arguments.get("max_amount"),
        search=arguments.get("search"),
        limit=arguments.get("limit", 50),
        offset=arguments.get("offset", 0),
    )


def _handle_get_spending_summary(db: Database, arguments: dict) -> dict:
    categories = db.get_spending_summary(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        account_id=arguments.get("account_id"),
        exclude_transfers=arguments.get("exclude_transfers", True),
    )
    total = sum(c["total"] for c in categories)
    return {"categories": categories, "total_spending": total}


def _handle_get_cash_flow(db: Database, arguments: dict) -> dict:
    return db.get_cash_flow(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        account_id=arguments.get("account_id"),
    )


def _handle_get_monthly_trend(db: Database, arguments: dict) -> dict:
    months = db.get_monthly_trend(
        months=arguments.get("months", 6),
        account_id=arguments.get("account_id"),
    )
    return {"months": months}


async def _handle_enroll(config: Config, db: Database) -> dict:
    config.validate_teller()
    enrollment_data = await run_enrollment(
        config.teller_app_id, config.enroll_port
    )

    access_token = enrollment_data.get("accessToken")
    enrollment = enrollment_data.get("enrollment", {})
    institution = enrollment_data.get("institution", {})

    if not access_token or not enrollment.get("id"):
        return {"error": "Enrollment completed but missing required data"}

    db.save_enrollment(
        enrollment_id=enrollment["id"],
        access_token=access_token,
        institution=institution.get("name", "Unknown"),
    )

    return {
        "status": "success",
        "enrollment_id": enrollment["id"],
        "institution": institution.get("name", "Unknown"),
        "message": "Account connected! Run sync to pull transactions.",
    }


def main() -> None:
    """Run the MCP server."""
    async def _run() -> None:
        server = create_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_run())
