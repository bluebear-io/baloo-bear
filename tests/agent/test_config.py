"""Tests for PI agent configuration helpers."""

from baloo.agent.config import get_agent_options, resolve_short_name
from baloo.config.settings import reset_settings


class TestGetAgentOptions:
    """Tests for get_agent_options function."""

    # --- Anthropic short names (default provider) ---

    def test_get_options_with_haiku_short_name(self):
        options = get_agent_options("haiku")
        assert options.model == "claude-haiku-4-5-20251001"
        assert options.provider == "anthropic"
        assert options.max_turns == 10

    def test_get_options_with_sonnet_short_name(self):
        options = get_agent_options("sonnet")
        assert options.model == "claude-sonnet-4-6"
        assert options.provider == "anthropic"
        assert options.max_turns == 20

    def test_get_options_with_opus_short_name(self):
        options = get_agent_options("opus")
        assert options.model == "claude-opus-4-6"
        assert options.provider == "anthropic"
        assert options.max_turns == 30

    # --- Google short names resolve on AGENT_PROVIDER ---

    def test_get_options_with_flash_short_name_on_google(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER", "google")
        reset_settings()
        options = get_agent_options("flash")
        assert options.model == "gemini-3.5-flash-lite"
        assert options.provider == "google"
        assert options.max_turns == 10

    def test_get_options_with_gemini_pro_short_name_on_google(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER", "google")
        reset_settings()
        options = get_agent_options("gemini-pro")
        assert options.model == "gemini-3.6-flash"
        assert options.provider == "google"
        assert options.max_turns == 20

    def test_flash_on_anthropic_maps_to_economy_claude(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        reset_settings()
        options = get_agent_options("flash")
        assert options.provider == "anthropic"
        assert options.model == "claude-haiku-4-5-20251001"

    # --- Explicit provider/model ---

    def test_get_options_with_provider_slash_model(self):
        options = get_agent_options("google/gemini-3.5-flash-lite")
        assert options.model == "gemini-3.5-flash-lite"
        assert options.provider == "google"

    def test_get_options_with_anthropic_slash_model(self):
        options = get_agent_options("anthropic/claude-opus-4-6")
        assert options.model == "claude-opus-4-6"
        assert options.provider == "anthropic"

    # --- Full model name passthrough ---

    def test_get_options_with_full_model_name(self):
        full_model = "claude-opus-4-6"
        options = get_agent_options(full_model)
        assert options.model == full_model
        assert options.provider == "anthropic"

    def test_full_model_name_uses_effective_provider(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER", "amazon-bedrock")
        reset_settings()
        options = get_agent_options("us.anthropic.claude-sonnet-4-6")
        assert options.provider == "amazon-bedrock"
        assert options.model == "us.anthropic.claude-sonnet-4-6"

    # --- Defaults ---

    def test_get_options_with_default_model(self):
        options = get_agent_options()
        assert options.model is not None
        assert options.system_prompt is not None

    def test_thinking_level_override(self):
        options = get_agent_options("opus", thinking_level="high")
        assert options.thinking_level == "high"

    def test_default_thinking_level(self):
        options = get_agent_options("sonnet")
        assert options.thinking_level == "medium"

    def test_system_prompt_is_set(self):
        options = get_agent_options("sonnet")
        assert options.system_prompt is not None
        assert "Baloo" in options.system_prompt


def test_thread_agent_settings_defaults():
    """Thread agent settings have correct defaults."""
    from baloo.config.settings import Settings

    s = Settings()
    assert s.thread_agent_enabled is False
    assert s.thread_agent_model == "haiku"
    assert s.thread_agent_max_replies == 3
    assert s.thread_agent_max_concurrent == 3
    assert s.feedback_signals_enabled is True
    assert s.feedback_signals_ttl_days == 180


def test_ast_tools_settings_defaults():
    """AST tools settings have correct defaults."""
    from baloo.config.settings import Settings

    s = Settings()
    assert s.ast_tools_enabled is True


def test_standard_alias_resolves_to_sonnet():
    from baloo.agent.config import get_agent_options

    opts = get_agent_options("standard")
    assert opts.model == "claude-sonnet-4-6"
    assert opts.provider == "anthropic"
    assert opts.max_turns == 20


def test_premium_alias_resolves_on_google(monkeypatch):
    from baloo.agent.config import get_agent_options

    monkeypatch.setenv("AGENT_PROVIDER", "google")
    reset_settings()
    opts = get_agent_options("premium")
    assert opts.model == "gemini-3.1-pro-preview"
    assert opts.provider == "google"
    assert opts.max_turns == 30


def test_gemini_3_1_pro_alias_resolves_same_as_premium(monkeypatch):
    from baloo.agent.config import get_agent_options

    monkeypatch.setenv("AGENT_PROVIDER", "google")
    reset_settings()
    opts = get_agent_options("gemini-3.1-pro")
    assert opts.model == "gemini-3.1-pro-preview"
    assert opts.provider == "google"


def test_openai_tier_short_names(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    reset_settings()

    assert get_agent_options("haiku").model == "gpt-5.6-luna"
    assert get_agent_options("sonnet").model == "gpt-5.6-terra"
    assert get_agent_options("opus").model == "gpt-5.6-sol"
    assert get_agent_options("sonnet").provider == "openai"


def test_bedrock_provider_model_string():
    from baloo.agent.config import get_agent_options

    opts = get_agent_options("amazon-bedrock/us.anthropic.claude-sonnet-4-6")
    assert opts.provider == "amazon-bedrock"
    assert opts.model == "us.anthropic.claude-sonnet-4-6"


def test_bedrock_via_settings_provider(monkeypatch):
    from baloo.agent.config import get_agent_options

    monkeypatch.setenv("AGENT_PROVIDER", "amazon-bedrock")
    monkeypatch.setenv("AGENT_MODEL", "us.anthropic.claude-sonnet-4-6")
    reset_settings()

    opts = get_agent_options()
    assert opts.provider == "amazon-bedrock"
    assert opts.model == "us.anthropic.claude-sonnet-4-6"


def test_bedrock_short_names_use_bedrock_tiers(monkeypatch):
    """AGENT_PROVIDER applies to every short-name agent role, not only primary."""
    monkeypatch.setenv("AGENT_PROVIDER", "amazon-bedrock")
    reset_settings()

    economy = get_agent_options("haiku")
    assert economy.provider == "amazon-bedrock"
    assert economy.model == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert economy.max_turns == 10

    standard = get_agent_options("sonnet")
    assert standard.provider == "amazon-bedrock"
    assert standard.model == "us.anthropic.claude-sonnet-4-6"
    assert standard.max_turns == 20

    premium = get_agent_options("opus")
    assert premium.provider == "amazon-bedrock"
    assert premium.model == "us.anthropic.claude-opus-4-6-v1"
    assert premium.max_turns == 30


def test_bedrock_default_short_name_agent_model(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "amazon-bedrock")
    monkeypatch.setenv("AGENT_MODEL", "sonnet")
    reset_settings()

    opts = get_agent_options()
    assert opts.provider == "amazon-bedrock"
    assert opts.model == "us.anthropic.claude-sonnet-4-6"


def test_bedrock_fp_and_thread_short_names_follow_provider(monkeypatch):
    """FP / thread defaults (haiku) must not stay on Anthropic when Bedrock is selected."""
    monkeypatch.setenv("AGENT_PROVIDER", "amazon-bedrock")
    monkeypatch.setenv("FP_VERIFICATION_MODEL", "haiku")
    monkeypatch.setenv("THREAD_AGENT_MODEL", "haiku")
    monkeypatch.setenv("DOCUMENTATION_DRIFT_MODEL", "haiku")
    reset_settings()

    for short in ("haiku", "flash"):
        opts = get_agent_options(short)
        assert opts.provider == "amazon-bedrock"
        assert opts.model.startswith("us.anthropic.claude-haiku")


def test_resolve_short_name_helper():
    provider, model_id, max_turns = resolve_short_name("sonnet", "amazon-bedrock")
    assert provider == "amazon-bedrock"
    assert model_id == "us.anthropic.claude-sonnet-4-6"
    assert max_turns == 20
