"""§2 Periodic health prober."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from grimoire.imagegen.health_prober import ImageGenHealthProber


async def test_prober_calls_health_check_on_each_backend_periodically() -> None:
    svc = AsyncMock()
    svc.list_backends = AsyncMock(
        return_value=[
            type("B", (), {"id": "a"})(),
            type("B", (), {"id": "b"})(),
        ]
    )
    prober = ImageGenHealthProber(svc, interval_seconds=0.05)
    prober.start()
    await asyncio.sleep(0.20)  # ~3-4 ticks
    await prober.stop()
    # First call happens immediately; then every interval.
    # 2 backends x >=2 ticks = >=4 calls.
    assert svc.health_check.await_count >= 4
    # Each backend gets called.
    targets = {call.args[0] for call in svc.health_check.await_args_list}
    assert targets == {"a", "b"}


async def test_prober_stop_is_idempotent() -> None:
    svc = AsyncMock()
    svc.list_backends = AsyncMock(return_value=[])
    prober = ImageGenHealthProber(svc, interval_seconds=0.05)
    prober.start()
    await prober.stop()
    await prober.stop()  # should not raise


async def test_prober_swallows_health_check_errors() -> None:
    svc = AsyncMock()
    svc.list_backends = AsyncMock(return_value=[type("B", (), {"id": "a"})()])
    svc.health_check = AsyncMock(side_effect=RuntimeError("boom"))
    prober = ImageGenHealthProber(svc, interval_seconds=0.05)
    prober.start()
    # Poll until the loop has fired at least twice. A bare sleep is too
    # flaky on slow CI runners where the prober task may not get scheduled
    # within the interval window.
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 2.0
    while svc.health_check.await_count < 2 and loop.time() < deadline:
        await asyncio.sleep(0.02)
    await prober.stop()
    # Loop kept running despite the exception.
    assert svc.health_check.await_count >= 2
