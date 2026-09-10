"""GitHub App authentication utilities."""

import asyncio
import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

import jwt

from baloo.config.settings import settings

logger = logging.getLogger(__name__)


def generate_jwt() -> str:
    """
    Generate a JWT for GitHub App authentication.

    Returns:
        JWT token as string
    """
    now = int(time.time())
    payload = {
        "iat": now - 60,  # Issued at time (60 seconds in the past to allow for clock drift)
        "exp": now + (10 * 60),  # JWT expiration time (10 minutes)
        "iss": settings.github_app_id,
    }

    token = jwt.encode(payload, settings.github_private_key_bytes, algorithm="RS256")
    return token


def verify_webhook_signature(payload_body: bytes, signature_header: str) -> bool:
    """
    Verify that the webhook payload was sent from GitHub by validating its signature.

    When WEBHOOK_PRE_VERIFIED is True, the signature check is skipped because
    a trusted proxy (e.g., baloo-cloud) has already validated it.

    Args:
        payload_body: Raw request body bytes
        signature_header: X-Hub-Signature-256 header value

    Returns:
        True if signature is valid, False otherwise
    """
    if settings.webhook_pre_verified:
        logger.debug("Webhook signature check skipped — WEBHOOK_PRE_VERIFIED is enabled")
        return True

    if not signature_header:
        return False

    # GitHub sends the signature as "sha256=<signature>"
    try:
        hash_algorithm, signature = signature_header.split("=")
    except ValueError:
        return False

    if hash_algorithm != "sha256":
        return False

    # Calculate expected signature
    expected_signature = hmac.new(
        settings.github_webhook_secret.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    # Compare signatures using constant-time comparison
    return hmac.compare_digest(expected_signature, signature)


class GitHubAuth:
    """Manages GitHub App authentication and installation tokens."""

    def __init__(self):
        self._installation_tokens: dict[int, tuple[str, datetime]] = {}

    def get_installation_token(self, installation_id: int) -> str:
        """
        Get an installation access token for the given installation ID.

        Installation tokens are cached and reused until they expire.

        Args:
            installation_id: GitHub App installation ID

        Returns:
            Installation access token
        """
        # Check if we have a cached token that's still valid
        if installation_id in self._installation_tokens:
            token, expires_at = self._installation_tokens[installation_id]
            # Use token if it doesn't expire within the next 5 minutes
            if datetime.now(timezone.utc) + timedelta(minutes=5) < expires_at:
                return token

        # Need to fetch a new token
        import httpx

        jwt_token = generate_jwt()
        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"

        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        response = httpx.post(url, headers=headers)
        response.raise_for_status()

        data = response.json()
        token = data["token"]
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))

        # Cache the token
        self._installation_tokens[installation_id] = (token, expires_at)

        return token


async def verify_repo_belongs_to_installation(installation_id: int, repo_full_name: str) -> bool:
    """Return True if repo_full_name is accessible under the given installation token."""
    import httpx

    auth = GitHubAuth()
    try:
        token = auth.get_installation_token(installation_id)
    except httpx.HTTPStatusError:
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{repo_full_name}",
            headers=headers,
        )
    return response.status_code == 200


# ponytail: process-local cache — the app slug only changes if the app is renamed,
# which a restart picks up.
_app_bot_login: str | None = None
_app_bot_login_lock = asyncio.Lock()


async def get_app_bot_login() -> str:
    """
    Return the app's reviewer login, e.g. "baloo-code-reviewer[bot]".

    Cached for the process; resolved from GET /app so it follows whichever
    GitHub App these credentials belong to.
    """
    global _app_bot_login

    if _app_bot_login is not None:
        return _app_bot_login

    # One fetch per process even when a burst of reviews starts at once.
    async with _app_bot_login_lock:
        if _app_bot_login is not None:
            return _app_bot_login

        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/app",
                headers={
                    "Authorization": f"Bearer {generate_jwt()}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        response.raise_for_status()
        slug = response.json().get("slug")
        if not slug:
            raise ValueError("GET /app returned no slug")
        _app_bot_login = f"{slug}[bot]"

    return _app_bot_login


async def is_this_app(login: str | None) -> bool:
    """
    True if `login` is this app's own bot account.

    Falls back to the fuzzy `is_baloo_actor` heuristic when the login can't be
    resolved, so an unreachable GET /app degrades instead of dropping events.
    """
    from baloo.github.discussions import is_baloo_actor

    try:
        return login == await get_app_bot_login()
    except Exception:
        logger.warning("Could not resolve app bot login — falling back to name heuristic")
        return is_baloo_actor(login)
