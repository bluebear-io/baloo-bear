"""Agent configuration for PI runtime."""

import logging

from baloo.agent.pi_runtime import PIAgentOptions
from baloo.agent.prompts import AST_TOOLS_PROMPT_SECTION, REVIEW_SYSTEM_PROMPT
from baloo.agent.tiers import (
    AGENT_MAX_TURNS,
    PROVIDER_TIER_MODELS,
    TIER_ALIASES,
    TIER_MAX_TURNS,
)
from baloo.config.runtime_settings import resolve_setting

logger = logging.getLogger(__name__)

# Alias -> tier. Defined in agent.tiers so the PI runtime can read the same
# table without importing this module back.
SHORT_NAME_TIERS = TIER_ALIASES


class ProviderConfigError(ValueError):
    """Raised when model settings contradict the configured provider."""


def resolve_short_name(name: str, provider: str | None = None) -> tuple[str, str, int]:
    """Resolve a tier short name against the effective provider.

    Returns ``(provider, model_id, max_turns)``. Raises ``ProviderConfigError``
    for a provider with no tier catalog: guessing another provider's model IDs
    would surface later as an opaque API error instead of a config mistake.
    """
    if name not in SHORT_NAME_TIERS:
        raise KeyError(name)
    effective = provider or resolve_setting("agent_provider")
    tier = SHORT_NAME_TIERS[name]
    catalog = PROVIDER_TIER_MODELS.get(effective)
    if catalog is None:
        raise ProviderConfigError(
            f"Provider {effective!r} has no model tiers, so the short name {name!r} "
            f"cannot be resolved. Set an explicit model ID for this provider, or use "
            f"AGENT_PROVIDER from: {', '.join(sorted(PROVIDER_TIER_MODELS))}."
        )
    return effective, catalog[tier], TIER_MAX_TURNS[tier]


def _build_system_prompt() -> str:
    """Build the system prompt, conditionally including the AST tools section."""
    prompt = REVIEW_SYSTEM_PROMPT
    if resolve_setting("ast_tools_enabled"):
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
                        Defaults to PI_THINKING_LEVEL.

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

    # 2. "provider/model" string. Only a real provider token counts as a prefix,
    #    so model IDs that contain slashes (Bedrock ARNs) fall through to 3.
    if model:
        prefix, _, remainder = model.partition("/")
        if remainder and (prefix in PROVIDER_TIER_MODELS or prefix == effective_provider):
            if prefix != effective_provider:
                raise ProviderConfigError(
                    f"Model {model!r} selects provider {prefix!r}, but AGENT_PROVIDER is "
                    f"{effective_provider!r}. The provider applies to every agent; set the "
                    f"model to a tier short name or a {effective_provider!r} model ID."
                )
            return PIAgentOptions(
                model=remainder,
                provider=effective_provider,
                system_prompt=system_prompt,
                thinking_level=level,
                max_turns=AGENT_MAX_TURNS,
            )

    # 3. Full model ID passthrough on the effective provider
    if model:
        return PIAgentOptions(
            model=model,
            provider=effective_provider,
            system_prompt=system_prompt,
            thinking_level=level,
            max_turns=AGENT_MAX_TURNS,
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
        max_turns=AGENT_MAX_TURNS,
    )
