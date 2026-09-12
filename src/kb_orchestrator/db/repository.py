"""SQLite repository for workflows and steps.

Works directly with the domain `Workflow`/`Step` models (Step 3) rather
than a parallel "Record" type mirroring each row -- contrast Project 1's
`NoteRecord`/`Note` split, justified there by the *chance* persistence and
domain shape might one day diverge. That divergence hasn't happened here,
and there's nothing to duplicate a whole second type hierarchy against --
introducing one now, on spec, would be exactly the kind of speculative
generality this curriculum's own stated principles rule out. The one
genuine storage-specific detail (`depends_on` has no native SQLite array
type) is handled by small, private row<->model helpers kept right here,
since this module is still "the only place that knows SQL."
"""

import json
from datetime import datetime

import aiosqlite

from kb_orchestrator.domain.models import Step, StepStatus, Workflow


def _step_to_row(workflow_id: str, step: Step, *, ordinal: int) -> dict[str, object]:
    return {
        "workflow_id": workflow_id,
        "id": step.id,
        "name": step.name,
        "message": step.message,
        "depends_on": json.dumps(step.depends_on),
        "status": step.status,
        "result": step.result,
        "error": step.error,
        "ordinal": ordinal,
        "created_at": step.created_at.isoformat(),
        "updated_at": step.updated_at.isoformat(),
    }


def _row_to_step(row: aiosqlite.Row) -> Step:
    return Step(
        id=row["id"],
        name=row["name"],
        message=row["message"],
        depends_on=json.loads(row["depends_on"]),
        status=row["status"],
        result=row["result"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class WorkflowRepository:
    """Persistence operations for workflows and their steps."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def create_workflow(self, workflow: Workflow) -> None:
        """Persist a workflow and every one of its steps in one call.

        Not split into "create workflow" + "add step" calls: a workflow
        with a subset of its steps missing (e.g. the process crashing
        between two separate calls) isn't a state anything downstream
        should ever have to reason about, so it's not a state this method
        allows getting into.
        """
        await self._connection.execute(
            "INSERT INTO workflows (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (
                workflow.id,
                workflow.name,
                workflow.created_at.isoformat(),
                workflow.updated_at.isoformat(),
            ),
        )
        for ordinal, step in enumerate(workflow.steps):
            await self._connection.execute(
                """
                INSERT INTO steps
                    (workflow_id, id, name, message, depends_on, status, result, error,
                     ordinal, created_at, updated_at)
                VALUES
                    (:workflow_id, :id, :name, :message, :depends_on, :status, :result, :error,
                     :ordinal, :created_at, :updated_at)
                """,
                _step_to_row(workflow.id, step, ordinal=ordinal),
            )
        await self._connection.commit()

    async def get_workflow(self, workflow_id: str) -> Workflow | None:
        """Read a workflow back, including all its steps, reconstructed as
        a real domain `Workflow` -- re-running its structural validators
        (Step 3) on every read, so corrupted or hand-edited data fails
        loudly here rather than being trusted silently downstream."""
        cursor = await self._connection.execute(
            "SELECT id, name, created_at, updated_at FROM workflows WHERE id = ?",
            (workflow_id,),
        )
        workflow_row = await cursor.fetchone()
        if workflow_row is None:
            return None

        steps_cursor = await self._connection.execute(
            "SELECT * FROM steps WHERE workflow_id = ? ORDER BY ordinal", (workflow_id,)
        )
        step_rows = await steps_cursor.fetchall()

        return Workflow(
            id=workflow_row["id"],
            name=workflow_row["name"],
            steps=[_row_to_step(row) for row in step_rows],
            created_at=workflow_row["created_at"],
            updated_at=workflow_row["updated_at"],
        )

    async def update_step(
        self,
        workflow_id: str,
        step_id: str,
        *,
        status: StepStatus,
        result: str | None = None,
        error: str | None = None,
        updated_at: datetime,
    ) -> bool:
        """Update one step's status/result/error. Returns whether a row
        actually matched -- callers decide what a no-op update means
        (Step 6's execution engine; a missing step there is a bug in the
        engine, not something this layer should have an opinion on)."""
        cursor = await self._connection.execute(
            """
            UPDATE steps SET status = ?, result = ?, error = ?, updated_at = ?
            WHERE workflow_id = ? AND id = ?
            """,
            (status, result, error, updated_at.isoformat(), workflow_id, step_id),
        )
        await self._connection.commit()
        return cursor.rowcount > 0
