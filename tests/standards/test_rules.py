import json
from pathlib import Path

import pytest

from baloo.standards.rules import (
    RulesFileError,
    RuleSource,
    UnsafePathError,
    load_rules,
    match_files,
)


def _source(root: Path, name: str, rules: list[dict] | None) -> RuleSource:
    root.mkdir(parents=True, exist_ok=True)
    if rules is not None:
        (root / ".bluebear").mkdir()
        (root / ".bluebear" / "skill_enforcement.json").write_text(json.dumps({"rules": rules}))
    return RuleSource(name=name, root=root)


def _skills(target: str, files: list[str], *sources: RuleSource) -> dict[str, list[str]]:
    rules = [rule for source in sources for rule in load_rules(source)]
    return {
        path: [f"{ref.source.name}:{ref.skill}" for ref in refs]
        for path, refs in match_files(target, files, rules).items()
    }


def test_missing_rules_file_means_no_rules(tmp_path):
    assert load_rules(_source(tmp_path / "repo", "repo", None)) == []


def test_only_path_rules_that_judge_code_are_loaded(tmp_path):
    source = _source(
        tmp_path / "repo",
        "repo",
        [
            {"tool_category": "write_file", "path": "**/*.go", "skill": "go"},
            {"path": "**/*.py", "skill": "python"},
            {"tool_category": "*", "path": "**/*.ts", "skill": "ts"},
            {
                "tool_category": "write_file",
                "path": "**",
                "skill": "ticket",
                "skip_coding_standards": True,
            },
            {"tool_category": "write_file", "path": "**/*.rs", "skill": "rust", "agent": "codex"},
            {"tool_category": "run_command", "command": "git push", "skill": "git"},
            {"tool_category": "write_file", "path": "*", "skill": "everything"},
        ],
    )
    assert [rule.skill for rule in load_rules(source)] == ["go", "python", "ts"]


@pytest.mark.parametrize("skill", ["../x", "a/b", "a\\b", ".."])
def test_skill_names_cannot_leave_the_skills_directory(tmp_path, skill):
    source = _source(tmp_path / "repo", "repo", [{"path": "**", "skill": skill}])
    with pytest.raises(RulesFileError):
        load_rules(source)


def test_invalid_rules_file_fails(tmp_path):
    root = tmp_path / "repo"
    (root / ".bluebear").mkdir(parents=True)
    (root / ".bluebear" / "skill_enforcement.json").write_text("{not json")
    with pytest.raises(RulesFileError):
        load_rules(RuleSource(name="repo", root=root))


def test_relative_patterns_match_at_any_depth_like_the_handler(tmp_path):
    source = _source(
        tmp_path / "repo",
        "repo",
        [
            {"path": "tests/**", "skill": "testing"},
            {"path": "repo/internal/**", "skill": "internal"},
            {"path": "other/**", "skill": "other"},
        ],
    )
    files = ["tests/a.py", "src/tests/b.py", "internal/c.go", "other/d.go", "e.go"]
    assert _skills("repo", files, source) == {
        "tests/a.py": ["repo:testing"],
        "src/tests/b.py": ["repo:testing"],
        "internal/c.go": ["repo:internal"],
        "other/d.go": ["repo:other"],
    }


def test_umbrella_rules_address_the_target_by_repo_name(tmp_path):
    umbrella = _source(
        tmp_path / "umbrella",
        "umbrella",
        [
            {"path": "**/*.go", "skill": "go"},
            {"path": "svc/**", "skill": "svc-standards"},
            {"path": "svc/api/**", "skill": "api"},
            {"path": "web/**", "skill": "web"},
        ],
    )
    assert _skills("svc", ["main.go", "api/h.go", "README.md"], umbrella) == {
        "main.go": ["umbrella:go", "umbrella:svc-standards"],
        "api/h.go": ["umbrella:go", "umbrella:svc-standards", "umbrella:api"],
        "README.md": ["umbrella:svc-standards"],
    }


def test_first_rule_naming_a_skill_wins_so_target_skills_shadow_umbrella_skills(tmp_path):
    target = _source(tmp_path / "svc", "svc", [{"path": "**/*.go", "skill": "go"}])
    umbrella = _source(
        tmp_path / "umbrella",
        "umbrella",
        [{"path": "**/*.go", "skill": "go"}, {"path": "**/*.go", "skill": "lint"}],
    )
    assert _skills("svc", ["main.go"], target, umbrella) == {"main.go": ["svc:go", "umbrella:lint"]}


def test_read_rejects_symlinks_out_of_the_checkout(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOKEN=abc")
    root = tmp_path / "repo"
    (root / "inner").mkdir(parents=True)
    (root / "inner" / "real.py").write_text("x = 1\n")
    (root / "leak.py").symlink_to(secret)
    (root / "link_dir").symlink_to(root / "inner")
    source = RuleSource(name="repo", root=root)

    assert source.read("inner/real.py") == "x = 1\n"
    assert source.read("missing.py") is None
    with pytest.raises(UnsafePathError):
        source.read("leak.py")
    with pytest.raises(UnsafePathError):
        source.read("link_dir/real.py")
