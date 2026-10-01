"""Authentication and RBAC.

Local accounts with bcrypt-hashed passwords. Login yields an opaque,
revocable token (30-day expiry) sent as ``Authorization: Bearer``.
Roles: admin > operator > viewer; viewers are read-only (GET only).
"""
import logging
import secrets
import time
import uuid

import bcrypt

from .db import session_scope
from .models import Session, User

log = logging.getLogger("drydock.auth")

TOKEN_TTL = 30 * 24 * 3600  # 30 days
MIN_PASSWORD_LEN = 8

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
ROLES = tuple(ROLE_RANK)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def validate_new_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(
            f"password must be at least {MIN_PASSWORD_LEN} characters")


def create_user(username: str, password: str, role: str = "operator") -> User:
    username = (username or "").strip()
    if not username:
        raise ValueError("username is required")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    validate_new_password(password)
    with session_scope() as s:
        if s.query(User).filter_by(username=username).first():
            raise ValueError("username already exists")
        u = User(id=uuid.uuid4().hex[:8], username=username,
                 password_hash=hash_password(password),
                 role=role, created_at=time.time())
        s.add(u)
        s.flush()
        return u


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with session_scope() as s:
        # prune the user's expired sessions opportunistically
        s.query(Session).filter(Session.user_id == user_id,
                                Session.expires_at < now).delete()
        s.add(Session(token=token, user_id=user_id,
                      created_at=now, expires_at=now + TOKEN_TTL))
    return token


def get_user_by_token(token: str):
    """Return the User for a live token, else None."""
    if not token:
        return None
    now = time.time()
    with session_scope() as s:
        sess = s.get(Session, token)
        if not sess or sess.expires_at < now:
            if sess:
                s.delete(sess)
            return None
        u = s.get(User, sess.user_id)
        if not u:
            return None
        s.expunge(u)
        return u


def revoke_token(token: str) -> None:
    with session_scope() as s:
        sess = s.get(Session, token)
        if sess:
            s.delete(sess)


def user_count() -> int:
    with session_scope() as s:
        return s.query(User).count()


def admin_count() -> int:
    with session_scope() as s:
        return s.query(User).filter_by(role="admin").count()


def public_user(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role,
            "created_at": u.created_at}
