# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- The agent sandbox now fails closed. When `REPO_SANDBOX_MODE` is not `off` and bubblewrap cannot run, Baloo refuses to start and refuses to review, instead of logging a warning and running the agent unisolated with Baloo's full environment
- The agent subprocess environment is scrubbed to an allowlist on every spawn, sandboxed or not, so it never inherits the GitHub App key, database URL, or dashboard credentials
- A review agent with no provisioned worktree is sandboxed against an empty directory rather than running in Baloo's own working directory
- Repository policy files (`AGENTS.md`, `CONTRIBUTING.md`, plan documents, and the documentation drift catalog) are read from the PR's base branch instead of its head, so a pull request can no longer supply the rules it will be judged by
- Guidelines violations are no longer automatically elevated to HIGH; severity follows the impact of the violation
- Attacker-controlled PR content (title, author, branch names, description, file paths, diff, quoted discussion) is fenced as untrusted data in the review prompt, with a per-prompt nonce and spotlighting rules that make it data rather than instructions
- The `## Review guidance for Baloo` brief can only add checks to a review; it can no longer narrow scope, lower severity, or suppress findings, and the "be practical if the author says it's a fix" leniency clause is gone
- Dependency-bot and security-patch handling is decided from GitHub's account type and PR labels instead of the words in the title and description
- Posted review bodies are sanitized: secret-shaped strings are redacted and markdown links, images, and loading HTML tags are made inert
- `REVIEW_AUTO_APPROVE` now defaults to `false`, and approval additionally requires the repository to be listed in the new `REVIEW_AUTO_APPROVE_REPOS`

### Changed

- Reworked public documentation for open source use
- Replaced internal deployment references with generic or public-safe defaults
- Added standard community and security policy files

## [0.1.0] - 2026-03-31

### Added

- Initial Baloo release as an AI-powered GitHub pull request review agent
- FastAPI webhook handling for GitHub App pull request events
- Anthropic-powered review generation and structured finding processing
- Optional PostgreSQL-backed dashboard and fidelity analysis support
