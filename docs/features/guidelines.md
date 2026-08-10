# Guidelines Enforcement

Baloo reads convention files from the repository being reviewed and uses them to enforce project-specific rules.

## How It Works

When reviewing a PR, Baloo fetches these files from the target repository (if they exist):

- **`AGENTS.md`** — Repository guidance for coding agents (architecture, conventions, tooling)
- **`CONTRIBUTING.md`** — Contributor guidelines (commit format, branch naming, workflow)

The contents are injected into the review agent's prompt. The agent then flags any PR changes that contradict the documented conventions.

### Guidelines are read from the base branch

Both files are fetched at the PR's **base** commit, not at its head, and the agent is told not to read them out of the checkout. The rules a review is judged by must be the rules already merged into the target branch — otherwise a pull request (from a fork, by anyone who can open one) could rewrite `AGENTS.md` in the same commit as its code and instruct the reviewer to ignore what it changed.

A practical consequence: changes to `AGENTS.md` or `CONTRIBUTING.md` do not take effect for the PR that makes them. They apply to every PR opened after the change merges. The same rule governs the other repo-owned files Baloo treats as policy — plan documents used by [fidelity analysis](fidelity.md) and the [documentation drift](documentation-drift.md) catalog.

## What Gets Flagged

Guidelines violations are reported with category **"Guidelines"** at a severity that reflects the impact of the violation. Examples:

- Branch name missing required ticket ID (e.g., `fix/thing` when the repo requires `fix/PROJ-123/thing`)
- Commit messages missing required ticket references
- Dependency versions not pinned exactly (e.g., `^1.2.3` when exact pinning is required)
- Code that violates architectural decisions stated in AGENTS.md
- Using a tool or pattern explicitly discouraged by the guidelines
- Missing integration tests when AGENTS.md mandates TDD

## Setting Up Your Repository

### AGENTS.md

This file tells Baloo (and other coding agents) how your project works. Rules should be concrete and actionable — Baloo can only enforce what's written down.

```markdown
# AGENTS.md

## Architecture
- Backend: Python 3.11, FastAPI
- All database access goes through the repository pattern in `app/repos/`

## Conventions
- Every PR must be tied to a ticket; branch name must include the ticket ID
- Branch format: `feat/PROJ-123/short-description` or `fix/PROJ-456/short-description`
- Commit format: `feat(scope): [PROJ-123] subject`
- Pin all dependency versions exactly — no ^ or ~ ranges

## Testing
- Every PR must include integration tests
- Work TDD: write failing tests before implementation

## Common Commands
- `uv run pytest` — run tests
- `uv run ruff check` — lint
```

### CONTRIBUTING.md

Standard contributor guidelines. Baloo reads this alongside AGENTS.md:

```markdown
# Contributing

## Branching Strategy

All branch names must include the ticket ID. Examples:
- feat/PROJ-123/add-auth
- fix/PROJ-456/fix-pagination

## Commit Format
<type>(<scope>): [<ticket-id>] <subject>

Examples:
  feat(auth): [PROJ-123] implement password hashing
  fix(api): [PROJ-456] correct pagination query parameter

## Dependency Management
- All versions must be pinned exactly — no ^, ~, or >= ranges
- Verify new packages before adding them

## Pull Requests
- Link the related ticket in the PR description
- Include test coverage
- Run lint before opening
```

## Per-PR review guidance

Standing guidelines catch convention violations. For high-signal reviews on a *specific* change, add a `## Review guidance for Baloo` section to the PR description with falsifiable, diff-anchored checks.

When that heading is present, Baloo extracts the section from the PR body and elevates it into a dedicated checklist for the review (primary checks to verify and cite). Findings that answer a check should cite the brief.

If `CONTRIBUTING.md` requires that section on every PR, Baloo's guidelines rules also cover omissions — but the reliability win is putting a real brief in the PR body so it becomes the primary checklist.

Full workflow and template: [How to Get the Most Out of Baloo](../how-to-get-the-most.md).

## No Guidelines? No Problem

If neither file exists in the repository, Baloo skips the guidelines compliance check entirely. It won't invent rules that aren't documented.

## Configuration

Guidelines enforcement is always on when the files exist. There is no separate toggle — if you don't want it, simply don't include `AGENTS.md` or `CONTRIBUTING.md` in your repository.
