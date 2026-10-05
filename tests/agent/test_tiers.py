"""Guards that keep the tier table consistent with pricing and with pi."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from baloo.agent.costs import ANTHROPIC_PRICING
from baloo.agent.databricks import DATABRICKS_PROVIDER
from baloo.agent.tiers import PROVIDER_TIER_MODELS

_PI = Path(__file__).resolve().parents[2] / "node_modules" / ".bin" / "pi"


def test_every_provider_defines_all_three_tiers():
    for provider, tiers in PROVIDER_TIER_MODELS.items():
        assert set(tiers) == {"economy", "standard", "premium"}, provider


def test_anthropic_tier_models_are_priced():
    # An unpriced model silently falls back to the provider-reported cost, so a
    # tier bump that forgets costs.py would not fail anywhere else.
    for tier, model in PROVIDER_TIER_MODELS["anthropic"].items():
        assert model in ANTHROPIC_PRICING, f"{tier} model {model} has no pricing entry"


@pytest.mark.skipif(not _PI.exists(), reason="pi is not installed (run npm install)")
def test_tier_models_exist_in_the_pinned_pi_catalog(tmp_path):
    """pi rejects an unknown model before making a request, so a stale or
    mistyped tier ID fails every review for that provider."""
    env = {
        **os.environ,
        # Placeholder credentials only make pi list each provider's models.
        "PI_OFFLINE": "1",
        "PI_CODING_AGENT_DIR": str(tmp_path),
        "ANTHROPIC_API_KEY": "placeholder",
        "GEMINI_API_KEY": "placeholder",
        "OPENAI_API_KEY": "placeholder",
        "AWS_ACCESS_KEY_ID": "placeholder",
        "AWS_SECRET_ACCESS_KEY": "placeholder",
        "AWS_REGION": "us-east-1",
    }
    out = subprocess.run(
        [str(_PI), "--list-models"], env=env, capture_output=True, text=True, timeout=120
    ).stdout
    catalog = {tuple(line.split()[:2]) for line in out.splitlines()[1:] if line.strip()}
    assert catalog, "pi --list-models returned nothing"

    missing = [
        f"{provider}/{model}"
        for provider, tiers in PROVIDER_TIER_MODELS.items()
        if provider != DATABRICKS_PROVIDER  # registered by Baloo, not in pi's catalog
        for model in tiers.values()
        if (provider, model) not in catalog
    ]
    assert not missing, f"tier models unknown to the pinned pi: {missing}"
