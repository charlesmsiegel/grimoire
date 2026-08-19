"""The scene-break detector's route half (#84).

What these pin, beyond the happy path: that the heuristic gate is the SERVER's
decision (so the client can fire after every turn and spend nothing), that the
detector never ends or splits anything by itself, that a dismissal moves the
watermark so the same posts cannot re-earn the same suggestion, and that a
transcript which changed under a question in flight does not get the answer
stamped onto it.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.llm_errors import LLMError
from grimoire.main import create_app
from grimoire.routes import scenes as scenes_routes

from .llm_fakes import FakeLLM

YES = '{"break": true, "reason": "The ledger changed hands.", "title": "The Long Walk Back"}'
NO = '{"break": false, "reason": "They are still mid-argument.", "title": ""}'


def _judge(*texts: str) -> FakeLLM:
    """The shared fake scripted with one turn per question (`llm_fakes.py` —
    this suite writes no fake of its own). With no texts it still needs a turn,
    since `FakeLLM` refuses an empty script: the tests that pass none are
    asserting the provider is never reached, so what the turn says is exactly
    what must not appear."""
    return FakeLLM([[t] for t in texts] or [[YES]])


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    return TestClient(app)


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
    """A usable LLM connection, so `_require_connection` stops being the answer."""
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
    assert llm.calls == 1
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
    assert _post(client, cid, sid)["asked"] is True and llm.calls == 1


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
    llm = _use(client, _judge())
    store.write_config(scene_break_every="0")
    cid, sid = _scene(client, posts=400)
    assert _get(client, cid, sid) == {
        "verdict": "", "reason": "", "title": "", "stale": False, "posts": 400,
        "score": 0, "signals": [], "every": 0, "due": False}
    assert _post(client, cid, sid, force="true")["asked"] is True and llm.calls == 1


def test_force_still_refuses_a_scene_with_nothing_new(client):
    """`force` overrides the threshold, never the emptiness: re-asking about a
    transcript that has not moved pays a provider to repeat itself."""
    _key(client)
    llm = _use(client, _judge(YES, NO))
    cid, sid = _scene(client, posts=40)
    assert _post(client, cid, sid, force="true")["asked"] is True
    assert _post(client, cid, sid, force="true")["asked"] is False
    assert llm.calls == 1


def test_a_missing_connection_is_refused_before_the_gate_is_consulted(client):
    """A 409 that only appeared once a scene happened to be due would be
    indistinguishable, on the client, from the quiet no-op that is this route's
    normal answer."""
    cid, sid = _scene(client, posts=3)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 409


def test_a_provider_failure_is_a_502_and_writes_nothing(client):
    _key(client)
    _use(client, FakeLLM([["ignored"]], error=LLMError("rate_limit", "rate limited")))
    cid, sid = _scene(client, posts=40)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 502 and r.json()["kind"] == "rate_limit"
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
    assert _post(client, cid, sid)["asked"] is False and llm.calls == 1
    _posts(cid, sid, 40, start=40)
    assert _post(client, cid, sid)["asked"] is True and llm.calls == 2


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

    async def _edit_mid_flight(*a, **k):
        store.scenes.edit_message(cid, sid, 0, "Something else entirely.")
        return YES

    llm.complete = _edit_mid_flight
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
    assert _post(client, cid, sid)["asked"] is True and llm.calls == 2


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
        store.rolling_summary.covered_digest(messages))
    stale = scenes_routes._break_commit(
        cid, sid, {"at": 40, "locs": 0, "times": 0},
        {"break": False, "reason": "stale no", "title": ""},
        store.rolling_summary.covered_digest(messages[:40]))
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
    digest = store.rolling_summary.covered_digest(messages)
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

    async def _append_mid_flight(*a, **k):
        _posts(cid, sid, 1, start=40)
        return YES

    llm.complete = _append_mid_flight
    assert _post(client, cid, sid)["asked"] is True
    assert _get(client, cid, sid)["verdict"] == "yes"
