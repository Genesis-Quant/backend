from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core.apps.incremental.models import IncrementalUpdateTask
from core.apps.tasks.models import WorkflowTaskInstance
from core.apps.tasks.services import (
    TaskGatewayService,
    delete_workflow_task_mappings,
    synchronize_workflow_task_instances,
    workflow_task_backfill_statement,
)
from core.apps.users.models import User
from core.database.base import Base


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            IncrementalUpdateTask.__table__,
            WorkflowTaskInstance.__table__,
        ],
    )
    with Session(engine) as active_session:
        yield active_session


def create_user(session: Session, username: str, *, is_admin: bool = False) -> User:
    user = User(
        username=username,
        password_hash="test-password-hash",
        is_admin=is_admin,
    )
    session.add(user)
    session.flush()
    return user


def create_incremental_task(session: Session, user_id: int) -> IncrementalUpdateTask:
    task = IncrementalUpdateTask(
        user_id=user_id,
        payload={},
        requested_outputs=[],
        state="RUNNING_EXECUTION",
        task_id_history=[],
        process_instance_history=[],
        workflow_tasks=[],
        state_history=[],
        events=[],
    )
    session.add(task)
    session.flush()
    return task


def test_child_task_lookup_uses_indexed_mapping(session: Session) -> None:
    owner = create_user(session, "owner")
    outsider = create_user(session, "outsider")
    administrator = create_user(session, "administrator", is_admin=True)
    task = create_incremental_task(session, owner.id)
    synchronize_workflow_task_instances(
        session,
        application="incremental",
        record_id=task.id,
        instances=[{"id": 321, "taskCode": 99}],
    )
    session.commit()

    application, found = TaskGatewayService().find_accessible_task(
        session,
        owner,
        321,
    )
    assert application == "incremental"
    assert found.id == task.id

    application, found = TaskGatewayService().find_accessible_task(
        session,
        administrator,
        321,
    )
    assert application == "incremental"
    assert found.id == task.id

    with pytest.raises(FileNotFoundError):
        TaskGatewayService().find_accessible_task(session, outsider, 321)


def test_task_mapping_preserves_old_attempts_and_deletes_by_parent(
    session: Session,
) -> None:
    owner = create_user(session, "owner")
    task = create_incremental_task(session, owner.id)
    synchronize_workflow_task_instances(
        session,
        application="incremental",
        record_id=task.id,
        instances=[
            {"id": 100, "taskCode": 9},
            {"id": 101, "taskCode": 9},
        ],
    )
    session.commit()

    assert list(
        session.scalars(
            select(WorkflowTaskInstance.task_instance_id).order_by(
                WorkflowTaskInstance.task_instance_id
            )
        )
    ) == [100, 101]

    delete_workflow_task_mappings(session, "incremental", [task.id])
    session.commit()
    assert list(session.scalars(select(WorkflowTaskInstance))) == []


def test_backfill_batch_skips_recent_failures_and_prioritizes_untried_rows(
    session: Session,
) -> None:
    owner = create_user(session, "owner")
    now = datetime.now(UTC)
    untried = create_incremental_task(session, owner.id)
    untried.state = "SUCCESS"
    untried.process_instance_id = 1
    stale = create_incremental_task(session, owner.id)
    stale.state = "SUCCESS"
    stale.process_instance_id = 2
    stale.last_synced_at = now - timedelta(hours=2)
    recent_failure = create_incremental_task(session, owner.id)
    recent_failure.state = "FAILURE"
    recent_failure.process_instance_id = 3
    recent_failure.last_synced_at = now
    session.commit()

    selected = list(
        session.scalars(
            workflow_task_backfill_statement(
                IncrementalUpdateTask,
                retry_before=now - timedelta(hours=1),
                limit=10,
            )
        )
    )
    assert [task.id for task in selected] == [untried.id, stale.id]
