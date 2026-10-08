"""OpenRouter's native decisions adapter, and the facade's native attempt (slice H).

Every request here goes to an `httpx.MockTransport` replaying the hand-authored
bodies under `fixtures/llm/native/openrouter/` (see that directory's README):
nothing reaches a provider.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from grimoire import decisions, llm, llm_usage, openrouter
from grimoire.decisions import Answer, Choice, Item, ItemResult, Option, Predicate, Score
from grimoire.llm import FALLBACK_KEY, LLMClient
from grimoire.llm_errors import LLMError
from grimoire.openrouter import DECISIONS_URL, OpenRouterClient, decision_body, decision_result
from tests.llm_fakes import FIXTURES, FakeLLM

BODIES = FIXTURES / "native" / "openrouter"
KEY = "sk-or-secret-key"
MODEL = "typesafe/jev-1.13"

MARA = Option("characters:mara", "Mara, the cartographer.")
WINIFRED = Option("characters:winifred", "Winifred, the harbourmaster.")
OVER = Predicate("over", "Has the scene reached its end?")
SPEAKER = Choice("speaker", "Who speaks next?", (MARA, WINIFRED), allow_none=True)
TENSION = Score("tension", "How tense is the exchange?", ("Calm.", "Uneasy.", "Heated."))
CONTEXT = "Mara and Winifred argue over the Saltmarch charts."
ITEM = Item(CONTEXT, (OVER, SPEAKER, TENSION))
SPEAKER_ONLY = Item(CONTEXT, (SPEAKER,))
ONE_OPTION = Choice("speaker", "Who speaks next?", (MARA,), allow_none=True)

CONN = {"id": "or-main", "kind": "openrouter", "model": MODEL, "api_key": KEY}


def body(name: str) -> dict:
    return json.loads((BODIES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


class Wire:
    """A MockTransport handler answering from a script, the last entry
    repeating; each entry a `(status, body)` or an exception to raise."""

    def __init__(self, *script, headers: dict | None = None):
        self.script = list(script)
        self.headers = headers or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        entry = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        if isinstance(entry, Exception):
            raise entry
        status, payload = entry
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return httpx.Response(status, text=text, headers=self.headers if status >= 400 else {})

    def sent(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)


def adapter(wire: Wire) -> OpenRouterClient:
    return OpenRouterClient(http=httpx.AsyncClient(transport=httpx.MockTransport(wire)))


def facade(wire: Wire, **kwargs) -> LLMClient:
    return LLMClient(openrouter=adapter(wire), **kwargs)


# ---- the request ----------------------------------------------------------

def test_openrouter_decision_body_maps_each_question_type():
    assert decision_body(ITEM, MODEL) == {
        "model": MODEL,
        "state": CONTEXT,
        "questions": {
            "over": {"type": "noul", "instructions": "Has the scene reached its end?"},
            "speaker": {"type": "choice", "instructions": "Who speaks next?",
                        "criteria": {"characters:mara": "Mara, the cartographer.",
                                     "characters:winifred": "Winifred, the harbourmaster.",
                                     "none": decisions.NATIVE_NONE_TEXT}},
            "tension": {"type": "score", "instructions": "How tense is the exchange?",
                        "criteria": ["Calm.", "Uneasy.", "Heated."]},
        },
    }


def test_openrouter_maps_a_one_option_nullable_choice_to_two_criteria():
    sent = decision_body(Item(CONTEXT, (ONE_OPTION,)), MODEL)
    assert sent["questions"]["speaker"]["criteria"] == {
        "characters:mara": "Mara, the cartographer.", "none": decisions.NATIVE_NONE_TEXT}


def test_openrouter_decision_body_ignores_aliases_and_explain():
    aliased = Choice("speaker", "Who speaks next?",
                     (Option("characters:mara", "Mara, the cartographer.", ("the_mapmaker",)),
                      WINIFRED), allow_none=True)
    plain = decision_body(Item(CONTEXT, (SPEAKER,)), MODEL)
    sent = decision_body(Item(CONTEXT, (aliased,)), MODEL)
    assert sent == plain and "mapmaker" not in json.dumps(sent)
    # `decision_body` takes no explain at all: a native backend returns no
    # rationale, so nothing asks for one.
    assert set(plain) == {"model", "state", "questions"}


def test_a_none_option_id_moves_the_reserved_key():
    taken = Choice("speaker", "Who speaks next?", (Option("none", "Nobody in particular."), MARA),
                   allow_none=True)
    criteria = decision_body(Item(CONTEXT, (taken,)), MODEL)["questions"]["speaker"]["criteria"]
    assert criteria == {"none": "Nobody in particular.", "characters:mara": "Mara, the cartographer.",
                        "none_2": decisions.NATIVE_NONE_TEXT}


# ---- the reply ------------------------------------------------------------

def test_decision_result_reads_the_canned_bodies(caplog):
    result = decision_result(body("answered"), ITEM)
    assert result.backend == "native" and result.rationale == ""
    assert result.answers["over"] == Answer(True, probability=0.82)
    assert result.answers["speaker"] == Answer(
        "characters:winifred",
        distribution={"characters:mara": 0.2, "characters:winifred": 0.7, decisions.NONE_KEY: 0.1})
    # The argmax level, never the weighted score (1.4) rounded.
    assert result.answers["tension"] == Answer(2, distribution={"0": 0.1, "1": 0.4, "2": 0.5})

    none = decision_result(body("none"), SPEAKER_ONLY).answers["speaker"]
    assert (none.answer, none.reason) == (None, "abstained")
    assert none.distribution == {"characters:mara": 0.15, "characters:winifred": 0.25,
                                 decisions.NONE_KEY: 0.6}

    one = decision_result(body("nullable_one"), Item(CONTEXT, (ONE_OPTION,))).answers["speaker"]
    assert one.answer == "characters:mara"
    assert one.distribution == {"characters:mara": 0.85, decisions.NONE_KEY: 0.15}

    wrong = decision_result(body("wrong_type"), SPEAKER_ONLY).answers["speaker"]
    assert wrong == Answer(None, "unreadable")

    rowan = body("answered")
    rowan["answers"]["speaker"]["choice"] = "characters:rowan"
    unoffered = decision_result(rowan, ITEM).answers["speaker"]
    assert (unoffered.answer, unoffered.reason, unoffered.detail) == (
        None, "unreadable", decisions.NOT_AN_OPTION)

    short = body("answered")
    del short["answers"]["tension"]
    with caplog.at_level(logging.WARNING, logger="grimoire.openrouter"):
        partial = decision_result(short, ITEM)
    assert partial.answers["tension"] == Answer(None, "unreadable")
    assert partial.answers["over"].answer is True
    warnings = [r for r in caplog.records if r.name == "grimoire.openrouter"]
    assert len(warnings) == 1 and "1 of 3" in warnings[0].getMessage()


def test_an_unoffered_key_spelled_like_the_reserved_none_is_not_an_option():
    sly = body("none")
    sly["answers"]["speaker"]["choice"] = decisions.NONE_KEY
    sly["answers"]["speaker"]["probabilities"] = {decisions.NONE_KEY: 1.0}
    answer = decision_result(sly, SPEAKER_ONLY).answers["speaker"]
    assert (answer.reason, answer.detail, answer.distribution) == (
        "unreadable", decisions.NOT_AN_OPTION, None)


def test_a_null_choice_is_unreadable_even_where_none_is_allowed():
    # `choice` is a required string; only the reserved none key abstains.
    null = body("none")
    null["answers"]["speaker"]["choice"] = None
    answer = decision_result(null, SPEAKER_ONLY).answers["speaker"]
    assert (answer.answer, answer.reason, answer.detail) == (None, "unreadable", "")
    assert answer.distribution == {"characters:mara": 0.15, "characters:winifred": 0.25,
                                   decisions.NONE_KEY: 0.6}
    closed = Item(CONTEXT, (Choice("speaker", "Who speaks next?", (MARA, WINIFRED)),))
    bare = {"answers": {"speaker": {"type": "choice", "choice": None}}}
    assert decision_result(bare, closed).answers["speaker"] == Answer(None, "unreadable")


def test_a_score_with_no_probabilities_is_unreadable():
    weighted = body("answered")
    del weighted["answers"]["tension"]["probabilities"]
    assert decision_result(weighted, ITEM).answers["tension"] == Answer(None, "unreadable")


def test_an_envelope_answering_no_question_is_a_bad_response():
    with pytest.raises(LLMError) as exc:
        decision_result(body("unanswered"), ITEM)
    assert exc.value.kind == "bad_response"
    # Answers keyed by ids nobody asked are not answers either.
    stray = {**body("unanswered"), "answers": {"rowan": {"type": "noul", "noul": 0.9}}}
    with pytest.raises(LLMError):
        decision_result(stray, ITEM)


def test_a_malformed_body_is_a_bad_response():
    for malformed in (body("malformed"), [], "answers", None, {"answers": ["over"]}):
        with pytest.raises(LLMError) as exc:
            decision_result(malformed, ITEM)
        assert exc.value.kind == "bad_response"


# ---- the adapter's call ---------------------------------------------------

async def test_openrouter_decide_posts_once_and_captures_the_body():
    answered = body("answered")
    answered["echo"] = "data:image/png;base64,QUJDREVG"
    wire = Wire((200, answered))
    events: list[dict] = []
    client = facade(wire, capture=lambda: events.append, retries=0)
    result = await client.decide_native(ITEM, CONN)
    assert result.answers["speaker"].answer == "characters:winifred"
    assert len(wire.requests) == 1
    request = wire.requests[0]
    assert str(request.url) == DECISIONS_URL and request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert wire.sent() == decision_body(ITEM, MODEL)
    assert [e["event"] for e in events] == ["start", "decision_body", "end"]
    captured = events[1]["payload"]
    assert "QUJDREVG" not in captured and "[elided]" in captured
    assert json.loads(captured)["answers"] == answered["answers"]
    assert events[-1]["payload"] == {"status": "complete"}
    assert KEY not in json.dumps(events)


async def test_an_error_body_is_captured_and_mapped():
    wire = Wire((400, body("error_400")))
    events: list[dict] = []
    with pytest.raises(LLMError) as exc:
        await facade(wire, capture=lambda: events.append, retries=0).decide_native(ITEM, CONN)
    assert (exc.value.kind, exc.value.status) == ("bad_response", 400)
    assert exc.value.detail == "Invalid request: questions.speaker.criteria must be an object"
    assert [e["event"] for e in events] == ["start", "http_error_body", "end"]
    assert events[-1]["payload"] == {"status": "error"}


async def test_openrouter_usage_is_mapped_not_estimated():
    holder: dict = {}
    await facade(Wire((200, body("answered"))), retries=0).decide_native(ITEM, CONN, holder)
    assert holder["prompt_tokens"] == 412 and holder["completion_tokens"] == 58
    assert holder["cost_usd"] == pytest.approx(0.0000187)
    assert holder["cost_basis"] == llm_usage.BILLED
    assert holder["model"] == "typesafe/jev-1.13-20260917"
    assert holder["requested_model"] == MODEL and holder["attempts"] == 1
    assert llm_usage.ESTIMATE_KEY not in holder and llm_usage.ESTIMATED not in holder

    bare = body("answered")
    del bare["usage"]
    holder = {}
    counted: list[str] = []
    client = facade(Wire((200, bare)), retries=0,
                    count_tokens=lambda text: counted.append(text) or 7)
    await client.decide_native(ITEM, CONN, holder)
    for field in ("prompt_tokens", "completion_tokens", "cost_usd", "cost_basis",
                  llm_usage.ESTIMATED):
        assert field not in holder
    assert counted == []


async def test_a_billed_envelope_that_answers_nothing_still_files_its_usage():
    holder: dict = {}
    with pytest.raises(LLMError) as exc:
        await facade(Wire((200, body("unanswered"))), retries=0).decide_native(ITEM, CONN, holder)
    assert exc.value.kind == "bad_response"
    assert holder["prompt_tokens"] == 412 and holder["completion_tokens"] == 0


async def test_a_reply_that_is_not_json_is_a_bad_response():
    with pytest.raises(LLMError) as exc:
        await facade(Wire((200, "<html>gateway</html>")), retries=0).decide_native(ITEM, CONN)
    assert exc.value.kind == "bad_response"


async def test_a_read_timeout_is_a_timeout_and_not_retried():
    wire = Wire(httpx.ReadTimeout("slow"))
    with pytest.raises(LLMError) as exc:
        await facade(wire, retries=2, timeout=5).decide_native(ITEM, CONN)
    assert exc.value.kind == "timeout"
    assert len(wire.requests) == 1


async def test_the_adapter_refuses_an_empty_key_unsent():
    wire = Wire((200, body("answered")))
    with pytest.raises(LLMError) as exc:
        await facade(wire).decide_native(ITEM, {**CONN, "api_key": ""})
    assert exc.value.kind == "missing_key" and wire.requests == []


# ---- the facade's attempt -------------------------------------------------

async def test_facade_decide_native_retries_rate_limits_only():
    wire = Wire((429, {"error": {"code": 429, "message": "slow down"}}), (200, body("answered")))
    holder: dict = {}
    result = await facade(wire).decide_native(ITEM, CONN, holder)
    assert result.answers["over"].answer is True
    assert len(wire.requests) == 2 and holder["attempts"] == 2

    refused = Wire((400, body("error_400")), (200, body("answered")))
    with pytest.raises(LLMError) as exc:
        await facade(refused).decide_native(ITEM, CONN)
    assert exc.value.status == 400 and len(refused.requests) == 1

    once = Wire((429, {"error": {"code": 429, "message": "slow down"}}), (200, body("answered")))
    with pytest.raises(LLMError) as exc:
        await facade(once).decide_native(ITEM, CONN, retries=0)
    assert exc.value.kind == "rate_limit" and len(once.requests) == 1


async def test_a_retry_after_past_the_cap_is_not_waited_out():
    wire = Wire((429, {"error": {"code": 429, "message": "slow down"}}), (200, body("answered")),
                headers={"Retry-After": str(int(llm.RETRY_AFTER_CAP) + 30)})
    with pytest.raises(LLMError) as exc:
        await facade(wire).decide_native(ITEM, CONN)
    assert exc.value.kind == "rate_limit" and len(wire.requests) == 1


@pytest.mark.parametrize("status", [400, 403, 404])
async def test_a_refused_native_request_does_not_mark_the_connection_failing(status):
    seen: list = []
    wire = Wire((status, body("error_400")))
    with pytest.raises(LLMError):
        await facade(wire, retries=0, observer=lambda c, e: seen.append((c, e))).decide_native(
            ITEM, CONN)
    assert seen == []


@pytest.mark.parametrize("failure", [(500, {"error": {"code": 500, "message": "boom"}}),
                                     (401, {"error": {"code": 401, "message": "revoked"}}),
                                     httpx.ConnectError("refused")])
async def test_a_failing_native_request_marks_the_connection(failure):
    seen: list = []
    with pytest.raises(LLMError) as exc:
        await facade(Wire(failure), retries=0,
                     observer=lambda c, e: seen.append((c, e))).decide_native(ITEM, CONN)
    assert seen == [(CONN, exc.value)]


async def test_a_served_native_request_is_observed_healthy():
    seen: list = []
    await facade(Wire((200, body("answered"))),
                 observer=lambda c, e: seen.append((c, e))).decide_native(ITEM, CONN)
    assert seen == [(CONN, None)]


async def test_facade_decide_native_strips_the_fallback_and_sends_no_sampling():
    fallback_wire = Wire((200, body("answered")))
    fallback = {"id": "or-spare", "kind": "openrouter", "model": "spare/model", "api_key": "sk-spare"}
    conn = {**CONN, "sampling": {"preset_id": "warm", "preset_name": "Warm",
                                 "params": {"temperature": 0.9, "max_tokens": 300}},
            FALLBACK_KEY: fallback}
    wire = Wire((500, {"error": {"code": 500, "message": "boom"}}))
    client = facade(wire, retries=0)
    holder: dict = {}
    with pytest.raises(LLMError):
        await client.decide_native(ITEM, conn, holder)
    assert len(wire.requests) == 1 and fallback_wire.requests == []
    assert wire.sent() == decision_body(ITEM, MODEL)
    assert FALLBACK_KEY not in holder[llm.ATTEMPTED]
    assert FALLBACK_KEY in conn  # the caller's dict is left as it was


class RecordingNative:
    """An adapter standing in for another kind's: records what it was sent."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def decide(self, item, model, key, *, usage=None, bound=None):
        self.calls.append((item, model, key))
        return ItemResult({"over": Answer(False)}, backend="native")


