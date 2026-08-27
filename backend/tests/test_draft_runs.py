"""`draft` runs: the twelve computing routes, the opener, and their run routes.

Phase 3 of detaching runs (#398). What is being pinned here is one sentence
each way:

* a draft **survives the request that asked for it**, so a client that goes
  away finds its work and its result on the way back;
* a draft **never holds anything**, so a tagline being drafted can neither
  refuse a chat turn nor freeze a scene.

The second is the one worth a suite of its own. `draft` declares no exclusion
key by construction (`runs.exclusion_key`), so it is not obviously testable
from the outside -- and it is exactly the property that would be quietly lost
by someone reaching for `reserve_turn` because it was the reservation helper
they had already read.
"""

from __future__ import annotations

import time
from unittest import mock

import pytest

import grimoire.store as store
from grimoire import routes
from grimoire.llm_errors import LLMError
from grimoire.routes import runs as runs_mod
from tests.llm_fakes import (
    FailingOpenRouter,
    FakeCatalog,
    FakeOpenRouter,
    FakeOpenRouterComplete,
    StallingOpenRouter,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def world(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters",
                json={"name": "Mara", "version_name": "main"})
    return wid


@pytest.fixture
def campaign(client, world):
    cid = client.post("/api/campaigns",
                      json={"name": "Saltmarch", "world": world}).json()["id"]
    return world, cid


def _tagline(world):
    return f"/api/worlds/{world}/characters/mara/tagline/generate"


def _wait_state(client, base, run_id, state="landed", timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"{base}/{run_id}").json()["run"]
        if run["state"] == state:
            return run
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached {state}")


# ---- the 202 contract ------------------------------------------------------

def test_a_draft_answers_202_with_a_run_and_delivers_its_result(client, world):
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Keeps the tide-gate.")

    started = client.post(_tagline(world))

    assert started.status_code == 202
    run = started.json()["run"]
    assert (run["cls"], run["kind"]) == ("draft", "tagline")
    # NOT `state == "running"`. The 202 is built after the work is handed to
    # the runner, so a faked provider can finish before the response is shaped
    # -- rarely, and only when the machine is loaded enough to schedule the
    # detached task in that gap. Asserting it passed here for a week and then
    # failed once inside the full suite, which is the shape #351 documents.
    # What the client actually needs is that the body names a run it can poll.
    assert run["state"] in ("running", "landed")
    landed = _wait_state(client, f"/api/worlds/{world}/runs", run["id"])
    assert landed["result"] == {"tagline": "Keeps the tide-gate."}


def test_a_refusal_the_route_can_make_is_still_a_refusal(client, world):
    """Everything decidable while the request is there stays on the request. A
    404 for a character that does not exist must not arrive minutes later as a
    run state -- and a run must not be left reserved for it either."""
    r = client.post(f"/api/worlds/{world}/characters/nobody/tagline/generate")

    assert r.status_code == 404
    assert client.get(f"/api/worlds/{world}/runs").json()["runs"] == []


def test_a_provider_failure_lands_on_the_run_with_its_own_status(client, world):
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down", retry_after=12.4)

    started = client.post(_tagline(world))
    failed = _wait_state(client, f"/api/worlds/{world}/runs",
                         started.json()["run"]["id"], "failed")

    # The status the same failure carried when this route blocked, so a client's
    # existing handling of it needs no second shape -- and the window with it,
    # which no header can carry once the 202 has gone out.
    assert failed["error"]["status"] == 429
    assert failed["error"]["kind"] == "rate_limit"
    assert failed["error"]["retry_after"] == "13"


# ---- a draft holds nothing -------------------------------------------------

def test_a_draft_declares_no_exclusion_key(client):
    """The property every other test in this section is a consequence of."""
    assert runs_mod.exclusion_key(("world", "realm"), "draft") is None
    assert runs_mod.exclusion_key(("campaign", "c"), "draft") is None
    assert runs_mod.exclusion_key(("scene", "c", "abc"), "draft") is None


def test_two_drafts_on_one_subject_overlap_and_stay_discoverable(client, world):
    """A world is a coarse subject and two images are described at once
    routinely. Neither run may displace the other, and both have to remain
    addressable -- which is why discovery here is a list and not a pointer."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: StallingOpenRouter(["half "])

    first = client.post(_tagline(world), headers={"X-Grimoire-Attempt": "a1"})
    second = client.post(
        f"/api/worlds/{world}/characters/mara/voice-anchor/generate",
        headers={"X-Grimoire-Attempt": "a2"})
    assert (first.status_code, second.status_code) == (202, 202)

    listed = client.get(f"/api/worlds/{world}/runs").json()["runs"]
    assert {r["attempt_id"] for r in listed} == {"a1", "a2"}
    assert {r["kind"] for r in listed} == {"tagline", "voice-anchor"}
    assert {r["state"] for r in listed} == {"running"}


def test_a_live_opener_does_not_refuse_a_turn_on_its_scene(client, campaign):
    """The regression the issue names outright, asked where it can actually
    happen. Exclusion keys are built from the WHOLE subject, so a world- or
    campaign-scoped draft could never collide with a scene's turns whatever
    class it took; the opener is the one draft that shares a subject with them,
    and so the only one that would hold a scene if it asked for a key."""
    _world, cid = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    runs_mod.reserve_scene_draft(client.app, cid, sid, "opener", "a1")

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hi"])
    turn = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"text": "hello"})

    assert turn.status_code == 200


def test_a_live_draft_does_not_freeze_the_scene_it_runs_on(client, campaign):
    """The opener is the one draft with a scene subject, and it writes nothing
    -- `first-post` is what puts its text in the transcript. Holding the scene
    would refuse the very route the opener exists to feed.

    Reserved directly rather than driven through the route, because
    `TestClient` buffers a streaming response to completion: a test that held
    the provider open would deadlock on its own request rather than observe
    anything. What is being asked is a question about the RESERVATION -- does
    `reserve_scene_draft` take a key `require_scene_free` reads -- and this
    asks it exactly.
    """
    _world, cid = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    run, fresh = runs_mod.reserve_scene_draft(client.app, cid, sid, "opener", "a1")
    assert fresh and run.state == "running"

    # Renaming is the sharpest form of "the scene's shape changed": a `turn`
    # holding the scene is refused `scene_busy` for it.
    renamed = client.put(f"/api/campaigns/{cid}/scenes/{sid}",
                         json={"title": "Renamed"})

    assert renamed.status_code == 200


def test_a_live_turn_does_not_refuse_an_opener(client, campaign):
    """The other direction, and it is not symmetric by construction: a `turn`
    holds an exclusion key, so a draft that consulted one would be refused by
    it. It must not consult one.

    Re-generating an opener the player did not like is an ordinary thing to do
    twice, and an opener runs on a scene where no turn should be in flight
    anyway -- but "should" is not "cannot", and being refused would be the
    wrong answer even then.
    """
    _world, cid = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    identity = store.scenes.scene_identity(cid, sid)
    client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "held", identity,
        {"campaign": "Saltmarch", "scene": "S"})
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["The quay."])

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/opener", json={"prompt": "begin"})

    assert r.status_code == 200
    assert "The quay." in r.text


# ---- idempotency and recovery ---------------------------------------------

def test_a_duplicate_delivery_adopts_the_run_rather_than_generating_twice(client, world):
    """A draft is a whole provider call. A retried POST that started a second
    one would be paid for twice and the two answers would disagree."""
    fake = FakeOpenRouterComplete("Keeps the tide-gate.")
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    head = {"X-Grimoire-Attempt": "once"}

    first = client.post(_tagline(world), headers=head)
    _wait_state(client, f"/api/worlds/{world}/runs", first.json()["run"]["id"])
    second = client.post(_tagline(world), headers=head)

    assert second.json()["run"]["id"] == first.json()["run"]["id"]
    assert fake.calls == 1


def test_a_client_that_lost_its_202_finds_the_run_by_attempt(client, world):
    """The case the whole class is being migrated for: the server accepted the
    POST and began generating, and the answer was lost on the way back."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Keeps the tide-gate.")
    started = client.post(_tagline(world), headers={"X-Grimoire-Attempt": "lost"})

    found = client.get(f"/api/worlds/{world}/runs?attempt=lost").json()["runs"]

    assert [r["id"] for r in found] == [started.json()["run"]["id"]]
    # And an attempt nobody made answers with nothing rather than with somebody
    # else's run.
    assert client.get(f"/api/worlds/{world}/runs?attempt=other").json()["runs"] == []


