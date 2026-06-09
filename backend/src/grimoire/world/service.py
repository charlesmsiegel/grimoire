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

import hashlib
import json
import logging
import re
import shutil
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from grimoire.library import LibraryService
from grimoire.library.errors import LibraryNotFoundError
from grimoire.state_store import StateStore
from grimoire.state_store.errors import InvalidRefError
from grimoire.state_store.paths import KIND_TO_DIR, library_root, validate_path_component
from grimoire.types.common import CampaignId, EntityKind, InGameTime, Scope
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
    LocationStateData,
    LoreEntry,
    Monster,
    MonsterCategory,
    Month,
    Season,
    SelectiveLogic,
    Weather,
    WorldCalendar,
)
from grimoire.util import canonicalize_character_ref, now_iso, parse_iso_datetime

from .atmosphere import generate_atmosphere
from .calendar import holiday_at, parse_calendar, season_for
from .config import WorldConfig
from .errors import CompositionError, WorldError, WorldNotFoundError
from .location_generator import generate_location_frontmatter
from .weather import generate_weather

logger = logging.getLogger(__name__)


_DEFAULT_CALENDAR_ID = "gregorian"


def _qualify_greeting_ref(ref: str, world_id: str) -> str:
    """Canonicalize a greeting's character ref into a resolvable form.

    Greetings author present/POV characters as bare world-character ids
    (e.g. ``mina-ashido``). A bare single-segment id can't be parsed on its
    own — ``canonicalize_character_ref`` and the characters service both need
    the world to qualify it. Prefix bare ids with the greeting's world so the
    seeded scene stores the canonical ``library:worlds/<world>/characters/<id>``
    form that drift checks, the cast HUD, and context assembly all accept.
    Refs that already carry a scheme or path are canonicalized as-is.
    """
    r = (ref or "").strip()
    if not r:
        return r
    if ":" in r or "/" in r:
        return canonicalize_character_ref(r)
    return canonicalize_character_ref(f"{world_id}/{r}")


def _seed_from_builtin(cal: WorldCalendar, calendar_id: str) -> WorldCalendar:
    """Populate empty months/weekday_names from a builtin calendar engine."""
    from grimoire.world.calendars.base import WEEKDAY_NAMES
    from grimoire.world.calendars.registry import BUILTIN_CALENDARS, engine_for

    builtin = BUILTIN_CALENDARS.get(calendar_id)
    if builtin is None:
        return cal
    engine = engine_for(builtin)
    ref_year = 2024
    try:
        year_days = engine.year_length_days(ref_year)
        start_jdn = engine.to_jdn(ref_year, 1, 1)
    except Exception:
        return cal
    months: list[Month] = []
    seen_months: set[int] = set()
    jdn = start_jdn
    while jdn < start_jdn + year_days:
        parts = engine.from_jdn(jdn)
        if parts.month in seen_months:
            jdn += 1
            continue
        seen_months.add(parts.month)
        name = engine.month_name(parts)
        next_m = parts.month + 1
        try:
            next_jdn = engine.to_jdn(ref_year, next_m, 1)
        except Exception:
            next_jdn = start_jdn + year_days
        days = next_jdn - engine.to_jdn(ref_year, parts.month, 1)
        if days <= 0:
            days = 30
        months.append(Month(name=name, days=days))
        jdn = next_jdn
    if not months:
        return cal
    weekdays = cal.week_day_names if cal.week_day_names else list(WEEKDAY_NAMES)
    return cal.model_copy(update={"months": months, "week_day_names": weekdays})


# World-internal entity kinds World owns CRUD for.
_OWNED_KINDS: frozenset[str] = frozenset(
    {"item", "location", "lore", "faction", "greeting", "monster"}
)

# §2 Lore secrecy → player-audience filter
_PLAYER_HIDDEN_SECRECIES: frozenset[str] = frozenset({"restricted", "secret"})
_VALID_AUDIENCES: frozenset[str] = frozenset({"model", "player"})


def _filter_by_audience(entries: list[LoreEntry], audience: str) -> list[LoreEntry]:
    if audience == "model":
        return entries
    return [e for e in entries if (e.secrecy or "public").lower() not in _PLAYER_HIDDEN_SECRECIES]


