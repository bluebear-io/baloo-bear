"""Agent configuration for PI runtime."""

import logging

from baloo.agent.pi_runtime import PIAgentOptions
from baloo.agent.prompts import AST_TOOLS_PROMPT_SECTION, REVIEW_SYSTEM_PROMPT
from baloo.config.runtime_settings import resolve_setting
from baloo.config.settings import settings

logger = logging.getLogger(__name__)

# Short names are tier aliases. Which backend they hit is controlled by
# AGENT_PROVIDER — the model split (economy / standard / premium) is an
# implementation detail of each Baloo agent role, not a separate provider.
SHORT_NAME_TIERS: dict[str, tuple[str, int]] = {
    # Economy — FP verification, thread replies, simple reviews
    "flash": ("economy", 10),
    "haiku": ("economy", 10),
    # Standard — default code reviews
    "standard": ("standard", 20),
    "gemini-pro": ("standard", 20),
    "sonnet": ("standard", 20),
    # Premium — complex / security-sensitive reviews
    "premium": ("premium", 30),
    "gemini-3.1-pro": ("premium", 30),
    "opus": ("premium", 30),
}

# Per-provider model IDs for each tier. Bedrock uses US inference-profile IDs;
# override with a bare Bedrock model ID or provider/model string when needed.
PROVIDER_TIER_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {
        "economy": "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-5",
        "premium": "claude-opus-5",
    },
    "google": {
        "economy": "gemini-3.5-flash-lite",
        "standard": "gemini-3.6-flash",
        "premium": "gemini-3.1-pro-preview",
    },
    "amazon-bedrock": {
        "economy": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "standard": "us.anthropic.claude-sonnet-5",
        "premium": "us.anthropic.claude-opus-5",
    },
    "openai": {
        "economy": "gpt-5.6-luna",
        "standard": "gpt-5.6-terra",
        "premium": "gpt-5.6-sol",
    },
}

# Backward-compat: short name -> (provider, model_id, max_turns) using the
# historical default where Claude-named aliases pointed at Anthropic and
# Gemini-named aliases at Google. Prefer resolve_short_name() / get_agent_options().
MODEL_REGISTRY: dict[str, tuple[str, str, int]] = {
    "flash": ("google", "gemini-3.5-flash-lite", 10),
    "haiku": ("anthropic", "claude-haiku-4-5-20251001", 10),
    "standard": ("anthropic", "claude-sonnet-5", 20),
    "gemini-pro": ("google", "gemini-3.6-flash", 20),
    "sonnet": ("anthropic", "claude-sonnet-5", 20),
    "premium": ("google", "gemini-3.1-pro-preview", 30),
    "gemini-3.1-pro": ("google", "gemini-3.1-pro-preview", 30),
    "opus": ("anthropic", "claude-opus-5", 30),
}

MODEL_MAP = {name: spec[1] for name, spec in MODEL_REGISTRY.items()}
MAX_TURNS = {name: spec[2] for name, spec in MODEL_REGISTRY.items()}


def resolve_short_name(name: str, provider: str | None = None) -> tuple[str, str, int]:
    """Resolve a tier short name against the effective provider.

    Returns ``(provider, model_id, max_turns)``. Unknown providers fall back to
    the Anthropic tier catalog (model IDs) while keeping the requested provider
    string so explicit/custom providers can still be selected with bare IDs.
    """
    if name not in SHORT_NAME_TIERS:
        raise KeyError(name)
    effective = provider or resolve_setting("agent_provider")
    tier, max_turns = SHORT_NAME_TIERS[name]
    catalog = PROVIDER_TIER_MODELS.get(effective) or PROVIDER_TIER_MODELS["anthropic"]
    return effective, catalog[tier], max_turns


def _build_system_prompt() -> str:
    """Build the system prompt, conditionally including the AST tools section."""
    prompt = REVIEW_SYSTEM_PROMPT
    if settings.ast_tools_enabled:
        prompt += AST_TOOLS_PROMPT_SECTION
    return prompt


def get_agent_options(model: str = None, thinking_level: str | None = None) -> PIAgentOptions:
    """
    Get PI agent configuration options.

    ``AGENT_PROVIDER`` applies to every agent role. Short names (``haiku``,
    ``sonnet``, ``opus``, ``flash``, …) select a model tier on that provider;
    they do not pick a different backend.

    Args:
        model: Override model selection (default from settings).
               Accepts short names ("flash", "haiku", "sonnet", "gemini-pro", "opus")
               or full "provider/model" strings (e.g. "google/gemini-2.5-flash").
        thinking_level: Thinking level (off, minimal, low, medium, high).
                        Defaults to settings.pi_thinking_level.

    Returns:
        PIAgentOptions configured for read-only code review
    """
    level = thinking_level or resolve_setting("pi_thinking_level")
    system_prompt = _build_system_prompt()
    effective_provider = resolve_setting("agent_provider")

    # 1. Short name → tier on the effective provider
    if model and model in SHORT_NAME_TIERS:
        provider, model_id, max_turns = resolve_short_name(model, effective_provider)
        return PIAgentOptions(
            model=model_id,
            provider=provider,
            system_prompt=system_prompt,
            thinking_level=level,
            max_turns=max_turns,
        )

    # 2. Explicit "provider/model" string (cross-provider escape hatch)
    if model and "/" in model:
        provider, model_id = model.split("/", 1)
        return PIAgentOptions(
            model=model_id,
            provider=provider,
            system_prompt=system_prompt,
            thinking_level=level,
            max_turns=20,
        )

    # 3. Full model ID passthrough on the effective provider
    if model:
        return PIAgentOptions(
            model=model,
            provider=effective_provider,
            system_prompt=system_prompt,
            thinking_level=level,
            max_turns=20,
        )

    # 4. Default from settings (env + DB overlay) — resolve short names first
    default_model = resolve_setting("agent_model")
    if default_model in SHORT_NAME_TIERS:
        provider, model_id, max_turns = resolve_short_name(default_model, effective_provider)
        return PIAgentOptions(
            model=model_id,
            provider=provider,
            system_prompt=system_prompt,
            thinking_level=level,
            max_turns=max_turns,
        )

    return PIAgentOptions(
        model=default_model,
        provider=effective_provider,
        system_prompt=system_prompt,
        thinking_level=level,
        max_turns=20,
    )
