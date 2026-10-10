"""`inference.generate`, the one door to generation (spec 7.2, slice I ruling 8).

It takes the call site's resolution, refuses one for another task or
operation, or one that resolved nothing, before any client call, and hands the
facade the resolution's chain of targets (`resolved.chain`, Task 9d) --
positionally, with `schema=` only when one is given. `note_outcome` files a
ceiling's timeout against the resolution's primary target, and `for_task`
relabels a resolution for a sibling task on its own route.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import fields

import pytest

from grimoire import inference, llm, llm_reasoning, model_guidance, schemas, store, wire
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.store.inference.capabilities import NO, UNKNOWN, YES, Cap
from grimoire.store.inference.resolved import ResolvedInference
from tests import wire_kit
from tests.llm_fakes import FakeLLM, SequencedProvider

CONN = wire_kit.target(provider_id="openrouter", model="vendor/active", api_key="sk-test")
#: `CONN` with a fallback riding it, as a resolution's chain carries one.
SPARE = wire_kit.target(provider_id="spare", kind="openai_compatible", model="local/spare",
                        api_key="sk-spare", base_url="http://localhost:1234/v1")
FALLING_BACK = wire.Chain(CONN, SPARE)

#: A generation's schema (01f): small, closed, inside the portable subset.
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title"],
          "properties": {"title": {"type": "string", "description": "the scene's label"}}}


def _prompt(schema: dict = SCHEMA) -> list[dict]:
    """A prompt that carries `schema` as the templates render it."""
    return [{"role": "system", "content": f"Reply with JSON matching:\n{schemas.render(schema)}"},
            {"role": "user", "content": "Mara reaches the harbour."}]


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


def test_a_stream_yields_the_facades_deltas_and_sends_the_chain():
    fake = FakeLLM([["The tide ", "turns."]])
    holder: dict = {}
    resolved = wire_kit.resolution(CONN)
    deltas = _drain(inference.generate("chat", [{"role": "user", "content": "go"}],
                                       client=fake, resolved=resolved, usage=holder))
    assert deltas == ["The tide ", "turns."]
    assert fake.calls == 1
    assert fake.requests[0]["chain"] == resolved.chain
    assert fake.target == CONN
    assert fake.requests[0]["messages"] == [{"role": "user", "content": "go"}]


def test_the_chain_carries_the_fallback_the_resolution_attaches():
    fake = FakeLLM([["Mara nods."]])
    resolved = wire_kit.resolution(FALLING_BACK)
    _drain(inference.generate("chat", [], client=fake, resolved=resolved))
    chain = fake.requests[0]["chain"]
    assert chain == FALLING_BACK
    assert chain.fallback is not None and chain.fallback.provider_id == "spare"


def test_a_joined_generation_completes_with_no_schema():
    fake = FakeLLM([["Seraphine ", "shrugs."]])
    text = asyncio.run(inference.generate("tagline", [], client=fake,
                                          resolved=wire_kit.resolution(CONN, "tagline"),
                                          stream=False))
    assert text == "Seraphine shrugs."
    assert fake.schemas == [None] and fake.retries == [None]


def test_a_schema_is_passed_through():
    fake = FakeLLM([["{}"]])
    asyncio.run(inference.generate("chat", _prompt(), client=fake,
                                   resolved=wire_kit.resolution(CONN), schema=SCHEMA,
                                   stream=False))
    assert fake.schemas == [SCHEMA]


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
    inference.note_outcome(fake, wire_kit.resolution(FALLING_BACK), error)
    # The primary's target alone: never the chain, never the fallback.
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


# ---- structured generation (01f) ----

def _capable(resolved: ResolvedInference, *values: str) -> ResolvedInference:
    """`resolved` with each attempt's `structured_output` resolved to the
    value given for it, in attempt order."""
    attempts = tuple(dataclasses.replace(a, capabilities={"structured_output": Cap(v, "catalog")})
                     for a, v in zip(resolved.attempts, values, strict=True))
    return dataclasses.replace(resolved, attempts=attempts)


#: The fallback, on OpenRouter too, so one provider double answers both.
OTHER = wire_kit.target(provider_id="spare", model="vendor/spare", api_key="sk-spare")


def _refused_schema() -> LLMError:
    return LLMError("bad_response",
                    "response_format: json_schema strict mode is not supported", status=400)


def _real(provider, seen: list | None = None) -> LLMClient:
    observer = None if seen is None else (
        lambda target, err: seen.append((target.model, err.kind if err else None)))
    return LLMClient(openrouter=provider, timeout=0, retries=0, observer=observer)


def _schema_sent(provider) -> list[tuple[str, bool]]:
    return [(r["model"], "schema" in r["kwargs"]) for r in provider.requests]


@pytest.mark.parametrize(("values", "flags"), [
    ((YES, YES), [True, True]), ((YES, UNKNOWN), [True, False]),
    ((NO, YES), [False, True]), ((UNKNOWN, NO), [False, False])])
def test_a_schema_flags_exactly_the_capable_attempts(values, flags):
    resolved = _capable(wire_kit.resolution(wire.Chain(CONN, OTHER)), *values)
    before = resolved.chain
    fake = FakeLLM([["{}"]])
    asyncio.run(inference.generate("chat", _prompt(), client=fake, resolved=resolved,
                                   schema=SCHEMA, stream=False))
    sent = fake.requests[0]["chain"]
    assert [t.structured for t in sent.attempts] == flags
    assert sent == inference.call_chain(resolved, schema=SCHEMA)
    # New targets per call: the resolution's own are what they were.
    assert resolved.chain == before
    assert not any(t.structured for t in resolved.chain.attempts)
    assert [dataclasses.replace(t, structured=False) for t in sent.attempts] == list(
        before.attempts)


def test_call_chain_without_a_schema_is_the_resolutions():
    resolved = _capable(wire_kit.resolution(wire.Chain(CONN, OTHER)), YES, YES)
    assert inference.call_chain(resolved) == resolved.chain
    unflagged = _capable(wire_kit.resolution(CONN), NO)
    assert inference.call_chain(unflagged, schema=SCHEMA) == unflagged.chain
    with pytest.raises(ValueError, match="resolved to no connection"):
        inference.call_chain(_nothing(), schema=SCHEMA)


def test_the_capable_attempt_is_sent_the_envelope_and_no_schema_sends_none():
    provider = SequencedProvider([["{}"]])
    resolved = _capable(wire_kit.resolution(CONN), YES)
    asyncio.run(inference.generate("chat", _prompt(), client=_real(provider),
                                   resolved=resolved, schema=SCHEMA, stream=False))
    asyncio.run(inference.generate("chat", _prompt(), client=_real(provider),
                                   resolved=resolved, stream=False))
    assert _schema_sent(provider) == [("vendor/active", True), ("vendor/active", False)]
    assert provider.requests[0]["kwargs"]["schema"] == SCHEMA


def test_a_flagged_target_sent_no_schema_is_sent_unflagged():
    """The facade invariant (01f, 3.4): "flagged" means "sent the envelope",
    so a flagged target handed no schema is sent unflagged -- and a 400
    naming `response_format` is that attempt's own failure, not the mode
    refused."""
    flagged = dataclasses.replace(CONN, structured=True)
    assert llm._structured_share(flagged)
    provider = SequencedProvider([_refused_schema()])
    holder: dict = {}
    with pytest.raises(LLMError) as exc:
        asyncio.run(_real(provider).complete([{"role": "user", "content": "x"}],
                                             wire.Chain(flagged), holder))
    assert not isinstance(exc.value, llm.SchemaRefusalError)
    assert "schema" not in provider.requests[0]["kwargs"]
    sent = holder[llm.ATTEMPTED]
    assert sent.structured is False and llm._structured_share(sent) == {}


def _tailed() -> model_guidance.PreparedMessages:
    prepared = model_guidance.PreparedMessages("vendor/active", lambda model: (_prompt(), None))
    return prepared.with_tails({"prefill": [{"role": "assistant", "content": "{"}],
                                "instruct": [{"role": "user", "content": "go on"}]},
                               lambda target: "instruct", CONN)


@pytest.mark.parametrize(("schema", "messages", "match"), [
    ({"type": "object"}, _prompt(), "properties"),
    ({**SCHEMA, "properties": {"title": {"type": "string", "maxLength": 9}}}, _prompt(), "bound"),
    (SCHEMA, [{"role": "user", "content": "no schema here"}], "must be in the prompt"),
    (SCHEMA, [{"role": "assistant", "content": schemas.render(SCHEMA)}],
     "must be in the prompt"),
    (SCHEMA, [*_prompt(), {"role": "assistant", "content": "{"}], "assistant turn"),
    (SCHEMA, None, "per-attempt tails"),
])
@pytest.mark.parametrize("stream", [True, False])
def test_a_schema_call_it_cannot_send_is_refused_before_any_client_call(schema, messages,
                                                                        match, stream):
    fake = FakeLLM([["never sent"]])
    holder: dict = {}
    with pytest.raises(ValueError, match=match):
        inference.generate("chat", _tailed() if messages is None else messages, client=fake,
                           resolved=wire_kit.resolution(CONN), usage=holder, schema=schema,
                           stream=stream)
    assert fake.calls == 0 and holder == {}


def test_a_schema_spelled_by_tojson_is_not_the_one_the_check_reads():
    """One renderer on both sides (01f, 3.3): `tojson` escapes the
    apostrophe, so a template that spells the schema with it fails loudly."""
    from jinja2 import Environment
    tojson = Environment().from_string("{{ s | tojson(indent=2) }}").render(
        s={**SCHEMA, "properties": {"title": {"type": "string",
                                              "description": "the scene's label"}}})
    with pytest.raises(ValueError, match="schema_json"):
        inference.generate("chat", [{"role": "system", "content": tojson}],
                           client=FakeLLM([["x"]]), resolved=wire_kit.resolution(CONN),
                           schema=SCHEMA, stream=False)


def test_the_schema_may_ride_a_text_part():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": f"Answer with:\n{schemas.render(SCHEMA)}"}]}]
    fake = FakeLLM([["{}"]])
    asyncio.run(inference.generate("chat", messages, client=fake,
                                   resolved=wire_kit.resolution(CONN), schema=SCHEMA,
                                   stream=False))
    assert fake.calls == 1


def test_a_refused_primary_is_answered_by_its_resend():
    seen: list = []
    provider = SequencedProvider([_refused_schema(), ['{"title": "Dawn"}']])
    holder: dict = {}
    resolved = _capable(wire_kit.resolution(CONN), YES)
    text = asyncio.run(inference.generate("chat", _prompt(), client=_real(provider, seen),
                                          resolved=resolved, usage=holder, schema=SCHEMA,
                                          stream=False))
    assert text == '{"title": "Dawn"}'
    assert _schema_sent(provider) == [("vendor/active", True), ("vendor/active", False)]
    assert provider.requests[0]["messages"] == provider.requests[1]["messages"]
    # One holder: the attempt that answered, counting both attempts.
    assert holder["attempts"] == 2 and holder[llm.ATTEMPTED].structured is False
    # The refusal is never a health failure; the answer is observed.
    assert seen == [("vendor/active", None)]


def test_a_fallback_that_refuses_after_the_primary_failed_is_resent():
    seen: list = []
    provider = SequencedProvider([LLMError("network", "connection reset"),
                                  _refused_schema(), ['{"title": "Dawn"}']])
    holder: dict = {}
    resolved = _capable(wire_kit.resolution(wire.Chain(CONN, OTHER)), NO, YES)
    text = asyncio.run(inference.generate("chat", _prompt(), client=_real(provider, seen),
                                          resolved=resolved, usage=holder, schema=SCHEMA,
                                          stream=False))
    assert text == '{"title": "Dawn"}'
    assert _schema_sent(provider) == [("vendor/active", False), ("vendor/spare", True),
                                      ("vendor/spare", False)]
    assert holder["attempts"] == 3 and holder["model"] == "vendor/spare"
    assert seen == [("vendor/active", "network"), ("vendor/spare", None)]


def test_both_refusing_are_each_resent_and_compose_their_failures():
    seen: list = []
    first, second = LLMError("network", "reset again"), LLMError("timeout", "too slow")
    provider = SequencedProvider([_refused_schema(), _refused_schema(), first, second])
    holder: dict = {}
    resolved = _capable(wire_kit.resolution(wire.Chain(CONN, OTHER)), YES, YES)
    with pytest.raises(LLMError) as exc:
        asyncio.run(inference.generate("chat", _prompt(), client=_real(provider, seen),
                                       resolved=resolved, usage=holder, schema=SCHEMA,
                                       stream=False))
    assert _schema_sent(provider) == [("vendor/active", True), ("vendor/spare", True),
                                      ("vendor/active", False), ("vendor/spare", False)]
    assert not isinstance(exc.value, llm.SchemaRefusalError)
    assert exc.value.kind == "network" and exc.value.words == (first, second)
    assert "reset again" in exc.value.detail and "too slow" in exc.value.detail
    assert holder["attempts"] == 4
    assert seen == [("vendor/active", "network"), ("vendor/spare", "timeout")]


def test_both_refusing_answer_from_the_second_resend():
    provider = SequencedProvider([_refused_schema(), _refused_schema(),
                                  LLMError("network", "reset"), ['{"title": "Dawn"}']])
    resolved = _capable(wire_kit.resolution(wire.Chain(CONN, OTHER)), YES, YES)
    text = asyncio.run(inference.generate("chat", _prompt(), client=_real(provider),
                                          resolved=resolved, schema=SCHEMA, stream=False))
    assert text == '{"title": "Dawn"}'
    assert [r["model"] for r in provider.requests][-1] == "vendor/spare"


def test_a_generate_call_files_one_row_counting_every_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    provider = SequencedProvider([_refused_schema(), ['{"title": "Dawn"}']])
    resolved = _capable(wire_kit.resolution(CONN, "intent"), YES)

    async def go():
        with store.usage.meter("intent", campaign="realm") as m:
            return await inference.generate("intent", _prompt(), client=_real(provider),
                                            resolved=resolved, usage=m.usage, schema=SCHEMA,
                                            stream=False)

    assert asyncio.run(go()) == '{"title": "Dawn"}'
    (row,) = store.usage.calls(days=1)
    assert (row["task"], row["status"], row["attempts"]) == ("intent", "ok", 2)


def test_the_streamed_path_resends_and_yields_only_the_resends_text():
    provider = SequencedProvider([_refused_schema(), ['{"title": ', '"Dawn"}']])
    holder: dict = {}
    resolved = _capable(wire_kit.resolution(CONN), YES)
    deltas = _drain(inference.generate("chat", _prompt(), client=_real(provider),
                                       resolved=resolved, usage=holder, schema=SCHEMA))
    assert "".join(deltas) == '{"title": "Dawn"}'
    assert _schema_sent(provider) == [("vendor/active", True), ("vendor/active", False)]
    assert holder["attempts"] == 2


def test_the_streamed_path_composes_a_failed_resend():
    again = LLMError("network", "reset")
    provider = SequencedProvider([_refused_schema(), again])
    resolved = _capable(wire_kit.resolution(CONN), YES)
    with pytest.raises(LLMError) as exc:
        _drain(inference.generate("chat", _prompt(), client=_real(provider),
                                  resolved=resolved, schema=SCHEMA))
    assert exc.value is again


def test_another_failure_is_not_resent():
    refused = LLMError("bad_response", "vendor/active is not a valid model ID", status=400)
    provider = SequencedProvider([refused, ['{"title": "Dawn"}']])
    resolved = _capable(wire_kit.resolution(CONN), YES)
    with pytest.raises(LLMError):
        asyncio.run(inference.generate("chat", _prompt(), client=_real(provider),
                                       resolved=resolved, schema=SCHEMA, stream=False))
    assert len(provider.requests) == 1
