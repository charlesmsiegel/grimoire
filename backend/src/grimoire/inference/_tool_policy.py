"""Tool-run admission and the shared signal identifying active loop tasks."""

from __future__ import annotations

import asyncio
import weakref

from .. import claude_agent, model_guidance, prompts, schemas, store, tool_calls
from ..llm_errors import LLMError
from ..store.inference import resolve
from ..store.inference.resolved import ResolvedInference
from . import _requests

#: The tasks running a loop now: a loop does not start inside another (spec
#: 3.12, no recursion). A set of tasks rather than a `ContextVar`, which an
#: async generator does not carry across its consumer's steps.
_LOOPING: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()


class WallSpentError(LLMError):
    """The run's own wall clock ran out while a turn, or a decide-tool call
    (`routes.tool_decision`), was in flight: a budget stop, not a failure,
    so the meter files `aborted` and nothing reaches the error store or the
    connection's health (spec 3.9)."""

    #: `store.usage.NOT_A_FAILURE`, as absorb's `BudgetRefused` declares it.
    llm_call_failed = False


class _ToolTimeoutError(Exception):
    """A tool's wait ran out (`deadline.bounded`'s overrun)."""


def finalize_message(schema: dict | None, *, stopped: bool) -> dict:
    """The finalize turn's instruction (spec 3.7 step 7), as the user message
    appended after the run's last results: `templates/tools/finalize.j2`,
    carrying `schema` (rendered with `schema_json`) when there is one. Reads a
    template, so callers run it in a worker thread."""
    return {"role": "user",
            "content": prompts.render("tools/finalize.j2", schema=schema, stopped=stopped)}


def _check_loop_prompt(messages: list[dict]) -> None:
    """A prompt a loop can extend: not ending in an assistant turn (a
    prefill), not chosen per attempt, and -- a `PreparedMessages` -- frozen,
    so turn 2 can append to it (`snapshot` raises for one that is not)."""
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant":
        raise ValueError("a tool loop cannot start from a prompt ending in an assistant turn")
    if isinstance(messages, model_guidance.PreparedMessages):
        if messages.tailed:
            raise ValueError("a tool loop cannot carry per-attempt tails")
        messages.snapshot()


def _check_loop_route(task: str, resolved: ResolvedInference) -> None:
    """A route that requires tools, or lists them as optional
    (`routing.TOOLS_OPTIONAL`), on a primary not KNOWN unable to call them."""
    route = store.routing.route(task)
    if route is None or not ("tools" in route.requires
                             or route.key in store.routing.TOOLS_OPTIONAL):
        raise ValueError(f"{task!r} is on a route that neither requires tools nor offers them")
    if resolve.known_lacks(resolved, "tools"):
        raise ValueError(f"{task!r} resolved to a model known unable to call tools")


def _loop_refusal(task: str, messages: list[dict], toolset: tool_calls.Toolset,
                  resolved: ResolvedInference, run_id: str, final_schema: dict | None,
                  tool_choice: str) -> None:
    """Every refusal `run_tools` makes before anything is sent (spec 3.7), each
    a `ValueError`, so no meter opens."""
    _requests._generating(task, resolved)
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("a tool loop runs under the caller's run id, and none was given")
    if not isinstance(toolset, tool_calls.Toolset) or not toolset.tools:
        raise ValueError("a tool loop needs at least one tool")
    if tool_choice not in ("auto", "required"):
        raise ValueError(f"a loop's tool_choice is auto or required, not {tool_choice!r}")
    if final_schema is not None:
        schemas.check(final_schema)
    _check_loop_prompt(messages)
    _check_loop_route(task, resolved)
    if any(a.target.kind == "claude" for a in resolved.attempts):
        long = [t.name for t in toolset.tools if len(t.name) > claude_agent.MAX_TOOL_NAME]
        if long:
            raise ValueError(f"a Claude subscription takes tool names of at most "
                             f"{claude_agent.MAX_TOOL_NAME} characters: {', '.join(long)}")
    current = asyncio.current_task()
    if current is not None and current in _LOOPING:
        raise ValueError("a tool loop cannot start inside another")
