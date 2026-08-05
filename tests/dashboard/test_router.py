from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from baloo.dashboard.auth import verify_credentials
from baloo.dashboard.router import router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_credentials] = lambda: "tester"
    return app


def test_dashboard_overview_renders() -> None:
    app = _build_app()
    stats = {
        "total_reviews": 12,
        "reviews_today": 3,
        "avg_duration": 14.2,
        "approval_rate": 75.0,
        "severity": {"MEDIUM": 2},
        "recent_reviews": [
            SimpleNamespace(
                id=1,
                repo_full_name="example-org/example-repo",
                pr_number=42,
                pr_title="Fix dashboard rendering",
                review_status="approved",
                duration_seconds=12.5,
                started_at=datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc),
            )
        ],
        "errors_total": 0,
        "errors_today": 0,
        "error_rate": 0.0,
        "error_categories": {},
        "recent_failures": [],
        "hourly_activity": [],
    }

    with patch(
        "baloo.dashboard.router.DashboardService.get_overview_stats",
        new=AsyncMock(return_value=stats),
    ):
        client = TestClient(app)
        response = client.get("/dashboard/")

    assert response.status_code == 200
    assert "Overview" in response.text
    assert "example-org/example-repo" in response.text


def test_dashboard_settings_renders_sanitized_values(monkeypatch) -> None:
    from baloo.config.settings import reset_settings

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://dbuser:dbpass@db.example.com:5432/baloo"
        "?sslmode=require&password=query-secret&sslpassword=ssl-secret",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "dashboard-secret")
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    reset_settings()

    app = _build_app()
    client = TestClient(app)
    response = client.get("/dashboard/settings")

    assert response.status_code == 200
    assert "Settings" in response.text
    assert "APP_ENVIRONMENT" in response.text
    assert "production" in response.text
    assert "DATABASE_URL" in response.text
    assert (
        "postgresql+asyncpg://db.example.com:5432/baloo"
        "?sslmode=require&amp;password=%5BREDACTED%5D&amp;sslpassword=%5BREDACTED%5D"
        in response.text
    )
    assert "Configured (redacted)" in response.text
    assert "dbuser" not in response.text
    assert "dbpass" not in response.text
    assert "query-secret" not in response.text
    assert "ssl-secret" not in response.text
    assert "sk-ant-test-secret" not in response.text
    assert "webhook-secret" not in response.text
    assert "dashboard-secret" not in response.text


def test_every_setting_is_categorized() -> None:
    """No setting should fall into the catch-all 'Other' bucket — each new
    field must be assigned a category so it surfaces under a real heading."""
    from baloo.dashboard.router import _settings_rows

    uncategorized = sorted(r["name"] for r in _settings_rows() if r["category"] == "Other")
    assert uncategorized == [], f"settings missing a category: {uncategorized}"


def test_repo_provisioning_settings_are_grouped() -> None:
    from baloo.dashboard.router import _settings_rows

    by_name = {r["name"]: r["category"] for r in _settings_rows()}
    for name in (
        "repo_cache_enabled",
        "repo_cache_root",
        "repo_cache_max_disk_gb",
        "repo_sandbox_mode",
    ):
        assert by_name[name] == "Repo Provisioning"


def test_documentation_drift_settings_are_grouped() -> None:
    from baloo.dashboard.router import _settings_rows

    by_name = {r["name"]: r["category"] for r in _settings_rows()}
    for name in (
        "documentation_drift_enabled",
        "documentation_drift_catalog_path",
        "documentation_drift_model",
    ):
        assert by_name[name] == "Documentation Drift"


def test_settings_rows_mark_mutable_keys() -> None:
    from baloo.config.runtime_settings import MUTABLE_KEYS
    from baloo.dashboard.router import _settings_rows

    by_name = {r["name"]: r for r in _settings_rows()}
    for key in MUTABLE_KEYS:
        assert by_name[key]["mutable"] is True
    assert by_name["database_url"]["mutable"] is False


def test_dashboard_settings_post_sets_override(monkeypatch) -> None:
    from baloo.config.runtime_settings import reset_runtime_settings_cache, resolve_setting
    from baloo.config.settings import reset_settings

    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    reset_settings()
    reset_runtime_settings_cache()

    async def fake_set(key: str, value: str, *, updated_by: str | None = None) -> str:
        import baloo.config.runtime_settings as rs

        rs._cache = {**(rs._cache or {}), key: value}
        rs._cache_loaded_at = 10**12
        return value

    with patch("baloo.dashboard.router.set_override", side_effect=fake_set):
        with patch("baloo.dashboard.router.ensure_fresh_cache", new=AsyncMock()):
            app = _build_app()
            client = TestClient(app)
            response = client.post(
                "/dashboard/settings",
                data={"key": "agent_provider", "value": "amazon-bedrock", "action": "save"},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert "message=" in response.headers["location"]
    assert resolve_setting("agent_provider") == "amazon-bedrock"


def test_dashboard_settings_post_clear_override(monkeypatch) -> None:
    from baloo.config.runtime_settings import reset_runtime_settings_cache
    from baloo.config.settings import reset_settings

    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    reset_settings()
    reset_runtime_settings_cache()

    with patch("baloo.dashboard.router.clear_override", new=AsyncMock(return_value=True)) as clear:
        with patch("baloo.dashboard.router.ensure_fresh_cache", new=AsyncMock()):
            app = _build_app()
            client = TestClient(app)
            response = client.post(
                "/dashboard/settings",
                data={"key": "agent_model", "value": "", "action": "clear"},
                follow_redirects=False,
            )

    assert response.status_code == 303
    clear.assert_awaited_once_with("agent_model")


def test_dashboard_settings_post_rejects_non_mutable(monkeypatch) -> None:
    from baloo.config.runtime_settings import RuntimeSettingsError, reset_runtime_settings_cache
    from baloo.config.settings import reset_settings

    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    reset_settings()
    reset_runtime_settings_cache()

    async def boom(*args, **kwargs):
        raise RuntimeSettingsError("Setting is not mutable at runtime: database_url")

    with patch("baloo.dashboard.router.set_override", side_effect=boom):
        app = _build_app()
        client = TestClient(app)
        response = client.post(
            "/dashboard/settings",
            data={"key": "database_url", "value": "x", "action": "save"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
