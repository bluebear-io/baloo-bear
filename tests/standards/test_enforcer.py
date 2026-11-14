import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from baloo.agent.repo_provision import Checkout
from baloo.github.models import FileChange, PRContext, PRDiscussionContext, PRMetadata
from baloo.standards import enforcer
from baloo.standards.judge import JudgeCallResult, JudgeError, JudgeFile, Violation

SKILL_TEXT = "# Python\n\n- Never swallow exceptions.\n"
UMBRELLA = "acme/umbrella"


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def _rules(rules: list[dict]) -> str:
    return json.dumps({"rules": rules})


def _pr(repo: str, files: list[FileChange]) -> PRContext:
    return PRContext(
        metadata=PRMetadata(
            repo_full_name=repo,
            pr_number=7,
            title="t",
            description=None,
            author="dev",
            base_branch="main",
            head_branch="feat",
            head_sha="abc123",
            files_changed=files,
        ),
        discussion=PRDiscussionContext(),
        diff="",
    )


def _change(name: str, patch: str | None = "@@ -0,0 +1,2 @@\n+a\n+b", status="added"):
    return FileChange(
        filename=name, status=status, additions=2, deletions=0, changes=2, patch=patch
    )


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """A PR checkout for acme/svc plus an umbrella repo served by a fake provision_repo."""
    target = tmp_path / "checkout-svc"
    umbrella = tmp_path / "src-umbrella"
    target.mkdir()
    umbrella.mkdir()
    provisioned = []

    @asynccontextmanager
    async def fake_provision(installation_id, repo_full_name, head_sha, review_id=None):
        assert (installation_id, repo_full_name, head_sha) == (1, UMBRELLA, "umb-sha")
        dest = tmp_path / f"worktree-{len(provisioned)}"
        shutil.copytree(umbrella, dest, symlinks=True)
        provisioned.append(dest)
        yield Checkout(path=str(dest), available=True)

    monkeypatch.setattr(enforcer, "provision_repo", fake_provision)
    monkeypatch.setenv("CODING_STANDARDS_UMBRELLA_REPO", UMBRELLA)
    return {"target": target, "umbrella": umbrella, "provisioned": provisioned}


@pytest.fixture
def github_client():
    client = MagicMock()
    client.get_default_branch_head_sha = AsyncMock(return_value="umb-sha")
    return client


@pytest.fixture
def judge_calls(monkeypatch):
    calls = []

    async def judge_skill(skill, content, files: list[JudgeFile], *, model, review_logger=None):
        calls.append((skill, content, [f.path for f in files]))
        violations = [
            Violation(
                file_path=f.path,
                skill_name=skill,
                start_line=1,
                end_line=2,
                anchor_line=1,
                instruction="Never swallow exceptions.",
                explanation="It swallows the exception.",
                fix_hint="Re-raise it.",
            )
            for f in files
        ]
        return JudgeCallResult(violations=violations, metadata={"cost_usd": 0.01})

    monkeypatch.setattr(enforcer, "judge_skill", judge_skill)
    return calls


async def _run(github_client, repo, files, repo_path):
    return await enforcer.run_coding_standards(
        github_client=github_client,
        installation_id=1,
        pr_context=_pr(repo, files),
        repo_path=repo_path,
    )


async def test_umbrella_rules_produce_high_guidelines_findings_one_call_per_skill(
    repos, github_client, judge_calls
):
    _write(repos["target"], {"a.py": "a\nb\n", "api/b.py": "a\nb\n", "c.md": "a\nb\n"})
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules(
                [{"path": "**/*.py", "skill": "python"}, {"path": "web/**", "skill": "web"}]
            ),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )

    report = await _run(
        github_client,
        "acme/svc",
        [_change("a.py"), _change("api/b.py"), _change("c.md")],
        str(repos["target"]),
    )

    assert report.errors == []
    assert judge_calls == [("python", SKILL_TEXT, ["a.py", "api/b.py"])]
    assert [(c.path, c.line, c.severity.value, c.category.value) for c in report.comments] == [
        ("a.py", 1, "HIGH", "Guidelines"),
        ("api/b.py", 1, "HIGH", "Guidelines"),
    ]
    assert "umbrella/.agents/skills/python/SKILL.md" in report.comments[0].body
    assert "> Never swallow exceptions." in report.comments[0].body
    assert report.judge_calls == 1
    assert report.metadata["cost_usd"] == pytest.approx(0.01)


