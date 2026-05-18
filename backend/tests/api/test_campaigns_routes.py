"""REST contract tests for campaign routes that don't require the full turn loop."""

from __future__ import annotations

from typing import Any, ClassVar


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def submit_post(
        self, campaign_id: str, pc_ref: str, text: str, metadata: dict | None = None
    ) -> Any:
        from grimoire.types.orchestrator import SubmitResult

        self.calls.append(("submit", campaign_id, pc_ref, text))
        return SubmitResult(accepted=True, turn_id="t_123", auto_responding=True, reason="ok")

    async def regenerate_last(self, campaign_id: str) -> Any:
        from grimoire.types.orchestrator import RegenerateResult

        return RegenerateResult(turn_id="t_999", accepted=True, reason="regen")

    async def undo_turn(self, campaign_id: str, count: int) -> Any:
        from grimoire.types.orchestrator import UndoResult

        return UndoResult(turns_undone=[f"t_{i}" for i in range(count)])

    async def fork(self, campaign_id: str, from_turn_id: str, label: str) -> Any:
        from datetime import UTC, datetime

        from grimoire.types.orchestrator import ForkResult

        return ForkResult(
            new_branch_id=f"{campaign_id}:{label}",
            from_turn_id=from_turn_id,
            label=label,
            created_at=datetime.now(UTC),
        )


class FakeContinuity:
    async def facts_about(self, **kwargs: Any) -> list[Any]:
        return []

    async def open_commitments(self, **kwargs: Any) -> list[Any]:
        return []