async def test_decide_native_sends_each_kind_through_its_own_adapter(monkeypatch):
    # Another native kind is registered as the table registers one; the call
    # must reach that kind's adapter, never OpenRouter's with that kind's key.
    monkeypatch.setitem(llm.NATIVE_DECISION_KINDS, "openai_compatible",
                        llm.NativeAdapter(openrouter.decision_body, "_openai_compatible"))
    wire = Wire((200, body("answered")))
    other = RecordingNative()
    client = LLMClient(openrouter=adapter(wire), openai_compatible=other)
    conn = {"kind": "openai_compatible", "model": "local/judge", "api_key": "sk-local",
            "base_url": "https://judge.example.test/v1"}
    result = await client.decide_native(Item(CONTEXT, (OVER,)), conn)
    assert result.answers["over"].answer is False
    assert wire.requests == []
    assert other.calls == [(Item(CONTEXT, (OVER,)), "local/judge", "sk-local")]
    assert llm.native_body(ITEM, conn) == decision_body(ITEM, "local/judge")


@pytest.mark.parametrize("kind", ["anthropic", "claude"])
async def test_facade_decide_native_refuses_a_kind_without_an_endpoint(kind):
    wire = Wire((200, body("answered")))
    holder: dict = {}
    events: list[dict] = []
    with pytest.raises(LLMError) as exc:
        await facade(wire, capture=lambda: events.append).decide_native(
            ITEM, {**CONN, "kind": kind}, holder)
    assert exc.value.kind == "bad_response"
    assert exc.value.detail == f"{kind} connections have no native decisions endpoint"
    assert wire.requests == [] and holder == {} and events == []


