# Personal Finance MCP Server — Design Spec

## Overview

A Python MCP server that connects to financial accounts and exposes tools for querying transaction history, balances, cash flow, and spending analysis. Designed to run locally, with each user maintaining their own instance and credentials.

## Goals

1. Connect to Chase bank accounts and credit cards via Teller API
2. Import Venmo transaction history via CSV export
3. Normalize all financial data into a unified local SQLite database
4. Expose MCP tools that enable Claude to answer financial questions
5. Be distributable — any user can clone, configure, and run with their own accounts

## Non-Goals

- Web UI (beyond Teller enrollment page)
- Scheduled/automatic syncs
- Multi-user authentication
- Investment/crypto account support
- Budgeting or category management

## Architecture

### System Diagram

```
Teller API ──sync──► SQLite ◄──import──── Venmo CSV
                        │
                   MCP Server
                        │
                   Claude (via MCP protocol)
```

Single-process Python application. No external services beyond Teller API.

### Components

#### MCP Server (`src/server.py`)
- Registers all MCP tools
- Handles the MCP protocol lifecycle via `mcp` Python SDK
- Entry point for the application

#### Teller Client (`src/teller.py`)
- Wraps Teller API endpoints: accounts, balances, transactions
- Handles authentication via certificate + API key
- Manages pagination for transaction fetching
- Detects and reports enrollment disconnections

#### Venmo CSV Parser (`src/venmo.py`)
- Parses Venmo's CSV export format
- Normalizes transactions to match the unified schema
- Reports parse errors with row/field specifics
- Handles Venmo-specific fields (payment/charge, notes, counterparty)

#### Database Layer (`src/db.py`)
- SQLite via Python's built-in `sqlite3` module
- Schema creation and versioned migrations via `schema_version` table
- Sequential migration functions (v1→v2, v2→v3, etc.) applied automatically on startup
- All query logic for MCP tools (spending summaries, cash flow, trends)
- Sync bookkeeping (last sync time, deduplication)

#### Enrollment Handler (`src/enroll/`)
- Lightweight local HTTP server + HTML page
- Embeds Teller Connect JS widget
- Captures access token on successful enrollment
- Stores token securely in local config

#### Configuration (`src/config.py`)
- Reads from environment variables and/or `.env` file
- Settings: Teller API key, Teller certificate path, Teller private key path, DB file path
- Validates required config on startup

## Data Model

### `accounts` table
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Teller account ID or generated ID for Venmo |
| source | TEXT | "teller" or "venmo" |
| institution | TEXT | e.g., "Chase", "Venmo" |
| name | TEXT | Account name (e.g., "Chase Checking") |
| type | TEXT | "depository" or "credit" |
| subtype | TEXT | "checking", "savings", "credit_card" |
| last_four | TEXT | Last 4 digits of account number |
| status | TEXT | "open", "closed" |
| enrollment_id | TEXT | Teller enrollment ID (null for Venmo) |
| created_at | TEXT | ISO timestamp |
| updated_at | TEXT | ISO timestamp |

### `balances` table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| account_id | TEXT FK | References accounts.id |
| available | REAL | Available balance (null if unknown) |
| ledger | REAL | Ledger/total balance (null if unknown) |
| as_of | TEXT | ISO timestamp when balance was fetched |

Balances are snapshot-based — each sync appends a new row, enabling balance history over time. Venmo accounts do NOT have balance records; `get_balances` excludes Venmo accounts and `get_accounts` shows "N/A" for Venmo balance. The latest balance for Teller accounts is determined by `MAX(as_of)`.

### `transactions` table
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Teller transaction ID or Venmo CSV ID |
| account_id | TEXT FK | References accounts.id |
| amount | REAL | Signed amount: negative = money out, positive = money in |
| date | TEXT | Transaction date (YYYY-MM-DD) |
| description | TEXT | Transaction description |
| category | TEXT | Teller category or "uncategorized" |
| type | TEXT | "card_payment", "transfer", "atm", etc. |
| status | TEXT | "posted" or "pending" |
| counterparty | TEXT | Merchant or person name |
| source | TEXT | "teller" or "venmo" |
| raw_data | TEXT | JSON blob of original data |
| created_at | TEXT | ISO timestamp |

