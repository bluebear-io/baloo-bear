"""Tests for the `@baloo review` issue-comment command."""

from __future__ import annotations

import itertools
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from baloo.github.webhook_handler import _is_review_command, _run_review_command, app

# Unique per post — the handler dedups repeated X-GitHub-Delivery ids process-wide.
_delivery_ids = (f"delivery-{n}" for n in itertools.count())


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _make_issue_comment_payload(
    *,
    action: str = "created",
    body: str = "@baloo review",
    author_association: str = "MEMBER",
    author: str = "maintainer",
    is_pull_request: bool = True,
    comment_id: int = 555,
    pr_number: int = 7,
    repo: str = "org/repo",
    installation_id: int = 1,
) -> dict:
    issue: dict = {
        "number": pr_number,
        "title": "Test PR",
        "html_url": f"https://github.com/{repo}/pull/{pr_number}",
    }
    if is_pull_request:
        issue["pull_request"] = {"url": f"https://api.github.com/repos/{repo}/pulls/{pr_number}"}

    return {
        "action": action,
        "issue": issue,
        "comment": {
            "id": comment_id,
            "body": body,
            "author_association": author_association,
            "user": {"login": author, "id": 1, "avatar_url": "", "html_url": ""},
            "html_url": f"https://github.com/{repo}/pull/{pr_number}#issuecomment-{comment_id}",
        },
        "repository": {
            "id": 1,
            "name": "repo",
            "full_name": repo,
            "owner": {"login": "org", "id": 1, "avatar_url": "", "html_url": ""},
            "html_url": f"https://github.com/{repo}",
            "default_branch": "main",
        },
        "installation": {"id": installation_id},
        "sender": {"login": author, "id": 1, "avatar_url": "", "html_url": ""},
    }


def _post(client, payload: dict, delivery: str | None = None):
    body = json.dumps(payload).encode()
    delivery_id = delivery if delivery is not None else next(_delivery_ids)
    with (
        patch("baloo.github.webhook_handler.verify_webhook_signature", return_value=True),
        patch(
            "baloo.github.webhook_handler._validate_webhook_security",
            new=AsyncMock(return_value=None),
        ),
    ):
        return client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "issue_comment",
                "X-Hub-Signature-256": "sha256=fake",
                "X-GitHub-Delivery": delivery_id,
            },
        )


@pytest.mark.parametrize(
    "body,expected",
    [
        ("@baloo review", True),
        ("@BALOO Review", True),
        ("  @baloo review  ", True),
        ("@baloo review please re-run", True),
        ("@baloo review\nthanks", True),
        ("@baloo reviewer", False),
        ("please @baloo review", False),
        ("looks good to me", False),
        ("", False),
        ("   ", False),
    ],
)
def test_is_review_command(body: str, expected: bool):
    assert _is_review_command(body) is expected


def test_review_command_queued_for_authorized_member(client):
    payload = _make_issue_comment_payload(author_association="MEMBER")

    with (
        patch("baloo.github.webhook_handler._run_review_command", new=AsyncMock()) as mock_run,
        patch("baloo.github.webhook_handler.cancel_existing_review") as mock_cancel,
    ):
        resp = _post(client, payload, delivery="delivery-queued")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["action"] == "review_command"
    mock_cancel.assert_called_once_with("org/repo", 7)
    mock_run.assert_called_once_with("org/repo", 7, 1, 555, "delivery-queued")


def test_review_command_ignored_on_plain_issue(client):
    payload = _make_issue_comment_payload(is_pull_request=False)

    resp = _post(client, payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ignored"
    assert data["reason"] == "not a pull request"


def test_review_command_ignored_for_unauthorized_commenter(client):
    payload = _make_issue_comment_payload(author_association="CONTRIBUTOR")

    with patch("baloo.github.webhook_handler._run_review_command", new=AsyncMock()) as mock_run:
        resp = _post(client, payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ignored"
    assert data["reason"] == "commenter not authorized"
    mock_run.assert_not_called()


def test_non_command_comment_ignored(client):
    payload = _make_issue_comment_payload(body="looks good, merging")

    resp = _post(client, payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ignored"
    assert data["reason"] == "not a review command"


def test_review_command_ignored_on_edit(client):
    payload = _make_issue_comment_payload(action="edited")

    resp = _post(client, payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ignored"
    assert data["reason"] == "action=edited"


@pytest.mark.asyncio
async def test_run_review_command_reacts_then_forces_full_review():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with (
        patch("baloo.github.webhook_handler.GitHubAPIClient", return_value=mock_client),
        patch("baloo.github.webhook_handler.process_pr_review", new=AsyncMock()) as mock_review,
    ):
        await _run_review_command("org/repo", 7, 1, 555, "delivery-123")

    mock_client.add_reaction.assert_awaited_once_with("org/repo", 555, "eyes")
    mock_review.assert_awaited_once()
    _, kwargs = mock_review.call_args
    # head_sha="" forces a fresh review (skips DB-level dedup)
    assert kwargs["head_sha"] == ""
    assert kwargs["trigger_reason"] == "issue_comment:@baloo review"
    assert kwargs["synchronize_base_sha"] is None
