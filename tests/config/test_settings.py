"""Tests for repo-provisioning settings."""

import pytest
from pydantic import ValidationError

from baloo.config.settings import Settings


def test_repo_cache_enabled_defaults_to_true():
    assert Settings().repo_cache_enabled is True


def test_repo_cache_root_default():
    assert Settings().repo_cache_root == "/tmp/baloo-repo-cache"


def test_repo_cache_max_disk_gb_default():
    assert Settings().repo_cache_max_disk_gb == 10


def test_repo_cache_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("REPO_CACHE_ENABLED", "false")
    assert Settings().repo_cache_enabled is False


def test_repo_sandbox_mode_defaults_to_bwrap():
    assert Settings().repo_sandbox_mode == "bwrap"


def test_repo_sandbox_mode_reads_env(monkeypatch):
    monkeypatch.setenv("REPO_SANDBOX_MODE", "off")
    assert Settings().repo_sandbox_mode == "off"


def test_documentation_drift_settings_defaults():
    s = Settings()
    assert s.documentation_drift_enabled is False
    assert s.documentation_drift_catalog_path == ".baloo/documentation-catalog.json"
    assert s.documentation_drift_model == "sonnet"


class TestProviderCredentialValidation:
    """AGENT_PROVIDER=databricks must not start without DATABRICKS_HOST."""

    def test_databricks_without_host_is_rejected(self):
        # Otherwise the app starts cleanly and fails every review with an
        # agent_error, one PR at a time, instead of once at deploy.
        with pytest.raises(ValidationError) as exc:
            Settings(agent_provider="databricks", databricks_host="")
        assert "DATABRICKS_HOST" in str(exc.value)

    def test_databricks_with_blank_host_is_rejected(self):
        with pytest.raises(ValidationError):
            Settings(agent_provider="databricks", databricks_host="   ")

    def test_databricks_with_host_is_accepted(self):
        s = Settings(
            agent_provider="databricks",
            databricks_host="https://dbc-test.cloud.databricks.com",
        )
        assert s.agent_provider == "databricks"

    def test_other_providers_do_not_require_a_databricks_host(self):
        # The check must be provider-scoped; anthropic/bedrock have no host.
        for provider in ("anthropic", "amazon-bedrock", "google", "openai"):
            assert Settings(agent_provider=provider, databricks_host="").agent_provider == provider
