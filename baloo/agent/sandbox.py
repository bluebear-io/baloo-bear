"""Filesystem sandbox for the agent subprocess.

Builds a `bwrap` (bubblewrap) argv prefix that limits the agent's filesystem
view to a single worktree, read-only. This enforces the multi-tenant boundary:
even a prompt-injected agent reading absolute paths cannot reach another
tenant's repo cache.

Network is intentionally left shared — the agent must call the model API. Only
the filesystem is restricted.

The subprocess is always spawned with a scrubbed, allowlisted environment
(`build_subprocess_env`) so baloo's secrets (GitHub key, DB creds, etc.) are
never exposed to a potentially prompt-injected agent that has open network
access — filesystem isolation alone does not address exfiltration, and the
scrub must not depend on the sandbox engaging.

The sandbox fails closed: when a non-off mode is configured but bubblewrap is
not functional, callers raise `SandboxUnavailableError` rather than running the
agent unisolated. `assert_startup_sandbox` runs the same check at process boot
so a misconfigured container dies immediately instead of at the first review.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_bwrap_works: bool | None = None  # cached runtime-probe result


class SandboxUnavailableError(RuntimeError):
    """Raised when a sandbox is required but bubblewrap cannot run.

    Running the agent without isolation is not an acceptable fallback: the
    subprocess reads attacker-authored PR content, so an unsandboxed run turns
    a prompt injection into arbitrary reads of the host filesystem.
    """


# The probe must exercise the SAME privileged operations the real prefix does —
# notably `--proc` + `--unshare-pid` (mounting a fresh /proc in a new PID/user
# namespace). A weaker probe like `bwrap --ro-bind / / -- true` passes on hardened
# platforms (default Docker seccomp, some k8s/AppArmor/gVisor) where the real
# command then dies with "Can't mount proc ... Operation not permitted" — a
# false positive that would crash every review instead of degrading. `/lib`
# (+`/lib64`) must be bound so the probe binary's dynamic loader is present;
# without it exec fails with a misleading "No such file or directory".
_PROBE_CMD = [
    "bwrap",
    "--ro-bind",
    "/usr",
    "/usr",
    "--ro-bind",
    "/bin",
    "/bin",
    "--ro-bind",
    "/lib",
    "/lib",
    "--ro-bind-try",
    "/lib64",
    "/lib64",
    "--proc",
    "/proc",
    "--dev",
    "/dev",
    "--tmpfs",
    "/tmp",
    "--unshare-pid",
    "--die-with-parent",
    "--",
    "true",
]


def sandbox_available(mode: str) -> bool:
    """Return True if the sandbox for `mode` is present AND actually runnable.

    `which bwrap` is not enough: bubblewrap needs unprivileged user namespaces
    (and the ability to mount a fresh /proc), which hardened platforms (some
    k8s/seccomp/AppArmor/gVisor setups, default Docker) block. If we only checked
    for the binary, every review would crash at the bwrap layer on such platforms
    with no fallback. So we probe once (cached) by actually running a bwrap
    invocation that mirrors the real prefix's privileged operations.
    """
    global _bwrap_works
    if mode != "bwrap":
        return False
    if shutil.which("bwrap") is None:
        return False
    if _bwrap_works is None:
        try:
            proc = subprocess.run(_PROBE_CMD, capture_output=True, timeout=5)
            _bwrap_works = proc.returncode == 0
            if not _bwrap_works:
                logger.error(
                    "bwrap probe failed (exit %s): %s",
                    proc.returncode,
                    proc.stderr.decode("utf-8", errors="replace").strip()[:500],
                )
        except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
            logger.error("bwrap probe raised: %s", exc)
            _bwrap_works = False
    return _bwrap_works


def reset_probe_cache() -> None:
    """Forget the cached bwrap probe result (tests only)."""
    global _bwrap_works
    _bwrap_works = None


def assert_startup_sandbox(mode: str) -> None:
    """Abort process startup when the configured sandbox cannot actually run.

    The failure mode this prevents is silent: bubblewrap is present in the image
    but blocked at runtime (default Docker seccomp, hardened k8s), which used to
    downgrade every review to an unisolated agent holding baloo's whole
    environment. Crashing at boot makes the misconfiguration impossible to miss.

    Raises:
        SandboxUnavailableError: mode is not 'off' and the sandbox is unusable.
    """
    if mode == "off":
        logger.warning(
            "REPO_SANDBOX_MODE=off — the agent subprocess runs without filesystem "
            "isolation. Only use this for local development."
        )
        return

    if sandbox_available(mode):
        logger.info("Sandbox check passed: repo_sandbox_mode=%s is functional", mode)
        return

    raise SandboxUnavailableError(
        f"repo_sandbox_mode={mode!r} is configured but the sandbox is not usable in "
        "this environment. Install bubblewrap and allow unprivileged user namespaces "
        "(e.g. run the container with --security-opt seccomp=unconfined or a seccomp "
        "profile that permits clone/unshare/mount), or set REPO_SANDBOX_MODE=off to "
        "explicitly accept running the review agent without filesystem isolation."
    )


def build_sandbox_prefix(mode: str, worktree: str) -> list[str]:
    """Return the argv prefix to wrap a tool-using pi command.

    The returned list ends with "--"; append the real command after it.
    Returns [] for any mode other than 'bwrap'.
    """
    if mode != "bwrap":
        return []

    wt = str(Path(worktree).resolve())
    return [
        "bwrap",
        # Minimal runtime: system dirs needed for node/pi to run.
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/opt",
        "/opt",
        # DNS: glibc getaddrinfo reads nsswitch.conf + hosts, not just resolv.conf.
        # Without these, resolving api.anthropic.com can fail despite resolv.conf.
        "--ro-bind-try",
        "/etc/resolv.conf",
        "/etc/resolv.conf",
        "--ro-bind-try",
        "/etc/nsswitch.conf",
        "/etc/nsswitch.conf",
        "--ro-bind-try",
        "/etc/hosts",
        "/etc/hosts",
        "--ro-bind-try",
        "/etc/ssl",
        "/etc/ssl",
        "--ro-bind-try",
        "/etc/ca-certificates",
        "/etc/ca-certificates",
        # AWS credential files (IRSA token, explicit shared credentials/config)
        # so Bedrock auth still works under the scrubbed sandbox env.
        *_aws_ro_bind_args(),
        # Generated models.json files: the Databricks provider registration and
        # the OpenRouter ZDR routing. Without them PI cannot resolve
        # `databricks/...` models, and OpenRouter requests lose ZDR.
        *_generated_agent_dir_ro_bind_args(),
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        # Fresh /tmp hides the repo cache (which lives under /tmp); the worktree
        # is bound afterwards so it overlays the tmpfs at its real path.
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        wt,
        wt,
        "--chdir",
        wt,
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--",
    ]


# Env vars the agent subprocess legitimately needs. Everything else (notably
# baloo's GitHub/DB/dashboard secrets) is dropped so a prompt-injected agent
# cannot read them from /proc/self/environ and exfiltrate over the open network.
# This applies to every spawn, sandboxed or not — the scrub is the last line of
# defence precisely when filesystem isolation is absent.
_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TERM",
        # Model providers (only the one in use is present; all listed for safety).
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        # OpenRouter (pi provider token: openrouter). Routing config reaches pi
        # through PI_CODING_AGENT_DIR, allowlisted below.
        "OPENROUTER_API_KEY",
        # Databricks AI Gateway (pi provider token: databricks). The PAT is read
        # by PI at request time via the apiKey env-var reference in models.json;
        # PI_CODING_AGENT_DIR points PI at that generated file.
        "DATABRICKS_TOKEN",
        "PI_CODING_AGENT_DIR",
        # AWS / Amazon Bedrock (pi provider token: amazon-bedrock). Static keys,
        # temporary session tokens, bearer auth, profiles, ECS task roles, and
        # IRSA are all recognized by the AWS SDK that pi bundles.
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_BEDROCK_FORCE_CACHE",
        "AWS_ENDPOINT_URL_BEDROCK_RUNTIME",
        "AWS_BEDROCK_SKIP_AUTH",
        "AWS_BEDROCK_FORCE_HTTP1",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        # Corporate proxy config — REQUIRED in enterprise networks or the agent
        # cannot reach the model API and every review fails. Both cases since some
        # tools read upper- and others lower-case.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "all_proxy",
        # CA bundle location if the image sets it explicitly.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
        "REQUESTS_CA_BUNDLE",
    }
)

# Host paths the AWS SDK may need to read inside the sandbox (IRSA token,
# explicit credentials/config files). Bound read-only when present.
_AWS_FILE_ENV_VARS = (
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
)

# Default shared-credentials locations the SDK reads when the *_FILE vars are
# unset. AWS_PROFILE is useless without them: the sandbox does not expose HOME,
# so ~/.aws would otherwise be invisible to the agent.
_AWS_DEFAULT_FILES = ("credentials", "config")


def _aws_ro_bind_args(env: dict[str, str] | None = None) -> list[str]:
    """Return ``--ro-bind-try`` pairs for AWS credential files referenced in env.

    Covers both explicit paths (IRSA token, ``AWS_SHARED_CREDENTIALS_FILE``,
    ``AWS_CONFIG_FILE``) and the default ``~/.aws`` files. Without these, the
    corresponding env vars survive the scrub but the files they point at do
    not exist inside bwrap, so Bedrock auth fails while Anthropic/Gemini keys
    still work. Paths are bound at their host location because HOME is passed
    through unchanged.
    """
    source = env if env is not None else os.environ
    args: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        if not raw:
            return
        path = str(Path(raw).expanduser().resolve())
        if path in seen:
            return
        seen.add(path)
        args.extend(["--ro-bind-try", path, path])

    for var in _AWS_FILE_ENV_VARS:
        _add(source.get(var))

    home = source.get("HOME")
    if home:
        for name in _AWS_DEFAULT_FILES:
            _add(str(Path(home) / ".aws" / name))

    return args


def _generated_agent_dir_ro_bind_args(env: dict[str, str] | None = None) -> list[str]:
    """Return ``--ro-bind-try`` pairs for the generated PI agent dirs.

    ``PI_CODING_AGENT_DIR`` survives the env scrub, but the directory it names
    lives on the host and would otherwise be invisible inside bwrap. Bound
    read-only: PI only reads models.json there, and ``--no-session`` plus
    ``HOME=/tmp`` cover anything it wants to write.

    ``--ro-bind-try`` makes each a no-op for providers that don't generate one,
    so every path is bound unconditionally rather than threading the provider
    down here.

    The fallback must match ``pi_agent_dir.agent_dir_path``, which uses
    ``Path.home()``. That falls back to the passwd entry when HOME is unset, so
    bailing out here would silently drop the bind in a container that strips
    HOME. The file would be written but invisible inside bwrap: Databricks then
    fails with ``Unknown provider "databricks"``, and OpenRouter — worse — runs
    on pi's built-in provider *without* the ZDR routing constraint.
    """
    from baloo.agent.databricks import AGENT_DIR_NAME as DATABRICKS_DIR
    from baloo.agent.openrouter import AGENT_DIR_NAME as OPENROUTER_DIR
    from baloo.agent.pi_agent_dir import agent_dir_path

    source = env if env is not None else os.environ
    home = source.get("HOME") or str(Path.home())
    args: list[str] = []
    for name in (DATABRICKS_DIR, OPENROUTER_DIR):
        path = str(agent_dir_path(name, home))
        args.extend(["--ro-bind-try", path, path])
    return args


def build_subprocess_env(base_env: dict[str, str]) -> dict[str, str]:
    """Return a minimal env (allowlist only) for the agent subprocess.

    Defaults HOME to /tmp so node/pi have a writable home inside the tmpfs even
    if the inherited HOME points at a path the sandbox does not bind writable.
    """
    env = {k: v for k, v in base_env.items() if k in _ENV_ALLOWLIST}
    env.setdefault("HOME", "/tmp")
    return env
