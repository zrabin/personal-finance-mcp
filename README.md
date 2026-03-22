# Personal Finance MCP Server

A Model Context Protocol (MCP) server that connects to your financial accounts and lets Claude answer questions about your finances.

## Features

- **Chase bank accounts & credit cards** via Teller API (free for personal use)
- **Venmo transactions** via CSV import
- **Local SQLite storage** — your data stays on your machine
- **Rich query tools** — spending summaries, cash flow, monthly trends, transaction search

## Quick Start

### 1. Install

```bash
git clone <repo-url>
cd personal_finance_mcp
pip3 install -e ".[dev]"
```

### 2. Set Up Teller (for Chase)

1. Sign up at [teller.io](https://teller.io) (free for personal use)
2. Create an application and download your certificate and private key
3. Store the certs in a secure location outside the repo:

```bash
mkdir -p ~/.finance_mcp/certs
cp /path/to/certificate.pem ~/.finance_mcp/certs/
cp /path/to/private_key.pem ~/.finance_mcp/certs/
chmod 600 ~/.finance_mcp/certs/*.pem
```

### 3. Configure Credentials

There are two ways to provide your Teller credentials:

#### Option A: Claude Desktop Config (recommended)

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "finance": {
      "command": "/absolute/path/to/python3",
      "args": ["-m", "personal_finance_mcp"],
      "env": {
        "TELLER_APPLICATION_ID": "your-app-id",
        "TELLER_CERTIFICATE": "/Users/yourname/.finance_mcp/certs/certificate.pem",
        "TELLER_PRIVATE_KEY": "/Users/yourname/.finance_mcp/certs/private_key.pem"
      }
    }
  }
}
```

**Important:** Use the absolute path to the Python binary where you installed the package. Claude Desktop doesn't inherit your shell's PATH, so `python3` may resolve to the system Python which won't have the package. Find the correct path with:

```bash
which python3
```

#### Option B: `.env` file

Useful for development or running the server outside of Claude Desktop:

```bash
cp .env.example .env
# Edit .env with your Teller credentials
```

### 4. Connect Your Accounts

In Claude, use:
- `enroll_account` — opens a browser to connect your Chase account
- `sync` — pulls latest transactions and balances
- `import_venmo_csv` — import a Venmo CSV export

### 5. Ask About Your Finances

Once connected, you can ask Claude things like:
- "How much did I spend on dining this month?"
- "What's my cash flow for the last 3 months?"
- "Show me my spending breakdown by category"
- "Search for all transactions at Whole Foods"

## Available Tools

| Tool | Description |
|------|-------------|
| `sync` | Pull latest data from connected bank accounts |
| `import_venmo_csv` | Import a Venmo CSV export |
| `get_accounts` | List connected accounts with balances |
| `get_balances` | Get current account balances |
| `get_transactions` | Search and filter transactions |
| `get_spending_summary` | Spending by category |
| `get_cash_flow` | Income vs expenses |
| `get_monthly_trend` | Month-over-month trends |
| `enroll_account` | Connect a new bank account |

## Venmo-Only Mode

Don't have a Teller account? You can still use the server with just Venmo CSV imports — no Teller credentials needed.

## Development

```bash
pip3 install -e ".[dev]"
python3 -m pytest -v
```

## Security

- All data stored locally in SQLite (default: `~/.finance_mcp/finance.db`)
- No data sent to external services (except Teller API for bank sync)
- Credentials never committed (`.env` in `.gitignore`)
- Enrollment server binds to localhost only
