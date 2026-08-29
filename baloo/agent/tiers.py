"""Model tier constants shared by the PI runtime and agent config.

These live here rather than in ``agent.config`` because ``agent.config`` imports
``PIAgentOptions`` from ``agent.pi_runtime``; putting them in either module would
make the other import it back. One definition, both sides read it.
"""

from __future__ import annotations

from baloo.agent.databricks import DATABRICKS_PROVIDER, DATABRICKS_TIER_MODELS

# Turn ceiling for every agent role that runs a full review. It is a ceiling,
# not a target: a review that finishes in three turns costs three turns.
# Single-purpose agents (smoke test, scope decider, thread reply, FP verify)
# override this with their own small limits.
TIER_MAX_TURNS: dict[str, int] = {
    # Economy — FP verification, thread replies. These finish in one turn, so
    # the low cap is a guard against a pathological loop, not a constraint.
    "economy": 10,
    # Standard — 30, not 20: at 20 the finished-run histogram decayed smoothly
    # to ~11 runs at turn 19 and then spiked to 86 at exactly 20, with a further
    # 66 aborting there having produced no output — a wall, not a distribution.
    "standard": 30,
    # Premium — complex / security-sensitive reviews.
    "premium": 30,
}

#: Ceiling for anything not resolved through a tier (a bare model ID, a
#: provider/model string). Follows the standard tier.
AGENT_MAX_TURNS = TIER_MAX_TURNS["standard"]

# Short names are tier aliases. Which backend they hit is controlled by
# AGENT_PROVIDER — the model split (economy / standard / premium) is an
# implementation detail of each Baloo agent role, not a separate provider.
TIER_ALIASES: dict[str, str] = {
    # Economy — FP verification, thread replies, simple reviews
    "flash": "economy",
    "haiku": "economy",
    # Standard — default code reviews
    "standard": "standard",
    "gemini-pro": "standard",
    "sonnet": "standard",
    # Premium — complex / security-sensitive reviews
    "premium": "premium",
    "gemini-3.1-pro": "premium",
    "opus": "premium",
}

# Per-provider model IDs for each tier. Bedrock uses US inference-profile IDs;
# override with a bare Bedrock model ID or provider/model string when needed.
PROVIDER_TIER_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {
        "economy": "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-4-6",
        "premium": "claude-opus-4-6",
    },
    "google": {
        "economy": "gemini-3.5-flash-lite",
        "standard": "gemini-3.6-flash",
        "premium": "gemini-3.1-pro-preview",
    },
    "amazon-bedrock": {
        "economy": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "standard": "us.anthropic.claude-sonnet-4-6",
        "premium": "us.anthropic.claude-opus-4-6-v1",
    },
    "openai": {
        "economy": "gpt-5.6-luna",
        "standard": "gpt-5.6-terra",
        "premium": "gpt-5.6-sol",
    },
    # Registered with PI via a generated models.json — see baloo/agent/databricks.py.
    DATABRICKS_PROVIDER: dict(DATABRICKS_TIER_MODELS),
}
