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

# -inf, not 0.0: time.monotonic() is time since boot on Linux, so on a
# freshly booted host 0.0 still looks like a fresh cache entry and the
# check would be suppressed for the first hour of uptime.
_EMPTY_CACHE: tuple[float, dict[str, str] | None] = (float("-inf"), None)
_cache = _EMPTY_CACHE


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
    # ponytail: no lock around the fetch. A cache miss served to N concurrent
    # requests costs N GitHub calls once per TTL, well inside the 60/hr
    # unauthenticated budget; add an asyncio.Lock if the check ever needs auth.
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
        url = release.get("html_url", "")
        # The banner renders this as an href, and Jinja's autoescape stops tag
        # injection but not a javascript: scheme.
        if current and newest and newest > current and url.startswith("https://"):
            latest = {"version": release["tag_name"], "url": url}
    except Exception as exc:  # network, rate limit, malformed payload
        # Cached like a real result, so this warns at most once per TTL.
        logger.warning(f"Upgrade check failed: {exc}")

    _cache = (time.monotonic(), latest)
    return latest
