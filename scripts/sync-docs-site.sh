#!/usr/bin/env bash
# Generate the MkDocs home and contributing pages from the repo-root README.md and
# CONTRIBUTING.md, rewriting links for the docs/ root. Both outputs are gitignored;
# CI runs this before `mkdocs build`, and so should a local `mkdocs serve`.
set -euo pipefail
cd "$(dirname "$0")/.."

GH=https://github.com/bluebear-io/baloo-bear/blob/main

sed -e 's|docs/README.md|docs/getting-started.md|g' \
    -e 's|docs/||g' \
    -e 's|(README.md)|(./)|g' \
    -e 's|(CONTRIBUTING.md)|(contributing.md)|g' \
    -e "s|(AGENTS.md)|($GH/AGENTS.md)|g" \
    -e "s|(SECURITY.md)|($GH/SECURITY.md)|g" \
    -e "s|(LICENSE)|($GH/LICENSE)|g" \
    -e "s|href=\"LICENSE\"|href=\"$GH/LICENSE\"|g" \
    -e "s|(DOCKER.md)|($GH/DOCKER.md)|g" \
    -e "s|(\.env.example)|($GH/.env.example)|g" \
    -e "s|(scripts/local_review.py)|($GH/scripts/local_review.py)|g" \
    README.md > docs/index.md

sed -e "s|(SECURITY.md)|($GH/SECURITY.md)|g" CONTRIBUTING.md > docs/contributing.md
