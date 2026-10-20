"""Fork recovery replaces guards without changing the readable log interface."""

from grimoire.store import logs


def test_process_recovery_keeps_facade_state_current(monkeypatch):
    owner = getattr(logs, "_recording", logs)
    before = logs._append_guard
    written, capped, floor = logs._written, logs._capped, logs._floor
    monkeypatch.setattr(owner, "_pid", -1)
    logs._ensure_process()
    assert logs._append_guard is owner._append_guard
    assert logs._append_guard is not before
    assert logs._size_guard is owner._size_guard
    assert logs._busy is owner._busy
    assert logs._pid == owner.os.getpid()
    assert logs._written is written
    assert logs._capped is capped
    assert logs._floor is floor
