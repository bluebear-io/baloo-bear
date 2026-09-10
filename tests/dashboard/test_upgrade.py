from __future__ import annotations

import httpx
import pytest

from baloo.dashboard import upgrade


@pytest.fixture(autouse=True)
def _clear_cache():
    upgrade._cache = upgrade._EMPTY_CACHE
    yield
    upgrade._cache = upgrade._EMPTY_CACHE


def _mock_release(monkeypatch, payload: dict) -> None:
    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())


@pytest.mark.asyncio
async def test_newer_release_is_reported(monkeypatch):
    monkeypatch.setattr(upgrade, "VERSION", "2.2.1")
    _mock_release(monkeypatch, {"tag_name": "v2.3.0", "html_url": "https://example.invalid/v2.3.0"})

    assert await upgrade.check_for_upgrade() == {
        "version": "v2.3.0",
        "url": "https://example.invalid/v2.3.0",
    }


@pytest.mark.asyncio
async def test_same_or_older_release_is_ignored(monkeypatch):
    monkeypatch.setattr(upgrade, "VERSION", "2.3.0")
    _mock_release(monkeypatch, {"tag_name": "v2.3.0", "html_url": "https://example.invalid/v2.3.0"})

    assert await upgrade.check_for_upgrade() is None


@pytest.mark.asyncio
async def test_network_failure_is_swallowed(monkeypatch):
    def _boom(**kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)

    assert await upgrade.check_for_upgrade() is None


def test_current_version_falls_back_to_pyproject(monkeypatch):
    monkeypatch.setattr(upgrade, "VERSION", "dev")
    assert upgrade._parse(upgrade.current_version()) is not None


@pytest.mark.asyncio
async def test_check_runs_on_a_freshly_booted_host(monkeypatch):
    """monotonic() is uptime on Linux; a low value must not read as a warm cache."""
    monkeypatch.setattr(upgrade.time, "monotonic", lambda: 200.0)
    monkeypatch.setattr(upgrade, "VERSION", "2.2.1")
    _mock_release(monkeypatch, {"tag_name": "v2.3.0", "html_url": "https://example.invalid/v2.3.0"})

    assert await upgrade.check_for_upgrade() is not None
