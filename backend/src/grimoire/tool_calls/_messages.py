"""Provider tool-turn spelling and per-holder streamed call accumulation."""

from __future__ import annotations

import json

from . import _definitions
from ._definitions import ToolCall as ToolCall

_OPENAI_FINISH = {"tool_calls": "tool_calls", "function_call": "tool_calls",
                  "stop": "stop", "length": "length"}
_ANTHROPIC_FINISH = {"tool_use": "tool_calls", "end_turn": "stop",
                     "stop_sequence": "stop", "max_tokens": "length"}

#: The Anthropic block types kept as opaque state, to be echoed back.
_THINKING = ("thinking", "redacted_thinking")

#: The string fields of a `reasoning_details` entry that stream in pieces.
_DETAIL_TEXT = ("text", "summary", "data")


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
                        "content": _definitions.ERROR_PREFIX + text if m.get("is_error") else text})
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
        self._finish = reason if reason in _definitions.FINISHES else "other"

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
            out.append(_definitions.ToolCall(id=call["id"], name=call["name"], arguments=arguments,
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
    found = usage.get(_definitions.KEY) if isinstance(usage, dict) else None
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