# ---- the subject-scoped run routes ----------------------------------------

def test_a_run_is_invisible_from_another_subject(client, world):
    """The isolation every run route rests on: an id from one world answers
    'gone' elsewhere, never another world's state."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Keeps the tide-gate.")
    run_id = client.post(_tagline(world)).json()["run"]["id"]
    other = client.post("/api/worlds", json={"name": "Elsewhere"}).json()["id"]

    assert client.get(f"/api/worlds/{other}/runs/{run_id}").status_code == 404
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.get(f"/api/worlds/{world}/runs/{run_id}").status_code == 200


def test_the_model_refresh_is_a_global_run(client):
    """The one draft that belongs to the app rather than to a record: the
    catalog is stored beside the connection, which no world or campaign owns."""
    conn = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint",
        "base_url": "https://x", "api_key": "sk-x"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(models=[{"id": "m-1"}])

    started = client.post(f"/api/llm-connections/{conn}/models/refresh")

    assert started.status_code == 202
    landed = _wait_state(client, "/api/runs", started.json()["run"]["id"])
    assert [m["id"] for m in landed["result"]["models"]] == ["m-1"]
    assert [r["kind"] for r in client.get("/api/runs").json()["runs"]] == ["models-refresh"]


def test_the_refresh_caches_its_catalog_even_if_nobody_is_listening(client):
    """The point of the refresh is the sidecar it leaves behind, so the write
    happens in the RUN. A client that never comes back still gets it."""
    conn = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint",
        "base_url": "https://x", "api_key": "sk-x"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(models=[{"id": "m-1"}])

    started = client.post(f"/api/llm-connections/{conn}/models/refresh",
                          headers={"X-Grimoire-Attempt": "mine"})
    _wait_state(client, "/api/runs", started.json()["run"]["id"])

    stored = client.get(f"/api/llm-connections/{conn}").json()
    assert [m["id"] for m in stored["models"]] == ["m-1"]
    # WHICH refresh wrote it, which is the only durable trace a draft leaves.
    # A client whose run was reaped asks the store whether ITS attempt landed,
    # and a timestamp cannot answer that: a second tab refreshing the same
    # connection moves it too, and global drafts overlap by design.
    assert stored["fetched_by"] == "mine"


def test_a_refresh_that_failed_leaves_the_stamp_alone(client):
    """The other half: nothing is written, so nothing claims this attempt."""
    conn = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint",
        "base_url": "https://x", "api_key": "sk-x"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(error=LLMError("rate_limit", "slow down"))

    started = client.post(f"/api/llm-connections/{conn}/models/refresh",
                          headers={"X-Grimoire-Attempt": "mine"})
    _wait_state(client, "/api/runs", started.json()["run"]["id"], "failed")

    assert client.get(f"/api/llm-connections/{conn}").json()["fetched_by"] == ""


def test_a_negative_cursor_is_refused_before_the_run_is_looked_up(client, world):
    """A malformed cursor is the caller's mistake; answering it with a 404 about
    a run that exists would send them looking in the wrong place."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Keeps the tide-gate.")
    run_id = client.post(_tagline(world)).json()["run"]["id"]

    r = client.get(f"/api/worlds/{world}/runs/{run_id}/stream?from=-1")

    assert r.status_code == 400


