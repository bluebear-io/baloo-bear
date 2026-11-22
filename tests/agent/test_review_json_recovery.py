"""Safety regressions for malformed terminal review JSON."""

import json

from baloo.agent.review_json_recovery import reconcile_review_json


def finding(title="Missing authorization check", line=12):
    return {
        "file": "app/auth.py",
        "line": line,
        "severity": "HIGH",
        "category": "Security",
        "title": title,
        "description": "The route bypasses the access-control check on write requests.",
    }


def malformed(*items, tail='"summary": {} invalid'):
    return '{"findings": [' + ", ".join(json.dumps(item) for item in items) + "], " + tail


def test_repair_cannot_discard_original_complete_finding():
    original = finding()
    output, status = reconcile_review_json(
        malformed(original), {"findings": [], "summary": {"total_issues": 0}}
    )
    assert status == "preserved_source_findings"
    assert output["findings"] == [original]
    assert output["summary"]["total_issues"] == 1


def test_repair_keeps_new_findings_without_duplicating_original():
    original, additional = finding(), finding("Another real bug", 25)
    output, status = reconcile_review_json(
        malformed(original), {"findings": [original, additional], "summary": {}}
    )
    assert status == "preserved_source_findings"
    assert output["findings"] == [original, additional]
    assert output["summary"]["total_issues"] == 2


def test_original_complete_finding_recovers_when_retry_fails():
    original = finding()
    output, status = reconcile_review_json(malformed(original), None)
    assert status == "preserved_source_findings"
    assert output["findings"] == [original]


def test_truncated_second_finding_fails_closed():
    original = finding()
    raw = '{"findings": [' + json.dumps(original) + ', {"file": "broken.py", "title": "unfinished'
    output, status = reconcile_review_json(raw, {"findings": [], "summary": {}})
    assert output is None
    assert status == "unrecoverable_source_findings"


def test_malformed_original_finding_cannot_be_claimed_clean():
    output, status = reconcile_review_json(
        '{"findings": [{"file": "a.py", "title": "oops"', {"findings": []}
    )
    assert output is None
    assert status == "unrecoverable_source_findings"


def test_no_original_findings_preserves_valid_retry():
    retry = {"findings": [], "summary": {"total_issues": 0}}
    assert reconcile_review_json("not JSON at all", retry) == (retry, "no_source_findings")


def test_wrong_retry_schema_is_rejected():
    output, status = reconcile_review_json(malformed(finding()), {"findings": "clean"})
    assert output is None
    assert status == "invalid_retry_schema"


def test_oversized_original_is_not_silently_accepted():
    output, status = reconcile_review_json("x" * 262145, {"findings": []})
    assert output is None
    assert status == "unrecoverable_source_findings"


def test_complete_general_finding_is_retained():
    general = {k: v for k, v in finding().items() if k not in ("file", "line")}
    raw = '{"general_findings": [' + json.dumps(general) + '], "summary": unfinished'
    output, status = reconcile_review_json(raw, {"general_findings": []})
    assert status == "preserved_source_findings"
    assert output["general_findings"] == [general]


def test_invalid_original_row_must_not_be_recovered():
    invalid = {**finding(), "line": -1}
    output, status = reconcile_review_json(malformed(invalid), {"findings": []})
    assert output is None
    assert status == "unrecoverable_source_findings"
