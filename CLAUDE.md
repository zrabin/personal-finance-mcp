# CLAUDE.md — Personal Finance MCP Server

## Project Overview
Python MCP server for personal finance. Connects to Chase bank accounts/credit cards via Teller API and imports Venmo transactions via CSV. Stores everything in local SQLite.

## Tech Stack
- Python 3.11+, `mcp` SDK, `httpx`, `sqlite3`, `python-dotenv`
- Tests: `pytest`, `pytest-asyncio`

## Architecture
- `src/personal_finance_mcp/config.py` — env var config, lazy Teller validation
- `src/personal_finance_mcp/db.py` — SQLite schema, CRUD, aggregation queries
- `src/personal_finance_mcp/teller.py` — Teller API client (async, cert-based auth)
- `src/personal_finance_mcp/venmo.py` — Venmo CSV parser
- `src/personal_finance_mcp/enroll/` — Local HTTP server for Teller Connect enrollment
- `src/personal_finance_mcp/server.py` — MCP tool registration and handlers

## Commands
- Run server: `python3 -m personal_finance_mcp`
- Run tests: `python3 -m pytest -v`
- Install: `pip3 install -e ".[dev]"`

## Conventions
- All transaction amounts: negative = money out, positive = money in
- Teller config is lazy-validated (server starts without it for Venmo-only use)
- Transaction dedup: INSERT OR IGNORE on primary key (Teller ID or Venmo CSV ID)
- Balance snapshots: new row per sync, latest determined by MAX(as_of)

## Testing
- Unit tests for each module in `tests/`
- Teller client tests use mocked HTTP responses
- DB tests use temporary SQLite databases (tmp_path fixture)
- Test fixtures in `tests/fixtures/`
- `clean_env` fixture (autouse) prevents .env leakage into tests
