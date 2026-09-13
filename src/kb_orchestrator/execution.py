"""Step execution: running one `Step` (Step 5) or a whole `Workflow`
(Step 6) against an `AgentClient`.

`execute_step` is deliberately pure with respect to persistence -- it
takes a `Step`, returns an updated `Step`, and touches no repository.
`run_workflow` is what actually decides which steps are eligible to run,
in what order, and persists each transition -- `execute_step` doesn't
check `step.status`/`depends_on` itself; that's this module's other half's
job, not something to re-litigate at the single-step layer.
"""

import asyncio
from datetime import UTC, datetime

from kb_orchestrator.agent_client import AgentCallError, AgentClient
from kb_orchestrator.config import Settings
from kb_orchestrator.db.repository import WorkflowRepository
from kb_orchestrator.domain.models import Step, Workflow


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


class DependencyCycleError(Exception):
    """No valid execution order exists -- some steps depend on each other
    in a cycle. Step 3 only ever checked for self-dependency and
    references to real steps; a longer cycle (A depends on B depends on
    A) slips past both those checks, which is exactly why that gap was
    flagged there as this layer's job to close, not left unaddressed."""


def topological_order(steps: list[Step]) -> list[Step]:
    """Order `steps` so every step appears after everything it
    `depends_on` -- Kahn's algorithm. Ties (steps that become eligible at
    the same time) resolve in the original list order, so a plain linear
    chain or an already-ordered list comes back unchanged.
    """
    by_id = {step.id: step for step in steps}
    remaining_deps = {step.id: len(step.depends_on) for step in steps}
    dependents: dict[str, list[str]] = {step.id: [] for step in steps}
    for step in steps:
        for dep_id in step.depends_on:
            dependents[dep_id].append(step.id)

    ready = [step.id for step in steps if remaining_deps[step.id] == 0]
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for dependent_id in dependents[current]:
            remaining_deps[dependent_id] -= 1
            if remaining_deps[dependent_id] == 0:
                ready.append(dependent_id)

    if len(order) != len(steps):
        cyclic = sorted(set(by_id) - set(order))
        raise DependencyCycleError(f"cycle detected among step(s): {cyclic}")

    return [by_id[step_id] for step_id in order]


def _compose_message(step: Step, completed: dict[str, Step]) -> str:
    """Build the message actually sent to kb-agent for `step`: its own
    `message`, prefixed with every completed dependency's result.

    Not a template language (no `{{placeholder}}` syntax) -- every
    dependency's result is simply prepended, in `depends_on` order,
    ahead of the step's own task text. Deliberately the simplest thing
    that lets a later step build on an earlier one's answer: nothing yet
    needs a dependency's result spliced into the *middle* of a step's own
    message, so there's no reason to build a parser for that.
    """
    if not step.depends_on:
        return step.message

    context = "\n\n".join(
        f"[{completed[dep_id].name}]: {completed[dep_id].result or '(no result)'}"
        for dep_id in step.depends_on
    )
    return f"Context from previous steps:\n{context}\n\nTask: {step.message}"


