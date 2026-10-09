"""The scene-break detector's route half (#84).

What these pin, beyond the happy path: that the heuristic gate is the SERVER's
decision (so the client can fire after every turn and spend nothing), that the
detector never ends or splits anything by itself, that a dismissal moves the
watermark so the same posts cannot re-earn the same suggestion, and that a
transcript which changed under a question in flight does not get the answer
stamped onto it.
"""

import importlib
import logging

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.decisions import Answer, ItemResult
from grimoire.llm_errors import LLMError
from grimoire.main import create_app
from grimoire.routes import scenes as scenes_routes

from .inference_fixtures import (
    OPENAI_DECIDER,
    SAME_PROVIDER,
    SPARE,
    decide_only,
    format2,
    neither,
    openai_decides_only,
    put_settings,
)
from .llm_fakes import FakeLLM, decision_reply

YES = decision_reply({"over": True}, rationales=["The ledger changed hands."])
NO = decision_reply({"over": False}, rationales=["They are still mid-argument."])

#: What the title call answers by default: the bare title.
TITLE = "The Long Walk Back"


def _judge(*verdicts: str, title: str = TITLE) -> FakeLLM:
    """The shared fake scripted with one turn per question (`llm_fakes.py` —
    this suite writes no fake of its own), and a title turn after each `YES`:
    a YES that lands is followed by the title call (`_break_title`), a NO is
    not. A test whose YES does NOT land scripts its own. With no verdicts it
    still needs a turn, since `FakeLLM` refuses an empty script: the tests that
    pass none are asserting the provider is never reached, so what the turn
    says is exactly what must not appear."""
    turns: list[list[str]] = []
    for verdict in verdicts:
        turns.append([verdict])
        if verdict == YES:
            turns.append([title])
    return FakeLLM(turns or [[YES]])


def _mid_flight(llm: FakeLLM, on_call: int, act) -> FakeLLM:
    """`llm`, with `act()` run as its `on_call`-th call (1-based) goes out:
    the transcript or the proposal moving while that call is with the
    provider. The call still answers from the script, and is still counted."""
    complete = llm.complete

    async def _complete(messages, conn, usage=None, *, schema=None):
        if llm.calls + 1 == on_call:
            act()
        return await complete(messages, conn, usage, schema=schema)

    llm.complete = _complete  # type: ignore[method-assign]
    return llm


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(app) as c:
        yield c


def _scene(client, posts=0):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    _posts(cid, sid, posts)
    return cid, sid


def _posts(cid: str, sid: str, n: int, start: int = 0, text: str = "Post") -> None:
    """`text` names the run, so a replay after a rewind can be DIFFERENT prose.
    Replaying the identical strings would leave the covered prefix genuinely
    unchanged, and the watermark genuinely still valid — a fixture that proves
    nothing about a rewind, which is what the first draft of the test below
    was."""
    for i in range(start, start + n):
        store.scenes.append_message(cid, sid, "user" if i % 2 == 0 else "assistant",
                                    f"{text} {i}.")


def _location(cid: str, name: str) -> str:
    """A campaign location, created through the store: `set_location` resolves
    the entity, so the scene cannot be moved somewhere that does not exist."""
    from grimoire.store import entities
    return entities.create_entity(store.campaigns.campaign_root(cid), "locations", name)


def _key(client):
    """A usable LLM connection, so `require_inference` stops being the answer."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})


def _use(client, llm):
    client.app.dependency_overrides[routes.get_llm] = lambda: llm
    return llm


def _get(client, cid, sid):
    return client.get(f"/api/campaigns/{cid}/scenes/{sid}/scene-break").json()


def _post(client, cid, sid, **params):
    return client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break", params=params).json()


# ---- GET: never spends a call ----
def test_get_on_a_fresh_scene_reports_the_empty_state(client):
    cid, sid = _scene(client, posts=3)
    assert _get(client, cid, sid) == {
        "verdict": "", "reason": "", "title": "", "stale": False, "posts": 3,
        "score": 0, "signals": [], "every": 20, "due": False}


def test_get_needs_no_llm_connection(client):
    """The inspector reads this on every scene select; a store with no key
    configured must still be able to render the panel."""
    cid, sid = _scene(client, posts=3)
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/scene-break").status_code == 200


def test_get_on_an_unknown_scene_is_404(client):
    cid, _ = _scene(client)
    r = client.get("/api/campaigns/%s/scenes/nope/scene-break" % cid)
    # The detail, not just the status: an unrouted path is also a 404, so this
    # would pass against a route that does not exist at all.
    assert r.status_code == 404 and r.json()["detail"] == "scene not found"


def test_get_shows_the_signals_it_would_ask_about(client):
    cid, sid = _scene(client, posts=20)
    store.scenes.set_location(cid, sid, _location(cid, "The Salt Gate"))
    store.scenes.set_location(cid, sid, _location(cid, "The Long Dock"))
    body = _get(client, cid, sid)
    assert body["due"] is True
    assert [s["kind"] for s in body["signals"]] == ["length", "location"]


# ---- POST: the gate ----
def test_a_turn_short_of_the_cadence_spends_nothing(client):
    """The client fires this after every turn, so the ordinary case is a no-op.
    It must not reach the model — that is what makes firing per turn free."""
    _key(client)
    llm = _use(client, _judge())
    cid, sid = _scene(client, posts=19)
    body = _post(client, cid, sid)
    assert body["asked"] is False and body["verdict"] == ""
    assert llm.calls == 0


def test_a_long_scene_that_never_moved_still_gets_asked_about(client):
    """Length reaches the bar on its own at twice the cadence. A scene can be
    over without anybody moving or the clock jumping."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    body = _post(client, cid, sid)
    assert llm.calls == 2          # the verdict, then the title
    assert body["asked"] is True and body["verdict"] == "yes"
    assert body["reason"] == "The ledger changed hands."
    assert body["title"] == "The Long Walk Back"


