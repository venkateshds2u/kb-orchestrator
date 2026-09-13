"""Tests for execute_step: pure logic, no HTTP at all.

Uses a fake `AgentClient` (structurally satisfying the `Protocol`, no
inheritance needed -- the same interface-segregation payoff as Project
2's `ToolProvider`), not `HttpAgentClient` -- what's under test here is
"does execute_step react correctly to a given result," not "does the
HTTP client work" (that's test_agent_client.py's job).
"""

from datetime import UTC, datetime

from kb_orchestrator.agent_client import AgentCallError, AgentChatResult, AgentExecutionFailure
from kb_orchestrator.domain.models import Step
from kb_orchestrator.execution import execute_step

_NOW = datetime.now(UTC)


def _step() -> Step:
    return Step(id="a", name="Research", message="find stuff", created_at=_NOW, updated_at=_NOW)


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
