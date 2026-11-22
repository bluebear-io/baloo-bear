"""Turn-limit recovery must retain evidence and never approve partial reviews."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baloo.agent.pi_runtime import PIAgentBase, PIAgentOptions, PIRunResult


def agent() -> PIAgentBase:
    return PIAgentBase(PIAgentOptions(model="model", provider="anthropic", name="BalooAgent"))


def transcript() -> list[dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "toolCall", "name": "read", "arguments": {"path": "app.py"}},
                {"type": "text", "text": "Confirmed missing authorization check."},
            ],
        },
        {
            "role": "toolResult",
            "toolName": "read",
            "content": [
                {"type": "text", "text": "def update():\n    save()"},
            ],
        },
    ]


def finding() -> dict[str, Any]:
    return {
        "findings": [
            {
                "file": "app.py",
                "line": 2,
                "severity": "HIGH",
                "category": "Security",
                "title": "Missing authorization",
                "description": "Updates are not authorized",
            }
        ],
        "summary": {"total_issues": 1},
    }


def fake_proc() -> MagicMock:
    proc = MagicMock()
    proc.returncode = None
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def test_transcript_includes_evidence_and_limits_tool_output():
    text = agent()._transcript_text(transcript())
    assert "[tool call] read" in text
    assert "save()" in text
    assert "Confirmed missing authorization check" in text
    assert (
        len(
            agent()._transcript_text(
                [
                    {
                        "role": "toolResult",
                        "toolName": "read",
                        "content": [
                            {"type": "text", "text": "z" * 100000},
                        ],
                    },
                ]
            )
        )
        < 2000
    )


@pytest.mark.asyncio
async def test_no_evidence_skips_additional_inference():
    with patch("baloo.agent.pi_runtime.asyncio.create_subprocess_exec") as spawn:
        assert await agent()._finalize_from_transcript(
            "Review",
            [{"role": "assistant", "content": [{"type": "text", "text": "thinking"}]}],
            None,
        ) == (None, None)
        spawn.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final_text,expect_found",
    [
        (json.dumps(finding()), True),
        ('{"findings": [], "summary": {"total_issues": 0}}', False),
        ("not JSON", False),
    ],
)
async def test_finalizer_only_accepts_concrete_findings(final_text, expect_found):
    reviewer = agent()
    proc = fake_proc()
    completed = PIRunResult(assistant_text=final_text, model="model")
    with (
        patch(
            "baloo.agent.pi_runtime.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        patch.object(reviewer, "_drive_session", new=AsyncMock(return_value=completed)) as run,
    ):
        output, metadata = await reviewer._finalize_from_transcript(
            "Review this", transcript(), None
        )
    assert bool(output) is expect_found
    assert isinstance(metadata, dict)
    assert run.await_args.args[1].startswith("Review this")
    assert "no tools are available" in run.await_args.args[1]
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_capped_reviewer_fetches_messages():
    reviewer = agent()
    proc = fake_proc()
    canned = [
        {"type": "response", "command": "set_thinking_level", "success": True},
        {"type": "response", "command": "prompt", "success": True},
        {
            "type": "response",
            "command": "get_messages",
            "success": True,
            "data": {"messages": transcript()},
        },
    ]
    reviewer._read_event = AsyncMock(side_effect=canned)

    async def dispatch(_proc, result, _logger):
        result.max_turns_reached = True
        result.num_turns = 30

    reviewer._dispatch_events = AsyncMock(side_effect=dispatch)
    result = await reviewer._drive_session(proc, "Review", 0)
    assert result.final_messages == transcript()
    commands = [json.loads(call.args[0]) for call in proc.stdin.write.call_args_list]
    assert [item["type"] for item in commands] == ["set_thinking_level", "prompt", "get_messages"]


@pytest.mark.asyncio
async def test_non_review_agents_do_not_fetch_messages():
    reviewer = PIAgentBase(PIAgentOptions(name="ThreadAgent"))
    proc = fake_proc()
    reviewer._read_event = AsyncMock(
        side_effect=[
            {"type": "response", "command": "set_thinking_level", "success": True},
            {"type": "response", "command": "prompt", "success": True},
        ]
    )

    async def dispatch(_proc, result, _logger):
        result.max_turns_reached = True

    reviewer._dispatch_events = AsyncMock(side_effect=dispatch)
    result = await reviewer._drive_session(proc, "Review", 0)
    assert result.final_messages == []
    assert len(proc.stdin.write.call_args_list) == 2


@pytest.mark.asyncio
async def test_primary_recovered_findings_never_autoapprove():
    from baloo.agent.client import BalooAgent
    from baloo.github.models import FileChange, PRContext, PRDiscussionContext, PRMetadata

    context = PRContext(
        metadata=PRMetadata(
            repo_full_name="test/repo",
            pr_number=1,
            title="Code update",
            description="",
            author="developer",
            base_branch="main",
            head_branch="fix",
            head_sha="abc123",
            files_changed=[
                FileChange(
                    filename="app.py", status="modified", additions=1, deletions=1, changes=2
                )
            ],
        ),
        discussion=PRDiscussionContext(),
        diff="diff --git a/app.py b/app.py",
    )
    reviewer = BalooAgent()
    with (
        patch.object(
            reviewer,
            "run_query",
            new=AsyncMock(
                return_value=(finding(), {"partial_review": True, "turn_limit_recovered": True})
            ),
        ),
        patch("baloo.agent.client.DecisionEngine.make_decision", return_value=(True, False)),
    ):
        review = await reviewer.review_pr(context)
    assert review.approve is False
    assert review.request_changes is False
    assert review.metadata["partial_review"] is True
    assert "Partial review" in review.summary


@pytest.mark.asyncio
async def test_capped_json_repair_is_partial_even_without_transcript_finalization():
    reviewer = agent()
    capped = PIRunResult(
        assistant_text='{"findings": [',
        all_assistant_texts=['{"findings": ['],
        max_turns_reached=True,
        num_turns=30,
        model="model",
    )
    with (
        patch(
            "baloo.agent.pi_runtime.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc()),
        ),
        patch.object(reviewer, "_drive_session", new=AsyncMock(return_value=capped)),
        patch.object(
            reviewer,
            "_retry_json",
            new=AsyncMock(return_value=(finding(), {"cost_usd": 0.01}, json.dumps(finding()))),
        ) as retry,
    ):
        output, metadata = await reviewer.run_query("Review")
    assert output == finding()
    assert metadata["json_retry"] is True
    assert metadata["partial_review"] is True
    retry.assert_awaited_once()


@pytest.mark.asyncio
async def test_valid_json_at_turn_cap_skips_unneeded_transcript_fetch():
    reviewer = agent()
    proc = fake_proc()
    reviewer._read_event = AsyncMock(
        side_effect=[
            {"type": "response", "command": "set_thinking_level", "success": True},
            {"type": "response", "command": "prompt", "success": True},
        ]
    )

    async def dispatch(_proc, result, _logger):
        result.max_turns_reached = True
        result.assistant_text = json.dumps(finding())
        result.all_assistant_texts = [result.assistant_text]

    reviewer._dispatch_events = AsyncMock(side_effect=dispatch)
    result = await reviewer._drive_session(proc, "Review", 0)
    assert result.final_messages == []
    assert len(proc.stdin.write.call_args_list) == 2


@pytest.mark.asyncio
async def test_previous_valid_response_skips_unneeded_transcript_fetch():
    reviewer = agent()
    proc = fake_proc()
    reviewer._read_event = AsyncMock(
        side_effect=[
            {"type": "response", "command": "set_thinking_level", "success": True},
            {"type": "response", "command": "prompt", "success": True},
        ]
    )

    async def dispatch(_proc, result, _logger):
        result.max_turns_reached = True
        result.assistant_text = "late malformed message"
        result.all_assistant_texts = [json.dumps(finding()), result.assistant_text]

    reviewer._dispatch_events = AsyncMock(side_effect=dispatch)
    result = await reviewer._drive_session(proc, "Review", 0)
    assert result.final_messages == []
    assert len(proc.stdin.write.call_args_list) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["finalizer", "json_retry"])
async def test_followup_sessions_share_cli_builder_and_sandbox(operation, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("GITHUB_PRIVATE_KEY", "DO_NOT_PROPAGATE")
    reviewer = agent()
    proc = fake_proc()
    result = PIRunResult(assistant_text=json.dumps(finding()), model="model")
    settings = SimpleNamespace(pi_binary_path="pi", repo_sandbox_mode="bwrap")
    with (
        patch("baloo.agent.pi_runtime.get_settings", return_value=settings),
        patch("baloo.agent.sandbox.sandbox_available", return_value=True),
        patch(
            "baloo.agent.pi_runtime.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ) as spawn,
        patch.object(reviewer, "_drive_session", new=AsyncMock(return_value=result)),
    ):
        if operation == "finalizer":
            await reviewer._finalize_from_transcript("Review", transcript(), "/work/tree")
        else:
            await reviewer._retry_json(raw_text="broken", proc_cwd="/work/tree")
    args = spawn.await_args.args
    assert args[0] == "bwrap"
    assert "--" in args
    pi_args = args[args.index("--") + 1 :]
    assert pi_args[0] == "pi"
    assert "--no-tools" in pi_args
    assert "--no-context-files" in pi_args
    assert "--no-skills" in pi_args
    assert spawn.await_args.kwargs["env"] is not None
    assert "GITHUB_PRIVATE_KEY" not in spawn.await_args.kwargs["env"]
    assert reviewer.options.max_turns != 2


@pytest.mark.asyncio
async def test_json_retry_spawn_failure_restores_original_options():
    reviewer = agent()
    original_options = reviewer.options
    with patch(
        "baloo.agent.pi_runtime.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=OSError("spawn failed")),
    ):
        parsed, metadata, raw = await reviewer._retry_json(raw_text="broken", proc_cwd=None)
    assert parsed is None and metadata is None and raw is None
    assert reviewer.options is original_options
