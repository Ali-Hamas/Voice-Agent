"""Password hashing (scrypt, stdlib) + session helpers."""
from __future__ import annotations

import hashlib
import os
import secrets

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from .db import get_user


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return secrets.compare_digest(candidate, expected)


def current_user(request: Request) -> dict | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    return get_user(int(uid))


def require_login(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="login required",
            headers={"Location": "/login"},
        )
    return user


def login_user(request: Request, user_id: int) -> None:
    request.session["uid"] = user_id


def logout_user(request: Request) -> None:
    request.session.pop("uid", None)
