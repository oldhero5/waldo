"""Authentication endpoints — register, login, token refresh, user info."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lib.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from lib.db import SessionLocal, User, Workspace, WorkspaceMember

router = APIRouter()


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str
    workspace_name: str = "My Workspace"


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    avatar_url: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    role: str | None = None


@router.post("/auth/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest):
    session = SessionLocal()
    try:
        # Check if email already exists
        existing = session.query(User).filter_by(email=req.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        # Create user
        user = User(
            email=req.email,
            password_hash=hash_password(req.password),
            display_name=req.display_name,
        )
        session.add(user)
        session.flush()

        # Create workspace
        slug = req.workspace_name.lower().replace(" ", "-")[:50]
        workspace = Workspace(name=req.workspace_name, slug=slug)
        session.add(workspace)
        session.flush()

        # Add user as admin
        member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="admin")
        session.add(member)
        session.commit()

        return TokenResponse(
            access_token=create_access_token(str(user.id)),
            refresh_token=create_refresh_token(str(user.id)),
        )
    finally:
        session.close()


@router.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest):
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(email=req.email).first()
        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        user.last_login = datetime.now(UTC)
        session.commit()

        return TokenResponse(
            access_token=create_access_token(str(user.id)),
            refresh_token=create_refresh_token(str(user.id)),
        )
    finally:
        session.close()


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh_token(refresh_token: str):
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
    )


@router.get("/auth/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        # Get user's primary workspace
        member = session.query(WorkspaceMember).filter_by(user_id=user.id).first()
        workspace = session.query(Workspace).filter_by(id=member.workspace_id).first() if member else None

        return UserResponse(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            workspace_id=str(workspace.id) if workspace else None,
            workspace_name=workspace.name if workspace else None,
            role=member.role if member else None,
        )
    finally:
        session.close()
