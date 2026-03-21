import pytest
from personal_finance_mcp.config import Config, TellerConfigError


def test_config_loads_db_path_default():
    config = Config()
    assert config.db_path.endswith("finance.db")


def test_config_loads_db_path_from_env(monkeypatch):
    monkeypatch.setenv("FINANCE_DB_PATH", "/tmp/test.db")
    config = Config()
    assert config.db_path == "/tmp/test.db"


def test_config_loads_port_default():
    config = Config()
    assert config.enroll_port == 8765


def test_config_loads_port_from_env(monkeypatch):
    monkeypatch.setenv("FINANCE_PORT", "9999")
    config = Config()
    assert config.enroll_port == 9999


def test_teller_config_raises_when_missing():
    """Teller config validated lazily, not on init."""
    config = Config()
    with pytest.raises(TellerConfigError):
        config.validate_teller()


def test_teller_config_valid(monkeypatch, tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    monkeypatch.setenv("TELLER_APPLICATION_ID", "test-app-id")
    monkeypatch.setenv("TELLER_CERTIFICATE", str(cert))
    monkeypatch.setenv("TELLER_PRIVATE_KEY", str(key))
    config = Config()
    config.validate_teller()  # Should not raise
    assert config.teller_app_id == "test-app-id"
