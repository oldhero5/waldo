"""Authentication utilities — JWT tokens, password hashing, FastAPI dependencies."""

import logging
import os
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from lib.config import settings
from lib.db import ApiKey, SessionLocal, User, WorkspaceMember

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# bcrypt's hard limit is 72 bytes — anything longer is silently truncated by
# the algorithm. We truncate explicitly so hash/verify stay consistent and
# the 4.x strict-mode exception never fires. We use bcrypt directly instead
# of passlib because passlib 1.7.4 reads `bcrypt.__about__.__version__` which
# bcrypt >=4.0 removed, breaking all hash/verify calls with a misleading
# "password too long" error.
_BCRYPT_MAX_BYTES = 72


def _to_bytes(password: str) -> bytes:
    data = password.encode("utf-8")
    if len(data) > _BCRYPT_MAX_BYTES:
        data = data[:_BCRYPT_MAX_BYTES]
    return data


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=settings.jwt_expire_minutes))
    payload = {"sub": user_id, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(UTC) + timedelta(days=30)
    payload = {"sub": user_id, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User:
    """FastAPI dependency — extracts user from JWT or API key."""
    session = SessionLocal()
    try:
        if not credentials:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        token = credentials.credentials

        # API key auth (starts with wld_)
        if token.startswith("wld_"):
            return _auth_api_key(session, token)

        # JWT auth
        return _auth_jwt(session, token)
    finally:
        session.close()


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User | None:
    """Like get_current_user but returns None instead of 401."""
    if not credentials:
        return None
    session = SessionLocal()
    try:
        try:
            return _auth_jwt(session, credentials.credentials)
        except HTTPException:
            return None
    finally:
        session.close()


def bootstrap_admin_if_empty() -> None:
    """Create the first admin user + workspace if the user table is empty.

    Call from app startup, NOT from request handlers — never auto-create users
    on an unauthenticated request, that's how default-credential takeovers happen.

    Password resolution (dev only — production always requires the env var):
      1. ADMIN_BOOTSTRAP_PASSWORD env var if set
      2. Otherwise, the dev default 'waldopass' so the first login Just Works
    Email resolution: ADMIN_BOOTSTRAP_EMAIL env var, default 'admin@waldo.ai'.
    """
    from lib.db import Project, Workspace

    session = SessionLocal()
    try:
        if session.query(User).count() > 0:
            return

        email = os.environ.get("ADMIN_BOOTSTRAP_EMAIL", "admin@waldo.ai")
        password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD")
        generated = False
        if not password:
            if settings.is_production():
                raise RuntimeError("Production startup requires ADMIN_BOOTSTRAP_PASSWORD when no users exist.")
            password = "waldopass"  # pragma: allowlist secret — dev-only default
            generated = True

        workspace = session.query(Workspace).first()
        if not workspace:
            workspace = Workspace(name="Default Workspace", slug="default")
            session.add(workspace)
            session.flush()

        user = User(
            email=email,
            password_hash=hash_password(password),
            display_name="Admin",
        )
        session.add(user)
        session.flush()

        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="admin"))

        for project in session.query(Project).filter(Project.workspace_id.is_(None)).all():
            project.workspace_id = workspace.id

        session.commit()

        if generated:
            banner = "=" * 72
            logger.warning(
                "\n%s\nWaldo bootstrapped first admin user.\n  email:    %s\n  password: %s\nStore this password — it will not be shown again.\n%s",
                banner,
                email,
                password,
                banner,
            )
    finally:
        session.close()


def _auth_jwt(session, token: str) -> User:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def _auth_api_key(session, key: str) -> User:
    prefix = key[:8]
    candidates = session.query(ApiKey).filter_by(key_prefix=prefix).all()
    for api_key in candidates:
        if verify_password(key, api_key.key_hash):
            if api_key.expires_at and api_key.expires_at < datetime.now(UTC):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key expired")
            # Update last_used
            api_key.last_used = datetime.now(UTC)
            session.commit()
            user = session.query(User).filter_by(id=api_key.user_id).first()
            if user:
                return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency — only admins pass.

    Admin = a WorkspaceMember row with role="admin" in any workspace.
    Raises 403 otherwise.
    """
    session = SessionLocal()
    try:
        is_admin = session.query(WorkspaceMember).filter_by(user_id=user.id, role="admin").first() is not None
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin role required",
            )
        return user
    finally:
        session.close()
