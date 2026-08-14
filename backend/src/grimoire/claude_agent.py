"""Claude Agent SDK provider — routes prompts through the local Claude Code login.

Auth is inherited from the host's Claude Code session (or CLAUDE_CODE_OAUTH_TOKEN);
usage bills against the owner's Claude subscription, not an API key. See
docs/superpowers/specs/2026-07-10-claude-provider-design.md for the policy notes.
"""

from __future__ import annotations

from typing import AsyncIterator

from .llm_errors import LLMError

# claude-agent-sdk lives in the `claude` extra, which Android does not install
# (android/app/build.gradle.kts mirrors the *base* deps only), so the import has
# to be survivable. `llm.py` imports ClaudeAgentClient at module scope, so an
# exception escaping here would stop the app from starting; stream() reports what
# was captured instead, leaving the failure at the same call it hit before.
try:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                                  CLINotFoundError, ProcessError, TextBlock, query)
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
        prompt = sum(v for k in _PROMPT_KEYS
                     if isinstance(v := block.get(k), int) and not isinstance(v, bool))
        completion = block.get("output_tokens")
        if any(k in block for k in _PROMPT_KEYS):
            usage["prompt_tokens"] = prompt
        if isinstance(completion, int) and not isinstance(completion, bool):
            usage["completion_tokens"] = completion
    cost = getattr(message, "total_cost_usd", None)
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost == cost:
        usage["cost_usd"] = float(cost)
        usage["cost_basis"] = COST_BASIS


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def _flatten(turns: list[dict]) -> str:
    # The Agent SDK takes a single prompt string, not a message array; render
    # the conversation as a transcript and cue the next assistant reply.
    lines = [f"[{m['role']}]\n{m['content']}" for m in turns]
    lines.append("[assistant]")
    return "\n\n".join(lines)


class ClaudeAgentClient:
    async def stream(self, messages: list[dict], model: str,
                     usage: dict | None = None) -> AsyncIterator[str]:
        """`usage`, when given, is filled in place from the run's trailing
        `ResultMessage` — see `_capture_usage`."""
        if query is None:
            if isinstance(_SDK_IMPORT_ERROR, ImportError):
                raise ClaudeAgentError(
                    "missing_dependency",
                    "claude-agent-sdk is not installed — pip install 'grimoire[claude]'",
                ) from _SDK_IMPORT_ERROR
            raise _sdk_failure()            # installed but broken: same type/message, same place
        system, turns = _split_system(messages)
        options = ClaudeAgentOptions(
            system_prompt=system or None, model=model, allowed_tools=[], max_turns=1,
        )
        try:
            async for message in query(prompt=_flatten(turns), options=options):
                # Proof of life for the facade's idle bound: the SDK sends
                # messages that carry no text (thinking, tool, result), and a
                # model can spend minutes on those before its first word (#243).
                yield ""
                _capture_usage(message, usage)
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

    async def complete(self, messages: list[dict], model: str,
                       usage: dict | None = None) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model, usage)])
