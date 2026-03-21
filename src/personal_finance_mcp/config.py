"""Configuration management — loads from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


class TellerConfigError(Exception):
    """Raised when Teller configuration is missing or invalid."""


class Config:
    """Application configuration. Teller settings validated lazily."""

    def __init__(self) -> None:
        load_dotenv()
        self.db_path: str = os.environ.get(
            "FINANCE_DB_PATH",
            str(Path.home() / ".finance_mcp" / "finance.db"),
        )
        self.enroll_port: int = int(os.environ.get("FINANCE_PORT", "8765"))
        self._teller_app_id: str | None = os.environ.get("TELLER_APPLICATION_ID")
        self._teller_cert: str | None = os.environ.get("TELLER_CERTIFICATE")
        self._teller_key: str | None = os.environ.get("TELLER_PRIVATE_KEY")

    @property
    def teller_app_id(self) -> str:
        self.validate_teller()
        return self._teller_app_id  # type: ignore[return-value]

    @property
    def teller_certificate(self) -> str:
        self.validate_teller()
        return self._teller_cert  # type: ignore[return-value]

    @property
    def teller_private_key(self) -> str:
        self.validate_teller()
        return self._teller_key  # type: ignore[return-value]

    def validate_teller(self) -> None:
        """Validate Teller config. Raises TellerConfigError if incomplete."""
        missing = []
        if not self._teller_app_id:
            missing.append("TELLER_APPLICATION_ID")
        if not self._teller_cert:
            missing.append("TELLER_CERTIFICATE")
        elif not Path(self._teller_cert).exists():
            raise TellerConfigError(
                f"Teller certificate not found: {self._teller_cert}"
            )
        if not self._teller_key:
            missing.append("TELLER_PRIVATE_KEY")
        elif not Path(self._teller_key).exists():
            raise TellerConfigError(
                f"Teller private key not found: {self._teller_key}"
            )
        if missing:
            raise TellerConfigError(
                f"Missing Teller config: {', '.join(missing)}. "
                "Set these environment variables or create a .env file."
            )