def test_a_move_at_the_cadence_fires_where_a_move_alone_does_not(client):
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=4)
    store.scenes.set_location(cid, sid, _location(cid, "The Salt Gate"))
    store.scenes.set_location(cid, sid, _location(cid, "The Long Dock"))
    assert _post(client, cid, sid)["asked"] is False and llm.calls == 0
    _posts(cid, sid, 20, start=4)
    assert _post(client, cid, sid)["asked"] is True and llm.calls == 2


def test_the_model_is_allowed_to_say_no(client):
    """The heuristic says "worth asking"; the model answers. A detector whose
    counts were also its verdict would fire every time a party walked through a
    door."""
    _key(client)
    _use(client, _judge(NO))
    cid, sid = _scene(client, posts=40)
    body = _post(client, cid, sid)
    assert body["asked"] is True and body["verdict"] == "no"
    assert body["reason"] == "They are still mid-argument." and body["title"] == ""


def test_a_no_is_remembered_so_the_same_posts_are_not_re_asked(client):
    _key(client)
    llm = _use(client, _judge(NO))
    cid, sid = _scene(client, posts=40)
    _post(client, cid, sid)
    assert _post(client, cid, sid)["asked"] is False and llm.calls == 1
    # ...and the standing answer is readable without spending anything.
    assert _get(client, cid, sid)["verdict"] == "no"


def test_the_scene_is_never_ended_or_split_by_the_detector(client):
    """Every continuity feature here proposes and waits. A confirmed break is a
    suggestion in the inspector — the transcript, the scene's `done` flag and
    the campaign's scene list are all exactly as they were."""
    _key(client)
    _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    before = store.scenes.read_scene(cid, sid)["messages"]
    _post(client, cid, sid)
    assert store.scenes.read_scene(cid, sid)["messages"] == before
    assert [s["id"] for s in store.scenes.list_scenes(cid)] == [sid]
    assert store.scenes.list_scenes(cid)[0]["done"] is False


def test_zero_turns_the_feature_off_and_force_cannot_reopen_it(client):
    """0 is a documented setting, not a missing one — including against the
    panel's own button, which is what `force` is."""
    _key(client)
    llm = _use(client, _judge(YES))
    store.write_config(scene_break_every="0")
    cid, sid = _scene(client, posts=400)
    assert _get(client, cid, sid) == {
        "verdict": "", "reason": "", "title": "", "stale": False, "posts": 400,
        "score": 0, "signals": [], "every": 0, "due": False}
    assert _post(client, cid, sid, force="true")["asked"] is True and llm.calls == 2


def test_force_still_refuses_a_scene_with_nothing_new(client):
    """`force` overrides the threshold, never the emptiness: re-asking about a
    transcript that has not moved pays a provider to repeat itself."""
    _key(client)
    llm = _use(client, _judge(YES, NO))
    cid, sid = _scene(client, posts=40)
    assert _post(client, cid, sid, force="true")["asked"] is True
    assert _post(client, cid, sid, force="true")["asked"] is False
    assert llm.calls == 2


