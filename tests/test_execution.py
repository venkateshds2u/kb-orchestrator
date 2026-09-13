"""Tests for execute_step/run_workflow: pure logic plus a real (in-memory)
repository -- no HTTP at all.

Uses a fake `AgentClient` (structurally satisfying the `Protocol`, no
inheritance needed -- the same interface-segregation payoff as Project
2's `ToolProvider`), not `HttpAgentClient` -- what's under test here is
"does execution react correctly to a given result," not "does the HTTP
client work" (that's test_agent_client.py's job). `run_workflow`'s own
tests use a real `WorkflowRepository` backed by the in-memory
`db_connection` fixture, not a fake -- same "mock only the boundary that
actually needs it" pattern as Steps 4/5's own tests.
"""

from datetime import UTC, datetime

import aiosqlite
import pytest

from kb_orchestrator.agent_client import AgentCallError, AgentChatResult, AgentExecutionFailure
from kb_orchestrator.db.repository import WorkflowRepository
from kb_orchestrator.domain.models import Step, Workflow
from kb_orchestrator.execution import (
    DependencyCycleError,
    execute_step,
    run_workflow,
    topological_order,
)

_NOW = datetime.now(UTC)


def _step(
    id: str = "a",
    *,
    name: str | None = None,
    depends_on: list[str] | None = None,
    status: str = "pending",
    result: str | None = None,
) -> Step:
    return Step(
        id=id,
        name=name or f"step-{id}",
        message=f"do {id}",
        depends_on=depends_on or [],
        status=status,  # type: ignore[arg-type]
        result=result,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _result(
    *,
    text: str | None = "here's what I found",
    execution_failure: AgentExecutionFailure | None = None,
    hit_iteration_limit: bool = False,
) -> AgentChatResult:
    return AgentChatResult(
        text=text,
        tool_calls=[],
        iterations=1,
        stop_reason="end_turn",
        hit_iteration_limit=hit_iteration_limit,
        hit_token_budget=False,
        execution_failure=execution_failure,
        input_tokens=10,
        output_tokens=5,
    )


class _FakeClient:
    def __init__(self, result: AgentChatResult | Exception) -> None:
        self._result = result

    async def send_message(self, message: str) -> AgentChatResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_successful_call_marks_step_succeeded_with_result() -> None:
    step = await execute_step(_FakeClient(_result(text="the answer")), _step())

    assert step.status == "succeeded"
    assert step.result == "the answer"
    assert step.error is None


async def test_original_step_is_not_mutated() -> None:
    original = _step()
    await execute_step(_FakeClient(_result()), original)

    assert original.status == "pending"
    assert original.result is None


async def test_updated_at_advances_past_the_original() -> None:
    original = _step()
    step = await execute_step(_FakeClient(_result()), original)

    assert step.updated_at > original.updated_at


async def test_agent_call_error_marks_step_failed() -> None:
    step = await execute_step(_FakeClient(AgentCallError("connection refused")), _step())

    assert step.status == "failed"
    assert step.error == "connection refused"
    assert step.result is None


async def test_kb_agent_execution_failure_marks_step_failed() -> None:
    failure = AgentExecutionFailure(tool_name="search_notes", arguments={}, error="tool broke")
    step = await execute_step(_FakeClient(_result(execution_failure=failure)), _step())

    assert step.status == "failed"
    assert step.error == "tool broke"


async def test_graceful_wrap_up_with_real_text_still_succeeds() -> None:
    """kb-agent hitting its own iteration/token limit but still returning
    a real answer (its own Step 8's graceful stop) counts as this step
    succeeding -- a workflow step got a usable answer, just possibly a
    less complete one. Distinguishing "succeeded but limited" from a full
    success isn't this step's scope (nothing downstream reads that
    distinction yet)."""
    step = await execute_step(
        _FakeClient(_result(text="partial answer", hit_iteration_limit=True)), _step()
    )

    assert step.status == "succeeded"
    assert step.result == "partial answer"


# --- topological_order (Step 6) -------------------------------------------


def test_topological_order_returns_a_linear_chain_in_dependency_order() -> None:
    a, b, c = _step("a"), _step("b", depends_on=["a"]), _step("c", depends_on=["b"])

    assert [s.id for s in topological_order([c, a, b])] == ["a", "b", "c"]


def test_topological_order_handles_diamond_dependencies() -> None:
    a = _step("a")
    b = _step("b", depends_on=["a"])
    c = _step("c", depends_on=["a"])
    d = _step("d", depends_on=["b", "c"])

    order = [s.id for s in topological_order([d, c, b, a])]

    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_topological_order_preserves_original_order_among_independent_steps() -> None:
    assert [s.id for s in topological_order([_step("b"), _step("a")])] == ["b", "a"]


def test_topological_order_raises_on_a_cycle() -> None:
    """Step 3 rejects self-dependency and references to unknown steps, but
    a longer cycle (a depends on b, b depends on a) satisfies both those
    checks -- exactly the gap flagged there as this layer's job to close."""
    a = _step("a", depends_on=["b"])
    b = _step("b", depends_on=["a"])

    with pytest.raises(DependencyCycleError):
        topological_order([a, b])


# --- run_workflow (Step 6) -------------------------------------------------


class _ScriptedClient:
    """Returns one scripted result per call, in the order given; records
    each call's actual message for asserting on how context was composed."""

    def __init__(self, results: list[AgentChatResult | Exception]) -> None:
        self._results = list(results)
        self.messages: list[str] = []

    async def send_message(self, message: str) -> AgentChatResult:
        self.messages.append(message)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _chain_workflow(workflow_id: str = "w1") -> Workflow:
    return Workflow(
        id=workflow_id,
        name="research and draft",
        steps=[
            Step(
                id="research",
                name="Research",
                message="find stuff",
                created_at=_NOW,
                updated_at=_NOW,
            ),
            Step(
                id="draft",
                name="Draft",
                message="write it up",
                depends_on=["research"],
                created_at=_NOW,
                updated_at=_NOW,
            ),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


async def test_run_workflow_executes_steps_in_dependency_order(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _chain_workflow()
    await repo.create_workflow(workflow)
    client = _ScriptedClient([_result(text="research findings"), _result(text="draft text")])

    final = await run_workflow(client, repo, workflow)

    assert final.status == "succeeded"
    assert [s.status for s in final.steps] == ["succeeded", "succeeded"]
    assert final.steps[0].result == "research findings"
    assert final.steps[1].result == "draft text"


async def test_run_workflow_composes_a_dependencys_result_into_the_next_message(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _chain_workflow()
    await repo.create_workflow(workflow)
    client = _ScriptedClient([_result(text="research findings"), _result(text="draft text")])

    await run_workflow(client, repo, workflow)

    assert client.messages[0] == "find stuff"  # first step: no dependencies, message unchanged
    assert "research findings" in client.messages[1]
    assert "write it up" in client.messages[1]


async def test_run_workflow_stops_on_first_failure_leaving_later_steps_pending(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = WorkflowRepository(db_connection)
    workflow = _chain_workflow()
    await repo.create_workflow(workflow)
    client = _ScriptedClient([AgentCallError("boom")])

    final = await run_workflow(client, repo, workflow)

    assert final.status == "failed"
    assert final.steps[0].status == "failed"
    assert final.steps[0].error == "boom"
    assert final.steps[1].status == "pending"  # never attempted


async def test_run_workflow_resumes_skipping_already_succeeded_steps(
    db_connection: aiosqlite.Connection,
) -> None:
    workflow = Workflow(
        id="w1",
        name="research and draft",
        steps=[
            Step(
                id="research",
                name="Research",
                message="find stuff",
                status="succeeded",
                result="cached findings",
                created_at=_NOW,
                updated_at=_NOW,
            ),
            Step(
                id="draft",
                name="Draft",
                message="write it up",
                depends_on=["research"],
                created_at=_NOW,
                updated_at=_NOW,
            ),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )
    repo = WorkflowRepository(db_connection)
    await repo.create_workflow(workflow)
    client = _ScriptedClient([_result(text="draft text")])

    final = await run_workflow(client, repo, workflow)

    assert len(client.messages) == 1  # "research" was not re-run
    assert "cached findings" in client.messages[0]
    assert final.steps[1].result == "draft text"


async def test_run_workflow_on_an_already_failed_workflow_is_a_noop(
    db_connection: aiosqlite.Connection,
) -> None:
    workflow = Workflow(
        id="w1",
        name="wf",
        steps=[_step("a", status="failed")],
        created_at=_NOW,
        updated_at=_NOW,
    )
    repo = WorkflowRepository(db_connection)
    await repo.create_workflow(workflow)
    client = _ScriptedClient([])

    final = await run_workflow(client, repo, workflow)

    assert final == workflow
    assert client.messages == []
