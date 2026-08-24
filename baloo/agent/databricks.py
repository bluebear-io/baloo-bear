"""Databricks AI Gateway support for the PI runtime.

PI has no native ``databricks`` provider the way it has ``amazon-bedrock``, so
Baloo registers one through PI's ``models.json`` custom-provider mechanism and
points the subprocess at it with ``PI_CODING_AGENT_DIR``.

Three details are load-bearing and were each confirmed against a live workspace:

- ``authHeader`` — the gateway requires ``Authorization: Bearer`` and rejects
  the ``x-api-key`` header PI's built-in ``anthropic`` provider sends. Setting
  ``ANTHROPIC_BASE_URL`` does not help: PI passes the built-in provider's own
  baseUrl explicitly, so requests still go to api.anthropic.com.
- ``supportsEagerToolInputStreaming: false`` — the gateway's Anthropic
  translator rejects per-tool ``eager_input_streaming`` on the streaming+tools
  path every Baloo review uses. Without this flag requests **hang** rather than
  failing, which surfaces as an ``agent_error`` with no detail.
- Model IDs are Unity Catalog FQNs (``system.ai.claude-*``). The older flat
  ``databricks-claude-*`` names now return 501 NOT_IMPLEMENTED.

The token is never written to disk: ``apiKey`` holds the *name* of an
environment variable, which PI resolves at request time.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DATABRICKS_PROVIDER = "databricks"

#: Environment variable holding the workspace PAT. Referenced by name inside
#: models.json so the secret stays out of the generated file.
DATABRICKS_TOKEN_ENV = "DATABRICKS_TOKEN"

#: Path the AI Gateway serves the Anthropic-dialect passthrough on.
_GATEWAY_PATH = "/ai-gateway/anthropic"

#: Unity Catalog model services backing each Baloo tier. Static rather than
#: discovered: listing models needs a `unity-catalog`-scoped token, while
#: inference only needs the gateway scope, so discovery would demand broader
#: credentials than running reviews does. Mirrors how Bedrock pins tier IDs.
DATABRICKS_TIER_MODELS: dict[str, str] = {
    "economy": "system.ai.claude-haiku-4-5",
    "standard": "system.ai.claude-sonnet-4-6",
    "premium": "system.ai.claude-opus-4-6",
}

# Conservative per-model limits. The gateway rejects requests whose output
# exceeds a model's cap, so these stay at values confirmed to work.
_CONTEXT_WINDOW = 200_000
_MAX_TOKENS = 32_000


class DatabricksConfigError(ValueError):
    """Raised when Databricks is selected but its settings are unusable."""


def normalize_host(host: str) -> str:
    """Return the bare workspace origin for ``host``.

    Accepts what an operator is likely to paste: with or without a scheme, with
    or without the ``/ai-gateway/anthropic`` suffix that appears in Databricks'
    own copy-paste snippets.
    """
    cleaned = host.strip().rstrip("/")
    if not cleaned:
        raise DatabricksConfigError(
            "DATABRICKS_HOST is empty. Set it to your workspace URL, e.g. "
            "https://dbc-xxxxxxxx-xxxx.cloud.databricks.com"
        )
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    if cleaned.endswith(_GATEWAY_PATH):
        cleaned = cleaned[: -len(_GATEWAY_PATH)]
    return cleaned.rstrip("/")


def build_models_config(host: str) -> dict:
    """Return the ``models.json`` payload registering the Databricks provider."""
    return {
        "providers": {
            DATABRICKS_PROVIDER: {
                "baseUrl": f"{normalize_host(host)}{_GATEWAY_PATH}",
                "api": "anthropic-messages",
                # The *name* of the env var, not the token. PI resolves it per
                # request, so the secret never lands in the generated file.
                "apiKey": DATABRICKS_TOKEN_ENV,
                "authHeader": True,
                "headers": {"x-databricks-use-coding-agent-mode": "true"},
                "compat": {
                    # Omitting this hangs every tool-enabled request. See module docstring.
                    "supportsEagerToolInputStreaming": False,
                    "supportsLongCacheRetention": True,
                },
                # ponytail: no `cost` block, so PI reports $0 for every Databricks
                # review. Databricks bills DBUs at a per-contract rate, so there is
                # no correct constant to hardcode; add per-tier prices here if the
                # dashboard cost figure starts mattering. See docs/features/databricks.md.
                "models": [
                    {
                        "id": model_id,
                        "reasoning": True,
                        "input": ["text", "image"],
                        "contextWindow": _CONTEXT_WINDOW,
                        "maxTokens": _MAX_TOKENS,
                    }
                    for model_id in sorted(set(DATABRICKS_TIER_MODELS.values()))
                ],
            }
        }
    }


def ensure_agent_dir(host: str, base_dir: str | os.PathLike[str] | None = None) -> Path:
    """Write ``models.json`` for the Databricks provider and return its directory.

    The directory becomes ``PI_CODING_AGENT_DIR`` for the PI subprocess. It is
    deliberately *not* under ``/tmp``: the sandbox mounts a fresh tmpfs there,
    which would hide the file from the agent.
    """
    home = Path(base_dir) if base_dir is not None else Path.home()
    agent_dir = home / ".baloo" / "pi-databricks"
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_path = agent_dir / "models.json"
    payload = json.dumps(build_models_config(host), indent=2) + "\n"

    # Rewrite only on change so concurrent reviews don't race on the file.
    if not config_path.exists() or config_path.read_text(encoding="utf-8") != payload:
        config_path.write_text(payload, encoding="utf-8")
        logger.info("wrote Databricks provider config to %s", config_path)

    return agent_dir
