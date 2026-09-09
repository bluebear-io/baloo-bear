"""Tests for listing Baloo as a reviewer (the GitHub re-request button)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import baloo.github.api_client as api_client_module
from baloo.github.api_client import GitHubAPIClient

BOT = "baloo-code-reviewer[bot]"


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    api_client_module._self_review_request_supported = True
    yield
    api_client_module._self_review_request_supported = True


def _make_client() -> tuple[GitHubAPIClient, AsyncMock]:
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    auth = MagicMock()
    auth.get_installation_token.return_value = "tok"
    return GitHubAPIClient(installation_id=1, http_client=mock_http, auth=auth), mock_http


def _mock_response(body: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_requests_self_as_reviewer():
    client, mock_http = _make_client()
    mock_http.post.return_value = _mock_response({"requested_reviewers": [{"login": BOT}]})

    with patch("baloo.github.api_client.get_app_bot_login", new=AsyncMock(return_value=BOT)):
        assert await client.request_self_as_reviewer("org/repo", 7) is True

    url, kwargs = mock_http.post.call_args[0][0], mock_http.post.call_args[1]
    assert url.endswith("/repos/org/repo/pulls/7/requested_reviewers")
    assert kwargs["json"] == {"reviewers": [BOT]}


@pytest.mark.asyncio
async def test_silently_ignored_request_disables_further_attempts():
    """GitHub answers 200 while dropping reviewers it won't accept."""
    client, mock_http = _make_client()
    mock_http.post.return_value = _mock_response({"requested_reviewers": []})

    with patch("baloo.github.api_client.get_app_bot_login", new=AsyncMock(return_value=BOT)):
        assert await client.request_self_as_reviewer("org/repo", 7) is False
        assert await client.request_self_as_reviewer("org/repo", 8) is False

    assert mock_http.post.call_count == 1


@pytest.mark.asyncio
async def test_client_error_disables_further_attempts():
    client, mock_http = _make_client()
    error_response = httpx.Response(422, text="Unprocessable")
    mock_http.post.side_effect = httpx.HTTPStatusError(
        "422", request=httpx.Request("POST", "https://api.github.com"), response=error_response
    )

    with patch("baloo.github.api_client.get_app_bot_login", new=AsyncMock(return_value=BOT)):
        assert await client.request_self_as_reviewer("org/repo", 7) is False
        assert await client.request_self_as_reviewer("org/repo", 8) is False

    assert mock_http.post.call_count == 1


@pytest.mark.asyncio
async def test_server_error_keeps_trying():
    client, mock_http = _make_client()
    error_response = httpx.Response(500, text="oops")
    mock_http.post.side_effect = httpx.HTTPStatusError(
        "500", request=httpx.Request("POST", "https://api.github.com"), response=error_response
    )

    with patch("baloo.github.api_client.get_app_bot_login", new=AsyncMock(return_value=BOT)):
        assert await client.request_self_as_reviewer("org/repo", 7) is False
        assert await client.request_self_as_reviewer("org/repo", 8) is False

    assert mock_http.post.call_count == 2
