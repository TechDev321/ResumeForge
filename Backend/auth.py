from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from fastapi import Header, HTTPException


def get_app_password() -> str:
    return (os.getenv("APP_PASSWORD") or "").strip()


def auth_required() -> bool:
    """Password gate is active when APP_PASSWORD is set."""
    return bool(get_app_password())


def _auth_secret() -> str:
    # Optional separate secret; falls back to the password itself.
    return (os.getenv("APP_AUTH_SECRET") or "").strip() or get_app_password()


def make_session_token(password: str) -> str:
    return hmac.new(
        _auth_secret().encode("utf-8"),
        password.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def expected_session_token() -> str:
    password = get_app_password()
    if not password:
        return ""
    return make_session_token(password)


def verify_password(password: str) -> bool:
    expected = get_app_password()
    if not expected:
        return True
    return secrets.compare_digest(password or "", expected)


def require_auth(authorization: str | None = Header(default=None)) -> None:
    """Dependency for protected API routes."""
    if not auth_required():
        return

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")

    token = authorization[7:].strip()
    expected = expected_session_token()
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
