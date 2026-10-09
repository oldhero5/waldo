import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lib.db import Base, DeploymentTarget, EdgeDevice, Project, Workspace
from scripts.assign_workspace import assign_resources


def test_assignment_is_explicit_atomic_and_cannot_move_owned_data():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        own = Workspace(id=uuid.uuid4(), name="Own", slug="own")
        other = Workspace(id=uuid.uuid4(), name="Other", slug="other")
        legacy = Project(id=uuid.uuid4(), name="Legacy")
        foreign = Project(id=uuid.uuid4(), name="Foreign", workspace_id=other.id)
        session.add_all([own, other, legacy, foreign])
        session.commit()
        assign_resources(session, own.id, "project", [legacy.id])
        assert legacy.workspace_id is None
        with pytest.raises(ValueError, match="another workspace"):
            assign_resources(session, own.id, "project", [legacy.id, foreign.id], apply=True)
        assert legacy.workspace_id is None
        assign_resources(session, own.id, "project", [legacy.id], apply=True)
        session.refresh(legacy)
        assert legacy.workspace_id == own.id


def test_assignment_cannot_claim_a_foreign_endpoint_indirectly():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        own = Workspace(id=uuid.uuid4(), name="Own", slug="own")
        other = Workspace(id=uuid.uuid4(), name="Other", slug="other")
        target = DeploymentTarget(id=uuid.uuid4(), name="Foreign", workspace_id=other.id)
        device = EdgeDevice(id=uuid.uuid4(), name="Legacy", device_type="jetson", target_id=target.id)
        session.add_all([own, other, target, device])
        session.commit()
        with pytest.raises(ValueError, match="target_id"):
            assign_resources(session, own.id, "device", [device.id], apply=True)
        assert device.workspace_id is None
