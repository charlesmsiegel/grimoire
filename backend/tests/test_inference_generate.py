"""`inference.generate`, the one door to generation (spec 7.2, slice I ruling 8).

It takes the call site's resolution, refuses one for another task or
operation, or one that resolved nothing, before any client call, and hands the
facade the dict it was always handed (`resolved.conn`) -- positionally, with
`schema=` only when one is given, so a fake sees the request it always saw.
`note_outcome` files a ceiling's timeout against the resolution's primary,
and `for_task` relabels a resolution for a sibling task on its own route.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields

import pytest

from grimoire import inference, llm_reasoning
from grimoire.llm_errors import LLMError
from grimoire.store.inference.resolved import ResolvedInference
from tests import wire_kit
from tests.llm_fakes import FakeLLM

CONN = {"id": "openrouter", "kind": "openrouter", "model": "vendor/active",
        "api_key": "sk-test"}


def _nothing() -> ResolvedInference:
    """A resolution of `chat` that resolved nothing (no attempts): what the
    resolver answers when no connection serves the route."""
    chat = wire_kit.resolution(CONN)
    return ResolvedInference(**{**{f.name: getattr(chat, f.name) for f in fields(chat)},
                                "attempts": ()})


def _drain(agen) -> list[str]:
    async def go():
        return [delta async for delta in agen]
    return asyncio.run(go())


def test_a_stream_yields_the_facades_deltas_and_sends_the_dict():
    fake = FakeLLM([["The tide ", "turns."]])
    holder: dict = {}
    deltas = _drain(inference.generate("chat", [{"role": "user", "content": "go"}],
                                       client=fake, resolved=wire_kit.resolution(CONN),
                                       usage=holder))
    assert deltas == ["The tide ", "turns."]
    assert fake.calls == 1
    assert fake.conn is CONN
    assert fake.requests[0]["messages"] == [{"role": "user", "content": "go"}]


def test_a_joined_generation_completes_with_no_schema():
    fake = FakeLLM([["Seraphine ", "shrugs."]])
    text = asyncio.run(inference.generate("tagline", [], client=fake,
                                          resolved=wire_kit.resolution(CONN, "tagline"),
                                          stream=False))
    assert text == "Seraphine shrugs."
    assert fake.schemas == [None] and fake.retries == [None]


def test_a_schema_is_passed_through():
    fake = FakeLLM([["{}"]])
    schema = {"type": "object"}
    asyncio.run(inference.generate("chat", [], client=fake,
                                   resolved=wire_kit.resolution(CONN), schema=schema,
                                   stream=False))
    assert fake.schemas == [schema]


@pytest.mark.parametrize(("resolved", "match"), [
    (wire_kit.resolution(CONN, "retry"), "a resolution of 'retry' cannot generate 'chat'"),
    (wire_kit.resolution(CONN, "chat", operation="decide"),
     "a 'decide' resolution cannot generate"),
    (_nothing(), "'chat' resolved to no connection"),
])
@pytest.mark.parametrize("stream", [True, False])
def test_a_resolution_it_cannot_send_is_refused_before_any_client_call(resolved, match,
                                                                       stream):
    fake = FakeLLM([["never sent"]])
    holder: dict = {}
    with pytest.raises(ValueError, match=match):
        inference.generate("chat", [], client=fake, resolved=resolved, usage=holder,
                           stream=stream)
    assert fake.calls == 0 and holder == {}


def test_note_outcome_files_against_the_primary():
    fake = FakeLLM([["x"]])
    error = LLMError("timeout", "the reply did not finish")
    inference.note_outcome(fake, wire_kit.resolution(CONN), error)
    assert fake.noted == [(CONN, error)]
    inference.note_outcome(fake, _nothing(), error)
    assert len(fake.noted) == 1


def test_for_task_relabels_a_sibling_on_the_same_route():
    chat = wire_kit.resolution(CONN, "chat")
    assert inference.for_task(chat, "chat") is chat
    director = inference.for_task(chat, "director")
    assert (director.task, director.route, director.attempts) == (
        "director", chat.route, chat.attempts)
    assert type(director) is type(chat)
    fake = FakeLLM([["Mara waits."]])
    assert _drain(inference.generate("director", [], client=fake,
                                     resolved=director)) == ["Mara waits."]


@pytest.mark.parametrize("task", ["tagline", "scene-break", "no-such-task"])
def test_for_task_refuses_another_route(task):
    with pytest.raises(ValueError, match="is not on the route"):
        inference.for_task(wire_kit.resolution(CONN, "chat"), task)


def test_the_reasoning_stream_installs_its_buffer_before_it_starts():
    holder: dict = {}
    seen = []

    async def source():
        yield "Hello"

    def start():
        seen.append(isinstance(holder.get(llm_reasoning.KEY), llm_reasoning.Buffer))
        return source()

    events = _drain(llm_reasoning.stream(holder, start))
    assert seen == [True]
    assert events == [{"delta": "Hello"}]
    assert llm_reasoning.KEY not in holder


def test_a_start_that_refuses_takes_the_buffer_back_off():
    holder: dict = {}

    def start():
        raise ValueError("a resolution of 'retry' cannot generate 'chat'")

    with pytest.raises(ValueError):
        _drain(llm_reasoning.stream(holder, start))
    assert holder == {}
