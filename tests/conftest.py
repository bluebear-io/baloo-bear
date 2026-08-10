"""Global pytest configuration for deterministic Baloo tests."""

from __future__ import annotations

import os

import pytest

# Keep tests independent from the developer's local .env file.
os.environ.setdefault("BALOO_ENV_FILE", "/tmp/baloo-tests.env")
os.environ.setdefault("APP_ENVIRONMENT", "test")
os.environ.setdefault("AGENT_PROVIDER", "anthropic")
os.environ.setdefault("AGENT_FALLBACK_MODEL", "google/gemini-2.5-flash")
os.environ.setdefault("PI_BINARY_PATH", "pi")
os.environ.setdefault("PI_THINKING_LEVEL", "medium")
os.environ.setdefault("REVIEW_AUTO_APPROVE", "true")
os.environ.setdefault("REVIEW_AUTO_APPROVE_REPOS", "*")
os.environ.setdefault("REVIEW_MIN_SEVERITY", "MEDIUM")
os.environ.setdefault("REVIEW_USE_CHECKS_API", "true")
os.environ.setdefault("DASHBOARD_ENABLED", "true")
os.environ.setdefault("FIDELITY_ENABLED", "true")
# Agents refuse to run when a sandbox is configured but bubblewrap is unusable,
# which is the normal state of a test runner. Tests that exercise sandboxing
# patch the mode explicitly.
os.environ.setdefault("REPO_SANDBOX_MODE", "off")


@pytest.fixture(autouse=True)
def reset_baloo_settings():
    """Reset cached settings between tests so env overrides stay deterministic."""
    from baloo.config.runtime_settings import reset_runtime_settings_cache
    from baloo.config.settings import reset_settings

    reset_settings()
    reset_runtime_settings_cache()
    yield
    reset_settings()
    reset_runtime_settings_cache()
