import hashlib
import hmac

from app.github.signatures import verify_webhook_signature


def test_valid_signature_is_accepted() -> None:
    raw_body = b'{"action":"opened"}'
    secret = "test-secret"
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(raw_body, f"sha256={digest}", secret)


def test_invalid_or_missing_signature_is_rejected() -> None:
    raw_body = b'{"action":"opened"}'

    assert not verify_webhook_signature(raw_body, "sha256=invalid", "test-secret")
    assert not verify_webhook_signature(raw_body, None, "test-secret")
