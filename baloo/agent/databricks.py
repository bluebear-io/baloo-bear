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

A custom provider's model list is *replacing*, not additive, so the tier
models are always declared here. Any Unity Catalog name in the operator's model
settings is declared alongside them: PI 0.73 happens to synthesise an undeclared
model from a sibling entry (with a stderr warning on every spawn), but PI's
custom-provider contract does not promise that, so an operator-owned model
service is declared explicitly rather than relying on the fallback.

The token is never written to disk: ``apiKey`` holds the *name* of an
environment variable, which PI resolves at request time.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable
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

#: Settings whose value may name a Unity Catalog model service. All four are
#: read on every write so the payload is identical whichever agent role spawns
#: first; deriving it from the spawning agent's own model would rewrite the
#: file each time roles alternate and defeat the idempotent-write check below.
MODEL_SETTING_KEYS: tuple[str, ...] = (
    "agent_model",
    "fp_verification_model",
    "thread_agent_model",
    "documentation_drift_model",
)

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


def _model_service(value: object) -> str | None:
    """Return ``value`` as a Unity Catalog name, or None if it is not one.

    Accepts the bare name and the ``databricks/`` prefixed form, since
    ``get_agent_options`` hands PI the remainder either way. Tier short names
    and first-party Anthropic IDs have no dots, so the ``catalog.schema.name``
    shape separates them without asking the gateway — which would need the
    ``unity-catalog`` token scope Baloo deliberately does not require.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().removeprefix(f"{DATABRICKS_PROVIDER}/")
    if "/" in candidate:  # another provider's model, e.g. "anthropic/a.b.c"
        return None
    parts = candidate.split(".")
    return candidate if len(parts) == 3 and all(parts) else None


def configured_model_services() -> list[str]:
    """Unity Catalog names found in the operator's model settings.

    Imported lazily: ``baloo.agent.tiers`` imports this module at import time,
    so reaching into ``baloo.config`` at module scope would make that order
    load-bearing. A config layer that raises yields no extras rather than
    failing the write — the tier models still have to reach disk.
    """
    try:
        from baloo.config.runtime_settings import resolve_setting

        values = [resolve_setting(key) for key in MODEL_SETTING_KEYS]
    except Exception as exc:  # noqa: BLE001 - config layer unavailable
        logger.warning(
            "could not read model settings for models.json; extra models not declared: %s", exc
        )
        return []
    return [name for name in map(_model_service, values) if name]


def build_models_config(host: str, extra_models: Iterable[str] = ()) -> dict:
    """Return the ``models.json`` payload registering the Databricks provider.

    ``extra_models`` entries that are not Unity Catalog names are dropped, so
    raw setting values (tier short names included) can be passed unfiltered.
    """
    model_ids = set(DATABRICKS_TIER_MODELS.values())
    model_ids.update(name for name in map(_model_service, extra_models) if name)
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
                    for model_id in sorted(model_ids)
                ],
            }
        }
    }


def ensure_agent_dir(
    host: str,
    base_dir: str | os.PathLike[str] | None = None,
    extra_models: Iterable[str] | None = None,
) -> Path:
    """Write ``models.json`` for the Databricks provider and return its directory.

    The directory becomes ``PI_CODING_AGENT_DIR`` for the PI subprocess. It is
    deliberately *not* under ``/tmp``: the sandbox mounts a fresh tmpfs there,
    which would hide the file from the agent.

    ``extra_models`` defaults to the model services named by the operator's
    model settings; pass an explicit iterable (``()`` included) to bypass that.
    """
    if extra_models is None:
        extra_models = configured_model_services()
    home = Path(base_dir) if base_dir is not None else Path.home()
    agent_dir = home / ".baloo" / "pi-databricks"
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_path = agent_dir / "models.json"
    payload = json.dumps(build_models_config(host, extra_models), indent=2) + "\n"

    # Skip the write when the content already matches, so the steady state is a
    # pure read and concurrent reviews don't touch the file at all.
    try:
        if config_path.read_text(encoding="utf-8") == payload:
            return agent_dir
    except FileNotFoundError:
        pass

    # Write via a uniquely-named temp file in the same directory, then rename.
    # os.replace is atomic on POSIX, so a PI subprocess reading models.json
    # concurrently sees either the old file or the new one, never a truncated
    # one. The temp name must be unique: a shared name would let two concurrent
    # first-run writers interleave into the same file and rename the mess into
    # place, reintroducing the race this avoids.
    fd, tmp_name = tempfile.mkstemp(dir=agent_dir, prefix=".models-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, config_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    logger.info("wrote Databricks provider config to %s", config_path)

    return agent_dir
