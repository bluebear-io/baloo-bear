# Coding Standards

Coding standards enforcement checks every changed line of a PR against the coding standards the
repository maps to its files. It is optional and runs alongside the normal review when enabled.

Violations are posted like any other Baloo finding: inline, at HIGH severity, in the Guidelines
category, so they request changes and go through the same filtering, deduplication, thread
tracking and FP verification.

## How It Works

1. The repository maps file paths to skills in `.bluebear/skill_enforcement.json`.
2. A skill is a markdown coding standard at `.agents/skills/<skill>/SKILL.md`.
3. For each changed file, Baloo collects the skills whose rules match it.
4. For each skill, one model call judges all of that skill's files. Changed lines are marked, and
   only violations on, or caused by, changed lines are reported.
5. A violation is suppressed by a comment next to the code that names the rule and gives a
   concrete reason, for example `# Never swallow exceptions is broken here, because ...`.

If the repository has no rules file, or no rule matches a changed file, the feature does nothing.

## Quick Start

1. Add `.bluebear/skill_enforcement.json` to the repository:

   ```json
   {
     "rules": [
       {"tool_category": "write_file", "path": "**/*.py", "skill": "python-standards"},
       {"tool_category": "write_file", "path": "services/api/**", "skill": "api-standards"}
     ]
   }
   ```

2. Add each skill at `.agents/skills/<skill>/SKILL.md`.

3. Enable the feature:

   ```env
   CODING_STANDARDS_ENABLED=true
   ```

## Rules

- `path` is a glob with `*`, `?` and `**`. A relative path matches at any depth, so
  `tests/**` also matches `src/tests/test_a.py`. Character classes (`[...]`), alternation
  (`{a,b}`) and escapes (`\`) are not supported: a rule that uses them is skipped and listed
  under "Coding standards enforcement incomplete", and the other rules still apply.
- Rules with `tool_category` other than `write_file` (or empty), rules with an `agent`, and rules
  with `"skip_coding_standards": true` are ignored.
- When several rules name the same skill for a file, the first one applies.

## Umbrella Repository

An organization that keeps shared standards in one repository, which groups the others as
subdirectories, sets `CODING_STANDARDS_UMBRELLA_REPO=<owner>/<umbrella>`.

For a PR in another repository named `svc`, Baloo also applies the umbrella's rules, treating
`svc` as checked out at `svc/` inside the umbrella. A rule such as `svc/api/**` then matches
`api/handler.py` in `svc`. Skills named by umbrella rules are read from the umbrella's
`.agents/skills/`. Rules from the PR's own repository come first, so its own skill wins when both
repositories name the same skill for a file.

The umbrella is read at the head of its default branch and must be readable by the same GitHub
App installation.

## When Something Fails

Enforcement problems never block the review. Each one is listed under "Coding standards
enforcement incomplete" in the review summary and in Baloo's progress comment, for example a missing skill file, a file GitHub
returned no patch for, or a failed model call.
