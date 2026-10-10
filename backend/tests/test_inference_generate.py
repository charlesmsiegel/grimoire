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
import json
from dataclasses import fields, replace

import pytest

import grimoire.store as store
from grimoire import inference, llm, llm_reasoning, model_guidance, schemas, wire
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.store.inference.capabilities import Cap
from grimoire.store.inference.resolved import ResolvedInference
from tests import wire_kit
from tests.llm_fakes import FakeLLM, SequencedProvider

CONN = wire_kit.target(provider_id="openrouter", model="vendor/active", api_key="sk-test")
#: `CONN` with a fallback riding it, as a resolution's chain carries one.
SPARE = wire_kit.target(provider_id="spare", kind="openai_compatible", model="local/spare",
                        api_key="sk-spare", base_url="http://localhost:1234/v1")
FALLING_BACK = wire.Chain(CONN, SPARE)

#: A schema inside the portable subset (01f-C3).
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title"],
          "properties": {"title": {"type": "string", "description": "Mara's scene"}}}


def _carrying(schema: dict, *, where: str = "system") -> list[dict]:
    """A prompt whose `where` message carries `schema` as `schema_json`
    renders it -- the one spelling `generate` checks for (spec 3.3)."""
    body = f"Reply with JSON matching:\n{schemas.render(schema)}"
    if where == "system":
        return [{"role": "system", "content": body},
                {"role": "user", "content": "Seraphine waits at Saltmarch."}]
    return [{"role": "system", "content": "Answer as JSON."},
            {"role": "user", "content": [{"type": "text", "text": body}]}]


