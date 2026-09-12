"""Tests for domain model validation rules and derived workflow status."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kb_orchestrator.domain.models import Step, Workflow

_NOW = datetime.now(UTC)


def _step(id: str, *, depends_on: list[str] | None = None, status: str = "pending") -> Step:
    return Step(
        id=id,
        name=f"step-{id}",
        message="do the thing",
        depends_on=depends_on or [],
        status=status,  # type: ignore[arg-type]
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_step_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        Step(id="1", name="", message="x", created_at=_NOW, updated_at=_NOW)


def test_step_rejects_blank_message() -> None:
    with pytest.raises(ValidationError):
        Step(id="1", name="x", message="", created_at=_NOW, updated_at=_NOW)


def test_step_rejects_self_dependency() -> None:
    with pytest.raises(ValidationError):
        _step("a", depends_on=["a"])


def test_step_defaults_to_pending_with_no_result_or_error() -> None:
    step = _step("a")
    assert step.status == "pending"
    assert step.result is None
    assert step.error is None


def test_step_is_frozen() -> None:
    step = _step("a")
    with pytest.raises(ValidationError):
        step.status = "running"


def test_workflow_rejects_dependency_on_unknown_step() -> None:
    with pytest.raises(ValidationError):
        Workflow(
            id="w1",
            name="wf",
            steps=[_step("a", depends_on=["does-not-exist"])],
            created_at=_NOW,
            updated_at=_NOW,
        )


def test_workflow_accepts_a_real_dependency_chain() -> None:
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[_step("a"), _step("b", depends_on=["a"])],
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert [s.id for s in workflow.steps] == ["a", "b"]


# --- derived Workflow.status ----------------------------------------------


def test_status_is_pending_with_no_steps() -> None:
    workflow = Workflow(id="w1", name="wf", steps=[], created_at=_NOW, updated_at=_NOW)
    assert workflow.status == "pending"


def test_status_is_pending_when_all_steps_pending() -> None:
    workflow = Workflow(
        id="w1", name="wf", steps=[_step("a"), _step("b")], created_at=_NOW, updated_at=_NOW
    )
    assert workflow.status == "pending"


def test_status_is_running_when_any_step_is_running() -> None:
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[_step("a", status="running"), _step("b")],
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert workflow.status == "running"


def test_status_is_running_when_some_succeeded_and_some_pending() -> None:
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[_step("a", status="succeeded"), _step("b")],
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert workflow.status == "running"


def test_status_is_succeeded_only_when_every_step_succeeded() -> None:
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[_step("a", status="succeeded"), _step("b", status="succeeded")],
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert workflow.status == "succeeded"


def test_status_is_failed_if_any_step_failed_even_alongside_successes() -> None:
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[_step("a", status="succeeded"), _step("b", status="failed")],
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert workflow.status == "failed"


def test_status_is_waiting_for_approval_and_outranks_running() -> None:
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[_step("a", status="running"), _step("b", status="waiting_for_approval")],
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert workflow.status == "waiting_for_approval"


def test_status_failed_outranks_waiting_for_approval() -> None:
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[_step("a", status="waiting_for_approval"), _step("b", status="failed")],
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert workflow.status == "failed"
