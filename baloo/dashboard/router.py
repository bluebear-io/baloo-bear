"""Dashboard routes served with Jinja2 + HTMX."""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from baloo.config.runtime_settings import (
    MUTABLE_KEYS,
    RESTART_REQUIRED_KEYS,
    RuntimeSettingsError,
    _unwrap_optional,
    clear_override,
    ensure_fresh_cache,
    resolve_setting,
    set_override,
    setting_source,
    validate_override,
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

# Substrings that mark a setting as a credential. One classifier, used by both
# _format_setting_value (what is rendered) and _derive_control (how it renders).
# Keeping two lists in step is how LINEAR_API_KEY ended up printed in cleartext:
# the control said "masked" while the value formatter had never heard of it.
SECRET_PATTERNS = ("_key", "_secret", "_password", "_token")


def _is_secret(name: str) -> bool:
    return any(pattern in name for pattern in SECRET_PATTERNS)


# database_url is deliberately excluded — it has its own sanitizer below that
# keeps the useful connection target while stripping credentials.
SENSITIVE_SETTINGS = {name for name in Settings.model_fields if _is_secret(name)}

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
        "pi_binary_path",
        "pi_thinking_level",
        "databricks_host",
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
    ("databricks", "Databricks AI Gateway"),
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


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _sort_findings(findings: Any) -> list[Any]:
    """Severest first. Unknown severities sort last rather than disappearing."""
    return sorted(
        findings,
        key=lambda f: _SEVERITY_RANK.get(str(f.severity).lower(), len(_SEVERITY_RANK)),
    )


def _setting_category(name: str) -> str:
    for category, names in SETTING_CATEGORIES.items():
        if name in names:
            return category
    return "Other"


# Three tiers so a newcomer meeting 50 settings knows which ones matter.
# "Required" is what Baloo will not run without; "Common" is what an operator
# actually tunes; everything else is Advanced and collapses by default.
REQUIRED_SETTINGS = frozenset(
    {
        "github_app_id",
        "github_private_key",
        "github_webhook_secret",
        "anthropic_api_key",
    }
)

COMMON_SETTINGS = frozenset(
    {
        "agent_provider",
        "agent_model",
        "databricks_host",
        "pi_thinking_level",
        "review_auto_approve",
        "review_min_severity",
        "review_use_checks_api",
        "ticket_id_prefix",
        "fp_verification_enabled",
        "thread_agent_enabled",
        "documentation_drift_enabled",
        "fidelity_enabled",
        "ast_tools_enabled",
        "feedback_signals_enabled",
        "database_enabled",
        "database_url",
        "dashboard_username",
        "dashboard_password",
        "log_level",
        "app_environment",
        "max_concurrent_reviews",
    }
)


def _setting_tier(name: str) -> str:
    if name in REQUIRED_SETTINGS:
        return "required"
    if name in COMMON_SETTINGS:
        return "common"
    return "advanced"


# Uppercase to match FindingsFilter's severity_order lookup and the values
# documented in docs/features/severity-routing.md. Lowercase here silently
# fell through to the .get() default, making every option mean MEDIUM.
REVIEW_SEVERITY_CHOICES = (
    ("CRITICAL", "Critical only"),
    ("HIGH", "High and above"),
    ("MEDIUM", "Medium and above"),
    ("LOW", "Low and above"),
)

THINKING_LEVEL_CHOICES = (
    ("off", "Off"),
    ("minimal", "Minimal"),
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
)

_EXPLICIT_CHOICES = {
    "agent_provider": AGENT_PROVIDER_CHOICES,
    "review_min_severity": REVIEW_SEVERITY_CHOICES,
    "pi_thinking_level": THINKING_LEVEL_CHOICES,
}


def _field_bounds(field: Any) -> tuple[Any, Any]:
    """Pull ge/le out of a pydantic field's metadata, if present."""
    minimum = maximum = None
    for item in getattr(field, "metadata", ()):
        minimum = getattr(item, "ge", minimum)
        maximum = getattr(item, "le", maximum)
    return minimum, maximum


def _derive_control(name: str, field: Any, mutable: bool) -> str:
    """Pick the input control for a setting from its pydantic annotation."""
    if _is_secret(name) or name == "database_url":
        return "masked"
    if not mutable:
        return "text"
    if name in _EXPLICIT_CHOICES:
        return "select"
    annotation = _unwrap_optional(field.annotation)
    if annotation is bool:
        return "toggle"
    if annotation in (int, float):
        return "number"
    return "text"


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
        minimum, maximum = _field_bounds(field)
        control = _derive_control(name, field, mutable)
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
                "choices": _EXPLICIT_CHOICES.get(name),
                "control": control,
                "minimum": minimum,
                "maximum": maximum,
                "bool_value": bool(effective) if control == "toggle" else None,
                "restart_required": name in RESTART_REQUIRED_KEYS,
                "tier": _setting_tier(name),
            }
        )

    # Group by category. Fields are not declared contiguously by category, so
    # without this a category renders as several separate cards.
    order = {name: index for index, name in enumerate(SETTING_CATEGORIES)}
    rows.sort(key=lambda row: order.get(row["category"], len(order)))
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
        context={"review": review, "findings": _sort_findings(review.findings)},
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
    request: Request,
    username: str = Depends(verify_credentials),
):
    """Set or clear allowlisted runtime overrides, or run a provider smoke test.

    Accepts one or many key/value pairs. Every pair is validated before
    anything is written, so a single bad field cannot leave the batch
    half-applied.
    """
    from baloo.agent.provider_smoke import SMOKE_TRIGGER_KEYS, smoke_test_provider

    form = await request.form()
    action = str(form.get("action", "save"))

    if action == "test_connection":
        result = await smoke_test_provider()
        return _settings_redirect(
            message="Ran provider smoke test.",
            smoke_ok="1" if result.ok else "0",
            smoke_message=result.message,
        )

    # A per-row "Revert to env" button carries its own key, so reverting one
    # setting never depends on the client trimming the other pairs out.
    clear_key = form.get("clear_key")
    if clear_key:
        try:
            await clear_override(str(clear_key))
        except RuntimeSettingsError as exc:
            return _settings_redirect(error=str(exc))
        return _settings_redirect(
            message=f"Cleared override for {str(clear_key).upper()}; using env default."
        )

    keys = [str(k) for k in form.getlist("key")]
    values = [str(v) for v in form.getlist("value")]
    if len(keys) != len(values):
        return _settings_redirect(error="Malformed settings submission.")
    if not keys:
        return _settings_redirect(error="No settings submitted.")

    if action == "clear":
        for key in keys:
            if key not in MUTABLE_KEYS:
                return _settings_redirect(error=f"Setting is not mutable at runtime: {key}")
        try:
            for key in keys:
                await clear_override(key)
        except RuntimeSettingsError as exc:
            return _settings_redirect(error=str(exc))
        plural = "" if len(keys) == 1 else "s"
        return _settings_redirect(
            message=f"Cleared {len(keys)} override{plural}; using env default{plural}."
        )

    if action != "save":
        return _settings_redirect(error="Unknown action.")

    # Validate everything first — all or nothing.
    errors: list[str] = []
    for key, value in zip(keys, values):
        try:
            validate_override(key, value)
        except RuntimeSettingsError as exc:
            errors.append(f"{key.upper()}: {exc}")
    if errors:
        return _settings_redirect(error=" · ".join(errors))

    # Skip writes that would store the value the setting already resolves to,
    # so a client that submits every field (no JS, say) does not convert the
    # whole page from env to permanent db overrides in one click.
    pending = [
        (key, value) for key, value in zip(keys, values) if str(resolve_setting(key)) != value
    ]
    if not pending:
        return _settings_redirect(message="No changes to save.")

    written: list[str] = []
    try:
        for key, value in pending:
            await set_override(key, value, updated_by=username)
            written.append(key)
    except RuntimeSettingsError as exc:
        if written:
            return _settings_redirect(
                error=f"{exc} Applied before failing: {', '.join(k.upper() for k in written)}."
            )
        return _settings_redirect(error=str(exc))

    plural = "" if len(written) == 1 else "s"
    names = ", ".join(k.upper() for k in written)
    flash: dict[str, Any] = {"message": f"Updated {len(written)} setting{plural}: {names}."}
    if any(key in SMOKE_TRIGGER_KEYS for key in written):
        result = await smoke_test_provider()
        flash["smoke_ok"] = "1" if result.ok else "0"
        flash["smoke_message"] = result.message

    return _settings_redirect(**flash)
