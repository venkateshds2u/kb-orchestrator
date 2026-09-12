"""Tests for WorkflowRepository."""

from datetime import UTC, datetime

import aiosqlite

from kb_orchestrator.db.repository import WorkflowRepository
from kb_orchestrator.domain.models import Step, Workflow

_NOW = datetime.now(UTC)


def _step(id: str, *, depends_on: list[str] | None = None) -> Step:
    return Step(
        id=id,
        name=f"step-{id}",
        message="do the thing",
        depends_on=depends_on or [],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _workflow(workflow_id: str = "w1", *, steps: list[Step] | None = None) -> Workflow:
    return Workflow(
        id=workflow_id,
        name="test workflow",
        steps=steps if steps is not None else [_step("a"), _step("b", depends_on=["a"])],
        created_at=_NOW,
        updated_at=_NOW,
    )


async def test_create_and_get_round_trip(db_connection: aiosqlite.Connection) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _workflow()

    await repo.create_workflow(workflow)
    fetched = await repo.get_workflow(workflow.id)

    assert fetched == workflow


async def test_get_missing_returns_none(db_connection: aiosqlite.Connection) -> None:
    repo = WorkflowRepository(db_connection)
    assert await repo.get_workflow("does-not-exist") is None


async def test_depends_on_round_trips_through_json_encoding(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _workflow(
        steps=[_step("a"), _step("b", depends_on=["a"]), _step("c", depends_on=["a", "b"])]
    )
    await repo.create_workflow(workflow)

    fetched = await repo.get_workflow(workflow.id)

    assert fetched is not None
    assert {s.id: s.depends_on for s in fetched.steps} == {"a": [], "b": ["a"], "c": ["a", "b"]}


async def test_step_order_is_preserved(db_connection: aiosqlite.Connection) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _workflow(steps=[_step("c"), _step("a"), _step("b")])
    await repo.create_workflow(workflow)

    fetched = await repo.get_workflow(workflow.id)

    assert fetched is not None
    assert [s.id for s in fetched.steps] == ["c", "a", "b"]


async def test_update_step_changes_status_and_result(db_connection: aiosqlite.Connection) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _workflow()
    await repo.create_workflow(workflow)
    new_updated_at = datetime.now(UTC)

    updated = await repo.update_step(
        workflow.id, "a", status="succeeded", result="all done", updated_at=new_updated_at
    )

    assert updated is True
    fetched = await repo.get_workflow(workflow.id)
    assert fetched is not None
    step_a = next(s for s in fetched.steps if s.id == "a")
    assert step_a.status == "succeeded"
    assert step_a.result == "all done"
    assert step_a.error is None


async def test_update_step_records_error(db_connection: aiosqlite.Connection) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _workflow()
    await repo.create_workflow(workflow)

    await repo.update_step(
        workflow.id, "a", status="failed", error="boom", updated_at=datetime.now(UTC)
    )

    fetched = await repo.get_workflow(workflow.id)
    assert fetched is not None
    step_a = next(s for s in fetched.steps if s.id == "a")
    assert step_a.status == "failed"
    assert step_a.error == "boom"


async def test_update_step_missing_returns_false(db_connection: aiosqlite.Connection) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _workflow()
    await repo.create_workflow(workflow)

    updated = await repo.update_step(
        workflow.id, "does-not-exist", status="succeeded", updated_at=datetime.now(UTC)
    )

    assert updated is False


async def test_reconstructed_workflow_status_reflects_stored_step_statuses(
    db_connection: aiosqlite.Connection,
) -> None:
    """Proves the round trip isn't just "the bytes come back the same" --
    Workflow.status (a derived property, Step 3) correctly reflects
    updated step state after a real update + re-read."""
    repo = WorkflowRepository(db_connection)
    workflow = _workflow()
    await repo.create_workflow(workflow)
    assert workflow.status == "pending"

    await repo.update_step(workflow.id, "a", status="succeeded", updated_at=datetime.now(UTC))
    await repo.update_step(workflow.id, "b", status="succeeded", updated_at=datetime.now(UTC))

    fetched = await repo.get_workflow(workflow.id)
    assert fetched is not None
    assert fetched.status == "succeeded"


async def test_steps_from_different_workflows_can_share_an_id(
    db_connection: aiosqlite.Connection,
) -> None:
    """Step ids are scoped per-workflow (composite primary key) -- two
    different workflows both having a step id "a" must not collide."""
    repo = WorkflowRepository(db_connection)
    await repo.create_workflow(_workflow("w1"))
    await repo.create_workflow(_workflow("w2"))

    first = await repo.get_workflow("w1")
    second = await repo.get_workflow("w2")

    assert first is not None
    assert second is not None
    assert {s.id for s in first.steps} == {"a", "b"}
    assert {s.id for s in second.steps} == {"a", "b"}
