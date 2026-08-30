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
    # Startup banner, before the cache is loaded; the log says "(env)".
    "main.py",
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
    """Guards against reintroducing the settings.X bypass.

    Matches every shape the bypass actually took in this codebase, not just the
    bare ``settings.`` one: ``get_settings().X`` and an aliased ``s = get_settings()``
    followed by ``s.X`` were both real, and a name-only regex missed both.
    """
    keys = "|".join(sorted(MUTABLE_KEYS))
    patterns = [
        re.compile(r"get_settings\(\)\.(" + keys + r")\b"),
        # Any short attribute access on a local settings alias, e.g. `s.foo`.
        re.compile(r"\b[a-z_]{1,10}\.(" + keys + r")\b"),
    ]
    roots = [Path("baloo"), Path("scripts")]
    files = [p for root in roots if root.exists() for p in sorted(root.rglob("*.py"))]
    files.append(Path("main.py"))

    offenders = []
    for path in files:
        if str(path) in ALLOWED_DIRECT_READERS or not path.exists():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "resolve_setting(" in line:
                continue
            if any(p.search(line) for p in patterns):
                offenders.append(f"{path}:{lineno}: {stripped}")

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


def test_databricks_host_is_never_dashboard_writable() -> None:
    """DATABRICKS_HOST is the URL the gateway bearer token is sent to.

    If it were settable from a web form, dashboard access alone would be enough
    to repoint the gateway at an attacker-controlled host and have Baloo ship
    DATABRICKS_TOKEN there on the next review.
    """
    from baloo.config.runtime_settings import RuntimeSettingsError, validate_override

    assert "databricks_host" not in MUTABLE_KEYS
    with pytest.raises(RuntimeSettingsError):
        validate_override("databricks_host", "https://attacker.example.com")


def test_databricks_token_is_never_a_setting() -> None:
    """The PAT stays an env var passed through the sandbox, never DB-overridable."""
    from baloo.config.settings import Settings

    assert "databricks_token" not in Settings.model_fields
