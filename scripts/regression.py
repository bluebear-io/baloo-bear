"""Baloo regression harness.

Runs known PR scenarios through the full review pipeline and checks assertions.

Usage:
    uv run python -m scripts.regression

Requirements:
    ANTHROPIC_API_KEY or OPENROUTER_API_KEY must be set.

Override the model for all scenarios:
    BALOO_REGRESSION_MODEL=claude-haiku-4-5-20251001 uv run python -m scripts.regression

Run a single scenario by name:
    uv run python -m scripts.regression --scenario missing-tests-surfaces-finding
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _run_scenario(scenario: dict, base_env: dict[str, str]) -> dict:
    """Run one scenario via dry_run_pr and return the parsed ReviewResult JSON."""
    # BASE_ENV provides defaults; ambient env wins (see regression_scenarios.py).
    env = dict(base_env)
    env.update(os.environ)

    model_override = os.environ.get("BALOO_REGRESSION_MODEL") or scenario.get("model")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name

    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "scripts.dry_run_pr",
        "--repo",
        str(REPO_ROOT),
        "--base",
        scenario["base"],
        "--head",
        scenario["head"],
        "--output-json",
        out_path,
    ]
    if model_override:
        cmd += ["--model", model_override]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )

        if proc.returncode != 0:
            return {"_error": proc.stderr[-2000:]}

        try:
            return json.loads(Path(out_path).read_text())
        except Exception as exc:
            return {"_error": f"Could not parse output JSON: {exc}\nstderr: {proc.stderr[-500:]}"}
    finally:
        Path(out_path).unlink(missing_ok=True)


def _check_assertions(result: dict, assertions: list[dict]) -> list[str]:
    """Return a list of failure messages (empty = all passed)."""
    inline = result.get("comments", [])
    general = result.get("general_findings", [])
    all_findings = inline + general

    failures = []
    for a in assertions:
        t = a["type"]

        if t == "no_blocking_findings":
            if result.get("request_changes"):
                findings_desc = ", ".join(
                    f"[{f.get('severity', '?')}] {f.get('path', 'general')}" for f in all_findings
                )
                failures.append(
                    f"request_changes=True but expected no blocking findings. "
                    f"Findings: {findings_desc or '(none listed)'}"
                )

        elif t == "has_findings":
            if not all_findings:
                failures.append("Expected ≥1 finding but got 0")

        elif t == "request_changes":
            if not result.get("request_changes"):
                failures.append("Expected request_changes=True")

        elif t == "approve":
            if not result.get("approve"):
                failures.append("Expected approve=True")

        elif t == "summary_contains":
            text = a["text"]
            if text.lower() not in result.get("summary", "").lower():
                failures.append(f"Expected summary to contain '{text}'")

        elif t == "finding_count_gte":
            count = len(all_findings)
            if count < a["value"]:
                failures.append(f"Expected ≥{a['value']} findings, got {count}")

        elif t == "general_findings_gte":
            count = len(general)
            if count < a["value"]:
                failures.append(f"Expected ≥{a['value']} general findings, got {count}")

        elif t == "has_severity":
            want = a["severity"].upper()
            if not any(f.get("severity") == want for f in all_findings):
                failures.append(f"Expected ≥1 {want} finding")

        else:
            failures.append(f"Unknown assertion type: '{t}'")

    return failures


def _print_result_stats(result: dict) -> None:
    inline = result.get("comments", [])
    general = result.get("general_findings", [])
    print(
        f"  inline={len(inline)} general={len(general)} "
        f"request_changes={result.get('request_changes')} approve={result.get('approve')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenario", help="Run only this scenario (by name)")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print full review summary on failure"
    )
    args = parser.parse_args(argv)

    # Import here so the file is easy to edit without restarting
    from scripts.regression_scenarios import BASE_ENV, SCENARIOS  # noqa: PLC0415

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [s for s in scenarios if s["name"] == args.scenario]
        if not scenarios:
            print(
                f"Unknown scenario '{args.scenario}'. Available: {[s['name'] for s in SCENARIOS]}"
            )
            return 1

    passed = failed = 0
    for scenario in scenarios:
        name = scenario["name"]
        model = os.environ.get("BALOO_REGRESSION_MODEL") or scenario.get("model", "?")
        print(f"\n{'─'*60}")
        print(f"  {name}")
        print(f"  {scenario.get('description', '')}")
        print(f"  diff: {scenario['base']}..{scenario['head']}  model: {model}")
        print("  running...", flush=True)

        result = _run_scenario(scenario, BASE_ENV)

        if "_error" in result:
            print(f"  FAIL (runner error):\n    {result['_error'][:400]}")
            failed += 1
            continue

        _print_result_stats(result)
        failures = _check_assertions(result, scenario.get("assertions", []))

        if failures:
            print("  FAIL:")
            for f in failures:
                print(f"    ✗ {f}")
            if args.verbose:
                print("\n  --- summary ---")
                print(result.get("summary", "(none)"))
            failed += 1
        else:
            print("  PASS ✓")
            passed += 1

    print(f"\n{'='*60}")
    print(f"  {passed} passed  {failed} failed  ({passed + failed} total)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
