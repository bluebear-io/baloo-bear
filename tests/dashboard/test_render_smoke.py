"""Every dashboard route renders.

This is the safety net for the template redesign: the ports touch every
template, and the pre-existing suite only covered a few of them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from baloo.dashboard.auth import verify_credentials
from baloo.dashboard.router import router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_credentials] = lambda: "tester"
    return app


def _review(review_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=review_id,
        repo_full_name="example-org/example-repo",
        pr_number=42,
        pr_title="Fix dashboard rendering",
        pr_author="octocat",
        commit_sha="a" * 40,
        review_status="approved",
        trigger_reason="synchronize",
        started_at=datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 4, 14, 12, 0, 30, tzinfo=timezone.utc),
        duration_seconds=30.0,
        model_used="anthropic/claude-sonnet-5",
        tokens_input=1000,
        tokens_output=200,
        cost_usd=0.12,
        pr_total_cost=0.34,
        agent_turns=5,
        files_examined=9,
        auto_approved=False,
        fidelity_score=88.0,
        error_message=None,
        error_category=None,
        findings=[],
        logs=[],
    )


OVERVIEW = {
    "total_reviews": 12,
    "reviews_today": 3,
    "avg_duration": 14.2,
    "approval_rate": 75.0,
    "severity": {"MEDIUM": 2},
    "recent_reviews": [_review()],
    "errors_total": 1,
    "errors_today": 0,
    "error_rate": 8.3,
    "error_categories": {"timeout": 1},
    "recent_failures": [_review(2)],
    "hourly_activity": [{"hour": "2026-04-14 12", "count": 3}],
}

ANALYTICS = {
    "daily": [{"day": "2026-04-14", "count": 3}],
    "statuses": {"approved": 9, "error": 1},
    "severities": {"MEDIUM": 2},
    "repos": [{"name": "example-org/example-repo", "count": 12}],
    "total_cost": 1.23,
    "prev_total_cost": 1.0,
    "error_categories": {"timeout": 1},
    "daily_errors": [{"day": "2026-04-14", "count": 1}],
    "success_rate": 91.7,
    "prev_success_rate": 90.0,
    "error_total": 1,
    "prev_error_total": 2,
    "total_in_period": 12,
    "prev_total_in_period": 10,
}

OUTCOMES = {
    "total": 40,
    "outcomes": {"actioned": 26, "ignored": 14},
    "hit_rate": 65.0,
    "noise_rate": 35.0,
    "severity_data": {"high": {"total": 10, "actioned": 7, "hit_rate": 70.0}},
    "category_data": {"Correctness": {"total": 12, "actioned": 8, "hit_rate": 66.7}},
    "trends": [{"day": "2026-04-14", "total": 5, "hit_rate": 60.0, "noise_rate": 40.0}],
    "repos": ["example-org/example-repo"],
}

REVIEWS = {
    "reviews": [_review()],
    "page": 1,
    "per_page": 25,
    "total": 1,
    "total_pages": 1,
    "repos": ["example-org/example-repo"],
    "search": "",
}


@pytest.mark.parametrize(
    "path,method,payload",
    [
        ("/dashboard/", "get_overview_stats", OVERVIEW),
        ("/dashboard/analytics", "get_analytics_data", ANALYTICS),
        ("/dashboard/outcomes", "get_outcomes_data", OUTCOMES),
        ("/dashboard/reviews", "list_reviews", REVIEWS),
    ],
)
def test_route_renders(path: str, method: str, payload: dict) -> None:
    app = _build_app()
    with patch(
        f"baloo.dashboard.router.DashboardService.{method}",
        new=AsyncMock(return_value=payload),
    ):
        response = TestClient(app).get(path)

    assert response.status_code == 200, response.text
    assert "<html" in response.text


def test_review_detail_renders() -> None:
    app = _build_app()
    with patch(
        "baloo.dashboard.router.DashboardService.get_review_detail",
        new=AsyncMock(return_value=_review()),
    ):
        response = TestClient(app).get("/dashboard/reviews/1")

    assert response.status_code == 200, response.text
    assert "example-org/example-repo" in response.text


def test_settings_renders() -> None:
    response = TestClient(_build_app()).get("/dashboard/settings")

    assert response.status_code == 200, response.text
    assert "Settings" in response.text


@pytest.mark.xfail(reason="CDN tags removed in Task 2 of the redesign", strict=True)
def test_no_cdn_references() -> None:
    """After the redesign, every asset is served from dashboard-static."""
    templates = Path("baloo/dashboard/templates")
    offenders = [
        str(path)
        for path in templates.rglob("*.html")
        for needle in ("cdn.tailwindcss.com", "unpkg.com", "jsdelivr.net")
        if needle in path.read_text()
    ]
    assert offenders == [], f"CDN references remain in: {offenders}"
