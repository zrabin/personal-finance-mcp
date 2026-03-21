# PROGRESS.md — Build Progress

## Completed
- [x] Design spec written and reviewed
- [x] Implementation plan written and reviewed
- [x] Project scaffolding + config management (6 tests)
- [x] Database layer — schema, CRUD, aggregations, balance snapshots, enrollments, sync log (20 tests)
- [x] Venmo CSV parser — normalization, edge cases, dedup by native ID (8 tests)
- [x] Teller API client — cert auth, pagination, sign normalization, 429 retry/backoff (8 tests)
- [x] Enrollment handler — local HTTP server for Teller Connect, asyncio-safe threading
- [x] MCP server — all 9 tool handlers with error handling (13 tests)
- [x] Project documentation (CLAUDE.md, README.md, PROGRESS.md)

## Decisions Made
- **Teller for Chase, CSV for Venmo** — Teller doesn't support Venmo
- **SQLite local storage** — enables fast queries, offline use, balance history
- **Lazy Teller config** — server starts without Teller creds for Venmo-only use
- **Snapshot-based balances** — each sync appends a new balance row
- **Transaction dedup via INSERT OR IGNORE** — uses native IDs from both sources
- **Exponential backoff on 429** — 3 retries with 1s/2s/4s delays
- **autouse clean_env fixture** — prevents .env leakage into all tests

## Test Suite
- 55 tests total, all passing
- Config: 6, DB: 20, Venmo: 8, Teller: 8, Server: 13

## Next Steps for User
- [ ] Sign up for Teller at teller.io (free)
- [ ] Get API credentials (application ID, certificate, private key)
- [ ] Create .env file with credentials
- [ ] Add MCP server to Claude config
- [ ] Run enroll_account to connect Chase
- [ ] Run sync to pull transactions
- [ ] Download Venmo CSV and import