async def test_target_rules_use_the_target_skill(repos, github_client, judge_calls):
    _write(
        repos["target"],
        {
            "a.go": "a\nb\n",
            ".bluebear/skill_enforcement.json": _rules([{"path": "**/*.go", "skill": "go"}]),
            ".agents/skills/go/SKILL.md": "target go skill",
        },
    )
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules([{"path": "**/*.go", "skill": "go"}]),
            ".agents/skills/go/SKILL.md": "umbrella go skill",
        },
    )

    report = await _run(github_client, "acme/svc", [_change("a.go")], str(repos["target"]))

    assert judge_calls == [("go", "target go skill", ["a.go"])]
    assert "svc/.agents/skills/go/SKILL.md" in report.comments[0].body


async def test_a_pr_in_the_umbrella_uses_only_its_own_checkout(repos, github_client, judge_calls):
    _write(
        repos["target"],
        {
            "svc/x.py": "a\nb\n",
            ".bluebear/skill_enforcement.json": _rules([{"path": "svc/**", "skill": "python"}]),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )

    report = await _run(github_client, UMBRELLA, [_change("svc/x.py")], str(repos["target"]))

    assert judge_calls == [("python", SKILL_TEXT, ["svc/x.py"])]
    assert report.errors == []
    assert repos["provisioned"] == []
    github_client.get_default_branch_head_sha.assert_not_awaited()


async def test_no_rules_means_no_findings_and_no_judge_calls(repos, github_client, judge_calls):
    _write(repos["target"], {"a.py": "a\nb\n"})

    report = await _run(github_client, "acme/svc", [_change("a.py")], str(repos["target"]))

    assert (report.comments, report.errors, judge_calls) == ([], [], [])


async def test_without_an_umbrella_only_target_rules_apply(
    repos, github_client, judge_calls, monkeypatch
):
    monkeypatch.setenv("CODING_STANDARDS_UMBRELLA_REPO", "")
    _write(repos["target"], {"a.py": "a\nb\n"})
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules([{"path": "**/*.py", "skill": "python"}]),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )

    report = await _run(github_client, "acme/svc", [_change("a.py")], str(repos["target"]))

    assert (report.comments, judge_calls, repos["provisioned"]) == ([], [], [])


async def test_problems_are_reported_in_the_summary_not_hidden(
    repos, github_client, judge_calls, tmp_path
):
    (tmp_path / "server-secret").write_text("ANTHROPIC_API_KEY=x\n")
    _write(repos["target"], {"ok.py": "a\nb\n", "no_patch.py": "a\nb\n"})
    (repos["target"] / "img.py").write_bytes(b"\xff\xfe\x00")
    (repos["target"] / "leak.py").symlink_to(tmp_path / "server-secret")
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules(
                [{"path": "**/*.py", "skill": "python"}, {"path": "**/*.py", "skill": "missing"}]
            ),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )

    report = await _run(
        github_client,
        "acme/svc",
        [
            _change("ok.py"),
            _change("no_patch.py", patch=None),
            _change("img.py", patch=None),
            _change("leak.py"),
            _change("gone.py", status="removed"),
        ],
        str(repos["target"]),
    )

    assert judge_calls == [("python", SKILL_TEXT, ["ok.py"])]
    assert len(report.errors) == 3
    assert any("no_patch.py" in e and "no patch" in e for e in report.errors)
    assert any("leak.py" in e and "symlink" in e for e in report.errors)
    assert any("missing" in e and "does not exist" in e for e in report.errors)
    assert "ANTHROPIC_API_KEY" not in json.dumps(judge_calls)
    assert "Coding standards enforcement incomplete" in report.summary_section()


async def test_missing_checkout_and_broken_rules_become_errors(repos, github_client, judge_calls):
    no_checkout = await _run(github_client, "acme/svc", [_change("a.py")], None)
    assert len(no_checkout.errors) == 1 and "no checkout" in no_checkout.errors[0]

    _write(repos["target"], {"a.py": "a\nb\n"})
    _write(repos["umbrella"], {".bluebear/skill_enforcement.json": "{broken"})
    broken = await _run(github_client, "acme/svc", [_change("a.py")], str(repos["target"]))
    assert broken.comments == []
    assert len(broken.errors) == 1 and "RulesFileError" in broken.errors[0]


async def test_unavailable_umbrella_checkout_is_an_error(
    repos, github_client, judge_calls, monkeypatch
):
    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        yield Checkout(path=None, available=False)

    monkeypatch.setattr(enforcer, "provision_repo", unavailable)
    _write(repos["target"], {"a.py": "a\nb\n"})

    report = await _run(github_client, "acme/svc", [_change("a.py")], str(repos["target"]))

    assert judge_calls == []
    assert len(report.errors) == 1 and UMBRELLA in report.errors[0]


