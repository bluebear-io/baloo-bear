"""Smoke-test the effective LLM provider/model via a one-shot PI call.

Used by the dashboard so an admin can confirm credentials and provider wiring
after changing runtime settings, without running a full PR review.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from baloo.agent.config import get_agent_options
from baloo.agent.pi_runtime import PIAgentBase

logger = logging.getLogger(__name__)

SMOKE_TIMEOUT_SECONDS = 30.0

# Keys whose save/clear should auto-run a primary-provider smoke check.
SMOKE_TRIGGER_KEYS = frozenset({"agent_provider", "agent_model"})

_SMOKE_SYSTEM_PROMPT = (
    "You are a connectivity probe for Baloo. " "Respond with only the requested JSON object."
)
_SMOKE_PROMPT = 'Reply with ONLY this JSON object and nothing else: {"status":"ok"}'


@dataclass(frozen=True)
class SmokeResult:
    """Outcome of a provider connectivity smoke test."""

    ok: bool
    provider: str
    model: str
    duration_seconds: float
    message: str
    error: str | None = None

    @property
    def model_ref(self) -> str:
        return f"{self.provider}/{self.model}"


async def smoke_test_provider(
    *,
    model: str | None = None,
    timeout_seconds: float = SMOKE_TIMEOUT_SECONDS,
) -> SmokeResult:
    """Spawn PI with the effective (or overridden) provider/model and probe it.

    Uses ``--no-tools``, thinking off, and a 1-turn JSON ping so the check
    validates auth + endpoint wiring without touching a repo worktree.
    """
    options = get_agent_options(model)
    options.system_prompt = _SMOKE_SYSTEM_PROMPT
    options.thinking_level = "off"
    options.max_turns = 1
    options.no_tools = True
    options.name = "ProviderSmoke"
    # Never sandbox a connectivity probe — no cwd / no repo isolation needed.
    options.cwd = None

    provider = options.provider
    model_id = options.model
    start = time.monotonic()

    agent = PIAgentBase(options)
    try:
        _structured, metadata = await asyncio.wait_for(
            agent.run_query(_SMOKE_PROMPT),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        elapsed = time.monotonic() - start
        msg = f"Smoke test timed out after {timeout_seconds:.0f}s for " f"{provider}/{model_id}"
        logger.warning(msg)
        return SmokeResult(
            ok=False,
            provider=provider,
            model=model_id,
            duration_seconds=elapsed,
            message=msg,
            error="timeout",
        )
    except Exception as exc:
        elapsed = time.monotonic() - start
        err = str(exc)[:500]
        msg = f"Smoke test failed for {provider}/{model_id}: {err}"
        logger.warning(msg)
        return SmokeResult(
            ok=False,
            provider=provider,
            model=model_id,
            duration_seconds=elapsed,
            message=msg,
            error=err,
        )

    elapsed = time.monotonic() - start
    if metadata.get("is_error"):
        err = str(metadata.get("error_message") or "PI agent reported an error (see server logs)")[
            :500
        ]
        msg = f"Smoke test failed for {provider}/{model_id}: {err}"
        logger.warning(msg)
        return SmokeResult(
            ok=False,
            provider=provider,
            model=model_id,
            duration_seconds=elapsed,
            message=msg,
            error=err,
        )

    msg = f"Smoke test passed for {provider}/{model_id} " f"in {elapsed:.1f}s"
    logger.info(msg)
    return SmokeResult(
        ok=True,
        provider=provider,
        model=model_id,
        duration_seconds=elapsed,
        message=msg,
    )
