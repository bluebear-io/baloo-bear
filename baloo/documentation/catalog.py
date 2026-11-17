"""Load documentation drift catalogs from a provisioned repository."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from baloo.documentation.models import DocumentationCatalog

if TYPE_CHECKING:
    from baloo.github.api_client import GitHubAPIClient

logger = logging.getLogger(__name__)


def parse_documentation_catalog(raw: str, source: str) -> DocumentationCatalog | None:
    """Validate raw catalog JSON, logging and returning None when it is malformed."""
    try:
        return DocumentationCatalog.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Invalid documentation catalog at %s: %s", source, exc)
        return None


async def fetch_documentation_catalog(
    github_client: GitHubAPIClient,
    repo_full_name: str,
    catalog_path: str,
    ref: str | None,
) -> DocumentationCatalog | None:
    """Fetch the catalog from the repository at ``ref``.

    The catalog decides which docs the drift agent must review, so it is read
    from the base branch rather than the PR checkout — otherwise a PR could
    delete its own documentation obligations in the same commit that creates
    them.
    """
    if Path(catalog_path).is_absolute():
        logger.warning("Ignoring absolute documentation catalog path: %s", catalog_path)
        return None

    raw = await github_client.get_file_content(repo_full_name, catalog_path, ref=ref)
    if raw is None:
        return None

    return parse_documentation_catalog(raw, f"{repo_full_name}@{ref}:{catalog_path}")


def load_documentation_catalog(
    repo_path: str | None,
    catalog_path: str,
) -> DocumentationCatalog | None:
    """Load and validate a catalog from a local checkout (local-review tooling).

    Webhook reviews use `fetch_documentation_catalog` instead: the provisioned
    worktree is at the PR head, which the PR author controls.
    """
    if repo_path is None:
        return None

    repo_root = Path(repo_path).resolve()
    catalog_requested = Path(catalog_path)
    if catalog_requested.is_absolute():
        logger.warning("Ignoring absolute documentation catalog path: %s", catalog_path)
        return None

    catalog_resolved = (repo_root / catalog_requested).resolve()
    if not catalog_resolved.is_relative_to(repo_root):
        logger.warning("Ignoring documentation catalog outside repo: %s", catalog_path)
        return None

    if not catalog_resolved.exists():
        return None

    try:
        raw = catalog_resolved.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Unreadable documentation catalog at %s: %s", catalog_path, exc)
        return None

    return parse_documentation_catalog(raw, catalog_path)
