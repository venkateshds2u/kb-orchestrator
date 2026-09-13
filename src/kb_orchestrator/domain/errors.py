"""Domain-level errors.

Plain exceptions, not pydantic models -- these represent control flow (an
expected business outcome like "no such workflow"), not data crossing a
boundary.
"""


class WorkflowServiceError(Exception):
    """Base class for all service-layer errors."""


class WorkflowNotFoundError(WorkflowServiceError):
    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        super().__init__(f"Workflow not found: {workflow_id}")


class StepNotFoundError(WorkflowServiceError):
    def __init__(self, workflow_id: str, step_id: str) -> None:
        self.workflow_id = workflow_id
        self.step_id = step_id
        super().__init__(f"Step {step_id!r} not found in workflow {workflow_id!r}")


class StepNotAwaitingApprovalError(WorkflowServiceError):
    """Raised by `approve_step`/`reject_step` (Step 9) when the named step
    isn't actually `waiting_for_approval` -- e.g. it's still `pending`, or
    someone already acted on it."""

    def __init__(self, workflow_id: str, step_id: str, actual_status: str) -> None:
        self.workflow_id = workflow_id
        self.step_id = step_id
        self.actual_status = actual_status
        super().__init__(
            f"Step {step_id!r} in workflow {workflow_id!r} is not awaiting approval "
            f"(status: {actual_status!r})"
        )
