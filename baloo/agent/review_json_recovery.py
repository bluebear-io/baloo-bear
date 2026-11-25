"""Safely reconcile the reviewer's malformed JSON with a formatting retry.

Preserve validated complete findings from the original generation, never let a
retry silently drop them, and fail closed when the source is incomplete.
All processing is bounded and deterministic; no additional model calls.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

# Bound scanning of untrusted model output, not whole git files.
_MAX_TEXT = 262_144
_MAX_ITEMS = 40
_SECTION = {
    name: re.compile(r'"' + name + r'"\s*:\s*\[') for name in ("findings", "general_findings")
}


def _complete_object_slices(text: str, start: int):
    """Yield completed object literals in an array, tracking quotes and braces.

    Trailing incomplete objects are *not* recovered. No JSON structure is
    invented or closed heuristically, unlike dangerous generic JSON repair.
    """
    depth = 0
    in_string = False
    escaped = False
    begin = None
    for i in range(start, min(len(text), start + _MAX_TEXT)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                begin = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and begin is not None:
                    yield text[begin : i + 1], i + 1
                    begin = None
        elif ch == "]" and depth == 0:
            return


# Restrict tolerant parsing to the documented review finding fields. Every
# character of each recovered value must be retained, including source quotes.
_FINDING_KEYS = frozenset(
    {
        "file",
        "line",
        "severity",
        "category",
        "title",
        "description",
        "impact",
        "recommendation",
        "code_example",
    }
)
_FIELD_NAME = re.compile(r'"([a-z_]+)"\s*:')


def _decode_complete_finding(fragment: str, decode: Callable[[str], Any]) -> Any:
    """Decode a full finding with narrowly scoped unescaped-quote recovery.

    Does not invent fields, auto-close an incomplete object, or allow extra
    JSON structure. A quote within a string is escaped only when it cannot
    terminate that value at a legitimate finding-key boundary.
    """
    try:
        return decode(fragment)
    except (ValueError, TypeError):
        pass

    if len(fragment) > 32_768 or not fragment.startswith("{") or not fragment.endswith("}"):
        return None
    position = 1
    result: dict[str, Any] = {}
    decoder = json.JSONDecoder()
    while True:
        while position < len(fragment) and fragment[position].isspace():
            position += 1
        if position >= len(fragment):
            return None
        if fragment[position] == "}":
            return result if fragment[position + 1 :].strip() == "" else None
        match = _FIELD_NAME.match(fragment, position)
        if match is None:
            return None
        key = match.group(1)
        if key not in _FINDING_KEYS or key in result:
            return None
        position = match.end()
        while position < len(fragment) and fragment[position].isspace():
            position += 1
        if position >= len(fragment):
            return None
        if fragment[position] != '"':
            try:
                value, consumed = decoder.raw_decode(fragment[position:])
            except ValueError:
                return None
            position += consumed
        else:
            position += 1
            pieces: list[str] = []
            while position < len(fragment):
                char = fragment[position]
                if char == "\\" and position + 1 < len(fragment):
                    pieces.append(fragment[position : position + 2])
                    position += 2
                    continue
                if char == '"':
                    rest = fragment[position + 1 :]
                    is_final = bool(re.match(r"\s*}\s*$", rest))
                    next_key = re.match(r'\s*,\s*"([a-z_]+)"\s*:', rest)
                    valid_next = bool(next_key and next_key.group(1) in _FINDING_KEYS)
                    if is_final or valid_next:
                        position += 1
                        break
                    pieces.append('\\"')
                else:
                    pieces.append(char)
                position += 1
            else:
                return None
            try:
                value = json.loads('"' + "".join(pieces) + '"')
            except ValueError:
                return None
        result[key] = value
        while position < len(fragment) and fragment[position].isspace():
            position += 1
        if position < len(fragment) and fragment[position] == ",":
            position += 1
            continue
        if position < len(fragment) and fragment[position] == "}":
            return result if fragment[position + 1 :].strip() == "" else None
        return None


def _valid_finding(row: Any, *, general: bool) -> bool:
    if not isinstance(row, dict):
        return False
    for field in ("title", "description", "severity", "category"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            return False
    if len(row["title"]) < 4 or len(row["description"]) < 18:
        return False
    if row["severity"].upper() not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return False
    if not general:
        path = row.get("file")
        if not isinstance(path, str) or not path.strip() or path.startswith("/"):
            return False
        try:
            if int(row.get("line")) < 1:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _recover_complete_items(
    raw: str, decode: Callable[[str], Any]
) -> tuple[dict[str, list[dict]], bool, bool]:
    found: dict[str, list[dict]] = {"findings": [], "general_findings": []}
    claims_findings = False
    incomplete_section = False
    if not isinstance(raw, str) or len(raw) > _MAX_TEXT:
        # Oversized malformed responses must be treated as uncertain, not clean.
        return found, True, True
    for key, pattern in _SECTION.items():
        match = pattern.search(raw)
        if match is None:
            continue
        # A non-empty findings array claimed in malformed source is evidence
        # that an empty reformatter reply is not a trustworthy clean result.
        start = match.end()
        if re.match(r"\s*{", raw[start:]):
            claims_findings = True
        slices = list(_complete_object_slices(raw, start))
        # Use the scanner's actual final offset: identical objects may appear
        # more than once, and str.find would select the first occurrence.
        tail = raw[slices[-1][1] :] if slices else raw[start:]
        if not re.match(r"\s*\]\s*(?:,|}|$)", tail):
            incomplete_section = True
        if len(slices) > _MAX_ITEMS:
            incomplete_section = True
        for fragment, _end in slices[:_MAX_ITEMS]:
            try:
                row = _decode_complete_finding(fragment, decode)
            except (ValueError, TypeError):
                row = None
            if _valid_finding(row, general=key == "general_findings"):
                found[key].append(row)
            else:
                incomplete_section = True
    return found, claims_findings, incomplete_section


def reconcile_review_json(
    raw_original: str,
    repaired_output: dict[str, Any] | None,
    decode: Callable[[str], Any] = json.loads,
) -> tuple[dict[str, Any] | None, str]:
    """Preserve complete source findings across JSON retry, or fail closed.

    Returns (output, status). Uncertain recovery uses output=None so the
    existing BalooAgent failure path marks the review failed in GitHub.
    Never treats arbitrary prose or partial finding objects as a finding.
    """
    source, claims, incomplete = _recover_complete_items(raw_original, decode)
    source_count = sum(map(len, source.values()))
    retry = repaired_output if isinstance(repaired_output, dict) else None
    if retry is not None:
        for key in source:
            if not isinstance(retry.get(key, []), list):
                return None, "invalid_retry_schema"

    if incomplete or (claims and source_count == 0):
        return None, "unrecoverable_source_findings"
    if not source_count:
        return retry, "no_source_findings"

    # A review has only one terminal generation. The formatting retry may
    # repair syntax, but must not rewrite away completed source findings.
    merged = dict(retry or {})
    for key, originals in source.items():
        items = list(merged.get(key) or [])
        for original in originals:
            identity = (
                str(original.get("file", "")),
                str(original.get("line", "")),
                original["title"].strip().casefold(),
            )
            existing = {
                (
                    str(item.get("file", "")),
                    str(item.get("line", "")),
                    str(item.get("title", "")).strip().casefold(),
                )
                for item in items
                if isinstance(item, dict)
            }
            if identity not in existing:
                items.append(original)
        merged[key] = items
    if not isinstance(merged.get("summary"), dict):
        merged["summary"] = {}
    merged["summary"]["total_issues"] = len(merged.get("findings", [])) + len(
        merged.get("general_findings", [])
    )
    return merged, "preserved_source_findings"
