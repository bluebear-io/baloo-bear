"""DB-backed runtime settings overlay on top of env/Pydantic Settings.

Env-backed ``Settings`` remains the immutable bootstrap layer. Allowlisted keys
may be overridden in the ``runtime_settings`` table and are served from an
in-memory cache (30s TTL for multi-replica convergence). Secrets and infra
settings are never overridable.
"""

from __future__ import annotations

import logging
import time
from typing import Any, get_args, get_origin

from baloo.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

MUTABLE_KEYS = frozenset(
    {
        "agent_provider",
        "agent_model",
        "pi_thinking_level",
        "fp_verification_model",
        "thread_agent_model",
        "documentation_drift_model",
    }
)

CACHE_TTL_SECONDS = 30

# Process-local overlay: key -> raw string value from DB.
_cache: dict[str, str] | None = None
_cache_loaded_at: float | None = None


class RuntimeSettingsError(ValueError):
    """Raised when a runtime override is invalid or not allowed."""


def reset_runtime_settings_cache() -> None:
    """Clear the overlay cache (for tests)."""
    global _cache, _cache_loaded_at
    _cache = None
    _cache_loaded_at = None


def _cache_is_fresh() -> bool:
    if _cache is None or _cache_loaded_at is None:
        return False
    return (time.monotonic() - _cache_loaded_at) < CACHE_TTL_SECONDS


def _field_annotation(key: str) -> Any:
    field = Settings.model_fields.get(key)
    if field is None:
        raise RuntimeSettingsError(f"Unknown setting: {key}")
    return field.annotation


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = [a for a in get_args(annotation) if a is not type(None)]
    if len(args) == 1:
        return args[0]
    return annotation


def coerce_setting_value(key: str, raw: str) -> Any:
    """Coerce a stored/submitted string to the Settings field type."""
    if key not in Settings.model_fields:
        raise RuntimeSettingsError(f"Unknown setting: {key}")

    annotation = _unwrap_optional(_field_annotation(key))
    text = raw if isinstance(raw, str) else str(raw)

    if annotation is bool or annotation is bool | None:
        lowered = text.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise RuntimeSettingsError(f"Invalid boolean for {key}: {raw!r}")

    if annotation is int:
        try:
            return int(text.strip())
        except ValueError as exc:
            raise RuntimeSettingsError(f"Invalid integer for {key}: {raw!r}") from exc

    if annotation is float:
        try:
            return float(text.strip())
        except ValueError as exc:
            raise RuntimeSettingsError(f"Invalid float for {key}: {raw!r}") from exc

    # Default: string
    return text


def validate_override(key: str, raw: str) -> str:
    """Validate an override and return the canonical string to store."""
    if key not in MUTABLE_KEYS:
        raise RuntimeSettingsError(f"Setting is not mutable at runtime: {key}")

    coerced = coerce_setting_value(key, raw)

    if key in {
        "agent_provider",
        "agent_model",
        "fp_verification_model",
        "thread_agent_model",
        "documentation_drift_model",
        "pi_thinking_level",
    }:
        if not isinstance(coerced, str) or not coerced.strip():
            raise RuntimeSettingsError(f"{key} must be a non-empty string")
        return coerced.strip()

    return str(coerced)


def resolve_setting(key: str) -> Any:
    """Return DB override if present and allowlisted; otherwise env Settings value.

    Sync read of the process-local cache only. Call ``ensure_fresh_cache()`` from
    async paths (startup, dashboard, review start) so multi-replica TTL refresh
    stays current.
    """
    settings = get_settings()
    env_value = getattr(settings, key)

    if key not in MUTABLE_KEYS:
        return env_value

    if not settings.database_enabled:
        return env_value

    if _cache is None:
        return env_value

    if key in _cache:
        try:
            return coerce_setting_value(key, _cache[key])
        except RuntimeSettingsError:
            logger.warning("Invalid cached override for %s; falling back to env", key)
            return env_value

    return env_value


def setting_source(key: str) -> str:
    """Return ``db`` if an overlay is active for ``key``, else ``env``."""
    settings = get_settings()
    if key in MUTABLE_KEYS and settings.database_enabled and _cache is not None and key in _cache:
        return "db"
    return "env"


def get_override_map() -> dict[str, str]:
    """Return a copy of the current overlay cache (empty if unloaded)."""
    return dict(_cache or {})


