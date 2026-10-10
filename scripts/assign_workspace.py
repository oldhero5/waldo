"""Explicitly recover legacy unowned resources after the authorization upgrade.

Run from the installation host with database credentials. Dry-run by default:
python -m scripts.assign_workspace --workspace-id UUID --resource project --id UUID
Repeat with --apply to commit. This never moves an already owned resource.
"""

import argparse
import uuid

from fastapi import HTTPException

from lib.authorization import Principal, WorkspacePrincipal, require_resource
from lib.db import (
    ComparisonRun,
    DemoFeedback,
    DeploymentTarget,
    EdgeDevice,
    InferenceLog,
    ModelRegistry,
    Project,
    SavedWorkflow,
    SessionLocal,
    Workspace,
)

RESOURCES = {
    "project": Project,
    "workflow": SavedWorkflow,
    "target": DeploymentTarget,
    "device": EdgeDevice,
    "comparison": ComparisonRun,
    "feedback": DemoFeedback,
    "inference-log": InferenceLog,
}


def assign_resources(session, workspace_id, resource_type, resource_ids, *, apply=False):
    if session.get(Workspace, workspace_id) is None:
        raise ValueError("Workspace not found")
    model = RESOURCES[resource_type]
    # Used only for ownership queries; CLI access is controlled by host/DB access.
    principal = WorkspacePrincipal(Principal(user_id=uuid.UUID(int=0)), workspace_id, "admin")
    rows = []
    for resource_id in dict.fromkeys(resource_ids):
        row = session.get(model, resource_id)
        if row is None:
            raise ValueError(f"{resource_type} {resource_id} not found")
        if row.workspace_id not in (None, workspace_id):
            raise ValueError(f"{resource_type} {resource_id} already belongs to another workspace")
        # Do not make foreign trained models or endpoints visible through a new owner field.
        for attribute, linked_model in (
            ("model_id", ModelRegistry),
            ("target_id", DeploymentTarget),
            ("model_a_id", ModelRegistry),
            ("model_b_id", ModelRegistry),
        ):
            linked_id = getattr(row, attribute, None)
            if not linked_id:
                continue
            try:
                linked_id = uuid.UUID(str(linked_id))
            except ValueError:
                if attribute in ("model_a_id", "model_b_id"):
                    continue  # Built-in model names are not persisted registry IDs.
                raise ValueError(f"Invalid {attribute} on {resource_id}") from None
            try:
                require_resource(session, principal, linked_model, linked_id)
            except HTTPException:
                raise ValueError(
                    f"Assign {attribute} {linked_id} to this workspace first, or correct the link"
                ) from None
        rows.append(row)
    if apply:
        for row in rows:
            row.workspace_id = workspace_id
        session.commit()
    return [str(row.id) for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=uuid.UUID, required=True)
    parser.add_argument("--resource", choices=RESOURCES, required=True)
    parser.add_argument("--id", type=uuid.UUID, action="append", required=True, dest="ids")
    parser.add_argument("--apply", action="store_true", help="Commit these explicit assignments")
    args = parser.parse_args()
    with SessionLocal() as session:
        try:
            ids = assign_resources(session, args.workspace_id, args.resource, args.ids, apply=args.apply)
        except ValueError as error:
            parser.error(str(error))
    action = "Assigned" if args.apply else "Would assign (dry-run)"
    print(f"{action} {args.resource} {', '.join(ids)} to workspace {args.workspace_id}")


if __name__ == "__main__":
    main()
