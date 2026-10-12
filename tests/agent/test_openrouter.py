"""Tests for the OpenRouter ZDR routing config."""

import json

import pytest

from baloo.agent import openrouter
from baloo.agent.openrouter import (
    MAX_OUTPUT_TOKENS,
    OPENROUTER_PROVIDER,
    OPENROUTER_TIER_MODELS,
    build_models_config,
    build_routing,
    ensure_agent_dir,
    parse_provider_order,
)


def _routing(**kwargs) -> dict:
    provider = build_models_config(**kwargs)["providers"][OPENROUTER_PROVIDER]
    return provider["compat"]["openRouterRouting"]


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"provider_order": "novita"}, {"allow_fallbacks": False}, {"allow_fallbacks": True}],
)
def test_zdr_and_data_collection_deny_are_always_sent(kwargs):
    # The whole reason this provider exists: no setting combination may drop them.
    routing = _routing(**kwargs)
    assert routing["zdr"] is True
    assert routing["data_collection"] == "deny"


def test_config_never_replaces_the_catalog_or_endpoint():
    # Provider-level compat merges into every built-in openrouter model in pi.
    # Declaring `models` would replace pi's catalog; setting `baseUrl` would
    # redirect requests away from OpenRouter.
    provider = build_models_config()["providers"][OPENROUTER_PROVIDER]
    assert set(provider) == {"compat", "modelOverrides"}


def test_tier_models_get_an_output_cap_every_endpoint_accepts():
    # pi otherwise requests the catalog maximum (943,718 for glm-5.3), which
    # filters out Novita/DeepInfra (131,072) and 404s with fallbacks disabled.
    overrides = build_models_config()["providers"][OPENROUTER_PROVIDER]["modelOverrides"]
    for model_id in OPENROUTER_TIER_MODELS.values():
        assert overrides[model_id] == {"maxTokens": MAX_OUTPUT_TOKENS}
    assert MAX_OUTPUT_TOKENS <= 128_000  # lowest ZDR endpoint cap seen for the tiers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("moonshotai/kimi-k2.6", "moonshotai/kimi-k2.6"),
        ("openrouter/moonshotai/kimi-k2.6", "moonshotai/kimi-k2.6"),
        ("  z-ai/glm-5.2 ", "z-ai/glm-5.2"),
    ],
)
def test_configured_models_are_capped_too(raw, expected):
    overrides = build_models_config(extra_models=[raw])["providers"][OPENROUTER_PROVIDER][
        "modelOverrides"
    ]
    assert overrides[expected] == {"maxTokens": MAX_OUTPUT_TOKENS}


@pytest.mark.parametrize("raw", ["sonnet", "haiku", "", None, "openrouter/"])
def test_non_model_values_are_not_capped(raw):
    overrides = build_models_config(extra_models=[raw])["providers"][OPENROUTER_PROVIDER][
        "modelOverrides"
    ]
    assert set(overrides) == set(OPENROUTER_TIER_MODELS.values())


def test_configured_model_ids_reads_all_model_settings(monkeypatch):
    values = {
        "agent_model": "openrouter/moonshotai/kimi-k2.6",
        "fp_verification_model": "haiku",
        "thread_agent_model": "z-ai/glm-5.2",
        "documentation_drift_model": "sonnet",
    }
    import baloo.config.runtime_settings as rs

    monkeypatch.setattr(rs, "resolve_setting", values.__getitem__)
    assert openrouter.configured_model_ids() == ["moonshotai/kimi-k2.6", "z-ai/glm-5.2"]


def test_no_order_lets_openrouter_choose():
    assert "order" not in build_routing("")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("novita,deepinfra", ["novita", "deepinfra"]),
        (" Novita , DeepInfra ,", ["novita", "deepinfra"]),
        ("", []),
        (" , ", []),
    ],
)
def test_parse_provider_order(raw, expected):
    assert parse_provider_order(raw) == expected


def test_order_and_fallbacks_reach_the_payload():
    routing = build_routing("novita,deepinfra", allow_fallbacks=False)
    assert routing["order"] == ["novita", "deepinfra"]
    assert routing["allow_fallbacks"] is False


def test_ensure_agent_dir_writes_loadable_json(tmp_path):
    agent_dir = ensure_agent_dir(
        tmp_path, provider_order="novita", allow_fallbacks=False, extra_models=()
    )
    assert agent_dir == tmp_path / ".baloo" / "pi-openrouter"
    payload = json.loads((agent_dir / "models.json").read_text())
    assert payload == build_models_config("novita", False)


def test_ensure_agent_dir_reads_settings_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(openrouter, "configured_routing", lambda: ("deepinfra", False))
    monkeypatch.setattr(openrouter, "configured_model_ids", lambda: ["moonshotai/kimi-k2.6"])
    agent_dir = ensure_agent_dir(tmp_path)
    routing = json.loads((agent_dir / "models.json").read_text())["providers"][OPENROUTER_PROVIDER][
        "compat"
    ]["openRouterRouting"]
    assert routing["order"] == ["deepinfra"]
    assert routing["allow_fallbacks"] is False
    overrides = json.loads((agent_dir / "models.json").read_text())["providers"][
        OPENROUTER_PROVIDER
    ]["modelOverrides"]
    assert "moonshotai/kimi-k2.6" in overrides


def test_configured_routing_reads_settings(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "novita,deepinfra")
    monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", "false")
    from baloo.config.settings import reset_settings

    reset_settings()
    try:
        assert openrouter.configured_routing() == ("novita,deepinfra", False)
    finally:
        monkeypatch.delenv("OPENROUTER_PROVIDER_ORDER")
        monkeypatch.delenv("OPENROUTER_ALLOW_FALLBACKS")
        reset_settings()


def test_configured_routing_survives_a_broken_config_layer(monkeypatch):
    import baloo.config.runtime_settings as rs

    def boom(_key):
        raise RuntimeError("db down")

    monkeypatch.setattr(rs, "resolve_setting", boom)
    # Defaults are safe to send because ZDR does not depend on settings.
    assert openrouter.configured_routing() == ("", True)
