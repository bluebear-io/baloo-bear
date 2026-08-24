"""Settings page: control derivation and batch save."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from baloo.dashboard.auth import verify_credentials
from baloo.dashboard.router import _settings_rows, router


def _row(name: str) -> dict:
    rows = {row["name"]: row for row in _settings_rows()}
    assert name in rows, f"{name} missing from settings rows"
    return rows[name]


def test_bool_setting_gets_a_toggle() -> None:
    row = _row("fp_verification_enabled")
    assert row["control"] == "toggle"
    assert row["bool_value"] in (True, False)


def test_int_setting_gets_a_number_with_bounds() -> None:
    row = _row("fidelity_approval_threshold")
    assert row["control"] == "number"
    assert row["minimum"] == 0
    assert row["maximum"] == 100


def test_provider_setting_gets_a_select() -> None:
    row = _row("agent_provider")
    assert row["control"] == "select"
    assert ("anthropic", "Anthropic (direct API)") in row["choices"]


def test_severity_setting_gets_a_select() -> None:
    row = _row("review_min_severity")
    assert row["control"] == "select"
    assert [value for value, _ in row["choices"]] == ["critical", "high", "medium", "low"]


def test_secret_setting_is_masked_and_immutable() -> None:
    row = _row("github_webhook_secret")
    assert row["control"] == "masked"
    assert row["mutable"] is False


def test_model_setting_falls_back_to_text() -> None:
    row = _row("agent_model")
    assert row["control"] == "text"


def test_every_row_declares_a_control() -> None:
    for row in _settings_rows():
        assert row["control"] in {"toggle", "number", "select", "masked", "text"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_credentials] = lambda: "tester"
    return TestClient(app, follow_redirects=False)


def test_batch_save_writes_every_key() -> None:
    with patch("baloo.dashboard.router.set_override", new=AsyncMock()) as set_override:
        response = _client().post(
            "/dashboard/settings",
            data={
                "action": "save",
                "key": ["fp_verification_enabled", "thread_agent_max_replies"],
                "value": ["false", "7"],
            },
        )

    assert response.status_code == 303
    written = {call.args[0] for call in set_override.await_args_list}
    assert written == {"fp_verification_enabled", "thread_agent_max_replies"}


def test_batch_save_is_all_or_nothing() -> None:
    """One invalid field must not leave the others half-written."""
    with patch("baloo.dashboard.router.set_override", new=AsyncMock()) as set_override:
        response = _client().post(
            "/dashboard/settings",
            data={
                "action": "save",
                "key": ["fp_verification_enabled", "thread_agent_max_replies"],
                "value": ["false", "not-a-number"],
            },
        )

    assert response.status_code == 303
    set_override.assert_not_awaited()


def test_single_key_save_still_works() -> None:
    with patch("baloo.dashboard.router.set_override", new=AsyncMock()) as set_override:
        response = _client().post(
            "/dashboard/settings",
            data={"action": "save", "key": "agent_model", "value": "sonnet"},
        )

    assert response.status_code == 303
    set_override.assert_awaited_once()


def test_immutable_key_is_rejected() -> None:
    with patch("baloo.dashboard.router.set_override", new=AsyncMock()) as set_override:
        response = _client().post(
            "/dashboard/settings",
            data={"action": "save", "key": "database_url", "value": "sqlite://evil"},
        )

    assert response.status_code == 303
    set_override.assert_not_awaited()
