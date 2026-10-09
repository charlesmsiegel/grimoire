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

from grimoire import decisions, inference, prompts
from grimoire.llm_errors import LLMError
from grimoire.store import routing, suggest, voice_drift
from grimoire.store.continuity import identity, reconcile
from tests import llm_fakes
from tests.llm_fakes import (
    Cassette,
    CassetteMiss,
    CassetteProvider,
    FakeLLM,
    FakeOpenRouter,
    FakeOpenRouterComplete,
    from_cassette,
)

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


async def test_a_cassette_entry_can_fail_its_request_alone():
    cas = _cassette({"when": {"contains": "turn"}, "error": {"kind": "network",
                                                            "message": "reset"}},
                    {"when": {}, "reply": "fine"})
    fake = FakeLLM(cassette=cas)
    with pytest.raises(LLMError) as exc:
        await fake.complete([{"role": "user", "content": "the turn"}], CONN)
    assert (exc.value.kind, exc.value.detail) == ("network", "reset")
    assert await fake.complete([{"role": "user", "content": "the update"}], CONN) == "fine"


async def test_a_cassette_reply_can_be_streamed_as_deltas():
    fake = from_cassette("campaign_flow")
    deltas = await _drain(fake, [{"role": "system", "content":
                                  "Write continuous prose for the assigned speaker."}])
    assert len(deltas) > 1 and "".join(deltas).startswith('"Salt first,')


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
#: Every prompt a cassette entry can be keyed on, rendered from the real
#: templates, as a `(system, user)` pair. A generate prompt is keyed on its
#: system message alone, so its user half is "". `scene_suggestions/system.j2`,
#: the reply-format section and `absorb/system.j2` (whose steering paragraph is
#: conditional; True renders the superset) are the only ones needing vars, in
#: the shape their builders pass.
_THREAD_DRIVER = {"ref": "thread:the-debt", "kind": "thread", "label": "The debt",
                  "summary": "", "actors": [], "status": "open",
                  "pressure": {"state": "stale", "in_days": None, "friendly": ""},
                  "time_anchors": [], "links": [], "dormancy": 3}


def _generate_prompts() -> list[str]:
    return [prompts.render("absorb/system.j2", steering=True)] + \
           [prompts.render(t) for t in ("audit/system.j2",
                                        "dossier/system.j2", "voice_anchor/system.j2",
                                        "tagline/system.j2")] + [
        # `drivers` with a one-row index, so the drivers addendum is rendered
        # (and covered) too.
        prompts.render("scene_suggestions/system.j2", offscreen=False, s={"now": ""},
                       greeting_candidates=[], direction="", drivers=True,
                       view=suggest.driver_view({"driver_index": [_THREAD_DRIVER], "near_days": 7},
                                                suggest.NO_CONTROLS)),
        prompts.render("scene/sections/response_format.j2", player_names=[],
                       response_actor={"ref": "grimoire", "name": "Grimoire"}),
        prompts.render("tracker/update_system.j2"),
    ]


#: One examined row as `Examination.prompt_rows` shapes it, offering one
#: stored thread: the duplicate check's smallest item.
_IDENTITY_ROW = {"key": "r1", "kind": "thread", "title": "Recover the harbour ledger",
                 "beat": "Winifred went looking for the harbour ledger.", "status": "open",
                 "commitment_kind": "", "due": "", "quote": "", "speaker": "",
                 "certainty": None, "why_new": "", "distinguished_from": [],
                 "candidates": [{"id": "find-the-ledger", "title": "Find the ledger",
                                 "status": "open", "kind": "", "due": "",
                                 "latest_beat": "Winifred learned the harbour ledger exists.",
                                 "earlier": [],
                                 "signals": {"tokens": 0.4, "chars": 0.5, "via": "lexical"}}]}


