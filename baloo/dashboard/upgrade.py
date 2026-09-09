"""Check GitHub for a newer official Baloo release."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import httpx

from baloo.version import VERSION

logger = logging.getLogger(__name__)

# /releases/latest returns the newest published, non-prerelease, non-draft
# release, so main-branch builds and pre-releases never trigger the banner.
LATEST_RELEASE_URL = "https://api.github.com/repos/bluebear-io/baloo-bear/releases/latest"
CACHE_TTL_SECONDS = 3600

_cache: tuple[float, dict[str, str] | None] = (0.0, None)


def _parse(version: str) -> tuple[int, ...] | None:
    """Turn '2.2.1' or 'v2.2.1-rc1' into (2, 2, 1); None if it isn't a version."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else None


def current_version() -> str:
    """Running version: the build-time env var, falling back to pyproject."""
    if _parse(VERSION):
        return VERSION
    # ponytail: regex over pyproject beats tomllib, which is 3.11+ only.
    try:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        match = re.search(r'^version = "([^"]+)"', pyproject.read_text(), re.M)
    except OSError:
        return VERSION
    return match.group(1) if match else VERSION


async def check_for_upgrade() -> dict[str, str] | None:
    """Return {'version', 'url'} when a newer release exists, else None."""
    global _cache
    cached_at, cached = _cache
    if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
        return cached

    latest = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                LATEST_RELEASE_URL, headers={"Accept": "application/vnd.github+json"}
            )
            response.raise_for_status()
            release = response.json()
        current = _parse(current_version())
        newest = _parse(release.get("tag_name", ""))
        if current and newest and newest > current:
            latest = {"version": release["tag_name"], "url": release["html_url"]}
    except Exception as exc:  # network, rate limit, malformed payload
        logger.debug(f"Upgrade check failed: {exc}")

    _cache = (time.monotonic(), latest)
    return latest
