"""Tests for WorkflowService: business rules layered on top of the
repository."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from kb_orchestrator.db.repository import WorkflowRepository
from kb_orchestrator.domain.errors import (
    StepNotAwaitingApprovalError,
    StepNotFoundError,
    WorkflowNotFoundError,
)
from kb_orchestrator.domain.models import Step, Workflow
from kb_orchestrator.domain.service import StepSpec, WorkflowService

_NOW = datetime.now(UTC)


def _service(connection: aiosqlite.Connection) -> WorkflowService:
    return WorkflowService(WorkflowRepository(connection))


async def _waiting_workflow(connection: aiosqlite.Connection) -> None:
    """Persists a workflow with one step already `waiting_for_approval` --
    the state `create_workflow` never produces directly (steps always
    start `pending`), so approve/reject tests set it up straight through
    the repository."""
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[
            Step(
                id="a",
                name="A",
                message="draft something",
                status="waiting_for_approval",
                result="a draft",
                requires_approval=True,
                created_at=_NOW,
                updated_at=_NOW,
            )
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )
    await WorkflowRepository(connection).create_workflow(workflow)


async def test_create_workflow_assigns_id_and_timestamps(
    db_connection: aiosqlite.Connection,
) -> None:
    service = _service(db_connection)

    workflow = await service.create_workflow(
        "research and draft",
        [StepSpec(id="research", name="Research", message="find stuff")],
    )

    assert workflow.id  # a UUID was assigned
    assert workflow.created_at == workflow.updated_at

    fetched = await service.get_workflow(workflow.id)
    assert fetched == workflow


async def test_create_workflow_assigns_step_ids_from_specs_not_generated(
    db_connection: aiosqlite.Connection,
) -> None:
    """Unlike the workflow's own id, step ids are exactly what the caller
    chose in each StepSpec -- scoped per-workflow, so a human-chosen
    "research"/"draft" is the point, not a generated UUID."""
    service = _service(db_connection)

    workflow = await service.create_workflow(
        "wf",
        [
            StepSpec(id="research", name="Research", message="find stuff"),
            StepSpec(id="draft", name="Draft", message="write it up", depends_on=["research"]),
        ],
    )

    assert [s.id for s in workflow.steps] == ["research", "draft"]
    assert workflow.steps[1].depends_on == ["research"]


async def test_create_workflow_with_duplicate_step_ids_raises(
    db_connection: aiosqlite.Connection,
) -> None:
    """Step 3's own domain validator catches this before anything is
    persisted -- proving the service actually lets that validation run
    rather than bypassing it."""
    service = _service(db_connection)

    with pytest.raises(ValueError, match="duplicate step id"):
        await service.create_workflow(
            "wf",
            [
                StepSpec(id="a", name="A", message="x"),
                StepSpec(id="a", name="A again", message="y"),
            ],
        )


async def test_get_workflow_raises_not_found(db_connection: aiosqlite.Connection) -> None:
    service = _service(db_connection)
    with pytest.raises(WorkflowNotFoundError):
        await service.get_workflow("does-not-exist")


async def test_create_workflow_with_no_steps_is_allowed(
    db_connection: aiosqlite.Connection,
) -> None:
    service = _service(db_connection)

    workflow = await service.create_workflow("empty wf", [])

    assert workflow.steps == []
    assert workflow.status == "pending"


async def test_create_workflow_threads_requires_approval_from_spec(
    db_connection: aiosqlite.Connection,
) -> None:
    service = _service(db_connection)

    workflow = await service.create_workflow(
        "wf", [StepSpec(id="a", name="A", message="draft it", requires_approval=True)]
    )

    assert workflow.steps[0].requires_approval is True


# --- approve_step / reject_step (Step 9) -----------------------------------


async def test_approve_step_moves_it_to_succeeded_keeping_its_result(
    db_connection: aiosqlite.Connection,
) -> None:
    await _waiting_workflow(db_connection)
    service = _service(db_connection)

    updated = await service.approve_step("w1", "a")

    assert updated.steps[0].status == "succeeded"
    assert updated.steps[0].result == "a draft"
    assert updated.status == "succeeded"


async def test_reject_step_moves_it_to_failed_and_records_the_reason(
    db_connection: aiosqlite.Connection,
) -> None:
    await _waiting_workflow(db_connection)
    service = _service(db_connection)

    updated = await service.reject_step("w1", "a", reason="not good enough")

    assert updated.steps[0].status == "failed"
    assert updated.steps[0].error == "not good enough"
    assert updated.steps[0].result == "a draft"  # the rejected draft is kept, not cleared
    assert updated.status == "failed"


async def test_approve_step_raises_when_step_is_not_awaiting_approval(
    db_connection: aiosqlite.Connection,
) -> None:
    service = _service(db_connection)
    workflow = await service.create_workflow(
        "wf", [StepSpec(id="a", name="A", message="x")]
    )  # step starts "pending", not "waiting_for_approval"

    with pytest.raises(StepNotAwaitingApprovalError):
        await service.approve_step(workflow.id, "a")


async def test_approve_step_raises_when_step_does_not_exist(
    db_connection: aiosqlite.Connection,
) -> None:
    await _waiting_workflow(db_connection)
    service = _service(db_connection)

    with pytest.raises(StepNotFoundError):
        await service.approve_step("w1", "does-not-exist")
