# Severity Routing

Baloo routes findings to different GitHub surfaces based on severity, so developers see critical issues prominently while lesser suggestions stay out of the way.

Baloo never submits a "Request Changes" review and never blocks a merge. Its review is either an approval or a plain comment; merge decisions stay with humans.

## Routing Rules

| Severity | Where It Goes | Prevents Baloo's approval? |
|---|---|---|
| **CRITICAL** | Inline review comment, summary marked "❌ Changes Requested" | ✅ Yes |
| **HIGH** | Inline review comment, summary marked "❌ Changes Requested" | ✅ Yes |
| **MEDIUM** | GitHub Checks API annotation + collapsible PR digest | ❌ No |
| **LOW** | Filtered out (not posted) | ❌ No |

General findings (no file/line anchor, e.g. missing tests) are not posted inline or to the Checks API — they appear under a "💬 General Observations" section in the review summary and are stored in the dashboard. CRITICAL/HIGH general findings still withhold approval.

## How It Looks

### CRITICAL / HIGH → Review Comments

Posted as inline comments on the exact file and line. The review is submitted as a comment (not "Request Changes") whose summary says "❌ Changes Requested", and Baloo does not approve the PR. Nothing is blocked: if branch protection requires an approval, a human still provides it.

### MEDIUM → Checks API

Posted as annotations on a GitHub Check called "Baloo Code Quality". These appear in the Checks tab and as non-blocking annotations on the PR diff, but don't block merge. The full text is also available in the Check summary and in an expandable section of Baloo's completion comment.

The check is posted on every review, even with no MEDIUM findings, so the Checks tab always offers GitHub's **Re-run** button as a way to request a fresh review.

If the Checks API fails (e.g., missing permissions), MEDIUM findings are not posted as separate comments — they remain available in Baloo's expandable PR finding digest.

### LOW → Filtered

Findings below the minimum severity threshold are not posted. This reduces noise for developers.

## Severity Guidelines

The agent assigns severity based on these guidelines:

- **CRITICAL** — Reserved for confirmed exploitable vulnerabilities or certain catastrophic data loss
- **HIGH** — Security concerns, serious bugs, silent failure patterns, or clear guidelines violations
- **MEDIUM** — Quality, maintainability, or performance issues
- **LOW** — Style or minor polish

## Configuration

| Variable | Default | Description |
|---|---|---|
| `REVIEW_MIN_SEVERITY` | `MEDIUM` | Minimum severity to post. Set to `LOW` to see everything, `HIGH` to reduce noise |
| `REVIEW_USE_CHECKS_API` | `true` | Post a `Baloo Code Quality` check run on every review (MEDIUM findings become its annotations; the completion comment contains their full text either way). The check's **Re-run** button triggers a fresh review |
| `REVIEW_AUTO_APPROVE` | `false` | Auto-approve PRs with no CRITICAL/HIGH findings (opt-in) |

## Approval Decision Logic

```
Agent returned an error  →  Comment only (⚠️ warning posted; PR is NOT approved)
CRITICAL or HIGH found (inline or general)  →  Comment only, summary marked "Changes Requested" (no approval)
No blocking issues + high fidelity score  →  Approve
No blocking issues + auto-approve enabled  →  Approve
Otherwise  →  Comment only (no approval or rejection)
```

A failed agent run is never treated as a clean slate. A failing agent returns
zero findings, which would otherwise satisfy the auto-approve branch above, so
`agent_error` short-circuits the decision: Baloo approves nothing and edits its
progress comment to say the PR was not reviewed.