### Amount Sign Convention
All amounts use a unified sign convention: **negative = money leaving the user, positive = money entering**.
- **Teller**: Teller amounts are always positive strings. Sign is determined by the transaction `type` field — debits (card_payment, fee, etc.) become negative, credits (deposit, interest, etc.) stay positive.
- **Venmo**: Venmo CSV amounts include a sign (e.g., "- $50.00" for payments, "+ $25.00" for charges received). Parse the sign directly from the Amount column.

### `sync_log` table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| source | TEXT | "teller" or "venmo" |
| started_at | TEXT | ISO timestamp |
| completed_at | TEXT | ISO timestamp |
| accounts_synced | INTEGER | Count |
| transactions_synced | INTEGER | Count |
| status | TEXT | "success", "partial", "failed" |
| error | TEXT | Error message if failed |

### `enrollments` table
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Teller enrollment ID |
| access_token | TEXT | Teller access token |
| institution | TEXT | Institution name |
| created_at | TEXT | ISO timestamp |
| status | TEXT | "active", "disconnected" |

## MCP Tools

### `sync`
Pull latest data from all connected Teller accounts.
- **First sync**: Fetches all available transaction history
- **Subsequent syncs**: Fetches from `(last_sync_date - 3 days)` to catch late-posting transactions, deduplicates by transaction ID (INSERT OR IGNORE)
- Snapshots current balances into the `balances` table
- Reports sync results (accounts synced, new transactions)
- Detects and reports enrollment disconnections
- Implements exponential backoff on Teller rate-limit responses (HTTP 429)

### `import_venmo_csv`
Import transactions from a Venmo CSV export file.
- Parameters: `file_path` (string, required)
- Creates Venmo account if not exists
- Deduplicates by Venmo's native transaction ID from the CSV `ID` column
- Returns import summary (new transactions, duplicates skipped)

#### Expected Venmo CSV Columns
| CSV Column | Maps To | Notes |
|------------|---------|-------|
| ID | transactions.id | Native Venmo transaction ID, used for dedup |
| Datetime | transactions.date | Parsed to YYYY-MM-DD |
| Type | transactions.type | "Payment", "Charge", "Transfer" |
| Status | transactions.status | "Complete", "Pending", etc. |
| Note | transactions.description | Payment note/memo |
| From | transactions.counterparty | Sender (if receiving) |
| To | transactions.counterparty | Recipient (if sending) |
| Amount (total) | transactions.amount | Includes sign (- = sent, + = received) |

### `get_accounts`
List all connected accounts with current balances.
- Returns: account name, institution, type, last four, current balance
- Supports filtering by source ("teller", "venmo")

### `get_balances`
Get current balance for Teller-connected accounts.
- Parameters: `account_id` (string, optional)
- Returns: available balance, ledger balance, as-of timestamp
- Venmo accounts are excluded (no balance data available from CSV imports)

### `get_transactions`
Query transactions with flexible filters.
- Parameters:
  - `account_id` (string, optional)
  - `start_date` / `end_date` (string, optional, YYYY-MM-DD)
  - `category` (string, optional)
  - `min_amount` / `max_amount` (number, optional)
  - `search` (string, optional — searches description and counterparty)
  - `limit` (integer, optional, default 50, 0 = no limit)
  - `offset` (integer, optional, default 0)
- Returns: list of transactions matching filters, plus total count of matches (so Claude knows if results are truncated)

### `get_spending_summary`
Aggregated spending by category over a time period.
- Parameters: `start_date`, `end_date` (required), `account_id` (optional), `exclude_transfers` (boolean, optional, default true)
- Returns: category → total amount, sorted by amount descending
- Only includes outgoing transactions (negative amounts)
- When `exclude_transfers` is true, excludes transactions with type "transfer" to avoid counting inter-account movements (e.g., checking → credit card payments) as spending

### `get_cash_flow`
Income vs expenses summary.
- Parameters: `start_date`, `end_date` (required), `account_id` (optional)
- Returns: total income, total expenses, net cash flow

