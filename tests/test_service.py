"""Tests for WorkflowService: business rules layered on top of the
repository."""

import aiosqlite
import pytest

from kb_orchestrator.db.repository import WorkflowRepository
from kb_orchestrator.domain.errors import WorkflowNotFoundError
from kb_orchestrator.domain.service import StepSpec, WorkflowService


def _service(connection: aiosqlite.Connection) -> WorkflowService:
    return WorkflowService(WorkflowRepository(connection))


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
