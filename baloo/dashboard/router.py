"""Dashboard routes served with Jinja2 + HTMX."""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from baloo.config.runtime_settings import (
    MUTABLE_KEYS,
    RuntimeSettingsError,
    clear_override,
    ensure_fresh_cache,
    resolve_setting,
    set_override,
    setting_source,
)
from baloo.config.settings import Settings, get_settings
from baloo.dashboard.auth import verify_credentials
from baloo.dashboard.queries import DashboardService

router = APIRouter(
    prefix="/dashboard",
    dependencies=[Depends(verify_credentials)],
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# One-shot flash payloads for PRG redirects. Location only carries a server-
# generated token so user-controlled form values never flow into the URL
# (CodeQL: URL redirection from remote source).
_FLASH_TTL_SECONDS = 300
_flash_store: dict[str, tuple[float, dict[str, Any]]] = {}

SENSITIVE_SETTINGS = {
    "anthropic_api_key",
    "dashboard_password",
    "github_private_key",
    "github_webhook_secret",
}

SENSITIVE_DATABASE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "auth_source",
    "authsource",
    "client_secret",
    "pass",
    "password",
    "pwd",
    "secret",
    "ssl_password",
    "sslpassword",
    "token",
}

SETTING_CATEGORIES = {
    "GitHub": {
        "github_app_id",
        "github_private_key",
        "github_webhook_secret",
        "webhook_pre_verified",
        "webhook_delivery_dedupe_ttl_seconds",
    },
    "Anthropic": {"anthropic_api_key"},
    "Application": {
        "app_environment",
        "app_host",
        "app_port",
        "log_level",
        "max_concurrent_reviews",
        "review_stale_timeout_minutes",
    },
    "Agent": {
        "agent_provider",
        "agent_model",
        "agent_max_tokens",
        "agent_temperature",
        "pi_binary_path",
        "pi_thinking_level",
    },
    "Review": {
        "ticket_id_prefix",
        "review_auto_approve",
        "review_min_severity",
        "review_use_checks_api",
    },
    "Database": {"database_url", "database_enabled", "installation_id"},
    "Dashboard": {
        "dashboard_enabled",
        "dashboard_username",
        "dashboard_password",
        "log_retention_days",
    },
    "False-Positive Verification": {
        "fp_verification_enabled",
        "fp_verification_model",
        "fp_verification_max_concurrent",
        "fp_audit_log_path",
    },
    "Thread Agent": {
        "thread_agent_enabled",
        "thread_agent_model",
        "thread_agent_max_replies",
        "thread_agent_max_concurrent",
    },
    "Feedback Signals": {"feedback_signals_enabled", "feedback_signals_ttl_days"},
    "AST Tools": {"ast_tools_enabled"},
    "Fidelity Report": {
        "fidelity_enabled",
        "fidelity_plan_path_pattern",
        "fidelity_approval_threshold",
        "linear_api_key",
        "linear_api_url",
    },
    "Repo Provisioning": {
        "repo_cache_enabled",
        "repo_cache_root",
        "repo_cache_max_disk_gb",
        "repo_sandbox_mode",
    },
    "Documentation Drift": {
        "documentation_drift_enabled",
        "documentation_drift_catalog_path",
        "documentation_drift_model",
    },
}

AGENT_PROVIDER_CHOICES = (
    ("anthropic", "Anthropic (direct API)"),
    ("amazon-bedrock", "Amazon Bedrock"),
    ("google", "Google Gemini"),
    ("openai", "OpenAI"),
)


def _sanitize_database_query(query: str) -> str:
    if not query:
        return ""

    params = parse_qsl(query, keep_blank_values=True)
    sanitized = [
        (key, "[REDACTED]" if key.lower() in SENSITIVE_DATABASE_QUERY_KEYS else value)
        for key, value in params
    ]
    return urlencode(sanitized, doseq=True)


def _sanitize_database_url(value: str) -> str:
    """Remove database credentials while preserving the useful connection target."""
    if not value:
        return "(empty)"

    try:
        parsed = urlsplit(value)
    except ValueError:
        return "Configured (credentials redacted)"

    query = _sanitize_database_query(parsed.query)

    if not parsed.username and not parsed.password:
        if query == parsed.query:
            return value
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))

    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, query, parsed.fragment))


