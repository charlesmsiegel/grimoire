"""Tool calling's provider-neutral half (spec 01g): the shapes a caller
declares its tools in, how a tool definition and a tool turn are spelled per
provider kind, and what calls a reply made.

A gateway leaf, standard library plus `schemas` (itself a stdlib-only leaf),
so the adapters, the facade and -- later -- the store can all import it, as
they import `wire`.

01g-S1 landed what the `tools` capability's probe needs (the neutral
definition and its check, each kind's spelling of a definition and of
`tool_choice`, and call detection); 01g-S2 grew it in place with the
`ToolSpec` / `Toolset` shapes, `conforms`, argument accumulation, opaque
provider state and the tool-turn message lowering. The loop that uses them
is 01g-S4's.

**A definition** is `{"name", "description", "parameters"}`, `parameters`
inside the portable schema subset (`schemas.check`, spec 3.2). **A choice**
is one of `CHOICES`, OpenAI's own spelling: `auto` (the model may call),
`none` (it may not) and `required` (it must). Nothing here decides which
choice a caller asks: an adapter lowers what it is handed, and never
rewrites `required` (the loop's downgrade beside thinking is 01g-S4's).

**Two neutral message shapes** carry a tool history (spec 3.2):

    {"role": "assistant", "content": "<text, may be ''>",
     "tool_calls": [{"id": ..., "name": ..., "arguments": {...}}],
     "_opaque": {"kind", "provider_id", "model", "items": [...]}}   # optional
    {"role": "tool", "tool_call_id": ..., "name": ..., "content": "<text>",
     "is_error": False}

Nothing above the adapters spells a provider's format. `provenanced` drops
an `_opaque` that another `(kind, provider_id, model)` wrote, and the
per-kind lowering (`openai_messages`, the `anthropic_*` blocks) turns the
rest into the wire's own. Every lowering hands back the SAME list when no
message carries a tool turn, so a call without tools is the call it always
was, byte for byte.

**The `Collector`** rides the usage holder under `KEY`, as the reasoning
buffer does (`llm_reasoning.Buffer`): installed by the caller before the
facade call, kept and reset by `llm._stamp` at each attempt -- which also
tells it the attempt's provenance -- so a retry starts with nothing, and fed
by the adapters' stream readers. The feeders answer the text they consumed
(names and argument fragments), which the adapters note for the local token
estimate: arguments are billed completion. A holder without a collector is
never written.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, Protocol

from . import schemas

#: Where a `Collector` rides in the usage holder.
KEY = "_tool_calls"

#: The neutral `tool_choice` values.
CHOICES: tuple[str, ...] = ("auto", "none", "required")

#: A tool name both providers accept.
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: The normalised end reasons (`Collector.finish_reason`).
FINISHES: tuple[str, ...] = ("stop", "tool_calls", "length", "other")

#: The most tools one `Toolset` holds. Providers document limits far above
#: this; past a few dozen a model's choice among them degrades and the
#: definitions alone fill the prompt (spec 3.2). To be tuned against real runs.
MAX_TOOLS = 32

#: The longest description a tool may carry, in characters.
MAX_DESCRIPTION = 1024

#: The most characters one result is sent back as: a result rides every later
#: turn, so one oversized read taxes the rest of the run.
MAX_RESULT_CHARS = 8000

#: How long one tool execution is waited for, in seconds. A tool is a store
#: read; one that has not answered in this long has hit a cold library or a
#: sync stall, and the run is better told so than held. A tool that is not a
#: read sets its own (`ToolSpec.timeout`): the decide tool asks a model, and
#: bounds each of its calls itself (`routes.tool_decision`).
TOOL_TIMEOUT_S = 10.0

#: The `LLMError.code` of tools refused -- by a provider's 400 naming them, or
#: by an adapter whose kind cannot send them (`llm._tools_refusal`).
REFUSED = "tools_refused"

#: Where a call's text is prefixed on a wire with no error flag for a result.
ERROR_PREFIX = "ERROR: "

_OPENAI_FINISH = {"tool_calls": "tool_calls", "function_call": "tool_calls",
                  "stop": "stop", "length": "length"}
_ANTHROPIC_FINISH = {"tool_use": "tool_calls", "end_turn": "stop",
                     "stop_sequence": "stop", "max_tokens": "length"}

#: The Anthropic block types kept as opaque state, to be echoed back.
_THINKING = ("thinking", "redacted_thinking")

#: The string fields of a `reasoning_details` entry that stream in pieces.
_DETAIL_TEXT = ("text", "summary", "data")


# ---- the shapes (spec 3.2) ----
@dataclass(frozen=True)
class ToolOutput:
    """What a tool hands back. `text` is what the model is sent (truncated by
    the loop to the tool's `max_result_chars`); `refs` the stable store refs
    it inspected, for the trace; `proposal` a `propose` tool's suggestion,
    collected and never applied."""
    text: str
    refs: tuple[str, ...] = ()
    proposal: dict | None = None


class RunView(Protocol):
    """What a tool may ask of the run it is in (01g-S7): the decide tool
    takes a decision against the run's `max_decisions`, asks the spend left
    under its ceiling (None: no ceiling), charges what its own calls cost as
    the guard prices them, and notes each call's outcome -- its status and
    the option chosen, if any -- for the run's trace (spec 3.12). Nothing it
    could write the store with."""

    def take_decision(self) -> bool: ...

    def room_usd(self) -> float | None: ...

    def charge(self, usd: float) -> None: ...

    def note_decision(self, status: str, option: str | None = None) -> None: ...


@dataclass(frozen=True)
class ToolContext:
    """What a tool may know: ids, its deadline, the pinned store root, the
    loop turn that asked, and a view of the run's budget -- and nothing it
    could write with. A tool that makes model calls of its own (the decide
    tool) holds each to `call_budget`, the run's per-call ceiling
    (`llm_call_budget`; `<= 0` is none), and to the wall left less
    `reserve`, the seconds a reserved finalize turn still needs."""
    campaign: str
    scene_identity: str
    run_id: str
    deadline: float
    root: str
    turn: int = 0
    run: RunView | None = None
    call_budget: float = 0.0
    reserve: float = 0.0


class ToolError(Exception):
    """Raised by a tool to send the model `str(exc)` as an error result. Any
    other exception is sent as a generic error."""


#: A tool's function: sync (run in a worker thread) or async.
ToolFn = Callable[[dict, ToolContext], ToolOutput | Awaitable[ToolOutput]]


@dataclass(frozen=True)
class ToolSpec:
    """One tool a caller declares. `terminal` ends the loop when called: the
    call is handed back to the caller, never executed."""
    name: str
    description: str
    parameters: dict
    fn: ToolFn
    effect: Literal["read", "propose"] = "read"
    terminal: bool = False
    timeout: float = TOOL_TIMEOUT_S
    max_result_chars: int = MAX_RESULT_CHARS

    def definition(self) -> dict:
        """The neutral definition the adapters lower."""
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


@dataclass(frozen=True)
class ToolCall:
    """One call a reply made. `id` is the one the loop sends back: the
    provider's own until a switch of provider (spec 3.11), "" when the
    provider gave none (the loop names it). `arguments` is None when the
    provider's JSON did not parse to an object; `raw_arguments` is what was
    received. `provider_id` is the provider's id, kept for the capture."""
    id: str
    name: str
    arguments: dict | None
    raw_arguments: str
    provider_id: str = ""


#: The caller's executor (spec 3.6).
Execute = Callable[[ToolCall, ToolContext], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class Toolset:
    """The tools one run offers, validated at construction: each a
    `ToolSpec` with a well-formed name, distinct from the others, a string
    description of at most `MAX_DESCRIPTION` characters and parameters inside
    the portable subset; at most `MAX_TOOLS` of them. An empty set is a valid
    value -- the loop refuses to run one (01g-S4)."""
    tools: tuple[ToolSpec, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.tools, tuple):
            raise ValueError("a toolset's tools are a tuple of ToolSpec")
        if len(self.tools) > MAX_TOOLS:
            raise ValueError(f"a toolset holds at most {MAX_TOOLS} tools, not {len(self.tools)}")
        names: set[str] = set()
        for spec in self.tools:
            name = _checked_spec(spec)
            if name in names:
                raise ValueError(f"tool {name!r} is defined twice")
            names.add(name)

    def definitions(self) -> tuple[dict, ...]:
        """The neutral definitions, in order: what `check` and the adapters
        take."""
        return tuple(spec.definition() for spec in self.tools)

    def get(self, name: str) -> ToolSpec | None:
        """The tool called `name`, or None."""
        return next((spec for spec in self.tools if spec.name == name), None)


def _checked_spec(spec: object) -> str:
    """One `ToolSpec` checked (`Toolset`); its name."""
    if not isinstance(spec, ToolSpec):
        raise ValueError("a toolset holds ToolSpec values")
    if spec.effect not in ("read", "propose"):
        raise ValueError(f"tool {spec.name!r}: effect is read or propose, not {spec.effect!r}")
    if not callable(spec.fn):
        raise ValueError(f"tool {spec.name!r} has no function")
    if isinstance(spec.description, str) and len(spec.description) > MAX_DESCRIPTION:
        raise ValueError(f"tool {spec.name!r}: a description is at most "
                         f"{MAX_DESCRIPTION} characters")
    if not spec.timeout > 0 or not spec.max_result_chars > 0:
        raise ValueError(f"tool {spec.name!r}: a timeout and a result size are positive")
    return _checked_name(spec.definition())


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


# ---- arguments against their schema ----
_TYPE_WORDS = {"object": "an object", "array": "an array", "string": "a string",
               "integer": "an integer", "number": "a number", "boolean": "true or false",
               "null": "null"}


def _typed(value: object, kind: object) -> bool:
    """Whether `value` is of the subset's `kind` (an unknown kind is not
    checked). A bool is never a number, and an integer is an `int`."""
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    return True


def violation(value: object, schema: dict, path: str = "arguments") -> str:
    """The first way `value` fails `schema`, a schema inside the portable
    subset (`schemas.check`), as a sentence naming its `path`; "" when it
    conforms. Structural: types, `anyOf`, `enum`, required keys, no extra
    keys (the subset's objects are all closed) and each array item."""
    branches = schema.get("anyOf")
    if isinstance(branches, list):
        if any(isinstance(b, dict) and not violation(value, b, path) for b in branches):
            return ""
        return f"{path}: matches none of its alternatives"
    kind = schema.get("type")
    if not _typed(value, kind):
        return f"{path}: expected {_TYPE_WORDS.get(str(kind), kind)}"
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return f"{path}: not one of the allowed values"
    if kind == "object" and isinstance(value, dict):
        return _object_violation(value, schema, path)
    if kind == "array" and isinstance(value, list):
        items = schema.get("items")
        for i, item in enumerate(value):
            found = violation(item, items, f"{path}[{i}]") if isinstance(items, dict) else ""
            if found:
                return found
    return ""


def _object_violation(value: dict, schema: dict, path: str) -> str:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    for key in schema.get("required") or ():
        if key not in value:
            return f"{path}: missing {key!r}"
    for key in value:
        if key not in properties:
            return f"{path}: unexpected key {key!r}"
    for key, sub in properties.items():
        found = violation(value[key], sub, f"{path}.{key}") if key in value else ""
        if found:
            return found
    return ""


def conforms(value: object, schema: dict) -> bool:
    """Whether `value` conforms to `schema` (`violation` says how not)."""
    return not violation(value, schema)


# ---- definitions per kind ----
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


# ---- tool turns per kind (spec 3.3) ----
def _tool_turn(message: object) -> bool:
    return isinstance(message, dict) and (
        message.get("role") == "tool" or "tool_calls" in message or "_opaque" in message)


def carries_tool_turns(messages: object) -> bool:
    """Whether any message is a neutral tool turn (or carries opaque state)."""
    return isinstance(messages, list) and any(_tool_turn(m) for m in messages)


def _same_source(opaque: object, kind: str, provider_id: str, model: str) -> bool:
    return (isinstance(opaque, dict) and opaque.get("kind") == kind
            and opaque.get("provider_id") == provider_id and opaque.get("model") == model)


def provenanced(messages: list[dict], kind: str, provider_id: str, model: str) -> list[dict]:
    """`messages` with every `_opaque` another `(kind, provider_id, model)`
    wrote dropped (spec 3.4, 3.11 rule 3): provider state is echoed only to
    the provider and model that wrote it. The same list when nothing carries
    any, or when every one is the target's own."""
    if not isinstance(messages, list) or not any(
            isinstance(m, dict) and "_opaque" in m
            and not _same_source(m["_opaque"], kind, provider_id, model) for m in messages):
        return messages
    return [m if not (isinstance(m, dict) and "_opaque" in m)
            or _same_source(m["_opaque"], kind, provider_id, model)
            else {k: v for k, v in m.items() if k != "_opaque"} for m in messages]


def _items(message: dict) -> list[dict]:
    opaque = message.get("_opaque")
    items = opaque.get("items") if isinstance(opaque, dict) else None
    return [dict(i) for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _arguments(call: dict) -> dict:
    arguments = call.get("arguments")
    return arguments if isinstance(arguments, dict) else {}


def _result_text(message: dict) -> str:
    content = message.get("content", "")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def openai_call(call: dict) -> dict:
    """One neutral call as an OpenAI-style `tool_calls` entry."""
    return {"id": call.get("id", ""), "type": "function",
            "function": {"name": call.get("name", ""),
                         "arguments": json.dumps(_arguments(call), ensure_ascii=False)}}


def openai_messages(messages: list[dict], *, reasoning: bool = False) -> list[dict]:
    """The neutral tool turns as OpenAI-style chat completions take them: an
    assistant turn's calls as `function` entries with JSON-string arguments,
    a result as a `tool` message whose `content` carries `ERROR_PREFIX` when
    it is an error (the wire has no flag for one). `_opaque` is never sent as
    itself; with `reasoning` (OpenRouter) its items ride back as the
    assistant turn's `reasoning_details`. The same list when there is no tool
    turn in it."""
    if not carries_tool_turns(messages):
        return messages
    out: list[dict] = []
    for m in messages:
        if not _tool_turn(m):
            out.append(m)
        elif m.get("role") == "tool":
            text = _result_text(m)
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""),
                        "content": ERROR_PREFIX + text if m.get("is_error") else text})
        else:
            lowered = {k: v for k, v in m.items() if k not in ("tool_calls", "_opaque")}
            calls = m.get("tool_calls")
            if isinstance(calls, list) and calls:
                lowered["tool_calls"] = [openai_call(c) for c in calls if isinstance(c, dict)]
            items = _items(m)
            if reasoning and items:
                lowered["reasoning_details"] = items
            out.append(lowered)
    return out


