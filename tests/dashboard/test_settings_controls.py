"""Settings page: control derivation and batch save."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
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
    assert [value for value, _ in row["choices"]] == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


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
            # Must differ from the effective value — a save that matches the
            # current value is intentionally skipped, not written.
            data={"action": "save", "key": "agent_model", "value": "opus"},
        )

    assert response.status_code == 303
    set_override.assert_awaited_once()


def test_save_matching_current_value_writes_nothing() -> None:
    """A client that posts every field must not convert the page to db overrides."""
    with patch("baloo.dashboard.router.set_override", new=AsyncMock()) as set_override:
        response = _client().post(
            "/dashboard/settings",
            data={"action": "save", "key": "agent_model", "value": "sonnet"},
        )

    assert response.status_code == 303
    set_override.assert_not_awaited()


def test_immutable_key_is_rejected() -> None:
    with patch("baloo.dashboard.router.set_override", new=AsyncMock()) as set_override:
        response = _client().post(
            "/dashboard/settings",
            data={"action": "save", "key": "database_url", "value": "sqlite://evil"},
        )

    assert response.status_code == 303
    set_override.assert_not_awaited()


def test_every_row_has_a_tier() -> None:
    for row in _settings_rows():
        assert row["tier"] in {"required", "common", "advanced"}


def test_credentials_are_required_tier() -> None:
    """The settings you cannot run Baloo without must not hide under Advanced."""
    by_name = {row["name"]: row for row in _settings_rows()}
    for name in ("github_app_id", "github_private_key", "github_webhook_secret"):
        assert by_name[name]["tier"] == "required"


def test_startup_only_settings_are_flagged() -> None:
    """A setting read once at startup must say so, or the page implies a live change."""
    by_name = {row["name"]: row for row in _settings_rows()}
    assert by_name["max_concurrent_reviews"]["restart_required"] is True
    assert by_name["log_retention_days"]["restart_required"] is True
    assert by_name["agent_model"]["restart_required"] is False


def test_severity_choices_match_what_the_filter_reads() -> None:
    """Lowercase choices fell through FindingsFilter's .get() default, so every
    option silently meant MEDIUM."""
    from baloo.dashboard.router import REVIEW_SEVERITY_CHOICES

    severity_order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    for value, _label in REVIEW_SEVERITY_CHOICES:
        assert value in severity_order, f"{value!r} is not a severity FindingsFilter knows"


def test_severity_choice_matches_the_field_default() -> None:
    """If no choice equals the default, the <select> shows the wrong current value."""
    from baloo.dashboard.router import REVIEW_SEVERITY_CHOICES

    row = _row("review_min_severity")
    assert row["raw_value"] in [value for value, _ in REVIEW_SEVERITY_CHOICES]


def test_out_of_range_and_unknown_values_are_rejected() -> None:
    """Bounds live server-side; HTML min/max is a convenience, not validation."""
    from baloo.config.runtime_settings import RuntimeSettingsError, validate_override

    for key, bad in [
        ("max_concurrent_reviews", "0"),
        ("max_concurrent_reviews", "-1"),
        ("fidelity_approval_threshold", "9999"),
        ("review_min_severity", "banana"),
        ("agent_provider", "not-a-provider"),
        ("ticket_id_prefix", "(a+)+"),
    ]:
        with pytest.raises(RuntimeSettingsError):
            validate_override(key, bad)


def test_no_secret_is_rendered_on_the_settings_page(monkeypatch) -> None:
    """Render the real page and assert the secret string is simply absent."""
    from baloo.config.settings import reset_settings

    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_SUPERSECRET123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SUPERSECRET456")
    reset_settings()

    response = _client().get("/dashboard/settings")

    assert response.status_code == 200
    assert "lin_api_SUPERSECRET123" not in response.text
    assert "sk-ant-SUPERSECRET456" not in response.text


def test_categories_are_contiguous() -> None:
    """Each category renders as one card, not several.

    Settings fields are not declared contiguously by category, so the rows must
    be grouped before the template walks them.
    """
    seen: list[str] = []
    for row in _settings_rows():
        if not seen or seen[-1] != row["category"]:
            seen.append(row["category"])
    assert len(seen) == len(set(seen)), f"category split across cards: {seen}"
