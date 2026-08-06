# How to Get the Most Out of Baloo

Baloo reviews every PR out of the box. The teams that get **sharp, high-signal reviews** do more than install the app — they give Baloo the same context a careful human reviewer would demand: repo conventions, the ticket contract, and falsifiable checks for *this* diff.

This guide covers the levers that turn Baloo from a generic AI reviewer into a context-aware teammate.

## The three layers of context

| Layer | Where it lives | What it does |
| ----- | -------------- | ------------ |
| **Repo conventions** | `AGENTS.md`, `CONTRIBUTING.md` | Standing rules Baloo enforces on every PR |
| **Per-PR review brief** | `## Review guidance for Baloo` in the PR description | Diff-specific, falsifiable checks for this change |
| **Ticket / plan contract** | Branch ticket ID + `docs/plans/{TICKET}.md` (and your issue tracker when configured) | What the change is *supposed* to do — used by [fidelity analysis](features/fidelity.md) |

Generic prompts produce generic reviews. Concrete, testable instructions produce findings that cite the exact check they answered.

## Start with written conventions

Baloo reads `AGENTS.md` and `CONTRIBUTING.md` from the PR head and flags violations. Vague guidance ("write clean code") is ignored; enforceable rules are not.

Write rules that are:

- **Specific** — branch format, commit format, exact dependency pinning, required tests
- **Architectural** — where code must live, which patterns are banned, which modules own which concerns
- **Checkable from the diff** — something Baloo can confirm by reading files, not by guessing intent

See [Guidelines Enforcement](features/guidelines.md) for setup examples.

**Tip:** If your repo requires a review-brief section in every PR description, say so explicitly in `CONTRIBUTING.md`. Baloo will flag PRs that omit it.

## The highest-leverage practice: per-PR review guidance

Put a `## Review guidance for Baloo` section in the PR description. When that section exists, Baloo **extracts it from the PR body** and elevates it into a dedicated review checklist for that run — not buried in the rest of the description. Treat it as an **anti-bias review brief**: a list of checks to verify independently — not claims to trust.

### Why this works

Authors naturally frame PRs as successful ("no behavior change in staging", "feature-flagged"). A good brief restates the **ticket contract** and turns author claims into things Baloo must *falsify or confirm* against the code.

### What a strong brief looks like

