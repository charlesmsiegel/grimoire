"""Provider-neutral tool definitions, callable contracts, and schema checks."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from .. import schemas

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
    #: The id of the call this context runs, as the loop's trace entry for it
    #: names it: what lets a caller tie a record of its own to that entry.
    call_id: str = ""


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
