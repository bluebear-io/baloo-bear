# Fidelity Analysis

Fidelity analysis compares a PR's actual changes against a two-layer specification — the linked ticket (fetched from Linear) and a design plan document — scoring how closely the implementation matches what was specified.

## Why Fidelity?

When teams write design docs or implementation plans before coding, fidelity analysis closes the loop:

- Did the PR implement what was planned?
- Are there planned items missing from the PR?
- Did the PR add scope beyond the plan?

This is especially useful for teams that use ticket-linked plan files as part of their workflow.

## How It Works

1. **Extract ticket ID** — Baloo looks for a ticket ID in the PR branch name, title, or description (e.g., `PROJ-123` from branch `feat/PROJ-123-add-auth`)
2. **Fetch ticket** — When `LINEAR_API_KEY` is set, the linked ticket is fetched from Linear and becomes the ticket layer of the spec
3. **Fetch plan file** — Looks for a plan document at a configurable path (default: `docs/plans/{ticket_id}.md`) at the PR's base commit, so the spec a PR is scored against cannot be rewritten by the PR itself; this is the plan layer
4. **Analyze** — An LLM compares the two-layer spec (ticket and/or plan) against the PR diff, using the same model as the primary review agent (`AGENT_MODEL`)
5. **Score** — Produces a fidelity score (0–100) and a breakdown of matched/missing/extra items
6. **Report** — Posts the fidelity report as a separate PR comment

## Example Output

```
📋 Fidelity Report — PROJ-123

Score: 85/100

✅ Implemented:
- Add JWT token validation middleware
- Create /api/auth/refresh endpoint
- Add rate limiting to auth endpoints

❌ Missing:
- Add integration tests for token refresh flow

➕ Extra (not in plan):
- Added logout endpoint (not planned but reasonable)
```

## Linear Integration

When `LINEAR_API_KEY` is configured, Baloo fetches the linked issue from Linear and uses its
description as the ticket layer of the spec. Fidelity can then run on a ticket alone — no plan
file required. If the ticket exists but has insufficient detail (under ~300 characters / 5
lines), fidelity is skipped with an "insufficient detail" report so the author knows to flesh
out the ticket. When both a ticket and a plan file are present, both layers are analyzed; a
detailed plan takes priority over a stub ticket. If the Linear API rejects the configured
credentials, the report says so explicitly instead of pretending no plan exists.

## Plan File Format

Plan files are freeform markdown. Baloo works best when the plan lists concrete deliverables:

```markdown
# PROJ-123 — Add Authentication

## Planned Changes
- Add JWT middleware in `app/middleware/auth.py`
- Create `/api/auth/login` and `/api/auth/refresh` endpoints
- Add rate limiting (10 req/min) to all auth endpoints
- Write integration tests in `tests/api/test_auth.py`

## Out of Scope
- OAuth2 provider integration (separate ticket)
```

## Workflow Integration

Fidelity works best when your team commits to writing plan files before coding. A common pattern:

1. Create a ticket in your issue tracker (e.g., `PROJ-123`)
2. Write `docs/plans/PROJ-123.md` with the planned deliverables and merge it to the base branch before opening the implementation PR — plan files are read from the base branch
3. Open the PR from a branch that includes the ticket ID (e.g., `feat/PROJ-123/add-auth`)
4. Baloo automatically finds the plan, scores the PR, and posts the report

This closes the loop between what was planned and what was actually shipped.

## Impact on Approval

Fidelity score affects the approval decision:

- **Score ≥ threshold** (default 90) + no CRITICAL/HIGH findings → **auto-approve**
- **Score < threshold** → approval requires clean review findings only (fidelity doesn't block, but doesn't help)

This means a high-fidelity PR with only MEDIUM issues can still be auto-approved.

Both paths are subject to the same gate as any other approval: Baloo approves only when `REVIEW_AUTO_APPROVE` is on and the repository is listed in `REVIEW_AUTO_APPROVE_REPOS` (see [Configuration](../configuration.md#review-behavior)). The score is model output about a PR whose contents the author controls, so it cannot approve a repository that has not opted in.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `FIDELITY_ENABLED` | `true` | Enable fidelity analysis |
| `FIDELITY_PLAN_PATH_PATTERN` | `docs/plans/{ticket_id}.md` | Path pattern for plan files |
| `FIDELITY_APPROVAL_THRESHOLD` | `90` | Minimum score for auto-approval boost |
| `TICKET_ID_PREFIX` | `PROJ` | Prefix for ticket extraction (e.g., `PROJ` matches `PROJ-123`) |
| `LINEAR_API_KEY` | *(empty)* | Linear API key; enables ticket fetching when set |
| `LINEAR_API_URL` | `https://api.linear.app/graphql` | Linear GraphQL endpoint |
| `AGENT_MODEL` | *(see [Models](models.md))* | Model used for fidelity analysis — shared with the primary review agent |

Fidelity analysis runs on the effective `AGENT_MODEL`, including any runtime override set from the
dashboard (see [Runtime Overrides](../configuration.md#runtime-overrides-db)). Choosing a premium
model for reviews therefore also applies to fidelity. The thinking level is always `medium` for
fidelity regardless of `PI_THINKING_LEVEL`, so tuning that for reviews cannot degrade fidelity
results.

## No Spec? No Report

If Baloo can't find a ticket ID, fidelity analysis is silently skipped. With a ticket ID but no
plan file, fidelity can still run on the Linear ticket alone (when Linear integration is
configured and the ticket has enough detail). It never blocks a review.