def _normalize_kind(kind: EntityKind | str) -> str:
    """Accept enums and plural directory names."""
    if isinstance(kind, EntityKind):
        return kind.value
    if kind in {"items", "locations", "lore", "factions", "greetings", "characters", "monsters"}:
        return {
            "items": "item",
            "locations": "location",
            "lore": "lore",
            "factions": "faction",
            "greetings": "greeting",
            "characters": "character",
            "monsters": "monster",
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
        gateway: Any = None,
    ) -> None:
        self.library = library
        self.store: StateStore = library.store
        self.config: WorldConfig = config or WorldConfig()
        # Optional LLM gateway — used for atmosphere auto-generation (§3)
        # and emergent location generation (§9). Both are no-ops when None.
        self.gateway = gateway

    def set_gateway(self, gateway: Any) -> None:
        self.gateway = gateway

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
        meta = dict(meta or {})
        # §3 atmosphere auto-generation: only when config flag is on, a
        # gateway is wired, and the caller didn't supply an atmosphere block.
        if (
            self.config.atmosphere_auto_generate
            and self.gateway is not None
            and not (meta.get("atmosphere") or {})
        ):
            atmosphere = await generate_atmosphere(
                gateway=self.gateway,
                world_id=world_id,
                name=str(meta.get("name") or world_id),
                tags=list(meta.get("tags") or []),
                description=str(meta.get("description") or ""),
            )
            if atmosphere:
                meta["atmosphere"] = atmosphere
        return await self.library.create_world(world_id, meta)

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

    async def list_monsters(self, world_id: str) -> list[Monster]:
        rows = await self.library.list_in_world(world_id, EntityKind.MONSTER)
        return [_monster_from_entity(r) for r in rows]

    async def get_monster(self, world_id: str, entity_id: str) -> Monster:
        try:
            ent = await self.library.get_entity(world_id, "monster", entity_id)
        except LibraryNotFoundError as exc:
            raise WorldNotFoundError(str(exc)) from exc
        return _monster_from_entity(ent)

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

    async def list_resolved_for_campaign(
        self,
        campaign_id: CampaignId,
        kind: EntityKind | str,
    ) -> list[ResolvedEntity]:
        """Composition-aware listing with the read cascade applied (#600).

        Walks the same composition refs as :meth:`list_for_campaign`, resolves
        each row through the cascade (emergent shadow → override → snapshot /
        live), then appends campaign-local emergent entities of the kind —
        mirroring ``CharactersService.list_for_campaign`` so the campaign
        World tabs follow the same resolution rules as Cast.
        """
        normalized = _normalize_kind(kind)
        dir_name = KIND_TO_DIR.get(normalized)
        if dir_name is None:
            raise WorldError(f"unsupported kind {normalized!r}")
        out: list[ResolvedEntity] = []
        seen: set[str] = set()
        for ent in await self.library.list_for_composition(campaign_id, normalized):
            try:
                resolved = await self.library.resolve(
                    f"worlds/{ent.world_id}/{dir_name}/{ent.asset_id}", campaign_id
                )
            except LibraryNotFoundError:
                continue
            out.append(resolved)
            seen.add(ent.asset_id)
        for row in await self.store.list_emergent(campaign_id, normalized):
            asset_id = str(row.get("asset_id") or "")
            # An emergent that shadows a composed library asset already
            # surfaced through the cascade above; don't list it twice.
            if not asset_id or asset_id in seen:
                continue
            try:
                resolved = await self.library.resolve(
                    f"emergent/{normalized}/{asset_id}", campaign_id
                )
            except LibraryNotFoundError:
                continue
            out.append(resolved)
            seen.add(asset_id)
        return out

    async def upsert_override(
        self,
        campaign_id: CampaignId,
        kind: EntityKind | str,
        entity_id: str,
        patch: dict,
        *,
        world_id: str,
        source: str = "world:override",
    ) -> None:
        """Write a campaign-local override for a world-owned entity (#600).

        Counterpart of ``CharactersService.upsert_override`` for the
        non-character kinds; character overrides stay with the Characters
        module (they also invalidate its view cache).
        """
        from grimoire.state_store.indexers import make_library_id

        normalized = _normalize_kind(kind)
        if normalized == "character":
            raise WorldError("character overrides are owned by the Characters module")
        if normalized not in _OWNED_KINDS:
            raise WorldError(f"unsupported kind {normalized!r}")
        await self.store.write_override(
            campaign_id=campaign_id,
            library_id=make_library_id(world_id, normalized, entity_id),
            patch=patch,
            source=source,
        )

    # ------------------------------------------------------------------ #
    # Spatial queries
    # ------------------------------------------------------------------ #

    async def adjacent_locations(
        self,
        location_ref: str,
        campaign_id: CampaignId | None = None,
    ) -> list[Location]:
        """Locations adjacent to ``location_ref`` (parent + connection targets).

        ``location_ref`` is a full entity ref
        (``library:worlds/<world_id>/locations/<asset_id>``). When
        ``campaign_id`` is provided, connection targets that name another
        world's ref ('library:worlds/<other>/locations/<asset>') resolve
        through the campaign's composition cascade — so multi-world
        campaigns can trace adjacency across world boundaries.
        Connections whose ``to`` is a bare asset_id resolve against the
        same world as ``location_ref`` (legacy / common case).
        """
        parsed = _parse_location_ref(location_ref)
        if parsed is None:
            return []
        world_id, asset_id = parsed
        try:
            center = await self.get_location(world_id, asset_id)
        except WorldNotFoundError:
            return []

        comp_world_ids = await self._comp_world_ids(campaign_id)
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
            resolved = await self._resolve_connection_target(
                conn.to,
                source_world=world_id,
                comp_world_ids=comp_world_ids,
            )
            if resolved is None:
                continue
            if resolved.id not in seen:
                out.append(resolved)
                seen.add(resolved.id)
        return out

    async def path_between(
        self,
        src_ref: str,
        dst_ref: str,
        campaign_id: CampaignId | None = None,
    ) -> list[LocationConnection]:
        """BFS path between two location refs. Empty list = no route.

        When ``campaign_id`` is provided, the search graph includes every
        world in the campaign's composition; otherwise it's scoped to the
        world named by ``src_ref``.
        """
        if src_ref == dst_ref:
            return []
        src_parsed = _parse_location_ref(src_ref)
        dst_parsed = _parse_location_ref(dst_ref)
        if src_parsed is None or dst_parsed is None:
            return []

        comp_world_ids = await self._comp_world_ids(campaign_id) or {
            src_parsed[0],
            dst_parsed[0],
        }
        # Build the cross-world graph keyed by full ref.
        all_locs: dict[str, tuple[str, Location]] = {}
        for wid in comp_world_ids:
            try:
                for loc in await self.list_locations(wid):
                    all_locs[_location_ref(wid, loc.id)] = (wid, loc)
            except WorldNotFoundError:
                continue
        if src_ref not in all_locs or dst_ref not in all_locs:
            return []

        prev: dict[str, tuple[str, LocationConnection]] = {}
        frontier: deque[str] = deque([src_ref])
        visited: set[str] = {src_ref}
        while frontier:
            cur_ref = frontier.popleft()
            if cur_ref == dst_ref:
                break
            cur_world, cur_loc = all_locs[cur_ref]
            for conn in cur_loc.connections:
                neighbor_ref = (
                    conn.to if _is_entity_ref(conn.to) else _location_ref(cur_world, conn.to)
                )
                if neighbor_ref in visited or neighbor_ref not in all_locs:
                    continue
                visited.add(neighbor_ref)
                prev[neighbor_ref] = (cur_ref, conn)
                frontier.append(neighbor_ref)

        if dst_ref not in prev:
            return []
        path: list[LocationConnection] = []
        cursor = dst_ref
        while cursor in prev:
            parent_ref, conn = prev[cursor]
            path.append(conn)
            cursor = parent_ref
        path.reverse()
        return path

    async def locations_within(
        self,
        parent_ref: str,
        campaign_id: CampaignId | None = None,
        depth: int = 1,
    ) -> list[Location]:
        """Descendants of ``parent_ref`` up to ``depth`` levels.

        Children are matched by ``parent_id`` within the same world as
        ``parent_ref``; cross-world parenting is not supported (a
        location can only have one home world).
        """
        parsed = _parse_location_ref(parent_ref)
        if parsed is None:
            return []
        parent_world, parent_asset = parsed
        # campaign_id is accepted for API symmetry, but locations_within
        # walks parent-id chains which never cross worlds. We still honour
        # the composition so an excluded world's children aren't surfaced
        # via a stray parent_ref.
        if campaign_id is not None:
            comp_world_ids = await self._comp_world_ids(campaign_id)
            if parent_world not in comp_world_ids:
                return []

        all_locs = await self.list_locations(parent_world)
        by_parent: dict[str | None, list[Location]] = {}
        for loc in all_locs:
            by_parent.setdefault(loc.parent_id, []).append(loc)

        out: list[Location] = []
        frontier: list[tuple[Location, int]] = [
            (child, 1) for child in by_parent.get(parent_asset, [])
        ]
        while frontier:
            loc, level = frontier.pop(0)
            out.append(loc)
            if level < depth:
                frontier.extend((c, level + 1) for c in by_parent.get(loc.id, []))
        return out

    async def _comp_world_ids(self, campaign_id: CampaignId | None) -> set[str]:
        """Return the world ids in the campaign's composition (or empty set)."""
        if campaign_id is None:
            return set()
        try:
            comp = await self.library.get_composition(campaign_id)
        except Exception:
            return set()
        return {ref.world_id for ref in comp.worlds}

    async def _resolve_connection_target(
        self,
        target: str,
        *,
        source_world: str,
        comp_world_ids: set[str],
    ) -> Location | None:
        """Return the :class:`Location` ``target`` points to, or ``None``."""
        if _is_entity_ref(target):
            parsed = _parse_location_ref(target)
            if parsed is None:
                return None
            wid, aid = parsed
            # If we have a composition, restrict to its worlds. If we
            # don't (campaign_id=None caller), allow any registered world
            # so single-world callers still resolve cross-world refs.
            if comp_world_ids and wid not in comp_world_ids:
                return None
            try:
                return await self.get_location(wid, aid)
            except WorldNotFoundError:
                return None
        try:
            return await self.get_location(source_world, target)
        except WorldNotFoundError:
            return None

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
        self,
        query: str,
        campaign_id: CampaignId,
        top_k: int = 5,
        *,
        audience: str = "model",
    ) -> list[LoreEntry]:
        """FTS-backed lore search filtered by composition + secrecy.

        Drives :meth:`StateStore.keyword_search` against the lore FTS index,
        then post-filters by the campaign's composition (so excluded worlds
        never leak) and by audience (player audience drops restricted +
        secret entries).
        """
        if audience not in _VALID_AUDIENCES:
            raise ValueError(f"audience must be one of {sorted(_VALID_AUDIENCES)!r}")
        q = (query or "").strip()
        if not q:
            return []

        hits = await self.store.keyword_search(
            query=q,
            kinds=("lore",),
            top_k=top_k * 4,
        )
        if not hits:
            return []

        in_composition = {
            ent.asset_id: ent
            for ent in await self.library.list_for_composition(campaign_id, EntityKind.LORE)
        }
        out: list[LoreEntry] = []
        seen_ids: set[str] = set()
        for hit in hits:
            # SearchHit.ref is the library_id, shaped 'worlds/<wid>/lore/<asset>'.
            ref = getattr(hit, "ref", "") or ""
            asset_id = ref.split("/")[-1] if ref else ""
            if not asset_id or asset_id in seen_ids:
                continue
            ent = in_composition.get(asset_id)
            if ent is None:
                continue
            seen_ids.add(asset_id)
            out.append(_lore_from_entity(ent))
            if len(out) >= top_k * 2:
                break  # leave room for audience filter to trim further
        filtered = _filter_by_audience(out, audience)
        return filtered[:top_k]

    async def lore_by_keyword(
        self,
        keyword: str,
        campaign_id: CampaignId,
        *,
        min_length: int | None = None,
        audience: str = "model",
    ) -> list[LoreEntry]:
        """Match lore whose ``keywords`` list contains ``keyword`` (case-insensitive)."""
        if audience not in _VALID_AUDIENCES:
            raise ValueError(f"audience must be one of {sorted(_VALID_AUDIENCES)!r}")
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
        return _filter_by_audience(out, audience)

    async def lore_for_post(
        self,
        text: str,
        campaign_id: CampaignId,
        *,
        min_length: int | None = None,
        max_results: int | None = None,
        audience: str = "model",
        turn_id: str | None = None,
    ) -> list[LoreEntry]:
        """Scan a post for lore triggers; used by the Context Builder.

        Implements the SillyTavern-shaped scoring algorithm from
        ``docs/superpowers/specs/2026-05-19-card-imports-design.md`` §4:
        primary keyword + ``selective_logic`` over ``secondary_keys`` +
        deterministic probability roll + priority sort. Each entry's
        ``scan_depth`` narrows ``text`` to its last N lines before
        matching. ``constant`` entries fire unconditionally.

        ``turn_id`` seeds the probability roll deterministically; when
        absent we fall back to the entry id alone (still stable for a
        given pair of inputs).
        """
        if audience not in _VALID_AUDIENCES:
            raise ValueError(f"audience must be one of {sorted(_VALID_AUDIENCES)!r}")
        effective_min = self.config.lore.keyword_min_length if min_length is None else min_length
        effective_max = self.config.lore.max_lore_in_archive if max_results is None else max_results

        text = text or ""
        entities = await self.library.list_for_composition(campaign_id, EntityKind.LORE)
        scored: list[tuple[int, str, LoreEntry]] = []  # (-priority, id, entry)
        for ent in entities:
            lore = _lore_from_entity(ent)
            if not lore.enabled:
                continue
            if lore.constant:
                scored.append((-lore.priority, lore.id, lore))
                continue
            haystack = _build_haystack(text, scan_depth=lore.scan_depth)
            if not haystack:
                continue
            if not _primary_keyword_match(lore, haystack, effective_min):
                continue
            if lore.secondary_keys and not _evaluate_selective_logic(lore, haystack):
                continue
            if not _probability_check(lore, turn_id):
                continue
            scored.append((-lore.priority, lore.id, lore))

        scored.sort()
        triggered = [entry for _, _, entry in scored]
        # Apply audience filter BEFORE truncation so the cap counts visible entries only.
        visible = _filter_by_audience(triggered, audience)
        return visible[:effective_max]

    # ------------------------------------------------------------------ #
    # Greetings
    # ------------------------------------------------------------------ #

    async def list_greetings(self, world_id: str) -> list[Greeting]:
        return await self.library.list_greetings(world_id)

    async def get_greeting(self, world_id: str, greeting_id: str) -> Greeting:
        return await self.library.get_greeting(world_id, greeting_id)

    async def seed_scene_from_greeting(
        self,
        *,
        campaign_id: CampaignId,
        greeting_id: str,
        world_id: str,
        scene_manager: Any,
    ) -> Any:
        """§8 Build a SceneInit from a Greeting and create scene 1.

        Returns the resulting :class:`Scene`. The caller (typically the
        campaign-creation REST handler) is responsible for any follow-up
        (opening-narration LLM call, first-post append).
        """
        from grimoire.scenes.types import SceneInit  # late import to avoid cycle

        greeting = await self.library.get_greeting(world_id, greeting_id)

        in_game_start: datetime | None = None
        starting_time = getattr(greeting, "starting_time", None)
        if isinstance(starting_time, str) and starting_time:
            try:
                in_game_start = datetime.fromisoformat(starting_time)
            except ValueError:
                in_game_start = None

        pov = getattr(greeting, "pov_character", None)
        init = SceneInit(
            campaign_id=campaign_id,
            greeting_id=greeting_id,
            title=str(getattr(greeting, "name", None) or "Scene 1"),
            location_ref=getattr(greeting, "starting_location", None),
            in_game_start=in_game_start,
            pov_character_ref=_qualify_greeting_ref(pov, world_id) if pov else None,
            present_character_refs=[
                _qualify_greeting_ref(r, world_id)
                for r in (getattr(greeting, "present_characters", []) or [])
            ],
            mood=str(getattr(greeting, "mood", "") or "") or None,
            tags=list(getattr(greeting, "tags", []) or []),
        )
        return await scene_manager.start_scene(init)

    # ------------------------------------------------------------------ #
    # Calendar
    # ------------------------------------------------------------------ #

    async def calendar_for(self, world_id: str) -> WorldCalendar:
        meta = await self.library.get_world(world_id)
        cal = parse_calendar(world_id, meta.calendar)
        if not cal.months:
            cal = _seed_from_builtin(cal, meta.display_calendar_id or _DEFAULT_CALENDAR_ID)
        return cal

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
            cal = parse_calendar(ref.world_id, raw)
            if not cal.months:
                cal = _seed_from_builtin(cal, meta.display_calendar_id or _DEFAULT_CALENDAR_ID)
            ref_cals.append((ref.world_id, cal))

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
    ) -> Weather:
        """Return procedural weather, honouring any campaign-local override.

        Same ``(campaign, location, hour)`` inputs always produce the same
        result. A campaign-local override (stored on ``location_state.weather``)
        takes precedence when present.
        """
        override = await self._get_weather_override(
            campaign_id=campaign_id,
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
        source: str = "user",
    ) -> None:
        """Persist a campaign-local weather override on ``location_state``."""
        payload = json.dumps({**weather.model_dump(), "source": "override"})
        await self.store.db.execute(
            """
            INSERT INTO location_state (
              location_ref, campaign_id, weather, occupants, transient_features,
              updated_at_turn
            )
            VALUES (?, ?, ?, '[]', '[]', NULL)
            ON CONFLICT(location_ref, campaign_id) DO UPDATE SET
              weather = excluded.weather
            """,
            (
                _location_ref(world_id, location_id),
                campaign_id,
                payload,
            ),
        )
        # Tag the row with source through delta log for audit; we don't go
        # through ``apply_delta`` because the column shape doesn't match the
        # full LocationState contract (intentional — weather override only).
        _ = source

    async def apply_weather_override_delta(self, delta: Any) -> None:
        """Apply an extractor-emitted weather override delta (§5).

        Validates the delta kind/target_table, parses the payload, and
        routes through :meth:`override_weather`. Used by the orchestrator's
        delta-dispatch hook so extractor-detected weather changes ("it
        began to rain") actually take effect.
        """
        from grimoire.types.state import DeltaKind

        if getattr(delta, "kind", None) != DeltaKind.OVERRIDE_WRITE:
            raise ValueError(
                f"apply_weather_override_delta requires kind=OVERRIDE_WRITE, "
                f"got {getattr(delta, 'kind', None)!r}"
            )
        if getattr(delta, "target_table", None) != "location_state":
            raise ValueError(
                f"apply_weather_override_delta requires target_table='location_state', "
                f"got {getattr(delta, 'target_table', None)!r}"
            )
        after = getattr(delta, "after", None) or {}
        location_ref = getattr(delta, "target_id", None) or ""
        campaign_id = after.get("campaign_id") or ""
        weather_payload = after.get("weather") or {}
        weather = Weather.model_validate(weather_payload)
        # location_ref shape: 'library:worlds/<world_id>/locations/<asset_id>'.
        # Drop the leading 'library:' so split('/') yields stable indices.
        stripped = location_ref.removeprefix("library:")
        parts = stripped.split("/")
        try:
            world_id = parts[parts.index("worlds") + 1]
            location_id = parts[parts.index("locations") + 1]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"unparseable location_ref {location_ref!r}") from exc
        await self.override_weather(
            world_id,
            location_id,
            weather,
            campaign_id,
            source=delta.source or "extractor",
        )

    async def apply_emergent_location_delta(
        self,
        delta: Any,
        *,
        turn_id: str | None = None,
    ) -> Path:
        """§9 Materialize an emergent-location delta to disk + index.

        Calls the LLM gateway to flesh out the location frontmatter (if a
        gateway is available); writes the result via
        :meth:`StateStore.write_emergent` so the row is campaign-local
        and the delta log records the create.
        """
        from grimoire.types.state import DeltaKind

        if getattr(delta, "kind", None) != DeltaKind.EMERGENT_CREATE:
            raise ValueError(
                f"apply_emergent_location_delta requires kind=EMERGENT_CREATE, "
                f"got {getattr(delta, 'kind', None)!r}"
            )
        after = getattr(delta, "after", None) or {}
        if (after.get("kind") or "") != "location":
            raise ValueError("apply_emergent_location_delta requires after.kind='location'")

        campaign_id = str(after.get("campaign_id") or "")
        if not campaign_id:
            raise ValueError("apply_emergent_location_delta requires after.campaign_id")
        name = str(after.get("name") or "")
        entity_id = name.strip().replace(" ", "-").lower()[:40] or "emergent-location"

        frontmatter: dict[str, Any] = {}
        if self.gateway is not None:
            frontmatter = await generate_location_frontmatter(
                gateway=self.gateway,
                name=name,
                context=str(after.get("evidence") or ""),
                campaign_id=campaign_id,
            )
        frontmatter.setdefault("id", entity_id)
        frontmatter.setdefault("name", name or entity_id)
        frontmatter.setdefault("kind", "other")

        return await self.store.write_emergent(
            campaign_id=campaign_id,
            kind="location",
            entity_id=entity_id,
            frontmatter=frontmatter,
            body=str(frontmatter.get("description") or ""),
            source=delta.source or "extractor",
            turn_id=turn_id,
        )

    async def _get_weather_override(
        self,
        *,
        campaign_id: CampaignId,
        location_ref: str,
    ) -> Weather | None:
        row = await self.store.db.fetchone(
            """
            SELECT weather FROM location_state
            WHERE campaign_id = ? AND location_ref = ?
            """,
            (campaign_id, location_ref),
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
    # Location state (campaign-scoped, SQLite) — §10
    # ------------------------------------------------------------------ #

    async def get_location_state(
        self,
        location_ref: str,
        campaign_id: CampaignId,
    ) -> LocationStateData:
        row = await self.store.db.fetchone(
            "SELECT * FROM location_state WHERE location_ref = ? AND campaign_id = ?",
            (location_ref, campaign_id),
        )
        if row is None:
            return LocationStateData(
                location_ref=location_ref,
                campaign_id=campaign_id,
            )
        weather: Weather | None = None
        if row["weather"]:
            try:
                weather = Weather.model_validate(json.loads(row["weather"]))
            except Exception:
                weather = None
        return LocationStateData(
            location_ref=location_ref,
            campaign_id=campaign_id,
            weather=weather,
            time_of_day=row["time_of_day"] or "",
            occupants=[
                o
                for o in (json.loads(row["occupants"]) if row["occupants"] else [])
                if isinstance(o, str)
            ],
            condition=row["condition"] or "",
            transient_features=[
                t
                for t in (
                    json.loads(row["transient_features"]) if row["transient_features"] else []
                )
                if isinstance(t, str)
            ],
            updated_at_turn=row["updated_at_turn"],
        )

    async def update_location_state(
        self,
        location_ref: str,
        campaign_id: CampaignId,
        patch: dict,
        *,
        source: str = "user",
        turn_id: str | None = None,
    ) -> LocationStateData:
        from grimoire.types.state import DeltaKind, StateDelta

        current = await self.get_location_state(location_ref, campaign_id)
        merged = current.model_dump()
        for k, v in (patch or {}).items():
            merged[k] = v

        weather_value = merged.get("weather")
        weather_json: str | None
        if weather_value is None:
            weather_json = None
        elif isinstance(weather_value, str):
            weather_json = weather_value
        else:
            weather_json = json.dumps(weather_value, default=str)

        after = {
            "location_ref": location_ref,
            "campaign_id": campaign_id,
            "weather": weather_json,
            "time_of_day": merged.get("time_of_day") or "",
            "occupants": json.dumps(merged.get("occupants") or []),
            "condition": merged.get("condition") or "",
            "transient_features": json.dumps(merged.get("transient_features") or []),
            "updated_at_turn": turn_id or merged.get("updated_at_turn"),
        }
        delta = StateDelta(
            kind=DeltaKind.LOCATION_STATE_UPDATE,
            target_scope=Scope.CAMPAIGN_SQLITE,
            target_table="location_state",
            target_id=location_ref,
            after=after,
            confidence=1.0,
            source=source,
        )
        await self.store.apply_delta(
            delta=delta,
            source=source,
            turn_id=turn_id,
            campaign_id=campaign_id,
        )
        return await self.get_location_state(location_ref, campaign_id)

    # ------------------------------------------------------------------ #
    # Faction state (campaign-scoped, SQLite)
    # ------------------------------------------------------------------ #

    async def faction_state(
        self,
        faction_ref: str,
        campaign_id: CampaignId,
    ) -> FactionStateData:
        row = await self.store.db.fetchone(
            """
            SELECT * FROM faction_state
            WHERE faction_ref = ? AND campaign_id = ?
            """,
            (faction_ref, campaign_id),
        )
        if row is None:
            return FactionStateData(
                faction_ref=faction_ref,
                campaign_id=campaign_id,
            )
        return _faction_state_from_row(row)

    async def update_faction_state(
        self,
        faction_ref: str,
        campaign_id: CampaignId,
        patch: dict,
        *,
        source: str = "user",
        turn_id: str | None = None,
    ) -> FactionStateData:
        """§11 Route faction-state writes through apply_delta.

        Previous behaviour bypassed the delta log so undo/fork/retcon
        couldn't reverse a faction state change. The write now goes
        through ``apply_delta`` with kind=FACTION_STATE_UPDATE, matching
        every other long-lived state column.
        """
        from grimoire.types.state import DeltaKind, StateDelta

        existing = await self.faction_state(faction_ref, campaign_id)
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
        after = {
            "faction_ref": faction_ref,
            "campaign_id": campaign_id,
            "state": json.dumps(payload, sort_keys=True, default=str),
            "updated_at_turn": turn_id or now_iso(),
        }
        delta = StateDelta(
            kind=DeltaKind.FACTION_STATE_UPDATE,
            target_scope=Scope.CAMPAIGN_SQLITE,
            target_table="faction_state",
            target_id=faction_ref,
            after=after,
            confidence=1.0,
            source=source,
        )
        await self.store.apply_delta(
            delta=delta,
            source=source,
            turn_id=turn_id,
            campaign_id=campaign_id,
        )
        return await self.faction_state(faction_ref, campaign_id)

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
            # Character promotion has its own two-step propose / confirm
            # flow (see CharactersService.promote_to_library + the
            # /campaigns/{id}/characters/{eid}/promote-to-library endpoint
            # in api/campaigns.py). World-side promotion would skip those
            # safety rails, so we reject the kind here instead.
            raise WorldError(
                "character promotion is routed through CharactersService; "
                "POST /campaigns/{campaign_id}/characters/{entity_id}/promote-to-library"
            )
        return await self.library.promote_to_library(
            campaign_id, normalized, campaign_entity_id, target_world_id, source=source
        )


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _location_ref(world_id: str, asset_id: str) -> str:
    return f"library:worlds/{world_id}/locations/{asset_id}"


def _is_entity_ref(value: str) -> bool:
    return isinstance(value, str) and value.startswith("library:worlds/")


def _parse_location_ref(ref: str) -> tuple[str, str] | None:
    """Parse 'library:worlds/<world_id>/locations/<asset_id>' → (world_id, asset_id)."""
    if not isinstance(ref, str):
        return None
    stripped = ref.removeprefix("library:")
    parts = stripped.split("/")
    try:
        world_idx = parts.index("worlds")
        loc_idx = parts.index("locations")
        return parts[world_idx + 1], parts[loc_idx + 1]
    except (ValueError, IndexError):
        return None


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


def _monster_from_entity(ent: LibraryEntity) -> Monster:
    fm = ent.frontmatter or {}
    try:
        category = MonsterCategory(fm.get("category") or "other")
    except ValueError:
        category = MonsterCategory.OTHER
    return Monster(
        world_id=ent.world_id or "",
        id=ent.asset_id,
        name=ent.name,
        category=category,
        aliases=list(fm.get("aliases") or []),
        tags=list(ent.tags or fm.get("tags") or []),
        threat_level=str(fm.get("threat_level") or ""),
        habitat=[str(x) for x in (fm.get("habitat") or [])],
        abilities=[str(x) for x in (fm.get("abilities") or [])],
        weaknesses=[str(x) for x in (fm.get("weaknesses") or [])],
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


def _build_haystack(text: str, *, scan_depth: int | None) -> str:
    """Return the slice of ``text`` an entry should scan.

    ``scan_depth`` is interpreted in *lines* — useful when the caller
    passes a multi-post haystack joined with newlines:

    * ``None``: scan the whole text.
    * ``0``: scan nothing — paired with ``constant=True`` for entries
      that fire regardless of context.
    * ``> 0``: scan only the last N lines.
    """
    if scan_depth is None:
        return text
    if scan_depth <= 0:
        return ""
    lines = text.splitlines()
    if len(lines) <= scan_depth:
        return text
    return "\n".join(lines[-scan_depth:])


def _primary_keyword_match(entry: LoreEntry, haystack: str, min_length: int) -> bool:
    if not entry.keywords:
        return False
    text = haystack if entry.case_sensitive else haystack.lower()
    for kw in entry.keywords:
        needle = kw.strip()
        if not entry.case_sensitive:
            needle = needle.lower()
        if len(needle) < min_length:
            continue
        if entry.match_whole_words:
            pattern = rf"\b{re.escape(needle)}\b"
            flags = 0 if entry.case_sensitive else re.IGNORECASE
            if re.search(pattern, haystack, flags):
                return True
        else:
            if needle in text:
                return True
    return False


def _evaluate_selective_logic(entry: LoreEntry, haystack: str) -> bool:
    """Evaluate ``selective_logic`` over ``secondary_keys``.

    Empty ``secondary_keys`` is handled by the caller — when there are
    no secondary keys the requirement is satisfied trivially regardless
    of the logic.
    """
    text = haystack if entry.case_sensitive else haystack.lower()
    matches: list[bool] = []
    for key in entry.secondary_keys:
        needle = key.strip()
        if not entry.case_sensitive:
            needle = needle.lower()
        if not needle:
            matches.append(False)
            continue
        if entry.match_whole_words:
            flags = 0 if entry.case_sensitive else re.IGNORECASE
            matches.append(bool(re.search(rf"\b{re.escape(needle)}\b", haystack, flags)))
        else:
            matches.append(needle in text)
    if entry.selective_logic == SelectiveLogic.AND_ANY:
        return any(matches)
    if entry.selective_logic == SelectiveLogic.AND_ALL:
        return all(matches)
    if entry.selective_logic == SelectiveLogic.NOT_ANY:
        return not any(matches)
    if entry.selective_logic == SelectiveLogic.NOT_ALL:
        return not all(matches)
    return True


def _probability_check(entry: LoreEntry, turn_id: str | None) -> bool:
    """Deterministic dice roll seeded on ``(entry.id, turn_id)``.

    Same pair → same roll, so a given entry consistently fires or skips
    within a single turn regardless of how often the algorithm runs.
    """
    if entry.probability >= 100:
        return True
    if entry.probability <= 0:
        return False
    digest = hashlib.sha256(f"{entry.id}::{turn_id or ''}".encode()).digest()
    roll = int.from_bytes(digest[:4], "big") % 100
    return roll < entry.probability


def _lore_from_entity(ent: LibraryEntity) -> LoreEntry:
    fm = ent.frontmatter or {}
    kwargs: dict[str, Any] = dict(
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
    # Extended fields are forwarded when present so frontmatter Pydantic
    # validation catches malformed enum values rather than silently using
    # the default. Missing keys fall back to LoreEntry's defaults.
    for key in (
        "secondary_keys",
        "selective_logic",
        "constant",
        "enabled",
        "case_sensitive",
        "match_whole_words",
        "priority",
        "probability",
        "position",
        "at_depth",
        "scan_depth",
        "comment",
        "import_source",
    ):
        if key in fm and fm[key] is not None:
            kwargs[key] = fm[key]
    return LoreEntry(**kwargs)


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
            deadline=parse_iso_datetime(g.get("deadline")),
        )
        for g in (decoded.get("goals") or [])
        if isinstance(g, dict)
    ]
    return FactionStateData(
        faction_ref=row["faction_ref"],
        campaign_id=row["campaign_id"],
        goals=goals,
        resources=dict(decoded.get("resources") or {}),
        current_focus=str(decoded.get("current_focus") or ""),
        public_perception=str(decoded.get("public_perception") or ""),
        secrets=[str(s) for s in (decoded.get("secrets") or [])],
        updated_at_turn=row["updated_at_turn"],
    )
