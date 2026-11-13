"""LLM judge: one call per skill, covering every changed file that skill applies to."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from baloo.agent.config import get_agent_options
from baloo.agent.pi_runtime import PIAgentBase
from baloo.agent.tiers import RETRY_TURN_BUDGET
from baloo.standards.prompts import CODING_STANDARDS_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MIN_LINE_NUMBER_WIDTH = 4
_INT_FIELDS = ("start_line", "end_line", "caused_by_line")
_TEXT_FIELDS = ("file_path", "instruction_quote", "explanation", "fix_hint")


class JudgeError(RuntimeError):
    def __init__(self, message: str, metadata: dict[str, Any] | None = None):
        super().__init__(message)
        # A failed call still cost money; callers add it to the review's total.
        self.metadata = metadata or {}


@dataclass(frozen=True)
class JudgeFile:
    path: str
    lines: list[str]
    changed_ranges: list[tuple[int, int]]


@dataclass(frozen=True)
class Violation:
    file_path: str
    skill_name: str
    start_line: int
    end_line: int
    # A changed line inside the violation, so the finding anchors to a line in the PR diff.
    anchor_line: int
    instruction: str
    explanation: str
    fix_hint: str


@dataclass(frozen=True)
class JudgeCallResult:
    violations: list[Violation]
    metadata: dict[str, Any]


def file_lines(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def number_file(file: JudgeFile) -> str:
    """Render the file as fixed-width "NNNN| code" rows, with "NNNN+| " on changed lines."""
    width = max(MIN_LINE_NUMBER_WIDTH, len(str(len(file.lines))))
    changed = {line for start, end in file.changed_ranges for line in range(start, end + 1)}
    return "\n".join(
        f"{number:>{width}}{'+' if number in changed else ''}| {line}"
        for number, line in enumerate(file.lines, start=1)
    )


def _boundary(kind: str, content: str) -> str:
    # Derived from the content so the content cannot contain it.
    return f"{kind}_{hashlib.sha256(content.encode('utf-8')).hexdigest()[:24]}"


def build_message(skill_name: str, skill_content: str, files: list[JudgeFile]) -> str:
    skill_boundary = _boundary("SKILL", skill_content)
    parts = [
        f"SKILL NAME: {json.dumps(skill_name)}",
        f"===== BEGIN {skill_boundary} =====",
        skill_content,
        f"===== END {skill_boundary} =====",
    ]
    for file in files:
        numbered = number_file(file)
        file_boundary = _boundary("FILE", numbered)
        parts.extend(
            [
                "",
                f"FILE PATH: {json.dumps(file.path)}",
                f"CHANGED RANGES: {json.dumps([list(r) for r in file.changed_ranges])}",
                f"===== BEGIN {file_boundary} =====",
                numbered,
                f"===== END {file_boundary} =====",
            ]
        )
    return "\n".join(parts)


async def judge_skill(
    skill_name: str,
    skill_content: str,
    files: list[JudgeFile],
    *,
    model: str,
    review_logger: object | None = None,
) -> JudgeCallResult:
    if not skill_content.strip():
        raise JudgeError(f"skill {skill_name!r} is blank")
    answer, metadata = await _run_query(
        build_message(skill_name, skill_content, files), model, review_logger
    )
    raw = _model_violations(answer, metadata)
    violations = resolve_violations(raw, skill_name, files)
    logger.info(
        "Coding standards judge for skill %s: files=%d violations=%d raw=%d cost=$%.4f",
        skill_name,
        len(files),
        len(violations),
        len(raw),
        metadata.get("cost_usd") or 0.0,
    )
    return JudgeCallResult(violations=violations, metadata=metadata)


async def _run_query(
    prompt: str, model: str, review_logger: object | None
) -> tuple[Any, dict[str, Any]]:
    options = get_agent_options(model=model, thinking_level="off")
    options.system_prompt = CODING_STANDARDS_SYSTEM_PROMPT
    options.no_tools = True
    options.max_turns = RETRY_TURN_BUDGET
    options.name = "CodingStandardsJudge"
    return await PIAgentBase(options).run_query(prompt, review_logger=review_logger)


def _model_violations(answer: Any, metadata: dict[str, Any]) -> list[Any]:
    if answer is None:
        raise JudgeError("judge returned no parseable JSON answer", metadata)
    if not isinstance(answer, dict) or not isinstance(answer.get("violations"), list):
        raise JudgeError("judge answer has no violations list", metadata)
    violations: list[Any] = answer["violations"]
    return violations


def _is_well_formed(violation: Any) -> bool:
    if not isinstance(violation, dict):
        return False
    for name in _INT_FIELDS:
        value = violation.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    if not isinstance(violation.get("violates"), bool):
        return False
    return all(
        isinstance(violation.get(name), str) and violation[name].strip() for name in _TEXT_FIELDS
    )


def _anchor_line(
    start: int, end: int, caused_by_line: int, changed_ranges: list[tuple[int, int]]
) -> int | None:
    """The first changed line inside [start, end], else the causing changed line; None when
    the violation touches no changed line."""
    for range_start, range_end in changed_ranges:
        if range_start <= end and start <= range_end:
            return max(start, range_start)
    for range_start, range_end in changed_ranges:
        if range_start <= caused_by_line <= range_end:
            return caused_by_line
    return None


def resolve_violations(
    raw_violations: list[Any], skill_name: str, files: list[JudgeFile]
) -> list[Violation]:
    by_path = {file.path: file for file in files}
    violations: list[Violation] = []
    seen: set[tuple[str, str, int, int]] = set()
    dropped = {"malformed": 0, "not_violating": 0, "unknown_file": 0, "unchanged_lines": 0}
    for raw in raw_violations:
        if not _is_well_formed(raw):
            dropped["malformed"] += 1
            continue
        if not raw["violates"]:
            dropped["not_violating"] += 1
            continue
        file = by_path.get(raw["file_path"])
        if file is None:
            dropped["unknown_file"] += 1
            continue
        line_count = max(len(file.lines), 1)
        start, end = sorted(
            (min(max(raw["start_line"], 1), line_count), min(max(raw["end_line"], 1), line_count))
        )
        anchor = _anchor_line(start, end, raw["caused_by_line"], file.changed_ranges)
        if anchor is None:
            dropped["unchanged_lines"] += 1
            continue
        instruction = " ".join(raw["instruction_quote"].split())
        key = (file.path, instruction, start, end)
        if key in seen:
            continue
        seen.add(key)
        violations.append(
            Violation(
                file_path=file.path,
                skill_name=skill_name,
                start_line=start,
                end_line=end,
                anchor_line=anchor,
                instruction=instruction,
                explanation=raw["explanation"].strip(),
                fix_hint=raw["fix_hint"].strip(),
            )
        )
    if any(dropped.values()):
        logger.warning("Coding standards judge dropped violations for %s: %s", skill_name, dropped)
    return violations