def test_force_does_not_pay_to_judge_a_span_of_hidden_posts(client):
    """A post hidden from context is not in the question's prompt, so a forced
    check over a span that is all hidden would ask about an empty transcript."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=4)
    for i in range(4):
        store.scenes.set_excluded(cid, sid, i, True)
    assert _post(client, cid, sid, force="true")["asked"] is False
    assert llm.calls == 0
    store.scenes.set_excluded(cid, sid, 3, False)           # the control
    assert _post(client, cid, sid, force="true")["asked"] is True
    assert llm.calls == 2


def test_hidden_posts_neither_reach_nor_inflate_the_automatic_gate(client):
    """The automatic gate counts posts in context: a span that is all hidden
    asks about nothing, and hidden posts padding a short visible run must not
    buy the length signal the visible posts alone have not earned."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    for i in range(40):
        store.scenes.set_excluded(cid, sid, i, True)
    body = _post(client, cid, sid)
    assert body["asked"] is False and body["posts"] == 0 and body["due"] is False
    for i in range(20):                                     # 20 visible, 20 hidden
        store.scenes.set_excluded(cid, sid, i, False)
    body = _post(client, cid, sid)
    assert body["asked"] is False and body["posts"] == 20 and body["due"] is False
    assert llm.calls == 0
    for i in range(20, 40):                                 # the control: 40 visible
        store.scenes.set_excluded(cid, sid, i, False)
    assert _post(client, cid, sid)["asked"] is True and llm.calls == 2


def test_a_missing_connection_is_refused_before_the_gate_is_consulted(client):
    """A 409 that only appeared once a scene happened to be due would be
    indistinguishable, on the client, from the quiet no-op that is this route's
    normal answer."""
    cid, sid = _scene(client, posts=3)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 409


def test_a_provider_failure_reports_its_kind_and_writes_nothing(client):
    _key(client)
    _use(client, FakeLLM([["ignored"]], error=LLMError("rate_limit", "rate limited")))
    cid, sid = _scene(client, posts=40)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 429 and r.json()["kind"] == "rate_limit"   # (#213)
    assert _get(client, cid, sid)["verdict"] == ""


def test_a_negative_bound_is_rejected_rather_than_wrapped(client):
    _key(client)
    _use(client, _judge())
    cid, sid = _scene(client, posts=40)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break", params={"upto": -1})
    assert r.status_code == 400


def test_a_bounded_question_ignores_posts_past_its_bound(client):
    """The play loop releases the scene before firing this, so a fast next send
    can append an unanswered player post — and a question that took that post
    as the scene's END would be asking about a beat whose reply had not
    arrived."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=41)
    _post(client, cid, sid, upto=40)
    assert "Post 40." not in llm.requests[0]["messages"][1]["content"]
    assert _get(client, cid, sid)["posts"] == 1     # the unanswered post is still pending


# ---- what the question was asked about ----
def test_the_prompt_carries_only_the_posts_since_the_last_question(client):
    """Re-sending three hundred posts to ask whether the last twenty resolved
    anything would make the cheap half of this feature pointless."""
    _key(client)
    llm = _use(client, _judge(NO, YES))
    cid, sid = _scene(client, posts=40)
    _post(client, cid, sid)
    _posts(cid, sid, 40, start=40)
    _post(client, cid, sid)
    second = llm.requests[1]["messages"][1]["content"]
    assert "Post 39." not in second and "Post 79." in second


def test_the_prompt_carries_the_scene_facts_the_transcript_cannot(client):
    """A scene's first location and first date are set SILENTLY, so on the
    scenes that never move the transcript says neither."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    store.scenes.set_location(cid, sid, _location(cid, "The Salt Gate"))
    _post(client, cid, sid)
    assert "The Salt Gate" in llm.requests[0]["messages"][1]["content"]


# ---- dismissal ----
def test_dismissing_retires_the_proposal_and_moves_the_watermark(client):
    """"Not here" is an answer about the scene as it stands, so the count
    starts again from there — otherwise the very same posts re-earn the very
    same suggestion on the next turn."""
    _key(client)
    llm = _use(client, _judge(YES, YES))
    cid, sid = _scene(client, posts=40)
    _post(client, cid, sid)
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break/dismiss").json()
    assert body["verdict"] == "" and body["posts"] == 0 and body["due"] is False
    assert _post(client, cid, sid)["asked"] is False and llm.calls == 2
    _posts(cid, sid, 40, start=40)
    assert _post(client, cid, sid)["asked"] is True and llm.calls == 4


def test_dismissing_also_forgets_the_moves_it_was_asked_about(client):
    """The location watermark moves with the transcript one, or a dismissed
    suggestion re-earns its location point on the very next evaluation."""
    cid, sid = _scene(client, posts=20)
    store.scenes.set_location(cid, sid, _location(cid, "The Salt Gate"))
    store.scenes.set_location(cid, sid, _location(cid, "The Long Dock"))
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break/dismiss")
    _posts(cid, sid, 20, start=22)
    body = _get(client, cid, sid)
    assert [s["kind"] for s in body["signals"]] == ["length"]


