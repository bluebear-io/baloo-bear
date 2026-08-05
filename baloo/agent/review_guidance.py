"""Extract per-PR review guidance from a pull request description."""

from __future__ import annotations

import re

REVIEW_GUIDANCE_HEADING = "## Review guidance for Baloo"

# Match the conventional heading (allow optional trailing whitespace).
_HEADING_RE = re.compile(
    r"^##\s+Review guidance for Baloo\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# Next H2 markdown heading ends the section (### under the brief stays inside).
_NEXT_HEADING_RE = re.compile(r"^##\s+\S", re.MULTILINE)


def extract_review_guidance(description: str | None) -> str | None:
    """Return the Review guidance for Baloo section body, or None if absent/empty.

    Parses from the conventional ``## Review guidance for Baloo`` heading until the
    next ``##`` (or deeper) markdown heading. Whitespace-only bodies are treated as
    absent.
    """
    if not description:
        return None

    match = _HEADING_RE.search(description)
    if not match:
        return None

    rest = description[match.end() :]
    next_heading = _NEXT_HEADING_RE.search(rest)
    body = rest[: next_heading.start()] if next_heading else rest
    body = body.strip()
    return body or None
