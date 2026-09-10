# Severity Routing

Baloo routes findings to different GitHub surfaces based on severity, so developers see critical issues prominently while non-blocking suggestions stay out of the way.

## Routing Rules

| Severity | Where It Goes | Blocks PR? |
|---|---|---|
| **CRITICAL** | Inline review comment + "Request Changes" | ✅ Yes |
| **HIGH** | Inline review comment + "Request Changes" | ✅ Yes |
| **MEDIUM** | GitHub Checks API annotation | ❌ No |
| **LOW** | Filtered out (not posted) | ❌ No |

General findings (no file/line anchor, e.g. missing tests) are not posted inline or to the Checks API — they appear under a "💬 General Observations" section in the review summary. CRITICAL/HIGH general findings still count toward the "Request Changes" decision.

## How It Looks

### CRITICAL / HIGH → Review Comments

Posted as inline comments on the exact file and line. The PR review is submitted with "Request Changes" status, which blocks merge (if branch protection requires it).

### MEDIUM → Checks API

Posted as annotations on a GitHub Check called "Baloo Code Quality". These appear in the Checks tab and as non-blocking annotations on the PR diff, but don't block merge.

If the Checks API fails (e.g., missing permissions), MEDIUM findings fall back to regular issue comments.

### LOW → Filtered

Findings below the minimum severity threshold are not posted. This reduces noise for developers.

## Severity Guidelines

The agent assigns severity based on these guidelines:

- **CRITICAL** — Security vulnerabilities, data loss, silent failures, guidelines violations
- **HIGH** — Bugs or logic errors that can break functionality
- **MEDIUM** — Quality, maintainability, or performance issues
- **LOW** — Style or minor polish

## Configuration

| Variable | Default | Description |
|---|---|---|
| `REVIEW_MIN_SEVERITY` | `MEDIUM` | Minimum severity to post. Set to `LOW` to see everything, `HIGH` to reduce noise |
| `REVIEW_USE_CHECKS_API` | `true` | Post MEDIUM findings to Checks API. When `false`, MEDIUM findings go to review comments |
| `REVIEW_AUTO_APPROVE` | `false` | Auto-approve PRs with no CRITICAL/HIGH findings (opt-in) |

## Approval Decision Logic

```
Agent returned an error  →  Comment only (⚠️ warning posted; PR is NOT approved)
CRITICAL or HIGH found (inline or general)  →  Request Changes
No blocking issues + high fidelity score  →  Approve
No blocking issues + auto-approve enabled  →  Approve
Otherwise  →  Comment only (no approval or rejection)
```

A failed agent run is never treated as a clean slate. A failing agent returns
zero findings, which would otherwise satisfy the auto-approve branch above, so
`agent_error` short-circuits the decision: Baloo approves nothing and edits its
progress comment to say the PR was not reviewed.
