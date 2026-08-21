"""The run record and registry (detached runs, task 2).

Pure data. Scheduling is task 3, and nothing here imports it.
"""

from __future__ import annotations

import threading
import time

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
    done.finish("landed", at=1000.0, monotonic_at=1000.0)
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
    done.finish("landed", at=1000.0, monotonic_at=1000.0)
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


def test_expiry_survives_a_wall_clock_jump():
    """A phone that corrects a stale clock on reconnect moves wall time by
    minutes or more. Measuring the retention window against it would reap a run
    that just finished -- destroying the reconnect window this feature exists
    to provide -- or, on a backward correction, keep every run's frames far
    longer than intended."""
    r = runs.RunRegistry()
    fresh, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    # Finished a moment ago by the monotonic clock, but stamped as if the wall
    # clock had since jumped an hour forward.
    fresh.finish("landed", at=1000.0, monotonic_at=r._now())
    assert r.reap(now=r._now() + 1) == 0, "a just-finished run was reaped"
    assert r.get(fresh.id, SCENE) is fresh


def test_get_refuses_a_recycled_sid_when_the_identity_moved():
    """A scene deleted and replaced inside the retention window lands on the
    same `sid`, so the subject alone says the replacement owns the dead scene's
    run -- and the stream and cancel routes all resolve through here."""
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident-old", LABELS)
    assert r.get(run.id, SCENE, identity="ident-old") is run
    assert r.get(run.id, SCENE, identity="ident-new") is None
    # No identity asked for: unchanged behaviour, for subjects that have none.
    assert r.get(run.id, SCENE) is run


def test_for_subject_filters_by_identity_too():
    r = runs.RunRegistry()
    old, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident-old", LABELS)
    assert r.for_subject(SCENE, identity="ident-new") == []
    assert r.for_subject(SCENE, identity="ident-old") == [old]


def test_attempt_adoption_also_checks_the_scene_identity():
    """A stale client retrying an old attempt id after the scene was deleted and
    its `sid` recycled would otherwise adopt the dead scene's run through the
    attempt path -- which `get` and `for_subject` already refuse."""
    r = runs.RunRegistry()
    old, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident-old", LABELS)
    old.finish("landed")

    fresh, started = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident-new", LABELS)
    assert started and fresh is not old


def test_events_are_not_built_while_the_registry_lock_is_held():
    """A loop-backed factory blocks on a portal round trip. Called under the
    registry lock, it can deadlock the server: the handler holds the lock and
    waits on the loop, while the loop's own reaper blocks on that same lock.
    """
    r = runs.RunRegistry()
    held = []

    def factory():
        # Whatever this does must not need the registry lock -- which is what a
        # portal round trip effectively needs, since loop-side code takes it.
        held.append(r._lock.acquire(blocking=False))
        if held[-1]:
            r._lock.release()
        return runs._PlainEvent()

    r.set_event_factory(factory)
    r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert all(held), "an event was constructed while the registry lock was held"


def test_reaping_an_old_run_keeps_a_newer_attempt_mapping():
    """A recycled scene id can start a replacement run under the same attempt
    id. Reaping the old one must not delete the mapping that now points at the
    replacement -- a retry would then start the work again instead of adopting
    it, which is the duplicate send this index exists to prevent."""
    r = runs.RunRegistry()
    old, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident-old", LABELS)
    old.finish("landed", at=1000.0, monotonic_at=1000.0)
    new, started = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident-new", LABELS)
    assert started

    assert r.reap(now=1000.0 + runs.REAP_SECONDS + 1) == 1
    adopted, started_again = r.start_or_existing(SCENE, "turn", "chat", "a1",
                                                 "ident-new", LABELS)
    assert adopted is new and not started_again


class _CountingLock:
    """The registry's lock, counting acquisitions. A context manager, because
    that is the only way the registry ever uses it."""

    def __init__(self, inner):
        self._inner, self.acquisitions = inner, 0

    def __enter__(self):
        self.acquisitions += 1
        return self._inner.__enter__()

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)


def test_a_stop_cannot_slip_between_the_lookup_and_the_reservation():
    """The precancel record is worth nothing unless writing it is ATOMIC with
    looking for the run, and the same for consuming it and publishing one.

    Asking and then recording is two acquisitions, and a reservation fits
    between them: the lookup finds nothing, the run publishes seeing no record,
    and the record lands afterwards for nobody to read -- the Stop is lost, in
    exactly the window the record was added to close.

    Asserted by counting acquisitions rather than by racing threads. The first
    version of this test tried to drive the interleaving with a blocked thread
    and passed against the two-acquisition version about as often as not, which
    is worse than no test: the property is "one lock, so no interleaving is
    possible", and a scheduler that happens to cooperate proves nothing about
    it. Counting says the thing directly and fails the moment it stops being
    true.
    """
    reg = runs.RunRegistry()
    lock = _CountingLock(reg._lock)
    reg._lock = lock
    subject = ("scene", "saltmarch", "001--mara")

    assert reg.cancel_or_precancel(subject, "a-1", "i1") is None
    assert lock.acquisitions == 1, (
        "recording a Stop took more than one acquisition, so a reservation "
        "can land in the gap and never see it")

    lock.acquisitions = 0
    run, fresh = reg.start_or_existing(subject, "turn", "chat", "a-1", "i1", {})
    assert lock.acquisitions == 1, (
        "the record is consumed outside the acquisition that publishes the "
        "run, so a Stop can land in the gap and be dropped")
    assert fresh and run.cancel_requested, "the Stop did not reach the run"


