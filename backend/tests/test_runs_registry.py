"""The run record and registry (detached runs, task 2).

Pure data. Scheduling is task 3, and nothing here imports it.
"""

from __future__ import annotations

import pytest

from grimoire.routes import runs

SCENE = ("scene", "saltmarch", "0001--mara")
OTHER = ("scene", "saltmarch", "0002--winifred")
TWIN = ("scene", "realm", "0001--mara")     # SAME sid, different campaign
WORLD = ("world", "realm")
LABELS = {"campaign": "Saltmarch", "scene": "Mara"}


def _sse(payload: str) -> str:
    """One SSE data frame, the way the producer emits it."""
    return f'data: {{"delta": "{payload}"}}\n\n'


def test_turn_and_review_share_one_exclusion_key_per_scene():
    assert runs.exclusion_key(SCENE, "turn") == runs.exclusion_key(SCENE, "review")
    assert runs.exclusion_key(SCENE, "turn") != runs.exclusion_key(OTHER, "turn")
    # Scene ids are CAMPAIGN-LOCAL: `_numbering` derives the next number from
    # the files in that campaign's own directory, so `0001--mara` exists in
    # every campaign that has a first scene. A key built from `sid` alone
    # passes every other test here and then either rejects a turn in campaign B
    # because campaign A has one live, or routes B's reply onto A's scene.
    assert runs.exclusion_key(SCENE, "turn") != runs.exclusion_key(TWIN, "turn")


def test_background_and_draft_declare_no_key():
    assert runs.exclusion_key(SCENE, "background") is None
    assert runs.exclusion_key(WORLD, "draft") is None


def test_second_turn_on_a_busy_scene_is_refused():
    r = runs.RunRegistry()
    first, started = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert started
    assert r.live_for_key(runs.exclusion_key(SCENE, "turn")) is first
    with pytest.raises(runs.RunInFlightError) as exc:
        r.start_or_existing(SCENE, "turn", "chat", "a2", "ident", LABELS)
    assert exc.value.run_id == first.id


def test_a_turn_in_another_campaign_is_not_refused():
    """The same `sid` in two campaigns must not collide -- a phone user with two
    campaigns open hits this on their first turn in each."""
    r = runs.RunRegistry()
    r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    twin, started = r.start_or_existing(TWIN, "turn", "chat", "a2", "ident", LABELS)
    assert started and twin is not None


def test_a_terminal_run_releases_the_exclusion_key():
    r = runs.RunRegistry()
    first, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    first.finish("landed")
    assert r.live_for_key(runs.exclusion_key(SCENE, "turn")) is None
    second, started = r.start_or_existing(SCENE, "turn", "chat", "a2", "ident", LABELS)
    assert started and second is not first


def test_drafts_overlap_on_one_subject_and_both_stay_discoverable():
    """The bug a most-recent pointer would ship: the second start hides the
    first, which then has no discovery path at all."""
    r = runs.RunRegistry()
    a, _ = r.start_or_existing(WORLD, "draft", "image-description", "a1", None, LABELS)
    b, _ = r.start_or_existing(WORLD, "draft", "image-description", "a2", None, LABELS)
    assert {run.id for run in r.for_subject(WORLD)} == {a.id, b.id}


def test_repeated_attempt_id_returns_the_existing_run_even_when_terminal():
    r = runs.RunRegistry()
    first, started = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert started
    first.finish("landed")
    again, started_again = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert again is first and not started_again


def test_an_attempt_id_is_scoped_to_its_subject():
    """Attempt ids come from clients; two scenes can pick the same one."""
    r = runs.RunRegistry()
    a, _ = r.start_or_existing(SCENE, "turn", "chat", "same", "ident", LABELS)
    b, started = r.start_or_existing(OTHER, "turn", "chat", "same", "ident", LABELS)
    assert started and b is not a


def test_get_refuses_a_run_id_from_another_subject():
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert r.get(run.id, SCENE) is run
    assert r.get(run.id, OTHER) is None


