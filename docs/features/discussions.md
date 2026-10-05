# Discussion Tracking

Baloo tracks prior review conversations across PR iterations. When a new commit is pushed, Baloo doesn't just re-review from scratch — it understands what was already discussed.

## What It Does

- **Detects existing threads** — Finds Baloo's previous inline comments on the same PR
- **Skips duplicates** — If Baloo already flagged the same issue and is awaiting a response, it won't re-post
- **Posts follow-ups** — If the same issue persists but the developer has replied, Baloo posts a follow-up in the existing thread instead of creating a new one
- **Injects context** — The review agent sees a digest of prior discussions so it doesn't contradict its own earlier recommendations

## How Matching Works

Baloo uses fuzzy matching to link new findings to existing threads:

1. **Exact match** — Same file + same line + near-identical issue content (similarity above 0.8)
2. **Nearby match** — Same file + within ±5 lines + at least loosely similar content (similarity ≥ 0.2)
3. **Moved-anchor match** — Same file + within ±18 lines + clearly the same issue (similarity ≥ 0.55), for when edits above the hunk shift the line

Similarity is Jaccard similarity on terms extracted from the finding. When several threads qualify, the most similar, then closest, wins.

Matching accounts for line drift when code is modified between iterations.

## Thread States

| State | Meaning | Baloo's Behavior |
|---|---|---|
| **Awaiting response** | Baloo posted, developer hasn't replied | Skip (don't re-post) |
| **Active discussion** | Developer replied | May post follow-up in same thread |
| **New finding** | No existing thread | Post as new inline comment |

## Consistency Rules

The review agent is instructed to:

- **Not contradict** previous recommendations unless code changed significantly
- **Not flip-flop** between different valid approaches
- **Check if recommendations were addressed** before re-flagging

## Impact on Decisions

Open threads affect the approval decision:

- If Baloo has open threads awaiting response and no new findings, it still withholds approval and marks the summary "Changes Requested" (Baloo never submits a blocking "Request Changes" review)
- The summary reports how many threads remain open

## Example Flow

```
Push 1: Baloo finds SQL injection on auth.py:55
  → Posts inline comment, withholds approval

Developer replies: "We use an ORM, this is safe"

Push 2: Baloo re-reviews
  → Sees existing thread on auth.py:55
  → Agent reads the discussion context
  → If issue is gone: doesn't re-flag
  → If issue persists: posts follow-up in the same thread
```

## Configuration

Discussion tracking is always on. There are no feature flags — it's core to how Baloo behaves across PR iterations.

One part depends on [FP Verification](fp-verification.md) (on by default): before deciding, Baloo re-checks threads still awaiting a reply against the new diff, and when the issue is gone it posts a resolution reply and resolves the thread. With `FP_VERIFICATION_ENABLED=false`, those threads stay open until someone resolves them.
