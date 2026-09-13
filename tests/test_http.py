"""Tests for the HTTP API (Step 11): auth, request validation, and the
create -> run -> (approve) -> run-again lifecycle.

The app is exercised in-process via `httpx.ASGITransport`, against a real
in-memory `WorkflowRepository` (the `db_connection` fixture) and a fake
`AgentClient` -- no real kb-agent, no real database file, matching the
same "mock only the model/agent decisions, keep everything else real"
pattern used throughout this project and Project 2's own `test_http.py`.
"""

import aiosqlite
import httpx
import pytest
from pydantic import HttpUrl, SecretStr

from kb_orchestrator.agent_client import AgentChatResult
from kb_orchestrator.config import Settings
from kb_orchestrator.db.repository import WorkflowRepository
from kb_orchestrator.http import build_app

AUTH_TOKEN = "test-secret-token"  # noqa: S105 - test fixture, not a real credential


class _ScriptedClient:
    def __init__(self, results: list[AgentChatResult]) -> None:
        self._results = list(results)
        self.messages: list[str] = []

    async def send_message(self, message: str) -> AgentChatResult:
        self.messages.append(message)
        return self._results.pop(0)


def _result(text: str = "ok") -> AgentChatResult:
    return AgentChatResult(
        text=text,
        tool_calls=[],
        iterations=1,
        stop_reason="end_turn",
        hit_iteration_limit=False,
        hit_token_budget=False,
        execution_failure=None,
        input_tokens=1,
        output_tokens=1,
    )


def _settings() -> Settings:
    return Settings(
        kb_agent_base_url=HttpUrl("http://127.0.0.1:8000"),
        kb_agent_auth_token=SecretStr("agent-token"),
        http_auth_token=SecretStr(AUTH_TOKEN),
        step_retry_backoff_seconds=0.001,
    )


async def _client(
    db_connection: aiosqlite.Connection, results: list[AgentChatResult]
) -> httpx.AsyncClient:
    repo = WorkflowRepository(db_connection)
    app = build_app(_settings(), _ScriptedClient(results), repo)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


