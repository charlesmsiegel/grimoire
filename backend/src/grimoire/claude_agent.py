"""Claude Agent SDK provider — routes prompts through the local Claude Code login.

Auth is inherited from the host's Claude Code session (or CLAUDE_CODE_OAUTH_TOKEN);
usage bills against the owner's Claude subscription, not an API key. See
docs/superpowers/specs/2026-07-10-claude-provider-design.md for the policy notes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from . import llm_capture, llm_reasoning, llm_usage, tool_calls
from .llm_errors import LLMError

# claude-agent-sdk lives in the `claude` extra, which Android does not install
# (android/app/build.gradle.kts mirrors the *base* deps only), so the import has
# to be survivable. `llm.py` imports ClaudeAgentClient at module scope, so an
# exception escaping here would stop the app from starting; stream() reports what
# was captured instead, leaving the failure at the same call it hit before.
try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        CLINotFoundError,
        ProcessError,
        TextBlock,
        query,
    )
    _SDK_IMPORT_ERROR: Exception | None = None
except ImportError as exc:                  # the `claude` extra is not installed
    AssistantMessage = ClaudeAgentOptions = TextBlock = query = None
    # Empty tuples, not None: these are used as `except` targets, and
    # `except None` raises TypeError while `except ()` simply never matches.
    CLINotFoundError = ProcessError = ()
    _SDK_IMPORT_ERROR = exc
except Exception as exc:  # noqa: BLE001 - installed but broken; stream() re-raises its type/message
    AssistantMessage = ClaudeAgentOptions = TextBlock = query = None
    CLINotFoundError = ProcessError = ()
    _SDK_IMPORT_ERROR = exc


# What declaring tools needs (01g-S8): the `@tool` decorator, the in-process
# MCP server and the hook matcher -- imported apart, so an SDK too old to have
# them still serves every call that offers none, and only an offer of tools
# is refused (`_tool_options`). The floor in the `claude` extra is the first
# version with all three and the `"defer"` decision (0.1.74).
try:
    from claude_agent_sdk import HookMatcher, create_sdk_mcp_server
    from claude_agent_sdk import tool as sdk_tool
except Exception:  # noqa: BLE001 - absent or too old: only an offer of tools is refused
    HookMatcher = create_sdk_mcp_server = sdk_tool = None

#: How the SDK names an in-process MCP server's tool to the model.
TOOL_PREFIX = "mcp__grimoire__"
#: The longest tool name this kind can carry: the provider's 64, less the
#: prefix the SDK adds (spec 3.13). `ToolSpec`'s own limit stays 64.
MAX_TOOL_NAME = 64 - len(TOOL_PREFIX)


def _sdk_failure() -> Exception:
    """A **fresh** exception carrying the captured import failure's type and
    message, for stream() to raise on the broken-install path.

    Not the captured object itself. `raise` records the raise site on the
    exception's own `__traceback__`, so raising one module-level object over
    and over grows a single traceback without bound -- and every frame it
    keeps holds that call's locals, i.e. the prompt. The lazy import this
    replaced could not do that: a module that raises while executing is
    dropped from `sys.modules`, so each call re-ran the import and got a new
    object with a traceback of its own.

    `type(exc)(*exc.args)` rebuilds every exception whose `__init__` keeps
    BaseException's signature, which is the overwhelming majority; a type that
    takes something else falls back to the captured object with its traceback
    cleared, which is equally growth-free -- it just loses the import
    traceback, as the reconstruction does too.
    """
    exc = _SDK_IMPORT_ERROR
    try:
        return type(exc)(*exc.args)
    except Exception:  # noqa: BLE001 - an unreconstructible type must not mask the real failure
        return exc.with_traceback(None)


class ClaudeAgentError(LLMError):
    pass


#: What this path's dollars mean. Auth here is the host's Claude Code login, so
#: a call bills against a subscription and charges nothing per request; the
#: SDK's `total_cost_usd` is what the same work would have cost at API rates.
#: Recording it as spend would tell someone they had spent money they had not,
#: so `store.usage` keeps this basis out of the billed total and reports it
#: separately.
COST_BASIS = "equivalent"

#: The `usage` keys that are prompt tokens. Cache reads and cache writes are
#: billed input (at different rates, which is the provider's arithmetic and not
#: ours) -- counting only `input_tokens` would under-report a long campaign by
#: most of its prompt, since that is precisely the part that caches.
_PROMPT_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _capture_usage(message, usage: dict | None) -> None:
    """Fold an SDK `ResultMessage`'s accounting into `usage` (#152).

    Duck-typed rather than an `isinstance(message, ResultMessage)`, and
    deliberately: `ResultMessage` is not among the names imported at module
    scope, and adding a seventh to that guarded import would give an SDK that
    ever renames or drops it a way to break the whole provider — for a
    statistic. An object carrying `usage` or `total_cost_usd` is the message we
    mean; nothing else in the stream has either.

    Never raises, for the reason `llm_usage` does not: this is trailing
    metadata on a reply the caller already has.
    """
    if usage is None:
        return
    block = getattr(message, "usage", None)
    if isinstance(block, dict):
        # Only the keys that yielded a real count are summed, and a block whose
        # every prompt key is garbage records NOTHING rather than a total of
        # zero -- `llm_usage.tokens` says why that difference matters.
        counted = [n for n in (llm_usage.tokens(block.get(k)) for k in _PROMPT_KEYS)
                   if n is not None]
        if counted:
            usage["prompt_tokens"] = sum(counted)
        completion = llm_usage.tokens(block.get("output_tokens"))
        if completion is not None:
            usage["completion_tokens"] = completion
        # Recorded a second time, on purpose, and not double-counted: the sum
        # above folds these INTO `prompt_tokens` because that is what was
        # billed as input, and these say how that input split between a cache
        # hit and a fresh read (#148). This is the provider that caches without
        # being asked, so on a long campaign the read is most of every prompt --
        # which is invisible in a total that only knows the three added up.
        for key, count in (("cache_read_tokens", llm_usage.cache_read(block)),
                           ("cache_write_tokens", llm_usage.cache_written(block))):
            if count is not None:
                usage[key] = count
    cost = llm_usage.money(getattr(message, "total_cost_usd", None))
    if cost is not None:
        usage["cost_usd"] = cost
        usage["cost_basis"] = COST_BASIS


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def _turn(m: dict) -> str:
    """One message as a transcript block. A tool loop's turns (01g-S8) are
    labelled blocks: an assistant turn's calls after its text, each a
    `[assistant -> tool <name> <id>]` block of its arguments, and a result a
    `[tool <id>]` block. There is no session resume: the loop's history is
    the conversation, re-sent whole each turn."""
    if m.get("role") == "tool":
        return f"[tool {m.get('tool_call_id', '')}]\n{m.get('content', '')}"
    block = f"[{m['role']}]\n{m['content']}"
    for call in m.get("tool_calls") or ():
        if isinstance(call, dict):
            block += (f"\n\n[assistant -> tool {call.get('name', '')} {call.get('id', '')}]\n"
                      f"{json.dumps(call.get('arguments') or {}, ensure_ascii=False)}")
    return block


def _flatten(turns: list[dict]) -> str:
    # The Agent SDK takes a single prompt string, not a message array; render
    # the conversation as a transcript and cue the next assistant reply.
    lines = [_turn(m) for m in turns]
    lines.append("[assistant]")
    return "\n\n".join(lines)


async def _never_run(args: dict) -> dict:
    """Every declared tool's handler: the SDK never executes a Grimoire tool
    (the hook defers every call to the loop), and one that did would be a
    tool the caller did not run."""
    raise RuntimeError("a grimoire tool is executed by the loop, never by the SDK")


def _tool_options(system: str, model: str, tools: tuple[dict, ...],
                  tool_choice: str | None) -> object:
    """The options of one turn that offers tools (spec 3.13): isolated from
    the host's Claude setup -- no filesystem settings sources, no MCP server
    but this one, no built-in tools -- with each tool declared on an
    in-process MCP server whose handlers never run, and a PreToolUse hook on
    every tool that DEFERS a Grimoire tool's call (the run stops and reports
    it) and denies anything else. `tool_choice` "none" denies every call,
    and "required" cannot be asked of the SDK, so it is offered as "auto"."""
    if create_sdk_mcp_server is None or sdk_tool is None or HookMatcher is None:
        raise ClaudeAgentError("bad_response", "the installed claude-agent-sdk cannot declare "
                               "tools (0.1.74 or later is needed)", code=tool_calls.REFUSED)
    long = [t["name"] for t in tools if len(t["name"]) > MAX_TOOL_NAME]
    if long:
        raise ClaudeAgentError("bad_response", f"claude connections take tool names of at most "
                               f"{MAX_TOOL_NAME} characters: {', '.join(long)}",
                               code=tool_calls.REFUSED)
    names = {TOOL_PREFIX + t["name"] for t in tools}
    offered = tool_choice != "none"

    async def gate(hook_input, tool_use_id, context) -> dict:
        name = hook_input.get("tool_name", "") if isinstance(hook_input, dict) else ""
        decision = "defer" if offered and name in names else "deny"
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": decision,
            "permissionDecisionReason": "grimoire runs its own tools"}}

    declared = [sdk_tool(t["name"], t["description"], t["parameters"])(_never_run)
                for t in tools]
    return ClaudeAgentOptions(
        system_prompt=system or None, model=model, tools=[],
        allowed_tools=sorted(names) if offered else [],
        mcp_servers={"grimoire": create_sdk_mcp_server(name="grimoire", tools=declared)},
        strict_mcp_config=True, setting_sources=[],
        hooks={"PreToolUse": [HookMatcher(matcher="*", hooks=[gate])]},
        # One model turn and its deferred call; a second turn could only
        # follow a result, and the loop sends results itself.
        max_turns=2)


def _note_deferred(message: object, usage: dict | None) -> bool:
    """A `ResultMessage`'s deferred call, onto the holder's collector, its
    name stripped of the MCP prefix (an unknown remainder is the loop's to
    answer as an unknown tool); whether there was one. Duck-typed, as
    `_capture_usage` is."""
    deferred = getattr(message, "deferred_tool_use", None)
    if deferred is None:
        return False
    name = str(getattr(deferred, "name", "") or "")
    name = name.removeprefix(TOOL_PREFIX)
    arguments = json.dumps(getattr(deferred, "input", None) or {}, ensure_ascii=False)
    llm_usage.note_reply(usage, name + arguments)
    found = usage.get(tool_calls.KEY) if usage is not None else None
    if isinstance(found, tool_calls.Collector):
        found.note(str(getattr(deferred, "id", "") or ""), name, arguments)
    return True


#: What `probe` sends. Short on both sides on purpose: the reply is discarded
#: at its first word, and a prompt long enough to be interesting would be a
#: prompt long enough to be worth caching, billing and reading.
_PROBE_MESSAGES = [{"role": "system", "content": "Reply with the single word: ok"},
                   {"role": "user", "content": "ping"}]


class ClaudeAgentClient:
    async def stream(self, messages: list[dict], model: str,
                     usage: dict | None = None, *, tools: tuple[dict, ...] | None = None,
                     tool_choice: str | None = None) -> AsyncGenerator[str, None]:
        """`usage`, when given, is filled in place from the run's trailing
        `ResultMessage` — see `_capture_usage`.

        `tools` (01g-S8), when given, are declared for this one query
        (`_tool_options`): the model's first Grimoire tool call stops the run
        and comes back as the `ResultMessage`'s deferred call, noted on the
        holder's `tool_calls.Collector`, so a turn on this kind makes at most
        one call. Absent, the call is the call it always was."""
        if query is None:
            if isinstance(_SDK_IMPORT_ERROR, ImportError):
                raise ClaudeAgentError(
                    "missing_dependency",
                    "claude-agent-sdk is not installed — pip install 'grimoire[claude]'",
                ) from _SDK_IMPORT_ERROR
            raise _sdk_failure()            # installed but broken: same type/message, same place
        system, turns = _split_system(messages)
        options = (ClaudeAgentOptions(system_prompt=system or None, model=model,
                                      allowed_tools=[], max_turns=1)
                   if tools is None else _tool_options(system, model, tools, tool_choice))
        called = False
        try:
            async for message in query(prompt=_flatten(turns), options=options):
                llm_capture.emit(usage, "sdk_message", message)
                # Proof of life for the facade's idle bound: the SDK sends
                # messages that carry no text (thinking, tool, result), and a
                # model can spend minutes on those before its first word (#243).
                yield ""
                _capture_usage(message, usage)
                if tools is not None:
                    called = _note_deferred(message, usage) or called
                for block in getattr(message, "content", []) or []:
                    llm_reasoning.feed(usage, getattr(block, "thinking", None))
                if llm_reasoning.pending(usage):
                    yield ""
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            yield block.text
        except CLINotFoundError as exc:
            raise ClaudeAgentError("missing_dependency", str(exc)) from exc
        except ProcessError as exc:
            raise ClaudeAgentError("bad_response", str(exc)) from exc
        # so a future SDK-raised ClaudeAgentError isn't re-tagged as "network" below
        except ClaudeAgentError:
            raise
        except Exception as exc:
            raise ClaudeAgentError("network", str(exc)) from exc
        found = usage.get(tool_calls.KEY) if usage is not None else None
        if tools is not None and isinstance(found, tool_calls.Collector):
            found.finish("tool_calls" if called else "stop")

    async def probe(self, model: str) -> None:
        """Ask whether this path can generate. Returns on yes, raises on no.

        The other two kinds have a free endpoint that answers "is this
        credential good" without generating anything. This one has none: auth
        is the host's Claude Code login, and the only thing that knows whether
        it is still valid is the CLI, which learns it by running. So the probe
        is a real (tiny, capped-at-one-turn) generation — the cheapest honest
        answer, and one that costs a subscription turn rather than money.

        It stops at the first word rather than reading the reply out: closing
        the iterator unwinds `query`, which is the same shutdown the facade's
        idle bound already performs on every cancelled generation. A run that
        ends having said nothing at all still counts as healthy — the question
        asked here is whether the path works, not whether the model was
        talkative.

        A missing or broken SDK never reaches the subprocess: `stream` raises
        `missing_dependency` from the captured import failure, which is the
        answer, and a cheaper one than spawning to find out.
        """
        # Typed as a generator rather than an iterator (see `stream`) precisely
        # so this close is checkable: an iterator has no `aclose`, and an
        # abandoned SDK query would then be left to the garbage collector.
        agen = self.stream(_PROBE_MESSAGES, model)
        try:
            async for chunk in agen:
                if chunk:
                    return
        finally:
            await agen.aclose()

    async def complete(self, messages: list[dict], model: str,
                       usage: dict | None = None) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model, usage)])
