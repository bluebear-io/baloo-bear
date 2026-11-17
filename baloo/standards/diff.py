"""Changed line ranges of a file, from the unified diff patch GitHub returns for a PR file."""

from __future__ import annotations

import re

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class PatchParseError(ValueError):
    pass


def changed_ranges(patch: str) -> list[tuple[int, int]]:
    """1-based inclusive new-file ranges of added lines.

    These are the new-side ranges of `git diff -U0` hunks, which the handler judges; pure
    deletions add no range.
    """
    ranges: list[tuple[int, int]] = []
    new_line: int | None = None
    run_start: int | None = None

    def close_run(end: int) -> None:
        nonlocal run_start
        if run_start is not None:
            ranges.append((run_start, end))
            run_start = None

    for raw in patch.split("\n"):
        header = _HUNK_HEADER.match(raw)
        if header:
            if new_line is not None:
                close_run(new_line - 1)
            new_line = int(header.group(1))
            continue
        if new_line is None:
            raise PatchParseError("patch content before the first hunk header")
        if raw.startswith("+"):
            if run_start is None:
                run_start = new_line
            new_line += 1
        elif raw.startswith(("-", "\\")):
            # Removed lines do not exist on the new side, and "\ No newline at end of file"
            # is a marker, not a line.
            continue
        else:
            close_run(new_line - 1)
            new_line += 1
    if new_line is not None:
        close_run(new_line - 1)
    return ranges
