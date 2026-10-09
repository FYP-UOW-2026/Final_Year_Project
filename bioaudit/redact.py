"""Strip secrets out of finding evidence before it leaves this machine.

Evidence is taken from the app under test -- a logcat line, a provider's response -- so
it can carry the very token or password the finding is about. The backend stores what
it is sent, and an organisation's admins can read it, so redaction has to happen here,
before upload, not only server-side before the AI call (backend/src/utils/redact.js).

Intentionally aggressive: a false redaction costs a little context, while a missed one
puts a real credential in someone else's database.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED]"

_KEY_WORDS = r"token|secret|api[_-]?key|apikey|password|passwd|pwd|auth|bearer|session|credential"

# `key = value` / `key: value` / `"key": "value"`, for any key that names itself a
# secret. The key may be part of a longer identifier (access_token, db_password), which
# a \b-anchored pattern misses because "_" counts as a word character.
_KEY_VALUE = re.compile(
    rf"""(?i)(?P<key>(?<![A-Za-z0-9])[A-Za-z0-9_.-]*(?:{_KEY_WORDS})[A-Za-z0-9_-]*)"""
    # "[" is excluded so an already-redacted "[REDACTED]" is left alone.
    rf"""(?P<sep>["']?\s*[:=]\s*["']?)(?P<value>[^\s"',;&}}\[\]]+)"""
)

# Order matters: Bearer runs before the key/value pattern, which would otherwise
# redact only the word "Bearer" in "Authorization: Bearer <token>" and keep the token.
_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{10,}=*"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
]

# Long opaque blobs: base64-ish keys and hex hashes or raw keys.
_BLOBS = [
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
]


def redact(text: str | None) -> str | None:
    """Replace anything that looks like a secret in a single string."""
    if not text:
        return text
    out = text
    for pattern in _PATTERNS:
        out = pattern.sub(PLACEHOLDER, out)
    # Keep the key and its separator, so the reader still knows what kind of value sat there.
    out = _KEY_VALUE.sub(lambda m: f"{m.group('key')}{m.group('sep')}{PLACEHOLDER}", out)
    for pattern in _BLOBS:
        out = pattern.sub(PLACEHOLDER, out)
    return out
