"""Tests for lifecycle management."""

import pytest

from grimoire.lifecycle import LifecycleManager


class _AsyncStopper:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _SyncStopper:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FailingStopper:
    async def stop(self):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_stop_all_stops_in_reverse_order():
    lm = LifecycleManager()
    order = []

    class A:
        async def stop(self):
            order.append("a")

    class B:
        def stop(self):
            order.append("b")

    lm.register_async("a", A())
    lm.register_sync("b", B())
    await lm.stop_all()
    assert order == ["b", "a"]


@pytest.mark.asyncio
async def test_stop_all_continues_after_failure():
    lm = LifecycleManager()
    good = _AsyncStopper()
    lm.register_async("failing", _FailingStopper())
    lm.register_async("good", good)
    await lm.stop_all()
    assert good.stopped


@pytest.mark.asyncio
async def test_stop_all_handles_mixed_async_sync():
    lm = LifecycleManager()
    a = _AsyncStopper()
    s = _SyncStopper()
    lm.register_async("async", a)
    lm.register_sync("sync", s)
    await lm.stop_all()
    assert a.stopped
    assert s.stopped
