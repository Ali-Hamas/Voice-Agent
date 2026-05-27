"""HMAC-SHA256 verification for the public outbound enqueue webhook."""
from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str) -> bool:
    if not secret or not signature:
        return False
    expected = sign(secret, body)
    try:
        return hmac.compare_digest(expected, signature.strip().lower())
    except Exception:
        return False