async def test_a_failed_judge_call_is_reported_and_its_cost_counted(
    repos, github_client, monkeypatch
):
    async def failing(*args, **kwargs):
        raise JudgeError("judge returned no parseable JSON answer", {"cost_usd": 0.03})

    monkeypatch.setattr(enforcer, "judge_skill", failing)
    _write(repos["target"], {"a.py": "a\nb\n"})
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules([{"path": "**/*.py", "skill": "python"}]),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )

    report = await _run(github_client, "acme/svc", [_change("a.py")], str(repos["target"]))

    assert report.comments == []
    assert len(report.errors) == 1 and "python" in report.errors[0]
    assert report.metadata["cost_usd"] == pytest.approx(0.03)


async def test_files_over_the_call_limit_split_into_batches(
    repos, github_client, judge_calls, monkeypatch
):
    monkeypatch.setenv("CODING_STANDARDS_MAX_CHARS_PER_CALL", "30")
    _write(
        repos["target"],
        {"a.py": "a\nb\n", "b.py": "a\nb\n", "huge.py": "x" * 40 + "\nb\n"},
    )
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules([{"path": "**/*.py", "skill": "python"}]),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )

    report = await _run(
        github_client,
        "acme/svc",
        [_change("a.py"), _change("b.py"), _change("huge.py")],
        str(repos["target"]),
    )

    assert sorted(files for _, _, files in judge_calls) == [["a.py"], ["b.py"]]
    assert len(report.errors) == 1 and "huge.py" in report.errors[0]


async def test_an_empty_file_has_nothing_to_judge_and_is_not_an_error(
    repos, github_client, judge_calls
):
    # GitHub returns no patch for an empty file.
    _write(repos["target"], {"pkg/__init__.py": "", "a.py": "a\nb\n"})
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules([{"path": "**/*.py", "skill": "python"}]),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )

    report = await _run(
        github_client,
        "acme/svc",
        [_change("pkg/__init__.py", patch=None), _change("a.py")],
        str(repos["target"]),
    )

    assert report.errors == []
    assert judge_calls == [("python", SKILL_TEXT, ["a.py"])]


async def test_a_pure_rename_has_nothing_to_judge_and_is_not_an_error(
    repos, github_client, judge_calls
):
    # GitHub returns no patch for a rename without content changes.
    _write(repos["target"], {"moved.py": "a\nb\n"})
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules([{"path": "**/*.py", "skill": "python"}]),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )
    rename = FileChange(
        filename="moved.py", status="renamed", additions=0, deletions=0, changes=0, patch=None
    )

    report = await _run(github_client, "acme/svc", [rename], str(repos["target"]))

    assert judge_calls == []
    assert report.errors == []


async def test_an_unreadable_skill_fails_only_that_skill(repos, github_client, judge_calls):
    _write(repos["target"], {"a.py": "a\nb\n"})
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules(
                [{"path": "**/*.py", "skill": "python"}, {"path": "**/*.py", "skill": "binary"}]
            ),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )
    (repos["umbrella"] / ".agents/skills/binary").mkdir(parents=True)
    (repos["umbrella"] / ".agents/skills/binary/SKILL.md").write_bytes(b"\xff\xfe\x00")

    report = await _run(github_client, "acme/svc", [_change("a.py")], str(repos["target"]))

    assert [skill for skill, _, _ in judge_calls] == ["python"]
    assert len(report.comments) == 1
    assert len(report.errors) == 1 and "binary" in report.errors[0]


async def test_a_rule_with_unsupported_glob_syntax_fails_only_that_rule(
    repos, github_client, judge_calls
):
    _write(repos["target"], {"a.py": "a\nb\n"})
    _write(
        repos["umbrella"],
        {
            ".bluebear/skill_enforcement.json": _rules(
                [
                    {"path": "**/*.{py,pyi}", "skill": "typing"},
                    {"path": "**/*.py", "skill": "python"},
                ]
            ),
            ".agents/skills/python/SKILL.md": SKILL_TEXT,
        },
    )

    report = await _run(github_client, "acme/svc", [_change("a.py")], str(repos["target"]))

    assert [skill for skill, _, _ in judge_calls] == ["python"]
    assert len(report.errors) == 1
    assert "typing" in report.errors[0] and "**/*.{py,pyi}" in report.errors[0]
