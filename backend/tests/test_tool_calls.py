"""`grimoire.tool_calls`, as 01g-S1 lands it: the neutral tool definition and
its check, each kind's spelling of a definition and of `tool_choice`, and the
`Collector` that notices which calls a reply began and how it ended.

01g-S2 grows this module (argument accumulation, tool-turn lowering); what is
held here is the part the `tools` capability's probe stands on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import grimoire
from grimoire import schemas, tool_calls
from grimoire.tool_calls import Collector

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