def test_dismissing_an_unknown_scene_is_404(client):
    cid, _ = _scene(client)
    r = client.post(f"/api/campaigns/{cid}/scenes/nope/scene-break/dismiss")
    assert r.status_code == 404 and r.json()["detail"] == "scene not found"


# ---- the write is verified, not asserted ----
def test_an_answer_about_a_transcript_that_changed_underneath_is_not_stored(client):
    """`delete_scene` frees a scene's id and the numbering reuses it, and an
    edit inside the covered prefix rewrites prose the question was about. A
    proposal is prose ABOUT a story, so landing one on a different one puts a
    suggestion under a reason nobody can place."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    _mid_flight(llm, 1, lambda: store.scenes.edit_message(
        cid, sid, 0, "Something else entirely."))
    body = _post(client, cid, sid)
    assert body["asked"] is False and body["verdict"] == ""
    assert _get(client, cid, sid)["verdict"] == ""


def test_a_standing_answer_about_deleted_posts_is_presented_as_behind(client):
    """The prose stays — it is still the best thing anyone has — but it stops
    claiming to be about the scene on screen. A verdict whose watermark was
    voided reasoned about posts the player has since cut."""
    _key(client)
    _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    assert _post(client, cid, sid)["stale"] is False
    store.scenes.delete_from(cid, sid, 10)
    behind = _get(client, cid, sid)
    assert behind["verdict"] == "yes" and behind["stale"] is True
    assert behind["reason"] == "The ledger changed hands."


def test_the_model_sees_everything_the_answer_will_claim_to_cover(client):
    """After a rewind the scene is scored from zero and the answer is recorded
    as covering the whole transcript. Slicing the prompt from the OLD watermark
    would show the model the last few posts while the verdict went on file as a
    verdict about all of them."""
    _key(client)
    llm = _use(client, _judge(NO, YES))
    cid, sid = _scene(client, posts=40)
    _post(client, cid, sid)
    store.scenes.delete_from(cid, sid, 10)
    _posts(cid, sid, 40, start=10, text="Retake")
    _post(client, cid, sid)
    asked_about = llm.requests[1]["messages"][1]["content"]
    assert "Post 0." in asked_about and "Retake 49." in asked_about


def test_a_rewind_does_not_silence_the_detector_for_the_rest_of_the_scene(client):
    """The watermark is a claim about SPECIFIC posts, and a bare count cannot
    make it. Rewound from 40 to 10 and played back up to 35, this used to
    report nothing new for twenty-five posts of real story — and went on
    reporting nothing until the count passed 40 again."""
    _key(client)
    llm = _use(client, _judge(NO, YES))
    cid, sid = _scene(client, posts=40)
    _post(client, cid, sid)
    assert _get(client, cid, sid)["posts"] == 0
    store.scenes.delete_from(cid, sid, 10)
    _posts(cid, sid, 40, start=10, text="Retake")
    assert _get(client, cid, sid)["posts"] == 50        # the whole scene is unasked again
    assert _post(client, cid, sid)["asked"] is True and llm.calls == 3


def test_an_answer_about_fewer_posts_cannot_overwrite_a_newer_one(client):
    """Two questions can be in flight at once — the panel's button beside the
    play loop's — and the newer can finish first. The older one's prefix is
    still intact, because everything since is an APPEND, so nothing about the
    transcript refuses it. What refuses it is that the scene has already been
    answered about MORE."""
    _key(client)
    _use(client, _judge(YES))
    cid, sid = _scene(client, posts=45)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    scenes_routes._break_commit(
        cid, sid, {"at": 45, "locs": 0, "times": 0},
        {"break": True, "reason": "the player asked, and it was yes", "title": "Next"},
        store.rolling_summary.covered_digest(
            messages, store.appearances.player_label(cid, sid)))
    stale = scenes_routes._break_commit(
        cid, sid, {"at": 40, "locs": 0, "times": 0},
        {"break": False, "reason": "stale no", "title": ""},
        store.rolling_summary.covered_digest(
            messages[:40], store.appearances.player_label(cid, sid)))
    assert stale["landed"] is False
    kept = store.scenes.get_scene_break(cid, sid)
    assert kept["verdict"] == "yes" and kept["at"] == 45


def test_a_dismissal_is_not_undone_by_a_question_that_was_already_out(client):
    """"Not here" moves the watermark, so the same rule catches it: a proposal
    the player waved off must not be resurrected by an answer to a question
    that left before they said so."""
    _key(client)
    _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    digest = store.rolling_summary.covered_digest(
        messages, store.appearances.player_label(cid, sid))
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break/dismiss")
    late = scenes_routes._break_commit(
        cid, sid, {"at": 40, "locs": 0, "times": 0},
        {"break": True, "reason": "too late", "title": "No"}, digest)
    assert late["landed"] is False
    assert store.scenes.get_scene_break(cid, sid)["verdict"] == ""


def test_a_post_landing_during_the_question_does_not_throw_the_answer_away(client):
    """An ordinary turn appending during the call must pass, or a busy scene
    could never be asked about at all."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    _mid_flight(llm, 1, lambda: _posts(cid, sid, 1, start=40))
    assert _post(client, cid, sid)["asked"] is True
    assert _get(client, cid, sid)["verdict"] == "yes"
    assert _get(client, cid, sid)["title"] == TITLE


