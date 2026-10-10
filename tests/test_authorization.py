"""Installation and workspace authorization regressions without external services."""

import importlib.util
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import admin as admin_api
from app.api import auth as auth_api
from lib import auth
from lib.db import ApiKey, Base, Project, User, Workspace, WorkspaceMember


@pytest.fixture
def database(monkeypatch):
    from lib import authorization

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(auth, "SessionLocal", factory)
    monkeypatch.setattr(auth_api, "SessionLocal", factory)
    monkeypatch.setattr(authorization, "SessionLocal", factory)
    with factory() as session:
        yield session, factory
    engine.dispose()


def client():
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api/v1")
    app.include_router(admin_api.router, prefix="/api/v1")
    return TestClient(app)


def add_user(session, *, role="admin"):
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", display_name="Test")
    workspace = Workspace(name="Test", slug=uuid.uuid4().hex)
    session.add_all([user, workspace])
    session.flush()
    session.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role=role))
    session.commit()
    return user, workspace


def test_registration_cannot_grant_installation_admin(database, monkeypatch):
    session, _ = database
    monkeypatch.setattr(admin_api, "_queue_depths", lambda: [])
    response = client().post(
        "/api/v1/auth/register",
        json={
            "email": "signup@example.com",
            "password": "password",
            "display_name": "Signup",
            "is_platform_admin": True,
        },
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    response = client().get("/api/v1/admin/queue", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    user = session.query(User).filter_by(email="signup@example.com").one()
    assert user.is_platform_admin is False


def test_workspace_admin_cannot_access_installation_admin(database, monkeypatch):
    session, _ = database
    user, _ = add_user(session)
    monkeypatch.setattr(admin_api, "_queue_depths", lambda: [])
    response = client().get(
        "/api/v1/admin/queue", headers={"Authorization": f"Bearer {auth.create_access_token(str(user.id))}"}
    )
    assert response.status_code == 403


def test_explicit_installation_admin_can_access_admin(database, monkeypatch):
    session, _ = database
    user, _ = add_user(session)
    user.is_platform_admin = True
    session.query(WorkspaceMember).filter_by(user_id=user.id).delete()
    session.commit()
    monkeypatch.setattr(admin_api, "_queue_depths", lambda: [])
    response = client().get(
        "/api/v1/admin/queue", headers={"Authorization": f"Bearer {auth.create_access_token(str(user.id))}"}
    )
    assert response.status_code == 200


def test_bootstrap_only_grants_installation_admin_to_first_user(database, monkeypatch):
    session, _ = database
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "test-bootstrap-password")
    auth.bootstrap_admin_if_empty()
    first = session.query(User).one()
    assert first.is_platform_admin is True
    first.is_platform_admin = False
    session.commit()
    auth.bootstrap_admin_if_empty()
    session.refresh(first)
    assert first.is_platform_admin is False


def test_operator_reset_explicitly_grants_installation_admin(database, monkeypatch):
    from scripts import reset_admin

    session, factory = database
    user, _ = add_user(session)
    monkeypatch.setattr(reset_admin, "SessionLocal", factory)
    monkeypatch.setattr("sys.argv", ["reset_admin", "--email", user.email, "--password", "new-password"])
    assert reset_admin.main() == 0
    session.refresh(user)
    assert user.is_platform_admin is True


def key_for(session, user, workspace, *, scopes, expires_at=None):
    raw = "wld_test" + uuid.uuid4().hex
    session.add(
        ApiKey(
            user_id=user.id,
            workspace_id=workspace.id,
            name="Test",
            key_prefix=raw[:8],
            key_hash=auth.hash_password(raw),
            scopes=scopes,
            expires_at=expires_at,
        )
    )
    session.commit()
    return raw


@pytest.mark.parametrize("scopes", [[], ["read"]])
def test_api_key_cannot_write_without_write_scope(database, scopes):
    session, _ = database
    user, workspace = add_user(session)
    raw = key_for(session, user, workspace, scopes=scopes)
    from fastapi import Depends

    app = FastAPI()

    @app.post("/write")
    def write(user=Depends(auth.get_current_user)):
        return {"ok": True}

    response = TestClient(app).post("/write", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 403


def test_naive_expired_api_key_returns_401(database):
    session, _ = database
    user, workspace = add_user(session)
    raw = key_for(session, user, workspace, scopes=["read"], expires_at=datetime.utcnow() - timedelta(days=1))
    response = client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 401


def test_api_key_auth_preserves_workspace_and_scope_context(database):
    from fastapi import Depends

    session, _ = database
    user, workspace = add_user(session)
    raw = key_for(session, user, workspace, scopes=["read"])
    app = FastAPI()

    @app.get("/identity")
    def identity(request: Request, user=Depends(auth.get_current_user)):
        principal = request.state.principal
        return {
            "workspace": str(principal.workspace_id),
            "scopes": sorted(principal.scopes),
            "auth_kind": principal.auth_kind,
        }

    response = TestClient(app).get("/identity", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 200
    assert response.json() == {"workspace": str(workspace.id), "scopes": ["read"], "auth_kind": "api_key"}


def test_auth_me_uses_api_key_workspace_instead_of_older_membership(database):
    session, _ = database
    user, first = add_user(session)
    _, keyed_workspace = add_user(session)
    session.add(WorkspaceMember(user_id=user.id, workspace_id=keyed_workspace.id, role="viewer"))
    session.commit()
    raw = key_for(session, user, keyed_workspace, scopes=["read"])
    response = client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 200
    assert response.json()["workspace_id"] == str(keyed_workspace.id)
    assert response.json()["workspace_id"] != str(first.id)


def test_auth_me_honors_explicit_workspace_selection(database):
    session, _ = database
    user, _ = add_user(session)
    _, selected = add_user(session)
    session.add(WorkspaceMember(user_id=user.id, workspace_id=selected.id, role="viewer"))
    session.commit()
    response = client().get(
        "/api/v1/auth/me",
        headers={
            "Authorization": f"Bearer {auth.create_access_token(str(user.id))}",
            "X-Workspace-ID": str(selected.id),
        },
    )
    assert response.status_code == 200
    assert response.json()["workspace_id"] == str(selected.id)


def test_key_owner_without_membership_is_denied(database):
    session, _ = database
    user, workspace = add_user(session)
    raw = key_for(session, user, workspace, scopes=["read", "write"])
    session.query(WorkspaceMember).filter_by(user_id=user.id).delete()
    session.commit()
    response = client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 403


def test_api_key_cannot_use_installation_admin_routes(database, monkeypatch):
    session, _ = database
    user, workspace = add_user(session)
    user.is_platform_admin = True
    session.commit()
    raw = key_for(session, user, workspace, scopes=["read", "write", "admin"])
    monkeypatch.setattr(admin_api, "_queue_depths", lambda: [])
    response = client().get("/api/v1/admin/queue", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 403


def test_privilege_migration_defaults_existing_users_to_unprivileged():
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    path = Path(__file__).parents[1] / "alembic/versions/0a1b2c3d4e5f_installation_admin.py"
    spec = importlib.util.spec_from_file_location("platform_admin_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users (id) VALUES (1)"))
        operations = Operations(MigrationContext.configure(connection))
        with operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(text("SELECT is_platform_admin FROM users")).scalar_one() == 0
    engine.dispose()


def test_workspace_resolution_requires_membership(database):
    from fastapi import HTTPException

    from lib.authorization import Principal, resolve_workspace

    session, _ = database
    with pytest.raises(HTTPException) as error:
        resolve_workspace(session, Principal(user_id=uuid.uuid4()))
    assert error.value.status_code == 403


def test_workspace_scope_excludes_foreign_and_null_projects(database):
    from fastapi import HTTPException

    from lib.authorization import Principal, require_project, resolve_workspace, scope_projects

    session, _ = database
    user, own = add_user(session)
    _, foreign = add_user(session)
    projects = [
        Project(name="own", workspace_id=own.id),
        Project(name="foreign", workspace_id=foreign.id),
        Project(name="legacy", workspace_id=None),
    ]
    session.add_all(projects)
    session.commit()
    principal = resolve_workspace(session, Principal(user_id=user.id))
    assert [p.name for p in scope_projects(session.query(Project), principal).all()] == ["own"]
    assert require_project(session, principal, projects[0].id).id == projects[0].id
    for project in projects[1:]:
        with pytest.raises(HTTPException) as error:
            require_project(session, principal, project.id)
        assert error.value.status_code == 404


def test_api_key_workspace_cannot_be_switched(database):
    from fastapi import HTTPException

    from lib.authorization import Principal, resolve_workspace

    session, _ = database
    user, own = add_user(session)
    _, foreign = add_user(session)
    session.add(WorkspaceMember(user_id=user.id, workspace_id=foreign.id, role="admin"))
    session.commit()
    key_for(session, user, own, scopes=["read"])
    key = session.query(ApiKey).filter_by(user_id=user.id).one()
    principal = Principal(
        user_id=user.id, auth_kind="api_key", api_key_id=key.id, workspace_id=own.id, scopes=frozenset({"read"})
    )
    assert resolve_workspace(session, principal).workspace_id == own.id
    with pytest.raises(HTTPException) as error:
        resolve_workspace(session, principal, workspace_id=foreign.id)
    assert error.value.status_code == 403


def test_workspace_role_and_api_scope_both_required_for_writes(database):
    from fastapi import HTTPException

    from lib.authorization import Principal, require_workspace_role, resolve_workspace

    session, _ = database
    user, workspace = add_user(session, role="viewer")
    principal = resolve_workspace(session, Principal(user_id=user.id))
    with pytest.raises(HTTPException) as error:
        require_workspace_role(principal, "admin", "editor")
    assert error.value.status_code == 403
    member = session.query(WorkspaceMember).filter_by(user_id=user.id).one()
    member.role = "admin"
    session.commit()
    key_for(session, user, workspace, scopes=["read"])
    key = session.query(ApiKey).filter_by(user_id=user.id).one()
    key_principal = resolve_workspace(
        session,
        Principal(
            user_id=user.id,
            auth_kind="api_key",
            api_key_id=key.id,
            workspace_id=workspace.id,
            scopes=frozenset({"read"}),
        ),
    )
    with pytest.raises(HTTPException) as error:
        require_workspace_role(key_principal, "admin", "editor")
    assert error.value.status_code == 403


def test_workspace_resolution_refuses_revoked_key(database):
    from fastapi import HTTPException

    from lib.authorization import Principal, resolve_workspace

    session, factory = database
    user, workspace = add_user(session)
    key_for(session, user, workspace, scopes=["read", "write"])
    key = session.query(ApiKey).one()
    identity = Principal(
        user_id=user.id,
        auth_kind="api_key",
        api_key_id=key.id,
        workspace_id=workspace.id,
        scopes=frozenset({"read", "write"}),
    )
    with factory() as other:
        other.query(ApiKey).filter_by(id=key.id).delete()
        other.commit()
    with pytest.raises(HTTPException) as error:
        resolve_workspace(session, identity)
    assert error.value.status_code == 401


def test_workspace_resolution_refreshes_downgraded_key_scopes(database):
    from fastapi import HTTPException

    from lib.authorization import Principal, require_workspace_role, resolve_workspace

    session, factory = database
    user, workspace = add_user(session)
    key_for(session, user, workspace, scopes=["read", "write"])
    key = session.query(ApiKey).one()
    identity = Principal(
        user_id=user.id,
        auth_kind="api_key",
        api_key_id=key.id,
        workspace_id=workspace.id,
        scopes=frozenset({"read", "write"}),
    )
    with factory() as other:
        other.query(ApiKey).filter_by(id=key.id).update({ApiKey.scopes: ["read"]})
        other.commit()
    refreshed = resolve_workspace(session, identity)
    assert refreshed.identity.scopes == frozenset({"read"})
    with pytest.raises(HTTPException) as error:
        require_workspace_role(refreshed, "admin", "editor")
    assert error.value.status_code == 403


def test_two_default_workspace_registrations_have_unique_slugs(database):
    api = client()
    for email in ("first-default@example.com", "second-default@example.com"):
        response = api.post(
            "/api/v1/auth/register", json={"email": email, "password": "StrongPassword123!", "display_name": "Default"}
        )
        assert response.status_code == 201
    session, _ = database
    assert len({workspace.slug for workspace in session.query(Workspace).all()}) == 2


def test_bootstrap_does_not_guess_legacy_project_ownership(database, monkeypatch):
    session, _ = database
    legacy = Project(name="Unassigned", workspace_id=None)
    session.add(legacy)
    session.commit()
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "test-bootstrap-password")
    auth.bootstrap_admin_if_empty()
    session.refresh(legacy)
    assert legacy.workspace_id is None
