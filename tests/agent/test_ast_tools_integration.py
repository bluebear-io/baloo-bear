"""Tests for AST tools integration with PI runtime."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from baloo.agent import pi_runtime
from baloo.agent.pi_runtime import PIAgentBase, PIAgentOptions
from baloo.agent.prompts import AST_TOOLS_PROMPT_SECTION


def _make_mock_settings(**overrides):
    """Create a mock settings object with sensible defaults."""
    mock = MagicMock()
    mock.ast_tools_enabled = overrides.get("ast_tools_enabled", False)
    mock.pi_binary_path = overrides.get("pi_binary_path", None)
    return mock


def test_extension_flag_added_when_ast_tools_enabled():
    """PI command includes --extension flag when ast_tools_enabled is True."""
    options = PIAgentOptions(
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        system_prompt="test",
    )
    agent = PIAgentBase(options)

    with (
        patch(
            "baloo.agent.pi_runtime.get_settings",
            return_value=_make_mock_settings(ast_tools_enabled=True),
        ),
        patch("baloo.agent.pi_runtime.resolve_setting", return_value=True),
    ):
        cmd = agent._build_pi_command()

    assert "--extension" in cmd
    ext_idx = cmd.index("--extension")
    ext_path = cmd[ext_idx + 1]
    assert ext_path.endswith("baloo-ast-tools.ts")
    # --tools is an allowlist over built-in AND extension tools. Without the
    # AST names here the extension loads but the model can never call them
    # (verified against pi 0.73.1 and 0.85.1 via getAllTools()).
    tools = cmd[cmd.index("--tools") + 1].split(",")
    assert tools == ["read", "grep", "find", "ls", "ast_outline", "ast_grep", "ast_symbols"]


def test_extension_flag_omitted_when_ast_tools_disabled():
    """PI command has no --extension flag when ast_tools_enabled is False."""
    options = PIAgentOptions(
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        system_prompt="test",
    )
    agent = PIAgentBase(options)

    with (
        patch(
            "baloo.agent.pi_runtime.get_settings",
            return_value=_make_mock_settings(ast_tools_enabled=False),
        ),
        patch("baloo.agent.pi_runtime.resolve_setting", return_value=False),
    ):
        cmd = agent._build_pi_command()

    assert "--extension" not in cmd
    assert cmd[cmd.index("--tools") + 1] == "read,grep,find,ls"


def test_ast_tool_names_match_extension_registrations():
    """AST_TOOLS must track the names registered in baloo-ast-tools.ts.

    A rename on either side would silently drop the tool from the allowlist.
    """
    ext_path = Path(pi_runtime.__file__).resolve().parents[2] / "extensions" / "baloo-ast-tools.ts"
    registered = re.findall(r'^\s*name: "([a-z_]+)",$', ext_path.read_text(), re.MULTILINE)
    assert sorted(registered) == sorted(pi_runtime.AST_TOOLS)
    assert all(name in AST_TOOLS_PROMPT_SECTION for name in registered)


def test_extension_flag_omitted_when_no_tools():
    """PI command has no --extension when no_tools is True (e.g. thread agent)."""
    options = PIAgentOptions(
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        system_prompt="test",
        no_tools=True,
    )
    agent = PIAgentBase(options)

    with patch(
        "baloo.agent.pi_runtime.get_settings",
        return_value=_make_mock_settings(ast_tools_enabled=True),
    ):
        cmd = agent._build_pi_command()

    assert "--extension" not in cmd