def _reconcile_record(letter: str, ref: str) -> dict:
    """One record as `reconcile._record_view` shapes it, with one beat."""
    rid = ref.partition(":")[2]
    return {"letter": letter, "ref": ref, "type": "plot thread",
            "line": reconcile.snippet_line(ref, {"title": rid.replace("-", " ").capitalize(),
                                                 "status": "open", "kind": "", "due": ""}),
            "beats": [{"scene": "s1", "text": "Winifred asked about the ledger."}],
            "pressure": "", "links": [], "actors": []}


#: A `build_payload`-shaped payload holding one possible duplicate: the
#: sweep's smallest item.
_RECONCILE_PAYLOAD = {
    "now": "", "chronicle": [{"id": "s1", "one_line": "Winifred came ashore."}],
    "recent": ["s1"], "known_scenes": ["s1"],
    "candidates": [{"key": "c1", "id": "candidate-1", "vocabulary": "same_thread",
                    "records": [_reconcile_record("A", "thread:find-the-ledger"),
                                _reconcile_record("B", "thread:recover-the-ledger")],
                    "signal_text": "word overlap 0.40"}]}


def _decide_prompts() -> dict[str, tuple[str, str]]:
    """Each decide conversion `campaign_flow` drives, by task: the `(system,
    user)` pair `inference.decide` sends for one item, built through
    `inference.structured_messages` exactly as it builds it."""
    item = voice_drift.build_item("Seraphine", "Clipped. Never uses contractions.",
                                  "Seraphine: Salt first.")
    pairs = {"voice-drift": inference.structured_messages([item],
                                                          explain=voice_drift.explain()),
             "continuity-identity": inference.structured_messages(
                 identity.build_items([_IDENTITY_ROW], {}), explain=identity.explain()),
             "continuity-reconcile": inference.structured_messages(
                 reconcile.build_items(_RECONCILE_PAYLOAD), explain=reconcile.explain())}
    return {task: (system["content"], user["content"])
            for task, (system, user) in pairs.items()}


#: The decide conversions `campaign_flow` never drives, each with the reason.
#: Their route tests script their own replies, so a cassette entry for them
#: would be one nothing reads.
NOT_IN_CASSETTE = {
    "scene-break": ("the play loop's follow-up, which `test_scene_break_routes.py` and "
                    "`test_turn_follow_ups.py` drive with scripted decide replies"),
    "response-selector": ("the next-speaker pick in group play, which "
                          "`test_character_turns.py` and `test_group_play_turns.py` "
                          "drive with scripted decide replies"),
}


def _rendered_prompts() -> list[tuple[str, str]]:
    return [(text, "") for text in _generate_prompts()] + list(_decide_prompts().values())


def _as_messages(pair: tuple[str, str]) -> list[dict]:
    system, user = pair
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _shipped() -> Cassette:
    return Cassette.load("campaign_flow")


def test_every_decide_task_is_rendered_or_named_as_not_driven():
    """A decide route's task either has its pair rendered here (and so a
    cassette entry the next test demands) or is named in `NOT_IN_CASSETTE`
    with the reason -- never neither, never both."""
    decide = {t for r in routing.ROUTES if r.operation == "decide" for t in r.tasks}
    rendered = set(_decide_prompts())
    assert decide <= rendered | set(NOT_IN_CASSETTE), decide - rendered - set(NOT_IN_CASSETTE)
    assert not rendered & set(NOT_IN_CASSETTE)
    # Only a decide task is ever excused: a generate task named here would be
    # one whose prompt nothing renders and no cassette matcher is held to.
    assert set(NOT_IN_CASSETTE) <= decide, set(NOT_IN_CASSETTE) - decide
    assert set(NOT_IN_CASSETTE) <= set(routing.TASK_ROUTE)
    assert all(reason.strip() for reason in NOT_IN_CASSETTE.values())


