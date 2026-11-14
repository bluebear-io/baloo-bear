"""Path-rule matching for coding standards, equivalent to the Bluebear handler's skill
enforcement over `.bluebear/skill_enforcement.json`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from baloo.standards.glob import UnsupportedGlobError, match, validate

RULES_FILE = PurePosixPath(".bluebear/skill_enforcement.json")
SKILLS_DIR = PurePosixPath(".agents/skills")
_WRITE_CATEGORIES = ("", "*", "write_file")


class RulesFileError(ValueError):
    pass


class UnsafePathError(ValueError):
    pass


@dataclass(frozen=True)
class RuleSource:
    """A checked-out repository whose rules file and skills are read from disk."""

    name: str
    root: Path

    def skill_relpath(self, skill: str) -> PurePosixPath:
        return SKILLS_DIR / skill / "SKILL.md"

    def read(self, relpath: PurePosixPath | str) -> str | None:
        """Read a file of the checkout, or None when it does not exist.

        The checkout is PR-controlled: a symlink could point at a server file such as
        /proc/self/environ, whose content would reach the LLM and the PR. Only regular files
        whose path has no symlink component and stays inside the checkout are read.
        """
        root = self.root.resolve()
        path = root / relpath
        if not path.exists() and not path.is_symlink():
            return None
        resolved = path.resolve()
        if resolved != path or not resolved.is_relative_to(root):
            raise UnsafePathError(f"{self.name}/{relpath} is a symlink or leaves the repository")
        if not path.is_file():
            raise UnsafePathError(f"{self.name}/{relpath} is not a regular file")
        return path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class PathRule:
    path: str
    skill: str
    source: RuleSource


@dataclass(frozen=True)
class SkillRef:
    skill: str
    source: RuleSource


def load_rules(source: RuleSource) -> tuple[list[PathRule], list[str]]:
    """The rules that judge code: path rules for file writes, with no specific agent and
    without skip_coding_standards. A missing rules file means no rules.

    Also returns why each rule this matcher cannot honor was skipped; the other rules apply.
    """
    try:
        text = source.read(RULES_FILE)
    except UnicodeDecodeError as error:
        raise RulesFileError(f"{source.name}/{RULES_FILE} is not valid UTF-8") from error
    if text is None:
        return [], []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise RulesFileError(f"{source.name}/{RULES_FILE} is not valid JSON: {error}") from error
    raw_rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(raw_rules, list):
        raise RulesFileError(f"{source.name}/{RULES_FILE} has no 'rules' list")

    rules: list[PathRule] = []
    skipped: list[str] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise RulesFileError(f"{source.name}/{RULES_FILE} has a rule that is not an object")
        path = raw.get("path") or ""
        skill = raw.get("skill") or ""
        if not isinstance(path, str) or not isinstance(skill, str):
            raise RulesFileError(f"{source.name}/{RULES_FILE} has a non-string path or skill")
        if path in ("", "*") or not skill:
            continue
        if raw.get("tool_category", "") not in _WRITE_CATEGORIES:
            continue
        # A pull request has no agent, so agent-scoped rules never apply to one.
        if raw.get("agent"):
            continue
        if raw.get("skip_coding_standards"):
            continue
        # The skill name becomes a path under the skills directory and the PR author controls
        # it, so it must not escape that directory.
        if "/" in skill or "\\" in skill or ".." in skill:
            raise RulesFileError(f"{source.name}/{RULES_FILE} has an invalid skill name {skill!r}")
        pattern = normalize_pattern(path)
        try:
            validate(pattern)
        except UnsupportedGlobError as error:
            skipped.append(
                f"the {source.name}/{RULES_FILE} rule for skill `{skill}` was skipped: {error}"
            )
            continue
        rules.append(PathRule(path=pattern, skill=skill, source=source))
    return rules, skipped


def normalize_pattern(pattern: str) -> str:
    """The handler anchors every relative pattern at any depth."""
    pattern = pattern.replace("\\", "/")
    if pattern in ("*", "**") or pattern.startswith("/") or pattern.startswith("**/"):
        return pattern
    return "**/" + pattern


def match_files(
    target_name: str, files: list[str], rules: list[PathRule]
) -> dict[str, list[SkillRef]]:
    """Map each file (relative to the target repo root) to its skills, in rule order.

    A file is matched both as given and as `<target_name>/<file>`, which stands in for the
    handler's absolute path inside the umbrella workspace. As in the handler, the first rule
    naming a skill wins, so a target-repo skill shadows an umbrella skill of the same name.
    """
    matched: dict[str, list[SkillRef]] = {}
    for file in files:
        candidates = (file, f"{target_name}/{file}")
        seen: set[str] = set()
        refs: list[SkillRef] = []
        for rule in rules:
            if rule.skill in seen:
                continue
            if any(match(rule.path, candidate) for candidate in candidates):
                seen.add(rule.skill)
                refs.append(SkillRef(skill=rule.skill, source=rule.source))
        if refs:
            matched[file] = refs
    return matched
