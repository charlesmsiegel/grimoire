"""Wrappers that let a real function run and record something about each call.

`record_calls` is for the bounded-I/O tests: a read that must not scan a
listing or resolve a record again says so by the calls it records.
`campaign_lock_watch` is for the reads a route must make under the campaign
lock: the ledger, timeline and briefing each read several files that one save
writes in sequence under that lock, so each must read them under it too or it
can catch the save half done.
"""

import grimoire.store as store


def record_calls(monkeypatch, owner, name) -> list:
    """Patch `owner.name` with a pass-through that appends each call's
    positional arguments to the returned list."""
    calls = []
    real = getattr(owner, name)

    def recording(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(owner, name, recording)
    return calls


def campaign_lock_watch(cid, held: dict):
    """A `watch(name, real)` that wraps `real` so each call records, in
    `held[name]`, whether this thread holds `cid`'s campaign lock, then runs
    it."""
    def watch(name, real):
        def wrapper(*a, **kw):
            held[name] = store.locks.campaign_lock(cid)._is_owned()
            return real(*a, **kw)
        return wrapper
    return watch
