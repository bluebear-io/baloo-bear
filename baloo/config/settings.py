"""Configuration settings for Baloo using Pydantic."""

import logging
import os

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("BALOO_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )

    # GitHub App Configuration
    github_app_id: str = Field(default="", description="GitHub App ID")
    github_private_key: str = Field(default="", description="GitHub App private key (PEM format)")
    github_webhook_secret: str = Field(
        default="", description="GitHub webhook secret for signature validation"
    )
    webhook_pre_verified: bool = Field(
        default=False,
        description="Skip webhook signature verification (set True when behind a trusted proxy)",
    )

    # Anthropic Configuration
    anthropic_api_key: str = Field(default="", description="Anthropic API key for Claude")

    # Application Configuration
    app_environment: str = Field(
        default="development", description="Application environment (development, production)"
    )
    app_host: str = Field(default="0.0.0.0", description="Application host")
    app_port: int = Field(default=8000, description="Application port")
    log_level: str = Field(default="INFO", description="Logging level")
    max_concurrent_reviews: int = Field(
        default=3,
        ge=1,
        le=100,
        description="Maximum number of PR reviews to process concurrently",
    )
    review_stale_timeout_minutes: int = Field(
        default=30,
        description="Minutes after which an in-progress review is considered stale and can be superseded",
    )
    webhook_delivery_dedupe_ttl_seconds: int = Field(
        default=900,
        description="Seconds to suppress duplicate GitHub webhook delivery IDs in this process",
    )

    # Agent Configuration
    agent_provider: str = Field(
        default="anthropic",
        description=(
            "LLM provider for all agents " "(anthropic, google, openai, amazon-bedrock, databricks)"
        ),
    )
    databricks_host: str = Field(
        default="",
        description=(
            "Databricks workspace URL, e.g. https://dbc-xxxxxxxx-xxxx.cloud.databricks.com. "
            "Required when AGENT_PROVIDER=databricks; pair with a DATABRICKS_TOKEN PAT."
        ),
    )
    agent_model: str = Field(
        default="sonnet",
        description="Model for reviews: tier short name or provider/model / bare model ID",
    )
    pi_binary_path: str = Field(
        default="pi",
        description="Path to the pi binary (or just 'pi' if on PATH)",
    )
    pi_thinking_level: str = Field(
        default="medium",
        description="PI thinking level: off, minimal, low, medium, high",
    )

    # Review Configuration
    ticket_id_prefix: str = Field(
        default="PROJ",
        description="Prefix for ticket IDs (e.g., 'PROJ' for PROJ-123)",
    )
    review_auto_approve: bool = Field(
        default=False,
        description="Auto-approve PRs with no critical/high issues (opt-in: Baloo "
        "comments without approving unless this is enabled)",
    )
    review_min_severity: str = Field(
        default="MEDIUM",
        description="Minimum severity to report (LOW, MEDIUM, HIGH, CRITICAL)",
    )
    review_use_checks_api: bool = Field(
        default=True,
        description="Use GitHub Checks API for MEDIUM severity issues",
    )

    # Database Configuration
    database_url: str = Field(default="", description="PostgreSQL connection URL")
    database_enabled: bool = Field(
        default=False, description="Enable database persistence for review data"
    )

    # Multi-Tenant Configuration
    installation_id: str | None = Field(
        default=None,
        description="GitHub installation ID for tenant scoping (required in shared-DB deployments)",
    )

    @field_validator("installation_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    # Dashboard Configuration
    dashboard_enabled: bool = Field(default=True, description="Enable the review history dashboard")
    dashboard_username: str = Field(default="", description="Dashboard basic auth username")
    dashboard_password: str = Field(default="", description="Dashboard basic auth password")
    log_retention_days: int = Field(
        default=30,
        ge=0,
        le=3650,
        description="Days to retain execution logs (0 to disable cleanup)",
    )

    # FP Verification Configuration
    fp_verification_enabled: bool = Field(
        default=True,
        description="Enable LLM-powered false-positive verification pass",
    )
    fp_verification_model: str = Field(
        default="haiku",
        description="Model for FP verification (short name or provider/model)",
    )
    fp_verification_max_concurrent: int = Field(
        default=5,
        description="Max concurrent FP verification calls",
    )
    fp_audit_log_path: str = Field(
        default="/var/log/baloo/fp-audit.jsonl",
        description="Path for FP verification audit log (JSONL). Empty to disable.",
    )

    # Thread Agent Configuration
    thread_agent_enabled: bool = Field(
        default=False,
        description="Enable the thread conversation agent for PR comment replies",
    )
    thread_agent_model: str = Field(
        default="haiku",
        description="Model for thread replies (short name or provider/model)",
    )
    thread_agent_max_replies: int = Field(
        default=3,
        ge=0,
        le=50,
        description="Max total Baloo messages per thread (original + replies) before escalation",
    )
    thread_agent_max_concurrent: int = Field(
        default=3,
        description="Max parallel thread agent calls",
    )

    # Feedback Signals Configuration
    feedback_signals_enabled: bool = Field(
        default=True,
        description="Write and read feedback signals (requires DATABASE_ENABLED)",
    )
    feedback_signals_ttl_days: int = Field(
        default=180,
        ge=1,
        le=3650,
        description="Days before unmatched feedback signals expire",
    )

    # AST Tools Configuration
    ast_tools_enabled: bool = Field(
        default=True,
        description="Enable AST analysis tools (outline, grep, symbols) for the review agent",
    )

    # Repo Provisioning Configuration
    repo_cache_enabled: bool = Field(
        default=True,
        description="Check out the PR repo so the agent's file tools read real code "
        "(off = diff-only behavior)",
    )
    repo_cache_root: str = Field(
        default="/tmp/baloo-repo-cache",
        description="Ephemeral root dir for cached bare clones and per-review worktrees",
    )
    repo_cache_max_disk_gb: int = Field(
        default=10,
        description="Total cache disk cap (GB); least-recently-used caches are evicted over this",
    )
    repo_sandbox_mode: str = Field(
        default="bwrap",
        description="Filesystem sandbox for the agent subprocess: 'bwrap' or 'off' "
        "(bwrap falls back to off if unprivileged user namespaces are unavailable)",
    )

    # Documentation Drift Configuration
    documentation_drift_enabled: bool = Field(
        default=False,
        description="Enable PR-time documentation drift analysis",
    )
    documentation_drift_catalog_path: str = Field(
        default=".baloo/documentation-catalog.json",
        description="Repo-relative path to the documentation drift catalog",
    )
    documentation_drift_model: str = Field(
        default="sonnet",
        description="Model for documentation drift analysis (short name or provider/model)",
    )

    # Fidelity Report Configuration
    fidelity_enabled: bool = Field(
        default=True,
        description="Enable fidelity report comparing PR changes against design plan",
    )
    fidelity_plan_path_pattern: str = Field(
        default="docs/plans/{ticket_id}.md",
        description="Path pattern for plan files, with {ticket_id} placeholder",
    )
    fidelity_approval_threshold: int = Field(
        default=90,
        ge=0,
        le=100,
        description="Minimum fidelity score (0-100) required for auto-approval with clean review",
    )

    # Linear Configuration
    linear_api_key: str = Field(default="", description="Linear API key for ticket fallback")
    linear_api_url: str = Field(
        default="https://api.linear.app/graphql",
        description="Linear GraphQL API endpoint",
    )

    @field_validator("agent_provider", mode="before")
    @classmethod
    def normalize_agent_provider(cls, v: object) -> object:
        """Canonicalize the provider token to lowercase.

        Provider tokens are lowercase everywhere they are consumed — the
        ``PROVIDER_TIER_MODELS`` catalog, the ``databricks`` comparison in
        ``pi_runtime``, and pi's own ``--provider`` argument. Without this,
        ``AGENT_PROVIDER=Databricks`` passes every check by not matching any of
        them and reaches pi verbatim, which fails per review with
        ``Unknown provider "Databricks"``. Normalizing once here keeps the rest
        of the system free of case-insensitive comparisons.
        """
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @model_validator(mode="after")
    def check_provider_credentials(self) -> "Settings":
        """Reject a provider selection whose required settings are missing.

        Without this, ``AGENT_PROVIDER=databricks`` with no ``DATABRICKS_HOST``
        starts cleanly and then fails *every* review with an ``agent_error``,
        one PR at a time. Failing at settings load surfaces it once, at deploy.

        Only the environment-configured provider is checked. A provider chosen
        at runtime from the dashboard never reaches here; that path is covered
        by the smoke test the dashboard runs after the override is saved.
        """
        if self.agent_provider == "databricks" and not self.databricks_host.strip():
            raise ValueError(
                "AGENT_PROVIDER=databricks requires DATABRICKS_HOST. Set it to your "
                "workspace URL, e.g. https://dbc-xxxxxxxx-xxxx.cloud.databricks.com "
                "(DATABRICKS_HOST is environment-only and cannot be set from the "
                "dashboard). See docs/features/databricks.md."
            )
        return self

    @property
    def github_private_key_bytes(self) -> bytes:
        """Get GitHub private key as bytes."""
        # Handle both inline key and file path
        if self.github_private_key.startswith("-----BEGIN"):
            return self.github_private_key.encode("utf-8")
        else:
            # Assume it's a file path
            with open(self.github_private_key, "rb") as f:
                return f.read()


# Global settings instance (lazy-loaded to avoid import errors)
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        if _settings.webhook_pre_verified:
            logger.warning(
                "WEBHOOK_PRE_VERIFIED is enabled — webhook signature verification is DISABLED. "
                "Only use this when running behind a trusted proxy."
            )
    return _settings


def reset_settings() -> None:
    """Reset the global settings instance (useful for tests)."""
    global _settings
    _settings = None


# For backward compatibility - but note: this will be evaluated on import
# For testing, use get_settings() function instead
def __getattr__(name: str):
    """Lazy load settings attribute."""
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