def test_a_draft_can_be_cancelled_through_its_subject(client, world):
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: StallingOpenRouter(["half "])
    run_id = client.post(_tagline(world)).json()["run"]["id"]

    r = client.post(f"/api/worlds/{world}/runs/{run_id}/cancel")

    assert r.status_code == 200
    assert r.json()["run"]["state"] == "cancelled"


# ---- what a deleted record leaves behind, and what a broken one reports -----

def test_a_deleted_records_runs_stop_being_reachable(client, world):
    """Campaign and world ids are SLUGS, and a slug is reusable. Deleting
    "Realm" and creating another world of that name lands on the same id, so
    inside the retention window the replacement would otherwise be handed this
    one's runs -- and a retry with the same attempt id would adopt a proposal
    computed from a world that no longer exists."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Keeps the tide-gate.")
    started = client.post(_tagline(world), headers={"X-Grimoire-Attempt": "a1"})
    run_id = started.json()["run"]["id"]
    _wait_state(client, f"/api/worlds/{world}/runs", run_id)

    assert client.delete(f"/api/worlds/{world}").status_code == 200
    remade = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]

    assert remade == world, "the point of this test is a recycled id"
    assert client.get(f"/api/worlds/{world}/runs").json()["runs"] == []
    assert client.get(f"/api/worlds/{world}/runs?attempt=a1").json()["runs"] == []
    assert client.get(f"/api/worlds/{world}/runs/{run_id}").status_code == 404


def test_a_deleted_campaigns_runs_stop_being_reachable(client, campaign):
    _world, cid = campaign
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: StallingOpenRouter(["half "])
    run_id = client.post(f"/api/campaigns/{cid}/scene-suggestions",
                         headers={"X-Grimoire-Attempt": "a1"}).json()["run"]["id"]

    assert client.delete(f"/api/campaigns/{cid}").status_code == 200

    # The run is still generating -- forgetting it does not stop it, and does
    # not need to: a draft writes nowhere, so one nobody can find changes
    # nothing.
    assert client.get(f"/api/campaigns/{cid}/runs?attempt=a1").json()["runs"] == []
    assert client.get(f"/api/campaigns/{cid}/runs/{run_id}").status_code == 404


def test_an_unexpected_failure_reports_the_500_it_is(client, world):
    """A parser bug is a 500, and has to say so. The status is what a client
    builds its HTTP failure from, and an absent one falls back to 409 -- so an
    internal error reached the reader as a conflict they could do nothing
    about."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Keeps the tide-gate.")
    broken = lambda _text: (_ for _ in ()).throw(RuntimeError("the parser blew up"))
    with mock.patch.object(store.taglines, "parse_output", broken):
        started = client.post(_tagline(world))
        failed = _wait_state(client, f"/api/worlds/{world}/runs",
                             started.json()["run"]["id"], "failed")

    assert failed["error"]["kind"] == "run_failed"
    assert failed["error"]["status"] == 500


