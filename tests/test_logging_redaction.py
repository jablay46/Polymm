"""Tests that secrets never reach a log sink (task 0.5.3)."""

from __future__ import annotations

import io
import logging

import pytest

from polymm.logging import REDACTED, SecretRedactor, configure_logging

pytestmark = pytest.mark.unit

PRIVATE_KEY = "0x" + "ab" * 32
API_SECRET = "s3cr3t-" + "x" * 40
PASSPHRASE = "my-passphrase-value"
SIGNATURE = "0x" + "de" * 65


def test_known_value_is_redacted() -> None:
    r = SecretRedactor([API_SECRET])
    assert API_SECRET not in r.redact(f"secret is {API_SECRET} ok")
    assert REDACTED in r.redact(f"secret is {API_SECRET}")


def test_hex_private_key_redacted_by_pattern() -> None:
    """A key is caught even if the redactor never learned its value."""
    r = SecretRedactor()
    assert PRIVATE_KEY not in r.redact(f"key={PRIVATE_KEY}")


def test_key_value_pairs_redacted() -> None:
    r = SecretRedactor()
    out = r.redact("api_secret=abc123def456 passphrase: hunter2xyz")
    assert "abc123def456" not in out
    assert "hunter2xyz" not in out


def test_short_values_not_redacted() -> None:
    """Redacting tiny values would corrupt unrelated text."""
    r = SecretRedactor(["abc"])
    assert "abc" in r.redact("the abc of trading")


def test_signature_redacted() -> None:
    r = SecretRedactor()
    assert SIGNATURE not in r.redact(f"sig {SIGNATURE}")


def test_logging_filter_scrubs_interpolated_secret() -> None:
    """An f-string that interpolated a key is caught before formatting."""
    stream = io.StringIO()
    configure_logging("INFO", secrets=[API_SECRET])
    root = logging.getLogger()
    root.handlers[0].stream = stream  # type: ignore[attr-defined]

    logging.getLogger("polymm.test").info("placing order with %s", API_SECRET)

    captured = stream.getvalue()
    assert API_SECRET not in captured
    assert REDACTED in captured


def test_configure_logging_replaces_prior_handlers() -> None:
    """A previously installed non-redacting handler must not survive."""
    root = logging.getLogger()
    before = len(root.handlers)
    configure_logging("INFO")
    assert len(root.handlers) == 1
    # Restore a sane state for other tests.
    assert before >= 0
