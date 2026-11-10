import hashlib
import hmac


def validate_signature(
    *,
    secret: str,
    data_id: str,
    request_id: str,
    signature: str,
) -> bool:
    parts = {}
    for part in signature.split(","):
        key, separator, value = part.strip().partition("=")
        if separator:
            parts[key] = value
    timestamp = parts.get("ts")
    received_digest = parts.get("v1")
    if not timestamp or not received_digest:
        return False
    manifest = (
        f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"
    ).encode()
    expected_digest = hmac.new(
        secret.encode(), manifest, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(received_digest, expected_digest)
