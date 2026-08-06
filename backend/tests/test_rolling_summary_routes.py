"""The rolling per-scene summary's route half (#85).

What these pin, beyond the happy path: that the refresh gate is the SERVER's
decision (so the client can fire after every turn and spend nothing), that a
refusal is an ordinary status code rather than something that can take a turn
down, and that a transcript which changed under a stored summary makes the fold
start over instead of carrying prose about a post the player deleted.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.llm_errors import LLMError
from grimoire.main import create_app


class FakeLLM:
    """Records every prompt it is handed and answers from a canned list."""

    def __init__(self, *texts):
        self.texts = list(texts) or ["A summary."]
        self.prompts: list[list[dict]] = []

    async def stream(self, messages, cfg):
        yield "reply"

    async def complete(self, messages, cfg):
        self.prompts.append(messages)
        return self.texts[min(len(self.prompts) - 1, len(self.texts) - 1)]

    @property
    def calls(self) -> int:
        return len(self.prompts)


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
    for n in range(posts):
        store.scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant",
                                    f"Post {n}.")
    return cid, sid


def _key(client):
    """A usable LLM connection, so `_require_connection` stops being the answer."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})


def _use(client, llm):
    client.app.dependency_overrides[routes.get_llm] = lambda: llm
    return llm


# ---- GET: never spends a call ----
def test_get_on_a_fresh_scene_reports_the_empty_state(client):
    cid, sid = _scene(client, posts=3)
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()
    assert body == {"summary": "", "at": 0, "total": 3, "stale": False,
                    "every": 10, "due": False}


def test_get_reports_due_once_the_scene_has_run_far_enough(client):
    cid, sid = _scene(client, posts=10)
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()["due"] is True


def test_get_needs_no_llm_connection(client):
    """The panel reads on every scene select; a store with no key configured
    must still be able to render it."""
    cid, sid = _scene(client, posts=3)
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").status_code == 200


def test_get_on_an_unknown_scene_is_404(client):
    cid, _ = _scene(client)
    r = client.get(f"/api/campaigns/{cid}/scenes/nope/rolling-summary")
    # The detail, not just the status: an unrouted path is also a 404, so this
    # would pass against a route that does not exist at all.
    assert r.status_code == 404 and r.json()["detail"] == "scene not found"


# ---- POST: the gate ----
def test_a_turn_short_of_the_threshold_spends_nothing(client):
    """The client fires this after every turn, so the ordinary case is a no-op.
    It must not reach the model — that is what makes firing per turn free."""
    _key(client)
    llm = _use(client, FakeLLM())
    cid, sid = _scene(client, posts=9)
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()
    assert body["refreshed"] is False and body["summary"] == ""
    assert llm.calls == 0


def test_crossing_the_threshold_folds_and_stores(client):
    _key(client)
    llm = _use(client, FakeLLM("Mara reaches the salt gate; the ledger is still missing."))
    cid, sid = _scene(client, posts=10)
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()
    assert llm.calls == 1
    assert body["refreshed"] is True
    assert body["summary"] == "Mara reaches the salt gate; the ledger is still missing."
    assert body["at"] == 10 and body["total"] == 10 and body["stale"] is False
    # durable, and readable without a second call
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()["summary"] \
        == "Mara reaches the salt gate; the ledger is still missing."


