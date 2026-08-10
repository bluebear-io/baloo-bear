# Review Agent

Baloo uses [PI](https://github.com/mariozechner/pi-coding-agent) as its agentic runtime. When a PR is opened or updated, Baloo spawns a PI agent process that actively explores the repository to produce a thorough review.

## How It Works

1. **Webhook arrives** — GitHub sends a `pull_request` event
2. **Context assembly** — Baloo fetches the PR diff, file list, metadata, and any prior discussion threads
3. **Agent spawns** — A PI process starts in RPC mode with **read-only tools**: `read`, `grep`, `find`, `ls`
4. **Agentic review** — The agent reads changed files in full, greps for security patterns, explores project structure, checks for tests and configs
5. **Structured output** — The agent returns a JSON object with findings (file, line, severity, category, description, recommendation), plus a `general_findings` list for observations with no file/line anchor (e.g. missing tests, architectural gaps). General findings appear as a "General Observations" section in the PR summary rather than as inline comments
6. **Post-processing** — Findings go through FP verification (optional), severity filtering, duplicate detection, and severity routing before being posted

## Why Agentic?

Unlike simple "diff-in, comments-out" reviewers, Baloo's agent can:

- **Read full files** — not just the diff, but the entire file for context
- **Search the codebase** — grep for patterns, find related files, check if tests exist
- **Follow references** — if a function is changed, the agent can check where it's called
- **Read project conventions** — examines `AGENTS.md` and `CONTRIBUTING.md` for repo-specific rules

## Read-Only Guarantee

The agent has **no write access**. It cannot execute commands, modify files, or make API calls. All mutations (posting comments, updating GitHub) happen in the deterministic Python code after the agent returns its findings.

## Untrusted PR Content

Everything a pull request carries — title, description, author, branch names, file paths, diff, and quoted discussion — is written by whoever opened it. Baloo fences each of those fields in the prompt with markers carrying a nonce generated per review:

```
[UNTRUSTED-DATA pr_description BEGIN 3f9c1a2b4d5e]
...the PR description...
[UNTRUSTED-DATA pr_description END 3f9c1a2b4d5e]
```

The system prompt gives those fences the highest precedence: text inside them is data, never instructions. It cannot change the agent's task, scope, severity thresholds, or output format, and claims inside it ("this is only a refactor", "this fixes a build break") are treated as assertions to verify against the diff rather than reasons to stay quiet. An attempt to direct the review is reported as a HIGH Security finding instead of being followed. Marker-shaped text in the content is scrubbed before fencing, so a payload cannot close its own fence even if it guesses the format.

The same rule governs the `## Review guidance for Baloo` brief: it can add checks to a review, never remove them.

## Posted Output Is Sanitized

Every body Baloo posts — review summaries, inline comments, PR-level reports, thread replies, and Checks API output — passes through a sanitizer first:

- **Secret-shaped strings are redacted**, including inside code fences. The patterns are anchored on issuer prefixes and shapes (GitHub, Anthropic, OpenAI, Google, AWS, Slack, GitLab tokens, JWTs, PEM private keys, credentials embedded in connection strings) rather than on entropy, so ordinary quoted code and commit SHAs pass through untouched.
- **Markdown links and images are made inert.** `![](https://host/?data=…)` renders as an image request that every viewer's browser performs automatically, which is enough to exfiltrate anything the agent puts in the URL. Images become plain text, links keep their text with the target defanged (`https[://]…`), and HTML tags that can load or navigate are escaped.

Fenced blocks and inline code spans are exempt from the markdown pass, since nothing renders there and rewriting would corrupt the code a review is quoting.

## Bot and Security Classification

Dependency-update PRs get a relaxed review, and a Dependabot security update additionally gets prompt guidance not to report the upgrade as introducing the vulnerability it fixes. Both routes are decided from data the PR author cannot set:

- **Dependency bot** — the author's login is one of `dependabot[bot]`, `dependabot-preview[bot]`, `renovate[bot]` **and** GitHub reports the account type as `Bot`.
- **Security fix** — the PR carries a label naming security, a vulnerability, or a CVE. Applying a label requires triage or write access on the target repository.

Neither is inferred from the title or description. Configure Dependabot to apply a `security` label (`labels:` in `.github/dependabot.yml`) if you want security-update handling.

## Tools Available

| Tool | Purpose |
|---|---|
| `read` | Read file contents (full or by line range) |
| `grep` | Search for patterns across files |
| `find` | Locate files by name or pattern |
| `ls` | List directory contents |

## What the Agent Reviews

The system prompt instructs the agent to check, in priority order:

1. **Security** — SQL injection, XSS, secrets exposure, command injection, auth/authz issues
2. **Bugs** — Logic errors, null refs, race conditions, error handling gaps
3. **Silent failures** — Swallowed exceptions, missing error logging, silent default substitution
4. **Guidelines** — Violations of conventions in `AGENTS.md` / `CONTRIBUTING.md`
5. **Performance** — N+1 queries, blocking operations, algorithm efficiency
6. **Quality** — DRY, complexity, naming, test coverage

### Per-PR review guidance (when present)

When the PR description contains a `## Review guidance for Baloo` section, Baloo extracts that brief and adds it as **Step 0b** — a checklist verified alongside the standard review steps above. Findings that answer a brief check cite it explicitly. The brief is author-supplied, so it can only add checks: it cannot narrow scope, lower a severity, or suppress a finding.

Authors: see [How to Get the Most Out of Baloo](../how-to-get-the-most.md) for how to write falsifiable, diff-anchored checks.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `AGENT_MODEL` | `sonnet` | Model to use (see [Models](models.md)) |
| `AGENT_FALLBACK_MODEL` | `google/gemini-2.5-flash` | Fallback if primary fails |
| `AGENT_MAX_TOKENS` | `4096` | Max output tokens |
| `AGENT_TEMPERATURE` | `0.2` | Temperature for generation |
| `PI_THINKING_LEVEL` | `medium` | Thinking depth: off, minimal, low, medium, high |