def _format_setting_value(name: str, value: Any) -> str:
    if name in SENSITIVE_SETTINGS:
        return "Configured (redacted)" if value else "Not configured"
    if name == "database_url":
        return _sanitize_database_url(str(value or ""))
    if value is None:
        return "None"
    if value == "":
        return "(empty)"
    return str(value)


def _setting_category(name: str) -> str:
    for category, names in SETTING_CATEGORIES.items():
        if name in names:
            return category
    return "Other"


def _settings_rows() -> list[dict[str, Any]]:
    settings = get_settings()
    rows = []
    for name, field in Settings.model_fields.items():
        env_value = getattr(settings, name)
        mutable = name in MUTABLE_KEYS
        if mutable:
            effective = resolve_setting(name)
            source = setting_source(name)
        else:
            effective = env_value
            source = "env"
        default = field.default
        rows.append(
            {
                "category": _setting_category(name),
                "env_var": name.upper(),
                "name": name,
                "value": _format_setting_value(name, effective),
                "raw_value": "" if effective is None else str(effective),
                "default": _format_setting_value(name, default),
                "description": field.description or "",
                "mutable": mutable,
                "source": source,
                "choices": AGENT_PROVIDER_CHOICES if name == "agent_provider" else None,
            }
        )
    return rows


def _resolve_model_ref(configured: str) -> str:
    """Resolve a short name or provider/model string to ``provider/model_id``.

    Misconfiguration is reported inline so the Settings page still renders and
    the operator can see which role is broken.
    """
    from baloo.agent.config import get_agent_options

    if not configured:
        return "(disabled)"
    try:
        options = get_agent_options(configured)
    except ValueError as exc:
        return f"⚠ {exc}"
    return f"{options.provider}/{options.model}"


def _models_in_use() -> list[dict[str, str]]:
    """Summarize each agent role and the model it will actually call."""
    primary_configured = str(resolve_setting("agent_model"))
    primary_ref = _resolve_model_ref(primary_configured)

    fp_configured = str(resolve_setting("fp_verification_model"))
    thread_configured = str(resolve_setting("thread_agent_model"))
    docs_configured = str(resolve_setting("documentation_drift_model"))

    return [
        {
            "role": "Primary review",
            "setting": "AGENT_MODEL",
            "configured": primary_configured,
            "resolved": primary_ref,
            "source": setting_source("agent_model"),
        },
        {
            "role": "FP verification",
            "setting": "FP_VERIFICATION_MODEL",
            "configured": fp_configured,
            "resolved": _resolve_model_ref(fp_configured),
            "source": setting_source("fp_verification_model"),
        },
        {
            "role": "Thread agent",
            "setting": "THREAD_AGENT_MODEL",
            "configured": thread_configured,
            "resolved": _resolve_model_ref(thread_configured),
            "source": setting_source("thread_agent_model"),
        },
        {
            "role": "Fidelity analysis",
            "setting": "AGENT_MODEL",
            "configured": primary_configured,
            "resolved": primary_ref,
            "source": setting_source("agent_model"),
        },
        {
            "role": "Documentation drift",
            "setting": "DOCUMENTATION_DRIFT_MODEL",
            "configured": docs_configured,
            "resolved": _resolve_model_ref(docs_configured),
            "source": setting_source("documentation_drift_model"),
        },
        {
            "role": "Sync scope decider",
            "setting": "AGENT_MODEL",
            "configured": primary_configured,
            "resolved": primary_ref,
            "source": setting_source("agent_model"),
        },
    ]


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    stats = await DashboardService.get_overview_stats()
    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context=stats,
    )


@router.get("/reviews", response_class=HTMLResponse)
async def reviews_list(
    request: Request,
    page: int = Query(1, ge=1),
    repo: str | None = Query(None),
    status: str | None = Query(None),
    search: str | None = Query(None),
):
    data = await DashboardService.list_reviews(
        page=page,
        repo_filter=repo,
        status_filter=status,
        search_filter=search,
    )
    ctx = {"repo": repo, "status": status, "search": search, **data}
    # HTMX partial swap
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request,
            name="partials/reviews_table.html",
            context=ctx,
        )
    return templates.TemplateResponse(
        request=request,
        name="reviews.html",
        context=ctx,
    )


