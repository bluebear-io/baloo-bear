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