def test_the_first_fold_sees_the_whole_scene_and_no_prior(client):
    _key(client)
    llm = _use(client, FakeLLM("A summary."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")
    user = llm.prompts[0][1]["content"]
    assert "Post 0." in user and "Post 9." in user
    assert "Summary so far" not in user


def test_the_second_fold_carries_the_prior_and_only_the_new_posts(client):
    """The fold is incremental: a scene that runs long must not re-send its
    whole transcript on every refresh."""
    _key(client)
    llm = _use(client, FakeLLM("First summary.", "Second summary."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")
    for n in range(10, 20):
        store.scenes.append_message(cid, sid, "user", f"Post {n}.")
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")

    user = llm.prompts[1][1]["content"]
    assert "First summary." in user
    assert "Post 15." in user
    assert "Post 0." not in user


def test_force_refreshes_a_scene_that_is_not_due(client):
    _key(client)
    llm = _use(client, FakeLLM("Forced."))
    cid, sid = _scene(client, posts=2)
    body = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true").json()
    assert llm.calls == 1 and body["refreshed"] is True and body["summary"] == "Forced."


def test_force_still_spends_nothing_when_there_is_nothing_new(client):
    """Refresh-now on an already-current summary would otherwise ask the model
    to fold an empty list of posts onto a summary, and pay for the privilege."""
    _key(client)
    llm = _use(client, FakeLLM("Once."))
    cid, sid = _scene(client, posts=4)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true")
    body = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true").json()
    assert llm.calls == 1 and body["refreshed"] is False


def test_force_spends_nothing_on_an_empty_scene(client):
    _key(client)
    llm = _use(client, FakeLLM())
    cid, sid = _scene(client, posts=0)
    body = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true").json()
    assert llm.calls == 0 and body["refreshed"] is False


def test_zero_turns_the_automatic_refresh_off(client):
    _key(client)
    llm = _use(client, FakeLLM("Never."))
    store.write_config(rolling_summary_every="0")
    cid, sid = _scene(client, posts=40)
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()
    assert llm.calls == 0 and body["refreshed"] is False and body["every"] == 0
    # ...but the panel's own button still works
    assert client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true").json()["refreshed"] is True


# ---- POST: staleness ----
def test_an_edited_post_inside_the_covered_prefix_forces_a_full_refold(client):
    """The case a message COUNT cannot see. Editing in place leaves the
    transcript exactly as long as it was, so folding forward would carry prose
    about text that is no longer there for the rest of the scene."""
    _key(client)
    llm = _use(client, FakeLLM("First summary.", "Refolded."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")

    store.scenes.edit_message(cid, sid, 0, "Post 0, rewritten entirely.")
    assert client.get(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()["stale"] is True

    body = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true").json()
    user = llm.prompts[1][1]["content"]
    assert "Summary so far" not in user           # the prior was discarded
    assert "Post 0, rewritten entirely." in user  # and the whole scene re-read
    assert body["stale"] is False and body["at"] == 10


def test_a_shortened_transcript_is_stale_too(client):
    _key(client)
    _use(client, FakeLLM("First summary."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")
    store.scenes.trim_continuation(cid, sid, 4)
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()
    assert body["stale"] is True and body["total"] == 4


def test_appending_posts_does_not_make_a_summary_stale(client):
    """The negative of the two above: ordinary play must not throw the fold
    away, or the feature costs a whole-transcript call every time."""
    _key(client)
    _use(client, FakeLLM("First summary."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")
    store.scenes.append_message(cid, sid, "user", "And another thing.")
    assert client.get(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()["stale"] is False


def test_a_stale_summary_is_still_returned_not_blanked(client):
    """Review caught this: reporting a stale fold as no summary at all left the
    panel saying "no summary yet" about a scene that has one, and made the
    staleness warning -- which renders beside the prose -- unreachable. Stale
    means "this describes a transcript that moved on", not "this is gone"."""
    _key(client)
    _use(client, FakeLLM("Mara reaches the salt gate."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")
    store.scenes.edit_message(cid, sid, 0, "Post 0, rewritten entirely.")

    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()
    assert body["stale"] is True
    assert body["summary"] == "Mara reaches the salt gate."


def test_a_forced_refresh_reports_the_automatic_schedule_not_its_own(client):
    """`due` answers "would a plain per-turn POST spend a call". A forced call
    that found nothing new must not report `due` about ITSELF -- the panel reads
    this to say when the next refresh is coming."""
    _key(client)
    _use(client, FakeLLM("Once."))
    cid, sid = _scene(client, posts=4)
    body = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true").json()
    assert body["refreshed"] is True and body["due"] is False


def test_a_post_edited_during_the_call_does_not_get_the_summary_that_raced_it(client):
    """The prefix a fold was computed FROM must still be the prefix on disk when
    it lands. Otherwise the route pays for a summary of a transcript that no
    longer exists and then stores it, and — because the panel's Refresh button
    trusts this response directly — presents it as current until some later GET
    happens to notice."""
    _key(client)
    _use(client, FakeLLM("First summary."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")

    class EditsMidCall(FakeLLM):
        async def complete(self, messages, cfg):
            store.scenes.edit_message(cid, sid, 0, "Post 0, rewritten mid-call.")
            return "A summary of the transcript as it was before that edit."

    for n in range(10, 25):
        store.scenes.append_message(cid, sid, "user", f"Post {n}.")
    _use(client, EditsMidCall())
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()

    assert body["refreshed"] is False
    # ...and the response says what is actually true, rather than `stale: false`
    assert body["stale"] is True and body["summary"] == "First summary."
    assert store.scenes.get_rolling_summary(cid, sid)["summary"] == "First summary."


def test_a_recycled_scene_id_does_not_inherit_the_old_scene_s_summary(client):
    """`delete_scene` frees the id and the numbering reuses it, so remaking a
    scene under the same title can hand it the very id a refresh is in flight
    for. Writing there attaches one scene's prose to another — and on an empty
    replacement even Refresh cannot clear it, because there is nothing pending
    for a forced refresh to fold."""
    _key(client)
    cid, sid = _scene(client, posts=10)
    recycled: list[str] = []

    class DeletesMidCall(FakeLLM):
        async def complete(self, messages, cfg):
            store.scenes.delete_scene(cid, sid)
            recycled.append(store.scenes.create_scene(cid, "Saltmarch"))
            return "A summary of a scene that no longer exists."

    _use(client, DeletesMidCall())
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()

    assert recycled == [sid]        # the id really was handed to the new scene
    assert body["refreshed"] is False
    assert store.scenes.get_rolling_summary(cid, sid)["summary"] == ""


def test_posts_landing_during_the_call_do_not_block_the_write(client):
    """The negative of the two above, and the common case: appending leaves the
    covered prefix untouched, so an ordinary turn arriving mid-call must not
    cost the summary that was already paid for."""
    _key(client)
    cid, sid = _scene(client, posts=10)

    class AppendsMidCall(FakeLLM):
        async def complete(self, messages, cfg):
            store.scenes.append_message(cid, sid, "user", "A turn that landed mid-call.")
            return "Mara reaches the salt gate."

    _use(client, AppendsMidCall())
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()

    assert body["refreshed"] is True and body["stale"] is False
    assert body["summary"] == "Mara reaches the salt gate."
    # covers the ten it read, and reports the eleven that now exist
    assert body["at"] == 10 and body["total"] == 11


def test_an_older_refresh_does_not_overwrite_a_newer_one(client):
    """Two overlapping refreshes, the newer finishing first. The older one's
    covered prefix is still intact — those messages never changed — so the
    prefix check alone waves it through, and coverage silently REGRESSES from
    twelve posts to ten. The panel then shows the less complete prose as
    current, and the next automatic refresh comes early."""
    _key(client)
    cid, sid = _scene(client, posts=10)

    class NewerLandsFirst(FakeLLM):
        async def complete(self, messages, cfg):
            for n in range(10, 12):
                store.scenes.append_message(cid, sid, "user", f"Post {n}.")
            msgs = store.scenes.read_scene(cid, sid)["messages"]
            store.scenes.set_rolling_summary(
                cid, sid, "Newer summary, covering twelve.", len(msgs),
                store.rolling_summary.covered_digest(msgs))
            return "Older summary, covering ten."

    _use(client, NewerLandsFirst())
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()

    assert body["refreshed"] is False
    stored = store.scenes.get_rolling_summary(cid, sid)
    assert stored["summary"] == "Newer summary, covering twelve." and stored["at"] == 12
    assert body["summary"] == "Newer summary, covering twelve."


def test_an_empty_completion_still_reports_the_staleness_it_can_see(client):
    """The empty-reply branch returns before the write, so it never used to
    reconcile — and a forced refresh whose covered prefix changed during the
    call answered `stale: false` about a summary that had just gone stale. The
    panel renders this answer directly."""
    _key(client)
    _use(client, FakeLLM("Good summary."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")

    class EditsThenSaysNothing(FakeLLM):
        async def complete(self, messages, cfg):
            store.scenes.edit_message(cid, sid, 0, "Post 0, rewritten mid-call.")
            return "   \n  "

    # Posts have to be pending, or the route declines before ever calling the
    # model and this exercises the not-due branch instead of the empty-reply one.
    for n in range(10, 15):
        store.scenes.append_message(cid, sid, "user", f"Post {n}.")
    _use(client, EditsThenSaysNothing())
    body = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true").json()

    assert body["refreshed"] is False
    assert body["stale"] is True and body["summary"] == "Good summary."


# ---- POST: failure never reaches the turn loop ----
def test_no_connection_is_a_409_not_a_500(client):
    llm = _use(client, FakeLLM())
    cid, sid = _scene(client, posts=10)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")
    assert r.status_code == 409
    # `main.create_app` flattens a dict detail, so `kind` sits at the top level —
    # the same shape `runStream`'s error branch already reads.
    assert r.json()["kind"] == "missing_key"
    assert llm.calls == 0


def test_an_upstream_failure_is_a_502_and_leaves_the_stored_summary_alone(client):
    _key(client)
    _use(client, FakeLLM("Good summary."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")

    class Boom(FakeLLM):
        async def complete(self, messages, cfg):
            raise LLMError("upstream", "the model exploded")

    _use(client, Boom())
    for n in range(10, 25):
        store.scenes.append_message(cid, sid, "user", f"Post {n}.")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")
    assert r.status_code == 502
    assert client.get(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()["summary"] == "Good summary."


def test_an_empty_reply_is_not_stored_over_a_good_summary(client):
    """A provider can return an empty completion. Storing it would blank a
    summary the player could still read, and record it as covering the scene."""
    _key(client)
    _use(client, FakeLLM("Good summary."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")

    _use(client, FakeLLM("   \n  "))
    for n in range(10, 25):
        store.scenes.append_message(cid, sid, "user", f"Post {n}.")
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()
    assert body["refreshed"] is False
    assert client.get(
        f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()["summary"] == "Good summary."


def test_a_scene_renamed_mid_refresh_is_not_a_500(client):
    """Reachable from the UI: `runStream` fires this refresh AFTER releasing the
    scene lock that keeps rename off during a turn, so a player who renames the
    moment a turn ends races the write. A rename mints a new id and moves the
    file, and `SceneNotFound` has no handler above this route."""
    _key(client)
    renamed: list[str] = []

    class RenamesMidCall(FakeLLM):
        async def complete(self, messages, cfg):
            renamed.append(store.scenes.rename_scene(cid, sid, "Somewhere else"))
            return "A summary of a scene that has since moved."

    cid, sid = _scene(client, posts=10)
    _use(client, RenamesMidCall())
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()
    assert renamed and renamed[0] != sid          # the file really did move
    assert body["refreshed"] is False


def test_post_on_an_unknown_scene_is_404(client):
    _key(client)
    cid, _ = _scene(client)
    r = client.post(f"/api/campaigns/{cid}/scenes/nope/rolling-summary")
    assert r.status_code == 404 and r.json()["detail"] == "scene not found"


def test_a_multiline_reply_does_not_corrupt_the_scene_file(client):
    """End to end for the frontmatter trap: `store/frontmatter.py` is one line
    per key, so a model answering in paragraphs would otherwise write a scene
    file whose frontmatter block ends early and whose transcript is unreadable."""
    _key(client)
    _use(client, FakeLLM("Line one.\n---\ntitle: hijacked\n\nLine two."))
    cid, sid = _scene(client, posts=10)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary")

    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()
    assert scene["meta"]["title"] == "Saltmarch"
    assert len(scene["messages"]) == 10
    assert scene["meta"]["rolling_summary"] == "Line one. --- title: hijacked Line two."


# ---- config surface ----
def test_the_knob_is_readable_and_writable_over_the_config_route(client):
    assert client.get("/api/config").json()["rolling_summary_every"] == "10"
    assert client.put("/api/config", json={"rolling_summary_every": "25"}).json()[
        "rolling_summary_every"] == "25"
    assert client.get("/api/config").json()["rolling_summary_every"] == "25"