@router.get("/reviews/{review_id}", response_class=HTMLResponse)
async def review_detail(request: Request, review_id: int):
    review = await DashboardService.get_review_detail(review_id)
    if review is None:
        return HTMLResponse("<h1>Review not found</h1>", status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="review_detail.html",
        context={"review": review},
    )


@router.get("/reviews/{review_id}/logs", response_class=HTMLResponse)
async def review_logs(request: Request, review_id: int):
    logs = await DashboardService.get_review_logs(review_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/review_logs.html",
        context={"logs": logs, "review_id": review_id},
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics(
    request: Request,
    days: int = Query(30, ge=1, le=365),
):
    data = await DashboardService.get_analytics_data(days=days)
    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={"days": days, **data},
    )


@router.get("/outcomes", response_class=HTMLResponse)
async def outcomes(
    request: Request,
    days: int = Query(90, ge=1, le=365),
    repo: str | None = Query(None),
):
    data = await DashboardService.get_outcomes_data(days=days, repo_filter=repo)
    return templates.TemplateResponse(
        request=request,
        name="outcomes.html",
        context={"days": days, "repo": repo, **data},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    await ensure_fresh_cache()
    settings = get_settings()
    flash = _pop_flash(request.query_params.get("flash"))
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "settings_rows": _settings_rows(),
            "models_in_use": _models_in_use(),
            "database_enabled": settings.database_enabled and bool(settings.database_url),
            "message": flash.get("message") if flash else None,
            "error": flash.get("error") if flash else None,
            "smoke_ok": flash.get("smoke_ok") if flash else None,
            "smoke_message": flash.get("smoke_message") if flash else None,
        },
    )


def _purge_expired_flash() -> None:
    now = time.monotonic()
    expired = [token for token, (expires, _) in _flash_store.items() if expires <= now]
    for token in expired:
        _flash_store.pop(token, None)


def _put_flash(payload: dict[str, Any]) -> str:
    """Store a one-shot flash payload; return an opaque token for the redirect."""
    _purge_expired_flash()
    token = secrets.token_urlsafe(16)
    _flash_store[token] = (time.monotonic() + _FLASH_TTL_SECONDS, payload)
    return token


def _pop_flash(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    _purge_expired_flash()
    entry = _flash_store.pop(token, None)
    if entry is None:
        return None
    return entry[1]


def _settings_redirect(**flash: Any) -> RedirectResponse:
    """Redirect to settings with a server-generated flash token only."""
    payload = {key: value for key, value in flash.items() if value is not None}
    if not payload:
        return RedirectResponse(url="/dashboard/settings", status_code=303)
    token = _put_flash(payload)
    return RedirectResponse(
        url=f"/dashboard/settings?flash={token}",
        status_code=303,
    )


@router.post("/settings")
async def update_settings(
    key: str = Form(...),
    value: str = Form(""),
    action: str = Form("save"),
    username: str = Depends(verify_credentials),
):
    """Set or clear an allowlisted runtime override, or run a provider smoke test."""
    from baloo.agent.provider_smoke import SMOKE_TRIGGER_KEYS, smoke_test_provider

    if action == "test_connection":
        result = await smoke_test_provider()
        return _settings_redirect(
            message="Ran provider smoke test.",
            smoke_ok="1" if result.ok else "0",
            smoke_message=result.message,
        )

    try:
        if action == "clear":
            await clear_override(key)
            msg = f"Cleared override for {key.upper()}; using env default."
        elif action == "save":
            await set_override(key, value, updated_by=username)
            msg = f"Updated {key.upper()}."
        else:
            return _settings_redirect(error="Unknown action.")
    except RuntimeSettingsError as exc:
        return _settings_redirect(error=str(exc))

    flash: dict[str, Any] = {"message": msg}
    if key in SMOKE_TRIGGER_KEYS:
        result = await smoke_test_provider()
        flash["smoke_ok"] = "1" if result.ok else "0"
        flash["smoke_message"] = result.message

    return _settings_redirect(**flash)
