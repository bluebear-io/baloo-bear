"""OpenRouter support for the PI runtime, restricted to Zero Data Retention.

PI ships ``openrouter`` as a built-in provider, so model IDs and auth
(``OPENROUTER_API_KEY``) need nothing from Baloo. What PI cannot take on the
command line is OpenRouter's per-request routing preferences, and those are the
point of this module: every request must carry ``provider.zdr = true`` so that
OpenRouter only routes to endpoints with a Zero Data Retention policy, and
``data_collection = "deny"`` so no endpoint that stores or trains on prompts is
eligible. A review sends the whole diff and repository context, so this is not
optional and is deliberately not a setting.

PI reads routing from ``compat.openRouterRouting`` in ``models.json``. Set at
the provider level it merges into every built-in OpenRouter model, so the
catalog stays PI's and any OpenRouter model ID an operator configures inherits
the ZDR constraint without being declared here. Confirmed against pi 1.0.2 by
capturing the request body: ``"provider": {"zdr": true, "data_collection":
"deny", ...}``.

pi also requests ``max_completion_tokens`` equal to the model's catalog maximum,
which pi refreshes from pi.dev at runtime and which reached 943,718 for
``z-ai/glm-5.3`` on 2026-10-12. OpenRouter drops every endpoint whose output
cap is lower — Novita and DeepInfra cap at 131,072 — so a pinned order with
fallbacks disabled failed with ``404 No endpoints found``. ``modelOverrides``
caps the request for each model Baloo uses at ``MAX_OUTPUT_TOKENS``.

Provider pinning is a cost control as much as a quality one. Reviews are
dominated by prompt-cache reads, and OpenRouter's caching only holds while a
session sticks to one upstream (PI sends ``x-session-id`` for that). A
fallback to another provider mid-review re-bills the whole context as fresh
input, and list prices for the same model vary by 3x across providers.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path

from baloo.agent.pi_agent_dir import write_models_json

logger = logging.getLogger(__name__)

OPENROUTER_PROVIDER = "openrouter"

#: Directory under ``~/.baloo`` holding the generated models.json.
AGENT_DIR_NAME = "pi-openrouter"

#: Open-weight models with ZDR endpoints that support tool calling. Every ID
#: must exist in the pinned pi's catalog (``pi --list-models``). Last checked
#: against pi 1.0.2 and OpenRouter's ZDR endpoint list on 2026-10-12.
OPENROUTER_TIER_MODELS: dict[str, str] = {
    "economy": "z-ai/glm-5.3-flash",
    "standard": "z-ai/glm-5.3",
    "premium": "moonshotai/kimi-k2.7-code",
}


#: Per-response output cap sent to OpenRouter, reasoning included. Well above
#: what a review turn produces (a whole 13-turn review emitted ~13K tokens) and
#: at or below every ZDR endpoint's output limit for the tier models.
MAX_OUTPUT_TOKENS = 65_536

#: Settings whose value may name an OpenRouter model. Same keys as Databricks.
MODEL_SETTING_KEYS: tuple[str, ...] = (
    "agent_model",
    "fp_verification_model",
    "thread_agent_model",
    "documentation_drift_model",
)


def _model_id(value: object) -> str | None:
    """Return ``value`` as an OpenRouter model ID (``vendor/model``), or None.

    Tier short names have no slash; the ``openrouter/`` prefix is stripped the
    same way ``get_agent_options`` strips it before handing the ID to pi.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().removeprefix(f"{OPENROUTER_PROVIDER}/")
    vendor, _, model = candidate.partition("/")
    return candidate if vendor and model else None


def configured_model_ids() -> list[str]:
    """OpenRouter model IDs named in the operator's model settings."""
    try:
        from baloo.config.runtime_settings import resolve_setting

        values = [resolve_setting(key) for key in MODEL_SETTING_KEYS]
    except Exception as exc:  # noqa: BLE001 - config layer unavailable
        logger.warning("could not read model settings; output cap covers tier models only: %s", exc)
        return []
    return [model_id for model_id in map(_model_id, values) if model_id]


def parse_provider_order(raw: str) -> list[str]:
    """Split ``OPENROUTER_PROVIDER_ORDER`` into OpenRouter provider slugs."""
    return [slug.strip().lower() for slug in raw.split(",") if slug.strip()]


def build_routing(provider_order: str = "", allow_fallbacks: bool = True) -> dict:
    """Return the ``openRouterRouting`` block sent with every request."""
    routing: dict = {"zdr": True, "data_collection": "deny"}
    order = parse_provider_order(provider_order)
    if order:
        routing["order"] = order
    # Sent even when True: an explicit value documents intent in the payload
    # and keeps OpenRouter's default from silently changing under us.
    routing["allow_fallbacks"] = allow_fallbacks
    return routing


def build_models_config(
    provider_order: str = "", allow_fallbacks: bool = True, extra_models: Iterable[str] = ()
) -> dict:
    """Return the ``models.json`` payload applying ZDR routing to OpenRouter.

    ``extra_models`` entries that are not OpenRouter IDs are dropped, so raw
    setting values (tier short names included) can be passed unfiltered.
    """
    model_ids = set(OPENROUTER_TIER_MODELS.values())
    model_ids.update(model_id for model_id in map(_model_id, extra_models) if model_id)
    return {
        "providers": {
            OPENROUTER_PROVIDER: {
                "compat": {"openRouterRouting": build_routing(provider_order, allow_fallbacks)},
                # Per model because pi has no provider-level output cap. pi
                # ignores override IDs that are not in its catalog.
                "modelOverrides": {
                    model_id: {"maxTokens": MAX_OUTPUT_TOKENS} for model_id in sorted(model_ids)
                },
            }
        }
    }


def configured_routing() -> tuple[str, bool]:
    """Read the routing settings, falling back to defaults if config is unavailable.

    Imported lazily for the same reason as ``databricks.configured_model_services``:
    ``baloo.agent.tiers`` imports this module at import time.
    """
    try:
        from baloo.config.runtime_settings import resolve_setting

        return (
            str(resolve_setting("openrouter_provider_order") or ""),
            bool(resolve_setting("openrouter_allow_fallbacks")),
        )
    except Exception as exc:  # noqa: BLE001 - config layer unavailable
        # ZDR is unconditional, so the defaults are still safe to send.
        logger.warning("could not read OpenRouter routing settings; using defaults: %s", exc)
        return "", True


def ensure_agent_dir(
    base_dir: str | os.PathLike[str] | None = None,
    provider_order: str | None = None,
    allow_fallbacks: bool | None = None,
    extra_models: Iterable[str] | None = None,
) -> Path:
    """Write ``models.json`` for OpenRouter and return its directory.

    Arguments left as ``None`` come from the operator's settings.
    """
    if extra_models is None:
        extra_models = configured_model_ids()
    if provider_order is None or allow_fallbacks is None:
        configured_order, configured_fallbacks = configured_routing()
        provider_order = configured_order if provider_order is None else provider_order
        allow_fallbacks = configured_fallbacks if allow_fallbacks is None else allow_fallbacks
    return write_models_json(
        AGENT_DIR_NAME,
        build_models_config(provider_order, allow_fallbacks, extra_models),
        base_dir=base_dir,
    )
