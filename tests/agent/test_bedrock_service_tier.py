"""Bedrock service tier is opt-in and applies to every PI subprocess."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from baloo.agent.pi_runtime import (
    PIAgentBase,
    PIAgentOptions,
    _bedrock_tier_extension_args,
)
from baloo.config.settings import Settings


@pytest.mark.parametrize("tier", ["default", "flex", "priority"])
def test_tier_settings_are_valid(tier):
    assert Settings(bedrock_service_tier=tier.upper()).bedrock_service_tier == tier


def test_invalid_tier_fails_early():
    with pytest.raises(ValidationError, match="BEDROCK_SERVICE_TIER"):
        Settings(bedrock_service_tier="spot")


def _agent(provider="amazon-bedrock"):
    return PIAgentBase(PIAgentOptions(provider=provider, model="model-id"))


def _settings():
    return SimpleNamespace(pi_binary_path="pi", repo_sandbox_mode="off")


@pytest.mark.parametrize("tier,expected", [("default", False), ("flex", True), ("priority", True)])
def test_bedrock_tier_extension_loaded_only_when_enabled(tier, expected):
    with (
        patch("baloo.agent.pi_runtime.get_settings", return_value=_settings()),
        patch(
            "baloo.agent.pi_runtime.resolve_setting",
            side_effect=lambda key: tier if key == "bedrock_service_tier" else False,
        ),
    ):
        cmd = _agent()._build_pi_command()
        extension_paths = [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "--extension"]
    assert any(path.endswith("bedrock-service-tier.ts") for path in extension_paths) is expected


def test_other_providers_do_not_load_bedrock_extension():
    with (
        patch("baloo.agent.pi_runtime.get_settings", return_value=_settings()),
        patch("baloo.agent.pi_runtime.resolve_setting", return_value="flex"),
    ):
        cmd = _agent("anthropic")._build_pi_command()
    assert not any("bedrock-service-tier" in token for token in cmd)


def test_bedrock_tier_forwarded_to_scrubbed_subprocess():
    with patch("baloo.agent.pi_runtime.resolve_setting", return_value="flex"):
        env = _agent()._with_provider_env({"PATH": "/usr/bin"})
    assert env == {"PATH": "/usr/bin", "BEDROCK_SERVICE_TIER": "flex"}


def test_default_tier_does_not_modify_env():
    with patch("baloo.agent.pi_runtime.resolve_setting", return_value="default"):
        assert _agent()._with_provider_env(None) is None


@pytest.mark.parametrize(
    "provider,tier,enabled",
    [
        ("amazon-bedrock", "default", False),
        ("amazon-bedrock", "flex", True),
        ("amazon-bedrock", "priority", True),
        ("anthropic", "flex", False),
    ],
)
def test_extension_args_are_shared_by_both_spawn_paths(provider, tier, enabled):
    with patch("baloo.agent.pi_runtime.resolve_setting", return_value=tier):
        args = _bedrock_tier_extension_args(provider)
    assert bool(args) is enabled
    if enabled:
        assert args[0] == "--extension"
        assert args[1].endswith("/extensions/bedrock-service-tier.ts")
