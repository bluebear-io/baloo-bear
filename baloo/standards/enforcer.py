"""Coding standards enforcement for a PR: match changed files to skills and judge them."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from baloo.agent.repo_provision import provision_repo
from baloo.config.runtime_settings import resolve_setting
from baloo.config.settings import get_settings
from baloo.github.api_client import GitHubAPIClient
from baloo.github.models import (
    FileChange,
    FindingCategory,
    PRContext,
    ReviewComment,
    ReviewSeverity,
)
from baloo.standards.diff import changed_ranges
from baloo.standards.judge import (
    JudgeError,
    JudgeFile,
    Violation,
    file_lines,
    judge_skill,
    number_file,
)
from baloo.standards.rules import (
    RULES_FILE,
    RuleSource,
    SkillRef,
    UnsafePathError,
    load_rules,
    match_files,
)

logger = logging.getLogger(__name__)

# GitHub file statuses whose head version exists; "removed" and "unchanged" have nothing to judge.
_JUDGED_STATUSES = frozenset({"added", "modified", "renamed", "copied", "changed"})
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "cost_usd",
    "duration_seconds",
)


@dataclass
class StandardsReport:
    comments: list[ReviewComment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    judge_calls: int = 0
    metadata: dict[str, Any] = field(default_factory=lambda: {key: 0 for key in _USAGE_KEYS})

    def add_usage(self, metadata: dict[str, Any]) -> None:
        for key in _USAGE_KEYS:
            self.metadata[key] += metadata.get(key) or 0

    def summary_section(self) -> str:
        if not self.errors:
            return ""
        lines = ["⚠️ **Coding standards enforcement incomplete:**"]
        lines.extend(f"- {error}" for error in self.errors)
        return "\n".join(lines)


async def run_coding_standards(
    *,
    github_client: GitHubAPIClient,
    installation_id: int,
    pr_context: PRContext,
    repo_path: str | None,
    review_id: int | None = None,
) -> StandardsReport:
    """Never raises: every failure is reported in the returned report's errors."""
    try:
        return await _run(github_client, installation_id, pr_context, repo_path, review_id)
    except Exception as error:
        logger.exception(
            "Coding standards enforcement failed for %s#%s",
            pr_context.repo_full_name,
            pr_context.pr_number,
        )
        return StandardsReport(errors=[f"{type(error).__name__}: {error}"])


async def _run(
    github_client: GitHubAPIClient,
    installation_id: int,
    pr_context: PRContext,
    repo_path: str | None,
    review_id: int | None,
) -> StandardsReport:
    files = [f for f in pr_context.files_changed if f.status in _JUDGED_STATUSES]
    if not files:
        return StandardsReport()
    if repo_path is None:
        return StandardsReport(
            errors=["no checkout of the PR head is available, so no file could be judged"]
        )

    umbrella_repo = get_settings().coding_standards_umbrella_repo.strip()
    if not umbrella_repo or umbrella_repo.lower() == pr_context.repo_full_name.lower():
        return await enforce_on_checkouts(pr_context, Path(repo_path), None)

    umbrella_sha = await github_client.get_default_branch_head_sha(umbrella_repo)
    async with provision_repo(
        installation_id, umbrella_repo, umbrella_sha, review_id=review_id
    ) as checkout:
        if not checkout.available or checkout.path is None:
            return StandardsReport(
                errors=[
                    f"the umbrella repository {umbrella_repo} could not be checked out, so no "
                    "file was judged"
                ]
            )
        umbrella = RuleSource(name=umbrella_repo.split("/")[1], root=Path(checkout.path))
        return await enforce_on_checkouts(pr_context, Path(repo_path), umbrella)


async def enforce_on_checkouts(
    pr_context: PRContext, target_root: Path, umbrella: RuleSource | None
) -> StandardsReport:
    """Judge the PR's changed files given a checkout of its head and, optionally, of the
    umbrella repository. Raises on invalid rules; run_coding_standards reports it."""
    files = [f for f in pr_context.files_changed if f.status in _JUDGED_STATUSES]
    target_name = pr_context.repo_full_name.split("/")[1]
    target = RuleSource(name=target_name, root=target_root)
    # Target rules come first: the handler loads the closer directory's rules first.
    sources = [target] if umbrella is None else [target, umbrella]
    report = StandardsReport()
    rules = []
    for source in sources:
        source_rules, skipped = load_rules(source)
        rules.extend(source_rules)
        report.errors.extend(skipped)
    matched = match_files(target_name, [f.filename for f in files], rules)
    if matched:
        await _judge(target, files, matched, report)
    return report


