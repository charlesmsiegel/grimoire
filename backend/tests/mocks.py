"""Shared fake/mock classes for route-level contract tests."""

from __future__ import annotations

from typing import Any


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def submit_post(
        self, campaign_id: str, pc_ref: str, text: str, metadata: dict | None = None
    ) -> Any:
        from grimoire.types.orchestrator import SubmitResult

        self.calls.append(("submit", campaign_id, pc_ref, text))
        return SubmitResult(accepted=True, turn_id="t_123", auto_responding=True, reason="ok")

    async def submit_direction(
        self, campaign_id: str, scene_id: str, text: str | None = None
    ) -> Any:
        from grimoire.types.orchestrator import SubmitResult

        self.calls.append(("submit_direction", campaign_id, scene_id, text))
        return SubmitResult(
            accepted=True, turn_id="t_dir_1", auto_responding=True, reason="direction"
        )

    async def undo_turn(self, campaign_id: str, count: int) -> Any:
        from grimoire.types.orchestrator import UndoResult

        return UndoResult(turns_undone=[f"t_{i}" for i in range(count)])

    async def fork_campaign(
        self,
        *,
        campaign_id: str,
        new_campaign_id: str,
        new_name: str,
        fork_at_post_id: str | None = None,
        description: str | None = None,
        make_active: bool = False,
    ) -> Any:
        from datetime import UTC, datetime

        from grimoire.types.orchestrator import ForkCampaignResult

        if new_campaign_id == campaign_id:
            from grimoire.orchestrator.errors import CampaignIdExists

            raise CampaignIdExists(new_campaign_id)

        return ForkCampaignResult(
            new_campaign_id=new_campaign_id,
            new_name=new_name,
            forked_from_campaign_id=campaign_id,
            forked_at_post_id=fork_at_post_id,
            image_handling="hardlink",
            files_copied=0,
            deltas_replayed=0,
            fingerprint_match=True,
            degraded=False,
            queued=False,
            created_at=datetime.now(UTC),
        )

    async def list_pending_forks(self, campaign_id: str) -> list[dict]:
        return []

    async def get_lineage(self, campaign_id: str) -> dict:
        return {
            "root": campaign_id,
            "ancestors": [{"id": campaign_id, "forked_from_campaign_id": None}],
            "descendants": [{"id": campaign_id, "depth": 0}],
        }

    async def get_lineage_ancestors(self, campaign_id: str) -> list[dict]:
        return [{"id": campaign_id, "forked_from_campaign_id": None}]


class FakeContinuity:
    async def facts_about(self, **kwargs: Any) -> list[Any]:
        return []

    async def open_commitments(self, **kwargs: Any) -> list[Any]:
        return []

    async def stale_commitments(self, threshold: Any) -> list[Any]:
        return []

    async def pending_contradictions(self, limit: int = 20) -> list[Any]:
        return []


class FakeCharacters:
    def __init__(self) -> None:
        self.pcs: dict[str, list[dict]] = {}

    async def list_pcs(self, campaign_id: str) -> list[dict]:
        return self.pcs.get(campaign_id, [])

    async def add_pc(
        self,
        campaign_id: str,
        character_ref: str,
        name: str,
        owner: str = "local",
        role_tags: list[str] | None = None,
    ) -> dict:
        entry = {
            "character_ref": character_ref,
            "name": name,
            "owner": owner,
            "active": False,
            "role_tags": role_tags or [],
        }
        self.pcs.setdefault(campaign_id, []).append(entry)
        return entry

    async def remove_pc(self, campaign_id: str, character_ref: str) -> None:
        self.pcs[campaign_id] = [
            p for p in self.pcs.get(campaign_id, []) if p["character_ref"] != character_ref
        ]

    async def set_active_pc(self, campaign_id: str, character_ref: str) -> None:
        for p in self.pcs.get(campaign_id, []):
            p["active"] = p["character_ref"] == character_ref
