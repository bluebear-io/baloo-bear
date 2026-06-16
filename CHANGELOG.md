# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Review rule: flag the unbounded in-memory data-access anti-pattern (filtering/sorting/pagination/aggregation done in app code, and the bulk-fetch that feeds it — `size: 10000`, `scan`/`scroll`/`search_after`, fetch-then-process) as **CRITICAL**. Reviewers recommend pushing the work into the query/datastore.

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
