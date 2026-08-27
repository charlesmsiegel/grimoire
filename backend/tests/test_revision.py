"""The campaign write token, and the two endpoints that spend one (#409).

`store/revision.py` exists so a caller can ask "is this campaign still in the
state I priced against?" — a question nothing in the store could answer, which
is why `POST /advance` promised nothing about the moment it started from and
`POST /fork` could not tell a lost response from a failed write.

Three things are checked here, because a gap in any of them makes the other two
say something they cannot back up:

- what the token IS: opaque, unique per write, and degrading to `INITIAL`
  (which nothing can mint) rather than to something a stale reader matches;
- what MOVES it — every campaign-scoped mutating request, a detached turn's
  posts — and what deliberately does not, a fork of the campaign being the
  case with a reason (`store/fork.py`: "The source is never written to");
- what the two endpoints do with it.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.routes import campaigns as campaign_routes
from grimoire.routes import scenes as scene_routes
from grimoire.store import atomic, campaigns, clock, fork, revision, scenes, worlds
from tests.llm_fakes import FakeOpenRouter

# --- the token itself ------------------------------------------------------


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def test_a_campaign_nothing_has_stamped_reads_as_initial(cid):
    assert revision.current(cid) == revision.INITIAL


def test_a_bump_changes_the_token_and_never_repeats_one(cid):
    seen = {revision.current(cid)}
    for _ in range(5):
        token = revision.bump(cid)
        assert token not in seen
        assert revision.current(cid) == token
        seen.add(token)


def test_no_token_can_ever_equal_the_initial_reading(cid):
    # The property that makes `INITIAL` safe as the value a damaged or absent
    # file degrades to: a client holding it is refused the moment anything at
    # all has been recorded, rather than matching a campaign that has moved.
    assert all(revision.bump(cid) != revision.INITIAL for _ in range(20))


def test_a_damaged_file_reads_as_initial_rather_than_as_itself(cid):
    stamped = revision.bump(cid)
    path = store.campaigns.campaign_root(cid) / "revision.txt"
    path.write_bytes(b"\xff\xfe not text")
    assert revision.current(cid) == revision.INITIAL
    # ...and the point of that: the token somebody is still holding no longer
    # passes, so a stale operation is refused rather than waved through.
    with pytest.raises(revision.RevisionMismatchError):
        revision.require(cid, stamped)


def test_a_token_longer_than_this_module_writes_is_not_believed(cid):
    (store.campaigns.campaign_root(cid) / "revision.txt").write_text("x" * 500)
    assert revision.current(cid) == revision.INITIAL


def test_an_empty_expectation_is_no_expectation(cid):
    revision.bump(cid)
    revision.require(cid, "")           # a client that predates the token


def test_a_matching_expectation_passes_and_a_stale_one_names_both_values(cid):
    first = revision.bump(cid)
    revision.require(cid, first)
    second = revision.bump(cid)
    with pytest.raises(revision.RevisionMismatchError) as exc:
        revision.require(cid, first)
    assert exc.value.expected == first
    assert exc.value.current == second


def test_a_bump_that_cannot_be_written_does_not_raise(cid, monkeypatch):
    # It runs after the mutation it records has committed, so raising here would
    # turn work the user already has into a reported failure.
    monkeypatch.setattr(atomic, "write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("full disk")))
    assert revision.bump(cid) == revision.INITIAL


# --- what moves it ---------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    with TestClient(app) as c:
        yield c


def _campaign(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]


def _token(client, cid):
    return client.get(f"/api/campaigns/{cid}/clock").json()["revision"]


def test_a_campaign_scoped_write_moves_the_token(client):
    cid = _campaign(client)
    before = _token(client, cid)
    client.put(f"/api/campaigns/{cid}", json={"name": "Saltmarch Revisited"})
    assert _token(client, cid) != before


def test_a_write_in_another_campaign_does_not(client):
    cid, other = _campaign(client), _campaign(client)
    before = _token(client, cid)
    client.put(f"/api/campaigns/{other}", json={"name": "Elsewhere"})
    assert _token(client, cid) == before


def test_merely_reading_a_campaign_does_not_move_it(client):
    # The preview belongs in this list rather than the one above it: it is a
    # POST that persists nothing, and it is where a caller PICKS UP the token it
    # confirms with — one that stamped the campaign would hand back a value its
    # own response had already invalidated.
    cid = _clocked(client)
    before = _token(client, cid)
    client.get(f"/api/campaigns/{cid}")
    client.get(f"/api/campaigns/{cid}/scenes")
    client.post(f"/api/campaigns/{cid}/advance/preview", json={"days": 3})
    assert _token(client, cid) == before


def test_a_refused_write_moves_nothing(client):
    cid = _campaign(client)
    before = _token(client, cid)
    assert client.put(f"/api/campaigns/{cid}", json={"name": "  "}).status_code == 400
    assert _token(client, cid) == before


def test_forking_leaves_the_source_token_exactly_where_it_was(client):
    # The `@leaves_campaign_unchanged` case, and the composition it protects:
    # the checkpoint prompt forks the campaign and THEN advances it against the
    # token it priced with. A fork that moved the source's token would refuse
    # every skip it was taken for.
    cid = _campaign(client)
    before = _token(client, cid)
    client.post(f"/api/campaigns/{cid}/fork", json={"name": "Before the skip"})
    assert _token(client, cid) == before


def test_a_fork_does_not_inherit_its_parents_token(client):
    cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}", json={"name": "Saltmarch Again"})
    parent = _token(client, cid)
    forked = client.post(f"/api/campaigns/{cid}/fork", json={"name": "Branch"}).json()["id"]
    assert _token(client, forked) not in (parent, revision.INITIAL)


def test_a_detached_turns_posts_move_the_token(client):
    # The one mutation the activity middleware deliberately skips: a stream's
    # status line is sent before its outcome is known, so `_persist_reply` bumps
    # for itself. Called directly, which is what every detached turn strategy
    # reaches — the alternative is asserting this through a whole SSE turn.
    cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    before = _token(client, cid)
    assert routes.streaming._persist_reply(cid, sid, "The gate stands open.")
    assert _token(client, cid) != before


def test_a_send_moves_the_token_before_the_reply_lands(client, monkeypatch):
    """The player's post is a campaign mutation of its own.

    `_chat_run` appends it under the campaign lock and *then* returns a
    streaming response, so the reply that bumps the token is minutes away. The
    middleware therefore stamps the revision for a stream too (it stamps
    activity for none), or an advance priced before the send would confirm
    against a transcript that has already grown — and a large one would
    checkpoint the campaign with an unanswered post in it.

    `_persist_reply` is stubbed out precisely so it cannot be what moved the
    token: with the terminal write gone, the middleware's stamp at the response
    line is the only bump left, which is the one under test.
    """
    monkeypatch.setattr(routes.streaming, "_persist_reply", lambda *a, **k: 0)
    cid = _campaign(client)
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-secret"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    before = _token(client, cid)
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "I knock."}) as r:
        assert r.status_code == 200
        for _ in r.iter_lines():
            pass
    assert _token(client, cid) != before


def test_a_stream_does_not_stamp_activity(client):
    # The half of the rule that did NOT change: a stream's status is sent before
    # its outcome is known, so a turn that fails and rolls back must not have
    # ranked the campaign. The revision is the opposite trade -- see
    # `main._record_campaign_write`.
    cid = _campaign(client)
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-secret"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    root = store.campaigns.campaign_root(cid)
    (root / "activity.txt").unlink(missing_ok=True)
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "I knock."}) as r:
        for _ in r.iter_lines():
            pass
    assert not (root / "activity.txt").exists()


def test_a_preview_only_post_moves_nothing(client):
    # Every campaign-scoped POST that persists nothing has to say so, or it
    # refuses a clock confirmation over a campaign nothing wrote. Parsing an
    # import for review is one: it answers with a draft and writes no file.
    cid = _campaign(client)
    before = _token(client, cid)
    r = client.post(f"/api/campaigns/{cid}/scenes/import/parse",
                    files={"file": ("log.md", b"## Scene\n\n**Mara:** Hello.\n", "text/markdown")})
    assert r.status_code in (200, 400)     # the parse's own verdict is not the point
    assert _token(client, cid) == before


def test_every_preview_only_campaign_route_is_marked_as_one():
    """The marker is what the middleware reads, so an unmarked preview route is
    a false `campaign_moved` waiting to happen.

    Asserted on the endpoint rather than through a request for the one route
    that cannot be driven cheaply: drafting a description needs an image in the
    library and a provider call, and what is actually in question is one
    attribute. `_draft_description` documents itself as preview-only — the
    caller persists through the PUT on Save — and this is its only
    campaign-scoped surface; the other three describe world records.
    """
    # The endpoint function itself, which is exactly what the middleware reads
    # off `scope["route"]` -- the router is wrapped, so walking `app.routes` for
    # it would be testing the wrapper.
    assert campaign_routes.post_campaign_library_description_draft.grimoire_computes_only
    assert scene_routes.post_scene_import_parse.grimoire_computes_only


def test_a_reply_that_lands_nothing_moves_nothing(client):
    cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    before = _token(client, cid)
    assert not routes.streaming._persist_reply(cid, sid, "   ")
    assert _token(client, cid) == before


# --- POST /advance spends one ---------------------------------------------


def _clocked(client):
    """A campaign whose clock has a moment to measure a span FROM.

    `days` needs an anchor, and a campaign that has never played or advanced has
    none — so every advance below is the second one, which is also the shape the
    checkpoint prompt meets in practice.
    """
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance",
                json={"to": "2026-05-01", "reason": "the caravan sets out"})
    return cid


def _priced(client, cid, body):
    """A preview, and the token it was priced against."""
    r = client.post(f"/api/campaigns/{cid}/advance/preview", json=body).json()
    return r["digest"], r["revision"]


def test_an_advance_priced_against_the_current_state_lands(client):
    cid = _clocked(client)
    _digest, token = _priced(client, cid, {"days": 3})
    r = client.post(f"/api/campaigns/{cid}/advance",
                    json={"days": 3, "reason": "travel", "expect_revision": token})
    assert r.status_code == 200
    assert r.json()["moved"] is True


def test_an_advance_priced_against_a_state_the_campaign_has_left_is_refused(client):
    cid = _clocked(client)
    _digest, token = _priced(client, cid, {"days": 3})
    # Anything at all, in another tab: a scene edit, an absorb, a lore write.
    # The clock has not moved, which is exactly why the clock cannot be the
    # thing compared.
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Meanwhile"})
    r = client.post(f"/api/campaigns/{cid}/advance",
                    json={"days": 3, "reason": "travel", "expect_revision": token})
    assert r.status_code == 409
    # The dict is spread at the top level by `main`'s HTTPException handler,
    # the same shape `scene_busy` and `runs_in_flight` arrive in.
    body = r.json()
    assert body["kind"] == "campaign_moved"
    assert body["revision"] == _token(client, cid) != token
    assert "preview it again" in body["detail"]
    # ...and the clock stayed where it was, which is the whole point.
    assert client.get(f"/api/campaigns/{cid}/clock").json()["now"] == "2026-05-01"


def test_the_refusal_hands_back_what_to_price_against_next(client):
    cid = _clocked(client)
    _digest, stale = _priced(client, cid, {"days": 3})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Meanwhile"})
    fresh = client.post(f"/api/campaigns/{cid}/advance",
                        json={"days": 3, "reason": "travel", "expect_revision": stale},
                        ).json()["revision"]
    r = client.post(f"/api/campaigns/{cid}/advance",
                    json={"days": 3, "reason": "travel", "expect_revision": fresh})
    assert r.status_code == 200


def test_an_advance_with_no_expectation_still_moves_the_clock(client):
    cid = _clocked(client)
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Meanwhile"})
    r = client.post(f"/api/campaigns/{cid}/advance", json={"days": 3, "reason": "travel"})
    assert r.status_code == 200 and r.json()["moved"] is True


def test_a_no_op_advance_is_refused_on_a_stale_expectation_too(client):
    # It writes nothing, so nothing is at risk — but the digest it answers with
    # is measured from a moment the caller never asked about, and a caller that
    # asked to be told is told.
    cid = _clocked(client)
    _digest, token = _priced(client, cid, {"days": 0})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Meanwhile"})
    r = client.post(f"/api/campaigns/{cid}/advance",
                    json={"days": 0, "reason": "no move", "expect_revision": token})
    assert r.status_code == 409


def test_the_stale_check_runs_before_the_calendar_does(client, monkeypatch):
    # A doomed request must not import and run the campaign's calendar plugin,
    # nor walk a digest across it. The refusal is cheap on purpose.
    cid = _clocked(client)
    _digest, token = _priced(client, cid, {"days": 3})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Meanwhile"})
    monkeypatch.setattr(clock, "_provider",
                        lambda *a, **k: pytest.fail("the provider was resolved anyway"))
    assert client.post(f"/api/campaigns/{cid}/advance",
                       json={"days": 3, "reason": "travel",
                             "expect_revision": token}).status_code == 409


def test_the_check_is_taken_again_under_the_lock_that_covers_the_write(cid, monkeypatch):
    """Checking first and locking after leaves room for exactly the mutation
    being guarded against to land in between.

    Driven at the store, because that window only exists between the two steps:
    the fast check has already passed, the calendar work is running, and the
    lock has not been taken yet. `digest` stands in for another writer getting
    there during it.
    """
    token = revision.bump(cid)

    def _racing_digest(*a, **k):
        revision.bump(cid)
        return {"events": [], "elapsed_days": 3, "to_friendly": "", "truncated": False}

    monkeypatch.setattr(clock, "_provider", lambda *a, **k: object())
    monkeypatch.setattr(clock, "_resolve", lambda *a, **k: ("2026-05-01", "2026-05-04"))
    monkeypatch.setattr(clock, "digest", _racing_digest)
    monkeypatch.setattr(clock, "_commit",
                        lambda *a, **k: pytest.fail("the clock was written anyway"))
    with pytest.raises(revision.RevisionMismatchError):
        clock.advance(cid, days=3, reason="travel", expect_revision=token)


def test_a_key_that_cannot_be_recorded_does_not_undo_the_fork(cid, monkeypatch):
    # The record lands after the copy, so it must never be what loses one. A
    # lost record costs a duplicate on a retry -- what every caller had before
    # the key existed -- and the fork itself is already on disk.
    monkeypatch.setattr(atomic, "write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("full disk")))
    fork._record(cid, cid, "k-1", {"id": cid})


def test_two_advances_holding_one_token_cannot_both_commit(cid, monkeypatch):
    """The check under the lock is only binding if the token moves under it too.

    The activity middleware stamps once a response is on its way out, which is
    far too late to serialize two advances: the first releases the lock, fires
    its events and returns, and any of that is long enough for a second request
    carrying the SAME expected token to take the lock, read the value the first
    was supposed to have replaced, and commit a move priced against a clock that
    has already moved. Two callers with one token is the case `expect_revision`
    exists for, so the store bumps inside the hold.

    Both calls go straight to the store, which is what leaves the middleware out
    of it — exactly the window between one request's commit and its response.
    """
    token = revision.bump(cid)
    monkeypatch.setattr(clock, "_provider", lambda *a, **k: object())
    monkeypatch.setattr(clock, "_resolve", lambda *a, **k: ("2026-05-01", "2026-05-04"))
    monkeypatch.setattr(clock, "digest", lambda *a, **k: {
        "events": [], "elapsed_days": 3, "to_friendly": "", "truncated": False})

    assert clock.advance(cid, days=3, reason="travel", expect_revision=token)["moved"]
    with pytest.raises(revision.RevisionMismatchError):
        clock.advance(cid, days=3, reason="the same skip again", expect_revision=token)
    assert len(clock.read(cid)["log"]) == 1, "the second advance committed anyway"


def test_a_review_that_lands_moves_the_token(client, monkeypatch):
    """A review is the one durable campaign write with no response line behind
    it: the route that starts it answers 202 and is `@computes_only` (correctly
    — nothing is written at 202), and the run publishes minutes later.

    Driven through `_under_review_lock` directly, which is the choke point every
    absorb, audit and dossier retry reaches its terminal write through — the
    alternative is a whole detached review run to assert one stamp.
    """
    cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    before = _token(client, cid)
    run = SimpleNamespace(review_cancelled=False, cancel_requested=False,
                          scene_identity=store.scenes.scene_identity_strict(cid, sid))
    scene_routes._under_review_lock(cid, sid, run, lambda: None)
    assert _token(client, cid) != before


def test_a_review_that_is_cancelled_moves_nothing(client):
    # The bump is inside the fence, not beside it: a run whose review the reader
    # dismissed writes nothing, so there is nothing to record.
    cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    before = _token(client, cid)
    run = SimpleNamespace(review_cancelled=True, cancel_requested=False,
                          scene_identity=store.scenes.scene_identity_strict(cid, sid))
    with pytest.raises(Exception):   # noqa: B017 -- the private _ReviewCancelledError
        scene_routes._under_review_lock(cid, sid, run, lambda: None)
    assert _token(client, cid) == before


# --- POST /fork spends one -------------------------------------------------


def _fork(client, cid, name, **body):
    return client.post(f"/api/campaigns/{cid}/fork", json={"name": name, **body}).json()


def test_a_repeat_with_the_same_key_makes_one_copy_and_replays_its_report(client):
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"})
    first = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    second = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    assert first["replayed"] is False and second["replayed"] is True
    assert second["id"] == first["id"]
    assert {k: v for k, v in second.items() if k != "replayed"} == \
           {k: v for k, v in first.items() if k != "replayed"}
    ids = {c["id"] for c in client.get("/api/campaigns").json()}
    assert ids == {cid, first["id"]}


def test_a_repeat_under_a_different_name_still_replays_the_first_fork(client):
    # A key names an OPERATION. The second call is the same operation asked
    # again, which is what a client that lost a response is doing.
    cid = _campaign(client)
    first = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    second = _fork(client, cid, "Something Else", idempotency_key="k-1")
    assert second["id"] == first["id"]
    assert store.campaigns.read_campaign(first["id"])["meta"]["name"] == "Checkpoint"


def test_a_different_key_takes_a_second_copy(client):
    cid = _campaign(client)
    first = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    second = _fork(client, cid, "Checkpoint", idempotency_key="k-2")
    assert second["id"] != first["id"] and second["replayed"] is False


def test_no_key_is_the_behaviour_every_caller_had(client):
    cid = _campaign(client)
    first = _fork(client, cid, "Checkpoint")
    second = _fork(client, cid, "Checkpoint")
    assert second["id"] != first["id"] and second["replayed"] is False


def test_a_key_is_scoped_to_the_campaign_it_forks(client):
    one, two = _campaign(client), _campaign(client)
    a = _fork(client, one, "Checkpoint", idempotency_key="k-1")
    b = _fork(client, two, "Checkpoint", idempotency_key="k-1")
    assert b["id"] != a["id"] and b["replayed"] is False


def test_a_retrospective_forks_report_is_replayed_whole(client):
    cid = _campaign(client)
    first_sid = client.post(f"/api/campaigns/{cid}/scenes",
                            json={"title": "Arrival"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Departure"})
    one = _fork(client, cid, "Branch", from_scene=first_sid, idempotency_key="k-1")
    two = _fork(client, cid, "Branch", from_scene=first_sid, idempotency_key="k-1")
    assert one["removed_scenes"] and two["removed_scenes"] == one["removed_scenes"]
    assert two["records"] == one["records"] and two["from_scene"] == one["from_scene"]


def test_a_key_past_the_cap_is_refused_rather_than_truncated(client):
    cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/fork",
                    json={"name": "Checkpoint", "idempotency_key": "k" * (fork.KEY_LIMIT + 1)})
    assert r.status_code == 400
    assert [c["id"] for c in client.get("/api/campaigns").json()] == [cid]


def test_a_key_at_the_cap_is_accepted(client):
    cid = _campaign(client)
    key = "k" * fork.KEY_LIMIT
    assert _fork(client, cid, "Checkpoint", idempotency_key=key)["replayed"] is False
    assert _fork(client, cid, "Checkpoint", idempotency_key=key)["replayed"] is True


def test_a_forks_own_key_record_does_not_travel_into_a_fork_of_it(client):
    # `_copy` duplicates the whole directory, marker included. Left there, a
    # copy of a copy would claim to have been made for a key that named an
    # earlier operation on another campaign.
    cid = _campaign(client)
    child = _fork(client, cid, "Branch", idempotency_key="k-1")["id"]
    assert (store.campaigns.campaign_root(child) / fork.MARKER).exists()
    grandchild = _fork(client, child, "Twig")
    assert grandchild["replayed"] is False
    assert not (store.campaigns.campaign_root(grandchild["id"]) / fork.MARKER).exists()


def test_a_marker_that_does_not_describe_the_campaign_it_is_in_is_not_believed(client):
    # What a hand-copied campaign directory looks like. Believing it would
    # replay a report naming somebody else's fork.
    cid = _campaign(client)
    first = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    planted = _fork(client, cid, "Elsewhere", idempotency_key="k-2")
    (store.campaigns.campaign_root(planted["id"]) / fork.MARKER).write_text(
        json.dumps({"key": "k-3", "parent": cid, "at": "", "report": first}),
        encoding="utf-8")
    again = _fork(client, cid, "Checkpoint", idempotency_key="k-3")
    assert again["replayed"] is False and again["id"] not in (first["id"], planted["id"])


def test_a_repeat_is_answered_after_the_scene_it_was_cut_at_is_deleted(client):
    # A key names an operation that already happened, so nothing about the
    # request repeating it needs to be true a second time. The scene belongs to
    # the SOURCE, which keeps playing: validating it before the replay would
    # answer a retry with a 404 while the fork it made is sitting on the shelf.
    cid = _campaign(client)
    first_sid = client.post(f"/api/campaigns/{cid}/scenes",
                            json={"title": "Arrival"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Departure"})
    made = _fork(client, cid, "Branch", from_scene=first_sid, idempotency_key="k-1")
    assert client.delete(f"/api/campaigns/{cid}/scenes/{first_sid}").status_code == 200
    again = client.post(f"/api/campaigns/{cid}/fork",
                        json={"name": "Branch", "from_scene": first_sid,
                              "idempotency_key": "k-1"})
    assert again.status_code == 200
    assert again.json()["id"] == made["id"] and again.json()["replayed"] is True


def test_an_unknown_scene_is_still_refused_without_a_key(client):
    # The other half: the reordering must not have made the check optional for a
    # request that is not a repeat of anything.
    cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/fork",
                    json={"name": "Branch", "from_scene": "404--nope"})
    assert r.status_code == 404
    assert [c["id"] for c in client.get("/api/campaigns").json()] == [cid]


def test_a_half_written_marker_is_not_replayed_as_a_report(client):
    # The marker's report is returned as the route's body and the client reads
    # every field of it -- `forkNotes` walks `refused` and `failed` as arrays.
    # A truncated one carrying only the right id would replay as a success and
    # then fail in the reader's browser, where the documented recoverable path
    # is a second copy on the shelf.
    cid = _campaign(client)
    first = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    (store.campaigns.campaign_root(first["id"]) / fork.MARKER).write_text(
        json.dumps({"key": "k-1", "parent": cid, "at": "", "report": {"id": first["id"]}}),
        encoding="utf-8")
    again = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    assert again["replayed"] is False and again["id"] != first["id"]


def test_a_replayed_report_carries_every_field_the_client_reads(client):
    cid = _campaign(client)
    first = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    again = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    assert again["replayed"] is True
    assert set(again) == set(first)
    assert isinstance(again["refused"], list) and isinstance(again["failed"], list)


def test_a_forks_token_is_not_minted_until_the_cut_has_finished(cid, monkeypatch):
    # `copytree` publishes `campaign.md` partway through, so from that line on
    # the fork is a campaign to every read route -- and none of them takes this
    # campaign's lock. A token minted before `_cut_after` would be handed to a
    # reader mid-cut and still be current once the cut had finished: a stale
    # reading certified as good.
    sid = scenes.create_scene(cid, "Arrival")
    scenes.append_message(cid, sid, "user", "I knock.")
    later = scenes.create_scene(cid, "Departure")
    scenes.append_message(cid, later, "user", "I leave.")
    seen = []
    real = fork._cut_after
    monkeypatch.setattr(fork, "_cut_after",
                        lambda c, s: (seen.append(revision.current(c)), real(c, s))[1])
    made = fork.fork_campaign(cid, "Branch", from_scene=sid)
    # Mid-cut it reads as INITIAL, which no expectation can match...
    assert seen == [revision.INITIAL]
    # ...and the finished fork carries a real one.
    assert revision.current(made["id"]) not in (revision.INITIAL, revision.current(cid))


def test_an_unreadable_marker_costs_a_second_copy_rather_than_a_wrong_answer(client):
    cid = _campaign(client)
    first = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    (store.campaigns.campaign_root(first["id"]) / fork.MARKER).write_text("{ not json",
                                                                         encoding="utf-8")
    again = _fork(client, cid, "Checkpoint", idempotency_key="k-1")
    assert again["id"] != first["id"] and again["replayed"] is False
