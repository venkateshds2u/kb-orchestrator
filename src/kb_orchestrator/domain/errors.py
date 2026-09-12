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