def _with_caps(resolved, *values: str):
    """`resolved` with each attempt's `structured_output` set to `values[i]`
    (the capability `structured_capable` reads)."""
    attempts = tuple(replace(a, capabilities={"structured_output": Cap(v, "catalog")})
                     for a, v in zip(resolved.attempts, values, strict=True))
    return replace(resolved, attempts=attempts)


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
    asyncio.run(inference.generate("chat", _carrying(SCHEMA), client=fake,
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


# ---- 01f-S2: structured mode on generate, per attempt (spec 3.2, 3.3) ----
@pytest.mark.parametrize("stream", [True, False])
def test_a_schema_flags_exactly_the_attempts_known_to_take_it(stream):
    resolved = _with_caps(wire_kit.resolution(FALLING_BACK), "yes", "unknown")
    before = [a.target for a in resolved.attempts]
    fake = FakeLLM([["{}"]])
    got = inference.generate("chat", _carrying(SCHEMA), client=fake, resolved=resolved,
                             schema=SCHEMA, stream=stream)
    _drain(got) if stream else asyncio.run(got)
    sent = fake.requests[0]["chain"]
    assert (sent.primary.structured, sent.fallback.structured) == (True, False)
    # Otherwise the targets the resolution holds, and those are untouched.
    assert replace(sent.primary, structured=False) == CONN and sent.fallback == SPARE
    assert all(a.target is t for a, t in zip(resolved.attempts, before, strict=True))
    assert resolved.chain == FALLING_BACK and not resolved.chain.primary.structured


@pytest.mark.parametrize("cap", ["no", "unknown"])
def test_a_model_not_known_to_take_the_mode_is_not_flagged(cap):
    fake = FakeLLM([["{}"]])
    resolved = _with_caps(wire_kit.resolution(FALLING_BACK), cap, "yes")
    asyncio.run(inference.generate("chat", _carrying(SCHEMA), client=fake,
                                   resolved=resolved, schema=SCHEMA, stream=False))
    sent = fake.requests[0]["chain"]
    assert (sent.primary.structured, sent.fallback.structured) == (False, True)


def test_a_fallback_that_does_not_ride_is_not_sent():
    resolved = replace(_with_caps(wire_kit.resolution(FALLING_BACK), "yes", "yes"),
                       rides=False)
    fake = FakeLLM([["{}"]])
    asyncio.run(inference.generate("chat", _carrying(SCHEMA), client=fake,
                                   resolved=resolved, schema=SCHEMA, stream=False))
    assert fake.requests[0]["chain"].fallback is None


@pytest.mark.parametrize("stream", [True, False])
def test_without_a_schema_nothing_is_flagged_even_when_capable(stream):
    resolved = _with_caps(wire_kit.resolution(FALLING_BACK), "yes", "yes")
    fake = FakeLLM([["Mara nods."]])
    got = inference.generate("chat", [{"role": "user", "content": "go"}], client=fake,
                             resolved=resolved, stream=stream)
    _drain(got) if stream else asyncio.run(got)
    # The resolution's own chain, byte for byte: no schema keyword either.
    assert fake.requests[0]["chain"] == resolved.chain
    assert not fake.requests[0]["chain"].primary.structured
    if not stream:
        assert fake.schemas == [None]


def test_a_schema_in_a_user_text_part_is_carried():
    fake = FakeLLM([["{}"]])
    resolved = _with_caps(wire_kit.resolution(CONN), "yes")
    asyncio.run(inference.generate("chat", _carrying(SCHEMA, where="user"), client=fake,
                                   resolved=resolved, schema=SCHEMA, stream=False))
    assert fake.schemas == [SCHEMA]


def _tailed() -> model_guidance.PreparedMessages:
    prepared = model_guidance.PreparedMessages(
        "vendor/active", lambda model: (_carrying(SCHEMA), None))
    return prepared.with_tails({"prefill": [{"role": "assistant", "content": "{"}],
                                "instruct": [{"role": "user", "content": "Go on."}]},
                               lambda target: "instruct", CONN)


@pytest.mark.parametrize(("case", "match"), [
    ("outside", "bound"),
    ("absent", "does not carry"),
    ("tojson", "does not carry"),
    ("assistant", "assistant"),
    ("tails", "tails"),
    ("only-in-an-assistant-turn", "does not carry"),
])
@pytest.mark.parametrize("stream", [True, False])
def test_each_refusal_raises_before_any_client_call(case, match, stream):
    schema, messages = SCHEMA, _carrying(SCHEMA)
    if case == "outside":
        schema = {**SCHEMA, "properties": {"title": {"type": "string", "maxLength": 5}}}
        messages = _carrying(schema)
    elif case == "absent":
        messages = [{"role": "user", "content": "Reply with JSON."}]
    elif case == "tojson":
        shown = json.dumps(SCHEMA, indent=2)       # not `schemas.render`'s spelling
        messages = [{"role": "system", "content": shown}]
    elif case == "assistant":
        messages = [*messages, {"role": "assistant", "content": "{"}]
    elif case == "tails":
        messages = _tailed()
    elif case == "only-in-an-assistant-turn":
        messages = [{"role": "user", "content": "go"},
                    {"role": "assistant", "content": schemas.render(SCHEMA)},
                    {"role": "user", "content": "again"}]
    fake = FakeLLM([["never sent"]])
    holder: dict = {}
    with pytest.raises(ValueError, match=match):
        inference.generate("chat", messages, client=fake,
                           resolved=_with_caps(wire_kit.resolution(CONN), "yes"),
                           usage=holder, schema=schema, stream=stream)
    assert fake.calls == 0 and holder == {}


def test_an_outside_schema_is_a_schema_error():
    with pytest.raises(schemas.SchemaError):
        inference.generate("chat", _carrying(SCHEMA), client=FakeLLM([["x"]]),
                           resolved=wire_kit.resolution(CONN),
                           schema={"type": "object"}, stream=False)


def test_the_resolution_is_refused_before_the_schema():
    """The existing refusals come first, with their own sentences."""
    with pytest.raises(ValueError, match="cannot generate"):
        inference.generate("chat", [], client=FakeLLM([["x"]]),
                           resolved=wire_kit.resolution(CONN, "retry"),
                           schema={"type": "object"}, stream=False)


# ---- 01f-C2: a refused field is re-sent without it (spec 3.5) ----
#: A fallback on OpenRouter too, so one provider double serves both routes.
SPARE_OR = wire_kit.target(provider_id="spare", provider_name="Spare", model="vendor/spare",
                           api_key="sk-spare")


def _refused() -> LLMError:
    return LLMError("bad_response",
                    "response_format: json_schema strict mode is not supported", status=400)


def _busy() -> LLMError:
    return LLMError("rate_limit", "slow down", retry_after=30.0, status=429)


def _facade(provider, seen: list | None = None) -> LLMClient:
    observer = (None if seen is None else
                lambda target, err: seen.append((target.model, err.kind if err else None)))
    return LLMClient(openrouter=provider, timeout=0, retries=0, observer=observer)


def _resolved(*caps: str, fallback: bool = False):
    sent = wire.Chain(CONN, SPARE_OR) if fallback else CONN
    return _with_caps(wire_kit.resolution(sent), *caps)


def _joined(client, resolved, usage=None) -> str:
    return asyncio.run(inference.generate("chat", _carrying(SCHEMA), client=client,
                                          resolved=resolved, usage=usage, schema=SCHEMA,
                                          stream=False))


def _streamed(client, resolved, usage=None) -> list[str]:
    return _drain(inference.generate("chat", _carrying(SCHEMA), client=client,
                                     resolved=resolved, usage=usage, schema=SCHEMA))


def _sent(provider) -> list[tuple[str, bool]]:
    return [(r["model"], "schema" in r["kwargs"]) for r in provider.requests]


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_a_refusing_primary_is_answered_by_its_re_send(home):
    provider = SequencedProvider([_refused(), ['{"title": "Dawn"}']])
    seen: list = []
    with store.usage.meter("chat") as m:
        text = _joined(_facade(provider, seen), _resolved("yes"), m.usage)
    assert text == '{"title": "Dawn"}'
    assert _sent(provider) == [("vendor/active", True), ("vendor/active", False)]
    assert provider.requests[0]["messages"] == provider.requests[1]["messages"]
    # One row for the call that answered, counting every attempt it made.
    assert m.row is not None and m.row["status"] == "ok" and m.row["attempts"] == 2
    # The refusal marks nothing; the re-send's answer is observed.
    assert seen == [("vendor/active", None)]


def test_a_fallback_refusing_after_the_primary_failed_is_re_sent():
    provider = SequencedProvider([_busy(), _refused(), ['{"title": "Dawn"}']])
    holder: dict = {}
    text = _joined(_facade(provider), _resolved("yes", "yes", fallback=True), holder)
    assert text == '{"title": "Dawn"}'
    assert _sent(provider) == [("vendor/active", True), ("vendor/spare", True),
                               ("vendor/spare", False)]
    assert holder["attempts"] == 3 and holder["model"] == "vendor/spare"


def test_both_refusing_are_re_sent_in_route_order():
    provider = SequencedProvider([_refused(), _refused(), _busy(), ['{"title": "Dawn"}']])
    holder: dict = {}
    text = _joined(_facade(provider), _resolved("yes", "yes", fallback=True), holder)
    assert text == '{"title": "Dawn"}'
    assert _sent(provider) == [("vendor/active", True), ("vendor/spare", True),
                               ("vendor/active", False), ("vendor/spare", False)]
    # The refused call's two, and each re-send's one.
    assert holder["attempts"] == 4


def test_every_re_send_failing_is_the_routes_composed_afresh():
    busy, down = _busy(), LLMError("network", "connection reset")
    provider = SequencedProvider([_refused(), _refused(), busy, down])
    holder: dict = {}
    with pytest.raises(LLMError) as exc:
        _joined(_facade(provider), _resolved("yes", "yes", fallback=True), holder)
    assert not isinstance(exc.value, llm.SchemaRefusalError)
    assert exc.value.words == (busy, down)
    assert exc.value.kind == "rate_limit" and "fallback failed too" in exc.value.detail
    assert holder["attempts"] == 4


def test_a_lone_re_send_failing_raises_its_own_error():
    second = LLMError("bad_response", "still no", status=400)
    provider = SequencedProvider([_refused(), second, ['{"title": "x"}']])
    with pytest.raises(LLMError) as exc:
        _joined(_facade(provider), _resolved("yes"))
    assert exc.value is second and len(provider.requests) == 2


def test_another_refusal_is_not_re_sent():
    refused = LLMError("bad_response", "vendor/active is not a valid model ID", status=400)
    provider = SequencedProvider([refused, ['{"title": "x"}']])
    with pytest.raises(LLMError) as exc:
        _joined(_facade(provider), _resolved("yes"))
    assert not isinstance(exc.value, llm.SchemaRefusalError)
    assert len(provider.requests) == 1


def test_an_unflagged_attempt_is_never_re_sent():
    """`unknown` is not flagged, so a 400 naming the field is the attempt's
    own failure (it was not sent the envelope) and nothing is re-sent."""
    provider = SequencedProvider([_refused(), ['{"title": "x"}']])
    with pytest.raises(LLMError) as exc:
        _joined(_facade(provider), _resolved("unknown"))
    assert not isinstance(exc.value, llm.SchemaRefusalError)
    assert _sent(provider) == [("vendor/active", False)]


def test_the_streamed_path_re_sends_and_yields_only_the_re_sends_text(home):
    provider = SequencedProvider([_refused(), ['{"title": ', '"Dawn"}']])
    seen: list = []
    with store.usage.meter("chat") as m:
        deltas = _streamed(_facade(provider, seen), _resolved("yes"), m.usage)
    assert "".join(deltas) == '{"title": "Dawn"}'
    assert _sent(provider) == [("vendor/active", True), ("vendor/active", False)]
    assert m.row is not None and m.row["status"] == "ok" and m.row["attempts"] == 2
    assert seen == [("vendor/active", None)]


class _DiesOnTheThird(SequencedProvider):
    """Streams its script, and raises `died` after the third call's text."""

    died = LLMError("network", "connection reset")

    async def stream(self, messages, model="", *args, **kwargs):
        async for chunk in super().stream(messages, model, *args, **kwargs):
            yield chunk
        if len(self.requests) == 3:
            raise self.died


def test_a_streamed_re_send_that_dies_after_prose_is_not_sent_again():
    provider = _DiesOnTheThird([_refused(), _refused(), ['{"title": '], ['{"x": 1}']])
    with pytest.raises(LLMError) as exc:
        _streamed(_facade(provider), _resolved("yes", "yes", fallback=True))
    assert exc.value is _DiesOnTheThird.died
    # The primary's re-send yielded prose and died: the fallback's re-send
    # would follow text already shown, so it is never sent.
    assert _sent(provider) == [("vendor/active", True), ("vendor/spare", True),
                               ("vendor/active", False)]


def test_a_streamed_call_closed_early_closes_the_re_send():
    provider = SequencedProvider([_refused(), ["a", "b", "c"]])

    async def first_only():
        agen = inference.generate("chat", _carrying(SCHEMA), client=_facade(provider),
                                  resolved=_resolved("yes"), schema=SCHEMA)
        async for chunk in agen:
            if chunk:
                await agen.aclose()
                return chunk
        return None

    assert asyncio.run(first_only()) == "a"
    assert provider.closed == 2