# ---- the verdict is a decision; the title is a second call (slice F) ----
def _rows(task: str = "") -> list[dict]:
    """Today's ledger rows, oldest first; `task` narrows them."""
    return [r for r in store.usage.calls(days=1) if not task or r.get("task") == task]


def test_a_yes_drafts_the_title_in_a_second_call_on_the_summary_route(client):
    """The verdict is a closed question, so the title -- which a closed
    question cannot carry -- is drafted by its own call, metered under
    `scene-break-title`, which the `summary` route claims. What it stores is
    the cleaned title, not the reply."""
    _key(client)
    llm = _use(client, _judge(YES, title='"The Long Walk Back."\nIt ends at the gate.'))
    cid, sid = _scene(client, posts=40)
    body = _post(client, cid, sid)
    assert body["verdict"] == "yes" and body["title"] == "The Long Walk Back"
    assert store.scenes.get_scene_break(cid, sid)["title"] == "The Long Walk Back"
    assert [r["task"] for r in _rows()] == ["scene-break", "scene-break-title"]
    assert store.routing.route("scene-break-title").key == "summary"
    decide_call, title_call = llm.requests
    # The verdict asked for a schema; the title is plain prose.
    assert llm.schemas[0] is not None and llm.schemas[1] is None
    assert title_call["messages"] == store.scene_break.build_title_prompt(
        store.chronicle.transcript_text(
            store.regex.view.view(store.scenes.read_scene(cid, sid)["messages"], cid=cid,
                                  phase="prompt", offset=0, total=40),
            store.appearances.player_label(cid, sid)),
        store.chronicle.scene_facts(cid, sid), "Saltmarch", "The ledger changed hands.")
    assert "has the scene reached a natural place to stop?" in \
        decide_call["messages"][1]["content"]


def test_a_no_makes_no_title_call(client):
    _key(client)
    llm = _use(client, _judge(NO))
    cid, sid = _scene(client, posts=40)
    assert _post(client, cid, sid)["verdict"] == "no"
    assert llm.calls == 1
    assert [r["task"] for r in _rows()] == ["scene-break"]


def test_a_verdict_that_did_not_land_makes_no_title_call(client):
    """A title is paid for only once its verdict is on file: an edit inside
    the prefix while the question is out voids the verdict, and with it the
    reason to name anything."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    _mid_flight(llm, 1, lambda: store.scenes.edit_message(
        cid, sid, 0, "Something else entirely."))
    assert _post(client, cid, sid)["asked"] is False
    assert llm.calls == 1
    assert store.scenes.get_scene_break(cid, sid)["verdict"] == ""


def test_a_title_is_dropped_when_the_verdict_moved_on(client):
    """The title is written by a second guarded write, which lands only on the
    very verdict it was drafted for. A dismissal that lands while the title is
    out wins: the proposal on file is the dismissal's, with no title."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    _mid_flight(llm, 2, lambda: store.scenes.dismiss_scene_break(cid, sid))
    body = _post(client, cid, sid)
    assert llm.calls == 2
    stored = store.scenes.get_scene_break(cid, sid)
    assert stored["verdict"] == "" and stored["title"] == "" and stored["at"] == 40
    assert body["verdict"] == "" and body["title"] == ""


def test_a_failed_title_keeps_the_verdict(client):
    """A title that fails -- the provider, or a route that cannot resolve --
    leaves the verdict on file with an empty title. The title's own meter
    files a provider failure."""
    _key(client)
    _use(client, FakeLLM([[YES], [TITLE]], error=LLMError("rate_limit", "rate limited"),
                         fail_after=1))
    cid, sid = _scene(client, posts=40)
    body = _post(client, cid, sid)
    assert body["asked"] is True and body["verdict"] == "yes" and body["title"] == ""
    stored = store.scenes.get_scene_break(cid, sid)
    assert stored["verdict"] == "yes" and stored["title"] == ""
    assert stored["reason"] == "The ledger changed hands."
    failed = _rows("scene-break-title")
    assert len(failed) == 1 and failed[0]["status"] == "error"


