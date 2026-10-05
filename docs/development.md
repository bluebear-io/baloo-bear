# Development

This guide is for contributors working on Baloo itself.

Use this path when you want to:

- edit code locally
- run tests and linters
- debug Baloo without Docker
- work on prompts, webhook behavior, or internals

## 1. Prerequisites

You need:

- Python `3.10+`
- `uv`
- `npm`

Optional but recommended:

- `gitleaks`

## 2. Install Dependencies

```bash
git clone https://github.com/bluebear-io/baloo-bear.git
cd baloo-bear
uv sync
npm install                       # PI agent runtime + git hooks
npm --prefix extensions install   # AST tools used by the review agent
cp .env.example .env
```

`PI_BINARY_PATH` defaults to `pi`, so put the locally installed CLI on your `PATH` (the Docker image does the same), or set `PI_BINARY_PATH` to its full path:

```bash
export PATH="$PWD/node_modules/.bin:$PATH"
```

## 3. Run Baloo Directly

```bash
uv run python main.py
```

This is the direct developer workflow. If you want the service-style stack with PostgreSQL, use [docs/getting-started.md](getting-started.md) instead.

## 4. Test and Lint

```bash
uv run pytest
uv run pytest --cov=baloo --cov-report=term-missing
uv run ruff check baloo tests
uv run black --check baloo tests
```

When you add or update Python dependencies, regenerate the hash-pinned production requirements before committing:

```bash
uv export --frozen --no-dev --no-emit-project --no-header --output-file requirements-prod.txt
```

CI checks `requirements-prod.txt` against a fresh export, and the Docker image installs production dependencies from it.

## 5. Git Hooks

The repository uses Husky for a pre-commit hook that lints, checks formatting, and scans for secrets.

If `gitleaks` is not installed yet:

```bash
brew install gitleaks
```

Hook setup:

```bash
npm install
```

The installed pre-commit hook runs:

```bash
uv run ruff check baloo tests
uv run black --check baloo tests
gitleaks git --staged --pre-commit --no-banner --redact   # skipped if gitleaks is not installed
```

## Docs Site

The public docs at <https://oss.bluebear.io/baloo-bear/> are built with MkDocs from `docs/` and deployed by `.github/workflows/docs.yml` on every push to `main`. The home and contributing pages are generated from the repo-root `README.md` and `CONTRIBUTING.md`, so edit those files, not `docs/index.md` or `docs/contributing.md` (both are gitignored).

To preview locally:

```bash
scripts/sync-docs-site.sh
uvx --with mkdocs-material==9.7.6 mkdocs serve
```

A new page under `docs/` must also be added to the `nav` in `mkdocs.yml`, to `docs/README.md`, and to `docs/llms.txt`.