async def refresh_cache() -> None:
    """Load allowlisted overrides for this installation into the process cache."""
    global _cache, _cache_loaded_at

    settings = get_settings()
    if not settings.database_enabled or not settings.database_url:
        _cache = {}
        _cache_loaded_at = time.monotonic()
        return

    from sqlalchemy import select

    from baloo.db.engine import get_session_factory
    from baloo.db.models import RuntimeSetting

    installation_id = settings.installation_id
    try:
        factory = get_session_factory(settings.database_url)
        async with factory() as session:
            stmt = select(RuntimeSetting)
            if installation_id:
                stmt = stmt.where(RuntimeSetting.installation_id == installation_id)
            else:
                stmt = stmt.where(RuntimeSetting.installation_id.is_(None))
            rows = (await session.execute(stmt)).scalars().all()
    except Exception as exc:
        # Table may not exist yet mid-migration, or DB briefly unavailable.
        logger.warning("Failed to load runtime settings cache: %s", exc)
        if _cache is None:
            _cache = {}
            _cache_loaded_at = time.monotonic()
        return

    loaded: dict[str, str] = {}
    for row in rows:
        if row.key in MUTABLE_KEYS:
            loaded[row.key] = row.value

    _cache = loaded
    _cache_loaded_at = time.monotonic()
    logger.info("Loaded %d runtime setting override(s)", len(loaded))


async def ensure_fresh_cache() -> None:
    """Refresh the overlay cache when missing or past TTL."""
    if not _cache_is_fresh():
        await refresh_cache()


def _tenant_filter(stmt, model, installation_id: str | None):
    if installation_id:
        return stmt.where(model.installation_id == installation_id)
    return stmt.where(model.installation_id.is_(None))


async def set_override(key: str, value: str, *, updated_by: str | None = None) -> str:
    """Upsert a runtime override. Returns the stored string value."""
    global _cache, _cache_loaded_at

    stored = validate_override(key, value)
    settings = get_settings()
    if not settings.database_enabled or not settings.database_url:
        raise RuntimeSettingsError("Database must be enabled to set runtime overrides")

    from datetime import datetime, timezone

    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from baloo.db.engine import get_session_factory
    from baloo.db.models import RuntimeSetting

    installation_id = settings.installation_id
    now = datetime.now(timezone.utc)
    factory = get_session_factory(settings.database_url)

    async def _write(insert_allowed: bool) -> bool:
        """Update in place, or insert when absent. Returns True when written."""
        async with factory() as session:
            async with session.begin():
                stmt = select(RuntimeSetting).where(RuntimeSetting.key == key)
                stmt = _tenant_filter(stmt, RuntimeSetting, installation_id)
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing:
                    existing.value = stored
                    existing.updated_at = now
                    existing.updated_by = updated_by
                    return True
                if not insert_allowed:
                    return False
                session.add(
                    RuntimeSetting(
                        key=key,
                        value=stored,
                        installation_id=installation_id,
                        updated_at=now,
                        updated_by=updated_by,
                    )
                )
                return True

    try:
        await _write(insert_allowed=True)
    except IntegrityError:
        # A concurrent writer inserted the same (key, installation_id) between
        # our SELECT and INSERT; the partial unique index rejected the race
        # loser. Re-read and update the row the winner created.
        if not await _write(insert_allowed=False):
            raise

    if _cache is None:
        _cache = {}
    _cache[key] = stored
    _cache_loaded_at = time.monotonic()
    logger.info("Runtime override set: %s=%r (by %s)", key, stored, updated_by or "unknown")
    return stored


async def clear_override(key: str) -> bool:
    """Delete a runtime override. Returns True if a row was removed."""
    global _cache, _cache_loaded_at

    if key not in MUTABLE_KEYS:
        raise RuntimeSettingsError(f"Setting is not mutable at runtime: {key}")

    settings = get_settings()
    if not settings.database_enabled or not settings.database_url:
        raise RuntimeSettingsError("Database must be enabled to clear runtime overrides")

    from sqlalchemy import delete

    from baloo.db.engine import get_session_factory
    from baloo.db.models import RuntimeSetting

    installation_id = settings.installation_id
    factory = get_session_factory(settings.database_url)

    async with factory() as session:
        async with session.begin():
            stmt = delete(RuntimeSetting).where(RuntimeSetting.key == key)
            if installation_id:
                stmt = stmt.where(RuntimeSetting.installation_id == installation_id)
            else:
                stmt = stmt.where(RuntimeSetting.installation_id.is_(None))
            result = await session.execute(stmt)
            removed = (result.rowcount or 0) > 0

    if _cache is not None and key in _cache:
        del _cache[key]
        _cache_loaded_at = time.monotonic()

    if removed:
        logger.info("Runtime override cleared: %s", key)
    return removed