async def _judge(
    target: RuleSource,
    files: list[FileChange],
    matched: dict[str, list[SkillRef]],
    report: StandardsReport,
) -> None:
    judge_files: dict[str, JudgeFile] = {}
    for change in files:
        if change.filename in matched:
            judge_file = _load_judge_file(target, change, report)
            if judge_file is not None:
                judge_files[change.filename] = judge_file

    by_skill: dict[SkillRef, list[JudgeFile]] = {}
    for path, refs in matched.items():
        if path in judge_files:
            for ref in refs:
                by_skill.setdefault(ref, []).append(judge_files[path])

    model = str(resolve_setting("coding_standards_model"))
    semaphore = asyncio.Semaphore(int(resolve_setting("coding_standards_max_concurrency")))

    async def run(
        ref: SkillRef, skill_content: str, batch: list[JudgeFile]
    ) -> tuple[SkillRef, list[Violation]]:
        async with semaphore:
            try:
                result = await judge_skill(ref.skill, skill_content, batch, model=model)
            except Exception as error:
                logger.exception("Coding standards judge failed for skill %s", ref.skill)
                if isinstance(error, JudgeError):
                    report.add_usage(error.metadata)
                report.errors.append(
                    f"judging `{ref.skill}` failed for {len(batch)} file(s): "
                    f"{type(error).__name__}: {error}"
                )
                return ref, []
            report.judge_calls += 1
            report.add_usage(result.metadata)
            return ref, result.violations

    tasks = []
    max_chars = int(resolve_setting("coding_standards_max_chars_per_call"))
    for ref, skill_files in by_skill.items():
        skill_relpath = ref.source.skill_relpath(ref.skill)
        try:
            skill_content = ref.source.read(skill_relpath)
        except UnsafePathError as error:
            report.errors.append(f"skill `{ref.skill}` was not used: {error}")
            continue
        except UnicodeDecodeError:
            report.errors.append(
                f"skill `{ref.skill}` was not used: {ref.source.name}/{skill_relpath} is not "
                "valid UTF-8"
            )
            continue
        if skill_content is None:
            report.errors.append(
                f"skill `{ref.skill}` is mapped by {ref.source.name}/{RULES_FILE} but "
                f"{ref.source.name}/{skill_relpath} does not exist"
            )
            continue
        for batch in _batches(skill_files, max_chars, report):
            tasks.append(run(ref, skill_content, batch))

    for ref, violations in await asyncio.gather(*tasks):
        location = f"{ref.source.name}/{ref.source.skill_relpath(ref.skill)}"
        report.comments.extend(_to_comment(violation, location) for violation in violations)


def _load_judge_file(
    target: RuleSource, change: FileChange, report: StandardsReport
) -> JudgeFile | None:
    try:
        content = target.read(change.filename)
    except UnicodeDecodeError:
        logger.info("Coding standards skips binary file %s", change.filename)
        return None
    except UnsafePathError as error:
        report.errors.append(f"`{change.filename}` was not judged: {error}")
        return None
    if content is None:
        report.errors.append(
            f"`{change.filename}` was not judged: it is missing from the checked-out head commit"
        )
        return None
    if not content or (change.patch is None and change.additions == 0):
        # GitHub returns no patch for an empty file or a rename without content changes;
        # neither has changed lines to judge.
        return None
    if change.patch is None:
        report.errors.append(
            f"`{change.filename}` was not judged: GitHub returned no patch for it, so its "
            "changed lines are unknown"
        )
        return None
    ranges = changed_ranges(change.patch)
    if not ranges:
        return None
    lines = file_lines(content)
    if ranges[-1][1] > len(lines):
        report.errors.append(
            f"`{change.filename}` was not judged: its patch changes line {ranges[-1][1]} but "
            f"the checked-out file has {len(lines)} lines"
        )
        return None
    return JudgeFile(path=change.filename, lines=lines, changed_ranges=ranges)


def _batches(
    files: list[JudgeFile], max_chars: int, report: StandardsReport
) -> list[list[JudgeFile]]:
    batches: list[list[JudgeFile]] = []
    current: list[JudgeFile] = []
    size = 0
    for file in files:
        file_size = len(number_file(file))
        if file_size > max_chars:
            report.errors.append(
                f"`{file.path}` was not judged: {file_size:,} characters exceed the "
                f"{max_chars:,} per-call limit"
            )
            continue
        if current and size + file_size > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(file)
        size += file_size
    if current:
        batches.append(current)
    return batches


def _to_comment(violation: Violation, skill_location: str) -> ReviewComment:
    lines = (
        f"L{violation.start_line}"
        if violation.start_line == violation.end_line
        else f"L{violation.start_line}-L{violation.end_line}"
    )
    body = "\n".join(
        [
            f"**Violates `{violation.skill_name}`** ({lines}): {violation.explanation}",
            "",
            f"**Rule** (`{skill_location}`):",
            f"> {violation.instruction}",
            "",
            f"**Fix:** {violation.fix_hint}",
            "",
            "_If this deviation is intended, add a comment next to the code that names the rule "
            "and gives the reason; the next review honors it._",
        ]
    )
    return ReviewComment(
        path=violation.file_path,
        line=violation.anchor_line,
        body=body,
        severity=ReviewSeverity.HIGH,
        category=FindingCategory.GUIDELINES,
    )