```markdown
## Review guidance for Baloo

### Context (do not trust the author's framing)
- Ticket / contract: PROJ-123 — enable feature X in production only, gated on `isProduction()`; no behavior change in non-prod
- Base: main; Diff: 1 file, +53/−3; Subsystem: service factory helpers
- Author's claims to VERIFY (not facts): (a) non-prod envs unchanged (b) both factories get the config (c) the gate cannot be bypassed by a deploy flag
- Standards for these paths: load and validate against the architecture rules for `infra/**`

### What to check (falsifiable, anchored to this diff)
1. Prod gate integrity — every one of `buildFeatureEnv`, `buildFeatureLayers`, `buildFeaturePermissions`, and the feature-flag spreads returns empty/false when `isProduction()` is false
2. Both factories receive the layer + config when the gate is on
3. Extra IAM permissions only present behind the same production gate
4. No silent scope creep beyond the ticket

### Blocking classes for this PR
- Weakened / bypassed production gate
- Auth / RBAC loosened
- Removed or relaxed test assertions
```

### What this looks like in practice

Suppose a PR claims a production-only integration. The brief tells Baloo to verify that every helper returns empty/false outside production. The implementation instead has a temporary force-on (`return true` with a "REVERT BEFORE MERGE" comment).

Baloo cites the brief check explicitly — for example:

> **[CRITICAL] Bugs** — Prod-only gate disabled  
> Per the Review guidance for Baloo (check: "Prod gate integrity — … returns empty/false when `isProduction()` is false"): `featureEnabled()` unconditionally returns `true`. The author's own comment says REVERT BEFORE MERGE. Non-prod environments receive production-only config, env vars, and IAM — violating the ticket contract.

That finding did not come from a generic "look for bugs" prompt. It came from a **context-aware check** written for that exact diff.

### How to write checks Baloo can use

| Do | Don't |
| -- | ----- |
| Name functions, files, and expected return values | "Make sure this feature is safe" |
| Phrase checks as falsifiable questions | "Verify that X works" (invites a rubber-stamp yes) |
| Restate the ticket goal neutrally | Copy the PR summary's "this correctly does X" |
| List author claims as *claims to verify* | Treat green CI or author curl output as proof |
| Name blocking classes for *this* change | Dump a 40-item generic checklist |

### Automate the brief (recommended)

Have your coding agent generate the brief **before** opening the PR, from:

1. The linked ticket (contract)
2. The actual diff
3. Path → standards rules (skills, architecture docs, `AGENTS.md` sections)

Hand the brief to Baloo via the PR description — not back into the same author chat that wrote the code. The point is a **separate reviewer** with an unbiased checklist.

A practical workflow:

1. Finish the change
2. Generate a review brief from `base...HEAD` (ticket + diff + applicable standards)
3. Put the output under `## Review guidance for Baloo` in the body passed to `gh pr create`
4. When the PR materially grows or shifts, replace the section (do not append a second one)

Require the section in `CONTRIBUTING.md` so humans (and Baloo) refuse to treat a PR as review-ready without it.

## Give Baloo a ticket contract

Baloo extracts ticket IDs from branch names, titles, and descriptions. Pair that with:

- **Clear tickets** — acceptance criteria Baloo (and fidelity) can score against
- **Plan files** — e.g. `docs/plans/PROJ-123.md` with concrete deliverables and explicit out-of-scope items

[Fidelity analysis](features/fidelity.md) then reports what was implemented, missing, or added outside the plan. Ticket + plan + review brief is the full loop: *what was asked* → *what to verify on this diff* → *what shipped*.

## Catch issues before the PR

Run the same review pipeline locally with no GitHub comments:

```bash
uv run python scripts/local_review.py --git-workdir /path/to/your-repo --base origin/main --head HEAD
```

Use this to iterate on the change (and on the review brief) before Baloo posts publicly. See the [project README](https://github.com/Blue-Bear-Security/baloo-bear/blob/main/README.md#local-review-dry-run) for flags.

## Engage the discussion threads

Baloo tracks inline threads across pushes: it skips duplicates, follows up when feedback is addressed, and can continue the conversation when you reply. Treat findings like a human review — reply with a fix, a decline with reasoning, or a tradeoff. That history improves later reviews on the same PR. See [Discussion Tracking](features/discussions.md).

## Tune signal, not volume

| Setting | Why it helps |
| ------- | ------------ |
| `FP_VERIFICATION_ENABLED=true` | Second LLM pass drops weak findings |
| `REVIEW_MIN_SEVERITY` | Keep noise out of the PR surface |
| Severity routing | CRITICAL/HIGH block; MEDIUM can go to Checks |

Details: [Severity Routing](features/severity-routing.md), [FP Verification](features/fp-verification.md).

## Quick checklist for a high-signal repo

- [ ] `AGENTS.md` / `CONTRIBUTING.md` with concrete, enforceable rules
- [ ] `CONTRIBUTING.md` requires `## Review guidance for Baloo` on every PR
- [ ] Authors (or an agent skill) generate the brief from the ticket + diff before `gh pr create`
- [ ] Checks are falsifiable and anchored to named files/functions
- [ ] Author claims are listed as *claims to verify*, not facts
- [ ] Ticket IDs in branch names; plan docs for non-trivial work when fidelity is on
- [ ] Local dry-run available for authors who want a preview
- [ ] FP verification left on unless you have a reason to turn it off

## Related docs

- [Guidelines Enforcement](features/guidelines.md) — standing repo rules
- [Review Agent](features/review-agent.md) — how the agentic review runs
- [Fidelity Analysis](features/fidelity.md) — plan / ticket vs implementation
- [Discussion Tracking](features/discussions.md) — thread follow-ups
- [Getting Started](getting-started.md) — install and first review
