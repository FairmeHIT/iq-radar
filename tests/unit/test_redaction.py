from __future__ import annotations

import pytest

from iqradar.ingest.redact import assert_no_secrets, redact_secret_text


def test_redacts_openai_style_sk_tokens() -> None:
    raw = "provider returned token sk-proj-AbCdEf1234567890_secretSuffix in logs"

    redacted = redact_secret_text(raw)

    assert "sk-proj-AbCdEf1234567890_secretSuffix" not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_redacts_bearer_tokens_while_preserving_header_shape() -> None:
    raw = "Authorization: Bearer abc.DEF-123_4567890"

    redacted = redact_secret_text(raw)

    assert redacted == "Authorization: Bearer [REDACTED_SECRET]"


def test_redacts_known_secret_values_exactly() -> None:
    raw = "base_url ok, api_key=local-secret-value-123, model ok"

    redacted = redact_secret_text(raw, known_secret_values=("local-secret-value-123",))

    assert "local-secret-value-123" not in redacted
    assert redacted == "base_url ok, api_key=[REDACTED_SECRET], model ok"


def test_ignores_empty_known_secret_values() -> None:
    assert redact_secret_text("plain text", known_secret_values=("", "  ")) == "plain text"


def test_assert_no_secrets_accepts_redacted_text() -> None:
    assert_no_secrets("Authorization: Bearer [REDACTED_SECRET]")


def test_assert_no_secrets_rejects_sk_tokens() -> None:
    with pytest.raises(ValueError, match="Secret-like value detected"):
        assert_no_secrets("leaked sk-live-AbCdEf1234567890")


def test_assert_no_secrets_rejects_bearer_tokens() -> None:
    with pytest.raises(ValueError, match="Secret-like value detected"):
        assert_no_secrets("Authorization: Bearer abc.DEF-123_4567890")


def test_assert_no_secrets_rejects_known_secret_values() -> None:
    with pytest.raises(ValueError, match="Known secret value detected"):
        assert_no_secrets("api_key=local-secret-value-123", ("local-secret-value-123",))
