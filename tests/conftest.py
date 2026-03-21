"""Shared test fixtures."""

import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary database path."""
    return str(tmp_path / "test.db")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove all finance-related env vars to prevent .env leakage."""
    for key in [
        "TELLER_APPLICATION_ID",
        "TELLER_CERTIFICATE",
        "TELLER_PRIVATE_KEY",
        "FINANCE_DB_PATH",
        "FINANCE_PORT",
    ]:
        monkeypatch.delenv(key, raising=False)
