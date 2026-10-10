"""Tool calling's provider-neutral half (spec 01g): what a tool definition
looks like before an adapter spells it, and whether a reply called one.

A gateway leaf, standard library plus `schemas` (itself a stdlib-only leaf),
so the adapters, the facade and -- later -- the store can all import it, as
they import `wire`.

01g-S1 lands only what the `tools` capability's probe needs (its "Drift from
the spec's slice graph"): the neutral definition and its check, each kind's
spelling of a definition and of `tool_choice`, and a `Collector` that
notices the calls a reply BEGAN and how it ended. 01g-S2 grows this module in
place -- the `ToolSpec`/`Toolset` shapes, argument accumulation, opaque
provider state and tool-turn message lowering.

**A definition** is `{"name", "description", "parameters"}`, `parameters`
inside the portable schema subset (`schemas.check`, spec 3.2). **A choice**
is one of `CHOICES`, OpenAI's own spelling: `auto` (the model may call),
`none` (it may not) and `required` (it must). Nothing here decides which
choice a caller asks: an adapter lowers what it is handed, and never
rewrites `required` (the loop's downgrade beside thinking is 01g-S4's).

**The `Collector`** rides the usage holder under `KEY`, as the reasoning
buffer does (`llm_reasoning.Buffer`): installed by the caller before the
facade call, kept and reset by `llm._stamp` at each attempt so a retry
starts with nothing, and fed by the adapters' stream readers. A holder
without one is never written: a call that offered no tools reads exactly as
it always did.
"""

from __future__ import annotations

import re

from . import schemas

#: Where a `Collector` rides in the usage holder.
KEY = "_tool_calls"

#: The neutral `tool_choice` values.
CHOICES: tuple[str, ...] = ("auto", "none", "required")

#: A tool name both providers accept.
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: The normalised end reasons (`Collector.finish_reason`).
FINISHES: tuple[str, ...] = ("stop", "tool_calls", "length", "other")

_OPENAI_FINISH = {"tool_calls": "tool_calls", "function_call": "tool_calls",
                  "stop": "stop", "length": "length"}
_ANTHROPIC_FINISH = {"tool_use": "tool_calls", "end_turn": "stop",
                     "stop_sequence": "stop", "max_tokens": "length"}


def check(tools: object, tool_choice: object = None) -> None:
    """Raise `ValueError` unless `tools` is a non-empty tuple of definitions
    with distinct, well-formed names, a string description and parameters
    inside the portable subset, and `tool_choice` is None or one of
    `CHOICES`. A choice with no tools is refused: there is nothing to
    choose."""
    if tools is None:
        if tool_choice is not None:
            raise ValueError("a tool_choice needs tools to choose among")
        return
    if not isinstance(tools, tuple) or not tools:
        raise ValueError("tools is a non-empty tuple of definitions")
    names = [_checked_name(tool) for tool in tools]
    if len(set(names)) != len(names):
        raise ValueError("a tool is defined twice")
    if tool_choice is not None and tool_choice not in CHOICES:
        raise ValueError(f"tool_choice is one of {', '.join(CHOICES)}: {tool_choice!r}")


def _checked_name(tool: object) -> str:
    """One definition checked (`check`); its name."""
    if not isinstance(tool, dict):
        raise ValueError("a tool definition is an object")
    name = tool.get("name")
    if not isinstance(name, str) or not NAME.match(name):
        raise ValueError(f"a tool name matches {NAME.pattern}: {name!r}")
    if not isinstance(tool.get("description"), str):
        raise ValueError(f"tool {name!r} has a string description")
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError(f"tool {name!r} has an object of parameters")
    schemas.check(parameters)
    return name


def openai_tools(tools: tuple[dict, ...]) -> list[dict]:
    """The definitions as OpenAI-style chat completions spell them (OpenRouter
    and every OpenAI-compatible endpoint). Its `tool_choice` is the neutral
    string itself."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]}} for t in tools]


def anthropic_tools(tools: tuple[dict, ...]) -> list[dict]:
    """The definitions as the Anthropic Messages API spells them."""
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["parameters"]} for t in tools]


def anthropic_choice(choice: str) -> dict:
    """`choice` as the Anthropic Messages API spells it. A plain mapping:
    `required` is `any` whatever else the body holds -- forced tool use
    beside thinking is the caller's to avoid, never rewritten here."""
    return {"auto": {"type": "auto"}, "none": {"type": "none"},
            "required": {"type": "any"}}[choice]