def test_every_cassette_matcher_is_still_a_phrase_the_real_prompts_contain():
    """The link that would otherwise rot. A reworded prompt leaves the matcher
    dead, the cassette answers nothing, and — without this test — the only
    symptom is a `CassetteMiss` in whichever unrelated test happens to drive
    that call next. Each needle is looked for in the rendered text of ITS role,
    and one rendered prompt must satisfy all of an entry's needles at once."""
    cassette = _shipped()
    rendered = _rendered_prompts()
    for entry in cassette.entries:
        when = entry["when"]
        assert set(when) <= {"system_contains", "user_contains"}, when
        for key, needle in when.items():
            role = 0 if key == "system_contains" else 1
            assert any(needle in pair[role] for pair in rendered), \
                f"no shipped prompt's {key[:-9]} contains {needle!r} any more — the entry is dead"
        assert any(cassette._matches(when, _as_messages(pair)) for pair in rendered), \
            f"no one shipped prompt matches every matcher of {when!r}"


def test_the_cassette_covers_every_prompt_the_app_can_send():
    """The other direction: a new LLM call type with no cassette entry would
    only surface as a `CassetteMiss` the first time somebody wired the cassette
    into a test that triggers it. Every matcher of the entry must match."""
    cassette = _shipped()
    for pair in _rendered_prompts():
        assert any(cassette._matches(e["when"], _as_messages(pair)) for e in cassette.entries), \
            f"no cassette entry matches this prompt: {pair[0][:120]!r} / {pair[1][:120]!r}"


def test_the_voice_drift_body_is_a_decision_the_judge_reads():
    """The judge's canned reply decodes, through `decide()`'s own parser, to a
    usable `in_voice` -- the old body's `"consistent"` never parsed, so every
    absorb that reached it saw a failed check."""
    item = voice_drift.build_item("Seraphine", "Clipped.", "Seraphine: Salt first.")
    reply = _shipped().reply(_as_messages(_decide_prompts()["voice-drift"]))
    (result,) = decisions.parse("".join(reply), (item,), explain=True)
    finding = voice_drift.finding_of(result)
    assert finding == {"verdict": voice_drift.IN_VOICE, "note": "", "native": False}
    assert voice_drift.check_failure(finding) is None


def test_a_decide_entry_never_answers_another_decision():
    """The decide system prompt is every decision's, so the voice entry also
    keys on its own context: a decision without the anchor heading misses."""
    system, _user = _decide_prompts()["voice-drift"]
    with pytest.raises(CassetteMiss):
        _shipped().reply([{"role": "system", "content": system},
                           {"role": "user", "content": "Is this scene over?"}])


# ---- an entry's `model` is honoured, never ignored ----
_PRIMARY = {"kind": "openrouter", "model": "m/primary", "api_key": "k"}
_FALLBACK = {"kind": "openrouter", "model": "m/fallback", "api_key": "k"}


async def test_a_fake_honours_an_entrys_model():
    """`FakeLLM` serves the attempt it was handed and no fallback, so an entry
    naming a model answers only a request sent on that model: a primary-only
    entry can never answer the fallback's request."""
    fake = llm_fakes.from_entries([
        {"when": {"contains": "judge"}, "model": "m/primary", "reply": "primary"},
        {"when": {"contains": "judge"}, "reply": "anyone"}])
    msgs = [{"role": "user", "content": "judge this"}]
    assert await fake.complete(msgs, _PRIMARY) == "primary"
    assert await fake.complete(msgs, _FALLBACK) == "anyone"
    only = llm_fakes.from_entries([{"when": {"contains": "judge"}, "model": "m/primary",
                                    "reply": "primary"}])
    with pytest.raises(CassetteMiss):
        await only.complete(msgs, _FALLBACK)


def test_a_model_keyed_entry_refuses_a_request_that_names_no_model():
    """Read without a model (`Cassette.reply` called directly), an entry that
    names one is an error rather than a match: the key is honoured or it
    fails, never silently ignored."""
    cas = Cassette({"entries": [{"when": {}, "model": "m/primary", "reply": "x"}]})
    with pytest.raises(ValueError, match="model"):
        cas.reply([{"role": "user", "content": "hi"}])


