"""The native decisions adapters -- OpenRouter's and OpenAI's -- and the
facade's native attempt (slice H).

Every request here goes to an `httpx.MockTransport` replaying the hand-authored
bodies under `fixtures/llm/native/<provider>/` (see that directory's README):
nothing reaches a provider.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from grimoire import decisions, llm, llm_usage, openai_compatible
from grimoire.decisions import Answer, Choice, Item, ItemResult, Option, Predicate, Score
from grimoire.llm import FALLBACK_KEY, LLMClient
from grimoire.llm_errors import LLMError
from grimoire.openai_compatible import OpenAICompatibleClient
from grimoire.openrouter import DECISIONS_URL, OpenRouterClient, decision_body, decision_result
from tests.llm_fakes import FIXTURES, FakeLLM

BODIES = FIXTURES / "native" / "openrouter"
KEY = "sk-or-secret-key"
MODEL = "typesafe/jev-1.13"

OPENAI_BODIES = FIXTURES / "native" / "openai"
OPENAI_KEY = "sk-oa-secret-key"
OPENAI_MODEL = "gpt-6-luna"
#: The connection's own address, never the preset's, and never reached: every
#: request goes to a MockTransport.
OPENAI_BASE = "https://decisions.example.test/v1/"

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
OPENAI_CONN = {"id": "oa-main", "kind": "openai_compatible", "model": OPENAI_MODEL,
               "api_key": OPENAI_KEY, "base_url": OPENAI_BASE}


def body(name: str) -> dict:
    return json.loads((BODIES / f"{name}.json").read_text(encoding="utf-8"))


def openai_body(name: str) -> dict:
    return json.loads((OPENAI_BODIES / f"{name}.json").read_text(encoding="utf-8"))


# ---- the two wire shapes, for the assertions both adapters share ----------
#
# OpenRouter keys its answers by question id; OpenAI lists them, each named.
# These read and edit an answer in either shape, so a shared rule is one test
# parametrized over the provider rather than two that can drift apart.

PROVIDERS = ["openrouter", "openai"]


def canned(provider: str, name: str) -> dict:
    return body(name) if provider == "openrouter" else openai_body(name)


def read(provider: str):
    return decision_result if provider == "openrouter" else openai_compatible.decision_result


def answer_in(provider: str, payload: dict, qid: str) -> dict:
    if provider == "openrouter":
        return payload["answers"][qid]
    return next(a for a in payload["answers"] if a.get("name") == qid)


def drop_answer(provider: str, payload: dict, qid: str) -> None:
    if provider == "openrouter":
        del payload["answers"][qid]
    else:
        payload["answers"] = [a for a in payload["answers"] if a.get("name") != qid]


def stray_answer(provider: str) -> dict:
    """An envelope answering only a question nobody asked."""
    if provider == "openrouter":
        return {"answers": {"rowan": {"type": "noul", "noul": 0.9}}}
    return {"answers": [{"type": "predicate", "name": "rowan", "probability": 0.9}]}


def wrong_type(provider: str) -> dict:
    """The speaker question answered as though it were a predicate."""
    if provider == "openrouter":
        return body("wrong_type")
    return {"answers": [{"type": "predicate", "name": "speaker", "probability": 0.9}]}


def choice_probabilities(provider: str, weights: dict[str, float]):
    if provider == "openrouter":
        return dict(weights)
    return [{"value": key, "probability": p} for key, p in weights.items()]


#: The canned `answered` body's score distribution, and its weighted `score`:
#: in both the argmax (2) is not the rounding of the score.
TENSION = {"openrouter": ({"0": 0.1, "1": 0.4, "2": 0.5}, 1.4),
           "openai": ({"0": 0.35, "1": 0.2, "2": 0.45}, 1.1)}


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


def openai_adapter(wire: Wire) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(http=httpx.AsyncClient(transport=httpx.MockTransport(wire)))


def openai_facade(wire: Wire, **kwargs) -> LLMClient:
    return LLMClient(openai_compatible=openai_adapter(wire), **kwargs)


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

@pytest.mark.parametrize("provider", PROVIDERS)
def test_decision_result_reads_the_canned_bodies(provider, caplog):
    result = read(provider)(canned(provider, "answered"), ITEM)
    assert result.backend == "native" and result.rationale == ""
    assert result.answers["over"] == Answer(True, probability=0.82)
    assert result.answers["speaker"] == Answer(
        "characters:winifred",
        distribution={"characters:mara": 0.2, "characters:winifred": 0.7, decisions.NONE_KEY: 0.1})
    # The argmax level, never the weighted score rounded.
    distribution, weighted = TENSION[provider]
    assert answer_in(provider, canned(provider, "answered"), "tension")["score"] == weighted
    assert round(weighted) != 2
    assert result.answers["tension"] == Answer(2, distribution=distribution)

    none = read(provider)(canned(provider, "none"), SPEAKER_ONLY).answers["speaker"]
    assert (none.answer, none.reason) == (None, "abstained")
    assert none.distribution == {"characters:mara": 0.15, "characters:winifred": 0.25,
                                 decisions.NONE_KEY: 0.6}

    one = read(provider)(canned(provider, "nullable_one"),
                         Item(CONTEXT, (ONE_OPTION,))).answers["speaker"]
    assert one.answer == "characters:mara"
    assert one.distribution == {"characters:mara": 0.85, decisions.NONE_KEY: 0.15}

    wrong = read(provider)(wrong_type(provider), SPEAKER_ONLY).answers["speaker"]
    assert wrong == Answer(None, "unreadable")

    rowan = canned(provider, "answered")
    answer_in(provider, rowan, "speaker")["choice"] = "characters:rowan"
    unoffered = read(provider)(rowan, ITEM).answers["speaker"]
    assert (unoffered.answer, unoffered.reason, unoffered.detail) == (
        None, "unreadable", decisions.NOT_AN_OPTION)

    short = canned(provider, "answered")
    drop_answer(provider, short, "tension")
    with caplog.at_level(logging.WARNING, logger="grimoire.decisions"):
        partial = read(provider)(short, ITEM)
    assert partial.answers["tension"] == Answer(None, "unreadable")
    assert partial.answers["over"].answer is True
    warnings = [r for r in caplog.records if r.name == "grimoire.decisions"]
    assert len(warnings) == 1 and "1 of 3" in warnings[0].getMessage()
    assert ("OpenRouter's" if provider == "openrouter" else "OpenAI's") in warnings[0].getMessage()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_an_unoffered_key_spelled_like_the_reserved_none_is_not_an_option(provider):
    sly = canned(provider, "none")
    speaker = answer_in(provider, sly, "speaker")
    speaker["choice"] = decisions.NONE_KEY
    speaker["probabilities"] = choice_probabilities(provider, {decisions.NONE_KEY: 1.0})
    answer = read(provider)(sly, SPEAKER_ONLY).answers["speaker"]
    assert (answer.reason, answer.detail, answer.distribution) == (
        "unreadable", decisions.NOT_AN_OPTION, None)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_null_choice_is_unreadable_even_where_none_is_allowed(provider):
    # `choice` is required and never null; only the reserved none key abstains.
    null = canned(provider, "none")
    answer_in(provider, null, "speaker")["choice"] = None
    answer = read(provider)(null, SPEAKER_ONLY).answers["speaker"]
    assert (answer.answer, answer.reason, answer.detail) == (None, "unreadable", "")
    assert answer.distribution == {"characters:mara": 0.15, "characters:winifred": 0.25,
                                   decisions.NONE_KEY: 0.6}
    closed = Item(CONTEXT, (Choice("speaker", "Who speaks next?", (MARA, WINIFRED)),))
    bare = ({"answers": {"speaker": {"type": "choice", "choice": None}}} if provider == "openrouter"
            else {"answers": [{"type": "choice", "name": "speaker", "choice": None}]})
    assert read(provider)(bare, closed).answers["speaker"] == Answer(None, "unreadable")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_score_with_no_probabilities_is_unreadable(provider):
    weighted = canned(provider, "answered")
    del answer_in(provider, weighted, "tension")["probabilities"]
    assert read(provider)(weighted, ITEM).answers["tension"] == Answer(None, "unreadable")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_an_envelope_answering_no_question_is_a_bad_response(provider):
    with pytest.raises(LLMError) as exc:
        read(provider)(canned(provider, "unanswered"), ITEM)
    assert exc.value.kind == "bad_response"
    # Answers to questions nobody asked are not answers either.
    with pytest.raises(LLMError):
        read(provider)(stray_answer(provider), ITEM)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_malformed_body_is_a_bad_response(provider):
    # Each provider's answers in the OTHER provider's container is malformed too.
    other = ({"answers": [{"type": "noul", "name": "over", "noul": 0.9}]}
             if provider == "openrouter"
             else {"answers": {"over": {"type": "predicate", "probability": 0.9}}})
    for malformed in (canned(provider, "malformed"), [], "answers", None, other):
        with pytest.raises(LLMError) as exc:
            read(provider)(malformed, ITEM)
        assert exc.value.kind == "bad_response"


# ---- OpenAI's request and reply -------------------------------------------

def test_openai_decision_body_maps_each_question_type():
    assert openai_compatible.decision_body(ITEM, OPENAI_MODEL) == {
        "model": OPENAI_MODEL,
        "input": CONTEXT,
        "questions": [
            {"type": "predicate", "name": "over",
             "instructions": "Has the scene reached its end?"},
            {"type": "choice", "name": "speaker", "instructions": "Who speaks next?",
             "choices": [
                 {"value": "characters:mara", "description": "Mara, the cartographer."},
                 {"value": "characters:winifred", "description": "Winifred, the harbourmaster."},
                 {"value": "none", "description": decisions.NATIVE_NONE_TEXT}]},
            {"type": "score", "name": "tension", "instructions": "How tense is the exchange?",
             "levels": [{"label": "0", "description": "Calm."},
                        {"label": "1", "description": "Uneasy."},
                        {"label": "2", "description": "Heated."}]},
        ],
    }


def test_openai_maps_a_one_option_nullable_choice_to_two_choices():
    sent = openai_compatible.decision_body(Item(CONTEXT, (ONE_OPTION,)), OPENAI_MODEL)
    assert sent["questions"][0]["choices"] == [
        {"value": "characters:mara", "description": "Mara, the cartographer."},
        {"value": "none", "description": decisions.NATIVE_NONE_TEXT}]


def test_openai_decision_body_ignores_aliases_and_moves_the_reserved_none():
    aliased = Choice("speaker", "Who speaks next?",
                     (Option("characters:mara", "Mara, the cartographer.", ("the_mapmaker",)),
                      WINIFRED), allow_none=True)
    sent = openai_compatible.decision_body(Item(CONTEXT, (aliased,)), OPENAI_MODEL)
    assert sent == openai_compatible.decision_body(SPEAKER_ONLY, OPENAI_MODEL)
    assert "mapmaker" not in json.dumps(sent) and set(sent) == {"model", "input", "questions"}
    taken = Choice("speaker", "Who speaks next?", (Option("none", "Nobody in particular."), MARA),
                   allow_none=True)
    choices = openai_compatible.decision_body(Item(CONTEXT, (taken,)), OPENAI_MODEL)
    assert [c["value"] for c in choices["questions"][0]["choices"]] == [
        "none", "characters:mara", "none_2"]


def test_openai_refusal_is_per_question():
    result = openai_compatible.decision_result(openai_body("refused"), ITEM)
    assert result.answers["speaker"] == Answer(None, "refused")
    assert result.answers["over"] == Answer(False, probability=0.31)
    assert result.answers["tension"] == Answer(0, distribution={"0": 0.65, "1": 0.3, "2": 0.05})


def test_openai_confidence_is_not_carried():
    result = openai_compatible.decision_result(openai_body("answered"), ITEM)
    # Changing every confidence changes nothing: no field carries it.
    sure = openai_body("answered")
    for answer in sure["answers"]:
        answer["confidence"] = 1.0
    assert openai_compatible.decision_result(sure, ITEM) == result


def test_openai_a_name_answered_twice_is_unreadable_for_that_question():
    twice = openai_body("answered")
    twice["answers"].append({"type": "predicate", "name": "over", "probability": 0.1})
    result = openai_compatible.decision_result(twice, ITEM)
    assert result.answers["over"] == Answer(None, "unreadable")
    assert result.answers["speaker"].answer == "characters:winifred"


def test_openai_an_answer_of_another_type_is_unreadable():
    scored = openai_body("answered")
    answer_in("openai", scored, "over").update({"type": "score", "score": 1.0})
    assert openai_compatible.decision_result(scored, ITEM).answers["over"] == Answer(
        None, "unreadable")


def test_openai_choice_probabilities_listing_a_value_twice_are_dropped():
    doubled = openai_body("answered")
    answer_in("openai", doubled, "speaker")["probabilities"].append(
        {"value": "characters:mara", "probability": 0.0})
    speaker = openai_compatible.decision_result(doubled, ITEM).answers["speaker"]
    # The explicit choice still answers; the report it came with is dropped.
    assert speaker == Answer("characters:winifred")
    # Without an explicit choice there is then nothing to read.
    del answer_in("openai", doubled, "speaker")["choice"]
    assert openai_compatible.decision_result(doubled, ITEM).answers["speaker"] == Answer(
        None, "unreadable")


def test_openai_a_choice_answered_with_a_boolean_is_not_an_option():
    flagged = openai_body("answered")
    answer_in("openai", flagged, "speaker")["choice"] = True
    speaker = openai_compatible.decision_result(flagged, ITEM).answers["speaker"]
    assert (speaker.reason, speaker.detail) == ("unreadable", decisions.NOT_AN_OPTION)


def test_openai_score_probabilities_are_keyed_by_index_else_by_label():
    labelled = openai_body("answered")
    levels = answer_in("openai", labelled, "tension")["probabilities"]
    del levels[0]["value"]           # no index: its label "0" names the level
    levels[2]["value"] = 7           # out of range: its label "2" does
    assert openai_compatible.decision_result(labelled, ITEM).answers["tension"] == Answer(
        2, distribution={"0": 0.35, "1": 0.2, "2": 0.45})
    levels[1]["label"] = "Uneasy."   # an index in range wins over any label
    assert openai_compatible.decision_result(labelled, ITEM).answers["tension"].answer == 2
    levels[1]["value"] = 0           # two entries for level 0: the report is invalid
    assert openai_compatible.decision_result(labelled, ITEM).answers["tension"] == Answer(
        None, "unreadable")


def test_openai_answers_are_matched_by_name_not_by_position():
    shuffled = openai_body("answered")
    shuffled["answers"].reverse()
    shuffled["answers"].insert(0, {"type": "predicate", "name": None, "probability": 0.99})
    assert openai_compatible.decision_result(shuffled, ITEM) == openai_compatible.decision_result(
        openai_body("answered"), ITEM)


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


# ---- OpenAI's call ---------------------------------------------------------

async def test_openai_decide_posts_to_the_connections_base_url():
    answered = openai_body("answered")
    answered["echo"] = "data:image/png;base64,QUJDREVG"
    wire = Wire((200, answered))
    events: list[dict] = []
    client = openai_facade(wire, capture=lambda: events.append, retries=0)
    result = await client.decide_native(ITEM, OPENAI_CONN)
    assert result.answers["tension"].answer == 2
    assert len(wire.requests) == 1
    request = wire.requests[0]
    assert str(request.url) == "https://decisions.example.test/v1/decisions"
    assert request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {OPENAI_KEY}"
    # The reference documents no beta header, so none is sent.
    assert not any(name.lower().startswith("openai-") for name in request.headers)
    assert wire.sent() == openai_compatible.decision_body(ITEM, OPENAI_MODEL)
    assert [e["event"] for e in events] == ["start", "decision_body", "end"]
    captured = events[1]["payload"]
    assert "QUJDREVG" not in captured and "[elided]" in captured
    assert json.loads(captured)["answers"] == answered["answers"]
    assert events[-1]["payload"] == {"status": "complete"}
    text = json.dumps(events)
    assert OPENAI_KEY not in text and "example.test" not in text


async def test_an_openai_error_body_is_captured_and_mapped():
    wire = Wire((400, openai_body("error_400")))
    events: list[dict] = []
    with pytest.raises(LLMError) as exc:
        await openai_facade(wire, capture=lambda: events.append, retries=0).decide_native(
            ITEM, OPENAI_CONN)
    assert (exc.value.kind, exc.value.status) == ("bad_response", 400)
    assert exc.value.detail == ("Invalid value for 'questions[1].choices': "
                                "each choice must be unique.")
    assert [e["event"] for e in events] == ["start", "http_error_body", "end"]
    assert events[-1]["payload"] == {"status": "error"}


async def test_openai_usage_is_filed_as_reported_and_never_priced():
    holder: dict = {}
    counted: list[str] = []
    client = openai_facade(Wire((200, openai_body("answered"))), retries=0,
                           count_tokens=lambda text: counted.append(text) or 7)
    await client.decide_native(ITEM, OPENAI_CONN, holder)
    assert holder["prompt_tokens"] == 412 and holder["completion_tokens"] == 0
    assert holder["cache_read_tokens"] == 128 and holder["cache_write_tokens"] == 0
    for field in ("cost_usd", "cost_basis", llm_usage.ESTIMATED, llm_usage.ESTIMATE_KEY):
        assert field not in holder
    assert holder["model"] == OPENAI_MODEL and holder["provider"] == "openai_compatible"
    assert counted == []


async def test_an_openai_row_without_usage_files_no_counts():
    bare = openai_body("answered")
    del bare["usage"]
    holder: dict = {}
    counted: list[str] = []
    client = openai_facade(Wire((200, bare)), retries=0,
                           count_tokens=lambda text: counted.append(text) or 7)
    result = await client.decide_native(ITEM, OPENAI_CONN, holder)
    assert result.answers["over"].answer is True
    for field in ("prompt_tokens", "completion_tokens", "cache_read_tokens",
                  "cache_write_tokens", "cost_usd", "cost_basis", llm_usage.ESTIMATED,
                  llm_usage.ESTIMATE_KEY):
        assert field not in holder
    assert counted == []

    # One absent field files only itself.
    partial = openai_body("answered")
    del partial["usage"]["output_tokens"], partial["usage"]["input_tokens_details"]
    holder = {}
    await openai_facade(Wire((200, partial)), retries=0).decide_native(ITEM, OPENAI_CONN, holder)
    assert holder["prompt_tokens"] == 412
    assert "completion_tokens" not in holder and "cache_read_tokens" not in holder


async def test_an_openai_envelope_that_answers_nothing_still_files_its_usage():
    holder: dict = {}
    with pytest.raises(LLMError) as exc:
        await openai_facade(Wire((200, openai_body("unanswered"))), retries=0).decide_native(
            ITEM, OPENAI_CONN, holder)
    assert exc.value.kind == "bad_response" and holder["prompt_tokens"] == 412


async def test_openai_decide_refuses_a_missing_key_or_base_url_unsent():
    for conn in ({**OPENAI_CONN, "api_key": ""}, {**OPENAI_CONN, "base_url": ""}):
        wire = Wire((200, openai_body("answered")))
        with pytest.raises(LLMError) as exc:
            await openai_facade(wire).decide_native(ITEM, conn)
        assert exc.value.kind == "missing_key" and wire.requests == []


async def test_an_openai_read_timeout_is_a_timeout_and_not_retried():
    wire = Wire(httpx.ReadTimeout("slow"))
    with pytest.raises(LLMError) as exc:
        await openai_facade(wire, retries=2, timeout=5).decide_native(ITEM, OPENAI_CONN)
    assert exc.value.kind == "timeout" and len(wire.requests) == 1


async def test_an_openai_reply_that_is_not_json_is_a_bad_response():
    with pytest.raises(LLMError) as exc:
        await openai_facade(Wire((200, "<html>gateway</html>")), retries=0).decide_native(
            ITEM, OPENAI_CONN)
    assert exc.value.kind == "bad_response"


@pytest.mark.parametrize("status", [400, 403, 404])
async def test_a_refused_openai_request_does_not_mark_the_connection_failing(status):
    seen: list = []
    with pytest.raises(LLMError):
        await openai_facade(Wire((status, openai_body("error_400"))), retries=0,
                            observer=lambda c, e: seen.append((c, e))).decide_native(
            ITEM, OPENAI_CONN)
    assert seen == []


async def test_facade_decide_native_dispatches_openai_compatible():
    wire = Wire((429, {"error": {"message": "slow down", "type": "requests"}}),
                (200, openai_body("answered")))
    holder: dict = {}
    conn = {**OPENAI_CONN, "sampling": {"preset_id": "warm", "preset_name": "Warm",
                                        "params": {"temperature": 0.9}},
            FALLBACK_KEY: {**CONN}}
    seen: list = []
    result = await openai_facade(wire, observer=lambda c, e: seen.append(e)).decide_native(
        ITEM, conn, holder)
    assert result.backend == "native" and result.answers["over"].answer is True
    # Retried by the facade's rule, sent no sampling, never fell back.
    assert len(wire.requests) == 2 and holder["attempts"] == 2
    assert wire.sent() == openai_compatible.decision_body(ITEM, OPENAI_MODEL)
    assert FALLBACK_KEY not in holder[llm.ATTEMPTED]
    assert seen[-1] is None


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


async def test_decide_native_sends_each_kind_through_its_own_adapter():
    # Each registered kind reaches its own adapter with its own key: neither
    # connection's key is ever sent to the other provider.
    or_wire = Wire((200, body("answered")))
    oa_wire = Wire((200, openai_body("answered")))
    client = LLMClient(openrouter=adapter(or_wire), openai_compatible=openai_adapter(oa_wire))

    result = await client.decide_native(ITEM, OPENAI_CONN)
    assert result.answers["speaker"].answer == "characters:winifred"
    assert or_wire.requests == [] and len(oa_wire.requests) == 1
    assert oa_wire.requests[0].headers["authorization"] == f"Bearer {OPENAI_KEY}"

    await client.decide_native(ITEM, CONN)
    assert len(oa_wire.requests) == 1 and len(or_wire.requests) == 1
    assert or_wire.requests[0].headers["authorization"] == f"Bearer {KEY}"
    for request in (*or_wire.requests, *oa_wire.requests):
        other = OPENAI_KEY if request in or_wire.requests else KEY
        assert other not in json.dumps(dict(request.headers)) + request.content.decode()

    assert llm.native_body(ITEM, OPENAI_CONN) == openai_compatible.decision_body(
        ITEM, OPENAI_MODEL)
    assert llm.native_body(ITEM, CONN) == decision_body(ITEM, MODEL)


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
    text = json.dumps(llm.native_body(ITEM, OPENAI_CONN))
    assert OPENAI_KEY not in text and "https://" not in text and "example.test" not in text
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


async def test_fake_decide_native_accepts_openai_compatible():
    fake = FakeLLM([["x"]], decisions=[ItemResult({"over": Answer(True)})])
    holder: dict = {}
    result = await fake.decide_native(Item(CONTEXT, (OVER,)), OPENAI_CONN, holder)
    assert result.answers["over"].answer is True
    assert holder["provider"] == "openai_compatible"
    assert fake.native_requests == [(Item(CONTEXT, (OVER,)), OPENAI_CONN, None)]
