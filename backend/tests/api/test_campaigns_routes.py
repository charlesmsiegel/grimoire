"""REST contract tests for campaign routes that don't require the full turn loop."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from grimoire.state_store.errors import ConflictError, NotFoundError
from grimoire.types.mechanics import BulkSheetCreateResult, SheetRef
from tests.api.conftest import _FakeAttr
from tests.mocks import FakeCharacters, FakeContinuity, FakeOrchestrator


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


def test_undo(client, container) -> None:
    container.orchestrator = FakeOrchestrator()
    response = client.post("/api/campaigns/c1/turns/undo", json={"count": 3})
    assert response.status_code == 200
    assert len(response.json()["turns_undone"]) == 3


def test_submit_direction_dispatches_to_orchestrator(client, container) -> None:
    fake = FakeOrchestrator()
    container.orchestrator = fake
    response = client.post(
        "/api/campaigns/c1/turns/direct",
        json={"scene_id": "s1", "text": "winifred confronts Drake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["turn_id"] == "t_dir_1"
    assert body["auto_responding"] is True
    assert fake.calls == [("submit_direction", "c1", "s1", "winifred confronts Drake")]


def test_submit_direction_with_no_text(client, container) -> None:
    fake = FakeOrchestrator()
    container.orchestrator = fake
    response = client.post(
        "/api/campaigns/c1/turns/direct",
        json={"scene_id": "s1"},
    )
    assert response.status_code == 200
    assert fake.calls == [("submit_direction", "c1", "s1", None)]


def test_fork_campaign_route(client, container) -> None:
    container.orchestrator = FakeOrchestrator()
    response = client.post(
        "/api/campaigns/c1/forks",
        json={"new_campaign_id": "c1-divergent", "new_name": "Divergent"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["new_campaign_id"] == "c1-divergent"
    assert body["forked_from_campaign_id"] == "c1"
    assert body["queued"] is False


def test_fork_campaign_id_collision_returns_409(client, container) -> None:
    container.orchestrator = FakeOrchestrator()
    response = client.post(
        "/api/campaigns/c1/forks",
        json={"new_campaign_id": "c1", "new_name": "dup"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "CAMPAIGN_ID_EXISTS"


def test_lineage_routes(client, container) -> None:
    container.orchestrator = FakeOrchestrator()
    response = client.get("/api/campaigns/c1/lineage")
    assert response.status_code == 200
    body = response.json()
    assert body["root"] == "c1"
    assert isinstance(body["descendants"], list)

    response = client.get("/api/campaigns/c1/lineage/ancestors")
    assert response.status_code == 200
    assert response.json()[0]["id"] == "c1"


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


def test_continuity_ledger_returns_all_sections(client, container) -> None:
    container.continuity = FakeContinuity()
    response = client.get("/api/campaigns/c1/continuity/ledger")
    assert response.status_code == 200
    body = response.json()
    assert body["campaign_id"] == "c1"
    assert body["open_commitments"] == []
    assert body["overdue_commitments"] == []
    assert body["stale_commitments"] == []
    assert body["recent_facts"] == []
    assert body["unresolved_contradictions"] == []


def test_continuity_ledger_splits_open_and_overdue(client, container) -> None:
    """An overdue-status commitment lands in overdue_commitments, others in open."""
    from grimoire.continuity import (
        Commitment,
        CommitmentKind,
        CommitmentStatus,
        InGameTime,
    )
    from tests.continuity.conftest import make_fact

    def _commit(id_: str, status: CommitmentStatus, text: str) -> Commitment:
        return Commitment(
            id=id_,
            kind=CommitmentKind.PROMISE,
            text=text,
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
            status=status,
        )

    class FakeContinuityWithRows(FakeContinuity):
        async def open_commitments(self, **kwargs: Any) -> list[Any]:
            return [
                _commit("c1", CommitmentStatus.OPEN, "alpha"),
                _commit("c2", CommitmentStatus.OVERDUE, "beta"),
            ]

        async def facts_about(self, **kwargs: Any) -> list[Any]:
            return [make_fact(fact_id="f1", text="fact one")]

    container.continuity = FakeContinuityWithRows()
    response = client.get("/api/campaigns/c1/continuity/ledger")
    assert response.status_code == 200
    body = response.json()
    open_ids = {c["id"] for c in body["open_commitments"]}
    overdue_ids = {c["id"] for c in body["overdue_commitments"]}
    assert open_ids == {"c1"}
    assert overdue_ids == {"c2"}
    assert len(body["recent_facts"]) == 1


def test_continuity_ledger_via_registry(client, container) -> None:
    """When container.continuity is a registry, the route resolves per-campaign."""

    class _Registry:
        def __init__(self) -> None:
            self.requested_for: list[str] = []
            self._service = FakeContinuity()

        def for_campaign(self, campaign_id: str) -> Any:
            self.requested_for.append(campaign_id)
            return self._service

    registry = _Registry()
    container.continuity = registry
    response = client.get("/api/campaigns/c1/continuity/ledger")
    assert response.status_code == 200
    assert "c1" in registry.requested_for


def test_list_contradictions_route_passes_resolved_filter(client, container) -> None:
    class _Store:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def list_contradiction_reports(self, *, resolved: bool | None, limit: int):
            self.kwargs = {"resolved": resolved, "limit": limit}
            return []

    class _Service(FakeContinuity):
        def __init__(self) -> None:
            self._store = _Store()

    service = _Service()
    container.continuity = service
    response = client.get("/api/campaigns/c1/continuity/contradictions?resolved=false&limit=5")
    assert response.status_code == 200
    assert service._store.kwargs == {"resolved": False, "limit": 5}


class FakeOrchestratorWithSceneBreak:
    """Records ``resolve_scene_break`` calls and replays a scripted result."""

    def __init__(self, *, result: bool = True) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    async def resolve_scene_break(self, campaign_id: str, turn_id: str, choice: str) -> bool:
        self.calls.append((campaign_id, turn_id, choice))
        return self.result


def test_resolve_scene_break_dispatches_to_orchestrator(client, container) -> None:
    fake = FakeOrchestratorWithSceneBreak(result=True)
    container.orchestrator = fake
    response = client.post(
        "/api/campaigns/c1/turns/t_42/resolve-scene-break",
        json={"choice": "new_scene"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"resolved": True, "turn_id": "t_42", "choice": "new_scene"}
    assert fake.calls == [("c1", "t_42", "new_scene")]


def test_resolve_scene_break_404_when_not_pending(client, container) -> None:
    container.orchestrator = FakeOrchestratorWithSceneBreak(result=False)
    response = client.post(
        "/api/campaigns/c1/turns/t_99/resolve-scene-break",
        json={"choice": "continue"},
    )
    assert response.status_code == 404
    assert "t_99" in response.json()["detail"]


def test_resolve_scene_break_422_on_invalid_choice(client, container) -> None:
    fake = FakeOrchestratorWithSceneBreak()
    container.orchestrator = fake
    response = client.post(
        "/api/campaigns/c1/turns/t_42/resolve-scene-break",
        json={"choice": "skip"},
    )
    assert response.status_code == 422
    # Orchestrator must not be called when the choice is rejected at the edge.
    assert fake.calls == []


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


# The orchestration itself (created/skipped/writes, listing, sheet kinds) is
# unit-tested in tests/mechanics/test_service_bulk_sheets.py. These route tests
# verify the endpoint is a thin pass-through that forwards the cast/world
# services and maps domain errors to HTTP status codes.


class FakeMechanicsForBulk:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def bulk_create_missing_sheets(
        self, campaign_id: str, *, characters: Any, world: Any, source: str = "api"
    ) -> Any:
        self.calls.append({"campaign_id": campaign_id, "characters": characters, "world": world})
        if self._error is not None:
            raise self._error
        return self._result


def test_bulk_create_missing_sheets_delegates_and_returns_payload(client, container) -> None:
    result = BulkSheetCreateResult(
        created=[SheetRef(kind="character", entity_id="dorian")],
        skipped=[SheetRef(kind="character", entity_id="alistair")],
    )
    fake = FakeMechanicsForBulk(result=result)
    container.mechanics = fake
    container.characters = FakeCharactersWithOverride()
    container.world = _FakeAttr()

    response = client.post("/api/campaigns/c1/sheets/bulk-create-missing")
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == [{"kind": "character", "entity_id": "dorian"}]
    assert body["skipped"] == [{"kind": "character", "entity_id": "alistair"}]
    # The endpoint forwards the owning services to the mechanics method.
    assert fake.calls[0]["campaign_id"] == "c1"
    assert fake.calls[0]["characters"] is container.characters
    assert fake.calls[0]["world"] is container.world


def test_bulk_create_missing_sheets_409_when_no_mechanics(client, container) -> None:
    container.mechanics = FakeMechanicsForBulk(
        error=ConflictError("campaign 'c1' has no mechanics module bound")
    )
    container.characters = FakeCharactersWithOverride()
    container.world = _FakeAttr()
    response = client.post("/api/campaigns/c1/sheets/bulk-create-missing")
    assert response.status_code == 409


def test_bulk_create_missing_sheets_404_for_unknown_campaign(client, container) -> None:
    container.mechanics = FakeMechanicsForBulk(error=NotFoundError("campaign 'c-other' not found"))
    container.characters = FakeCharactersWithOverride()
    container.world = _FakeAttr()
    response = client.post("/api/campaigns/c-other/sheets/bulk-create-missing")
    assert response.status_code == 404


# World-view list endpoints ------------------------------------------- #


def test_world_view_list_returns_resolved_entity_shape(client, container) -> None:
    """World-view list endpoints must emit ResolvedEntity-shaped payloads.

    Regression: ``_list_kind`` returned raw ``LibraryEntity`` rows with no
    ``source_chain``. The World view's ``ChainBadge`` then read ``chain[0]`` on
    ``undefined`` and white-screened the whole app (blank page + a dangling
    stream WebSocket reported as "Connection closed").
    """
    from grimoire.types.common import EntityKind
    from grimoire.types.composition import (
        ResolutionLayer,
        ResolutionSource,
        ResolvedEntity,
    )

    ent = ResolvedEntity(
        kind=EntityKind.ITEM,
        asset_id="amulet",
        world_id="wod-london",
        name="Amulet",
        frontmatter={"id": "amulet", "name": "Amulet"},
        body="An old amulet.",
        source_chain=[
            ResolutionSource(
                layer=ResolutionLayer.LIBRARY_LIVE,
                scope="library",
                world_id="wod-london",
                version=3,
            )
        ],
    )

    class FakeWorld:
        async def list_resolved_for_campaign(self, campaign_id: str, kind: str) -> list[Any]:
            assert kind == "item"
            return [ent]

    container.world = FakeWorld()

    response = client.get("/api/campaigns/c1/items")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["asset_id"] == "amulet"
    assert row["name"] == "Amulet"
    assert row["world_id"] == "wod-london"
    # Contract fields the frontend ResolvedEntity / ChainBadge depend on.
    assert row["source_chain"], "source_chain must be populated so ChainBadge renders"
    assert row["source_chain"][0]["world_id"] == "wod-london"
    assert row["overrides_applied"] == []
    assert "extras" in row


# Cascade-resolved list + override endpoints (#600) -------------------- #


_CASCADE_KIND_CASES = [
    ("items", "item"),
    ("locations", "location"),
    ("lore", "lore"),
    ("factions", "faction"),
    ("monsters", "monster"),
    ("greetings", "greeting"),
]


def _seed_cascade_campaign(client, container, *, kind: str, kind_dir: str) -> None:
    """Seed the lifespan-built real services with one row per cascade layer.

    ``c1`` composes world ``w1`` carrying ``plain`` (untouched) and ``tweaked``
    (campaign override applied); ``spawned`` exists only as campaign-local
    emergent content. Async store calls cross into the app's loop via
    ``client.portal``.
    """
    from functools import partial

    store = container.state_store
    call = client.portal.call
    call(partial(store.upsert_campaign, campaign_id="c1", name="C1"))
    for asset_id, name in (("plain", "Plain"), ("tweaked", "Tweaked")):
        call(
            partial(
                store.write_library_file,
                library_id=f"worlds/w1/{kind_dir}/{asset_id}",
                frontmatter={"id": asset_id, "name": name},
                body=f"{name} body",
                source="test",
            )
        )
    call(
        partial(
            store.upsert_world_ref,
            campaign_id="c1",
            world_id="w1",
            priority=1,
            include=None,
            track_latest=True,
        )
    )
    call(
        partial(
            store.write_override,
            campaign_id="c1",
            library_id=f"worlds/w1/{kind_dir}/tweaked",
            patch={"name": "Tweaked (Override)"},
            source="test",
        )
    )
    call(
        partial(
            store.write_emergent,
            campaign_id="c1",
            kind=kind,
            entity_id="spawned",
            frontmatter={"id": "spawned", "name": "Spawned"},
            body="emergent body",
            source="extractor",
        )
    )


@pytest.mark.parametrize(("kind_dir", "kind"), _CASCADE_KIND_CASES)
def test_list_kind_returns_cascade_resolved_rows(client, container, kind_dir, kind) -> None:
    """Route contract (#600): every kind list runs the read cascade — a plain
    library row, an overridden row, and an emergent row, each with a truthful
    ``source_chain``."""
    _seed_cascade_campaign(client, container, kind=kind, kind_dir=kind_dir)

    response = client.get(f"/api/campaigns/c1/{kind_dir}")
    assert response.status_code == 200
    rows = {row["asset_id"]: row for row in response.json()}
    assert set(rows) == {"plain", "tweaked", "spawned"}

    plain = rows["plain"]
    assert plain["world_id"] == "w1"
    assert plain["source_chain"][0]["layer"] == "library_live"
    assert plain["overrides_applied"] == []

    tweaked = rows["tweaked"]
    assert tweaked["name"] == "Tweaked (Override)"
    assert tweaked["source_chain"][0]["layer"] == "override"
    assert tweaked["overrides_applied"]

    spawned = rows["spawned"]
    assert spawned["world_id"] is None
    assert spawned["source_chain"][0]["layer"] == "emergent"
    assert spawned["source_chain"][0]["scope"] == "campaign-local"


def test_patch_entity_override_writes_and_lists(client, container) -> None:
    """PATCH …/{kind}/{id}/override resolves the owning world from the
    composition and the next list shows the overridden row (#600)."""
    _seed_cascade_campaign(client, container, kind="item", kind_dir="items")

    response = client.patch(
        "/api/campaigns/c1/items/plain/override",
        json={"override": {"name": "Plain (Renamed)"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["world_id"] == "w1"
    assert body["ref"] == "library:worlds/w1/items/plain"

    rows = {row["asset_id"]: row for row in client.get("/api/campaigns/c1/items").json()}
    assert rows["plain"]["name"] == "Plain (Renamed)"
    assert rows["plain"]["source_chain"][0]["layer"] == "override"


def test_patch_entity_override_merges_with_existing(client, container) -> None:
    """Two override PATCHes accumulate: the second keeps the first's keys."""
    _seed_cascade_campaign(client, container, kind="item", kind_dir="items")

    first = client.patch(
        "/api/campaigns/c1/items/plain/override",
        json={"override": {"name": "Plain (Renamed)"}},
    )
    assert first.status_code == 200
    second = client.patch(
        "/api/campaigns/c1/items/plain/override",
        json={"override": {"provenance": "found in the bone orchard"}},
    )
    assert second.status_code == 200

    rows = {row["asset_id"]: row for row in client.get("/api/campaigns/c1/items").json()}
    assert rows["plain"]["name"] == "Plain (Renamed)"
    assert rows["plain"]["frontmatter"]["provenance"] == "found in the bone orchard"


def test_patch_entity_override_409_for_emergent_entity(client, container) -> None:
    """Emergent rows are campaign-local SSOT: overriding them is rejected both
    with and without an explicit world_id (the cascade resolves emergent first,
    so the override could never surface)."""
    _seed_cascade_campaign(client, container, kind="item", kind_dir="items")

    implicit = client.patch(
        "/api/campaigns/c1/items/spawned/override",
        json={"override": {"name": "X"}},
    )
    assert implicit.status_code == 409

    explicit = client.patch(
        "/api/campaigns/c1/items/spawned/override",
        json={"override": {"name": "X"}, "world_id": "w1"},
    )
    assert explicit.status_code == 409


def test_patch_entity_override_accepts_explicit_world(client, container) -> None:
    """An explicit world_id reaches library entities outside the composition —
    but only ones that actually exist (no orphan override for a typo'd id)."""
    from functools import partial

    store = container.state_store
    client.portal.call(partial(store.upsert_campaign, campaign_id="c1", name="C1"))
    client.portal.call(
        partial(
            store.write_library_file,
            library_id="worlds/w9/factions/camarilla",
            frontmatter={"id": "camarilla", "name": "Camarilla"},
            body="",
            source="test",
        )
    )
    response = client.patch(
        "/api/campaigns/c1/factions/camarilla/override",
        json={"override": {"name": "X"}, "world_id": "w9"},
    )
    assert response.status_code == 200
    assert response.json()["ref"] == "library:worlds/w9/factions/camarilla"

    missing = client.patch(
        "/api/campaigns/c1/factions/ghost/override",
        json={"override": {"name": "X"}, "world_id": "w9"},
    )
    assert missing.status_code == 404


def test_patch_entity_override_404_when_world_unresolvable(client, container) -> None:
    from functools import partial

    client.portal.call(partial(container.state_store.upsert_campaign, campaign_id="c1", name="C1"))
    response = client.patch(
        "/api/campaigns/c1/items/ghost/override",
        json={"override": {}},
    )
    assert response.status_code == 404


def test_patch_entity_override_404_for_unknown_kind(client, container) -> None:
    response = client.patch(
        "/api/campaigns/c1/pcs/p1/override",
        json={"override": {}},
    )
    assert response.status_code == 404


# Export routes -------------------------------------------------------- #


class FakeExportService:
    """Just enough of the ExportService surface for HTTP-route tests."""

    def __init__(self) -> None:
        self.history_records: dict[str, list] = {}
        self.preview_calls: list[tuple] = []

    def list_adapters(self) -> list:
        from grimoire.types.export import ExportCapabilities

        class _Adapter:
            id: ClassVar[str] = "epub"
            name: ClassVar[str] = "EPUB 3"
            extensions: ClassVar[list[str]] = ["epub"]
            mime_type: ClassVar[str] = "application/epub+zip"
            capabilities: ClassVar[ExportCapabilities] = ExportCapabilities(
                supports_appendices=True
            )

            def option_schema(self) -> dict:
                return {"type": "object"}

        return [_Adapter()]

    async def preview(self, campaign_id, adapter_id, selection, options) -> Any:
        from grimoire.types.export import ExportPreview

        self.preview_calls.append((campaign_id, adapter_id))
        return ExportPreview(
            adapter_id=adapter_id,
            scene_count=2,
            word_count=200,
            image_count=0,
            estimated_size_bytes=8192,
        )

    async def history(self, campaign_id: str) -> list:
        return list(self.history_records.get(campaign_id, []))


def test_list_export_adapters_returns_capabilities(client, container) -> None:
    container.export = FakeExportService()
    response = client.get("/api/campaigns/c1/exports/adapters")
    assert response.status_code == 200
    payload = response.json()
    assert payload["adapters"][0]["id"] == "epub"
    assert payload["adapters"][0]["capabilities"]["supports_appendices"] is True
    assert payload["adapters"][0]["option_schema"] == {"type": "object"}


def test_preview_export_returns_preview(client, container) -> None:
    fake = FakeExportService()
    container.export = fake
    response = client.post(
        "/api/campaigns/c1/exports/preview",
        json={
            "adapter_id": "epub",
            "selection": {},
            "options": {"title": "Probe"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["adapter_id"] == "epub"
    assert body["scene_count"] == 2
    assert fake.preview_calls == [("c1", "epub")]


def test_list_export_history_paginates(client, container) -> None:
    from datetime import UTC, datetime

    from grimoire.types.export import (
        ExportOptions,
        ExportRecord,
        ExportResult,
        ExportSelection,
    )

    fake = FakeExportService()
    fake.history_records["c1"] = [
        ExportRecord(
            id=f"e{i}",
            campaign_id="c1",
            adapter_id="epub",
            selection=ExportSelection(),
            options=ExportOptions(title=f"T{i}"),
            result=ExportResult(format="epub", size_bytes=10),
            created_at=datetime.now(UTC),
        )
        for i in range(3)
    ]
    container.export = fake

    response = client.get("/api/campaigns/c1/exports")
    assert response.status_code == 200
    assert len(response.json()["records"]) == 3

    response = client.get("/api/campaigns/c1/exports?limit=2")
    assert response.status_code == 200
    records = response.json()["records"]
    assert len(records) == 2
    assert records[-1]["options"]["title"] == "T2"

    # limit=0 must return zero records, not all of them (Python's -0 == 0).
    response = client.get("/api/campaigns/c1/exports?limit=0")
    assert response.status_code == 200
    assert response.json()["records"] == []


class _FakeFileWatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def scan_now(self, *, scope: str = "all") -> dict[str, Any]:
        self.calls.append(scope)
        return {"scope": scope, "library_files": 0, "campaign_files": 7, "failures": 0}


def test_rescan_campaigns_invokes_file_watcher_with_campaigns_scope(client, container) -> None:
    fw = _FakeFileWatcher()
    container.file_watcher = fw
    response = client.post("/api/campaigns/rescan")
    assert response.status_code == 200
    assert response.json() == {
        "scope": "campaigns",
        "library_files": 0,
        "campaign_files": 7,
        "failures": 0,
    }
    assert fw.calls == ["campaigns"]


def test_rescan_campaigns_returns_503_when_watcher_not_configured(client, container) -> None:
    container.file_watcher = None
    response = client.post("/api/campaigns/rescan")
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Scene ledger greeting backfill (issue #472)
# ---------------------------------------------------------------------------


class _FakeLedger:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self._n = 0

    async def list_all(self, campaign_id: str) -> list[dict[str, Any]]:
        return [i for i in self.items if i["campaign_id"] == campaign_id]

    async def list_active(self, campaign_id: str) -> list[dict[str, Any]]:
        return [
            i for i in self.items if i["campaign_id"] == campaign_id and i["status"] == "active"
        ]

    async def add(self, *, campaign_id, summary, source, greeting_id=None, **kw) -> str:
        self._n += 1
        item_id = f"ledger-{self._n}"
        self.items.append(
            {
                "id": item_id,
                "campaign_id": campaign_id,
                "summary": summary,
                "source": source,
                "greeting_id": greeting_id,
                "status": "active",
            }
        )
        return item_id


class _FakeScenesNoScenes:
    async def list_scenes(self, campaign_id: str) -> list[Any]:
        return []


class _FakeLibraryWithGreetings:
    def __init__(self, greetings: list[Any]) -> None:
        self._greetings = greetings

    async def get_composition(self, campaign_id: str) -> Any:
        return _FakeAttr(worlds=[_FakeAttr(world_id="w1")])

    async def list_greetings(self, world_id: str) -> list[Any]:
        return self._greetings


class _FakeStateStorePCs:
    def __init__(self, pc_role_tags: list[list[str]]) -> None:
        import json

        self._rows = [{"role_tags": json.dumps(tags)} for tags in pc_role_tags]

    async def list_pcs(self, campaign_id: str) -> list[dict[str, Any]]:
        return self._rows


def _greeting(gid: str, *, role_tags: list[str] | None = None) -> Any:
    return _FakeAttr(
        id=gid,
        name=gid.title(),
        body="",
        role_tags=role_tags or [],
        starting_location="Harbor",
    )


def test_backfill_scene_ledger_populates_applicable_greetings(client, container) -> None:
    container.scene_ledger = _FakeLedger()
    container.scenes = _FakeScenesNoScenes()
    container.library = _FakeLibraryWithGreetings(
        [
            _greeting("gr-universal"),
            _greeting("gr-hero", role_tags=["hero"]),
            _greeting("gr-villain", role_tags=["villain"]),
        ]
    )
    container.state_store = _FakeStateStorePCs([["hero"]])

    resp = client.post("/api/campaigns/c1/scene-ledger/backfill")
    assert resp.status_code == 200
    assert resp.json() == {"added": 2}

    listed = client.get("/api/campaigns/c1/scene-ledger?status=active").json()
    assert {i["greeting_id"] for i in listed} == {"gr-universal", "gr-hero"}

    # Idempotent: a second backfill adds nothing.
    resp2 = client.post("/api/campaigns/c1/scene-ledger/backfill")
    assert resp2.json() == {"added": 0}


def test_suggest_lazily_backfills_empty_ledger(client, container) -> None:
    from grimoire.types.llm import CompletionResponse, TokenUsage

    container.scene_ledger = _FakeLedger()
    container.scenes = _FakeScenesNoScenes()
    container.library = _FakeLibraryWithGreetings([_greeting("gr-universal")])
    container.state_store = _FakeStateStorePCs([["hero"]])
    container.continuity = FakeContinuity()

    class _FakeGateway:
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            return CompletionResponse(
                text="[]", model="t", finish_reason="stop", usage=TokenUsage()
            )

    container.llm_gateway = _FakeGateway()

    resp = client.post("/api/campaigns/c1/scenes/suggest")
    assert resp.status_code == 200
    # The empty ledger was backfilled from the applicable greeting.
    listed = client.get("/api/campaigns/c1/scene-ledger?status=active").json()
    assert {i["greeting_id"] for i in listed} == {"gr-universal"}


# ---------------------------------------------------------------------------
# Cast view (dramatis personae): GET /api/campaigns/{id}/cast
# ---------------------------------------------------------------------------


def _resolved_character(char_id: str, world_id: str | None):
    from grimoire.types.characters import Character, CharacterRole, ResolvedCharacter
    from grimoire.types.state import CharacterState

    ref = (
        f"library:worlds/{world_id}/characters/{char_id}"
        if world_id
        else f"campaign:emergent/character/{char_id}"
    )
    return ResolvedCharacter(
        character=Character(
            id=char_id, name=char_id.title(), role=CharacterRole.MAJOR_NPC, world_id=world_id
        ),
        current_state=CharacterState(character_ref=ref, campaign_id="c1"),
    )


def _scene(scene_id: str, **kwargs) -> object:
    from grimoire.scenes.types import Scene

    return Scene(id=scene_id, campaign_id="c1", ordinal=1, slug=scene_id, title=scene_id, **kwargs)


def test_cast_filters_to_pcs_emergent_and_appeared(client, container) -> None:
    from tests.mocks import FakeScenes

    chars = FakeCharacters()
    chars.resolved["c1"] = [
        _resolved_character("alice", "w1"),  # PC (registered via shorthand spelling)
        _resolved_character("bram", "w1"),  # appeared in a scene
        _resolved_character("celia", "w1"),  # never appeared → excluded
        _resolved_character("ghost", None),  # emergent → always included
    ]
    chars.pcs["c1"] = [
        {"character_ref": "w1/alice", "name": "Alice", "owner": "local", "active": True}
    ]
    scenes = FakeScenes()
    scenes.scenes["c1"] = [
        _scene("s1", present_character_refs=["library:worlds/w1/characters/bram"])
    ]
    container.characters = chars
    container.scenes = scenes

    resp = client.get("/api/campaigns/c1/cast")
    assert resp.status_code == 200
    assert [row["character"]["id"] for row in resp.json()] == ["alice", "bram", "ghost"]


def test_cast_counts_declared_refs_and_shorthand_spellings(client, container) -> None:
    from tests.mocks import FakeScenes

    chars = FakeCharacters()
    chars.resolved["c1"] = [
        _resolved_character("celia", "w1"),
        _resolved_character("dora", "w1"),
    ]
    scenes = FakeScenes()
    # Declared-at-creation cast counts as appearing, and shorthand spellings
    # normalize to the canonical ref.
    scenes.scenes["c1"] = [_scene("s1", declared_character_refs=["w1/celia"])]
    container.characters = chars
    container.scenes = scenes

    resp = client.get("/api/campaigns/c1/cast")
    assert resp.status_code == 200
    assert [row["character"]["id"] for row in resp.json()] == ["celia"]


def test_cast_with_no_scenes_keeps_pcs_and_emergent_only(client, container) -> None:
    from tests.mocks import FakeScenes

    chars = FakeCharacters()
    chars.resolved["c1"] = [
        _resolved_character("alice", "w1"),
        _resolved_character("bram", "w1"),
        _resolved_character("ghost", None),
    ]
    chars.pcs["c1"] = [
        {
            "character_ref": "library:worlds/w1/characters/alice",
            "name": "Alice",
            "owner": "local",
            "active": True,
        }
    ]
    container.characters = chars
    container.scenes = FakeScenes()

    resp = client.get("/api/campaigns/c1/cast")
    assert resp.status_code == 200
    assert [row["character"]["id"] for row in resp.json()] == ["alice", "ghost"]


def test_cast_keeps_characters_that_entered_then_left(client, container) -> None:
    """remove_present_character strips the sidecar membership fields, so a
    departed character's only surviving evidence is the confirmed cast-change
    log — appearance is historical (#581 review)."""
    from tests.api.conftest import _FakeAttr
    from tests.mocks import FakeScenes

    chars = FakeCharacters()
    chars.resolved["c1"] = [
        _resolved_character("bram", "w1"),  # entered scene s1, later left
        _resolved_character("celia", "w1"),  # never appeared
    ]
    scenes = FakeScenes()
    scenes.scenes["c1"] = [_scene("s1")]  # membership fields already cleared
    scenes.confirmed_cast_changes["s1"] = [_FakeAttr(character_ref="w1/bram")]
    container.characters = chars
    container.scenes = scenes

    resp = client.get("/api/campaigns/c1/cast")
    assert resp.status_code == 200
    assert [row["character"]["id"] for row in resp.json()] == ["bram"]