def test_reap_drops_terminal_runs_past_the_window_and_keeps_live_ones():
    r = runs.RunRegistry()
    done, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    done.finish("landed", at=1000.0)
    live, _ = r.start_or_existing(OTHER, "turn", "chat", "a2", "ident", LABELS)
    assert r.reap(now=1000.0 + runs.REAP_SECONDS + 1) == 1
    assert r.get(done.id, SCENE) is None
    assert r.get(live.id, OTHER) is live


def test_reaping_clears_every_index_not_just_the_run_table():
    """A reaped run left in `_by_attempt` would make a later send with the same
    attempt id adopt a corpse, and one left in `_by_key` would wedge the scene
    permanently -- neither is visible through `get`."""
    r = runs.RunRegistry()
    done, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    done.finish("landed", at=1000.0)
    assert r.reap(now=1000.0 + runs.REAP_SECONDS + 1) == 1

    assert r.for_subject(SCENE) == []
    assert r.live_for_key(runs.exclusion_key(SCENE, "turn")) is None
    fresh, started = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert started and fresh is not done


def test_a_live_run_is_never_reaped_however_old():
    r = runs.RunRegistry()
    live, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert r.reap(now=1e12) == 0
    assert r.get(live.id, SCENE) is live


# --- the frame buffer -------------------------------------------------------

def test_append_frame_returns_absolute_indexes_from_zero():
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert run.append_frame(_sse("Wind off the ")) == 0
    assert run.append_frame(_sse("water.")) == 1


def test_the_buffer_holds_heartbeats_verbatim_and_they_occupy_an_index():
    """The producer yields raw SSE strings, one of which is the comment frame
    `": heartbeat\\n\\n"` with no JSON payload at all. A buffer of decoded dicts
    could only take it by dropping it -- and dropping it silently shifts every
    later index, so a client resuming at `consumed + 1` replays text it already
    rendered."""
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    run.append_frame(_sse("Wind off the "))
    beat = run.append_frame(": heartbeat\n\n")
    run.append_frame(_sse("water."))

    assert beat == 1
    assert run.frames[1]["raw"] == ": heartbeat\n\n"
    assert [f["index"] for f in run.frames] == [0, 1, 2]


def test_frames_since_is_inclusive_so_a_resume_reproduces_the_text_once():
    """`since=N` yields frame N itself, so a client that consumed through N asks
    for N+1. Getting it backwards duplicates a delta mid-reply."""
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    run.append_frame(_sse("Wind off the "))
    run.append_frame(": heartbeat\n\n")
    run.append_frame(_sse("water."))

    whole = "".join(f["raw"] for f in run.frames_since(0))
    resumed = "".join(f["raw"] for f in run.frames_since(2))
    assert "Wind off the " in whole and "water." in whole
    assert resumed == _sse("water.")
    # Resuming across the heartbeat drops neither text nor an index.
    assert [f["index"] for f in run.frames_since(1)] == [1, 2]


def test_frames_since_past_the_end_is_empty_rather_than_an_error():
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    run.append_frame(_sse("only"))
    assert run.frames_since(99) == []


# --- the record itself ------------------------------------------------------

def test_a_new_run_carries_its_labels_and_identity():
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident-x", LABELS)
    assert run.labels == LABELS
    assert run.scene_identity == "ident-x"
    assert run.subject == SCENE and run.cls == "turn" and run.kind == "chat"
    assert run.state == "running" and run.ended_at is None


def test_finish_records_the_state_and_the_clock():
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    run.finish("failed", at=1234.0)
    assert run.state == "failed" and run.ended_at == 1234.0


def test_a_published_run_always_has_both_handshake_events():
    """The pre-start window is real: a cancel or a discovery can arrive while
    the route is still doing synchronous setup, before any runner exists. A run
    that is observable without its events leaves that caller waiting on
    something nothing will ever make."""
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert run.ready is not None and run.terminal is not None
    assert not run.ready.is_set() and not run.terminal.is_set()


def test_the_event_factory_is_injectable_for_the_portal():
    """Task 3 replaces the default with one that builds events on the lifespan
    loop. The registry itself must not know about portals, so it takes a
    factory -- and it has to be used for every run it publishes."""
    made = []

    def factory():
        made.append(1)
        return runs._PlainEvent()

    r = runs.RunRegistry(event_factory=factory)
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert len(made) == 2                       # ready and terminal
    assert run.ready is not None and run.terminal is not None
