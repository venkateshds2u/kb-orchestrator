"""Domain models: `Workflow` and `Step`.

Pure data plus structural invariants -- no execution logic (Step 6), no
persistence (Step 4). `id`/`created_at`/`updated_at` have no defaults,
same as Project 1's `Note`: generating them is the job of whatever code
actually constructs a workflow (a future workflow-builder/service layer),
not something to happen implicitly inside the model. `Workflow.status` is
the one exception -- a derived, read-only property, not a stored field:
storing it independently would let it drift out of sync with what its
steps actually say happened, the same "don't duplicate a truth that can
disagree with itself" reasoning behind Project 1's `Page.has_more`.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

StepStatus = Literal["pending", "running", "waiting_for_approval", "succeeded", "failed"]
WorkflowStatus = Literal["pending", "running", "waiting_for_approval", "succeeded", "failed"]


class Step(BaseModel):
    """One unit of work in a workflow: one message to send to kb-agent.

    `depends_on` lists the ids of steps that must complete before this one
    can run -- the same structure expresses both a sequential chain (each
    step depends on the one before it) and independent, parallel steps
    (empty `depends_on`), with no separate "step type" needed for either.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = Field(min_length=1)
    message: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = "pending"
    result: str | None = None
    error: str | None = None
    # How many times this step has actually been attempted so far (Step
    # 8) -- persisted (not just held in memory during one run_workflow
    # call) so it survives a crash mid-retry-loop, same reason every
    # other piece of step state is persisted. Defaulted to 0, unlike
    # id/created_at/updated_at: a fresh, never-attempted step genuinely
    # has no other sensible value, so there's no ambiguity a default
    # could paper over.
    attempt: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _not_self_dependent(self) -> Self:
        if self.id in self.depends_on:
            raise ValueError(f"step {self.id!r} cannot depend on itself")
        return self


class Workflow(BaseModel):
    """A named collection of steps, wired together by `Step.depends_on`."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = Field(min_length=1)
    steps: list[Step] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _step_ids_are_unique(self) -> Self:
        """Step ids are scoped to their workflow (Step 4's persistence
        schema keys steps on `(workflow_id, id)`, not a bare global `id`)
        -- a caller can reuse "research"/"draft" across many workflows, but
        not twice in the *same* one. Without this check, two same-id steps
        would silently collapse into one entry in the `known_ids` set the
        next validator builds, masking a real data problem instead of
        rejecting it -- a gap found while designing Step 4's schema, fixed
        here rather than left as a trap for whatever persists this later.
        """
        ids = [step.id for step in self.steps]
        duplicates = sorted({step_id for step_id in ids if ids.count(step_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate step id(s) within workflow: {duplicates}")
        return self

    @model_validator(mode="after")
    def _dependencies_reference_real_steps(self) -> Self:
        known_ids = {step.id for step in self.steps}
        for step in self.steps:
            unknown = sorted(set(step.depends_on) - known_ids)
            if unknown:
                raise ValueError(f"step {step.id!r} depends on unknown step id(s): {unknown}")
        return self

    @property
    def status(self) -> WorkflowStatus:
        return _compute_status(self.steps)


def _compute_status(steps: list[Step]) -> WorkflowStatus:
    """Derive a workflow's overall status from its steps' statuses.

    Precedence, most-urgent first: any failure means the workflow failed,
    even if other steps are still pending or succeeded -- a partial
    success alongside a failure is still a failed workflow. Any step
    waiting on a human outranks "running" -- the workflow isn't actively
    making progress on its own right now. Only when every step has
    succeeded is the whole workflow done.
    """
    if not steps:
        return "pending"
    if any(step.status == "failed" for step in steps):
        return "failed"
    if any(step.status == "waiting_for_approval" for step in steps):
        return "waiting_for_approval"
    if all(step.status == "succeeded" for step in steps):
        return "succeeded"
    if any(step.status in ("running", "succeeded") for step in steps):
        return "running"
    return "pending"
