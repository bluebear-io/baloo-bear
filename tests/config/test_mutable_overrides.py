"""Every newly-mutable key must actually reach its call site.

A key in MUTABLE_KEYS whose consumer still reads ``settings.X`` produces a
settings page that appears to save and changes nothing. These tests are what
make the expansion real.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from baloo.config import runtime_settings
from baloo.config.runtime_settings import MUTABLE_KEYS, resolve_setting

NEWLY_MUTABLE = [
    "review_auto_approve",
    "review_use_checks_api",
    "fp_verification_enabled",
    "thread_agent_enabled",
    "documentation_drift_enabled",
    "fidelity_enabled",
    "ast_tools_enabled",
    "feedback_signals_enabled",
    "review_min_severity",
    "thread_agent_max_replies",
    "fidelity_approval_threshold",
    "log_retention_days",
]

NEVER_MUTABLE = [
    "github_private_key",
    "github_webhook_secret",
    "anthropic_api_key",
    "database_url",
    "database_enabled",
    "dashboard_password",
    "dashboard_username",
    "app_host",
    "app_port",
    "installation_id",
    "pi_binary_path",
]

# Modules that legitimately read the env-layer Settings for a mutable key.
ALLOWED_DIRECT_READERS = {
    "baloo/config/settings.py",
    "baloo/config/runtime_settings.py",
    # Renders the env vs db comparison, so it needs the raw env value.
    "baloo/dashboard/router.py",
    # Runs at startup, before the overlay cache exists.
    "baloo/db/engine.py",
}


@pytest.fixture
def overlay(monkeypatch):
    """Install a fake DB overlay without touching a database."""
    from baloo.config.settings import reset_settings

    def _install(values: dict[str, str]) -> None:
        monkeypatch.setenv("DATABASE_ENABLED", "true")
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
        reset_settings()
        monkeypatch.setattr(runtime_settings, "_cache", dict(values))
        monkeypatch.setattr(runtime_settings, "_cache_loaded_at", float("inf"))

    yield _install

    runtime_settings.reset_runtime_settings_cache()
    reset_settings()


@pytest.mark.parametrize("key", NEWLY_MUTABLE)
def test_key_is_mutable(key: str) -> None:
    assert key in MUTABLE_KEYS


@pytest.mark.parametrize("key", NEVER_MUTABLE)
def test_secret_and_infra_keys_are_never_mutable(key: str) -> None:
    assert key not in MUTABLE_KEYS


def test_bool_override_resolves(overlay) -> None:
    overlay({"fp_verification_enabled": "false"})
    assert resolve_setting("fp_verification_enabled") is False


def test_int_override_resolves(overlay) -> None:
    overlay({"thread_agent_max_replies": "9"})
    assert resolve_setting("thread_agent_max_replies") == 9


def test_enum_override_resolves(overlay) -> None:
    overlay({"review_min_severity": "high"})
    assert resolve_setting("review_min_severity") == "high"


def test_no_call_site_reads_a_mutable_key_directly() -> None:
    """Guards against reintroducing the settings.X bypass."""
    pattern = re.compile(r"settings\.(" + "|".join(sorted(MUTABLE_KEYS)) + r")\b")
    offenders = []
    for path in sorted(Path("baloo").rglob("*.py")):
        if str(path) in ALLOWED_DIRECT_READERS:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")

    assert offenders == [], (
        "These read a mutable setting directly, so DB overrides are ignored. "
        "Use resolve_setting():\n" + "\n".join(offenders)
    )


def test_auto_approve_is_opt_in() -> None:
    """Baloo must not approve PRs until an operator opts in.

    Approving is a write action on someone's repository, so the default is off.
    """
    from baloo.config.settings import Settings

    assert Settings.model_fields["review_auto_approve"].default is False
