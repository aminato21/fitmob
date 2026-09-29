from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from app.config import Settings


COOKIE_NAME = "runstead_session"
SESSION_MAX_AGE_SEC = 60 * 60 * 24 * 30
PASSWORD_ITERATIONS = 310_000
_EPHEMERAL_SECRET = secrets.token_urlsafe(48)


def hash_password(password: str) -> tuple[str, str, int]:
    salt = secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return salt.hex(), password_hash.hex(), PASSWORD_ITERATIONS


def password_matches(
    password: str, salt_hex: str, password_hash_hex: str, iterations: int
) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(password_hash_hex)
    except ValueError:
        return False
    supplied_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations)
    )
    return hmac.compare_digest(supplied_hash, expected_hash)


def create_session_cookie(settings: Settings, username: str) -> str:
    expires_at = int(time.time()) + SESSION_MAX_AGE_SEC
    payload = f"{username}:{expires_at}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        _session_secret(settings), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def session_cookie_username(
    settings: Settings, cookie: str | None
) -> str | None:
    if not cookie or "." not in cookie:
        return None
    encoded, supplied_signature = cookie.rsplit(".", 1)
    expected_signature = hmac.new(
        _session_secret(settings), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        username, expires_text = payload.rsplit(":", 1)
        expires_at = int(expires_text)
    except (ValueError, UnicodeDecodeError):
        return None
    return username if expires_at >= int(time.time()) else None


def _session_secret(settings: Settings) -> bytes:
    return (settings.auth_secret_key or _EPHEMERAL_SECRET).encode("utf-8")