# ---- the opener ------------------------------------------------------------

def test_the_opener_buffers_its_frames_so_a_client_can_read_them_again(client, campaign):
    """The whole of what detaching the opener buys: backgrounding the app used
    to lose it outright, because the only copy of each frame was the one
    already on the wire."""
    _world, cid = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["The ", "quay."])

    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "begin"}).text
    run_id = _first_run_id(body)
    replayed = client.get(
        f"/api/campaigns/{cid}/scenes/{sid}/runs/{run_id}/stream?from=0").text

    assert "The " in replayed and "quay." in replayed
    # Frames are indexed, so a client that read through N resumes at N+1 rather
    # than re-rendering the whole reply.
    assert "id: 0" in replayed


def test_the_opener_writes_nothing_to_the_transcript(client, campaign):
    """Why it is a `draft` and not a `turn`. Classing it as a turn would claim
    its result is already in the transcript when nothing has been written."""
    _world, cid = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["The quay."])

    client.post(f"/api/campaigns/{cid}/scenes/{sid}/opener", json={"prompt": "begin"})

    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()
    assert scene["messages"] == []


def test_a_duplicate_opener_replays_rather_than_generating_again(client, campaign):
    _world, cid = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    fake = FakeOpenRouter(["The quay."])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    head = {"X-Grimoire-Attempt": "once"}
    url = f"/api/campaigns/{cid}/scenes/{sid}/opener"

    first = client.post(url, json={"prompt": "begin"}, headers=head).text
    again = client.post(url, json={"prompt": "begin"}, headers=head).text

    assert _first_run_id(first) == _first_run_id(again)
    assert fake.calls == 1


def test_an_opener_that_failed_is_recorded_failed_not_landed(client, campaign):
    """`ephemeral_frames` handles an upstream failure by emitting an error frame
    and finishing normally, so "did not raise" covers both a delivered opener
    and a failed one. Inferred, the run says `landed` with no error -- and a
    client that came back and polled instead of reading the frames would be
    told an opener arrived whose only terminal frame says it did not."""
    _world, cid = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down")

    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "begin"}).text
    run = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/"
                     f"{_first_run_id(body)}").json()["run"]

    assert run["state"] == "failed"
    assert run["error"]["kind"] == "rate_limit"


def test_a_preflight_refusal_keeps_its_status_on_the_run(client, world):
    """Both scenario parses reserve BEFORE their slow preflight, so a refusal
    can land after the run is discoverable -- and a client whose response was
    lost reads it off the record instead. Without the status the record says
    409 where the request said 400, and a retry adopts that wrong shape."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})

    refused = client.post(f"/api/worlds/{world}/scenario/parse-url",
                          json={"url": "not a url"},
                          headers={"X-Grimoire-Attempt": "a1"})

    assert refused.status_code == 400
    found = client.get(f"/api/worlds/{world}/runs?attempt=a1").json()["runs"]
    assert [r["state"] for r in found] == ["failed"]
    assert found[0]["error"]["status"] == 400


def _first_run_id(body: str) -> str:
    """The id from the leading `run` frame every producing route emits first."""
    import json
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            if "run" in payload:
                return payload["run"]["id"]
    raise AssertionError("no leading run frame in the response")