@pytest.mark.parametrize("failure", [store.locks.StoreBusy, OSError],
                         ids=["busy", "io"])
def test_a_title_write_that_fails_keeps_the_answer_the_verdicts(client, monkeypatch,
                                                               caplog, failure):
    """CODE-M2: the verdict landed before the title was drafted, so a title
    WRITE that fails -- a busy store, an unwritable file -- is the title lost,
    not the question: the answer is the verdict's, with an empty title, never
    a 409 or a 500 over a proposal that is already on file. Logged by kind."""
    _key(client)
    _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)

    def refuse(*_args, **_kwargs):
        raise failure("the title could not be written")

    monkeypatch.setattr(scenes_routes, "_break_title_commit", refuse)
    with caplog.at_level(logging.INFO, logger="grimoire.routes.scenes"):
        r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["asked"] is True and body["verdict"] == "yes" and body["title"] == ""
    stored = store.scenes.get_scene_break(cid, sid)
    assert stored["verdict"] == "yes" and stored["title"] == ""
    logged = [rec.getMessage() for rec in caplog.records
              if "scene-break title" in rec.getMessage()]
    want = f"scene-break title not written for {cid}/{sid}: {failure.__name__}"
    assert logged == [want]


def test_the_title_path_reads_and_renders_off_the_event_loop(client, monkeypatch):
    """CODE-M1: `_break_once` is awaited on the lifespan loop by the follow-up
    every landed turn schedules, so the store reads and template renders it
    adds -- the title route's resolution (`config.md`, the connections, the
    catalog sidecar), the title prompt, and the verdict item's -- run in a
    worker thread, never on the loop thread."""
    import asyncio
    import threading

    _key(client)
    llm = _judge(YES)
    cid, sid = _scene(client, posts=40)
    ran_on: dict[str, int] = {}

    def spy(name, real):
        def wrapper(*args, **kwargs):
            ran_on[name] = threading.get_ident()
            return real(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(scenes_routes, "_soft_resolved",
                        spy("resolve", scenes_routes._soft_resolved))
    for name in ("build_title_prompt", "build_item", "explain"):
        monkeypatch.setattr(store.scene_break, name,
                            spy(name, getattr(store.scene_break, name)))

    async def go():
        body = await scenes_routes._break_once(cid, sid, True, None, llm)
        return threading.get_ident(), body

    loop_thread, body = asyncio.run(go())
    assert body["title"] == TITLE
    assert set(ran_on) == {"resolve", "build_title_prompt", "build_item", "explain"}
    assert loop_thread not in ran_on.values(), ran_on


def test_a_title_route_that_cannot_resolve_keeps_the_verdict(client, caplog):
    """The other half of the failed title: the `summary` route pinned at a
    provider with no key. The verdict's route is untouched, so it is asked
    and stored; the title is skipped without a call -- and so without a ledger
    row, which is why it is logged: by the refusal's kind, never its sentence,
    which names the provider and model."""
    format2(client)
    client.post("/api/llm-connections", json={"kind": "openrouter", "name": "keyless"})
    put_settings(client, {"routes": {"summary": {"use": "model", "pin": {
        "provider": "keyless", "model": "vendor/keyless"}}}})
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    with caplog.at_level(logging.INFO, logger="grimoire.routes.scenes"):
        body = _post(client, cid, sid)
    assert body["verdict"] == "yes" and body["title"] == ""
    assert store.scenes.get_scene_break(cid, sid)["verdict"] == "yes"
    assert llm.calls == 1 and _rows("scene-break-title") == []
    skipped = [r.getMessage() for r in caplog.records
               if "scene-break title skipped" in r.getMessage()]
    want = (f"scene-break title skipped for {cid}/{sid}: the summary route "
            "cannot run (missing_key)")
    assert skipped == [want]
    assert "keyless" not in skipped[0] and "vendor/" not in skipped[0]


@pytest.mark.parametrize("seam", ["_soft_resolved", "build_title_prompt"])
def test_a_title_that_cannot_be_prepared_keeps_the_verdict(client, monkeypatch, caplog,
                                                           seam):
    """Brutal-1 nit: the YES verdict lands before the title is drafted, so an
    unreadable store file while resolving the title's route, or a broken
    override template, is the title lost -- never a 500 over a verdict already
    on file. Logged by kind; no title call is made."""
    _key(client)
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)

    def broken(*_args, **_kwargs):
        raise OSError("unreadable")

    target = scenes_routes if seam == "_soft_resolved" else store.scene_break
    monkeypatch.setattr(target, seam, broken)
    with caplog.at_level(logging.INFO, logger="grimoire.routes.scenes"):
        r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "yes" and r.json()["title"] == ""
    assert store.scenes.get_scene_break(cid, sid)["verdict"] == "yes"
    assert llm.calls == 1 and _rows("scene-break-title") == []
    logged = [rec.getMessage() for rec in caplog.records
              if "scene-break title" in rec.getMessage()]
    assert logged == [f"scene-break title skipped for {cid}/{sid}: OSError"]


