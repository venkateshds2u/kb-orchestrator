"""Tests for HttpAgentClient's SSE parsing and error handling.

Uses `httpx.MockTransport` (a real httpx testing utility, not a fake we
invented) to fake the actual network transport -- everything else
(`httpx.AsyncClient`'s real request building, our own SSE-parsing code)
runs for real. This is the appropriate boundary to mock here: spinning up
a real kb-agent process would need a real ANTHROPIC_API_KEY (this
project's standing choice, inherited from Project 2, is to avoid that),
and importing kb-agent's Python internals directly would violate the
HTTP-only boundary between these two packages that Step 0 deliberately
drew.
"""

import httpx
import pytest
import structlog

from kb_orchestrator.agent_client import AgentCallError, HttpAgentClient

_DONE_BODY_OK = (
    '{"text": "ok", "tool_calls": [], "iterations": 1, "stop_reason": "end_turn", '
    '"hit_iteration_limit": false, "hit_token_budget": false, '
    '"execution_failure": null, "input_tokens": 1, "output_tokens": 1}'
)


def _sse_response(events: list[tuple[str, str]], *, status_code: int = 200) -> httpx.Response:
    body = "".join(f"event: {event}\ndata: {data}\n\n" for event, data in events)
    return httpx.Response(status_code, content=body.encode())


def _client_with_transport(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="http://test")


async def test_send_message_returns_the_done_event_payload() -> None:
    done_body = (
        '{"text": "hi there", "tool_calls": [], "iterations": 1, '
        '"stop_reason": "end_turn", "hit_iteration_limit": false, '
        '"hit_token_budget": false, "execution_failure": null, '
        '"input_tokens": 10, "output_tokens": 5}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([("token", "hi"), ("token", " there"), ("done", done_body)])

    client = HttpAgentClient(
        _client_with_transport(httpx.MockTransport(handler)), auth_token="secret"
    )
    result = await client.send_message("hello")

    assert result.text == "hi there"
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 10


async def test_send_message_sends_bearer_auth_and_json_body() -> None:
    captured: list[httpx.Request] = []
    done_body = (
        '{"text": "ok", "tool_calls": [], "iterations": 1, "stop_reason": "end_turn", '
        '"hit_iteration_limit": false, "hit_token_budget": false, '
        '"execution_failure": null, "input_tokens": 1, "output_tokens": 1}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _sse_response([("done", done_body)])

    client = HttpAgentClient(
        _client_with_transport(httpx.MockTransport(handler)), auth_token="my-token"
    )
    await client.send_message("hello")

    assert captured[0].headers["authorization"] == "Bearer my-token"
    assert captured[0].url.path == "/chat"
    assert b'"message":"hello"' in captured[0].content


async def test_send_message_raises_on_error_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([("error", '{"error": "something broke"}')])

    client = HttpAgentClient(_client_with_transport(httpx.MockTransport(handler)), auth_token="t")

    with pytest.raises(AgentCallError, match="something broke"):
        await client.send_message("hello")


async def test_send_message_raises_on_non_2xx_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = HttpAgentClient(_client_with_transport(httpx.MockTransport(handler)), auth_token="t")

    with pytest.raises(AgentCallError):
        await client.send_message("hello")


async def test_send_message_raises_if_stream_ends_without_done_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([("token", "partial")])

    client = HttpAgentClient(_client_with_transport(httpx.MockTransport(handler)), auth_token="t")

    with pytest.raises(AgentCallError, match="without a 'done' event"):
        await client.send_message("hello")


async def test_send_message_raises_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = HttpAgentClient(_client_with_transport(httpx.MockTransport(handler)), auth_token="t")

    with pytest.raises(AgentCallError):
        await client.send_message("hello")


# --- correlation header propagation (Step 10) -------------------------------


async def test_send_message_includes_correlation_headers_when_context_is_bound() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _sse_response([("done", _DONE_BODY_OK)])

    client = HttpAgentClient(_client_with_transport(httpx.MockTransport(handler)), auth_token="t")

    with structlog.contextvars.bound_contextvars(workflow_id="w1", step_id="research"):
        await client.send_message("hello")

    assert captured[0].headers["x-workflow-id"] == "w1"
    assert captured[0].headers["x-step-id"] == "research"


async def test_send_message_omits_correlation_headers_when_no_context_is_bound() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _sse_response([("done", _DONE_BODY_OK)])

    client = HttpAgentClient(_client_with_transport(httpx.MockTransport(handler)), auth_token="t")

    await client.send_message("hello")

    assert "x-workflow-id" not in captured[0].headers
    assert "x-step-id" not in captured[0].headers