async def test_health_check_requires_no_auth(db_connection: aiosqlite.Connection) -> None:
    async with await _client(db_connection, []) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_create_workflow_without_auth_is_rejected(
    db_connection: aiosqlite.Connection,
) -> None:
    async with await _client(db_connection, []) as client:
        response = await client.post("/workflows", json={"name": "wf", "steps": []})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_create_workflow_returns_201_with_pending_status(
    db_connection: aiosqlite.Connection,
) -> None:
    async with await _client(db_connection, []) as client:
        response = await client.post(
            "/workflows",
            json={
                "name": "research and draft",
                "steps": [{"id": "a", "name": "A", "message": "find stuff"}],
            },
            headers=_auth_headers(),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["steps"][0]["id"] == "a"
    assert body["steps"][0]["status"] == "pending"


async def test_create_workflow_with_invalid_body_returns_422(
    db_connection: aiosqlite.Connection,
) -> None:
    async with await _client(db_connection, []) as client:
        response = await client.post("/workflows", json={}, headers=_auth_headers())

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


async def test_create_workflow_with_duplicate_step_ids_returns_422(
    db_connection: aiosqlite.Connection,
) -> None:
    async with await _client(db_connection, []) as client:
        response = await client.post(
            "/workflows",
            json={
                "name": "wf",
                "steps": [
                    {"id": "a", "name": "A", "message": "x"},
                    {"id": "a", "name": "A again", "message": "y"},
                ],
            },
            headers=_auth_headers(),
        )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_workflow"


async def test_get_workflow_returns_404_for_unknown_id(
    db_connection: aiosqlite.Connection,
) -> None:
    async with await _client(db_connection, []) as client:
        response = await client.get("/workflows/does-not-exist", headers=_auth_headers())

    assert response.status_code == 404


async def test_run_workflow_endpoint_executes_and_returns_succeeded(
    db_connection: aiosqlite.Connection,
) -> None:
    async with await _client(db_connection, [_result("done")]) as client:
        created = await client.post(
            "/workflows",
            json={"name": "wf", "steps": [{"id": "a", "name": "A", "message": "do it"}]},
            headers=_auth_headers(),
        )
        workflow_id = created.json()["id"]

        response = await client.post(f"/workflows/{workflow_id}/run", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["steps"][0]["result"] == "done"


async def test_run_workflow_endpoint_returns_404_for_unknown_workflow(
    db_connection: aiosqlite.Connection,
) -> None:
    async with await _client(db_connection, []) as client:
        response = await client.post("/workflows/does-not-exist/run", headers=_auth_headers())

    assert response.status_code == 404


async def test_full_http_driven_approve_and_resume_flow(
    db_connection: aiosqlite.Connection,
) -> None:
    """The whole point of Step 11: a client drives create -> run (pauses)
    -> approve -> run again (resumes) entirely over HTTP, no direct
    Python access to WorkflowService/run_workflow at all."""
    async with await _client(db_connection, [_result("a draft"), _result("b done")]) as client:
        created = await client.post(
            "/workflows",
            json={
                "name": "wf",
                "steps": [
                    {"id": "a", "name": "A", "message": "draft it", "requires_approval": True},
                    {"id": "b", "name": "B", "message": "finish it", "depends_on": ["a"]},
                ],
            },
            headers=_auth_headers(),
        )
        workflow_id = created.json()["id"]

        first_run = await client.post(f"/workflows/{workflow_id}/run", headers=_auth_headers())
        assert first_run.json()["status"] == "waiting_for_approval"
        assert first_run.json()["steps"][1]["status"] == "pending"

        approved = await client.post(
            f"/workflows/{workflow_id}/steps/a/approve", headers=_auth_headers()
        )
        assert approved.json()["steps"][0]["status"] == "succeeded"

        second_run = await client.post(f"/workflows/{workflow_id}/run", headers=_auth_headers())

    body = second_run.json()
    assert body["status"] == "succeeded"
    assert body["steps"][1]["result"] == "b done"


async def test_approve_step_returns_409_when_not_awaiting_approval(
    db_connection: aiosqlite.Connection,
) -> None:
    async with await _client(db_connection, []) as client:
        created = await client.post(
            "/workflows",
            json={"name": "wf", "steps": [{"id": "a", "name": "A", "message": "x"}]},
            headers=_auth_headers(),
        )
        workflow_id = created.json()["id"]

        response = await client.post(
            f"/workflows/{workflow_id}/steps/a/approve", headers=_auth_headers()
        )

    assert response.status_code == 409


async def test_reject_step_requires_a_reason(db_connection: aiosqlite.Connection) -> None:
    async with await _client(db_connection, [_result("a draft")]) as client:
        created = await client.post(
            "/workflows",
            json={
                "name": "wf",
                "steps": [{"id": "a", "name": "A", "message": "x", "requires_approval": True}],
            },
            headers=_auth_headers(),
        )
        workflow_id = created.json()["id"]
        await client.post(f"/workflows/{workflow_id}/run", headers=_auth_headers())

        missing_reason = await client.post(
            f"/workflows/{workflow_id}/steps/a/reject", json={}, headers=_auth_headers()
        )
        assert missing_reason.status_code == 422

        rejected = await client.post(
            f"/workflows/{workflow_id}/steps/a/reject",
            json={"reason": "not good enough"},
            headers=_auth_headers(),
        )

    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "failed"
    assert body["steps"][0]["error"] == "not good enough"


@pytest.mark.parametrize("path", ["/workflows/w1/steps/a/approve", "/workflows/w1/steps/a/reject"])
async def test_approve_and_reject_return_404_for_unknown_workflow(
    db_connection: aiosqlite.Connection, path: str
) -> None:
    async with await _client(db_connection, []) as client:
        response = await client.post(path, json={"reason": "x"}, headers=_auth_headers())

    assert response.status_code == 404