class FakeCharacters:
    def __init__(self) -> None:
        self.pcs: dict[str, list[dict]] = {}

    async def list_pcs(self, campaign_id: str) -> list[dict]:
        return self.pcs.get(campaign_id, [])

    async def add_pc(self, campaign_id: str, character_ref: str, name: str, owner: str) -> dict:
        entry = {
            "character_ref": character_ref,
            "name": name,
            "owner": owner,
            "active": False,
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


def test_submit_turn_dispatches_to_orchestrator(client, container) -> None:
    fake = FakeOrchestrator()
    container.orchestrator = fake
    response = client.post(
        "/api/campaigns/c1/turns",
        json={"pc_ref": "pc-1", "text": "hi"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["turn_id"] == "t_123"
    assert body["auto_responding"] is True
    assert fake.calls == [("submit", "c1", "pc-1", "hi")]


def test_regenerate(client, container) -> None:
    container.orchestrator = FakeOrchestrator()
    response = client.post("/api/campaigns/c1/turns/regenerate")
    assert response.status_code == 200
    assert response.json()["turn_id"] == "t_999"


def test_undo(client, container) -> None:
    container.orchestrator = FakeOrchestrator()
    response = client.post("/api/campaigns/c1/turns/undo", json={"count": 3})
    assert response.status_code == 200
    assert len(response.json()["turns_undone"]) == 3


def test_fork(client, container) -> None:
    container.orchestrator = FakeOrchestrator()
    response = client.post(
        "/api/campaigns/c1/forks",
        json={"from_turn_id": "t_5", "label": "side-arc"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["new_branch_id"] == "c1:side-arc"


def test_pcs_lifecycle(client, container) -> None:
    container.characters = FakeCharacters()
    response = client.get("/api/campaigns/c1/pcs")
    assert response.status_code == 200
    assert response.json() == []

    response = client.post(
        "/api/campaigns/c1/pcs",
        json={"character_ref": "ref-1", "name": "Alistair", "owner": "tester"},
    )
    assert response.status_code == 201

    response = client.get("/api/campaigns/c1/pcs")
    body = response.json()
    assert body[0]["character_ref"] == "ref-1"

    response = client.post("/api/campaigns/c1/pcs/ref-1/set-active")
    assert response.status_code == 200

    response = client.delete("/api/campaigns/c1/pcs/ref-1")
    assert response.status_code == 204

    response = client.get("/api/campaigns/c1/pcs")
    assert response.json() == []


class FakeRichCharacters(FakeCharacters):
    """Returns PCEntry-shaped dicts with the rich switcher fields."""

    async def list_pcs(self, campaign_id: str) -> list[dict]:
        return [
            {
                "character_ref": "library:worlds/wod-london/characters/aleksandr",
                "name": "Aleksandr",
                "owner": "local",
                "active": True,
                "current_scene_id": "scene-47",
                "current_location_ref": "library:worlds/wod-london/locations/camden-club",
                "last_played_at": "2026-05-18T10:00:00+00:00",
            }
        ]


def test_pcs_payload_includes_rich_switcher_fields(client, container) -> None:
    container.characters = FakeRichCharacters()
    response = client.get("/api/campaigns/c1/pcs")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    pc = body[0]
    assert pc["character_ref"] == "library:worlds/wod-london/characters/aleksandr"
    assert pc["current_scene_id"] == "scene-47"
    assert pc["current_location_ref"] == "library:worlds/wod-london/locations/camden-club"
    assert pc["last_played_at"].startswith("2026-05-18")


def test_facts_and_commitments(client, container) -> None:
    container.continuity = FakeContinuity()
    response = client.get("/api/campaigns/c1/facts")
    assert response.status_code == 200
    assert response.json() == []
    response = client.get("/api/campaigns/c1/commitments")
    assert response.status_code == 200
    assert response.json() == []


def test_orchestrator_503_when_missing(client, container) -> None:
    # Lifespan auto-wires an OrchestratorService; clear it so we can verify
    # the 503 branch in api/deps.py:_require for any service that goes missing.
    container.orchestrator = None
    response = client.post("/api/campaigns/c1/turns", json={"pc_ref": "p", "text": "x"})
    assert response.status_code == 503


class _FakeRow(dict):
    """sqlite-row-like dict that supports both indexing and attribute access."""


class FakeStateStoreForReviews:
    """Just enough surface for review-queue ownership checks."""

    def __init__(self, items: dict[str, str]) -> None:
        # review_id -> campaign_id
        self.items = items
        self.approved: list[str] = []
        self.rejected: list[tuple[str, str]] = []
        self.notes: list[tuple[str, str, str]] = []
        self.db = self  # the route calls state_store.db.fetchone/execute

    async def fetchone(self, sql: str, params: tuple) -> dict | None:
        if "FROM review_queue" in sql and "id = ?" in sql:
            review_id = params[0]
            if review_id not in self.items:
                return None
            return _FakeRow(campaign_id=self.items[review_id])
        return None

    async def execute(self, sql: str, params: tuple) -> None:
        if "UPDATE review_queue" in sql:
            notes, review_id, campaign_id = params
            self.notes.append((review_id, campaign_id, notes))

    async def approve_review_item(self, review_id: str) -> str:
        self.approved.append(review_id)
        return f"delta_{review_id}"

    async def reject_review_item(self, review_id: str, *, notes: str = "") -> None:
        self.rejected.append((review_id, notes))


def test_review_approve_rejects_wrong_campaign(client, container) -> None:
    store = FakeStateStoreForReviews({"r1": "c-real"})
    container.state_store = store
    # The review item exists but belongs to c-real, not c-other.
    response = client.post("/api/campaigns/c-other/reviews/r1/approve")
    assert response.status_code == 404
    assert store.approved == []  # store method must not run


def test_review_approve_succeeds_for_owning_campaign(client, container) -> None:
    store = FakeStateStoreForReviews({"r1": "c-real"})
    container.state_store = store
    response = client.post("/api/campaigns/c-real/reviews/r1/approve")
    assert response.status_code == 200
    assert response.json() == {"delta_id": "delta_r1"}
    assert store.approved == ["r1"]


def test_review_unknown_returns_404(client, container) -> None:
    store = FakeStateStoreForReviews({})
    container.state_store = store
    response = client.post("/api/campaigns/c1/reviews/missing/reject")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Character override + bulk-create-missing-sheets
# --------------------------------------------------------------------------- #


class _FakeChar:
    def __init__(self, char_id: str, world_id: str | None = None) -> None:
        self.id = char_id
        self.world_id = world_id


class _FakeResolved:
    def __init__(self, char_id: str, world_id: str | None = None) -> None:
        self.character = _FakeChar(char_id, world_id)


class FakeCharactersWithOverride(FakeCharacters):
    def __init__(self) -> None:
        super().__init__()
        self.overrides: list[tuple[str, str, dict, str]] = []
        self.resolved: dict[str, list[_FakeResolved]] = {}

    async def list_for_campaign(self, campaign_id: str) -> list[Any]:
        return self.resolved.get(campaign_id, [])

    async def upsert_override(
        self,
        campaign_id: str,
        character_ref: str,
        patch: dict,
        *,
        source: str = "user",
    ) -> None:
        self.overrides.append((campaign_id, character_ref, dict(patch), source))


def test_patch_character_override_writes_override(client, container) -> None:
    fake = FakeCharactersWithOverride()
    fake.resolved["c1"] = [_FakeResolved("alistair", "wod-london")]
    container.characters = fake
    response = client.patch(
        "/api/campaigns/c1/characters/alistair/override",
        json={"override": {"name": "New Name"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ref"] == "library:worlds/wod-london/characters/alistair"
    assert fake.overrides == [
        ("c1", "library:worlds/wod-london/characters/alistair", {"name": "New Name"}, "user"),
    ]


def test_patch_character_override_accepts_explicit_world(client, container) -> None:
    fake = FakeCharactersWithOverride()
    # No resolved cast — server must trust the body's world_id.
    container.characters = fake
    response = client.patch(
        "/api/campaigns/c1/characters/alistair/override",
        json={"override": {"name": "X"}, "world_id": "wod-rome"},
    )
    assert response.status_code == 200
    assert fake.overrides[0][1] == "library:worlds/wod-rome/characters/alistair"


def test_patch_character_override_404_when_world_unresolvable(client, container) -> None:
    container.characters = FakeCharactersWithOverride()
    response = client.patch(
        "/api/campaigns/c1/characters/who/override",
        json={"override": {}},
    )
    assert response.status_code == 404


class _BulkRow(dict):
    pass


class FakeStateStoreForBulk:
    def __init__(
        self,
        *,
        mechanics_module: str | None,
        existing: set[tuple[str, str]] | None = None,
    ) -> None:
        self._mechanics = mechanics_module
        self.existing = existing or set()
        self.writes: list[tuple[str, str, str, dict]] = []
        self.db = self

    async def fetchone(self, sql: str, params: tuple) -> dict | None:
        if "FROM campaigns WHERE id = ?" in sql:
            cid = params[0]
            if cid != "c1":
                return None
            return _BulkRow(mechanics_module=self._mechanics)
        return None

    async def get_sheet(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        mechanics_id: str,
    ) -> dict | None:
        if (kind, entity_id) in self.existing:
            return {"_existing": True}
        return None

    async def write_sheet(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        mechanics_id: str,
        sheet: dict,
        source: str,
        turn_id: str | None = None,
    ) -> str:
        self.writes.append((kind, entity_id, mechanics_id, sheet))
        return "ok"


class _FakeModule:
    sheet_kinds: ClassVar[list[str]] = ["character"]

    def initialize_sheet(self, kind: str, entity_id: str) -> dict:
        return {"kind": kind, "entity_id": entity_id, "initialized": True}


class FakeMechanicsForBulk:
    def __init__(self, *, module: Any = None) -> None:
        self._module = module

    def get_module(self, module_id: str) -> Any:
        return self._module

    async def module_info(self, module_id: str) -> Any:
        return None


def test_bulk_create_missing_sheets_creates_and_skips(client, container) -> None:
    state_store = FakeStateStoreForBulk(
        mechanics_module="vamp",
        existing={("character", "alistair")},
    )
    chars = FakeCharactersWithOverride()
    chars.resolved["c1"] = [
        _FakeResolved("alistair", "wod-london"),
        _FakeResolved("dorian", "wod-london"),
    ]
    container.state_store = state_store
    container.characters = chars
    container.mechanics = FakeMechanicsForBulk(module=_FakeModule())

    class FakeWorld:
        async def list_for_campaign(self, campaign_id: str, kind: str) -> list[Any]:
            return []

    container.world = FakeWorld()

    response = client.post("/api/campaigns/c1/sheets/bulk-create-missing")
    assert response.status_code == 200
    body = response.json()
    assert body["skipped"] == [{"kind": "character", "entity_id": "alistair"}]
    assert body["created"] == [{"kind": "character", "entity_id": "dorian"}]
    assert state_store.writes == [
        (
            "character",
            "dorian",
            "vamp",
            {"kind": "character", "entity_id": "dorian", "initialized": True},
        )
    ]


def test_bulk_create_missing_sheets_409_when_no_mechanics(client, container) -> None:
    container.state_store = FakeStateStoreForBulk(mechanics_module=None)
    container.characters = FakeCharactersWithOverride()
    container.mechanics = FakeMechanicsForBulk()

    class FakeWorld:
        async def list_for_campaign(self, campaign_id: str, kind: str) -> list[Any]:
            return []

    container.world = FakeWorld()
    response = client.post("/api/campaigns/c1/sheets/bulk-create-missing")
    assert response.status_code == 409


def test_bulk_create_missing_sheets_404_for_unknown_campaign(client, container) -> None:
    container.state_store = FakeStateStoreForBulk(mechanics_module=None)
    container.characters = FakeCharactersWithOverride()
    container.mechanics = FakeMechanicsForBulk()

    class FakeWorld:
        async def list_for_campaign(self, campaign_id: str, kind: str) -> list[Any]:
            return []

    container.world = FakeWorld()
    response = client.post("/api/campaigns/c-other/sheets/bulk-create-missing")
    assert response.status_code == 404
