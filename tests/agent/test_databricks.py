"""Tests for the Databricks AI Gateway provider config."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from baloo.agent import databricks
from baloo.agent.databricks import (
    DATABRICKS_PROVIDER,
    DatabricksConfigError,
    build_models_config,
    ensure_agent_dir,
    normalize_host,
)

WORKSPACE = "https://dbc-67b32222-ca30.cloud.databricks.com"


def _provider(host: str = WORKSPACE) -> dict:
    return build_models_config(host)["providers"][DATABRICKS_PROVIDER]


@pytest.mark.parametrize(
    "raw",
    [
        WORKSPACE,
        f"{WORKSPACE}/",
        "dbc-67b32222-ca30.cloud.databricks.com",
        # Databricks' own snippets include the gateway path; accept it rather
        # than producing a doubled /ai-gateway/anthropic/ai-gateway/anthropic.
        f"{WORKSPACE}/ai-gateway/anthropic",
        f"  {WORKSPACE}/ai-gateway/anthropic/  ",
    ],
)
def test_normalize_host_accepts_the_forms_operators_paste(raw):
    assert normalize_host(raw) == WORKSPACE


def test_normalize_host_rejects_empty():
    with pytest.raises(DatabricksConfigError):
        normalize_host("   ")


def test_base_url_targets_the_anthropic_gateway_route_exactly_once():
    assert _provider(f"{WORKSPACE}/ai-gateway/anthropic")["baseUrl"] == (
        f"{WORKSPACE}/ai-gateway/anthropic"
    )


def test_auth_uses_bearer_header():
    # The gateway rejects Anthropic's native x-api-key with 401; PI only sends
    # Authorization: Bearer when authHeader is set.
    assert _provider()["authHeader"] is True


def test_eager_tool_input_streaming_is_disabled():
    # Without this the gateway's Anthropic translator makes tool-enabled
    # requests hang rather than fail, surfacing as an agent_error with no detail.
    assert _provider()["compat"]["supportsEagerToolInputStreaming"] is False


def test_api_key_is_an_env_var_name_not_a_secret():
    # models.json is written to disk, so it must reference the token by name.
    assert _provider()["apiKey"] == databricks.DATABRICKS_TOKEN_ENV
    assert not _provider()["apiKey"].startswith("dapi")


def test_models_use_unity_catalog_ids():
    # Flat databricks-claude-* names now return 501 NOT_IMPLEMENTED.
    ids = [m["id"] for m in _provider()["models"]]
    assert ids, "expected at least one model"
    assert all(i.startswith("system.ai.") for i in ids)
    assert not any(i.startswith("databricks-claude-") for i in ids)


def test_every_tier_is_registered_as_a_model():
    ids = {m["id"] for m in _provider()["models"]}
    assert set(databricks.DATABRICKS_TIER_MODELS.values()) <= ids


def test_models_carry_no_cost_block():
    # Deliberate: Databricks bills DBUs at a per-contract rate, so PI reports
    # $0 rather than a confidently wrong dollar figure. See docs/features/databricks.md.
    assert all("cost" not in m for m in _provider()["models"])


def test_ensure_agent_dir_writes_loadable_json(tmp_path):
    agent_dir = ensure_agent_dir(WORKSPACE, base_dir=tmp_path)
    payload = json.loads((agent_dir / "models.json").read_text())
    assert DATABRICKS_PROVIDER in payload["providers"]


def test_ensure_agent_dir_is_idempotent(tmp_path):
    first = ensure_agent_dir(WORKSPACE, base_dir=tmp_path)
    stamp = (first / "models.json").stat().st_mtime_ns
    second = ensure_agent_dir(WORKSPACE, base_dir=tmp_path)
    assert second == first
    # Unchanged config must not be rewritten; concurrent reviews share the file.
    assert (second / "models.json").stat().st_mtime_ns == stamp


def test_ensure_agent_dir_rewrites_when_host_changes(tmp_path):
    ensure_agent_dir(WORKSPACE, base_dir=tmp_path)
    agent_dir = ensure_agent_dir("https://other.cloud.databricks.com", base_dir=tmp_path)
    payload = json.loads((agent_dir / "models.json").read_text())
    # Exact match, not startswith: a prefix check would also pass for a host
    # like other.cloud.databricks.com.evil.tld, and CodeQL flags the pattern.
    assert payload["providers"][DATABRICKS_PROVIDER]["baseUrl"] == (
        "https://other.cloud.databricks.com/ai-gateway/anthropic"
    )


def test_agent_dir_defaults_to_home_not_a_temp_dir(tmp_path, monkeypatch):
    # The sandbox mounts a fresh tmpfs over /tmp, which would hide the config,
    # so the production path must derive from HOME. Asserting on an injected
    # base_dir would be meaningless: pytest's tmp_path is itself under /tmp on
    # Linux, which is exactly how the previous version of this test passed on
    # macOS and failed in CI.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    assert ensure_agent_dir(WORKSPACE) == fake_home / ".baloo" / "pi-databricks"


def test_ensure_agent_dir_never_publishes_a_partial_file(tmp_path, monkeypatch):
    # write_text truncates in place, so a concurrent reader could see a partial
    # file. Publication must go through an atomic rename instead.
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(databricks.os, "replace", spy)
    agent_dir = ensure_agent_dir(WORKSPACE, base_dir=tmp_path)

    assert seen == [str(agent_dir / "models.json")]
    # No temp files left behind.
    assert [p.name for p in agent_dir.iterdir()] == ["models.json"]


def test_concurrent_writers_do_not_share_a_temp_filename(tmp_path):
    # A fixed temp name (e.g. models.tmp) would let two first-run writers
    # interleave into the same file and rename the mess into place.
    names: list[str] = []
    real_mkstemp = databricks.tempfile.mkstemp

    def spy(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        names.append(name)
        return fd, name

    with patch.object(databricks.tempfile, "mkstemp", spy):
        ensure_agent_dir(WORKSPACE, base_dir=tmp_path)
        ensure_agent_dir("https://other.databricks.com", base_dir=tmp_path)

    assert len(names) == 2
    assert names[0] != names[1]
