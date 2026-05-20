"""REST contract tests for the reclassification routes."""

from __future__ import annotations

from typing import Any


class FakeReclassifyLibrary:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def preview_reclassification(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: str,
    ) -> dict[str, Any]:
        self.calls.append(("preview", world_id, source_id, target_kind))
        return {
            "source_id": source_id,
            "target_kind": target_kind,
            "frontmatter": {"name": "Beatrice"},
            "body": "She lived.",
            "kept": ["name"],
            "dropped": [],
            "into_notes": [],
            "warnings": [],
            "required_overrides": [],
            "suggestion": {"kind": "character", "confidence": 0.9, "reason": "pronouns"},
        }

    async def reclassify_entity(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: str,
        overrides: dict | None = None,
        actor: str = "user",
    ) -> Any:
        from grimoire.library.reclassify import ReclassificationResult
        from grimoire.types.common import EntityKind

        self.calls.append(("commit", world_id, source_id, target_kind, overrides, actor))
        return ReclassificationResult(
            source_id=source_id,
            target_id="beatrice",
            target_kind=EntityKind(target_kind),
            fields_kept=["name"],
            fields_dropped=[],
            fields_into_notes=[],
            warnings=[],
        )

    async def undo_reclassification(
        self,
        world_id: str,
        timestamp: str,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        self.calls.append(("undo", world_id, timestamp, actor))
        return {
            "restored_source_id": "beatrice",
            "deleted_target_id": "beatrice",
            "undo_of": timestamp,
            "warnings": [],
        }

    async def list_reclassifications(self, world_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list", world_id))
        return [{"ts": "2026-05-19T00:00:00Z", "source_id": "x", "target_kind": "character"}]


def test_preview_reclassify_returns_mapping(client, container) -> None:
    container.library = FakeReclassifyLibrary()
    response = client.get(
        "/api/library/worlds/w/lore/beatrice/reclassify/preview?target_kind=character"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["frontmatter"]["name"] == "Beatrice"
    assert body["suggestion"]["kind"] == "character"


def test_commit_reclassify_writes_target(client, container) -> None:
    fake = FakeReclassifyLibrary()
    container.library = fake
    response = client.post(
        "/api/library/worlds/w/lore/beatrice/reclassify",
        json={"target_kind": "character", "overrides": {"role": "major_npc"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["target_id"] == "beatrice"
    assert body["target_kind"] == "character"
    assert ("commit", "w", "beatrice", "character", {"role": "major_npc"}, "user") in fake.calls


def test_undo_reclassify_calls_service(client, container) -> None:
    fake = FakeReclassifyLibrary()
    container.library = fake
    ts = "2026-05-19T12:00:00Z"
    response = client.post(f"/api/library/worlds/w/reclassifications/{ts}/undo")
    assert response.status_code == 200
    body = response.json()
    assert body["restored_source_id"] == "beatrice"
    assert ("undo", "w", ts, "user") in fake.calls


def test_list_reclassifications_returns_records(client, container) -> None:
    container.library = FakeReclassifyLibrary()
    response = client.get("/api/library/worlds/w/reclassifications")
    assert response.status_code == 200
    body = response.json()
    assert body == [{"ts": "2026-05-19T00:00:00Z", "source_id": "x", "target_kind": "character"}]
