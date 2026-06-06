"""Unit tests for emergent-PC reconciliation memoization (#560).

The memoization that makes ``_reconcile_emergent_pcs`` run at most once per
campaign lives on the per-app :class:`ServiceContainer` instance, not in
module-level state — so a fresh container reconciles again and tests don't
leak the memoized set into one another.
"""

from __future__ import annotations

from types import SimpleNamespace

from grimoire.api.campaigns.scenes import _reconcile_emergent_pcs
from grimoire.api.container import ServiceContainer


class _FakeStore:
    def __init__(self) -> None:
        self.pcs: list[dict[str, str]] = []
        self.emergent: dict[tuple[str, str, str], object] = {}

    async def list_pcs(self, campaign_id: str) -> list[dict[str, str]]:
        return list(self.pcs)

    async def get_emergent(self, campaign_id: str, kind: str, asset_id: str) -> object | None:
        return self.emergent.get((campaign_id, kind, asset_id))


class _FakeCharacters:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.added_pcs: list[str] = []

    async def create_emergent(self, campaign_id: str, data: object) -> None:
        self.created.append(data.id)  # type: ignore[attr-defined]

    async def add_pc(self, campaign_id: str, ref: str, display: str) -> None:
        self.added_pcs.append(ref)


def _scene_with(*refs: str) -> SimpleNamespace:
    return SimpleNamespace(present_pc_refs=list(refs))


async def test_reconcile_creates_emergent_pc_once_per_campaign() -> None:
    container = ServiceContainer()
    store = _FakeStore()
    characters = _FakeCharacters()
    scene = _scene_with("emergent/wraith")

    await _reconcile_emergent_pcs("camp-1", scene, characters, store, container)

    assert characters.created == ["wraith"]
    assert characters.added_pcs == ["emergent/wraith"]
    assert "camp-1" in container.reconciled_campaigns

    # Second call for the same campaign on the same container is a no-op.
    await _reconcile_emergent_pcs("camp-1", scene, characters, store, container)
    assert characters.created == ["wraith"]
    assert characters.added_pcs == ["emergent/wraith"]


async def test_fresh_container_reconciles_again() -> None:
    store = _FakeStore()
    characters = _FakeCharacters()
    scene = _scene_with("emergent/wraith")

    container_a = ServiceContainer()
    await _reconcile_emergent_pcs("camp-1", scene, characters, store, container_a)
    assert characters.created == ["wraith"]

    # A new app instance (new container) has its own memo and reconciles anew.
    container_b = ServiceContainer()
    await _reconcile_emergent_pcs("camp-1", scene, characters, store, container_b)
    assert characters.created == ["wraith", "wraith"]


async def test_reconcile_skips_non_emergent_refs() -> None:
    container = ServiceContainer()
    store = _FakeStore()
    characters = _FakeCharacters()
    scene = _scene_with("library/hero", "emergent/wraith")

    await _reconcile_emergent_pcs("camp-1", scene, characters, store, container)

    assert characters.created == ["wraith"]
    assert characters.added_pcs == ["emergent/wraith"]
