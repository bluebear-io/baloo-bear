"""Tests for DB-backed runtime settings overlay."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from baloo.config.runtime_settings import (
    MUTABLE_KEYS,
    RuntimeSettingsError,
    clear_override,
    coerce_setting_value,
    refresh_cache,
    reset_runtime_settings_cache,
    resolve_setting,
    set_override,
    setting_source,
    validate_override,
)
from baloo.config.settings import reset_settings
from baloo.db.engine import reset_engine
from baloo.db.models import Base, RuntimeSetting


@pytest.fixture
async def runtime_db(monkeypatch):
    """In-memory SQLite DB wired into runtime settings + settings singleton."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_MODEL", "sonnet")
    monkeypatch.delenv("INSTALLATION_ID", raising=False)
    reset_settings()
    reset_runtime_settings_cache()
    reset_engine()

    with patch("baloo.db.engine.get_session_factory", return_value=factory):
        yield factory
    reset_runtime_settings_cache()
    reset_engine()
    await engine.dispose()


def test_mutable_keys_cover_agent_knobs():
    assert "agent_provider" in MUTABLE_KEYS
    assert "agent_model" in MUTABLE_KEYS
    assert "agent_fallback_model" in MUTABLE_KEYS
    assert "anthropic_api_key" not in MUTABLE_KEYS
    assert "database_url" not in MUTABLE_KEYS


def test_validate_override_rejects_secrets():
    with pytest.raises(RuntimeSettingsError, match="not mutable"):
        validate_override("anthropic_api_key", "sk-secret")


def test_validate_override_rejects_empty_provider():
    with pytest.raises(RuntimeSettingsError, match="non-empty"):
        validate_override("agent_provider", "  ")


def test_coerce_string_passthrough():
    assert coerce_setting_value("agent_model", "opus") == "opus"


