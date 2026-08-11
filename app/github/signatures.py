import hashlib
import hmac


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    if signature_header is None:
        return False

    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected_signature = f"sha256={digest}"
    return hmac.compare_digest(expected_signature, signature_header)
