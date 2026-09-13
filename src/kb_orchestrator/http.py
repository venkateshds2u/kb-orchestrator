"""HTTP API: create, run, inspect, and approve/reject workflows.

Two related endpoints instead of "create-and-run-in-one-call": `POST
/workflows` only creates a workflow (fast, no agent calls); `POST
/workflows/{id}/run` actually executes it, blocking until it finishes,
fails, or pauses on an approval gate. Calling `/run` again on a workflow
that previously paused is exactly how a client resumes it after
`approve_step`/`reject_step` -- no separate "resume" endpoint needed,
since `run_workflow` (Step 6) is already resumable by construction.

No background job queue: `/run` blocks for as long as the workflow takes
to reach a stopping point, which for a workflow with several sequential
steps could be many seconds. A real production version of this API would
likely make `/run` return immediately (202 Accepted) and drive execution
from a worker process, with a separate endpoint or webhook for the
result -- named here as the real alternative, not built, since this
project has no task-queue infrastructure and building one would be new
scope well beyond "expose the workflow engine over HTTP."

Wire models here are this app's own, not the domain `Workflow`/`Step`
reused directly: `Workflow.status` is a computed `@property` (Step 3),
and pydantic's `model_dump()` does not include plain properties --
confirmed directly (not assumed) before designing this module, since
serializing a domain `Workflow` straight to JSON would have silently
dropped the single field a client most needs.
"""

import hmac
from datetime import datetime

import httpx
import structlog
import uvicorn
from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from kb_orchestrator.agent_client import AgentClient, HttpAgentClient
from kb_orchestrator.config import Settings
from kb_orchestrator.db.connection import open_database
from kb_orchestrator.db.repository import WorkflowRepository
from kb_orchestrator.domain.errors import (
    StepNotAwaitingApprovalError,
    StepNotFoundError,
    WorkflowNotFoundError,
)
from kb_orchestrator.domain.models import Step, StepStatus, Workflow, WorkflowStatus
from kb_orchestrator.domain.service import StepSpec, WorkflowService
from kb_orchestrator.execution import DependencyCycleError
from kb_orchestrator.execution import run_workflow as execute_workflow

logger = structlog.get_logger(__name__)


# --- wire schema ------------------------------------------------------------


class StepSpecBody(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    message: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    requires_approval: bool = False

    def to_domain(self) -> StepSpec:
        return StepSpec(
            id=self.id,
            name=self.name,
            message=self.message,
            depends_on=self.depends_on,
            requires_approval=self.requires_approval,
        )


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1)
    steps: list[StepSpecBody] = Field(default_factory=list)


class RejectStepRequest(BaseModel):
    reason: str = Field(min_length=1)


class StepBody(BaseModel):
    id: str
    name: str
    message: str
    depends_on: list[str]
    status: StepStatus
    result: str | None
    error: str | None
    attempt: int
    requires_approval: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, step: Step) -> "StepBody":
        return cls(
            id=step.id,
            name=step.name,
            message=step.message,
            depends_on=step.depends_on,
            status=step.status,
            result=step.result,
            error=step.error,
            attempt=step.attempt,
            requires_approval=step.requires_approval,
            created_at=step.created_at,
            updated_at=step.updated_at,
        )


class WorkflowBody(BaseModel):
    id: str
    name: str
    status: WorkflowStatus
    steps: list[StepBody]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, workflow: Workflow) -> "WorkflowBody":
        return cls(
            id=workflow.id,
            name=workflow.name,
            status=workflow.status,
            steps=[StepBody.from_domain(step) for step in workflow.steps],
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
        )


def _workflow_response(workflow: Workflow, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        WorkflowBody.from_domain(workflow).model_dump(mode="json"), status_code=status_code
    )


