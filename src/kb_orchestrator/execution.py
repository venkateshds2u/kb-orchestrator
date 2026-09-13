"""Step execution: running one `Step` against an `AgentClient`.

`execute_step` is deliberately pure with respect to persistence -- it
takes a `Step`, returns an updated `Step`, and touches no repository. Step
6's execution engine (iterating a whole workflow, persisting each
transition) is what actually calls this and decides when a step is
eligible to run; this function doesn't check `step.status` or
`depends_on` itself; that's the caller's job, not something to re-litigate
here.
"""

from datetime import UTC, datetime

from kb_orchestrator.agent_client import AgentCallError, AgentClient
from kb_orchestrator.domain.models import Step


async def execute_step(client: AgentClient, step: Step) -> Step:
    """Send `step.message` to kb-agent and return a new `Step` reflecting
    the outcome -- `step` itself is untouched (frozen, Step 3).

    Two distinct ways this can end in `failed`, both reported the same
    way on the returned `Step` (a workflow doesn't need to distinguish
    them, though `error`'s text still names which one happened):
    `AgentCallError` (the HTTP call itself broke) and a `execution_failure`
    in an otherwise-successful response (kb-agent's own tool call broke).
    """
    now = datetime.now(UTC)
    try:
        result = await client.send_message(step.message)
    except AgentCallError as exc:
        return step.model_copy(update={"status": "failed", "error": str(exc), "updated_at": now})

    if result.execution_failure is not None:
        return step.model_copy(
            update={"status": "failed", "error": result.execution_failure.error, "updated_at": now}
        )

    return step.model_copy(update={"status": "succeeded", "result": result.text, "updated_at": now})
