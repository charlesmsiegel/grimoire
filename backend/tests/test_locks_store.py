"""store/locks.py -- the per-campaign lock registry (#245).

The registry itself is four lines; what these tests protect is the *domain*:
sheets, proposals and audit must all serialize on the same object, because
that unification is what lets a module edit holding a campaign exclude every
other campaign-scoped mutator. A refactor that hands any borrower its own
lock would leave every existing test passing and silently reopen that race.
"""

import inspect
import threading

import pytest

from grimoire.store import (audit, campaigns, dice, locks, proposals, rolls, scenes, sheets,
                            worlds)


def _campaign(monkeypatch, tmp_path, name="Run", module="pool-basic"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign(name, wid, module=module)
    return wid, cid


def _blocks_while_held(cid, call, hold=None) -> bool:
    """True if `call` cannot start while `hold` (default campaign_lock(cid)) is
    held here.

    The worker signals `started` immediately before invoking `call`, and we
    wait for that signal *before* starting the clock. Without it, "the worker
    didn't finish in 300ms" is also what a worker that never got scheduled
    looks like, and the test would pass against a lock-free implementation
    (Codex review, #255)."""
    done, started = [], threading.Event()

    def worker():
        started.set()
        call()
        done.append(1)

    with (hold or locks.campaign_lock(cid)):
        t = threading.Thread(target=worker)
        t.start()
        assert started.wait(timeout=5), "the worker thread never ran"
        t.join(timeout=0.3)
        blocked = not done
    t.join(timeout=5)
    assert done, "the call never completed once the lock was released"
    return blocked


# ---- the registry ----


def test_campaign_lock_is_stable_and_reentrant(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    with lock:
        with lock:  # RLock: no deadlock (audit.apply_delta composes set_field)
            pass
    assert locks.campaign_lock(cid) is lock


def test_campaign_lock_is_per_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    a = campaigns.create_campaign("One", wid, module="pool-basic")
    b = campaigns.create_campaign("Two", wid, module="pool-basic")
    assert locks.campaign_lock(a) is not locks.campaign_lock(b)
    with locks.campaign_lock(a):
        acquired = locks.campaign_lock(b).acquire(timeout=1)  # no cross-blocking
        assert acquired
        locks.campaign_lock(b).release()


def test_get_or_create_is_atomic_under_a_cold_registry():
    """A plain `if cid not in registry` would be a check-then-act race that
    hands two concurrent first-ever callers different lock objects -- and two
    writers each holding "the" lock is exactly the corruption this guards."""
    cid = "never-registered-campaign"
    locks._campaign_locks.pop(cid, None)
    barrier, seen = threading.Barrier(8), []
    guard = threading.Lock()

    def grab():
        barrier.wait()
        lock = locks.campaign_lock(cid)
        with guard:
            seen.append(lock)

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(seen) == 8
    assert all(lock is seen[0] for lock in seen)
    locks._campaign_locks.pop(cid, None)


# ---- one domain: every campaign-scoped mutator serializes here ----


def test_sheet_writes_serialize_on_the_campaign_lock(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    assert _blocks_while_held(
        cid, lambda: sheets.write(cid, "characters", "mara", "medium", None, expected=None))


def test_proposals_serialize_on_the_campaign_lock(monkeypatch, tmp_path):
    """Proposals borrow the campaign lock (mechanics Phase 8) so a module edit
    holding the campaign excludes proposal creation -- a proposal derived from
    the old pack can never persist after the swap."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    assert _blocks_while_held(cid, lambda: proposals.new(cid, "s1", {}))


def test_audit_baselines_serialize_on_the_campaign_lock(monkeypatch, tmp_path):
    """capture_baseline snapshots every sheet at once; it takes the campaign
    lock so a concurrent rebind can't interleave between resolving the module
    and snapshotting sheets under it."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    sid = scenes.create_scene(cid, "Saltmarch")
    audit.clear_baselines(cid)
    assert _blocks_while_held(cid, lambda: audit.capture_baseline(cid, sid))
    assert sid in audit.read_baselines(cid)


def test_scene_writes_serialize_on_the_campaign_lock(monkeypatch, tmp_path):
    """Scene mutation joins the same domain (#254) rather than opening a second
    registry: the flows that persist a reply already hold the campaign lock
    (routes.streaming._continuation_stream), so a scene-only lock would add a second
    ordering to get wrong for no gain."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Saltmarch")
    assert _blocks_while_held(cid, lambda: scenes.append_message(cid, sid, "user", "hi"))
    assert _blocks_while_held(
        cid, lambda: scenes.append_reply(cid, sid, [{"speaker": None, "content": "ok"}]))
    assert _blocks_while_held(cid, lambda: scenes.edit_message(cid, sid, 0, "edited"))


def _lock_is_free(cid) -> bool:
    """Whether some *other* thread could take the campaign lock right now.

    Probing from this thread would prove nothing: the lock is reentrant, so a
    holder's own `acquire` always succeeds. Acquire and release both happen in
    the probe thread — an RLock cannot be released by anyone else.
    """
    seen = []

    def probe():
        lock = locks.campaign_lock(cid)
        got = lock.acquire(timeout=0.3)
        seen.append(got)
        if got:
            lock.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "the probe never finished"  # its acquire caps at 0.3s
    return seen[0]


@pytest.mark.parametrize("dated", [
    lambda cid: scenes.create_scene(cid, "Hinted", "2026-06-29"),
    lambda cid: scenes.set_datetime(cid, scenes.create_scene(cid, "Dated"), "2026-06-29"),
])
def test_calendar_plugin_code_never_runs_under_the_campaign_lock(monkeypatch, tmp_path, dated):
    """`get_provider` imports every user-authored provider in
    `<home>/calendars/` and `normalize` then runs that provider's own code.
    Nothing bounds how long a hand-written plugin takes, so the two scene
    mutators that need a calendar resolve it before they take the lock --
    otherwise one bad calendar stalls every writer in the campaign."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    free = []

    # Every entry point into provider code, not just the import: `normalize`
    # and `friendly` both call methods the plugin author wrote.
    for name in ("get_provider", "normalize", "friendly"):
        real = getattr(scenes.calendars, name)

        def watched(*args, _real=real):
            free.append(_lock_is_free(cid))
            return _real(*args)

        monkeypatch.setattr(scenes.calendars, name, watched)

    dated(cid)
    assert free, "the calendar was never resolved -- the test proves nothing"
    assert all(free), "the campaign lock was held across user calendar code"


def test_a_campaign_lock_holder_can_still_write_a_scene(monkeypatch, tmp_path):
    """Reentrancy is load-bearing, not incidental: proposals.commit_narration
    persists the reply through a callback while holding this lock, and
    scenes.create_scene captures an audit baseline that takes it again."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    with locks.campaign_lock(cid):
        sid = scenes.create_scene(cid, "Nested")
        scenes.append_message(cid, sid, "user", "no deadlock")
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1


def test_roll_writes_serialize_on_the_campaign_lock(monkeypatch, tmp_path):
    """The roll log joined the domain in #255. Before that it ran a private
    registry, so a module-pack swap holding every campaign lock did NOT
    exclude a roll append even though it excluded the proposal that roll is
    tagged with -- two lock domains over the same cid."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    assert _blocks_while_held(
        cid, lambda: rolls.append(cid, "s1", "Perception", dice.roll("2d6", seed=1)))


def test_roll_append_and_proposal_transition_exclude_each_other(monkeypatch, tmp_path):
    """The coupling the unification is for: a logged roll can carry the id of
    the proposal it resolved, so a thread mid-proposal-transition must exclude
    a roll append. Held from the *proposals* side, which is the same lock only
    while rolls stays in the domain."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    assert _blocks_while_held(
        cid, lambda: rolls.append(cid, "s1", None, dice.roll("1d6", seed=1)),
        hold=proposals.locked(cid))


def test_rolls_mutators_are_reentrant_under_a_held_campaign_lock(monkeypatch, tmp_path):
    """``routes.streaming._project_resolution`` appends inside
    ``proposals.locked``. That nesting is only safe because the shared lock is
    an RLock -- swap it for a plain Lock and the whole projection sequence
    self-deadlocks.

    Run in a worker thread so that regression fails this test in one second
    instead of hanging the suite forever (Codex review, #255)."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    done = []

    def nested():
        with locks.campaign_lock(cid):          # the outer span (projection)
            entry = rolls.find_or_append_by_proposal(
                cid, "s1", "check", dice.roll("1d6", seed=1), "pr-x")
            rolls.repoint_scenes(cid, {"s1": "s2"})
            done.append(entry)

    t = threading.Thread(target=nested, daemon=True)
    t.start()
    t.join(timeout=10)
    assert done, "a rolls mutator deadlocked under an already-held campaign lock"
    assert done[0]["id"] == "r1"
    assert rolls.read(cid)[0]["scene"] == "s2"


# ---- placement: the registry lives here and nowhere else (#245) ----


def test_rolls_has_no_private_lock_registry():
    """#255: `rolls` was the last campaign-scoped mutator running its own
    `_LOCKS`/`_LOCKS_GUARD`. Reintroducing one would leave every rolls test
    passing while quietly leaving the shared domain again -- and locks.py's
    roster, which is how a reader discovers the domain, would go stale with
    no way to detect it from locks.py alone."""
    assert not hasattr(rolls, "_LOCKS")
    assert not hasattr(rolls, "_LOCKS_GUARD")
    assert not hasattr(rolls, "_lock")


@pytest.mark.parametrize("mod", [sheets, proposals, audit, scenes, rolls])
def test_borrowers_neither_re_export_nor_re_implement_the_registry(mod):
    """The lock domain is discoverable from store/locks.py only if no module
    re-exports or re-implements it: `sheets.lock_for()` was the old name and
    must not come back, and borrowers spell the call `locks.campaign_lock(...)`
    at the point of use rather than importing the name into their own
    namespace -- which is the readability the move was for."""
    assert not hasattr(mod, "lock_for")
    assert not hasattr(mod, "_campaign_locks")
    assert not hasattr(mod, "campaign_lock")
    assert mod.locks is locks


def test_module_edit_holds_every_campaign_lock_from_this_registry():
    """The multi-campaign holder (User-edit vs LLM-play exclusion) must take
    the same registry's locks, not a private one. It reaches them through
    ``locks.hold_all`` (#234), which is where the sorted-order rule lives; the
    order itself is asserted behaviourally in
    ``test_module_edit_acquires_campaign_locks_in_sorted_order``."""
    from grimoire.store import module_edit
    assert "locks.hold_all(" in inspect.getsource(module_edit._campaign_locks)


# ---- hold_all: one deadline, sorted order (#234) ----
#
# Sorted order is what keeps the two multi-campaign holders from deadlocking
# once these locks are cross-process.
#
# Note what these tests are and are not. The two holders already AGREE today:
# module_edit._campaign_locks() iterates list_campaigns(), which walks
# `sorted(base.iterdir())` and reports each directory name as the id, so it is
# already cid-sorted -- the same key put_world_module sorts by. There is no
# live inversion to fix.
#
# What is missing is any guarantee of that. The agreement is incidental,
# undocumented, and one refactor of list_campaigns() (sort by name, by
# `updated`, drop the sort) away from becoming a genuine cross-process
# deadlock. hold_all() makes the rule explicit and these tests hold it there.


def test_hold_all_acquires_in_sorted_order(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    order = []
    real = locks.campaign_lock

    def spy(cid):
        order.append(cid)
        return real(cid)

    monkeypatch.setattr(locks, "campaign_lock", spy)
    with locks.hold_all(["zeta", "alpha", "mid"]):
        pass
    assert order == ["alpha", "mid", "zeta"]


def test_hold_all_holds_every_named_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with locks.hold_all(["a", "b", "c"]):
        assert all(locks.campaign_lock(c)._is_owned() for c in ("a", "b", "c"))
    assert not any(locks.campaign_lock(c)._is_owned() for c in ("a", "b", "c"))


def test_module_edit_acquires_campaign_locks_in_sorted_order(monkeypatch, tmp_path):
    """Behavioural, not a source grep: spy on the registry and assert the ORDER
    module_edit actually asks for. A source assertion would pass on a docstring
    mention and would not catch an alias."""
    from grimoire.store import module_edit

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    for name in ("Zulu", "Alpha", "Mike"):
        campaigns.create_campaign(name, wid, module="pool-basic")

    order = []
    real = locks.campaign_lock
    monkeypatch.setattr(locks, "campaign_lock",
                        lambda c: (order.append(c), real(c))[1])
    with module_edit._campaign_locks():
        pass
    assert len(order) == 3, f"acquired nothing: {order}"
    assert order == sorted(order), f"unsorted acquisition: {order}"


def test_world_module_rebind_acquires_in_sorted_order(monkeypatch, tmp_path):
    """Same guarantee for the other multi-lock holder, through the real route."""
    from fastapi.testclient import TestClient

    from grimoire import main

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    for name in ("Zulu", "Alpha", "Mike"):
        campaigns.create_campaign(name, wid, module="pool-basic")

    order = []
    real = locks.campaign_lock
    monkeypatch.setattr(locks, "campaign_lock",
                        lambda c: (order.append(c), real(c))[1])
    client = TestClient(main.create_app())
    # A DIFFERENT module from the campaigns' current one, so the route cannot
    # take a no-op path and acquire nothing. ("none" is rejected as reserved.)
    r = client.put(f"/api/worlds/{wid}/module", json={"module": "d20-basic"})
    assert r.status_code == 200, r.text
    assert len(order) == 3, f"the route acquired nothing: {order}"  # not vacuous
    assert order == sorted(order), f"unsorted acquisition: {order}"