# ---- the provider double ----
async def test_the_cassette_provider_answers_by_shape_and_model():
    """`CassetteProvider` sits under a real facade: an entry may name the
    model an attempt was sent, so a primary and its fallback answer apart."""
    provider = CassetteProvider([
        {"when": {"system_contains": "judge"}, "model": "m/primary",
         "error": {"kind": "network", "message": "reset"}},
        {"when": {"system_contains": "judge"}, "reply": ["ruled", " fine"]}])
    msgs = [{"role": "system", "content": "You judge."}]
    with pytest.raises(LLMError):
        [d async for d in provider.stream(msgs, "m/primary")]
    assert [d async for d in provider.stream(msgs, "m/fallback")] == ["ruled", " fine"]
    assert [r["model"] for r in provider.requests] == ["m/primary", "m/fallback"]
    with pytest.raises(CassetteMiss):
        [d async for d in provider.stream([{"role": "system", "content": "other"}], "m")]


async def test_the_cassette_provider_stalls_where_told():
    provider = CassetteProvider([{"when": {"contains": "slow"}, "stall": 5, "reply": "late"}])
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            anext(aiter(provider.stream([{"role": "user", "content": "slow"}], "m"))), 0.05)


def test_the_absorb_body_is_the_shape_the_parser_expects():
    """A canned body that no longer parses is worse than no fixture: it fails
    somewhere downstream of the code being tested."""
    fake = from_cassette("campaign_flow")
    reply = fake.cassette.reply([{"role": "system",
                                  "content": "You are absorbing a completed role-play scene"}])
    record = json.loads("".join(reply))
    assert {"one_line", "summary", "keywords", "timeline_events"} <= set(record)
    assert isinstance(record["keywords"], list)


def test_the_identity_body_is_the_shape_the_parser_expects():
    """The duplicate check's canned reply must decode to a decision, or every
    absorb test that reaches the check would see a `failed` phase."""
    items = identity.build_items([_IDENTITY_ROW], {})
    reply = _shipped().reply(_as_messages(_decide_prompts()["continuity-identity"]))
    results = decisions.parse("".join(reply), items, explain=True)
    answers = identity.answers_of([_IDENTITY_ROW], results)
    assert answers is not None
    [decision] = answers
    assert (decision["row"], decision["decision"]) == ("r1", "new")


def test_the_reconcile_body_is_the_shape_the_parser_expects():
    """The reconciliation sweep's canned reply must decode, or every test that
    reaches the sweep's model call would see a failed run; it proposes
    nothing, so no test is handed a decision it did not script."""
    items = reconcile.build_items(_RECONCILE_PAYLOAD)
    reply = _shipped().reply(_as_messages(_decide_prompts()["continuity-reconcile"]))
    results = decisions.parse("".join(reply), items, explain=True)
    assert reconcile.proposals_of(_RECONCILE_PAYLOAD, results) == {}


def test_each_continuity_entry_answers_only_its_own_decision():
    """Both continuity entries share the decide system phrase, so each keys on
    its own item's heading: the identity entry never answers the sweep, nor
    the sweep's the identity check."""
    identity_reply = _shipped().reply(
        _as_messages(_decide_prompts()["continuity-identity"]))
    reconcile_reply = _shipped().reply(
        _as_messages(_decide_prompts()["continuity-reconcile"]))
    assert "".join(reconcile_reply) == "{}"
    assert '"decision": "new"' in "".join(identity_reply)


def test_decision_reply_omits_a_none_index():
    """A `None` answer leaves its index out, so the item it stands for is one
    the reply never reached: unread (`NO_ITEM`), never answered."""
    body = json.loads(llm_fakes.decision_reply(None, {"decision": "new"},
                                               rationales=("", "a second search")))
    assert body == {"1": {"answers": {"decision": "new"}, "rationale": "a second search"}}
    items = identity.build_items([_IDENTITY_ROW, {**_IDENTITY_ROW, "key": "r2"}], {})
    first, second = decisions.parse(json.dumps(body), items, explain=True)
    assert first.answers[identity.DECISION_ID].detail == decisions.NO_ITEM
    assert second.answers[identity.DECISION_ID].answer == "new"