def anthropic_assistant(message: dict, text_blocks: list[dict]) -> list[dict]:
    """An assistant turn's blocks for the Messages API: its own opaque
    thinking blocks first (the API wants them before the `tool_use` they
    led to), then its text, then a `tool_use` block per call."""
    thinking = [i for i in _items(message) if i.get("type") in _THINKING]
    calls = message.get("tool_calls")
    uses = [{"type": "tool_use", "id": c.get("id", ""), "name": c.get("name", ""),
             "input": _arguments(c)}
            for c in (calls if isinstance(calls, list) else ()) if isinstance(c, dict)]
    return [*thinking, *text_blocks, *uses]


def anthropic_result(message: dict) -> dict:
    """A neutral `tool` message as a `tool_result` block."""
    block = {"type": "tool_result", "tool_use_id": message.get("tool_call_id", ""),
             "content": _result_text(message)}
    if message.get("is_error"):
        block["is_error"] = True
    return block


# ---- reading calls out of a stream (spec 3.4) ----
def _parsed(raw: str) -> dict | None:
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _index(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


class Collector:
    """The tool calls one attempt's reply made, its opaque provider state,
    and how it ended."""

    def __init__(self) -> None:
        #: One record per call, in the order the calls began: the provider's
        #: id ("" for none), its name, its argument fragments and an
        #: Anthropic `tool_use` block's starting `input`.
        self._calls: list[dict] = []
        #: OpenAI-style stream index -> the position of the call open at it.
        self._open: dict[int, int] = {}
        #: Anthropic block index -> ("call" | "opaque", position).
        self._blocks: dict[int, tuple[str, int]] = {}
        #: Provider state to echo back: thinking blocks or reasoning details.
        self._opaque: list[dict] = []
        #: A `reasoning_details` entry's own `index` -> its position.
        self._details: dict[int, int] = {}
        self._finish = ""
        self._source = ("", "", "")

    def begin(self, *, kind: str = "", provider_id: str = "", model: str = "") -> None:
        """Forget everything: a new attempt starts (`llm._stamp`), on the
        provider and model named, which is what its opaque state is tagged
        with."""
        self._calls.clear()
        self._open.clear()
        self._blocks.clear()
        self._opaque.clear()
        self._details.clear()
        self._finish = ""
        self._source = (kind, provider_id, model)

    def _find(self, call_id: str) -> int | None:
        if not call_id:
            return None
        return next((n for n, c in enumerate(self._calls) if c["id"] == call_id), None)

    def _start(self, call_id: str, name: str, start: dict | None = None) -> int:
        self._calls.append({"id": call_id, "name": name, "args": [], "input": start})
        return len(self._calls) - 1

    def _fill(self, pos: int, name: str, arguments: str) -> None:
        call = self._calls[pos]
        if name and not call["name"]:
            call["name"] = name
        if arguments:
            call["args"].append(arguments)

    def note(self, call_id: str, name: str, arguments: str = "") -> None:
        """A call the provider began, keyed by its id ("" for none, which is
        always a new call); a known id fills a name still missing and adds
        `arguments` to its fragments."""
        pos = self._find(call_id)
        if pos is None:
            pos = self._start(call_id, "")
        self._fill(pos, name, arguments)

    def openai_fragment(self, fragment: object) -> str:
        """One `choices[0].delta.tool_calls[i]` fragment; the text it carried.

        Keyed by `index` (0 when absent): an id that differs from the call
        open at that index starts a new call -- some servers send every
        parallel call at `index: 0` with distinct ids -- unless the open call
        has no id yet, which adopts it (an id-less first fragment). A
        fragment with no id continues the open call, or starts one."""
        if not isinstance(fragment, dict):
            return ""
        index = _index(fragment.get("index"))
        call_id = _text(fragment.get("id"))
        function = fragment.get("function")
        function = function if isinstance(function, dict) else {}
        name, arguments = _text(function.get("name")), _text(function.get("arguments"))
        open_at = self._open.get(index)
        pos = self._find(call_id)
        if pos is None and call_id and open_at is not None and not self._calls[open_at]["id"]:
            self._calls[open_at]["id"] = call_id
            pos = open_at
        if pos is None:
            pos = open_at if not call_id and open_at is not None else self._start(call_id, "")
        self._fill(pos, name, arguments)
        self._open[index] = pos
        return name + arguments

    def reasoning_detail(self, entry: object) -> None:
        """One `delta.reasoning_details` entry, kept as opaque state. Entries
        naming an `index` already seen are one entry streamed in pieces:
        their string fields concatenate and a field still empty is filled."""
        if not isinstance(entry, dict):
            return
        index = entry.get("index")
        pos = self._details.get(index) if isinstance(index, int) else None
        if pos is None:
            self._opaque.append(dict(entry))
            if isinstance(index, int):
                self._details[index] = len(self._opaque) - 1
            return
        kept = self._opaque[pos]
        for key, value in entry.items():
            if key in _DETAIL_TEXT and isinstance(value, str) and isinstance(kept.get(key), str):
                kept[key] += value
            elif kept.get(key) in (None, "") and value not in (None, ""):
                kept[key] = value

    def anthropic_frame(self, frame: dict) -> str:
        """One `content_block_start` / `content_block_delta` frame; the text
        a call's name or arguments carried. A `tool_use` block is a call and
        its `input_json_delta`s its arguments; a thinking or redacted
        thinking block (and its thinking and signature deltas) is opaque
        state."""
        index = _index(frame.get("index"))
        if frame.get("type") == "content_block_start":
            return self._anthropic_start(index, frame.get("content_block"))
        delta = frame.get("delta")
        slot = self._blocks.get(index)
        if not isinstance(delta, dict) or slot is None:
            return ""
        where, pos = slot
        kind = delta.get("type")
        if where == "call" and kind == "input_json_delta":
            partial = _text(delta.get("partial_json"))
            self._fill(pos, "", partial)
            return partial
        block = self._opaque[pos] if where == "opaque" else None
        if block is not None and kind == "thinking_delta":
            block["thinking"] = _text(block.get("thinking")) + _text(delta.get("thinking"))
        elif block is not None and kind == "signature_delta":
            block["signature"] = _text(block.get("signature")) + _text(delta.get("signature"))
        return ""

    def _anthropic_start(self, index: int, block: object) -> str:
        if not isinstance(block, dict):
            return ""
        kind = block.get("type")
        if kind == "tool_use":
            start = block.get("input")
            name = _text(block.get("name"))
            pos = self._start(_text(block.get("id")), name,
                              start if isinstance(start, dict) else None)
            self._blocks[index] = ("call", pos)
            return name
        if kind == "thinking":
            self._opaque.append({"type": "thinking", "thinking": _text(block.get("thinking")),
                                 "signature": _text(block.get("signature"))})
        elif kind == "redacted_thinking":
            self._opaque.append({"type": "redacted_thinking", "data": _text(block.get("data"))})
        else:
            return ""
        self._blocks[index] = ("opaque", len(self._opaque) - 1)
        return ""

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

    def calls(self) -> tuple[ToolCall, ...]:
        """The calls, in order, their arguments read. Fragments that join to
        a JSON object are `arguments`; anything else is None, with
        `raw_arguments` kept. No fragment at all reads an Anthropic block's
        starting `input` (an object), else `{}`: a call with no arguments on
        either wire. Whitespace alone is `{}` too."""
        out: list[ToolCall] = []
        for call in self._calls:
            raw = "".join(call["args"])
            start = call["input"]
            if raw.strip():
                arguments = _parsed(raw)
            elif isinstance(start, dict) and start:
                arguments, raw = dict(start), json.dumps(start, ensure_ascii=False)
            else:
                arguments = {}
            out.append(ToolCall(id=call["id"], name=call["name"], arguments=arguments,
                                raw_arguments=raw, provider_id=call["id"]))
        return tuple(out)

    def opaque(self) -> dict | None:
        """The provider state this reply asked to have echoed back, tagged
        with the attempt that wrote it (`begin`), or None: what the loop
        stores as the assistant turn's `_opaque`."""
        if not self._opaque:
            return None
        kind, provider_id, model = self._source
        return {"kind": kind, "provider_id": provider_id, "model": model,
                "items": [dict(item) for item in self._opaque]}

    @property
    def finish_reason(self) -> str:
        """`stop`, `tool_calls`, `length` or `other`; "" before the end."""
        return self._finish


def _collector(usage: object) -> Collector | None:
    found = usage.get(KEY) if isinstance(usage, dict) else None
    return found if isinstance(found, Collector) else None


def from_openai_chunk(obj: object, usage: object) -> str:
    """Feed one OpenAI-style SSE chunk's tool-call fragments, reasoning
    details and end reason to the holder's `Collector`, and answer the call
    text it carried -- names and argument fragments, for the local estimate,
    whether or not a collector is there. Reads the first choice, as the prose
    adapters do."""
    if not isinstance(obj, dict):
        return ""
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    choice = choices[0]
    delta = choice.get("delta")
    delta = delta if isinstance(delta, dict) else {}
    fragments = delta.get("tool_calls")
    collector = _collector(usage)
    if collector is None:
        # Nobody is collecting: only the call text is wanted, read through a
        # scratch collector -- and none is built for the ordinary chunk.
        if not isinstance(fragments, list):
            return ""
        collector = Collector()
    said = "".join(collector.openai_fragment(f) for f in fragments) \
        if isinstance(fragments, list) else ""
    details = delta.get("reasoning_details")
    for entry in details if isinstance(details, list) else ():
        collector.reasoning_detail(entry)
    reason = choice.get("finish_reason")
    if isinstance(reason, str) and reason:
        collector.finish(_OPENAI_FINISH.get(reason, "other"))
    return said


def feed_anthropic(usage: object, frame: object) -> str:
    """Feed one Anthropic `content_block_start` / `content_block_delta` frame
    -- the whole frame, for its block `index` -- to the holder's `Collector`,
    and answer the call text it carried (`Collector.anthropic_frame`). With
    no collector there is no block bookkeeping, so only a frame's own
    `partial_json` is answered."""
    if not isinstance(frame, dict):
        return ""
    collector = _collector(usage)
    if collector is not None:
        return collector.anthropic_frame(frame)
    delta = frame.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
        return _text(delta.get("partial_json"))
    block = frame.get("content_block")
    return _text(block.get("name")) if isinstance(block, dict) \
        and block.get("type") == "tool_use" else ""


def anthropic_stop(usage: object, stop_reason: object) -> None:
    """Feed an Anthropic `stop_reason` to the holder's `Collector`."""
    collector = _collector(usage)
    if collector is not None and isinstance(stop_reason, str) and stop_reason:
        collector.finish(_ANTHROPIC_FINISH.get(stop_reason, "other"))


# ---- the loop's shapes (01g-S4; spec 3.7, 3.9, 3.10) ----
#: The shortest window a turn could plausibly answer in: a turn is not sent
#: when the run's wall clock has less than this left (spec 3.9).
MIN_TURN_SECONDS = 5.0

#: The default executor's own bounded thread pool (spec 3.6): never the
#: shared default executor, which also serves httpx's DNS lookups.
TOOL_WORKERS = 4

#: The limits a run can stop on (`LoopResult.limit`). `spend` is 01g-S5's.
LIMITS: tuple[str, ...] = ("turns", "tool_calls", "decisions", "wall", "spend",
                           "result_chars")

#: The results a model is sent for a call the loop did not run as asked.
NOT_RUN = "not run: the run's tool budget is spent"
CUT_OFF = "your arguments were cut off; call again with a shorter request"
TOOL_FAILED = "the tool failed"
TIMED_OUT = "timed out"
CAPACITY = "tool capacity exhausted"

#: How far a counted prompt is scaled up before it is priced (spec 3.9): a
#: local count is not an upper bound -- chars/4 under-counts CJK text, and a
#: cl100k-style encoder under-counts Claude's tokenizer. Structural; to be
#: tuned against real runs.
PROJECTION_MARGIN = 1.5

#: A tool-call id every provider accepts (spec 3.11).
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


@dataclass(frozen=True)
class RunBudget:
    """What one run may spend, checked before every send and execution (spec
    3.9). The defaults are structural and to be tuned against real runs.
    `wall_seconds` None is `llm_call_budget`; `<= 0` is no wall."""
    max_turns: int = 6
    max_tool_calls: int = 12
    max_decisions: int = 2
    wall_seconds: float | None = None
    max_output_tokens: int = 2048
    max_result_chars_total: int = 48_000
    reserve_final: bool = True
    #: The most a run's projected spend may reach, in USD (01g-S5); None is
    #: no spend axis. A projection, never accounting (`inference.price_for`).
    spend_ceiling_usd: float | None = None

    def __post_init__(self) -> None:
        ceiling = self.spend_ceiling_usd
        if ceiling is not None and (isinstance(ceiling, bool)
                                    or not isinstance(ceiling, (int, float))
                                    or not math.isfinite(ceiling) or ceiling < 0):
            raise ValueError("RunBudget.spend_ceiling_usd is a finite amount of at least 0")
        for name in ("max_turns", "max_tool_calls", "max_output_tokens",
                     "max_result_chars_total"):
            if not _positive_int(getattr(self, name)):
                raise ValueError(f"RunBudget.{name} is a positive whole number")
        if isinstance(self.max_decisions, bool) or not isinstance(self.max_decisions, int) \
                or self.max_decisions < 0:
            raise ValueError("RunBudget.max_decisions is a whole number")


@dataclass(frozen=True)
class TraceEntry:
    """One thing a run did (spec 3.10): a model turn, a tool call, a decision
    or the stop -- with refs and sizes, never argument or result text."""
    turn: int
    kind: Literal["model", "tool", "decide", "stop"]
    name: str = ""
    call_id: str = ""
    ok: bool = True
    refs: tuple[str, ...] = ()
    chars: int = 0
    elapsed_ms: int = 0
    note: str = ""


@dataclass(frozen=True)
class LoopResult:
    """How a run ended (spec 3.7). `error` is the `LLMError` a `failed` run
    stopped on."""
    status: Literal["completed", "budget_exhausted", "failed"]
    limit: str = ""
    text: str = ""
    final: dict | None = None
    final_call: ToolCall | None = None
    declined: tuple[ToolCall, ...] = ()
    proposals: tuple[dict, ...] = ()
    messages: tuple[dict, ...] = ()
    trace: tuple[TraceEntry, ...] = ()
    rows: tuple[dict, ...] = ()
    error: Exception | None = None
    run_id: str = ""


@dataclass(frozen=True)
class LoopEvent:
    """One step of a run as it happens: `text` (a turn's delta), `turn_end`
    (`interstitial` for a turn that ended in calls), `tool_start`,
    `tool_end` and, last, `done` with the result."""
    kind: Literal["text", "turn_end", "tool_start", "tool_end", "done"]
    turn: int = 0
    delta: str = ""
    finish: str = ""
    interstitial: bool = False
    call_id: str = ""
    name: str = ""
    ok: bool = True
    chars: int = 0
    refs: tuple[str, ...] = ()
    result: LoopResult | None = None


class RunRefused(Exception):  # noqa: N818 - the spec's name for it (01g 3.9)
    """A run refused before anything is sent (spec 3.9): `kind` says why
    (`unpriceable`), the message what to do about it."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _cut_marker(omitted: int) -> str:
    return f"\n[truncated: {omitted} more characters]"


def truncate(text: str, limit: int) -> str:
    """`text` held to `limit` characters, the marker saying how much was cut
    included: room for it (at the widest count it could carry) comes out of
    the kept text, so the result never passes `limit`. A limit too small for
    the marker keeps the first `limit` characters and no marker -- the cap is
    the promise, the marker a courtesy."""
    limit = max(0, limit)
    if len(text) <= limit:
        return text
    keep = limit - len(_cut_marker(len(text)))
    if keep < 0:
        return text[:limit]
    return text[:keep] + _cut_marker(len(text) - keep)


def loop_id(run_id: str, n: int) -> str:
    """The loop's own call id: `gc_<run8>_<n>`, inside `SAFE_ID`."""
    return f"gc_{re.sub(r'[^A-Za-z0-9]', '', run_id)[:8]}_{n}"


def rewrite_ids(messages: list[dict], mint: Callable[[], str]) -> list[dict]:
    """`messages` with every call id renamed to a fresh `mint()`, call and
    result alike, on new messages; the rest as they were. Renamed per
    occurrence, never by id: a provider may reuse an id from one turn to the
    next, and two calls must not come out sharing one. A result is paired
    with the call of that id in the latest assistant turn before it."""
    out: list[dict] = []
    current: dict[str, str] = {}
    for message in messages:
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        renamed = message
        if isinstance(calls, list):
            current = {}
            fresh = []
            for c in calls:
                if isinstance(c, dict) and c.get("id"):
                    current[c["id"]] = new = mint()
                    fresh.append({**c, "id": new})
                else:
                    fresh.append(c)
            renamed = {**message, "tool_calls": fresh}
        elif (isinstance(message, dict) and message.get("role") == "tool"
              and message.get("tool_call_id") in current):
            renamed = {**message, "tool_call_id": current[message["tool_call_id"]]}
        out.append(renamed)
    return out


class _Workers:
    """A bounded thread pool that says when it is full rather than queueing:
    a thread a timed-out wait abandoned keeps its worker until it returns."""

    def __init__(self, size: int):
        self._pool = ThreadPoolExecutor(max_workers=size, thread_name_prefix="grimoire-tool")
        self._size = size
        self._busy = 0
        self._lock = threading.Lock()

    def submit(self, fn: Callable, *args: object) -> Future | None:
        with self._lock:
            if self._busy >= self._size:
                return None
            self._busy += 1
        future = self._pool.submit(fn, *args)
        future.add_done_callback(self._release)
        return future

    def _release(self, _future: Future) -> None:
        with self._lock:
            self._busy -= 1


def registered(toolset: Toolset, *, workers: int = TOOL_WORKERS) -> Execute:
    """The default executor (spec 3.6): each call dispatched by name to its
    `ToolSpec.fn`. An async tool is awaited; a sync one runs on this
    executor's own bounded pool, and once every worker is held -- by threads
    the loop stopped waiting on -- a call is refused with `CAPACITY` rather
    than queued. The loop bounds the wait; nothing here kills a thread."""
    pool = _Workers(workers)

    async def execute(call: ToolCall, ctx: ToolContext) -> ToolOutput:
        spec = toolset.get(call.name)
        if spec is None:
            raise ToolError(f"there is no tool named {call.name!r}")
        args = call.arguments or {}
        if inspect.iscoroutinefunction(spec.fn):
            return await spec.fn(args, ctx)
        future = pool.submit(spec.fn, args, ctx)
        if future is None:
            raise ToolError(CAPACITY)
        result = await asyncio.wrap_future(future)
        return await result if inspect.isawaitable(result) else result

    return execute


async def text_deltas(events: AsyncIterator[LoopEvent],
                      on_done: Callable[[LoopResult], None] | None = None
                      ) -> AsyncIterator[str]:
    """A loop's events (`inference.stream_tools`) as the plain iterator of
    text deltas `generate(stream=True)` yields -- heartbeats (`""`) included
    -- so a streaming caller keeps its display and its watcher as they are
    (01g-C6). `on_done` is handed the run's result. Closing this closes the
    run."""
    try:
        async for event in events:
            if event.kind == "text":
                yield event.delta
            elif event.kind == "done" and on_done is not None and event.result is not None:
                on_done(event.result)
    finally:
        close = getattr(events, "aclose", None)
        if close is not None:
            await close()
