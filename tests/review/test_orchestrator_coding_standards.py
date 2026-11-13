"""Coding standards findings through the full review pipeline (GitHub and the LLM stubbed)."""

import json
from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from baloo.agent.repo_provision import Checkout
from baloo.github.models import FileChange, FindingCategory, ReviewComment, ReviewSeverity
from tests.review.test_orchestrator import (
    _base_patches,
    _make_agent,
    _make_github_client,
    _make_pr_context,
)

SKILL_TEXT = "# Python\n\n- Never swallow exceptions.\n"
CODE = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
PATCH = "@@ -0,0 +1,5 @@\n" + "\n".join(f"+{line}" for line in CODE.splitlines())


def _checkout(tmp_path, skills: dict[str, str]):
    root = tmp_path / "worktree"
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "a.py").write_text(CODE)
    (root / ".bluebear").mkdir()
    (root / ".bluebear" / "skill_enforcement.json").write_text(
        json.dumps(
            {"rules": [{"tool_category": "write_file", "path": "**/*.py", "skill": "python"}]}
        )
    )
    for name, text in skills.items():
        skill_dir = root / ".agents" / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(text)
    return root


def _judge_answer(**overrides):
    occurrence = {
        "file_path": "svc/a.py",
        "start_line": 4,
        "end_line": 5,
        "caused_by_line": 0,
        "explanation": "The except block discards the error.",
        "violates": True,
        "fix_hint": "Re-raise the exception or return an error status.",
    }
    occurrence.update(overrides)
    return {
        "checklist": [
            {"instruction_quote": "Never swallow exceptions.", "occurrences": [occurrence]}
        ]
    }


async def _review(tmp_path, *, enabled, skills, judge, agent_comments=None):
    root = _checkout(tmp_path, skills)
    ctx = _make_pr_context()
    ctx.files_changed = [
        FileChange(
            filename="svc/a.py", status="added", additions=5, deletions=0, changes=5, patch=PATCH
        )
    ]
    gc = _make_github_client(ctx)
    agent = _make_agent(comments=agent_comments)

    @asynccontextmanager
    async def fake_provision(installation_id, repo_full_name, head_sha, review_id=None):
        yield Checkout(path=str(root), available=True)

    from baloo.review.orchestrator import process_pr_review

    with ExitStack() as stack:
        for p in _base_patches(gc, agent, coding_standards_enabled=enabled):
            stack.enter_context(p)
        stack.enter_context(patch("baloo.review.orchestrator.provision_repo", fake_provision))
        stack.enter_context(patch("baloo.standards.judge._run_query", judge))
        await process_pr_review(
            repo_full_name="org/repo",
            pr_number=1,
            installation_id=1,
            trigger_reason="pull_request:opened",
            notify_progress=False,
            head_sha="abc123",
        )
    return gc


def _posted_reviews(gc):
    return [
        call.args[2] if len(call.args) >= 3 else call.kwargs["review_result"]
        for call in gc.post_review.call_args_list
    ]


async def test_violation_is_posted_as_a_blocking_guidelines_finding(tmp_path):
    judge = AsyncMock(return_value=(_judge_answer(), {"cost_usd": 0.02}))

    gc = await _review(tmp_path, enabled=True, skills={"python": SKILL_TEXT}, judge=judge)

    judge.assert_awaited_once()
    [review] = [r for r in _posted_reviews(gc) if r.request_changes]
    [comment] = review.comments
    assert (comment.path, comment.line) == ("svc/a.py", 4)
    assert comment.severity == ReviewSeverity.HIGH
    assert comment.category == FindingCategory.GUIDELINES
    assert "Never swallow exceptions." in comment.body
    assert "Re-raise the exception or return an error status." in comment.body
    assert "python/SKILL.md" in comment.body


def test_judge_cost_is_part_of_the_review_total():
    from baloo.review.orchestrator import _total_review_cost_usd

    review_metadata = {"cost_usd": 0.10, "coding_standards": {"cost_usd": 0.02}}
    assert _total_review_cost_usd(review_metadata, {"cost_usd": 0.01}) == pytest.approx(0.13)


async def test_standards_findings_join_the_main_review_findings(tmp_path):
    judge = AsyncMock(return_value=(_judge_answer(), {"cost_usd": 0.0}))
    agent_finding = ReviewComment(
        path="svc/b.py",
        line=3,
        body="**Null dereference**\n\nThe value can be None here and is used without a check.",
        severity=ReviewSeverity.HIGH,
        category=FindingCategory.BUGS,
    )

    gc = await _review(
        tmp_path,
        enabled=True,
        skills={"python": SKILL_TEXT},
        judge=judge,
        agent_comments=[agent_finding],
    )

    [review] = [r for r in _posted_reviews(gc) if r.request_changes]
    assert sorted(c.path for c in review.comments) == ["svc/a.py", "svc/b.py"]


async def test_disabled_feature_never_calls_the_judge(tmp_path):
    judge = AsyncMock(return_value=(_judge_answer(), {"cost_usd": 0.0}))

    gc = await _review(tmp_path, enabled=False, skills={"python": SKILL_TEXT}, judge=judge)

    judge.assert_not_awaited()
    assert not any(r.request_changes for r in _posted_reviews(gc))


async def test_enforcement_problems_are_listed_in_the_review_summary(tmp_path):
    judge = AsyncMock(return_value=(_judge_answer(), {"cost_usd": 0.0}))

    gc = await _review(tmp_path, enabled=True, skills={}, judge=judge)

    judge.assert_not_awaited()
    summaries = [r.summary for r in _posted_reviews(gc)]
    assert any(
        "Coding standards enforcement incomplete" in s and "python" in s for s in summaries
    ), summaries
