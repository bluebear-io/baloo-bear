"""Cross-tenant isolation tests for DashboardService."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from baloo.db.engine import reset_engine
from baloo.db.models import Base, Review, ReviewLog


@pytest.fixture
async def dashboard_db():
    reset_engine()
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with patch("baloo.dashboard.queries.get_session_factory", return_value=factory):
        yield factory
    await engine.dispose()
    reset_engine()


@pytest.mark.asyncio
async def test_get_overview_stats_only_counts_own_tenant(dashboard_db):
    """overview stats must not count reviews from other tenants."""
    async with dashboard_db() as session:
        async with session.begin():
            session.add(
                Review(
                    repo_full_name="owner/repo",
                    pr_number=1,
                    review_status="approved",
                    trigger_reason="test",
                    started_at=datetime.now(timezone.utc),
                    installation_id="tenant_a",
                )
            )
            session.add(
                Review(
                    repo_full_name="owner/repo",
                    pr_number=2,
                    review_status="approved",
                    trigger_reason="test",
                    started_at=datetime.now(timezone.utc),
                    installation_id="tenant_b",
                )
            )

    with patch("baloo.dashboard.queries.get_settings") as mock_settings:
        mock_settings.return_value.database_url = "sqlite+aiosqlite://"
        mock_settings.return_value.installation_id = "tenant_a"

        from baloo.dashboard.queries import DashboardService

        stats = await DashboardService.get_overview_stats()

    assert stats["total_reviews"] == 1


@pytest.mark.asyncio
async def test_list_reviews_only_returns_own_tenant(dashboard_db):
    """list_reviews must not return reviews from other tenants."""
    async with dashboard_db() as session:
        async with session.begin():
            for i, inst in enumerate(["tenant_a", "tenant_b", "tenant_a"]):
                session.add(
                    Review(
                        repo_full_name="owner/repo",
                        pr_number=i + 1,
                        review_status="approved",
                        trigger_reason="test",
                        started_at=datetime.now(timezone.utc),
                        installation_id=inst,
                    )
                )

    with patch("baloo.dashboard.queries.get_settings") as mock_settings:
        mock_settings.return_value.database_url = "sqlite+aiosqlite://"
        mock_settings.return_value.installation_id = "tenant_a"

        from baloo.dashboard.queries import DashboardService

        result = await DashboardService.list_reviews()

    assert result["total"] == 2
    assert all(r.installation_id == "tenant_a" for r in result["reviews"])


@pytest.mark.asyncio
async def test_pr_total_cost_includes_active_and_cancelled_logged_costs(dashboard_db):
    """PR total cost includes every action for the same repo/PR."""
    now = datetime.now(timezone.utc)
    async with dashboard_db() as session:
        async with session.begin():
            completed = Review(
                repo_full_name="owner/repo",
                pr_number=7,
                review_status="approved",
                trigger_reason="test",
                started_at=now,
                cost_usd=0.10,
                installation_id="tenant_a",
            )
            active = Review(
                repo_full_name="owner/repo",
                pr_number=7,
                review_status="in_progress",
                trigger_reason="test",
                started_at=now,
                installation_id="tenant_a",
            )
            cancelled = Review(
                repo_full_name="owner/repo",
                pr_number=7,
                review_status="cancelled",
                trigger_reason="test",
                started_at=now,
                installation_id="tenant_a",
            )
            other_pr = Review(
                repo_full_name="owner/repo",
                pr_number=8,
                review_status="approved",
                trigger_reason="test",
                started_at=now,
                cost_usd=0.99,
                installation_id="tenant_a",
            )
            session.add_all([completed, active, cancelled, other_pr])
            await session.flush()
            session.add_all(
                [
                    ReviewLog(
                        review_id=active.id,
                        event_type="agent_completed",
                        message="agent done",
                        metadata_json=json.dumps({"cost": 0.02}),
                        installation_id="tenant_a",
                    ),
                    ReviewLog(
                        review_id=cancelled.id,
                        event_type="agent_completed",
                        message="agent done",
                        metadata_json=json.dumps({"cost": 0.03}),
                        installation_id="tenant_a",
                    ),
                ]
            )

    with patch("baloo.dashboard.queries.get_settings") as mock_settings:
        mock_settings.return_value.database_url = "sqlite+aiosqlite://"
        mock_settings.return_value.installation_id = "tenant_a"

        from baloo.dashboard.queries import DashboardService

        result = await DashboardService.list_reviews()
        detail = await DashboardService.get_review_detail(completed.id)

    pr_reviews = [r for r in result["reviews"] if r.pr_number == 7]
    assert pr_reviews
    assert {r.review_status for r in pr_reviews} == {"approved", "in_progress", "cancelled"}
    assert all(r.pr_total_cost_usd == pytest.approx(0.15) for r in pr_reviews)
    assert detail.pr_total_cost_usd == pytest.approx(0.15)


def test_log_cost_zero_and_invalid_values():
    """cost=0 must not fall through to cost_usd; bad values must not raise."""
    from baloo.dashboard.queries import _log_cost

    assert _log_cost(json.dumps({"cost": 0, "cost_usd": 0.5})) == 0.0
    assert _log_cost(json.dumps({"cost_usd": 0.25})) == 0.25
    assert _log_cost(json.dumps({"cost": "not-a-number"})) == 0.0
    assert _log_cost(json.dumps({"cost": None, "cost_usd": 0.1})) == 0.1
    assert _log_cost(None) == 0.0
    assert _log_cost("not json") == 0.0
