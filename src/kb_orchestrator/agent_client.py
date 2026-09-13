"""HTTP client for kb-agent's `/chat` endpoint (Project 2, Step 11).

`AgentClient` is a `Protocol` (not a dependency on the concrete
`HttpAgentClient` class), the same interface-segregation pattern as
Project 2's `ToolProvider` -- it lets tests hand in a fake without
inheriting a concrete class, and keeps `execution.py` (Step 5) from
knowing anything about HTTP at all.

No `history` parameter: every call here is a fresh, standalone request.
Per Step 0's confirmed design, kb-agent's own multi-turn `history`
mechanism is for one continuous conversation with one persona -- but a
workflow's steps are different *roles* ("research", "draft", ...), not
turns in one chat. Chaining context between steps (Step 6) happens by
composing a step's `message` text to include a prior step's result, not
by threading kb-agent's conversation history across unrelated calls.
"""

import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx
from pydantic import BaseModel


class AgentToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, object]
    result_text: str
    is_error: bool


class AgentExecutionFailure(BaseModel):
    tool_name: str
    arguments: dict[str, object]
    error: str


class AgentChatResult(BaseModel):
    """Mirrors kb-agent's `ChatResponseBody` (its own wire schema, not
    Anthropic's SDK types) -- this app's own pydantic model at this
    boundary, not a reuse of kb-agent's class across a process/package
    line that only actually connects over HTTP."""

    text: str | None
    tool_calls: list[AgentToolCall]
    iterations: int
    stop_reason: str | None
    hit_iteration_limit: bool
    hit_token_budget: bool
    execution_failure: AgentExecutionFailure | None
    input_tokens: int
    output_tokens: int


class AgentCallError(Exception):
    """The HTTP call to kb-agent itself failed -- a network/protocol
    problem (connection refused, timeout, non-2xx, a malformed or
    truncated SSE stream). Distinct from `AgentChatResult.execution_failure`,
    which is a *successful* HTTP call whose body reports that kb-agent's
    own tool execution failed -- this app can't fix a dropped connection
    by retrying with different words, same reasoning Project 2 drew
    between a tool's `is_error=True` and its own infrastructure failures.
    """


class AgentClient(Protocol):
    async def send_message(self, message: str) -> AgentChatResult: ...


async def _iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """Parse Server-Sent Events from a streaming response body.

    Matches kb-agent's own `_format_sse` encoding exactly (verified by
    reading that source, not guessed): one `event: <type>` line, one or
    more `data: <line>` lines (a multi-line payload is split across
    several, rejoined here with `\\n`), then a blank line separator.
    """
    event: str | None = None
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if event is not None:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
        elif line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
    if event is not None:
        yield event, "\n".join(data_lines)


class HttpAgentClient:
    """The real `AgentClient`: an `httpx.AsyncClient` already configured
    with kb-agent's base URL, plus the bearer token to call it with."""

    def __init__(self, client: httpx.AsyncClient, *, auth_token: str) -> None:
        self._client = client
        self._auth_token = auth_token

    async def send_message(self, message: str) -> AgentChatResult:
        done_data: str | None = None
        try:
            async with self._client.stream(
                "POST",
                "/chat",
                json={"message": message, "history": []},
                headers={"Authorization": f"Bearer {self._auth_token}"},
            ) as response:
                response.raise_for_status()
                async for event, data in _iter_sse(response):
                    if event == "done":
                        done_data = data
                    elif event == "error":
                        raise AgentCallError(json.loads(data).get("error", data))
        except httpx.HTTPError as exc:
            raise AgentCallError(str(exc)) from exc

        if done_data is None:
            raise AgentCallError("kb-agent's response stream ended without a 'done' event")
        return AgentChatResult.model_validate_json(done_data)
