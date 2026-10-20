"""A process-replaced lock guard must remain visible through the old interface."""

import os

from grimoire.store import locks


def test_pid_change_keeps_facade_and_runtime_on_the_same_guard(monkeypatch):
    # On the original module this exercises the existing PID fallback too.
    owner = getattr(locks, '_runtime', locks)
    before = owner._registry_guard
    monkeypatch.setattr(owner, '_world_lifecycle_pid', os.getpid() - 1)
    monkeypatch.setattr(owner, '_world_lifecycle_locks', {})

    locks._world_lifecycle_process()

    assert locks._registry_guard is owner._registry_guard
    assert locks._registry_guard is not before
    assert locks._world_lifecycle_pid == os.getpid()
    assert '_registry_guard' in dir(locks)
    assert '_world_lifecycle_pid' in dir(locks)
