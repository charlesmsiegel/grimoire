"""store/locks.py -- the per-campaign lock registry (#245).

The registry itself is four lines; what these tests protect is the *domain*:
sheets, proposals and audit must all serialize on the same object, because
that unification is what lets a module edit holding a campaign exclude every
other campaign-scoped mutator. A refactor that hands any borrower its own
lock would leave every existing test passing and silently reopen that race.
"""

import inspect
import os
import subprocess
import sys
import threading
import time

import pytest

from grimoire.store import (audit, calendars, campaigns, dice, locks, proposals, rolls,
                            scenes, sheets, worlds)
from grimoire.store.audit import apply as audit_apply, baselines as audit_baselines
from grimoire.store.campaigns import read as campaigns_read
from grimoire.store.scenes import locking as scenes_locking
from grimoire.store.sheets import (advancement as sheets_advancement,
                                   creation as sheets_creation, writer as sheets_writer)


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
        real = getattr(calendars, name)

        def watched(*args, _real=real):
            free.append(_lock_is_free(cid))
            return _real(*args)

        monkeypatch.setattr(calendars, name, watched)

    dated(cid)
    assert free, "the calendar was never resolved -- the test proves nothing"
    assert all(free), "the campaign lock was held across user calendar code"


def test_campaign_creation_never_runs_calendar_plugin_code_under_the_lock(monkeypatch, tmp_path):
    """The same rule on the creation path, which now takes the lock at all.

    `create_campaign` serializes from before it publishes `campaign.md` through
    the last initializing write, so the wizard's `calendar=`/`region=` handling
    became a candidate for running *inside* that span -- and
    `validate_calendar` both imports every user-authored provider and calls the
    provider's own `validate_rule`. Resolving and validating the config from the
    world root before the lock is what keeps the critical section bounded to
    this package's own file writes.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    free = []

    # `store.campaigns` is a package now, so the calendars module is reached
    # directly rather than through it -- the same object `campaigns.lifecycle`
    # binds, which is why patching an attribute on it still intercepts.
    for name in ("get_provider", "validate_calendar"):
        real = getattr(calendars, name)

        def watched(*args, _real=real):
            # The cid is not returned until creation finishes, so probe the id
            # creation will pick; the assert below fails loudly if it does not.
            free.append(_lock_is_free("saltmarch"))
            return _real(*args)

        monkeypatch.setattr(calendars, name, watched)

    cid = campaigns.create_campaign("Saltmarch", wid, calendar="hebrew", region="IL")
    assert cid == "saltmarch", "the probe watched a different campaign's lock"
    assert free, "no provider code ran -- the test proves nothing"
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


#: `sheets`, `audit` and `scenes` are packages now, so their borrowers are the
#: files that actually take the lock -- three under `sheets/`, two under
#: `audit/` (`prompt.py` reads sheets but takes no lock), and exactly one under
#: `scenes/`: every scene mutator wears `locking._serialized`, so `locking.py`
#: is the only file there that names the registry. No facade imports a `locks`
#: of its own; all three are checked separately below.
@pytest.mark.parametrize("mod", [sheets_writer, sheets_creation, sheets_advancement,
                                 proposals, audit_baselines, audit_apply, scenes_locking,
                                 rolls])
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


@pytest.mark.parametrize("pkg", [sheets, audit, scenes])
def test_a_borrower_facade_re_exports_no_part_of_the_registry(pkg):
    """The other half of the rule above, for the borrowers that are packages:
    each `__init__.py` re-exports every name its own files define, so a
    registry name reintroduced anywhere under `sheets/`, `audit/` or `scenes/`
    surfaces here -- including in a file the parametrized list above does not
    name."""
    assert not hasattr(pkg, "lock_for")
    assert not hasattr(pkg, "_campaign_locks")
    assert not hasattr(pkg, "campaign_lock")


def test_module_edit_holds_every_campaign_lock_from_this_registry():
    """The multi-campaign holder (User-edit vs LLM-play exclusion) must take
    the same registry's locks, not a private one. It reaches them through
    ``locks.hold_all`` (#234), which is where the sorted-order rule lives; the
    order itself is asserted behaviourally in
    ``test_module_edit_acquires_campaign_locks_in_sorted_order``."""
    from grimoire.store import module_edit
    assert "locks.hold_all(" in inspect.getsource(module_edit._campaign_locks)


# ---- hold_all: one deadline, sorted order (#234, #267) ----
#
# Sorted order is what keeps the two multi-campaign holders from deadlocking
# once these locks are cross-process.
#
# This comment used to say the two holders already agreed, incidentally, and
# that there was "no live inversion to fix" -- because list_campaigns() walks
# `sorted(base.iterdir())`. It does, and then it ends on
# `out.sort(key=updated, reverse=True)`, so what it returns is recency order
# while put_world_module sorts by cid. The inversion was live (#267); hold_all
# is what closes it, not a guarantee bolted onto an existing agreement.
#
# That mistake is also why the order tests below stamp `updated`. Campaigns
# created back-to-back share one `updated` second, and a stable sort on equal
# keys leaves them in iterdir order -- which IS cid order. Assert against that
# fixture and the acquisition order comes from the input rather than from the
# code: the test passes on an unsorted holder. Verified, not theorized -- with
# module_edit reverted to its pre-fix raw `ExitStack` loop, the un-stamped
# version of test_module_edit_acquires_campaign_locks_in_sorted_order passed.


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


def _three_campaigns_in_reverse_recency(monkeypatch, tmp_path):
    """Three campaigns whose recency order is the REVERSE of their id order.

    The whole point of the fixture. `list_campaigns()` returns
    `updated`-descending, so a holder that iterates it unsorted acquires
    ['zulu', 'mike', 'alpha'] here while a sorted one acquires
    ['alpha', 'mike', 'zulu'] -- the two are now distinguishable, which they
    are not for campaigns created in one second (see the note above).

    `touch` is the store's own writer for `updated`; only the clock it reads is
    replaced, so the frontmatter is stamped the way production stamps it.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    for name in ("Zulu", "Alpha", "Mike"):
        campaigns.create_campaign(name, wid, module="pool-basic")

    real_now = campaigns_read.now_iso
    for day, cid in enumerate(("alpha", "mike", "zulu"), start=1):
        monkeypatch.setattr(campaigns_read, "now_iso",
                            lambda day=day: f"2026-01-{day:02d}T00:00:00Z")
        campaigns.touch(cid)
    monkeypatch.setattr(campaigns_read, "now_iso", real_now)   # the route touches too

    listed = [c["id"] for c in campaigns.list_campaigns()]
    # Not an assertion about list_campaigns -- an assertion that this fixture
    # can still tell a sorted holder from an unsorted one. If list_campaigns
    # ever returns cid order again, every order test below goes vacuous, and
    # this is what says so instead of quietly passing.
    assert listed == ["zulu", "mike", "alpha"], listed
    return wid


def test_module_edit_acquires_campaign_locks_in_sorted_order(monkeypatch, tmp_path):
    """Behavioural, not a source grep: spy on the registry and assert the ORDER
    module_edit actually asks for. A source assertion would pass on a docstring
    mention and would not catch an alias."""
    from grimoire.store import module_edit

    _three_campaigns_in_reverse_recency(monkeypatch, tmp_path)

    order = []
    real = locks.campaign_lock
    monkeypatch.setattr(locks, "campaign_lock",
                        lambda c: (order.append(c), real(c))[1])
    with module_edit._campaign_locks():
        pass
    assert len(order) == 3, f"acquired nothing: {order}"
    assert order == sorted(order), f"unsorted acquisition: {order}"


def test_world_module_rebind_acquires_in_sorted_order(monkeypatch, tmp_path):
    """Same guarantee for the other multi-lock holder, through the real route.

    Same fixture as module_edit's, deliberately: this route is the holder that
    sorts by cid, so it is only distinguishable from the one that iterates
    `list_campaigns()` when those two orders disagree."""
    from fastapi.testclient import TestClient

    from grimoire import main

    wid = _three_campaigns_in_reverse_recency(monkeypatch, tmp_path)

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


# ---- cross-process exclusion (#234) ----
#
# These need real processes. An in-process test cannot demonstrate that a
# second *process* is excluded, which is the entire point of the change.

_HOLDER = """
import sys, time
sys.path[:0] = {path!r}
from grimoire.store import locks
lock = locks.{factory}
with lock:
    print("HELD", flush=True)
    time.sleep({hold})
"""

_PROBE = """
import sys
sys.path[:0] = {path!r}
from grimoire.store import locks
lock = locks.{factory}
print("GOT" if lock.acquire(timeout={wait}) else "BUSY", flush=True)
"""


def _hold_in_child(tmp_path, factory, hold=30.0):
    """Spawn a process holding a lock; return once it actually holds it."""
    src = _HOLDER.format(path=sys.path, factory=factory, hold=hold)
    p = subprocess.Popen([sys.executable, "-c", src], text=True,
                         stdout=subprocess.PIPE,
                         env={**os.environ, "GRIMOIRE_HOME": str(tmp_path)})
    assert p.stdout.readline().strip() == "HELD", "child never acquired"
    return p


def _probe(tmp_path, factory, wait=0.5) -> str:
    """Ask another process whether it can take the lock right now."""
    src = _PROBE.format(path=sys.path, factory=factory, wait=wait)
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, timeout=60,
                       env={**os.environ, "GRIMOIRE_HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_a_second_process_cannot_hold_the_same_campaign(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, "campaign_lock(%r)" % cid)
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
        with pytest.raises(locks.CampaignBusy):
            with locks.campaign_lock(cid):
                pass
    finally:
        p.kill()
        p.wait(timeout=10)


def test_the_campaign_is_acquirable_once_the_holder_exits(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, "campaign_lock(%r)" % cid)
    p.kill()
    p.wait(timeout=10)
    deadline = time.monotonic() + 15
    while not locks.campaign_lock(cid).acquire(timeout=0.2):
        assert time.monotonic() < deadline, "never released"
    locks.campaign_lock(cid).release()


def test_different_campaigns_do_not_block_across_processes(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    a = campaigns.create_campaign("One", wid, module="pool-basic")
    b = campaigns.create_campaign("Two", wid, module="pool-basic")
    p = _hold_in_child(tmp_path, "campaign_lock(%r)" % a)
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 5.0)
        with locks.campaign_lock(b):
            pass
    finally:
        p.kill()
        p.wait(timeout=10)


def test_reentrancy_takes_the_file_lock_exactly_once(monkeypatch, tmp_path):
    """A child must stay blocked at depth 2 and be freed only by the OUTERMOST
    release -- otherwise the inner exit drops cross-process exclusion."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    factory = "campaign_lock(%r)" % cid
    with lock:
        with lock:
            pass                                   # inner exit must NOT unlock
        assert _probe(tmp_path, factory) == "BUSY", "inner exit dropped the file lock"
    assert _probe(tmp_path, factory) == "GOT", "outer exit failed to release"


# ---- the RLock contract the wrapper must preserve ----


def test_acquire_returns_false_rather_than_raising(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, "campaign_lock(%r)" % cid)
    try:
        assert locks.campaign_lock(cid).acquire(timeout=0.3) is False
    finally:
        p.kill()
        p.wait(timeout=10)


def test_non_blocking_acquire_does_not_retry(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, "campaign_lock(%r)" % cid)
    try:
        started = time.monotonic()
        assert locks.campaign_lock(cid).acquire(blocking=False) is False
        assert time.monotonic() - started < 2.0
    finally:
        p.kill()
        p.wait(timeout=10)


def test_non_blocking_acquire_rejects_a_timeout(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        locks.campaign_lock(cid).acquire(blocking=False, timeout=5)


def test_acquire_rejects_a_negative_timeout_that_is_not_minus_one(monkeypatch, tmp_path):
    """RLock accepts -1 as "no timeout" and rejects every other negative."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        locks.campaign_lock(cid).acquire(timeout=-2)


def test_release_by_a_non_owner_raises_and_changes_nothing(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    with lock:
        err = []

        def stranger():
            try:
                lock.release()
            except RuntimeError as e:
                err.append(e)

        t = threading.Thread(target=stranger)
        t.start()
        t.join(timeout=5)
        assert err, "a non-owner release must raise"
        assert lock._is_owned(), "our hold survived the intruder"
    assert lock.acquire(timeout=5), "still usable afterwards"
    lock.release()


def test_the_thread_lock_is_released_after_a_timeout(monkeypatch, tmp_path):
    """A timed-out acquire must not strand the in-process RLock."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, "campaign_lock(%r)" % cid)
    try:
        assert locks.campaign_lock(cid).acquire(timeout=0.3) is False
        assert not locks.campaign_lock(cid)._is_owned()
    finally:
        p.kill()
        p.wait(timeout=10)
    deadline = time.monotonic() + 15
    while not locks.campaign_lock(cid).acquire(timeout=0.2):
        assert time.monotonic() < deadline
    locks.campaign_lock(cid).release()


def test_an_exception_inside_the_block_releases_both_layers(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    with pytest.raises(ValueError):
        with lock:
            raise ValueError("boom")
    assert not lock._is_owned()
    assert lock.acquire(timeout=5)
    lock.release()


def test_a_failing_unlock_still_closes_the_fd_and_releases(monkeypatch, tmp_path):
    """Inject the failure at the UNLOCK, not at proclock.release: the
    requirement is that release() closes the descriptor even when the unlock
    call itself raises. Stubbing release() to succeed-then-raise would test
    something else entirely."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    fds = []
    real_acquire = locks.proclock.acquire

    def spy(path, deadline):
        fd = real_acquire(path, deadline)
        fds.append(fd)
        return fd

    def bad_unlock(fd):
        raise OSError("unlock failed")

    monkeypatch.setattr(locks.proclock, "acquire", spy)
    monkeypatch.setattr(locks.proclock, "_unlock", bad_unlock)
    with pytest.raises(OSError):
        with lock:
            pass
    assert fds, "the lock was taken"
    with pytest.raises(OSError):        # EBADF: the fd really was closed
        os.fstat(fds[0])
    assert not lock._is_owned(), "the thread lock was freed anyway"


# ---- hold_all, cross-process ----


def test_hold_all_keeps_unwinding_when_one_release_raises(monkeypatch, tmp_path):
    """A plain `for lock in reversed(held): lock.release()` strands every
    remaining lock when one raises. ExitStack does not.

    Needs a _ProcessScopedLock instance: a built-in threading.RLock has a
    read-only `release`, so monkeypatch.setattr on one raises AttributeError.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    first = locks.campaign_lock("aaa")
    real_release = first.release
    calls = []

    def angry():
        calls.append("aaa")
        real_release()
        raise OSError("release exploded")

    monkeypatch.setattr(first, "release", angry)
    with pytest.raises(OSError):
        with locks.hold_all(["aaa", "bbb"]):
            pass
    assert calls == ["aaa"]
    assert locks.campaign_lock("bbb").acquire(timeout=5), "bbb was stranded"
    locks.campaign_lock("bbb").release()


def test_hold_all_unwinds_everything_when_a_later_lock_is_busy(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    p = _hold_in_child(tmp_path, "campaign_lock('zulu')")
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
        with pytest.raises(locks.CampaignBusy):
            with locks.hold_all(["alpha", "zulu"]):
                pass
        assert not locks.campaign_lock("alpha")._is_owned(), "alpha was stranded"
        assert locks.campaign_lock("alpha").acquire(timeout=5)
        locks.campaign_lock("alpha").release()
    finally:
        p.kill()
        p.wait(timeout=10)


def test_hold_all_spends_one_shared_deadline_across_the_whole_span(monkeypatch, tmp_path):
    """The budget must be shared: time spent on an earlier lock has to come out
    of what is left for a later one.

    Getting this test to discriminate took two tries. Contending several locks
    proves nothing, because hold_all raises on the FIRST failure either way, so
    per-lock and shared deadlines take the same wall-clock. What separates them
    is a SLOW-but-successful early acquisition followed by a contended one:
    with a shared deadline the contended lock inherits only the remainder and
    fails fast; with a per-lock deadline it starts a fresh full timeout.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    p = _hold_in_child(tmp_path, "campaign_lock('zzz')")
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 2.0)
        real = locks.campaign_lock

        def slow(cid):
            lock = real(cid)
            if cid == "aaa":
                time.sleep(1.7)        # eats most of the shared budget
            return lock

        monkeypatch.setattr(locks, "campaign_lock", slow)
        started = time.monotonic()
        with pytest.raises(locks.CampaignBusy):
            with locks.hold_all(["aaa", "zzz"]):   # sorted: aaa first, then zzz
                pass
        elapsed = time.monotonic() - started
        # shared: ~1.7 + ~0.3 = ~2.0s. per-lock: ~1.7 + a fresh 2.0 = ~3.7s.
        assert elapsed < 3.0, "took %.1fs: each lock got its own deadline" % elapsed
    finally:
        p.kill()
        p.wait(timeout=10)


def test_a_second_process_cannot_hold_the_module_edit_lock(monkeypatch, tmp_path):
    """The second stated goal of #234.

    Note what this asserts on: module_edit._M, the lock the code actually
    takes -- NOT locks.module_edit_lock(). Testing the new lock against itself
    would pass before _M is rewired, so _M could stay a private process-local
    RLock and every test would still be green.
    """
    from grimoire.store import module_edit

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    p = _hold_in_child(tmp_path, "module_edit_lock()")
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
        with pytest.raises(locks.ModuleEditBusy):
            with module_edit._M:            # the lock module_edit really uses
                pass
    finally:
        p.kill()
        p.wait(timeout=10)


def test_a_campaign_named_module_edit_does_not_collide_with_the_module_lock(
        monkeypatch, tmp_path):
    """Lock namespaces must not overlap.

    Without a domain in the lock path, a campaign whose id is literally
    `module-edit` hashes to the same file as the global module-edit lock. Module
    publication takes the module-edit lock and THEN every campaign lock, so that
    campaign would make publication block on itself for the full timeout and
    could make journal recovery skip forever. Nothing else in the suite would
    have noticed.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert (locks.campaign_lock("module-edit")._path()
            != locks.module_edit_lock()._path())

    # and the real consequence: holding one must not block the other
    with locks.module_edit_lock():
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 2.0)
        with locks.campaign_lock("module-edit"):
            pass


def test_lock_domains_are_folded_into_the_digest(tmp_path):
    """Not merely into the readable prefix -- otherwise a crafted campaign id
    could still be made to hash onto another domain's file."""
    from grimoire.store import proclock

    a = proclock.lock_path(tmp_path, "campaign", "x")
    b = proclock.lock_path(tmp_path, "domain", "x")
    assert a != b
    assert a.name.split("-")[-1] != b.name.split("-")[-1]   # digests differ


def test_a_held_module_edit_lock_blocks_a_real_publication(monkeypatch, tmp_path):
    """`with module_edit._M` proves the object is shared; this proves the thing
    users actually hit. A child holds the module-edit lock and a real
    publication entry point must refuse rather than swap a pack under it."""
    from grimoire.store import module_edit

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    p = _hold_in_child(tmp_path, "module_edit_lock()")
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
        with pytest.raises(locks.ModuleEditBusy):
            module_edit.create_module("Blocked Pack")
    finally:
        p.kill()
        p.wait(timeout=10)


def test_a_non_owner_release_does_not_drop_cross_process_exclusion(monkeypatch, tmp_path):
    """The in-process half of this is covered above. The half that matters for
    #234 is that a stray release from another thread cannot unlock the FILE --
    which only another process can observe."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    factory = "campaign_lock(%r)" % cid
    with lock:
        errs = []

        def stranger():
            try:
                lock.release()
            except RuntimeError as e:
                errs.append(e)

        t = threading.Thread(target=stranger)
        t.start()
        t.join(timeout=5)
        assert errs, "a non-owner release must raise"
        assert _probe(tmp_path, factory) == "BUSY", \
            "a stray release unlocked the file for another process"
    assert _probe(tmp_path, factory) == "GOT", "still releasable by its owner"


def test_another_thread_can_acquire_after_our_acquire_timed_out(monkeypatch, tmp_path):
    """Retrying from the same thread would succeed even if the timed-out
    acquire had left the reentrant RLock held by us -- an RLock lets its owner
    back in. Only a DIFFERENT thread proves it was really released."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, "campaign_lock(%r)" % cid)
    try:
        assert locks.campaign_lock(cid).acquire(timeout=0.3) is False
    finally:
        p.kill()
        p.wait(timeout=10)

    got = []

    def other_thread():
        lock = locks.campaign_lock(cid)
        deadline = time.monotonic() + 15
        while not lock.acquire(timeout=0.2):
            if time.monotonic() > deadline:
                return
        got.append(True)
        lock.release()

    t = threading.Thread(target=other_thread)
    t.start()
    t.join(timeout=30)
    assert got, "the timed-out acquire stranded the lock against other threads"


def test_a_contended_create_scene_leaves_no_scene_behind(monkeypatch, tmp_path):
    """create_scene runs its whole body under the lock (#254), so contention
    must strike before the first durable write -- no orphaned scene file, and
    no scene without the audit baseline that capture_baseline now refuses to
    skip silently."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    d = campaigns.campaign_root(cid) / "scenes"
    before = sorted(p.name for p in d.glob("*")) if d.exists() else []

    p = _hold_in_child(tmp_path, "campaign_lock(%r)" % cid)
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
        with pytest.raises(locks.CampaignBusy):
            scenes.create_scene(cid, "Saltmarch")
    finally:
        p.kill()
        p.wait(timeout=10)

    after = sorted(q.name for q in d.glob("*")) if d.exists() else []
    assert after == before, "a scene file survived a contended create"


@pytest.mark.parametrize("route,payload", [
    ("roll", {"notation": "1d20", "label": "Perception"}),
    ("check", {"check": "brawl", "actor": "characters:mara", "difficulty": 6}),
])
def test_a_manual_roll_and_its_transcript_line_share_one_hold(
        monkeypatch, tmp_path, route, payload):
    """The roll and its transcript line must commit under ONE hold.

    Both writes take the campaign lock anyway, but separately: contention
    arriving between them returns 409 with the roll already durable and no
    transcript line, so the retry the 409 invites logs a SECOND roll while the
    first stays invisible forever.

    Asserting on depth rather than on an observable failure, because the
    failure is not reachable from a test: a permanently-held lock makes
    `rolls.append` raise first and nothing is written either way, and there is
    no way to make contention arrive *between* two calls from outside.

    The spies stand in front of the real mutators, so they observe the depth
    the ROUTE established -- 1 when the route wraps both writes in one hold, 0
    when each mutator is left to take its own. That zero-versus-one is the
    whole property, and it is verified red-green.
    """
    from fastapi.testclient import TestClient

    from grimoire import main
    from grimoire.store import rolls

    _wid, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    client = TestClient(main.create_app())
    sid = scenes.create_scene(cid, "Saltmarch")

    depths = []
    real_append_roll = rolls.append
    real_append_msg = scenes.append_message

    def spy_roll(*a, **k):
        depths.append(("rolls", locks.campaign_lock(cid)._depth))
        return real_append_roll(*a, **k)

    def spy_msg(*a, **k):
        depths.append(("scene", locks.campaign_lock(cid)._depth))
        return real_append_msg(*a, **k)

    monkeypatch.setattr(rolls, "append", spy_roll)
    monkeypatch.setattr(scenes, "append_message", spy_msg)

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/{route}", json=payload)
    assert r.status_code == 200, r.text
    assert [k for k, _ in depths] == ["rolls", "scene"], depths
    assert all(d >= 1 for _, d in depths), \
        f"each write took its own lock instead of sharing the route's: {depths}"


def test_the_chronicle_save_persists_under_one_hold(monkeypatch, tmp_path):
    """`put_chronicle` writes the chronicle record, the timeline, the scene's
    absorbed marker and the approved edits -- four independent writes.

    With a lock per write, contention arriving partway returned 409 after the
    record and timeline were already durable, and the retry that 409 invites
    appended the timeline events a SECOND time while the first attempt's edits
    were never applied. One hold means a busy response is reported before the
    first write.

    Depth is the discriminator for the same reason as the manual-roll test:
    contention cannot be made to arrive *between* two calls from outside, and
    the spies stand in front of the real mutators, so they see the depth the
    ROUTE established -- 1 with the outer hold, 0 without.
    """
    from fastapi.testclient import TestClient

    from grimoire import main
    from grimoire.store import chronicle

    _wid, cid = _campaign(monkeypatch, tmp_path)
    client = TestClient(main.create_app())
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", "something happened")

    depths = []
    real_absorb = chronicle.absorb
    real_timeline = chronicle.append_timeline

    def spy_absorb(*a, **k):
        depths.append(("absorb", locks.campaign_lock(cid)._depth))
        return real_absorb(*a, **k)

    def spy_timeline(*a, **k):
        depths.append(("timeline", locks.campaign_lock(cid)._depth))
        return real_timeline(*a, **k)

    monkeypatch.setattr(chronicle, "absorb", spy_absorb)
    monkeypatch.setattr(chronicle, "append_timeline", spy_timeline)

    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={
        "one_line": "They argued.", "summary": "A long argument.",
        "keywords": [], "timeline_events": [], "edits": []})
    assert r.status_code == 200, r.text
    assert [k for k, _ in depths] == ["absorb", "timeline"], depths
    assert all(d >= 1 for _, d in depths), \
        f"each write took its own lock instead of sharing the route's: {depths}"


def test_nowait_reports_contention_instead_of_waiting(monkeypatch, tmp_path):
    """`campaign_lock` waits up to LOCK_TIMEOUT, which is right for a mutation
    that has to happen and wrong for one that can be skipped. The nowait form
    exists for the second kind — see `store.prompt_log.record`."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    held, done = threading.Event(), threading.Event()

    def hold():
        with locks.campaign_lock("run"):
            held.set()
            done.wait(10)

    keeper = threading.Thread(target=hold)
    keeper.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        with locks.campaign_lock_nowait("run") as got:
            assert got is False
        assert time.monotonic() - started < 1.0        # not LOCK_TIMEOUT
    finally:
        done.set()
        keeper.join(10)

    with locks.campaign_lock_nowait("run") as got:     # free again
        assert got is True


def test_nowait_is_reentrant_for_a_thread_that_already_holds_it(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with locks.campaign_lock("run"):
        with locks.campaign_lock_nowait("run") as got:
            assert got is True
    # and the outer release still leaves it fully free
    with locks.campaign_lock_nowait("run") as got:
        assert got is True


def test_nowait_releases_only_what_it_took(monkeypatch, tmp_path):
    """A False yield must not release a lock it never acquired — that would
    corrupt the holder's state rather than merely skip the work."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    held, done = threading.Event(), threading.Event()

    def hold():
        with locks.campaign_lock("run"):
            held.set()
            done.wait(10)

    keeper = threading.Thread(target=hold)
    keeper.start()
    try:
        assert held.wait(5)
        with locks.campaign_lock_nowait("run") as got:
            assert got is False          # exiting here must not raise
    finally:
        done.set()
        keeper.join(10)
    assert not keeper.is_alive()


def test_a_best_effort_hold_gives_up_instead_of_waiting(monkeypatch, tmp_path):
    """`context._assemble` pairs the transcript with the transient-state ledger
    under this (#120). It must never block a turn or raise: `post_chat` has
    already appended the player's post by then, and the undo that would take it
    back off is not wired until afterwards — a `StoreBusy` there strands the
    post with nothing able to remove it. Under contention it reports False and
    the caller reads unlocked."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = "run"
    held, done = threading.Event(), threading.Event()
    HOLD = 12.0

    def _hold():
        with locks.campaign_lock(cid):
            held.set()
            done.wait(HOLD)

    t = threading.Thread(target=_hold, daemon=True)
    t.start()
    assert held.wait(5)
    try:
        started = time.monotonic()
        with locks.best_effort_campaign_lock(cid, timeout=0.5) as got:
            elapsed = time.monotonic() - started
        assert got is False                     # did not get it
        assert elapsed < 5.0                    # and did not wait the holder out
    finally:
        done.set()
        t.join(5)


def test_a_best_effort_hold_takes_the_lock_when_it_is_free(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with locks.best_effort_campaign_lock("run") as got:
        assert got is True
    # released: a normal hold still works afterwards
    with locks.campaign_lock("run"):
        pass
