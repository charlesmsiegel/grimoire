"""§3 create_world auto-fills empty atmosphere when config flag is on."""

from __future__ import annotations

import json

from grimoire.library import LibraryService
from grimoire.world import WorldConfig, WorldService


class _FakeGateway:
    async def complete(self, task, request, *, campaign_id=None, turn_id=None):
        return type(
            "Resp",
            (),
            {
                "text": json.dumps(
                    {
                        "default_register": "test register",
                        "default_palette": "test palette",
                        "mood_tags": ["a", "b"],
                    }
                )
            },
        )()


def _atmosphere_of(meta) -> dict:
    raw = meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)
    return raw.get("atmosphere") or {}


async def test_create_world_fills_atmosphere_when_empty_and_flag_on(
    library: LibraryService,
) -> None:
    svc = WorldService(library, gateway=_FakeGateway())
    meta = await svc.create_world("w1", {"id": "w1", "name": "W1"})
    atmosphere = _atmosphere_of(meta)
    assert atmosphere.get("default_register") == "test register"
    assert atmosphere.get("mood_tags") == ["a", "b"]


async def test_create_world_skips_when_flag_off(library: LibraryService) -> None:
    svc = WorldService(
        library,
        gateway=_FakeGateway(),
        config=WorldConfig(atmosphere_auto_generate=False),
    )
    meta = await svc.create_world("w1", {"id": "w1", "name": "W1"})
    assert _atmosphere_of(meta) == {}


async def test_create_world_skips_when_atmosphere_already_set(
    library: LibraryService,
) -> None:
    svc = WorldService(library, gateway=_FakeGateway())
    meta = await svc.create_world(
        "w1",
        {"id": "w1", "name": "W1", "atmosphere": {"default_register": "preset"}},
    )
    assert _atmosphere_of(meta).get("default_register") == "preset"


async def test_create_world_skips_when_no_gateway(library: LibraryService) -> None:
    svc = WorldService(library, gateway=None)
    meta = await svc.create_world("w1", {"id": "w1", "name": "W1"})
    assert _atmosphere_of(meta) == {}