async def test_an_unrepresentable_item_is_refused_before_any_request():
    crowd = Choice("speaker", "Who speaks next?",
                   tuple(Option(f"characters:c{i}", f"Townsperson {i}.") for i in range(255)),
                   allow_none=True)
    item = Item(CONTEXT, (crowd,))
    gap = decisions.native_gap(item)
    assert gap
    wire = Wire((200, body("answered")))
    holder: dict = {}
    with pytest.raises(LLMError) as exc:
        await facade(wire).decide_native(item, CONN, holder)
    assert (exc.value.kind, exc.value.code, exc.value.detail) == (
        "bad_response", "native_unrepresentable", gap)
    assert wire.requests == [] and holder == {}


def test_native_body_holds_no_key_or_url():
    conn = {**CONN, "base_url": "https://openrouter.ai/api/v1"}
    sent = llm.native_body(ITEM, conn)
    assert sent == decision_body(ITEM, MODEL)
    text = json.dumps(sent)
    assert KEY not in text and "https://" not in text and "openrouter.ai" not in text
    with pytest.raises(LLMError):
        llm.native_body(ITEM, {**CONN, "kind": "claude"})


def test_native_rejected_statuses_add_forbidden():
    assert llm.REJECTED_STATUSES | {403} == llm.NATIVE_REJECTED_STATUSES
    assert 401 not in llm.NATIVE_REJECTED_STATUSES


