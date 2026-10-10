"""`grimoire.tool_calls`, as 01g-S1 lands it: the neutral tool definition and
its check, each kind's spelling of a definition and of `tool_choice`, and the
`Collector` that notices which calls a reply began and how it ended.

01g-S2 grows this module (argument accumulation, tool-turn lowering); what is
held here is the part the `tools` capability's probe stands on.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import grimoire
from grimoire import anthropic as anthropic_mod
from grimoire import schemas, tool_calls
from grimoire.openai_compatible import _strict_messages
from grimoire.tool_calls import Collector, ToolCall, Toolset, ToolSpec

PING = {"name": "ping", "description": "Says the caller is here.",
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}}


# ---- the definition ----
def test_check_accepts_a_well_formed_offer():
    tool_calls.check((PING,), "required")
    tool_calls.check((PING,), None)
    tool_calls.check(None, None)


@pytest.mark.parametrize("tools, choice", [
    ((), "auto"),                                         # empty
    ([PING], "auto"),                                     # not a tuple
    (("ping",), "auto"),                                  # not a definition
    (({**PING, "name": "9lives"},), "auto"),              # a bad name
    (({**PING, "name": "x" * 65},), "auto"),              # too long
    ((PING, PING), "auto"),                               # defined twice
    (({**PING, "description": None},), "auto"),           # no description
    ((PING,), "any"),                                     # not a neutral choice
    (None, "required"),                                   # a choice with nothing to choose
])
def test_check_refuses(tools, choice):
    with pytest.raises(ValueError):
        tool_calls.check(tools, choice)


def test_parameters_are_held_to_the_portable_subset():
    """Spec 3.2: a tool's parameters pass `schemas.check` -- strict mode needs
    `required` and `additionalProperties: false` even on an empty object."""
    loose = {**PING, "parameters": {"type": "object", "properties": {}}}
    with pytest.raises(schemas.SchemaError):
        tool_calls.check((loose,), "auto")


def test_the_module_imports_only_the_standard_library_and_schemas():
    tree = ast.parse(Path(grimoire.__file__).with_name("tool_calls.py").read_text("utf-8"))
    local = {(n.module, tuple(a.name for a in n.names)) for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom) and n.level}
    assert local == {(None, ("schemas",))}


# ---- the lowering ----
def test_openai_tools_are_functions():
    assert tool_calls.openai_tools((PING,)) == [{"type": "function", "function": {
        "name": "ping", "description": "Says the caller is here.",
        "parameters": PING["parameters"]}}]


def test_anthropic_tools_carry_an_input_schema():
    assert tool_calls.anthropic_tools((PING,)) == [{
        "name": "ping", "description": "Says the caller is here.",
        "input_schema": PING["parameters"]}]


def test_anthropic_choice_is_a_plain_mapping():
    """`required` is `any` whatever else the request holds: the caller, never
    the lowering, avoids forced tool use beside thinking."""
    assert tool_calls.anthropic_choice("auto") == {"type": "auto"}
    assert tool_calls.anthropic_choice("none") == {"type": "none"}
    assert tool_calls.anthropic_choice("required") == {"type": "any"}


# ---- the collector ----
def _chunk(*fragments, finish=None) -> dict:
    return {"choices": [{"delta": {"tool_calls": list(fragments)}, "finish_reason": finish}]}


def test_fragments_of_one_call_across_chunks_are_one_call():
    usage = {tool_calls.KEY: Collector()}
    tool_calls.from_openai_chunk(_chunk({"index": 0, "id": "call_1",
                                         "function": {"name": "ping", "arguments": ""}}), usage)
    tool_calls.from_openai_chunk(_chunk({"index": 0, "function": {"arguments": "{}"}}), usage)
    tool_calls.from_openai_chunk({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                                 usage)
    found = usage[tool_calls.KEY]
    assert found.called and found.names() == ("ping",)
    assert found.finish_reason == "tool_calls"


def test_two_ids_at_index_zero_are_two_calls():
    collector = Collector()
    collector.openai_fragment({"index": 0, "id": "a", "function": {"name": "ping"}})
    collector.openai_fragment({"index": 0, "function": {"arguments": "{}"}})
    collector.openai_fragment({"index": 0, "id": "b", "function": {"name": "pong"}})
    assert collector.names() == ("ping", "pong")


def test_two_indices_are_two_calls_and_a_late_name_fills_in():
    collector = Collector()
    collector.openai_fragment({"index": 0, "id": "a"})
    collector.openai_fragment({"index": 1, "id": "b", "function": {"name": "pong"}})
    collector.openai_fragment({"index": 0, "function": {"name": "ping"}})
    assert collector.names() == ("ping", "pong")


def test_a_fragment_with_no_id_is_keyed_by_its_index():
    collector = Collector()
    collector.openai_fragment({"function": {"name": "ping"}})
    collector.openai_fragment({"function": {"arguments": "{}"}})
    assert collector.names() == ("ping",)


def test_begin_forgets_the_attempt():
    collector = Collector()
    collector.note("a", "ping")
    collector.finish("tool_calls")
    collector.begin()
    assert not collector.called
    assert collector.names() == () and collector.finish_reason == ""


@pytest.mark.parametrize("raw, want", [("tool_calls", "tool_calls"),
                                       ("function_call", "tool_calls"), ("stop", "stop"),
                                       ("length", "length"), ("content_filter", "other")])
def test_openai_finish_reasons(raw, want):
    usage = {tool_calls.KEY: Collector()}
    tool_calls.from_openai_chunk({"choices": [{"delta": {}, "finish_reason": raw}]}, usage)
    assert usage[tool_calls.KEY].finish_reason == want


@pytest.mark.parametrize("raw, want", [("tool_use", "tool_calls"), ("end_turn", "stop"),
                                       ("stop_sequence", "stop"), ("max_tokens", "length"),
                                       ("refusal", "other")])
def test_anthropic_stop_reasons(raw, want):
    usage = {tool_calls.KEY: Collector()}
    tool_calls.anthropic_stop(usage, raw)
    assert usage[tool_calls.KEY].finish_reason == want


def test_an_anthropic_tool_use_start_frame_is_a_call():
    usage = {tool_calls.KEY: Collector()}
    tool_calls.feed_anthropic(usage, {"type": "content_block_start", "index": 1,
                                      "content_block": {"type": "tool_use", "id": "toolu_1",
                                                        "name": "ping", "input": {}}})
    tool_calls.feed_anthropic(usage, {"type": "content_block_delta", "index": 1,
                                      "delta": {"type": "input_json_delta",
                                                "partial_json": "{}"}})
    tool_calls.feed_anthropic(usage, {"type": "content_block_start", "index": 0,
                                      "content_block": {"type": "text", "text": ""}})
    assert usage[tool_calls.KEY].names() == ("ping",)


@pytest.mark.parametrize("usage", [None, {}, {tool_calls.KEY: "not a collector"}])
def test_the_feeders_write_nothing_without_a_collector(usage):
    before = None if usage is None else dict(usage)
    tool_calls.from_openai_chunk(_chunk({"index": 0, "id": "a"}, finish="tool_calls"), usage)
    tool_calls.feed_anthropic(usage, {"type": "content_block_start",
                                      "content_block": {"type": "tool_use", "id": "t",
                                                        "name": "ping"}})
    tool_calls.anthropic_stop(usage, "tool_use")
    assert usage == before


# ==== 01g-S2: the shapes, conforms, accumulation and tool-turn lowering ====

ARGS = {"type": "object", "properties": {
    "query": {"type": "string"},
    "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "kind": {"type": "string", "enum": ["lore", "cast"]},
    "tags": {"type": "array", "items": {"type": "string"}}},
    "required": ["query", "limit", "kind", "tags"], "additionalProperties": False}


def _read(args, ctx):
    return tool_calls.ToolOutput(text="ok")


def _spec(name="search", **kw) -> ToolSpec:
    return ToolSpec(name=name, description="Searches the library.", parameters=ARGS,
                    fn=_read, **kw)


# ---- Toolset ----
def test_a_toolset_offers_its_definitions_in_order():
    tools = Toolset((_spec("search"), _spec("read", effect="propose")))
    assert [d["name"] for d in tools.definitions()] == ["search", "read"]
    tool_calls.check(tools.definitions(), "auto")
    assert tools.get("read").effect == "propose" and tools.get("nope") is None
    assert Toolset(()).definitions() == ()


@pytest.mark.parametrize("build", [
    lambda: Toolset((_spec("9lives"),)),                                   # bad name
    lambda: Toolset((_spec(), _spec())),                                   # duplicate
    lambda: Toolset(tuple(_spec(f"t{n}") for n in range(tool_calls.MAX_TOOLS + 1))),
    lambda: Toolset((ToolSpec("search", "d", {"type": "object", "properties": {}}, _read),)),
    lambda: Toolset((ToolSpec("search", "x" * (tool_calls.MAX_DESCRIPTION + 1), ARGS, _read),)),
    lambda: Toolset((_spec(effect="write"),)),                             # not read/propose
    lambda: Toolset((_spec(timeout=0),)),
    lambda: Toolset([_spec()]),                                            # not a tuple
    lambda: Toolset(({"name": "search"},)),                                # not a ToolSpec
])
def test_a_toolset_refuses(build):
    with pytest.raises(ValueError):
        build()


def test_the_spec_constants():
    assert (tool_calls.MAX_TOOLS, tool_calls.MAX_DESCRIPTION, tool_calls.MAX_RESULT_CHARS,
            tool_calls.TOOL_TIMEOUT_S) == (32, 1024, 8000, 10.0)


# ---- conforms ----
GOOD = {"query": "Saltmarch", "limit": None, "kind": "lore", "tags": ["harbour"]}


def test_conforming_arguments():
    assert tool_calls.conforms(GOOD, ARGS)
    assert tool_calls.conforms({**GOOD, "limit": 3}, ARGS)
    assert tool_calls.violation(GOOD, ARGS) == ""


@pytest.mark.parametrize("value, needle", [
    ({**GOOD, "query": 3}, "arguments.query: expected a string"),
    ({k: v for k, v in GOOD.items() if k != "kind"}, "missing 'kind'"),
    ({**GOOD, "extra": 1}, "unexpected key 'extra'"),
    ({**GOOD, "kind": "place"}, "arguments.kind: not one of"),
    ({**GOOD, "limit": True}, "arguments.limit: matches none"),      # a bool is no integer
    ({**GOOD, "limit": 2.5}, "arguments.limit: matches none"),
    ({**GOOD, "tags": ["a", 1]}, "arguments.tags[1]: expected a string"),
    ([], "arguments: expected an object"),
])
def test_non_conforming_arguments_name_the_first_problem(value, needle):
    assert not tool_calls.conforms(value, ARGS)
    assert needle in tool_calls.violation(value, ARGS)


# ---- accumulation ----
def test_openai_fragments_accumulate_into_arguments():
    collector = Collector()
    for fragment in ({"index": 0, "id": "call_1", "function": {"name": "search",
                                                               "arguments": ""}},
                     {"index": 0, "function": {"arguments": '{"query": "Salt'}},
                     {"index": 0, "function": {"arguments": 'march"}'}}):
        collector.openai_fragment(fragment)
    (call,) = collector.calls()
    assert call == ToolCall(id="call_1", name="search", arguments={"query": "Saltmarch"},
                            raw_arguments='{"query": "Saltmarch"}', provider_id="call_1")


def test_an_id_after_an_idless_fragment_is_adopted_not_a_second_call():
    """The 01g-S1 handoff: an id-less first fragment, then the id at the same
    index, is one call."""
    collector = Collector()
    collector.openai_fragment({"index": 0, "function": {"name": "search", "arguments": "{"}})
    collector.openai_fragment({"index": 0, "id": "call_9", "function": {"arguments": "}"}})
    (call,) = collector.calls()
    assert (call.id, call.name, call.arguments) == ("call_9", "search", {})


def test_parallel_calls_at_index_zero_keep_their_own_arguments():
    collector = Collector()
    collector.openai_fragment({"index": 0, "id": "a", "function": {"name": "ping",
                                                                   "arguments": '{"n": 1}'}})
    collector.openai_fragment({"index": 0, "id": "b", "function": {"name": "pong",
                                                                   "arguments": '{"n": '}})
    collector.openai_fragment({"index": 0, "function": {"arguments": "2}"}})
    assert [(c.id, c.arguments) for c in collector.calls()] == [("a", {"n": 1}), ("b", {"n": 2})]


@pytest.mark.parametrize("raw, want", [("not json", None), ("[1, 2]", None), ("", {}),
                                       ("  ", {}), ('{"a": 1}', {"a": 1})])
def test_arguments_that_are_not_an_object_read_as_none(raw, want):
    collector = Collector()
    collector.note("c", "ping", raw)
    (call,) = collector.calls()
    assert call.arguments == want and call.raw_arguments == raw


def test_an_idless_call_is_left_for_the_loop_to_name():
    collector = Collector()
    collector.openai_fragment({"function": {"name": "ping", "arguments": "{}"}})
    assert collector.calls()[0].id == "" and collector.calls()[0].provider_id == ""


def test_anthropic_blocks_accumulate_by_index_and_keep_signed_thinking():
    collector = Collector()
    collector.begin(kind="anthropic", provider_id="anthropic-main", model="claude-test-1")
    frames = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "Mara would know."}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "sig-1"}},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "redacted_thinking", "data": "opaque-blob"}},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {}}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": '{"query": '}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": '"Mara"}'}},
    ]
    said = "".join(collector.anthropic_frame(f) for f in frames)
    assert said == 'search{"query": "Mara"}'
    (call,) = collector.calls()
    assert (call.id, call.name, call.arguments) == ("toolu_1", "search", {"query": "Mara"})
    assert collector.opaque() == {
        "kind": "anthropic", "provider_id": "anthropic-main", "model": "claude-test-1",
        "items": [{"type": "thinking", "thinking": "Mara would know.", "signature": "sig-1"},
                  {"type": "redacted_thinking", "data": "opaque-blob"}]}


def test_a_tool_use_start_input_is_read_when_no_fragment_follows():
    collector = Collector()
    collector.anthropic_frame({"type": "content_block_start", "index": 0, "content_block": {
        "type": "tool_use", "id": "t", "name": "ping", "input": {"n": 1}}})
    assert collector.calls()[0].arguments == {"n": 1}


def test_reasoning_details_merge_by_their_own_index():
    usage = {tool_calls.KEY: Collector()}
    usage[tool_calls.KEY].begin(kind="openrouter", provider_id="or", model="vendor/m")
    for entry in ({"type": "reasoning.text", "index": 0, "text": "Wini", "signature": None},
                  {"type": "reasoning.text", "index": 0, "text": "fred", "signature": "s"},
                  {"type": "reasoning.encrypted", "index": 1, "data": "xyz"}):
        tool_calls.from_openai_chunk({"choices": [{"delta": {"reasoning_details": [entry]}}]},
                                     usage)
    assert usage[tool_calls.KEY].opaque()["items"] == [
        {"type": "reasoning.text", "index": 0, "text": "Winifred", "signature": "s"},
        {"type": "reasoning.encrypted", "index": 1, "data": "xyz"}]


def test_begin_forgets_arguments_and_opaque_state():
    collector = Collector()
    collector.note("a", "ping", "{}")
    collector.reasoning_detail({"type": "reasoning.text", "text": "x"})
    collector.begin()
    assert collector.calls() == () and collector.opaque() is None


def test_the_feeders_answer_the_call_text_with_or_without_a_collector():
    chunk = _chunk({"index": 0, "id": "a", "function": {"name": "ping", "arguments": "{}"}})
    assert tool_calls.from_openai_chunk(chunk, {}) == "ping{}"
    assert tool_calls.from_openai_chunk(chunk, {tool_calls.KEY: Collector()}) == "ping{}"
    assert tool_calls.from_openai_chunk({"choices": [{"delta": {"content": "x"}}]}, {}) == ""
    frame = {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": "{}"}}
    assert tool_calls.feed_anthropic({}, frame) == "{}"


# ---- lowering (spec 3.3) ----
HISTORY = [
    {"role": "system", "content": "You are the archivist."},
    {"role": "user", "content": "Who keeps the Saltmarch ledger?"},
    {"role": "assistant", "content": "Let me check.",
     "tool_calls": [{"id": "call_1", "name": "search", "arguments": {"query": "ledger"}},
                    {"id": "call_2", "name": "read", "arguments": {}}],
     "_opaque": {"kind": "openrouter", "provider_id": "or", "model": "vendor/m",
                 "items": [{"type": "reasoning.encrypted", "data": "xyz"}]}},
    {"role": "tool", "tool_call_id": "call_1", "name": "search", "content": "Mara.",
     "is_error": False},
    {"role": "tool", "tool_call_id": "call_2", "name": "read", "content": "no such record",
     "is_error": True},
]


def test_a_history_without_tool_turns_is_the_same_list():
    plain = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert tool_calls.openai_messages(plain) is plain
    assert tool_calls.openai_messages(plain, reasoning=True) is plain
    assert tool_calls.provenanced(plain, "openrouter", "or", "m") is plain
    _system, turns = anthropic_mod._messages(plain)
    assert turns == [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
                     {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}]


def test_openai_lowering_round_trips_a_collected_call():
    """A call read off the stream, stored as the neutral turn, goes back out
    in the wire's own shape with the same id, name and arguments."""
    collector = Collector()
    collector.openai_fragment({"index": 0, "id": "call_1", "function": {
        "name": "search", "arguments": '{"query": "ledger"}'}})
    (call,) = collector.calls()
    turn = {"role": "assistant", "content": "",
            "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments}]}
    (lowered,) = tool_calls.openai_messages([turn])
    (sent,) = lowered["tool_calls"]
    assert sent == {"id": "call_1", "type": "function",
                    "function": {"name": "search", "arguments": '{"query": "ledger"}'}}
    assert json.loads(sent["function"]["arguments"]) == call.arguments


def test_openai_lowering_of_the_whole_history():
    out = tool_calls.openai_messages(HISTORY, reasoning=True)
    assert out[:2] == HISTORY[:2]
    assistant = out[2]
    assert assistant["content"] == "Let me check." and "_opaque" not in assistant
    assert [c["function"]["name"] for c in assistant["tool_calls"]] == ["search", "read"]
    assert assistant["tool_calls"][1]["function"]["arguments"] == "{}"
    assert assistant["reasoning_details"] == [{"type": "reasoning.encrypted", "data": "xyz"}]
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": "Mara."}
    assert out[4] == {"role": "tool", "tool_call_id": "call_2",
                      "content": "ERROR: no such record"}
    # An endpoint that is not OpenRouter is never sent the reasoning details.
    assert "reasoning_details" not in tool_calls.openai_messages(HISTORY)[2]
    assert HISTORY[2]["tool_calls"][0]["arguments"] == {"query": "ledger"}   # not mutated


def test_opaque_state_reaches_only_its_own_provider_and_model():
    same = tool_calls.provenanced(HISTORY, "openrouter", "or", "vendor/m")
    assert same is HISTORY
    for other in (("openrouter", "or", "vendor/other"), ("openrouter", "or-2", "vendor/m"),
                  ("anthropic", "or", "vendor/m")):
        dropped = tool_calls.provenanced(HISTORY, *other)
        assert "_opaque" not in dropped[2] and dropped[2]["tool_calls"] == HISTORY[2]["tool_calls"]
        assert "_opaque" in HISTORY[2]


def test_strict_folding_keeps_tool_messages_in_place():
    lowered = tool_calls.openai_messages(
        [*HISTORY, {"role": "system", "content": "Answer briefly."},
         {"role": "assistant", "content": "Mara keeps it."}])
    folded = _strict_messages(lowered)
    assert [m["role"] for m in folded] == ["user", "assistant", "tool", "tool", "user",
                                          "assistant"]
    assert folded[0]["content"] == ("You are the archivist.\n\n"
                                    "Who keeps the Saltmarch ledger?")
    assert folded[1]["tool_calls"] == lowered[2]["tool_calls"]
    assert folded[2]["tool_call_id"] == "call_1" and folded[3]["tool_call_id"] == "call_2"
    assert folded[4]["content"] == "Answer briefly."


def test_strict_folding_carries_a_plain_assistant_turn_into_the_calls():
    lowered = tool_calls.openai_messages([
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hm."},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c", "name": "ping", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "c", "content": "pong"}])
    folded = _strict_messages(lowered)
    assert [m["role"] for m in folded] == ["user", "assistant", "tool"]
    assert folded[1]["content"] == "Hm." and folded[1]["tool_calls"][0]["id"] == "c"


