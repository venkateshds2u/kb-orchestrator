"""Business logic for workflows: ID/timestamp assignment and translating
"no such row" into a named domain error.

No SQL lives here -- a `WorkflowRepository` is injected in, not
constructed here, so this class is exercised directly in tests (a fake
repository stands in fine, same as Project 1's `NoteService` tests) with
no real database involved.
"""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from kb_orchestrator.db.repository import WorkflowRepository
from kb_orchestrator.domain.errors import WorkflowNotFoundError
from kb_orchestrator.domain.models import Step, Workflow


class StepSpec(BaseModel):
    """What a caller decides when defining a new step -- everything else
    (`status`, `result`, `error`, timestamps) is generated or defaulted,
    the same "caller supplies input, service fills in the rest" split as
    Project 1's `CreateNoteInput`."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    message: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class WorkflowService:
    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    async def create_workflow(self, name: str, step_specs: list[StepSpec]) -> Workflow:
        now = datetime.now(UTC)
        steps = [
            Step(
                id=spec.id,
                name=spec.name,
                message=spec.message,
                depends_on=spec.depends_on,
                created_at=now,
                updated_at=now,
            )
            for spec in step_specs
        ]
        # Constructing Workflow here (before persisting) means a duplicate
        # step id or a dependency on an unknown sibling fails via Step 3's
        # own validators -- loudly, before anything ever reaches SQL.
        workflow = Workflow(id=str(uuid4()), name=name, steps=steps, created_at=now, updated_at=now)
        await self._repository.create_workflow(workflow)
        return workflow

    async def get_workflow(self, workflow_id: str) -> Workflow:
        workflow = await self._repository.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(workflow_id)
        return workflow