def test_a_title_that_resolves_logs_nothing(client, caplog):
    _key(client)
    _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    with caplog.at_level(logging.INFO, logger="grimoire.routes.scenes"):
        assert _post(client, cid, sid)["title"] == TITLE
    assert not [r for r in caplog.records if "scene-break title" in r.getMessage()]


# ---- the Decision role (slice F: the scene_break route decides) ----
#: A native endpoint's YES: no rationale, which a native backend never has.
NATIVE_YES = ItemResult({"over": Answer(True)})

#: What a native endpoint answers for a model it has no decisions for.
NO_ENDPOINT = LLMError("bad_response", "no decisions endpoint for this model", status=404)


@pytest.mark.parametrize("on", [SPARE, SAME_PROVIDER], ids=["spare", "same-provider"])
def test_a_decide_only_decision_model_answers_natively(client, on):
    """Slice H: a Decision model that cannot generate is answered by its
    provider's native decisions endpoint. The verdict comes from there, the
    role fallback is not asked, and the one completion is the title's."""
    decide_only(client, fallback=True, on=on)
    llm = _use(client, FakeLLM([[TITLE]], decisions=[NATIVE_YES]))
    cid, sid = _scene(client, posts=40)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "yes"
    [(_item, conn, _retries)] = llm.native_requests
    assert (conn["id"], conn["model"]) == ("openrouter", "vendor/decider")
    [title_call] = llm.requests
    assert llm.schemas == [None]
    assert (title_call["conn"]["id"], title_call["conn"]["model"]) == \
        ("openrouter", "vendor/active")


@pytest.mark.parametrize("on", [SPARE, SAME_PROVIDER], ids=["spare", "same-provider"])
def test_a_decide_only_decision_model_falls_to_the_role_fallback(client, on):
    """Review Focus 1: when the native endpoint fails, the role fallback that
    generates answers in its place -- on another provider or its own (spec
    I-1, a stage of its own rather than a retry)."""
    decide_only(client, fallback=True, on=on)
    llm = _use(client, FakeLLM([[YES], [TITLE]], decisions=[NO_ENDPOINT]))
    cid, sid = _scene(client, posts=40)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "yes"
    assert [c["model"] for _i, c, _r in llm.native_requests] == ["vendor/decider"]
    decide_call, title_call = llm.requests
    assert (decide_call["conn"]["id"], decide_call["conn"]["model"]) == on
    assert all(req["conn"].get("model") != "vendor/decider" for req in llm.requests)
    # The title is the summary route's: Fast, inheriting Primary.
    assert (title_call["conn"]["id"], title_call["conn"]["model"]) == \
        ("openrouter", "vendor/active")


def test_a_decide_only_decision_model_without_a_fallback_answers_natively(client):
    decide_only(client, fallback=False)
    llm = _use(client, FakeLLM([[TITLE]], decisions=[NATIVE_YES]))
    cid, sid = _scene(client, posts=40)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "yes"
    assert len(llm.native_requests) == 1 and llm.schemas == [None]


def test_an_openai_model_the_user_marks_decisions_only_answers_natively(client):
    """Brutal review H (2-Y1): the OpenAI preset says every model generates,
    so a decisions-only model there resolves structured until the user says
    otherwise. Their capability override `generate: no` outranks the preset,
    the model is native-only (spec 5.3), and the route's decision is answered
    through `decide_native` on that provider -- the OpenAI adapter reached
    without naming any model in code."""
    conn_id = openai_decides_only(client)
    decided = store.inference.resolve.resolve("scene-break", operation="decide")
    assert decided.attempts[0].capabilities["generate"][:2] == ("yes", "preset")
    assert decided.attempts[0].decision_mode == "structured"
    got = client.put(f"/api/llm-connections/{conn_id}/facts",
                     json={"model": OPENAI_DECIDER, "overrides": {"generate": "no"}})
    assert got.status_code == 200, got.text
    decided = store.inference.resolve.resolve("scene-break", operation="decide")
    first = decided.attempts[0]
    assert first.capabilities["generate"][:2] == ("no", "user")
    assert first.decision_mode == "native" and first.provider_preset == "openai"
    llm = _use(client, FakeLLM([[TITLE]], decisions=[NATIVE_YES]))
    cid, sid = _scene(client, posts=40)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "yes"
    [(_item, conn, _retries)] = llm.native_requests
    assert (conn["id"], conn["kind"], conn["model"]) == (
        conn_id, "openai_compatible", OPENAI_DECIDER)
    assert llm.schemas == [None]       # the one completion is the title's
    (row,) = _rows("scene-break")
    assert (row["operation"], row["decision_mode"]) == ("decide", "native")


