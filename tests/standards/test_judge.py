from unittest.mock import AsyncMock, patch

import pytest

from baloo.standards.judge import (
    JudgeError,
    JudgeFile,
    build_message,
    judge_skill,
    resolve_violations,
)

FILE = JudgeFile(
    path="svc/a.py",
    lines=[f"line {n}" for n in range(1, 11)],
    changed_ranges=[(4, 5)],
)


def _raw(**overrides):
    violation = {
        "file_path": "svc/a.py",
        "instruction_quote": "Never   swallow\n exceptions.",
        "start_line": 4,
        "end_line": 4,
        "caused_by_line": 0,
        "explanation": "It swallows the exception.",
        "violates": True,
        "fix_hint": "Re-raise it.",
    }
    violation.update(overrides)
    return violation


def test_message_marks_changed_lines_of_every_file():
    other = JudgeFile(path="svc/b.py", lines=["x"], changed_ranges=[(1, 1)])
    message = build_message("python", "Never swallow exceptions.", [FILE, other])
    assert "   4+| line 4" in message
    assert "   3| line 3" in message
    assert 'FILE PATH: "svc/b.py"' in message
    assert "CHANGED RANGES: [[4, 5]]" in message


def test_violation_on_changed_lines_is_kept_and_anchored_to_a_changed_line():
    [violation] = resolve_violations([_raw(start_line=2, end_line=6)], "python", [FILE])
    assert (violation.start_line, violation.end_line, violation.anchor_line) == (2, 6, 4)
    assert violation.instruction == "Never swallow exceptions."
    assert violation.skill_name == "python"


def test_violation_only_in_unchanged_lines_is_dropped():
    assert resolve_violations([_raw(start_line=8, end_line=9)], "python", [FILE]) == []


def test_violation_caused_by_a_changed_line_anchors_to_that_line():
    [violation] = resolve_violations(
        [_raw(start_line=8, end_line=9, caused_by_line=5)], "python", [FILE]
    )
    assert violation.anchor_line == 5


def test_non_violations_unknown_files_malformed_and_duplicates_are_dropped():
    raw = [
        _raw(violates=False),
        _raw(file_path="other.py"),
        _raw(start_line="4"),
        _raw(explanation=" "),
        _raw(),
        _raw(),
    ]
    assert len(resolve_violations(raw, "python", [FILE])) == 1


async def test_judge_skill_returns_violations_and_cost():
    with patch(
        "baloo.standards.judge._run_query",
        AsyncMock(return_value=({"violations": [_raw()]}, {"cost_usd": 0.02})),
    ) as run_query:
        result = await judge_skill("python", "Never swallow exceptions.", [FILE], model="sonnet")
    run_query.assert_awaited_once()
    assert [v.file_path for v in result.violations] == ["svc/a.py"]
    assert result.metadata == {"cost_usd": 0.02}


async def test_unparseable_answer_fails_and_still_reports_its_cost():
    with patch(
        "baloo.standards.judge._run_query",
        AsyncMock(return_value=(None, {"cost_usd": 0.01})),
    ):
        with pytest.raises(JudgeError) as raised:
            await judge_skill("python", "rule", [FILE], model="sonnet")
    assert raised.value.metadata == {"cost_usd": 0.01}


async def test_judge_runs_without_tools_and_sends_the_skill_and_files():
    captured = {}

    class FakeAgent:
        def __init__(self, options):
            captured["options"] = options

        async def run_query(self, prompt, review_logger=None):
            captured["prompt"] = prompt
            return {"violations": []}, {"cost_usd": 0.0}

    with patch("baloo.standards.judge.PIAgentBase", FakeAgent):
        await judge_skill("python", "Never swallow exceptions.", [FILE], model="haiku")

    options = captured["options"]
    assert options.no_tools is True
    assert options.name == "CodingStandardsJudge"
    assert "Never swallow exceptions." in captured["prompt"]
    assert "   4+| line 4" in captured["prompt"]