def test_a_stop_after_the_reservation_finds_the_run_itself():
    """The other ordering, which must not start recording a phantom precancel:
    a run that exists is cancelled through the run, and nothing is left behind
    for a later attempt with the same id to pick up."""
    reg = runs.RunRegistry()
    subject = ("scene", "saltmarch", "001--mara")
    run, _ = reg.start_or_existing(subject, "turn", "chat", "a-1", "i1", {})

    found = reg.cancel_or_precancel(subject, "a-1", "i1")

    assert found is run
    assert not run.cancel_requested, "the registry cancelled it rather than the caller"
    assert not reg._precancelled, "a record was left for an attempt that had a run"


# --- the live-run count the Android shell promotes on -----------------------

def test_the_live_callback_fires_at_reservation_not_when_the_runner_starts():
    """The promotion has to happen the moment a run is RESERVED.

    The registry goes live before the handler has built its prompt, and that
    setup is not always fast -- context construction can reach semantic recall.
    A phone locking during it would find the service unpromoted and the process
    reclaimable before the detached runner ever began, losing the turn in
    exactly the window the foreground service exists to protect.

    So this asserts the callback has ALREADY fired with the runner untouched,
    which fails against any implementation that hangs it off `runner.start`.
    """
    reg = runs.RunRegistry()
    seen: list[int] = []
    reg.set_live_sink(seen.append)

    reg.start_or_existing(SCENE, "turn", "chat", "a1", "i1", LABELS)

    assert seen == [1], f"promotion did not fire at reservation: {seen}"


def test_the_live_callback_fires_on_a_release_that_never_started():
    """And the matching demotion. A run reserved by a route that then refuses
    is never entered by the runner at all, so a demotion hung off the runner
    would leave the service pinned by a run that no longer exists."""
    reg = runs.RunRegistry()
    seen: list[int] = []
    reg.set_live_sink(seen.append)
    run, _ = reg.start_or_existing(SCENE, "turn", "chat", "a1", "i1", LABELS)

    reg.retire(run.id)                  # what the pre-start release path does

    assert seen == [1, 0], f"demotion did not fire on release: {seen}"


def test_the_callback_only_speaks_at_the_crossings():
    """Not once per run. The shell promotes on the first live run and demotes
    on the last, and a callback per reservation would have it thrashing the
    foreground state on every turn of a busy campaign."""
    reg = runs.RunRegistry()
    seen: list[int] = []
    reg.set_live_sink(seen.append)
    a, _ = reg.start_or_existing(SCENE, "turn", "chat", "a1", "i1", LABELS)
    b, _ = reg.start_or_existing(OTHER, "turn", "chat", "a2", "i2", LABELS)

    reg.retire(a.id)
    reg.retire(b.id)

    assert seen == [1, 0], f"the callback spoke between crossings: {seen}"


def test_a_failing_callback_does_not_reach_the_run():
    """Fail-soft, and this is the direction that matters: a foreground
    promotion the OS refuses, or a notification that cannot be built, must not
    take down a turn that is generating perfectly well."""
    reg = runs.RunRegistry()

    def refuse(_count):
        raise RuntimeError("the OS said no")

    reg.set_live_sink(refuse)

    run, fresh = reg.start_or_existing(SCENE, "turn", "chat", "a1", "i1", LABELS)

    assert fresh and run.state == "running"
    reg.retire(run.id)                  # and the other direction too


def test_a_stale_live_transition_is_dropped_rather_than_delivered():
    """The count is decided under the registry lock and delivered outside it,
    so two threads can reach the sink in the opposite order to the transitions
    they describe: retiring the last run computes 0, a new send reserves and
    delivers 1, and the delayed 0 arrives last.

    That is not a cosmetic ordering bug. `ServerService` demotes on 0, so the
    process becomes reclaimable while a run is generating -- the phone locks
    and the turn this feature exists to save is lost.

    Driven through `_fire_live` directly, with the stamps the registry would
    have given them. Racing two real threads does NOT discriminate here and an
    earlier version of this test proved it: the delivery lock serializes them,
    so the sequence guard can be deleted outright and the threaded version
    still passes. The rule is "a stamp older than the last delivered is not
    delivered", and this is that rule.
    """
    reg = runs.RunRegistry()
    seen: list[int] = []
    reg.set_live_sink(seen.append)

    reg._fire_live(1, 7)      # the new run's reservation, delivered first
    reg._fire_live(0, 6)      # the retire it overtook, arriving late

    assert seen == [1], "a superseded transition was announced as the present"


def test_deliveries_do_not_interleave_inside_the_sink():
    """The other half, and the reason the guard is not merely a comparison:
    held across the call, not just around the compare. Two sinks running
    concurrently would otherwise be inside the Android runtime at once, and
    `_delivered_seq` would say the newer had landed while the older was still
    executing.
    """
    reg = runs.RunRegistry()
    inside = threading.Semaphore(0)
    overlapped: list[bool] = []
    running = threading.Event()

    def sink(_live: int) -> None:
        overlapped.append(running.is_set())
        running.set()
        inside.release()
        time.sleep(0.05)
        running.clear()

    reg.set_live_sink(sink)
    threads = [threading.Thread(target=reg._fire_live, args=(1, n)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(2)

    assert overlapped == [False, False], "two deliveries were in the sink at once"


def test_an_ordinary_pair_of_transitions_still_both_arrive():
    """The counterweight: a sequence guard that dropped anything but a stale
    delivery would leave the service pinned, or never promoted at all."""
    reg = runs.RunRegistry()
    seen: list[int] = []
    reg.set_live_sink(seen.append)

    run, _ = reg.start_or_existing(("scene", "c", "i"), "turn", "chat", "a1", "i", {})
    reg.retire(run.id)

    assert seen == [1, 0]
