"""Known PR scenarios for Baloo regression testing.

Each scenario tests a real diff from the baloo-bear repo itself.  Assertions
are intentionally loose (LLM is non-deterministic) — they check observable
behavior, not exact output.

Add new scenarios here whenever a bug is fixed or a new feature ships.
"""

from __future__ import annotations

# Env vars applied to every scenario run (mirrors prod minus expensive passes).
# Override any of these via ambient env before running.
BASE_ENV: dict[str, str] = {
    "FP_VERIFICATION_ENABLED": "false",  # skip for speed
    "DOCUMENTATION_DRIFT_ENABLED": "false",  # skip for speed
    "FIDELITY_ENABLED": "false",  # skip for speed
    "THREAD_AGENT_ENABLED": "false",
    "REPO_CACHE_ENABLED": "false",
    "REVIEW_AUTO_APPROVE": "true",
    "REVIEW_MIN_SEVERITY": "MEDIUM",
    "REVIEW_USE_CHECKS_API": "false",
    "PI_THINKING_LEVEL": "low",
}

# Default models — override per scenario or via BALOO_REGRESSION_MODEL env var.
# Use haiku for cheap/fast scenarios, sonnet for those requiring deeper reasoning.
_FAST_MODEL = "claude-haiku-4-5-20251001"
_MAIN_MODEL = "claude-sonnet-5"

SCENARIOS: list[dict] = [
    {
        "name": "docs-fix-no-blocking",
        "description": (
            "A one-line docs typo fix should not produce blocking findings. "
            "Regression for false-positive noise on trivial changes."
        ),
        # Diff: 300332e (readme support link) → e577e11 (fix docs typo)
        "base": "300332e",
        "head": "e577e11",
        "model": _FAST_MODEL,
        "assertions": [
            {"type": "no_blocking_findings"},
        ],
    },
    {
        "name": "missing-tests-surfaces-finding",
        "description": (
            "PR adding a Pydantic model_validator with no tests should surface ≥1 finding. "
            "Validates general_findings path: the test file is not in the diff so the finding "
            "must appear as a general observation rather than being silently dropped."
        ),
        # Diff: cfe5191 (semantic PR docs merge) → 9bad2fa (validator fix, no tests added)
        "base": "cfe5191",
        "head": "9bad2fa",
        "model": _MAIN_MODEL,
        "assertions": [
            {"type": "has_findings"},
        ],
    },
    {
        "name": "dep-bump-no-blocking",
        "description": (
            "A pure dependency version bump should not produce blocking findings. "
            "Regression for false positives on automated Dependabot-style PRs."
        ),
        # Diff: single pytest dev-dep bump commit
        "base": "b9a3198^",
        "head": "b9a3198",
        "model": _FAST_MODEL,
        "assertions": [
            {"type": "no_blocking_findings"},
        ],
    },
]
