#!/usr/bin/env python3
"""Upsert the bootstrap admin user and ensure it has admin membership.

Usage (host):
    uv run python scripts/reset_admin.py
    uv run python scripts/reset_admin.py --email me@example.com --password hunter2

Usage (docker):
    docker compose exec waldo-app uv run python scripts/reset_admin.py

Idempotent: if the user exists, the password and admin role are updated in
place. If not, the user + workspace + membership are created. Either way,
the script prints the resolved credentials at the end so you know what to
log in with.

Defaults match `bootstrap_admin_if_empty`: admin@waldo.ai / waldopass.
"""

import argparse
import sys

from lib.auth import hash_password
from lib.db import Project, SessionLocal, User, Workspace, WorkspaceMember


def main() -> int:
    parser = argparse.ArgumentParser(description="Upsert Waldo admin user")
    parser.add_argument("--email", default="admin@waldo.ai")
    parser.add_argument("--password", default="waldopass")  # pragma: allowlist secret
    parser.add_argument("--display-name", default="Admin")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        workspace = session.query(Workspace).first()
        if not workspace:
            workspace = Workspace(name="Default Workspace", slug="default")
            session.add(workspace)
            session.flush()
            print(f"  · created workspace {workspace.name}")

        user = session.query(User).filter_by(email=args.email).first()
        if user:
            user.password_hash = hash_password(args.password)
            user.display_name = user.display_name or args.display_name
            action = "updated"
        else:
            user = User(
                email=args.email,
                password_hash=hash_password(args.password),
                display_name=args.display_name,
            )
            session.add(user)
            session.flush()
            action = "created"

        membership = session.query(WorkspaceMember).filter_by(workspace_id=workspace.id, user_id=user.id).first()
        if not membership:
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="admin"))
        elif membership.role != "admin":
            membership.role = "admin"

        # Attach any orphan projects to the default workspace so the new admin
        # actually sees existing data after login.
        orphans = session.query(Project).filter(Project.workspace_id.is_(None)).all()
        for p in orphans:
            p.workspace_id = workspace.id
        if orphans:
            print(f"  · attached {len(orphans)} orphan project(s) to {workspace.name}")

        session.commit()

        banner = "=" * 60
        print(banner)
        print(f"Admin {action}:")
        print(f"  email:    {args.email}")
        print(f"  password: {args.password}")
        print(f"  workspace: {workspace.name}")
        print(banner)
        return 0
    except Exception as e:
        session.rollback()
        print(f"reset_admin failed: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
