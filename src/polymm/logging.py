"""Logging with mandatory secret redaction.

Secrets must never reach a log sink. Redaction is not optional and not
configurable — it is a filter applied to every record, and it also scrubs
the formatted message so an f-string that interpolated a key still gets
caught before it is written.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

REDACTED = "<redacted>"

# Values shorter than this are not treated as secrets: redacting a 3-char
# value would corrupt unrelated text and give a false sense of safety.
_MIN_SECRET_LEN = 8

# Structural patterns that look like secrets regardless of known values.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 0x-prefixed 32-byte hex (private keys). A 20-byte address or 32-byte
    # condition id is shorter and is left readable.
    re.compile(r"0x[0-9a-fA-F]{64}"),
    # 64 hex chars without prefix (bare private key).
    re.compile(r"\b[0-9a-fA-F]{64}\b"),
    # PEM blocks.
    re.compile(r"-----BEGIN[^-]+-----.*?-----END[^-]+-----", re.DOTALL),
)

# Keys whose value should always be redacted when logged as key=value.
_SENSITIVE_KEYS = (
    "private_key",
    "privatekey",
    "api_secret",
    "apisecret",
    "api_key",
    "apikey",
    "passphrase",
    "secret",
    "signature",
    "password",
    "token",
)
_KEY_VALUE_RE = re.compile(r"(?i)\b(" + "|".join(_SENSITIVE_KEYS) + r")\b\s*[=:]\s*(\S+)")


class SecretRedactor:
    """Holds the live secret values and scrubs them from any text."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets: list[str] = []
        for s in secrets:
            self.add(s)

    def add(self, secret: str | None) -> None:
        if secret and len(secret) >= _MIN_SECRET_LEN and secret not in self._secrets:
            self._secrets.append(secret)

    def redact(self, text: str) -> str:
        out = text
        # 1. Known live values first — highest confidence.
        for secret in self._secrets:
            out = out.replace(secret, REDACTED)
        # 2. key=value / key: value for sensitive key names.
        out = _KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
        # 3. Structural patterns.
        for pattern in _PATTERNS:
            out = pattern.sub(REDACTED, out)
        return out


class RedactingFilter(logging.Filter):
    """Scrubs the record message, args, and traceback before formatting."""

    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        # Render args into the message first so interpolated secrets are seen.
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            message = str(record.msg)
        record.msg = self._redactor.redact(message)
        record.args = ()

        # A traceback is rendered by the formatter *after* filters run, so
        # record.exc_text is still empty here. Render it ourselves, redact,
        # and hand the formatter pre-redacted text (this is what leaked a
        # private key that appeared inside an exception message).
        if record.exc_info:
            import traceback

            text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = self._redactor.redact(text)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._redactor.redact(record.exc_text)

        if record.stack_info:
            record.stack_info = self._redactor.redact(record.stack_info)
        return True


def configure_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    """Install a single redacting stderr handler on the root logger."""
    redactor = SecretRedactor(secrets)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(redactor))

    root = logging.getLogger()
    # Replace handlers so a previously installed non-redacting one cannot leak.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