# ---- the fake -------------------------------------------------------------

async def test_fake_decide_native_scripts_and_stamps():
    answered = ItemResult({"over": Answer(True, probability=0.9)})
    failure = LLMError("rate_limit", "slow down")
    fake = FakeLLM([["unused"]], decisions=[answered, failure])
    holder: dict = {}
    item = Item(CONTEXT, (OVER,))
    result = await fake.decide_native(item, CONN, holder, retries=1)
    assert result.backend == "native" and result.answers == answered.answers
    assert holder["model"] == MODEL and holder["provider"] == "openrouter"
    assert holder[llm.ATTEMPTED] is CONN and holder["attempts"] == 1
    with pytest.raises(LLMError) as exc:
        await fake.decide_native(item, CONN)
    assert exc.value is failure
    with pytest.raises(LLMError):  # the last entry repeats
        await fake.decide_native(item, CONN)
    assert fake.native_requests == [(item, CONN, 1), (item, CONN, None), (item, CONN, None)]
    assert fake.calls == 0  # no generation was made

    structured = ItemResult({"over": Answer(False)}, backend="structured")
    assert (await FakeLLM([["x"]], decisions=[structured]).decide_native(item, CONN)).backend == \
        "structured"

    crowd = Item(CONTEXT, (Choice("speaker", "Who?", tuple(
        Option(f"characters:c{i}", "") for i in range(255)), allow_none=True),))
    refusing = FakeLLM([["x"]], decisions=[answered])
    empty: dict = {}
    with pytest.raises(LLMError) as exc:
        await refusing.decide_native(crowd, CONN, empty)
    assert exc.value.code == "native_unrepresentable" and empty == {}
    assert refusing.native_requests == []

    with pytest.raises(AssertionError, match="FakeLLM has no native decisions scripted"):
        await FakeLLM([["x"]]).decide_native(item, CONN)
    with pytest.raises(ValueError, match="at least one native decision"):
        FakeLLM([["x"]], decisions=[])


@pytest.mark.parametrize("kind", ["anthropic", "claude"])
async def test_fake_decide_native_refuses_a_kind_the_facade_refuses(kind):
    fake = FakeLLM([["x"]], decisions=[ItemResult({"over": Answer(True)})])
    holder: dict = {}
    with pytest.raises(LLMError) as exc:
        await fake.decide_native(Item(CONTEXT, (OVER,)), {**CONN, "kind": kind}, holder)
    assert (exc.value.kind, exc.value.detail) == (
        "bad_response", f"{kind} connections have no native decisions endpoint")
    assert holder == {} and fake.native_requests == []


async def test_fake_decide_native_strips_the_fallback_before_it_stamps():
    fake = FakeLLM([["x"]], decisions=[ItemResult({"over": Answer(True)})])
    conn = {**CONN, FALLBACK_KEY: {"kind": "openrouter", "model": "spare/model"}}
    holder: dict = {}
    await fake.decide_native(Item(CONTEXT, (OVER,)), conn, holder)
    assert holder[llm.ATTEMPTED] == CONN and FALLBACK_KEY not in holder[llm.ATTEMPTED]
    assert fake.native_requests[0][1] == CONN
    assert FALLBACK_KEY in conn

