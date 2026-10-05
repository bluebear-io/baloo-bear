"""Tests for GitHub review Markdown formatting."""

from baloo.github.models import (
    FindingCategory,
    GeneralFinding,
    ReviewComment,
    ReviewSeverity,
)
from baloo.processor.formatter import CommentFormatter


def test_collapsible_medium_findings_keeps_complete_bodies_and_locations() -> None:
    long_body = "This is the full explanation. " * 80
    inline = ReviewComment(
        path="src/service.py",
        line=42,
        body=long_body,
        severity=ReviewSeverity.MEDIUM,
        category=FindingCategory.QUALITY,
    )
    general = GeneralFinding(
        body="Add an end-to-end performance test.",
        severity=ReviewSeverity.MEDIUM,
        category=FindingCategory.PERFORMANCE,
    )

    rendered = CommentFormatter.format_collapsible_medium_findings([inline, general])

    assert rendered.startswith("<details>")
    assert "2 medium suggestions — expand to read" in rendered
    assert "`src/service.py:42`" in rendered
    assert "_General observation_" in rendered
    assert long_body in rendered
    assert rendered.endswith("</details>")


def test_collapsible_medium_findings_ignores_other_severities() -> None:
    high = GeneralFinding(
        body="Blocking issue",
        severity=ReviewSeverity.HIGH,
        category=FindingCategory.BUGS,
    )

    assert CommentFormatter.format_collapsible_medium_findings([high]) == ""


def test_findings_digest_is_expanded_markdown() -> None:
    finding = ReviewComment(
        path="app.py",
        line=5,
        body="Full suggestion",
        severity=ReviewSeverity.MEDIUM,
        category=FindingCategory.QUALITY,
    )

    rendered = CommentFormatter.format_findings_digest([finding])

    assert "<details>" not in rendered
    assert "### 1. Quality — `app.py:5`" in rendered
    assert "Full suggestion" in rendered


def test_reviewed_commit_links_to_the_commit_page() -> None:
    rendered = CommentFormatter.format_reviewed_commit("abc1234def5678", "octo/repo")

    assert rendered == (
        "🔍 Reviewed commit [`abc1234`](https://github.com/octo/repo/commit/abc1234def5678)"
    )


def test_reviewed_commit_falls_back_to_plain_sha_without_repo() -> None:
    assert (
        CommentFormatter.format_reviewed_commit("abc1234def5678") == "🔍 Reviewed commit `abc1234`"
    )


def test_reviewed_commit_is_empty_without_sha() -> None:
    assert CommentFormatter.format_reviewed_commit("", "octo/repo") == ""
    assert CommentFormatter.format_reviewed_commit(None, "octo/repo") == ""


def test_summary_shows_reviewed_commit_under_the_header() -> None:
    rendered = CommentFormatter.format_summary(
        [], commit_sha="abc1234def5678", repo_full_name="octo/repo"
    )

    lines = [line for line in rendered.splitlines() if line.strip()]
    assert lines[0] == "## 🐻 Baloo Review Summary"
    assert lines[1] == (
        "🔍 Reviewed commit [`abc1234`](https://github.com/octo/repo/commit/abc1234def5678)"
    )


def test_summary_omits_reviewed_commit_when_sha_is_missing() -> None:
    assert "Reviewed commit" not in CommentFormatter.format_summary([])