# --- auth middleware (near-identical to kb-agent's/kb-mcp-server's; not ---
# shared -- three separate packages with no dependency between them) -------


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, token: str, exempt_paths: frozenset[str]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token
        self._exempt_paths = exempt_paths

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self._exempt_paths:
            return await call_next(request)

        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential, self._token):
            logger.warning("http_auth_rejected", path=request.url.path)
            return JSONResponse(
                {"error": "unauthorized", "detail": "missing or invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


# --- routes ------------------------------------------------------------


async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def create_workflow(request: Request) -> Response:
    try:
        body = CreateWorkflowRequest.model_validate(await request.json())
    except ValidationError as exc:
        return JSONResponse({"error": "invalid_request", "detail": str(exc)}, status_code=422)

    service: WorkflowService = request.app.state.service
    try:
        workflow = await service.create_workflow(body.name, [s.to_domain() for s in body.steps])
    except ValidationError as exc:
        # Step 3's own domain validators (duplicate step ids, a dependency
        # on an unknown sibling) surface here as a client error -- the
        # request described an invalid workflow, not a server problem.
        return JSONResponse({"error": "invalid_workflow", "detail": str(exc)}, status_code=422)
    return _workflow_response(workflow, status_code=201)


async def get_workflow(request: Request) -> Response:
    service: WorkflowService = request.app.state.service
    try:
        workflow = await service.get_workflow(request.path_params["workflow_id"])
    except WorkflowNotFoundError:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return _workflow_response(workflow)


async def run_workflow_route(request: Request) -> Response:
    service: WorkflowService = request.app.state.service
    workflow_id = request.path_params["workflow_id"]
    try:
        workflow = await service.get_workflow(workflow_id)
    except WorkflowNotFoundError:
        return JSONResponse({"error": "not_found"}, status_code=404)

    try:
        result = await execute_workflow(
            request.app.state.agent_client,
            request.app.state.repository,
            workflow,
            request.app.state.settings,
        )
    except DependencyCycleError as exc:
        # A malformed workflow definition, not a server problem -- Step
        # 3/4 deferred cycle detection to exactly this layer, so this is
        # the first point such a workflow can even be discovered.
        return JSONResponse({"error": "invalid_workflow", "detail": str(exc)}, status_code=400)
    return _workflow_response(result)


async def approve_step_route(request: Request) -> Response:
    service: WorkflowService = request.app.state.service
    try:
        workflow = await service.approve_step(
            request.path_params["workflow_id"], request.path_params["step_id"]
        )
    except (WorkflowNotFoundError, StepNotFoundError):
        return JSONResponse({"error": "not_found"}, status_code=404)
    except StepNotAwaitingApprovalError as exc:
        return JSONResponse({"error": "invalid_state", "detail": str(exc)}, status_code=409)
    return _workflow_response(workflow)


async def reject_step_route(request: Request) -> Response:
    try:
        body = RejectStepRequest.model_validate(await request.json())
    except ValidationError as exc:
        return JSONResponse({"error": "invalid_request", "detail": str(exc)}, status_code=422)

    service: WorkflowService = request.app.state.service
    try:
        workflow = await service.reject_step(
            request.path_params["workflow_id"],
            request.path_params["step_id"],
            reason=body.reason,
        )
    except (WorkflowNotFoundError, StepNotFoundError):
        return JSONResponse({"error": "not_found"}, status_code=404)
    except StepNotAwaitingApprovalError as exc:
        return JSONResponse({"error": "invalid_state", "detail": str(exc)}, status_code=409)
    return _workflow_response(workflow)


# --- app + runner -----------------------------------------------------------


def build_app(
    settings: Settings, agent_client: AgentClient, repository: WorkflowRepository
) -> Starlette:
    """Build the ASGI app around an already-connected `agent_client`/
    `repository`, rather than constructing them internally -- makes this
    testable with a real in-memory repository and a fake `AgentClient`
    (no real kb-agent, no real database file needed), the same "construct
    infrastructure outside, pass it in" split as kb-agent's own
    `build_app` (Project 2, Step 11).
    """
    app = Starlette(
        routes=[
            Route("/health", health_check),
            Route("/workflows", create_workflow, methods=["POST"]),
            Route("/workflows/{workflow_id}", get_workflow),
            Route("/workflows/{workflow_id}/run", run_workflow_route, methods=["POST"]),
            Route(
                "/workflows/{workflow_id}/steps/{step_id}/approve",
                approve_step_route,
                methods=["POST"],
            ),
            Route(
                "/workflows/{workflow_id}/steps/{step_id}/reject",
                reject_step_route,
                methods=["POST"],
            ),
        ]
    )
    app.state.settings = settings
    app.state.agent_client = agent_client
    app.state.repository = repository
    app.state.service = WorkflowService(repository)
    app.add_middleware(
        BearerAuthMiddleware,
        token=settings.http_auth_token.get_secret_value(),
        exempt_paths=frozenset({"/health"}),
    )
    return app


async def run_http(settings: Settings) -> None:
    """Build the app around a real database connection and a real
    `HttpAgentClient`, and serve it with uvicorn."""
    async with open_database(settings) as connection:
        repository = WorkflowRepository(connection)
        async with httpx.AsyncClient(base_url=str(settings.kb_agent_base_url)) as http_client:
            agent_client = HttpAgentClient(
                http_client, auth_token=settings.kb_agent_auth_token.get_secret_value()
            )
            app = build_app(settings, agent_client, repository)
            config = uvicorn.Config(
                app, host=settings.http_host, port=settings.http_port, log_config=None
            )
            server = uvicorn.Server(config)
            await server.serve()
