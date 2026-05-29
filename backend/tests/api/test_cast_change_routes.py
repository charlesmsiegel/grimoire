"""REST contract tests for the cast-change routes (#464)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _FakeScene:
    id: str
    campaign_id: str


@dataclass
class _FakePending:
    id: str
    campaign_id: str = "c1"
    scene_id: str = "s1"
    character_ref: str = "library:worlds/w/characters/reyes"
    change: str = "enter"
    is_pc: bool = False
    evidence: str = "strides in"
    confidence: float = 0.8
    turn_id: str | None = "t1"
    status: str = "pending"
    created_at: str = "2026-05-28T00:00:00+00:00"

    def model_dump(self, mode: str = "python") -> dict:
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "scene_id": self.scene_id,
            "character_ref": self.character_ref,
            "change": self.change,
            "is_pc": self.is_pc,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "turn_id": self.turn_id,
            "status": self.status,
            "created_at": self.created_at,
        }


class FakeScenes:
    def __init__(self) -> None:
        self.scene = _FakeScene(id="s1", campaign_id="c1")
        self.pending = [_FakePending(id="cc-1")]
        self.calls: list[tuple] = []

    async def get_scene(self, scene_id: str) -> _FakeScene:
        if scene_id != self.scene.id:
            raise KeyError(scene_id)
        return self.scene

    async def list_pending_cast_changes(self, scene_id: str) -> list[_FakePending]:
        return list(self.pending)

    async def confirm_cast_change(self, scene_id: str, change_id: str) -> None:
        if change_id not in {p.id for p in self.pending}:
            raise KeyError(change_id)
        self.calls.append(("confirm", scene_id, change_id))

    async def dismiss_cast_change(self, scene_id: str, change_id: str) -> None:
        if change_id not in {p.id for p in self.pending}:
            raise KeyError(change_id)
        self.calls.append(("dismiss", scene_id, change_id))


@pytest.fixture
def wire(container, client):
    container.scenes = FakeScenes()
    return container


def test_list_cast_changes(wire, client) -> None:
    resp = client.get("/api/campaigns/c1/scenes/s1/cast-changes")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["character_ref"].endswith("reyes")
    assert body[0]["change"] == "enter"


def test_list_rejects_wrong_campaign(wire, client) -> None:
    resp = client.get("/api/campaigns/other/scenes/s1/cast-changes")
    assert resp.status_code == 404


def test_confirm_cast_change(wire, client) -> None:
    resp = client.post("/api/campaigns/c1/scenes/s1/cast-changes/cc-1/confirm")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert wire.scenes.calls == [("confirm", "s1", "cc-1")]


def test_confirm_unknown_change_404(wire, client) -> None:
    resp = client.post("/api/campaigns/c1/scenes/s1/cast-changes/cc-nope/confirm")
    assert resp.status_code == 404


def test_dismiss_cast_change(wire, client) -> None:
    resp = client.post("/api/campaigns/c1/scenes/s1/cast-changes/cc-1/dismiss")
    assert resp.status_code == 200
    assert wire.scenes.calls == [("dismiss", "s1", "cc-1")]


def test_list_unknown_scene_maps_to_404(wire, client) -> None:
    # get_scene raises KeyError for an unknown scene; the route must map it to
    # 404 rather than leaking a 500 (ownership check now inside the try).
    resp = client.get("/api/campaigns/c1/scenes/s_nope/cast-changes")
    assert resp.status_code == 404


def test_confirm_unknown_scene_maps_to_404(wire, client) -> None:
    resp = client.post("/api/campaigns/c1/scenes/s_nope/cast-changes/cc-1/confirm")
    assert resp.status_code == 404
