"""Run coding standards enforcement locally, without GitHub, on a local diff.

Judges `git diff base...head` of a local repository against the skills its
`.bluebear/skill_enforcement.json` maps to the changed files, plus an optional local checkout of
an umbrella repository. Makes real model calls through the configured provider and prints the
findings Baloo would post.

Examples:
    uv run python -m scripts.local_standards --git-workdir /path/to/repo --base origin/main

    # A sub-repository of an umbrella checked out at /path/to/umbrella:
    uv run python -m scripts.local_standards --git-workdir /path/to/umbrella/svc \\
        --umbrella /path/to/umbrella

Requires provider credentials, e.g. `export ANTHROPIC_API_KEY=...`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from baloo.github.models import PRContext
from baloo.standards.enforcer import StandardsReport, enforce_on_checkouts
from baloo.standards.rules import RuleSource
from scripts.local_review import build_local_pr_context, run_git


def with_patches(context: PRContext, repo_root: str, base: str, head: str) -> PRContext:
    """GitHub returns each file's patch with the PR; the local diff has to supply it."""
    files = []
    for change in context.files_changed:
        diff = run_git(["diff", f"{base}...{head}", "--", change.filename], repo_root, True)
        hunk_start = diff.find("\n@@")
        patch = diff[hunk_start + 1 :] if hunk_start >= 0 else None
        files.append(change.model_copy(update={"patch": patch}))
    metadata = context.metadata.model_copy(update={"files_changed": files})
    return context.model_copy(update={"metadata": metadata})


def print_report(report: StandardsReport, output_json: bool) -> None:
    if output_json:
        print(
            json.dumps(
                {
                    "findings": [c.model_dump(mode="json") for c in report.comments],
                    "errors": report.errors,
                    "judge_calls": report.judge_calls,
                    "usage": report.metadata,
                },
                indent=2,
            )
        )
        return
    print(f"# Coding standards: {len(report.comments)} finding(s)\n")
    for comment in report.comments:
        print(f"## [{comment.severity.value}] {comment.path}:{comment.line}\n\n{comment.body}\n")
    if report.errors:
        print(report.summary_section(), "\n")
    print(
        f"judge calls: {report.judge_calls}, cost: ${report.metadata['cost_usd']:.4f}, "
        f"tokens: {report.metadata['input_tokens']} in / {report.metadata['output_tokens']} out"
    )


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--git-workdir", help="Repository under review (default: cwd)")
    parser.add_argument("--base", default="origin/main", help="Base ref for the comparison")
    parser.add_argument("--head", default="HEAD", help="Head ref for the comparison")
    parser.add_argument("--repo-full-name", help="Override inferred owner/repo name")
    parser.add_argument("--umbrella", help="Local checkout of the umbrella repository")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    start = Path(args.git_workdir or ".").expanduser().resolve()
    repo_root = run_git(["rev-parse", "--show-toplevel"], str(start), True)
    context = build_local_pr_context(
        base=args.base,
        head=args.head,
        title="Local coding standards check",
        description="",
        author="local",
        repo_full_name=args.repo_full_name,
        git_workdir=repo_root,
    )
    context = with_patches(context, repo_root, args.base, args.head)
    umbrella = None
    if args.umbrella:
        umbrella_root = Path(args.umbrella).expanduser().resolve()
        umbrella = RuleSource(name=umbrella_root.name, root=umbrella_root)

    report = await enforce_on_checkouts(context, Path(repo_root), umbrella)
    print_report(report, args.json)
    return 1 if report.errors else 0


def main() -> None:
    sys.exit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
