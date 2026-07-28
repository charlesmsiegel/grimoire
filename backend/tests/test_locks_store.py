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

from grimoire.store import audit, campaigns, locks, proposals, scenes, sheets, worlds


def _campaign(monkeypatch, tmp_path, name="Run", module="pool-basic"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign(name, wid, module=module)
    return wid, cid


def _blocks_while_held(cid, call) -> bool:
    """True if `call` cannot start while campaign_lock(cid) is held here."""
    done = []
    with locks.campaign_lock(cid):
        t = threading.Thread(target=lambda: (call(), done.append(1)))
        t.start()
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


# ---- placement: the registry lives here and nowhere else (#245) ----


@pytest.mark.parametrize("mod", [sheets, proposals, audit])
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
    the same registry's locks, not a private one."""
    from grimoire.store import module_edit
    assert "locks.campaign_lock(" in inspect.getsource(module_edit._campaign_locks)
