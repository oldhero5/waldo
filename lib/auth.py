"""Authentication utilities — JWT tokens, password hashing, FastAPI dependencies."""
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from lib.config import settings
from lib.db import ApiKey, SessionLocal, User, WorkspaceMember

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


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
    """FastAPI dependency — extracts user from JWT or API key.

    If no users exist in the database (first run), creates an admin user
    and returns it without requiring auth. This provides a smooth onboarding
    experience where the first user to access the app becomes the admin.
    """
    session = SessionLocal()
    try:
        # Check if any users exist — if not, bootstrap
        user_count = session.query(User).count()
        if user_count == 0:
            return _bootstrap_admin(session)

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
        user_count = session.query(User).count()
        if user_count == 0:
            return _bootstrap_admin(session)
        try:
            return _auth_jwt(session, credentials.credentials)
        except HTTPException:
            return None
    finally:
        session.close()


def _bootstrap_admin(session) -> User:
    """Create the first admin user + default workspace on first access."""
    from lib.db import Workspace

    workspace = session.query(Workspace).first()
    if not workspace:
        workspace = Workspace(name="Default Workspace", slug="default")
        session.add(workspace)
        session.flush()

    user = User(
        email="admin@localhost",
        password_hash=hash_password("admin"),
        display_name="Admin",
    )
    session.add(user)
    session.flush()

    member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="admin")
    session.add(member)

    # Assign existing projects to this workspace
    from lib.db import Project
    for project in session.query(Project).filter(Project.workspace_id.is_(None)).all():
        project.workspace_id = workspace.id

    session.commit()
    session.refresh(user)
    return user


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
