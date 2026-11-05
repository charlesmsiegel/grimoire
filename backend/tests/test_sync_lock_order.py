"""Sync takes several libraries' locks in one total order, even when two opens share a root."""
from __future__ import annotations

import threading
from pathlib import Path

from grimoire.core.identity import PlacementRef
from grimoire.images import Placement
from grimoire.store import etag
from grimoire.sync import resolve, sync, sync_until_quiet
from grimoire.sync import session

from image_fixtures import HARBOR, PNG, SAILOR
from sync_helpers import DESKTOP, PHONE
from test_sync_images import H, _images, _path, unpaired  # noqa: F401  (fixture)


def test_held_hand_edited_source_placement_converges_by_resolving_to_the_valid_side(unpaired):  # noqa: F811
    desktop, phone = unpaired
    batch = _images(desktop).change()
    ref = batch.add_image(HARBOR, "wren", PNG, {"origin": "test"})
    batch.place(SAILOR, "avatar", Placement("set", "avatar", ref), None)
    batch.commit()
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    held = _path(PlacementRef(SAILOR, "avatar"))
    source = desktop.root / held
    text = source.read_text()
    source.write_text(text.replace('"mode": "set"', '"mode": "adjust"'))
    report = sync_until_quiet(desktop, phone, H)
    assert [c.code for c in report.conflicts] == ["head_dependency"]
    good, bad = (phone.root / held).read_bytes(), source.read_bytes()
    resolve(desktop, phone, held, etag(good), {DESKTOP: etag(bad), PHONE: etag(good)}, H)
    assert source.read_bytes() == good
    assert sync(desktop, phone, H).conflicts == ()


class _Fake:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.lock = threading.RLock()


def test_lock_order_is_total_when_roots_tie():
    """Two stores whose roots compare equal: _lock_order's stable sort keeps argument order, so
    _locked(a, b) and _locked(b, a) take the locks in opposite orders (an AB/BA deadlock)."""
    a, b = _Fake("/lib"), _Fake("/lib")
    holding_a, holding_b = threading.Event(), threading.Event()
    done = []

    def one(first, second, mine, theirs):  # type: ignore[no-untyped-def]
        with first.lock:
            mine.set()
            theirs.wait(2)
            if second.lock.acquire(timeout=1):
                second.lock.release()
                done.append(True)
            else:
                done.append(False)
    order1, order2 = session._lock_order((a, b)), session._lock_order((b, a))
    t1 = threading.Thread(target=one, args=(*order1, holding_a, holding_b), daemon=True)
    t2 = threading.Thread(target=one, args=(*order2, holding_b, holding_a), daemon=True)
    t1.start()
    t2.start()
    t1.join(5)
    t2.join(5)
    assert done == [True, True], f"lock-order inversion on a root tie: {done}"


def test_plan_sync_of_one_library_opened_twice_raises_same_device_both_ways(tmp_path, monkeypatch):
    """Real stores: a writable and a read-only open of one library share a root string. plan_sync(w, ro)
    and plan_sync(ro, w) lock in argument order; each holds one lock and times out on the other
    instead of both raising SameDevice."""
    import contextlib

    from grimoire.store import LibraryStore
    from grimoire.sync import SameDevice, identify, plan_sync
    w = LibraryStore.create(tmp_path / "lib")
    identify(w, DESKTOP)
    ro = LibraryStore.open(tmp_path / "lib", writable=False)
    assert w.root == ro.root and w.lock is not ro.lock
    barrier = threading.Barrier(2, timeout=3)
    real = LibraryStore.cached_reads
    first_taken = threading.local()

    @contextlib.contextmanager
    def paused(self):  # type: ignore[no-untyped-def]
        if not getattr(first_taken, "done", False):
            first_taken.done = True
            with self.lock:
                with contextlib.suppress(threading.BrokenBarrierError):
                    barrier.wait()  # both threads now hold their first library's lock
                with real(self):
                    yield
            return
        if not self.lock.acquire(timeout=2):
            raise TimeoutError("lock-order inversion: waited for the other library while holding one")
        try:
            with real(self):
                yield
        finally:
            self.lock.release()
    monkeypatch.setattr(LibraryStore, "cached_reads", paused)
    results: list[BaseException] = []

    def run(a, b):  # type: ignore[no-untyped-def]
        try:
            plan_sync(a, b, {})
        except BaseException as error:  # noqa: BLE001
            results.append(error)
    threads = [threading.Thread(target=run, args=args, daemon=True) for args in ((w, ro), (ro, w))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    w.close()
    assert all(isinstance(e, SameDevice) for e in results), [type(e).__name__ for e in results]
