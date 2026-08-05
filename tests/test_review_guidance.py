"""Tests for review guidance extraction."""

from baloo.agent.review_guidance import REVIEW_GUIDANCE_HEADING, extract_review_guidance


def test_extract_review_guidance_present():
    description = f"""## Summary
Does a thing.

{REVIEW_GUIDANCE_HEADING}

### What to check
1. Gate returns false outside prod
2. No scope creep

## Test plan
- [ ] manual
"""
    body = extract_review_guidance(description)
    assert body is not None
    assert "Gate returns false outside prod" in body
    assert "No scope creep" in body
    assert "## Test plan" not in body
    assert "Does a thing" not in body


def test_extract_review_guidance_case_insensitive_heading():
    description = "## REVIEW GUIDANCE FOR BALOO\n\nCheck the gate.\n"
    assert extract_review_guidance(description) == "Check the gate."


def test_extract_review_guidance_until_end_when_no_following_heading():
    description = f"{REVIEW_GUIDANCE_HEADING}\n\nOnly this section.\n"
    assert extract_review_guidance(description) == "Only this section."


def test_extract_review_guidance_empty_body_is_absent():
    assert extract_review_guidance(f"{REVIEW_GUIDANCE_HEADING}\n\n   \n") is None
    assert extract_review_guidance(f"{REVIEW_GUIDANCE_HEADING}\n") is None


def test_extract_review_guidance_missing_heading():
    assert extract_review_guidance("## Summary\n\nNo brief here.\n") is None
    assert extract_review_guidance(None) is None
    assert extract_review_guidance("") is None


def test_extract_review_guidance_stops_at_deeper_heading_only_for_h2_plus():
    """### under the brief stays inside; next ## ends it."""
    description = f"""{REVIEW_GUIDANCE_HEADING}

### Context
Ticket PROJ-1

## Other section
Ignore me
"""
    body = extract_review_guidance(description)
    assert body is not None
    assert "### Context" in body
    assert "Ticket PROJ-1" in body
    assert "Other section" not in body