def test_resolve_falls_back_to_env_when_cache_empty(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("AGENT_MODEL", "haiku")
    reset_settings()
    reset_runtime_settings_cache()
    assert resolve_setting("agent_model") == "haiku"
    assert setting_source("agent_model") == "env"


def test_resolve_uses_cache_overlay(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("AGENT_MODEL", "haiku")
    reset_settings()
    reset_runtime_settings_cache()

    import baloo.config.runtime_settings as rs

    rs._cache = {"agent_model": "opus"}
    rs._cache_loaded_at = 10**12  # fresh
    assert resolve_setting("agent_model") == "opus"
    assert setting_source("agent_model") == "db"


def test_resolve_ignores_overlay_when_db_disabled(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "false")
    monkeypatch.setenv("AGENT_MODEL", "haiku")
    reset_settings()
    reset_runtime_settings_cache()

    import baloo.config.runtime_settings as rs

    rs._cache = {"agent_model": "opus"}
    rs._cache_loaded_at = 10**12
    assert resolve_setting("agent_model") == "haiku"
    assert setting_source("agent_model") == "env"


@pytest.mark.asyncio
async def test_set_and_clear_override_roundtrip(runtime_db):
    await set_override("agent_provider", "amazon-bedrock", updated_by="tester")
    assert resolve_setting("agent_provider") == "amazon-bedrock"
    assert setting_source("agent_provider") == "db"

    async with runtime_db() as session:
        row = (await session.execute(select(RuntimeSetting))).scalar_one()
        assert row.key == "agent_provider"
        assert row.value == "amazon-bedrock"
        assert row.updated_by == "tester"
        assert row.installation_id is None

    removed = await clear_override("agent_provider")
    assert removed is True
    assert resolve_setting("agent_provider") == "anthropic"
    assert setting_source("agent_provider") == "env"


@pytest.mark.asyncio
async def test_set_override_scopes_by_installation_id(runtime_db, monkeypatch):
    monkeypatch.setenv("INSTALLATION_ID", "inst-42")
    reset_settings()

    await set_override("agent_model", "opus", updated_by="alice")

    async with runtime_db() as session:
        row = (await session.execute(select(RuntimeSetting))).scalar_one()
        assert row.installation_id == "inst-42"
        assert row.key == "agent_model"


@pytest.mark.asyncio
async def test_refresh_cache_loads_rows(runtime_db):
    async with runtime_db() as session:
        async with session.begin():
            session.add(
                RuntimeSetting(
                    key="pi_thinking_level",
                    value="high",
                    installation_id=None,
                    updated_at=datetime.now(timezone.utc),
                    updated_by="seed",
                )
            )

    reset_runtime_settings_cache()
    await refresh_cache()
    assert resolve_setting("pi_thinking_level") == "high"


@pytest.mark.asyncio
async def test_set_override_rejects_non_mutable(runtime_db):
    with pytest.raises(RuntimeSettingsError, match="not mutable"):
        await set_override("database_url", "postgresql://x", updated_by="x")


@pytest.mark.asyncio
async def test_set_override_updates_existing_row(runtime_db):
    await set_override("agent_model", "opus", updated_by="alice")
    await set_override("agent_model", "sonnet", updated_by="bob")

    async with runtime_db() as session:
        rows = (await session.execute(select(RuntimeSetting))).scalars().all()

    assert len(rows) == 1
    assert rows[0].value == "sonnet"
    assert rows[0].updated_by == "bob"
    assert resolve_setting("agent_model") == "sonnet"


@pytest.mark.asyncio
async def test_set_override_recovers_from_concurrent_insert(runtime_db):
    """A racing writer inserts first; our INSERT loses the unique index and retries as update."""
    import baloo.config.runtime_settings as rs

    async with runtime_db() as session:
        async with session.begin():
            session.add(
                RuntimeSetting(
                    key="agent_model",
                    value="haiku",
                    installation_id=None,
                    updated_at=datetime.now(timezone.utc),
                    updated_by="racer",
                )
            )

    real_filter = rs._tenant_filter
    calls = {"n": 0}

    def flaky_filter(stmt, model, installation_id):
        calls["n"] += 1
        if calls["n"] == 1:
            # First lookup misses the row the racing writer just committed,
            # so set_override attempts an INSERT and hits the unique index.
            return stmt.where(model.key == "__never_matches__")
        return real_filter(stmt, model, installation_id)

    with patch.object(rs, "_tenant_filter", flaky_filter):
        await set_override("agent_model", "opus", updated_by="admin")

    assert calls["n"] >= 2

    async with runtime_db() as session:
        rows = (await session.execute(select(RuntimeSetting))).scalars().all()

    assert len(rows) == 1
    assert rows[0].value == "opus"
    assert rows[0].updated_by == "admin"
    assert resolve_setting("agent_model") == "opus"


def test_get_agent_options_picks_up_overlay(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("AGENT_MODEL", "haiku")
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    reset_settings()
    reset_runtime_settings_cache()

    import baloo.config.runtime_settings as rs
    from baloo.agent.config import get_agent_options

    rs._cache = {"agent_model": "opus", "agent_provider": "anthropic"}
    rs._cache_loaded_at = 10**12

    options = get_agent_options()
    assert options.model == "claude-opus-5"
    assert options.provider == "anthropic"


def test_fidelity_agent_uses_get_agent_options(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("AGENT_MODEL", "haiku")
    reset_settings()
    reset_runtime_settings_cache()

    import baloo.config.runtime_settings as rs
    from baloo.fidelity.fidelity_analyzer import FidelityAgent

    rs._cache = {"agent_model": "sonnet"}
    rs._cache_loaded_at = 10**12

    agent = FidelityAgent()
    assert agent.options.model == "claude-sonnet-5"
    assert "fidelity" in agent.options.system_prompt.lower() or agent.options.system_prompt


def test_fidelity_agent_keeps_medium_thinking_level(monkeypatch):
    """Global PI_THINKING_LEVEL changes must not degrade fidelity analysis."""
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("PI_THINKING_LEVEL", "off")
    reset_settings()
    reset_runtime_settings_cache()

    import baloo.config.runtime_settings as rs
    from baloo.fidelity.fidelity_analyzer import FidelityAgent

    rs._cache = {"pi_thinking_level": "off"}
    rs._cache_loaded_at = 10**12

    assert FidelityAgent().options.thinking_level == "medium"