async def _run_ready_step(
    client: AgentClient,
    repository: WorkflowRepository,
    workflow_id: str,
    step: Step,
    completed: dict[str, Step],
    settings: Settings,
) -> Step:
    """Run one step that's already known to be ready (all its
    dependencies are in `completed`), retrying on failure up to
    `settings.step_max_attempts` times with exponential backoff before
    persisting a final `failed` outcome.

    Both of `execute_step`'s failure categories (`AgentCallError` and
    kb-agent's own `execution_failure`) are retried the same way here --
    Step 5 kept them as genuinely distinct exception/data shapes
    specifically so a future refinement (e.g. don't retry a considered,
    deterministic `execution_failure`) could treat them differently
    without restructuring anything; nothing yet demands that distinction,
    so retrying both uniformly is the correct amount of behavior for now.
    """
    message = _compose_message(step, completed)
    attempt_step = step.model_copy(update={"message": message})

    result_step = step
    attempt = 0
    for attempt_number in range(1, settings.step_max_attempts + 1):
        if attempt_number > 1:
            backoff = settings.step_retry_backoff_seconds * (2 ** (attempt_number - 2))
            await asyncio.sleep(backoff)

        attempt = await repository.increment_attempt(workflow_id, step.id)
        await repository.update_step(
            workflow_id, step.id, status="running", updated_at=datetime.now(UTC)
        )

        result_step = await execute_step(client, attempt_step)
        if result_step.status == "succeeded":
            break

    final_step = result_step.model_copy(update={"attempt": attempt})
    await repository.update_step(
        workflow_id,
        step.id,
        status=final_step.status,
        result=final_step.result,
        error=final_step.error,
        updated_at=final_step.updated_at,
    )
    return final_step


async def run_workflow(
    client: AgentClient, repository: WorkflowRepository, workflow: Workflow, settings: Settings
) -> Workflow:
    """Execute `workflow` wave by wave: every step whose dependencies are
    already satisfied runs *concurrently* with its wave-mates, persisting
    each transition via `repository` as it goes, so progress survives a
    crash mid-run (the reason Step 4's persistence exists at all). A
    linear chain (Step 6's own scope) is just the special case where every
    wave happens to contain exactly one step -- this is a strict
    generalization of Step 6's runner, not a parallel alternative to it.

    Resumable: a step already `succeeded` (from a prior, interrupted run
    of this same workflow) is skipped, its existing result reused as
    context for whatever depends on it, rather than re-run. A workflow
    already `failed` is returned untouched -- Step 8's retries are
    *within* one attempt at a step (immediate backoff-and-retry for a
    transient failure, inside `_run_ready_step`), not a mechanism for
    re-running a workflow that already exhausted those retries and
    reached a considered `failed` state; re-attempting a permanently
    failed step is a coarser-grained decision this function doesn't make
    on its own.

    A step failing blocks only *its own* downstream dependents (they can
    never satisfy "all dependencies completed", so they stay `pending`
    forever) -- not unrelated, independent branches, which keep running to
    completion. This is the behavior Step 6 explicitly named as needing
    revisiting once real parallel branches existed: stopping *everything*
    because one independent branch failed would be too broad now that
    "everything" isn't necessarily one chain.
    """
    if workflow.status == "failed":
        return workflow

    # Called only for its cycle-detection side effect -- the wave loop
    # below computes its own execution order from readiness directly, but
    # a cyclic workflow definition should fail loudly right here, not
    # silently sit as "pending forever" once no wave ever becomes ready.
    topological_order(workflow.steps)

    completed: dict[str, Step] = {s.id: s for s in workflow.steps if s.status == "succeeded"}
    failed_ids: set[str] = set()
    remaining = [s for s in workflow.steps if s.id not in completed]

    while remaining:
        # Ready now: every dependency already succeeded, and none failed
        # (a failed dependency means this step can never become ready --
        # not "not yet", but "not ever" -- so it's left out of every
        # future wave rather than retried against a stale `completed`).
        ready = [
            step
            for step in remaining
            if all(dep in completed for dep in step.depends_on)
            and not any(dep in failed_ids for dep in step.depends_on)
        ]
        if not ready:
            break  # everything left is permanently blocked by a failure

        results = await asyncio.gather(
            *(
                _run_ready_step(client, repository, workflow.id, step, completed, settings)
                for step in ready
            )
        )
        for result_step in results:
            if result_step.status == "succeeded":
                completed[result_step.id] = result_step
            else:
                failed_ids.add(result_step.id)

        ready_ids = {step.id for step in ready}
        remaining = [step for step in remaining if step.id not in ready_ids]

    refreshed = await repository.get_workflow(workflow.id)
    assert refreshed is not None, "workflow was read at the top of this call; it cannot vanish"
    return refreshed
