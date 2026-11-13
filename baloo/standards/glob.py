"""Glob matching equivalent to github.com/bmatcuk/doublestar/v4 Match, the matcher the Bluebear
handler uses for `.bluebear/skill_enforcement.json` path rules.

Only the syntax those rules use is supported: `*`, `?` and the `**` segment. Character classes,
alternation and escapes raise, so a rule never silently matches differently from the handler.
"""

from __future__ import annotations

import functools
import re

_UNSUPPORTED = frozenset("[]{}\\")


class UnsupportedGlobError(ValueError):
    pass


@functools.cache
def _compile(pattern: str) -> re.Pattern[str]:
    unsupported = _UNSUPPORTED.intersection(pattern)
    if unsupported:
        raise UnsupportedGlobError(
            f"glob {pattern!r} uses unsupported syntax {sorted(unsupported)}; "
            "only *, ? and ** are supported"
        )
    segments = pattern.split("/")
    parts: list[str] = []
    separator_needed = False
    for index, segment in enumerate(segments):
        first = index == 0
        last = index == len(segments) - 1
        if segment == "**":
            # "**/" spans zero or more whole directories; a trailing "**" also matches the
            # directory itself, as doublestar does.
            if last:
                parts.append(".*" if first else "(?:/.*)?")
            else:
                parts.append("(?:.*/)?" if first else "/(?:.*/)?")
                separator_needed = False
            continue
        if separator_needed:
            parts.append("/")
        separator_needed = True
        parts.append(
            "".join(
                "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
                for char in segment
            )
        )
    return re.compile("".join(parts) + r"\Z")


def match(pattern: str, path: str) -> bool:
    return _compile(pattern).match(path) is not None