### `get_monthly_trend`
Month-over-month spending and income trends.
- Parameters: `months` (integer, optional, default 6), `account_id` (optional)
- Returns: list of { month, income, expenses, net } objects

### `enroll_account`
Start Teller Connect enrollment flow.
- Opens a local browser page with the Teller Connect widget
- User authenticates with their bank
- On success, saves access token and returns enrollment confirmation
- Returns URL for user to open if browser doesn't auto-open

## Configuration

### Environment Variables
| Variable | Required | Description |
|----------|----------|-------------|
| `TELLER_APPLICATION_ID` | No* | Teller application ID |
| `TELLER_CERTIFICATE` | No* | Path to Teller certificate file (.pem) |
| `TELLER_PRIVATE_KEY` | No* | Path to Teller private key file (.pem) |

\* Teller variables are validated lazily — only when a Teller-dependent tool is invoked (enroll, sync). The server starts without them, allowing Venmo-only usage.
| `FINANCE_DB_PATH` | No | SQLite database path (default: `~/.finance_mcp/finance.db`) |
| `FINANCE_PORT` | No | Port for enrollment server (default: 8765) |

### File Structure
```
personal_finance_mcp/
├── CLAUDE.md                    # Project rules and context
├── PROGRESS.md                  # Build progress log
├── README.md                    # Setup and usage instructions
├── pyproject.toml               # Python project config
├── src/
│   └── personal_finance_mcp/
│       ├── __init__.py
│       ├── __main__.py          # Entry point
│       ├── server.py            # MCP server + tool definitions
│       ├── teller.py            # Teller API client
│       ├── venmo.py             # Venmo CSV parser
│       ├── db.py                # Database layer
│       ├── config.py            # Configuration management
│       └── enroll/
│           ├── __init__.py
│           ├── handler.py       # Local HTTP server for enrollment
│           └── templates/
│               └── connect.html # Teller Connect widget page
├── tests/
│   ├── __init__.py
│   ├── test_db.py
│   ├── test_venmo.py
│   ├── test_teller.py
│   └── test_server.py
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-03-20-personal-finance-mcp-design.md
```

## Setup Flow (for any user)

1. Clone the repository
2. Install: `pip install -e .` (or `uv pip install -e .`)
3. Sign up for a free Teller account at teller.io
4. Download Teller certificate and private key
5. Set environment variables (or create `.env` file)
6. Add MCP server to Claude config:
   ```json
   {
     "mcpServers": {
       "finance": {
         "command": "python",
         "args": ["-m", "personal_finance_mcp"],
         "env": {
           "TELLER_APPLICATION_ID": "...",
           "TELLER_CERTIFICATE": "/path/to/certificate.pem",
           "TELLER_PRIVATE_KEY": "/path/to/private_key.pem"
         }
       }
     }
   }
   ```
7. Use `enroll_account` tool to connect bank accounts
8. Use `sync` tool to pull initial transaction data
9. Use `import_venmo_csv` tool to import Venmo history

## Error Handling

- **Teller API errors**: Surface HTTP status + error message as MCP tool error responses
- **Enrollment disconnections**: Detected during sync, reported with re-enrollment instructions
- **Venmo CSV parse errors**: Report row number and field that failed, skip malformed rows
- **SQLite errors**: Caught and reported with context, never swallowed
- **Missing config**: Clear error message on startup listing which variables are missing

## Testing Strategy

- **Unit tests**: Venmo CSV parsing, DB queries, aggregation logic, config validation
- **Integration tests**: Teller sandbox API (Teller provides a free sandbox environment)
- **MCP tool tests**: End-to-end with mocked data sources
- **Test fixtures**: Sample Venmo CSVs, sample Teller API responses

## Security Considerations

- Access tokens stored locally in SQLite enrollments table. Database file created with restrictive permissions (0600). Users on shared machines should be aware that tokens are not encrypted at rest.
- `.env` file in `.gitignore` — credentials never committed
- Teller certificate and key paths referenced, not embedded
- No network exposure beyond the temporary local enrollment server
- Enrollment server binds to localhost only
