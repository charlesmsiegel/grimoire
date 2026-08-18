"""The shared gateway fakes and their canned bodies (#204).

Test infrastructure gets tests for the same reason production code does: a fake
that silently answers everything makes every test that uses it vacuous, and
nothing else in the suite would notice. The two properties worth the most here
are that a cassette **refuses** an unmatched request rather than defaulting, and
that its matchers are still phrases the real prompts contain — the second is
what stops the fixtures rotting the day a system prompt is reworded.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from grimoire import prompts
from grimoire.llm_errors import LLMError
from tests import llm_fakes
from tests.llm_fakes import (Cassette, CassetteMiss, FakeLLM, FakeOpenRouter,
                             FakeOpenRouterComplete, from_cassette)

CONN = {"kind": "openrouter", "model": "m", "api_key": "k"}


async def _drain(fake, messages=None) -> list[str]:
    return [d async for d in fake.stream(messages or [{"role": "user", "content": "hi"}], CONN)]


# ---- scripted replies ----
async def test_deltas_stream_one_at_a_time():
    assert await _drain(FakeOpenRouter(["Hel", "lo"])) == ["Hel", "lo"]


async def test_complete_joins_the_turns_deltas():
    fake = FakeOpenRouter(["Hel", "lo"])
    assert await fake.complete([], CONN) == "Hello"


async def test_scripted_turns_are_consumed_in_order_and_the_last_one_repeats():
    fake = FakeOpenRouterComplete(["first", "second"])
    assert [await fake.complete([], CONN) for _ in range(3)] == ["first", "second", "second"]
    assert fake.calls == 3


async def test_every_request_is_recorded():
    fake = FakeOpenRouter(["ok"])
    await _drain(fake, [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}])
    assert fake.calls == 1
    assert fake.messages[-1] == {"role": "user", "content": "U"}
    assert fake.conn == CONN


async def test_an_injected_error_arrives_after_the_deltas_it_was_given():
    fake = llm_fakes.FailingOpenRouter(["half "])
    got: list[str] = []
    with pytest.raises(LLMError) as exc:
        async for delta in fake.stream([], CONN):
            got.append(delta)
    assert got == ["half "] and exc.value.kind == "network"


async def test_a_quiet_turn_yields_the_facades_empty_liveness_frame_first():
    assert await _drain(llm_fakes.QuietThenAnswers()) == ["", "At last."]


async def test_a_stall_holds_complete_too_not_just_stream():
    """`complete()` goes through `stream()` for this reason. A stalling fake
    that answered a completing route (absorb, dossier, tagline, suggestions)
    instantly would let a timeout or cancellation test pass without the stall
    it was written for ever happening."""
    fake = llm_fakes.StallingOpenRouter(["half "])
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(fake.complete([], CONN), timeout=0.05)


async def test_a_failing_call_still_records_what_the_caller_sent():
    """`complete()` records before it raises, like `stream()` does — a test
    asserting on the request the route built must still be able to when the
    call is the one that fails."""
    fake = llm_fakes.FailingOpenRouter()
    with pytest.raises(LLMError):
        await fake.complete([{"role": "system", "content": "S"}], CONN)
    assert fake.calls == 1 and fake.messages == [{"role": "system", "content": "S"}]


def test_a_fake_takes_either_a_script_or_a_cassette_but_not_both():
    with pytest.raises(ValueError):
        FakeLLM()
    with pytest.raises(ValueError):
        FakeLLM([["x"]], cassette=Cassette.load("campaign_flow"))


def test_a_fake_with_no_turns_is_rejected_where_it_is_built():
    """Otherwise the first call raises IndexError from inside the fake, with
    nothing in the traceback pointing at the test that constructed it."""
    with pytest.raises(ValueError, match="at least one turn"):
        FakeLLM([])


# ---- cassettes ----
def _cassette(*entries) -> Cassette:
    return Cassette({"entries": list(entries)}, "test")


async def test_a_cassette_answers_by_what_the_request_looks_like():
    cas = _cassette({"when": {"system_contains": "absorbing"}, "reply": "{}"},
                    {"when": {"system_contains": "script"}, "reply": "prose"})
    fake = FakeLLM(cassette=cas)
    assert await fake.complete([{"role": "system", "content": "you are absorbing a scene"}], CONN) == "{}"
    assert await fake.complete([{"role": "system", "content": "reply as a script"}], CONN) == "prose"


async def test_a_cassette_takes_the_first_matching_entry():
    cas = _cassette({"when": {"contains": "salt"}, "reply": "specific"},
                    {"when": {}, "reply": "catch-all"})
    fake = FakeLLM(cassette=cas)
    assert await fake.complete([{"role": "user", "content": "the salt"}], CONN) == "specific"
    assert await fake.complete([{"role": "user", "content": "the quay"}], CONN) == "catch-all"


async def test_a_cassette_refuses_a_request_it_does_not_cover():
    """The whole point of the cassette over a fixed reply: an unmatched call is
    a test driving something the fixtures never described, and answering it
    anyway would leave that test green and meaningless."""
    fake = FakeLLM(cassette=_cassette({"when": {"system_contains": "absorbing"}, "reply": "{}"}))
    with pytest.raises(CassetteMiss) as exc:
        await fake.complete([{"role": "system", "content": "something else entirely"}], CONN)
    assert "absorbing" in str(exc.value)          # says what it tried
    assert "something else entirely" in str(exc.value)   # and what it got


async def test_the_user_matcher_does_not_see_the_system_message():
    fake = FakeLLM(cassette=_cassette({"when": {"user_contains": "salt"}, "reply": "hit"}))
    with pytest.raises(CassetteMiss):
        await fake.complete([{"role": "system", "content": "salt"}], CONN)


def test_an_unknown_matcher_is_an_error_not_a_silent_pass():
    fake = FakeLLM(cassette=_cassette({"when": {"assistant_contains": "x"}, "reply": "y"}))
    with pytest.raises(ValueError, match="unknown matcher"):
        fake.cassette.reply([{"role": "user", "content": "x"}])


def test_an_empty_cassette_is_rejected_when_it_is_built():
    with pytest.raises(ValueError):
        Cassette({"entries": []}, "empty")


def test_a_reply_written_as_json_instead_of_as_a_string_is_rejected():
    """The authoring mistake this format invites: writing the payload as a JSON
    object rather than as the string a model would send. Iterating it would
    stream its keys as text and fail somewhere far from the fixture."""
    fake = FakeLLM(cassette=_cassette({"when": {}, "reply": {"one_line": "o"}}))
    with pytest.raises(ValueError, match="string or a list of strings"):
        fake.cassette.reply([{"role": "user", "content": "x"}])


async def test_a_cassette_reply_can_be_streamed_as_deltas():
    fake = from_cassette("campaign_flow")
    deltas = await _drain(fake, [{"role": "system", "content": "Write your reply as a script."}])
    assert len(deltas) > 1 and "".join(deltas).startswith("**Seraphine:**")


async def test_from_entries_answers_by_shape_not_order():
    """An inline cassette is order-independent: each request gets the reply its
    own shape selects, whichever order the two arrive in.

    This is what absorb's tests need once its phases run concurrently -- "the
    first call" stops naming anything, so a reply can only be tied to a request
    by what the request looks like.
    """
    fake = llm_fakes.from_entries([
        {"when": {"system_contains": "absorbing a completed"}, "reply": "EXTRACTION"},
        {"when": {"system_contains": "auditing a completed"}, "reply": "AUDIT"},
    ])
    audit_first = await fake.complete(
        [{"role": "system", "content": "You are auditing a completed scene"}], CONN)
    extraction_second = await fake.complete(
        [{"role": "system", "content": "You are absorbing a completed scene"}], CONN)
    assert audit_first == "AUDIT"
    assert extraction_second == "EXTRACTION"


async def test_from_entries_refuses_a_request_it_does_not_cover():
    """Inline entries inherit the file cassette's refusal. A default reply here
    would make every migrated absorb test vacuous in exactly the way the
    ordered script it replaced could not be."""
    fake = llm_fakes.from_entries(
        [{"when": {"system_contains": "absorbing"}, "reply": "X"}])
    with pytest.raises(CassetteMiss):
        await fake.complete([{"role": "system", "content": "something else"}], CONN)


# ---- the shipped cassette still matches the shipped prompts ----
#: Every system prompt a cassette entry can be keyed on, rendered from the real
#: template. `scene_suggestions/system.j2` and the reply-format section are the
#: only two needing vars, and both take the shape their builders pass.
def _rendered_prompts() -> list[str]:
    return [prompts.render(t) for t in ("absorb/system.j2", "audit/system.j2",
                                        "dossier/system.j2", "voice_anchor/system.j2",
                                        "voice_drift/system.j2", "tagline/system.j2")] + [
        prompts.render("scene_suggestions/system.j2", offscreen=False, s={"now": ""},
                       greeting_candidates=[], direction=""),
        prompts.render("scene/sections/response_format.j2", player_names=[]),
    ]


def test_every_cassette_matcher_is_still_a_phrase_the_real_prompts_contain():
    """The link that would otherwise rot. A reworded system prompt leaves the
    matcher dead, the cassette answers nothing, and — without this test — the
    only symptom is a `CassetteMiss` in whichever unrelated test happens to
    drive that call next."""
    rendered = _rendered_prompts()
    cassette = json.loads((llm_fakes.FIXTURES / "campaign_flow.json").read_text(encoding="utf-8"))
    for entry in cassette["entries"]:
        needle = entry["when"]["system_contains"]
        assert any(needle in text for text in rendered), \
            f"no shipped prompt contains {needle!r} any more — the cassette entry is dead"


def test_the_cassette_covers_every_prompt_the_app_can_send():
    """The other direction: a new LLM call type with no cassette entry would
    only surface as a `CassetteMiss` the first time somebody wired the cassette
    into a test that triggers it."""
    cassette = json.loads((llm_fakes.FIXTURES / "campaign_flow.json").read_text(encoding="utf-8"))
    needles = [e["when"]["system_contains"] for e in cassette["entries"]]
    for text in _rendered_prompts():
        assert any(n in text for n in needles), \
            f"no cassette entry matches this prompt: {text[:120]!r}"


def test_the_absorb_body_is_the_shape_the_parser_expects():
    """A canned body that no longer parses is worse than no fixture: it fails
    somewhere downstream of the code being tested."""
    fake = from_cassette("campaign_flow")
    reply = fake.cassette.reply([{"role": "system",
                                  "content": "You are absorbing a completed role-play scene"}])
    record = json.loads("".join(reply))
    assert {"one_line", "summary", "keywords", "timeline_events"} <= set(record)
    assert isinstance(record["keywords"], list)
