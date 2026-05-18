"""Concrete World service (spec 09).

The World module is a behavior + storage layer for everything *inside* a
world: items, locations, lore, factions, greetings. CRUD lives in
``LibraryService`` (file-mediated writes through ``StateStore``); this
service adds the per-campaign behaviors the Library on its own doesn't
provide:

* Composition-aware listing with ``include`` filters
* Spatial queries (adjacency, ``path_between``, ``locations_within``)
* Cross-world variant lookup
* Lore keyword triggers for archive-tier injection
* Procedural weather + override
* Calendar / season / holiday queries
* Faction state CRUD (campaign-scoped, SQLite-backed)
* World fork (directory copy)
* Promotion of campaign-local entities into the library

Character behaviors live in ``08-characters.md`` and layer on top.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.library import LibraryService
from grimoire.library.errors import LibraryNotFoundError
from grimoire.state_store import StateStore
from grimoire.state_store.errors import InvalidRefError
from grimoire.state_store.paths import KIND_TO_DIR, library_root, validate_path_component
from grimoire.types.common import CampaignId, EntityKind, InGameTime
from grimoire.types.composition import (
    Greeting,
    LibraryEntity,
    ResolvedEntity,
    UpgradeReport,
    WorldMeta,
)
from grimoire.types.world import (
    Faction,
    FactionGoal,
    FactionStateData,
    Holiday,
    Item,
    Location,
    LocationConnection,
    LocationKind,
    LoreEntry,
    Season,
    Weather,
    WorldCalendar,
)

from .calendar import holiday_at, parse_calendar, season_for
from .config import WorldConfig
from .errors import CompositionError, WorldError, WorldNotFoundError
from .weather import generate_weather

logger = logging.getLogger(__name__)

# World-internal entity kinds World owns CRUD for.
_OWNED_KINDS: frozenset[str] = frozenset({"item", "location", "lore", "faction", "greeting"})


def _normalize_kind(kind: EntityKind | str) -> str:
    """Accept enums and plural directory names."""
    if isinstance(kind, EntityKind):
        return kind.value
    if kind in {"items", "locations", "lore", "factions", "greetings", "characters"}:
        return {
            "items": "item",
            "locations": "location",
            "lore": "lore",
            "factions": "faction",
            "greetings": "greeting",
            "characters": "character",
        }[kind]
    return kind


class WorldService:
    """Spec 09 implementation.

    Construct with an initialized :class:`LibraryService` (which already
    holds a :class:`StateStore`). The service is otherwise stateless;
    per-campaign behaviors derive from inputs.
    """

    def __init__(
        self,
        library: LibraryService,
        *,
        config: WorldConfig | None = None,
    ) -> None:
        self.library = library
        self.store: StateStore = library.store
        self.config: WorldConfig = config or WorldConfig()

    def _safe_world_dir(self, world_id: str) -> Path:
        """Resolve ``data/library/worlds/<world_id>`` after path-safety checks.

        Why: ``delete_world`` and ``fork_world`` hand the result to
        ``shutil.rmtree`` / ``shutil.copytree``. Without validation, an id
        like ``"../../something"`` would let the OS normalise the ``..``
        segments and escape the worlds root, silently deleting or
        clobbering arbitrary directories.

        The allowlist regex rejects path separators and any leading
        non-alphanumeric (so ``..`` is impossible); the resolved-subpath
        assertion is belt-and-braces in case a future code path introduces
        a new escape vector (symlinks, drive letters, etc.).
        """
        validate_path_component(world_id, name="world_id")
        worlds_root = (library_root(self.store.data_root) / "worlds").resolve()
        candidate = (worlds_root / world_id).resolve()
        try:
            candidate.relative_to(worlds_root)
        except ValueError as exc:
            raise InvalidRefError(f"unsafe world_id: {world_id!r}") from exc
        return candidate

    # ------------------------------------------------------------------ #
    # World management
    # ------------------------------------------------------------------ #

    async def list_worlds(self) -> list[WorldMeta]:
        return await self.library.list_worlds()

    async def get_world(self, world_id: str) -> WorldMeta:
        return await self.library.get_world(world_id)

    async def create_world(self, world_id: str, meta: dict | None = None) -> WorldMeta:
        return await self.library.create_world(world_id, meta or {})

    async def update_world_meta(self, world_id: str, patch: dict) -> WorldMeta:
        existing = await self.library.get_world(world_id)
        merged = existing.model_dump()
        merged.update(patch or {})
        merged["id"] = world_id
        # ``update_entity`` would expect a world entity-kind file; we round-trip
        # through ``create_world`` because the underlying file is YAML-only and
        # writes are upserts.
        return await self.library.create_world(world_id, merged)

    async def delete_world(self, world_id: str) -> None:
        # Validate world_id up-front so the SQL delete below and the rmtree
        # below operate on the same allowlisted shape.
        root = self._safe_world_dir(world_id)
        # Delete every entity row under the world, then the world file itself.
        rows = await self.store.db.fetchall(
            "SELECT id FROM library_index WHERE world_id = ?", (world_id,)
        )
        for row in rows:
            await self.store.delete_library_file(library_id=row["id"], source="world:delete")
        # ``library_path`` for the world card.
        import contextlib

        from grimoire.state_store.indexers import make_library_id

        with contextlib.suppress(Exception):
            await self.store.delete_library_file(
                library_id=make_library_id(world_id, "world", world_id),
                source="world:delete",
            )
        # Best-effort directory cleanup.
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)

    async def fork_world(self, src_world_id: str, dst_world_id: str) -> WorldMeta:
        """Copy a world directory under a new id and reindex.

        The user-visible operation is a deep directory copy; the index is
        rebuilt by walking the copied files through ``write_library_file``
        so every row carries a delta record and proper version numbers.
        """
        src_root = self._safe_world_dir(src_world_id)
        dst_root = self._safe_world_dir(dst_world_id)
        if not src_root.exists():
            raise WorldNotFoundError(f"source world {src_world_id!r} does not exist")
        if dst_root.exists():
            raise WorldError(f"destination world {dst_world_id!r} already exists at {dst_root}")

        shutil.copytree(src_root, dst_root)

        # Reindex by walking files and using the typed write API.
        await self._reindex_world_dir(dst_world_id)
        return await self.library.get_world(dst_world_id)

    async def _reindex_world_dir(self, world_id: str) -> None:
        from grimoire.files import load_yaml, read_markdown
        from grimoire.state_store.indexers import make_library_id

        root = library_root(self.store.data_root) / "worlds" / world_id
        world_yaml = root / "world.yaml"
        if world_yaml.exists():
            data = dict(load_yaml(world_yaml) or {})
            data["id"] = world_id
            await self.store.write_library_file(
                library_id=make_library_id(world_id, "world", world_id),
                frontmatter=data,
                body="",
                source="world:fork",
            )
        for kind, dir_name in KIND_TO_DIR.items():
            sub = root / dir_name
            if not sub.exists():
                continue
            for path in sorted(sub.glob("*.md")):
                doc = read_markdown(path)
                asset_id = path.stem
                fm = dict(doc.frontmatter)
                fm.setdefault("id", asset_id)
                await self.store.write_library_file(
                    library_id=make_library_id(world_id, kind, asset_id),
                    frontmatter=fm,
                    body=doc.body,
                    source="world:fork",
                )

    # ------------------------------------------------------------------ #
    # Per-kind CRUD (delegates to LibraryService)
    # ------------------------------------------------------------------ #

    async def list_in_world(self, world_id: str, kind: EntityKind | str) -> list[LibraryEntity]:
        return await self.library.list_in_world(world_id, kind)

    async def get_entity(
        self, world_id: str, kind: EntityKind | str, entity_id: str
    ) -> LibraryEntity:
        return await self.library.get_entity(world_id, kind, entity_id)

    async def create_entity(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        frontmatter: dict,
        body: str = "",
        *,
        source: str = "user",
    ) -> LibraryEntity:
        normalized = _normalize_kind(kind)
        if normalized == "character":
            raise WorldError("characters are owned by the Characters module")
        if normalized not in _OWNED_KINDS:
            raise WorldError(f"unsupported kind {normalized!r}")
        return await self.library.create_entity(
            world_id, normalized, entity_id, frontmatter, body, source=source
        )

    async def update_entity(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        frontmatter_patch: dict | None = None,
        body: str | None = None,
        *,
        source: str = "user",
    ) -> LibraryEntity:
        return await self.library.update_entity(
            world_id, kind, entity_id, frontmatter_patch, body, source=source
        )

    async def delete_entity(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        *,
        source: str = "user",
    ) -> None:
        await self.library.delete_entity(world_id, kind, entity_id, source=source)

    # ------------------------------------------------------------------ #
    # Typed projections (light wrappers over LibraryEntity)
    # ------------------------------------------------------------------ #

    async def list_locations(self, world_id: str) -> list[Location]:
        rows = await self.library.list_in_world(world_id, EntityKind.LOCATION)
        return [_location_from_entity(r) for r in rows]

    async def get_location(self, world_id: str, entity_id: str) -> Location:
        try:
            ent = await self.library.get_entity(world_id, "location", entity_id)
        except LibraryNotFoundError as exc:
            raise WorldNotFoundError(str(exc)) from exc
        return _location_from_entity(ent)

    async def list_items(self, world_id: str) -> list[Item]:
        rows = await self.library.list_in_world(world_id, EntityKind.ITEM)
        return [_item_from_entity(r) for r in rows]

    async def list_lore(self, world_id: str) -> list[LoreEntry]:
        rows = await self.library.list_in_world(world_id, EntityKind.LORE)
        return [_lore_from_entity(r) for r in rows]

    async def list_factions(self, world_id: str) -> list[Faction]:
        rows = await self.library.list_in_world(world_id, EntityKind.FACTION)
        return [_faction_from_entity(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Per-campaign resolution
    # ------------------------------------------------------------------ #

    async def resolve(self, entity_ref: str, campaign_id: CampaignId) -> ResolvedEntity:
        return await self.library.resolve(entity_ref, campaign_id)

    async def list_for_campaign(
        self,
        campaign_id: CampaignId,
        kind: EntityKind | str,
    ) -> list[LibraryEntity]:
        """Composition-aware listing of one kind. Walks world refs by priority
        and applies each ref's ``include`` filter.
        """
        return await self.library.list_for_composition(campaign_id, kind)

    # ------------------------------------------------------------------ #
    # Spatial queries
    # ------------------------------------------------------------------ #

    async def adjacent_locations(
        self, world_id: str, location_id: str, campaign_id: CampaignId | None = None
    ) -> list[Location]:
        """Locations connected to ``location_id`` within the same world.

        Returns parent + connection targets that exist in the world's
        index. Locations not found are silently skipped.
        """
        center = await self.get_location(world_id, location_id)
        out: list[Location] = []
        seen: set[str] = set()
        if center.parent_id and center.parent_id not in seen:
            try:
                parent = await self.get_location(world_id, center.parent_id)
                out.append(parent)
                seen.add(parent.id)
            except WorldNotFoundError:
                pass
        for conn in center.connections:
            if conn.to in seen:
                continue
            try:
                neighbor = await self.get_location(world_id, conn.to)
                out.append(neighbor)
                seen.add(neighbor.id)
            except WorldNotFoundError:
                pass
        return out

    async def path_between(
        self, world_id: str, src_id: str, dst_id: str
    ) -> list[LocationConnection]:
        """BFS over ``connections`` to find a route. Empty list = no route."""
        if src_id == dst_id:
            return []
        locations: dict[str, Location] = {
            loc.id: loc for loc in await self.list_locations(world_id)
        }
        if src_id not in locations or dst_id not in locations:
            return []
        # BFS storing predecessor + the connection used.
        prev: dict[str, tuple[str, LocationConnection]] = {}
        frontier: deque[str] = deque([src_id])
        visited: set[str] = {src_id}
        while frontier:
            current = frontier.popleft()
            if current == dst_id:
                break
            cur_loc = locations.get(current)
            if cur_loc is None:
                continue
            for conn in cur_loc.connections:
                if conn.to in visited or conn.to not in locations:
                    continue
                visited.add(conn.to)
                prev[conn.to] = (current, conn)
                frontier.append(conn.to)
        if dst_id not in prev and src_id != dst_id:
            return []
        # Reconstruct.
        path: list[LocationConnection] = []
        cursor = dst_id
        while cursor in prev:
            parent, conn = prev[cursor]
            path.append(conn)
            cursor = parent
        path.reverse()
        return path

    async def locations_within(
        self, world_id: str, parent_id: str, depth: int = 1
    ) -> list[Location]:
        """Descendants of ``parent_id`` up to ``depth`` levels (parent-id chain)."""
        all_locs = await self.list_locations(world_id)
        by_parent: dict[str | None, list[Location]] = {}
        for loc in all_locs:
            by_parent.setdefault(loc.parent_id, []).append(loc)
        out: list[Location] = []
        frontier: list[tuple[Location, int]] = [
            (child, 1) for child in by_parent.get(parent_id, [])
        ]
        while frontier:
            loc, level = frontier.pop(0)
            out.append(loc)
            if level < depth:
                frontier.extend((c, level + 1) for c in by_parent.get(loc.id, []))
        return out

    # ------------------------------------------------------------------ #
    # Cross-world variants
    # ------------------------------------------------------------------ #

    async def cross_world_lookup(
        self,
        asset_id: str,
        kind: EntityKind | str,
        exclude_world: str | None = None,
    ) -> list[LibraryEntity]:
        rows = await self.library.variants_of(asset_id, kind)
        if exclude_world:
            rows = [r for r in rows if r.world_id != exclude_world]
        return rows

    # ------------------------------------------------------------------ #
    # Lore
    # ------------------------------------------------------------------ #

    async def search_lore(
        self, query: str, campaign_id: CampaignId, top_k: int = 5
    ) -> list[LoreEntry]:
        """Naive substring search over lore reachable through composition.

        FTS-backed search lives on ``StateStore.keyword_search``; this method
        is a campaign-aware convenience that walks the composition first so
        results respect the ``include`` filter without leaking content from
        excluded worlds.
        """
        q = (query or "").strip().lower()
        if not q:
            return []
        entities = await self.library.list_for_composition(campaign_id, EntityKind.LORE)
        out: list[tuple[float, LoreEntry]] = []
        for ent in entities:
            lore = _lore_from_entity(ent)
            score = _score_lore(lore, q)
            if score > 0:
                out.append((score, lore))
        out.sort(key=lambda pair: pair[0], reverse=True)
        return [lore for _, lore in out[:top_k]]

    async def lore_by_keyword(
        self,
        keyword: str,
        campaign_id: CampaignId,
        *,
        min_length: int | None = None,
    ) -> list[LoreEntry]:
        """Match lore whose ``keywords`` list contains ``keyword`` (case-insensitive)."""
        effective_min = self.config.lore.keyword_min_length if min_length is None else min_length
        kw = (keyword or "").strip().lower()
        if len(kw) < effective_min:
            return []
        entities = await self.library.list_for_composition(campaign_id, EntityKind.LORE)
        out: list[LoreEntry] = []
        for ent in entities:
            lore = _lore_from_entity(ent)
            if any(kw == k.strip().lower() for k in lore.keywords):
                out.append(lore)
        return out

    async def lore_for_post(
        self,
        text: str,
        campaign_id: CampaignId,
        *,
        min_length: int | None = None,
        max_results: int | None = None,
    ) -> list[LoreEntry]:
        """Scan a post for lore-keyword triggers; used by the Context Builder."""
        effective_min = self.config.lore.keyword_min_length if min_length is None else min_length
        effective_max = self.config.lore.max_lore_in_archive if max_results is None else max_results
        body = (text or "").lower()
        if not body:
            return []
        entities = await self.library.list_for_composition(campaign_id, EntityKind.LORE)
        triggered: list[LoreEntry] = []
        for ent in entities:
            lore = _lore_from_entity(ent)
            for kw in lore.keywords:
                kw_lower = kw.strip().lower()
                if len(kw_lower) < effective_min:
                    continue
                if kw_lower in body:
                    triggered.append(lore)
                    break
        return triggered[:effective_max]

    # ------------------------------------------------------------------ #
    # Greetings
    # ------------------------------------------------------------------ #

    async def list_greetings(self, world_id: str) -> list[Greeting]:
        return await self.library.list_greetings(world_id)

    async def get_greeting(self, world_id: str, greeting_id: str) -> Greeting:
        return await self.library.get_greeting(world_id, greeting_id)

    # ------------------------------------------------------------------ #
    # Calendar
    # ------------------------------------------------------------------ #

    async def calendar_for(self, world_id: str) -> WorldCalendar:
        meta = await self.library.get_world(world_id)
        return parse_calendar(world_id, meta.calendar)

    async def calendar_for_campaign(self, campaign_id: CampaignId) -> WorldCalendar:
        """The calendar for ``campaign_id`` honouring multi-world policy.

        Multi-world campaigns with conflicting calendars are resolved per
        ``WorldConfig.composition.multiple_calendars_policy`` (default:
        ``pick`` — highest priority wins silently). ``merge_warn`` logs;
        ``error`` raises :class:`CompositionError`.
        """
        comp = await self.library.get_composition(campaign_id)
        refs = sorted(comp.worlds, key=lambda r: r.priority)
        if not refs:
            raise CompositionError(f"campaign {campaign_id!r} has no world refs")

        # Resolve each ref's calendar block; we treat an empty/missing
        # ``calendar`` field as "this world does not contribute a calendar"
        # so a multi-world campaign in which only one world has a calendar
        # never trips the merge_warn / error branches.
        ref_cals: list[tuple[str, WorldCalendar]] = []
        for ref in refs:
            meta = await self.library.get_world(ref.world_id)
            raw = meta.calendar if isinstance(meta.calendar, dict) else {}
            if not raw:
                continue
            ref_cals.append((ref.world_id, parse_calendar(ref.world_id, raw)))

        if not ref_cals:
            return await self.calendar_for(refs[0].world_id)

        picked_world, picked_cal = ref_cals[0]
        if len(ref_cals) == 1:
            return picked_cal

        conflicting = [world_id for world_id, cal in ref_cals[1:] if cal != picked_cal]
        if not conflicting:
            return picked_cal

        policy = self.config.composition.multiple_calendars_policy
        if policy == "merge_warn":
            logger.warning(
                "multiple worlds declare calendars for campaign %s; picking %s, "
                "conflicting refs: %s",
                campaign_id,
                picked_world,
                conflicting,
            )
            return picked_cal
        if policy == "error":
            raise CompositionError(
                f"campaign {campaign_id!r} has conflicting calendars across worlds "
                f"({picked_world!r} vs {conflicting!r}); set "
                f"composition.multiple_calendars_policy = 'pick' or 'merge_warn'"
            )
        # 'pick' is the default — silent.
        return picked_cal

    async def season_for(self, when: InGameTime, campaign_id: CampaignId) -> Season | None:
        cal = await self.calendar_for_campaign(campaign_id)
        return season_for(cal, when)

    async def holiday_at(self, when: InGameTime, campaign_id: CampaignId) -> Holiday | None:
        cal = await self.calendar_for_campaign(campaign_id)
        return holiday_at(cal, when)

    # ------------------------------------------------------------------ #
    # Weather
    # ------------------------------------------------------------------ #

    async def weather_for(
        self,
        world_id: str,
        location_id: str,
        when: InGameTime,
        campaign_id: CampaignId,
        *,
        branch_id: str | None = None,
    ) -> Weather:
        """Return procedural weather, honouring any campaign-local override.

        Same ``(campaign, location, hour)`` inputs always produce the same
        result. A campaign-local override (stored on ``location_state.weather``)
        takes precedence when present.
        """
        branch = branch_id or f"{campaign_id}:main"
        override = await self._get_weather_override(
            campaign_id=campaign_id,
            branch_id=branch,
            location_ref=_location_ref(world_id, location_id),
        )
        if override is not None:
            return override

        try:
            loc = await self.get_location(world_id, location_id)
            climate = loc.climate_zone
            indoor = loc.indoor
        except WorldNotFoundError:
            climate = None
            indoor = False
        cal = await self.calendar_for(world_id)
        return generate_weather(
            campaign_id=campaign_id,
            location_ref=_location_ref(world_id, location_id),
            when=when,
            calendar=cal,
            climate_zone=climate,
            indoor=indoor,
        )

    async def override_weather(
        self,
        world_id: str,
        location_id: str,
        weather: Weather,
        campaign_id: CampaignId,
        *,
        branch_id: str | None = None,
        source: str = "user",
    ) -> None:
        """Persist a campaign-local weather override on ``location_state``."""
        branch = branch_id or f"{campaign_id}:main"
        payload = json.dumps({**weather.model_dump(), "source": "override"})
        await self.store.db.execute(
            """
            INSERT INTO location_state (
              location_ref, campaign_id, branch_id, weather, occupants, transient_features,
              updated_at_turn
            )
            VALUES (?, ?, ?, ?, '[]', '[]', NULL)
            ON CONFLICT(location_ref, branch_id) DO UPDATE SET
              campaign_id = excluded.campaign_id,
              weather = excluded.weather
            """,
            (
                _location_ref(world_id, location_id),
                campaign_id,
                branch,
                payload,
            ),
        )
        # Tag the row with source through delta log for audit; we don't go
        # through ``apply_delta`` because the column shape doesn't match the
        # full LocationState contract (intentional — weather override only).
        _ = source

    async def _get_weather_override(
        self,
        *,
        campaign_id: CampaignId,
        branch_id: str,
        location_ref: str,
    ) -> Weather | None:
        row = await self.store.db.fetchone(
            """
            SELECT weather FROM location_state
            WHERE campaign_id = ? AND branch_id = ? AND location_ref = ?
            """,
            (campaign_id, branch_id, location_ref),
        )
        if row is None or not row["weather"]:
            return None
        try:
            payload = json.loads(row["weather"])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("source") != "override":
            return None
        try:
            return Weather.model_validate(payload)
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Faction state (campaign-scoped, SQLite)
    # ------------------------------------------------------------------ #

    async def faction_state(
        self,
        faction_ref: str,
        campaign_id: CampaignId,
        branch_id: str | None = None,
    ) -> FactionStateData:
        branch = branch_id or f"{campaign_id}:main"
        row = await self.store.db.fetchone(
            """
            SELECT * FROM faction_state
            WHERE faction_ref = ? AND branch_id = ?
            """,
            (faction_ref, branch),
        )
        if row is None:
            return FactionStateData(
                faction_ref=faction_ref,
                campaign_id=campaign_id,
                branch_id=branch,
            )
        return _faction_state_from_row(row)

    async def update_faction_state(
        self,
        faction_ref: str,
        campaign_id: CampaignId,
        patch: dict,
        *,
        branch_id: str | None = None,
        source: str = "user",
        turn_id: str | None = None,
    ) -> FactionStateData:
        branch = branch_id or f"{campaign_id}:main"
        existing = await self.faction_state(faction_ref, campaign_id, branch_id=branch)
        merged = existing.model_dump()
        for k, v in (patch or {}).items():
            if k == "goals" and isinstance(v, list):
                merged["goals"] = [g if isinstance(g, dict) else g.model_dump() for g in v]
            else:
                merged[k] = v
        payload = {
            "goals": merged.get("goals") or [],
            "resources": merged.get("resources") or {},
            "current_focus": merged.get("current_focus") or "",
            "public_perception": merged.get("public_perception") or "",
            "secrets": merged.get("secrets") or [],
        }
        await self.store.db.execute(
            """
            INSERT INTO faction_state (
              faction_ref, campaign_id, branch_id, state, updated_at_turn
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(faction_ref, branch_id) DO UPDATE SET
              campaign_id = excluded.campaign_id,
              state = excluded.state,
              updated_at_turn = excluded.updated_at_turn
            """,
            (
                faction_ref,
                campaign_id,
                branch,
                json.dumps(payload, sort_keys=True, default=str),
                turn_id or _now_iso(),
            ),
        )
        _ = source
        return await self.faction_state(faction_ref, campaign_id, branch_id=branch)

    # ------------------------------------------------------------------ #
    # Composition surfacing
    # ------------------------------------------------------------------ #

    async def get_composition(self, campaign_id: CampaignId):
        return await self.library.get_composition(campaign_id)

    async def upgrade_world_ref(self, campaign_id: CampaignId, world_id: str) -> UpgradeReport:
        return await self.library.upgrade_world_ref(campaign_id, world_id)

    # ------------------------------------------------------------------ #
    # Promotion (non-character kinds)
    # ------------------------------------------------------------------ #

    async def promote_to_library(
        self,
        campaign_id: CampaignId,
        kind: EntityKind | str,
        campaign_entity_id: str,
        target_world_id: str,
        *,
        source: str = "user",
    ) -> str:
        normalized = _normalize_kind(kind)
        if normalized == "character":
            raise WorldError("character promotion goes through the Characters module (task #12)")
        return await self.library.promote_to_library(
            campaign_id, normalized, campaign_entity_id, target_world_id, source=source
        )


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _location_ref(world_id: str, asset_id: str) -> str:
    return f"library:worlds/{world_id}/locations/{asset_id}"


def _location_from_entity(ent: LibraryEntity) -> Location:
    fm: dict[str, Any] = ent.frontmatter or {}
    conns = [
        LocationConnection(
            to=str(c.get("to") or ""),
            via=str(c.get("via") or ""),
            duration_min=int(c.get("duration_min") or 0),
            notes=str(c.get("notes") or ""),
        )
        for c in (fm.get("connections") or [])
        if isinstance(c, dict)
    ]
    coords = None
    raw_coords = fm.get("coordinates")
    if isinstance(raw_coords, dict) and "x" in raw_coords and "y" in raw_coords:
        from grimoire.types.world import Coords

        coords = Coords(x=float(raw_coords["x"]), y=float(raw_coords["y"]))
    try:
        kind = LocationKind(fm.get("kind") or "other")
    except ValueError:
        kind = LocationKind.OTHER
    return Location(
        world_id=ent.world_id or "",
        id=ent.asset_id,
        name=ent.name,
        parent_id=fm.get("parent_id"),
        kind=kind,
        aliases=list(fm.get("aliases") or []),
        tags=list(ent.tags or fm.get("tags") or []),
        climate_zone=fm.get("climate_zone"),
        indoor=bool(fm.get("indoor") or False),
        coordinates=coords,
        permanent_features=list(fm.get("permanent_features") or []),
        connections=conns,
        typical_occupants=[str(t) for t in (fm.get("typical_occupants") or [])],
        description=str(fm.get("description") or ""),
        body=ent.body,
    )


def _item_from_entity(ent: LibraryEntity) -> Item:
    fm = ent.frontmatter or {}
    return Item(
        world_id=ent.world_id or "",
        id=ent.asset_id,
        name=ent.name,
        aliases=list(fm.get("aliases") or []),
        tags=list(ent.tags or fm.get("tags") or []),
        provenance=fm.get("provenance"),
        current_holder=fm.get("current_holder"),
        description=str(fm.get("description") or ""),
        body=ent.body,
    )


def _faction_from_entity(ent: LibraryEntity) -> Faction:
    fm = ent.frontmatter or {}
    return Faction(
        world_id=ent.world_id or "",
        id=ent.asset_id,
        name=ent.name,
        kind=str(fm.get("kind") or ""),
        base_location=fm.get("base_location"),
        leaders=[str(x) for x in (fm.get("leaders") or [])],
        members=[str(x) for x in (fm.get("members") or [])],
        allies=[str(x) for x in (fm.get("allies") or [])],
        rivals=[str(x) for x in (fm.get("rivals") or [])],
        tags=list(ent.tags or fm.get("tags") or []),
        description=str(fm.get("description") or ""),
        body=ent.body,
    )


def _lore_from_entity(ent: LibraryEntity) -> LoreEntry:
    fm = ent.frontmatter or {}
    return LoreEntry(
        world_id=ent.world_id or "",
        id=ent.asset_id,
        title=ent.name,
        body=ent.body,
        tags=list(ent.tags or fm.get("tags") or []),
        keywords=list(ent.keywords or fm.get("keywords") or []),
        related_locations=[str(x) for x in (fm.get("related_locations") or [])],
        related_factions=[str(x) for x in (fm.get("related_factions") or [])],
        related_characters=[str(x) for x in (fm.get("related_characters") or [])],
        secrecy=str(fm.get("secrecy") or "public"),
    )


def _faction_state_from_row(row: Any) -> FactionStateData:
    try:
        decoded = json.loads(row["state"]) if row["state"] else {}
    except (TypeError, json.JSONDecodeError):
        decoded = {}
    if not isinstance(decoded, dict):
        decoded = {}
    goals = [
        FactionGoal(
            id=str(g.get("id") or ""),
            description=str(g.get("description") or ""),
            progress=float(g.get("progress") or 0.0),
            deadline=_parse_dt(g.get("deadline")),
        )
        for g in (decoded.get("goals") or [])
        if isinstance(g, dict)
    ]
    return FactionStateData(
        faction_ref=row["faction_ref"],
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        goals=goals,
        resources=dict(decoded.get("resources") or {}),
        current_focus=str(decoded.get("current_focus") or ""),
        public_perception=str(decoded.get("public_perception") or ""),
        secrets=[str(s) for s in (decoded.get("secrets") or [])],
        updated_at_turn=row["updated_at_turn"],
    )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _score_lore(lore: LoreEntry, q: str) -> float:
    score = 0.0
    if q in (lore.title or "").lower():
        score += 3.0
    body_lower = (lore.body or "").lower()
    if q in body_lower:
        score += 1.0
    for tag in lore.tags:
        if q in tag.lower():
            score += 0.5
    for kw in lore.keywords:
        if q == kw.strip().lower():
            score += 2.0
        elif q in kw.lower():
            score += 1.0
    return score


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
