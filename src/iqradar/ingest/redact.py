from __future__ import annotations

import re
from collections.abc import Sequence

REDACTION_MARKER = "[REDACTED_SECRET]"

SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{12,}\b")
BEARER_TOKEN_RE = re.compile(
    r"(?i)\b(Bearer\s+)(?!\[REDACTED_SECRET\]\b)[A-Za-z0-9._~+/=-]{12,}\b"
)


def _known_secret_values(values: Sequence[str]) -> list[str]:
    return sorted({value for value in values if value.strip()}, key=len, reverse=True)


def redact_secret_text(text: str, known_secret_values: Sequence[str] = ()) -> str:
    redacted = text
    for secret in _known_secret_values(known_secret_values):
        redacted = redacted.replace(secret, REDACTION_MARKER)
    redacted = SK_TOKEN_RE.sub(REDACTION_MARKER, redacted)
    return BEARER_TOKEN_RE.sub(r"\1" + REDACTION_MARKER, redacted)


def assert_no_secrets(text: str, known_secret_values: Sequence[str] = ()) -> None:
    for secret in _known_secret_values(known_secret_values):
        if secret in text:
            raise ValueError("Known secret value detected")
    if SK_TOKEN_RE.search(text) or BEARER_TOKEN_RE.search(text):
        raise ValueError("Secret-like value detected")