def test_anthropic_lowering_builds_tool_use_and_a_result_turn_with_system_after():
    history = [
        {"role": "user", "content": "Who keeps the ledger?"},
        {"role": "assistant", "content": "Let me check.",
         "tool_calls": [{"id": "toolu_1", "name": "search", "arguments": {"query": "ledger"}},
                        {"id": "toolu_2", "name": "read", "arguments": {}}],
         "_opaque": {"kind": "anthropic", "provider_id": "a", "model": "claude-test-1",
                     "items": [{"type": "thinking", "thinking": "Hm.", "signature": "s"}]}},
        {"role": "system", "content": "Results follow."},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "Mara."},
        {"role": "tool", "tool_call_id": "toolu_2", "content": "gone", "is_error": True},
        {"role": "user", "content": "And?"},
    ]
    system, turns = anthropic_mod._messages(history)
    assert system == ""
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[1]["content"] == [
        {"type": "thinking", "thinking": "Hm.", "signature": "s"},
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"query": "ledger"}},
        {"type": "tool_use", "id": "toolu_2", "name": "read", "input": {}}]
    assert turns[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Mara."},
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "gone", "is_error": True},
        {"type": "text", "text": "Results follow."},
        {"type": "text", "text": "And?"}]


def test_anthropic_lowering_round_trips_a_collected_call():
    collector = Collector()
    for frame in ({"type": "content_block_start", "index": 0, "content_block": {
                      "type": "tool_use", "id": "toolu_7", "name": "search", "input": {}}},
                  {"type": "content_block_delta", "index": 0, "delta": {
                      "type": "input_json_delta", "partial_json": '{"query": "Realm"}'}}):
        collector.anthropic_frame(frame)
    (call,) = collector.calls()
    _system, turns = anthropic_mod._messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments}]}])
    assert turns[1]["content"] == [{"type": "tool_use", "id": "toolu_7", "name": "search",
                                    "input": {"query": "Realm"}}]


def test_truncation_keeps_its_marker_inside_the_limit():
    text = "z" * 12345
    for limit in (40, 100, 12344):
        cut = tool_calls.truncate(text, limit)
        assert len(cut) <= limit
        kept, _, tail = cut.partition("\n[truncated: ")
        assert tail == f"{len(text) - len(kept)} more characters]"
    assert tool_calls.truncate(text, len(text)) == text
    # A limit too small for the marker keeps what fits, and no marker.
    assert tool_calls.truncate(text, 10) == "z" * 10
    assert tool_calls.truncate(text, 0) == "" == tool_calls.truncate(text, -3)