def test_a_decision_model_that_can_do_neither_is_refused(client):
    """I8: a model that can neither generate nor decide natively is the one
    the seam refuses, before anything is sent."""
    neither(client)
    llm = _use(client, FakeLLM([[YES]], decisions=[NATIVE_YES]))
    cid, sid = _scene(client, posts=40)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["kind"] == "incapable"
    assert body["detail"].startswith("The Scene-break checks route runs on the Decision "
                                     "role (vendor/neither on OpenRouter), which cannot "
                                     "generate text or make native decisions")
    assert llm.calls == 0 and llm.native_requests == []


def test_the_decision_role_now_serves_scene_break(client):
    """The route's default flipped from Fast to Decision, and a Decision role
    set on its own is what the verdict runs on -- the title stays on the
    summary route."""
    format2(client)
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "spare", "model": "vendor/spare"}}}})
    llm = _use(client, _judge(YES))
    cid, sid = _scene(client, posts=40)
    assert _post(client, cid, sid)["verdict"] == "yes"
    decide_call, title_call = llm.requests
    assert (decide_call["conn"]["id"], decide_call["conn"]["model"]) == \
        ("spare", "vendor/spare")
    assert title_call["conn"]["id"] == "openrouter"


def test_scene_break_on_a_legacy_store_resolves_as_before(client):
    """A format-1 store has no Decision role: it inherits Fast, then Primary,
    and the legacy `route_summary` key still moves the verdict -- and the
    title, which lives on that very route."""
    _key(client)
    client.post("/api/llm-connections", json={"kind": "openrouter", "name": "spare",
                                              "api_key": "sk-spare",
                                              "model": "vendor/spare"})
    llm = _use(client, _judge(YES, YES))
    cid, sid = _scene(client, posts=40)
    assert _post(client, cid, sid)["verdict"] == "yes"
    assert [req["conn"]["id"] for req in llm.requests] == ["openrouter", "openrouter"]
    store.write_config(route_summary="spare")
    _posts(cid, sid, 40, start=40)
    assert _post(client, cid, sid)["verdict"] == "yes"
    assert [req["conn"]["id"] for req in llm.requests[2:]] == ["spare", "spare"]


def test_each_write_bumps_the_campaign_revision_and_a_refused_title_does_not(client):
    """Both writes are a background run's, with no response line for the
    middleware to stamp (#409), so each bumps where it writes. A title whose
    verdict is no longer the one on file writes nothing, and stamps nothing."""
    cid, sid = _scene(client, posts=40)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    digest = store.rolling_summary.covered_digest(
        messages, store.appearances.player_label(cid, sid))
    watermark = {"at": 40, "locs": 0, "times": 0}
    before = store.revision.current(cid)
    verdict = scenes_routes._break_commit(
        cid, sid, watermark, {"break": True, "reason": "It resolved.", "title": ""}, digest)
    assert verdict["landed"] is True
    after_verdict = store.revision.current(cid)
    assert after_verdict != before
    # A watermark that differs only in its moves or advances is another
    # question's: nothing is written, nothing is stamped.
    for moved in ({**watermark, "locs": 1}, {**watermark, "times": 1}):
        other = scenes_routes._break_title_commit(cid, sid, moved, digest, "It resolved.",
                                                  "Not This One")
        assert store.scenes.scene_break_fields(other["scene"]["meta"])["title"] == ""
        assert store.revision.current(cid) == after_verdict
    titled = scenes_routes._break_title_commit(cid, sid, watermark, digest, "It resolved.",
                                               "The Long Walk Back")
    assert store.scenes.scene_break_fields(titled["scene"]["meta"])["title"] == \
        "The Long Walk Back"
    after_title = store.revision.current(cid)
    assert after_title != after_verdict
    # Titled already: the verdict it was drafted for no longer reads title "".
    again = scenes_routes._break_title_commit(cid, sid, watermark, digest, "It resolved.",
                                              "Another Name")
    assert store.scenes.scene_break_fields(again["scene"]["meta"])["title"] == \
        "The Long Walk Back"
    assert store.revision.current(cid) == after_title