class Collector:
    """The tool calls one attempt's reply began, and how it ended.

    Calls are noticed, not read: S1 keeps each call's provider id and name
    (what a probe needs to say "it called"), and 01g-S2 adds the arguments.
    """

    def __init__(self) -> None:
        self._calls: list[dict] = []
        #: OpenAI-style stream index -> the key of the call open at it.
        self._open: dict[int, str] = {}
        self._finish = ""

    def begin(self) -> None:
        """Forget everything: a new attempt starts (`llm._stamp`)."""
        self._calls.clear()
        self._open.clear()
        self._finish = ""

    def note(self, call_id: str, name: str) -> None:
        """A call the provider began, keyed by its id ("" for none, which is
        always a new call). A known id only fills a name still missing."""
        for call in self._calls:
            if call_id and call["id"] == call_id:
                if name and not call["name"]:
                    call["name"] = name
                return
        self._calls.append({"id": call_id, "name": name})

    def openai_fragment(self, fragment: object) -> None:
        """One `choices[0].delta.tool_calls[i]` fragment. Fragments are keyed
        by `index` (0 when absent): an `id` that differs from the call open at
        that index starts a new call -- some servers send every parallel call
        at `index: 0` with distinct ids -- and a fragment with no id continues
        the open call (or starts one keyed `#<index>`)."""
        if not isinstance(fragment, dict):
            return
        index = fragment.get("index")
        index = index if isinstance(index, int) and not isinstance(index, bool) else 0
        call_id = fragment.get("id")
        function = fragment.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        name = name if isinstance(name, str) else ""
        if isinstance(call_id, str) and call_id:
            key = call_id
        else:
            key = self._open.get(index, f"#{index}")
        self._open[index] = key
        self.note(key, name)

    def finish(self, reason: str) -> None:
        """The reply's end reason, already normalised (`FINISHES`)."""
        self._finish = reason if reason in FINISHES else "other"

    @property
    def called(self) -> bool:
        """Whether the reply began any call."""
        return bool(self._calls)

    def names(self) -> tuple[str, ...]:
        """The called tools' names, in the order the calls began."""
        return tuple(c["name"] for c in self._calls)

    @property
    def finish_reason(self) -> str:
        """`stop`, `tool_calls`, `length` or `other`; "" before the end."""
        return self._finish


def _collector(usage: object) -> Collector | None:
    found = usage.get(KEY) if isinstance(usage, dict) else None
    return found if isinstance(found, Collector) else None


def from_openai_chunk(obj: object, usage: object) -> None:
    """Feed one OpenAI-style SSE chunk's tool-call fragments and end reason to
    the holder's `Collector`; nothing without one. Reads the first choice,
    as the prose adapters do."""
    collector = _collector(usage)
    if collector is None or not isinstance(obj, dict):
        return
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return
    choice = choices[0]
    delta = choice.get("delta")
    fragments = delta.get("tool_calls") if isinstance(delta, dict) else None
    if isinstance(fragments, list):
        for fragment in fragments:
            collector.openai_fragment(fragment)
    reason = choice.get("finish_reason")
    if isinstance(reason, str) and reason:
        collector.finish(_OPENAI_FINISH.get(reason, "other"))


def feed_anthropic(usage: object, frame: object) -> None:
    """Feed one Anthropic `content_block_start` / `content_block_delta` frame
    -- the whole frame, so the block's `index` is in hand for 01g-S2's
    `input_json_delta` -- to the holder's `Collector`. S1 reads a started
    `tool_use` block and nothing else."""
    collector = _collector(usage)
    if collector is None or not isinstance(frame, dict):
        return
    if frame.get("type") != "content_block_start":
        return
    block = frame.get("content_block")
    if not isinstance(block, dict) or block.get("type") != "tool_use":
        return
    call_id, name = block.get("id"), block.get("name")
    collector.note(call_id if isinstance(call_id, str) else "",
                   name if isinstance(name, str) else "")


def anthropic_stop(usage: object, stop_reason: object) -> None:
    """Feed an Anthropic `stop_reason` to the holder's `Collector`."""
    collector = _collector(usage)
    if collector is not None and isinstance(stop_reason, str) and stop_reason:
        collector.finish(_ANTHROPIC_FINISH.get(stop_reason, "other"))
