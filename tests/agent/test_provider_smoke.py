"""Tests for provider connectivity smoke checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from baloo.agent.pi_runtime import PIAgentOptions
from baloo.agent.provider_smoke import SmokeResult, smoke_test_provider


def _options(**kwargs) -> PIAgentOptions:
    defaults = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "system_prompt": "",
        "thinking_level": "medium",
        "max_turns": 20,
        "no_tools": False,
        "name": None,
        "cwd": None,
    }
    defaults.update(kwargs)
    return PIAgentOptions(**defaults)


@pytest.mark.asyncio
async def test_smoke_test_provider_success():
    metadata = {
        "is_error": False,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.0,
    }
    with patch(
        "baloo.agent.provider_smoke.get_agent_options",
        return_value=_options(model="claude-haiku-4-5-20251001"),
    ):
        with patch(
            "baloo.agent.provider_smoke.PIAgentBase.run_query",
            new=AsyncMock(return_value=({"status": "ok"}, metadata)),
        ) as run_query:
            result = await smoke_test_provider()

    assert result.ok is True
    assert result.provider == "anthropic"
    assert result.model == "claude-haiku-4-5-20251001"
    assert "passed" in result.message.lower()
    run_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_smoke_test_provider_configures_no_tools_probe():
    captured: dict = {}

    def capture_options(model=None):
        opts = _options()
        captured["opts"] = opts
        return opts

    with patch("baloo.agent.provider_smoke.get_agent_options", side_effect=capture_options):
        with patch(
            "baloo.agent.provider_smoke.PIAgentBase.run_query",
            new=AsyncMock(return_value=({"status": "ok"}, {"is_error": False})),
        ):
            await smoke_test_provider()

    assert captured["opts"].no_tools is True
    assert captured["opts"].thinking_level == "off"
    assert captured["opts"].max_turns == 1
    assert captured["opts"].cwd is None
    assert captured["opts"].name == "ProviderSmoke"


@pytest.mark.asyncio
async def test_smoke_test_provider_agent_error():
    with patch(
        "baloo.agent.provider_smoke.get_agent_options",
        return_value=_options(provider="amazon-bedrock", model="us.anthropic.x"),
    ):
        with patch(
            "baloo.agent.provider_smoke.PIAgentBase.run_query",
            new=AsyncMock(return_value=(None, {"is_error": True})),
        ):
            result = await smoke_test_provider()

    assert result.ok is False
    assert "failed" in result.message.lower()


@pytest.mark.asyncio
async def test_smoke_test_provider_surfaces_provider_error_detail():
    """The operator must see the AWS/provider error, not just 'reported an error'."""
    detail = (
        "Agent returned error stop reason: AccessDeniedException: "
        "You don't have access to the model with the specified model ID."
    )
    with patch(
        "baloo.agent.provider_smoke.get_agent_options",
        return_value=_options(provider="amazon-bedrock", model="us.anthropic.x"),
    ):
        with patch(
            "baloo.agent.provider_smoke.PIAgentBase.run_query",
            new=AsyncMock(return_value=(None, {"is_error": True, "error_message": detail})),
        ):
            result = await smoke_test_provider()

    assert result.ok is False
    assert "AccessDeniedException" in result.message
    assert "AccessDeniedException" in (result.error or "")


@pytest.mark.asyncio
async def test_smoke_test_provider_timeout():
    with patch(
        "baloo.agent.provider_smoke.get_agent_options",
        return_value=_options(),
    ):
        with patch(
            "baloo.agent.provider_smoke.PIAgentBase.run_query",
            new=AsyncMock(side_effect=TimeoutError()),
        ):
            result = await smoke_test_provider(timeout_seconds=0.01)

    assert result.ok is False
    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_smoke_test_provider_exception():
    with patch(
        "baloo.agent.provider_smoke.get_agent_options",
        return_value=_options(),
    ):
        with patch(
            "baloo.agent.provider_smoke.PIAgentBase.run_query",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            result = await smoke_test_provider()

    assert result.ok is False
    assert "auth failed" in (result.error or "")


def test_smoke_result_model_ref():
    result = SmokeResult(
        ok=True,
        provider="anthropic",
        model="claude-sonnet-4-6",
        duration_seconds=1.2,
        message="ok",
    )
    assert result.model_ref == "anthropic/claude-sonnet-4-6"
