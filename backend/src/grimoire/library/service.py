"""Concrete Library service.

Wraps :class:`grimoire.state_store.StateStore` to provide the spec 18 surface.
The State Store owns SQLite mutations and file mediation; this service
translates between the file-shaped writes the store understands and the
typed values (``LibraryEntity``, ``WorldMeta``, ``Greeting``,
``ResolvedEntity``) the rest of the system consumes.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from grimoire.library.errors import (
    LibraryConflictError,
    LibraryError,
    LibraryNotFoundError,
    PromotionError,
)
from grimoire.state_store import StateStore
from grimoire.state_store.indexers import make_library_id
from grimoire.state_store.paths import parse_library_id
from grimoire.types.common import EntityKind
from grimoire.types.composition import (
    CampaignRef,
    Composition,
    Greeting,
    LibraryEntity,
    ResolutionLayer,
    ResolutionSource,
    ResolvedEntity,
    UpgradeReport,
    WorldMeta,
    WorldRef,
)

# Entity kinds that live inside a world directory.
_World_ENTITY_KINDS: frozenset[str] = frozenset(
    {"character", "item", "location", "lore", "faction", "greeting"}
)

_DIR_TO_KIND: dict[str, str] = {
    "characters": "character",
    "items": "item",
    "locations": "location",
    "lore": "lore",
    "factions": "faction",
    "greetings": "greeting",
}


def _normalize_kind(kind: EntityKind | str) -> str:
    if isinstance(kind, EntityKind):
        return kind.value
    if kind in _DIR_TO_KIND:
        return _DIR_TO_KIND[kind]
    return kind


def _include_to_kinds(include: list[str] | None) -> set[str] | None:
    """Translate a ``WorldRef.include`` list (directory names) into kinds.

    Returns ``None`` when the include is missing (``None``), meaning "include
    every kind" per spec 18. An empty list ``[]`` is preserved as an empty
    set, meaning "include nothing" — distinct from "all kinds", so a wizard
    that uncheck-all-kinds excludes the world rather than (silently)
    including everything.
    """
    if include is None:
        return None
    out: set[str] = set()
    for entry in include:
        out.add(_DIR_TO_KIND.get(entry, entry))
    return out


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _entity_from_row(row: dict) -> LibraryEntity:
    """Project a ``library_index`` row dict into a :class:`LibraryEntity`."""
    frontmatter = row.get("frontmatter") or {}
    try:
        kind = EntityKind(row["kind"])
    except ValueError as exc:
        raise LibraryError(f"unknown library kind {row['kind']!r}") from exc
    return LibraryEntity(
        id=row["id"],
        world_id=row.get("world_id"),
        kind=kind,
        asset_id=row["asset_id"],
        name=row.get("name") or row["asset_id"],
        path=row.get("path") or "",
        frontmatter=frontmatter,
        body=row.get("body") or "",
        body_compressed=row.get("body_compressed"),
        tags=list(row.get("tags") or []),
        keywords=list(row.get("keywords") or []),
        file_mtime=_parse_iso(row.get("file_mtime")),
        content_hash=row.get("content_hash") or "",
        indexed_at=_parse_iso(row.get("indexed_at")),
        version=int(row.get("version") or 0),
    )


def _world_meta_from_row(row: dict) -> WorldMeta:
    fm = row.get("frontmatter") or {}
    return WorldMeta(
        id=fm.get("id") or row.get("asset_id") or "",
        name=fm.get("name") or row.get("name") or row.get("asset_id") or "",
        description=fm.get("description") or "",
        tags=list(fm.get("tags") or row.get("tags") or []),
        genre=fm.get("genre") or "",
        calendar=fm.get("calendar") or {},
        atmosphere=fm.get("atmosphere") or {},
        defaults=fm.get("defaults") or {},
        version=int(fm.get("version") or row.get("version") or 0),
    )


def _greeting_from_row(row: dict) -> Greeting:
    fm = row.get("frontmatter") or {}
    return Greeting(
        id=fm.get("id") or row.get("asset_id") or "",
        world_id=row.get("world_id") or "",
        name=fm.get("name") or row.get("name") or row.get("asset_id") or "",
        starting_location=fm.get("starting_location"),
        starting_time=fm.get("starting_time"),
        present_characters=list(fm.get("present_characters") or []),
        pov_character=fm.get("pov_character"),
        mood=fm.get("mood") or "",
        body=row.get("body") or "",
        tags=list(fm.get("tags") or row.get("tags") or []),
    )


class LibraryService:
    """Concrete Library implementing the spec 18 surface.

    Construct with an initialized :class:`StateStore`. The watcher (task #9)
    is responsible for keeping the index in sync with the filesystem; this
    service only does file-mediated writes through the store.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ #
    # Discovery / listing
    # ------------------------------------------------------------------ #

    async def list_worlds(self) -> list[WorldMeta]:
        rows = await self.store.db.fetchall(
            "SELECT * FROM library_index WHERE kind = 'world' ORDER BY name"
        )
        return [_world_meta_from_row(_normalize_row(row)) for row in rows]

    async def get_world(self, world_id: str) -> WorldMeta:
        library_id = make_library_id(world_id, "world", world_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"world {world_id!r} not found")
        return _world_meta_from_row(row)

    async def list_in_world(self, world_id: str, kind: EntityKind | str) -> list[LibraryEntity]:
        normalized = _normalize_kind(kind)
        rows = await self.store.list_library_in_world(world_id, normalized)
        return [_entity_from_row(row) for row in rows]

    async def get_entity(
        self, world_id: str, kind: EntityKind | str, entity_id: str
    ) -> LibraryEntity:
        normalized = _normalize_kind(kind)
        library_id = make_library_id(world_id, normalized, entity_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"entity {kind}/{entity_id} not found in world {world_id!r}")
        return _entity_from_row(row)

    # ------------------------------------------------------------------ #
    # Top-level assets
    # ------------------------------------------------------------------ #

    async def list_style_guides(self) -> list[LibraryEntity]:
        rows = await self.store.db.fetchall(
            "SELECT * FROM library_index WHERE kind = 'style_guide' ORDER BY name"
        )
        return [_entity_from_row(_normalize_row(row)) for row in rows]

    async def list_image_presets(self) -> list[LibraryEntity]:
        rows = await self.store.db.fetchall(
            "SELECT * FROM library_index WHERE kind = 'image_preset' ORDER BY name"
        )
        return [_entity_from_row(_normalize_row(row)) for row in rows]

    async def get_style_guide(self, id: str) -> LibraryEntity:
        library_id = f"style-guides/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"style guide {id!r} not found")
        return _entity_from_row(row)

    async def create_style_guide(
        self,
        id: str,
        *,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        pacing: list[str] | None = None,
        voice: list[str] | None = None,
        themes: list[str] | None = None,
        avoid: list[str] | None = None,
        source: str = "user",
    ) -> LibraryEntity:
        library_id = f"style-guides/{id}"
        existing = await self.store.get_library_entity(library_id)
        if existing is not None:
            raise LibraryConflictError(f"style guide {id!r} already exists")
        frontmatter: dict[str, Any] = {"id": id, "name": name or id}
        if description:
            frontmatter["description"] = description
        if tags:
            frontmatter["tags"] = list(tags)
        body = _render_style_guide_body(
            name=name or id,
            pacing=pacing or [],
            voice=voice or [],
            themes=themes or [],
            avoid=avoid or [],
        )
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=frontmatter,
            body=body,
            source=source,
        )
        return await self.get_style_guide(id)

    async def parse_style_guide(self, id: str) -> dict[str, Any]:
        """Return the editable shape of a style guide for the edit form."""
        entity = await self.get_style_guide(id)
        parsed = _parse_style_guide_body(entity.body)
        fm = entity.frontmatter or {}
        return {
            "id": entity.asset_id,
            "name": entity.name,
            "description": fm.get("description") or "",
            "tags": list(entity.tags),
            "intro": parsed["intro"],
            "pacing": parsed["pacing"],
            "voice": parsed["voice"],
            "themes": parsed["themes"],
            "avoid": parsed["avoid"],
            "extra_sections": parsed["extra_sections"],
        }

    async def update_style_guide(
        self,
        id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        pacing: list[str] | None = None,
        voice: list[str] | None = None,
        themes: list[str] | None = None,
        avoid: list[str] | None = None,
        source: str = "user",
    ) -> LibraryEntity:
        existing = await self.get_style_guide(id)
        parsed = _parse_style_guide_body(existing.body)
        fm = dict(existing.frontmatter or {})
        new_name = name if name is not None else (fm.get("name") or existing.name or id)
        fm["id"] = id
        fm["name"] = new_name
        if description is not None:
            if description:
                fm["description"] = description
            else:
                fm.pop("description", None)
        if tags is not None:
            if tags:
                fm["tags"] = list(tags)
            else:
                fm.pop("tags", None)
        body = _render_style_guide_body(
            name=new_name,
            pacing=pacing if pacing is not None else parsed["pacing"],
            voice=voice if voice is not None else parsed["voice"],
            themes=themes if themes is not None else parsed["themes"],
            avoid=avoid if avoid is not None else parsed["avoid"],
            intro=parsed["intro"],
            extra_sections=parsed["extra_sections"],
        )
        await self.store.write_library_file(
            library_id=f"style-guides/{id}",
            frontmatter=fm,
            body=body,
            source=source,
        )
        return await self.get_style_guide(id)

    async def get_image_preset(self, id: str) -> LibraryEntity:
        library_id = f"image-presets/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"image preset {id!r} not found")
        return _entity_from_row(row)

    # ------------------------------------------------------------------ #
    # Greetings
    # ------------------------------------------------------------------ #

    async def list_greetings(self, world_id: str) -> list[Greeting]:
        rows = await self.store.list_library_in_world(world_id, "greeting")
        return [_greeting_from_row(row) for row in rows]

    async def get_greeting(self, world_id: str, id: str) -> Greeting:
        library_id = make_library_id(world_id, "greeting", id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"greeting {id!r} not found in world {world_id!r}")
        return _greeting_from_row(row)

    # ------------------------------------------------------------------ #
    # Cross-world variants
    # ------------------------------------------------------------------ #

    async def variants_of(self, asset_id: str, kind: EntityKind | str) -> list[LibraryEntity]:
        normalized = _normalize_kind(kind)
        rows = await self.store.variants_of(asset_id, normalized)
        return [_entity_from_row(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    async def create_world(self, id: str, meta: dict, *, source: str = "user") -> WorldMeta:
        frontmatter = dict(meta or {})
        frontmatter.setdefault("id", id)
        frontmatter.setdefault("name", id)
        frontmatter.setdefault("version", int(frontmatter.get("version") or 1))
        library_id = make_library_id(id, "world", id)
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=frontmatter,
            body="",
            source=source,
        )
        return await self.get_world(id)

    async def create_entity(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        frontmatter: dict,
        body: str,
        *,
        source: str = "user",
    ) -> LibraryEntity:
        normalized = _normalize_kind(kind)
        if normalized not in _World_ENTITY_KINDS:
            raise LibraryError(
                f"create_entity does not handle kind {normalized!r}; "
                "use create_world / write top-level assets via store"
            )
        fm = dict(frontmatter or {})
        fm.setdefault("id", entity_id)
        fm.setdefault("name", fm.get("name") or entity_id)
        library_id = make_library_id(world_id, normalized, entity_id)
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=fm,
            body=body or "",
            source=source,
        )
        return await self.get_entity(world_id, normalized, entity_id)

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
        normalized = _normalize_kind(kind)
        library_id = make_library_id(world_id, normalized, entity_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(
                f"cannot update missing entity {kind}/{entity_id} in {world_id!r}"
            )
        new_frontmatter = dict(row.get("frontmatter") or {})
        if frontmatter_patch:
            new_frontmatter.update(frontmatter_patch)
        new_body = body if body is not None else (row.get("body") or "")
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=new_frontmatter,
            body=new_body,
            source=source,
        )
        return await self.get_entity(world_id, normalized, entity_id)

    async def delete_entity(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        *,
        source: str = "user",
    ) -> None:
        normalized = _normalize_kind(kind)
        library_id = make_library_id(world_id, normalized, entity_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(
                f"cannot delete missing entity {kind}/{entity_id} in {world_id!r}"
            )
        await self.store.delete_library_file(library_id=library_id, source=source)

    # ------------------------------------------------------------------ #
    # Promotion
    # ------------------------------------------------------------------ #

    async def promote_to_library(
        self,
        campaign_id: str,
        entity_kind: EntityKind | str,
        campaign_entity_id: str,
        target_world_id: str,
        *,
        source: str = "user",
    ) -> str:
        """Promote a campaign-local emergent entity into the library.

        Reads the emergent file, writes it as a library entity in
        ``target_world_id``, and returns the library path. The original
        emergent file is left in place; callers may opt to delete it.
        Characters are not yet supported here — task #12 owns that flow.
        """
        normalized = _normalize_kind(entity_kind)
        if normalized not in _World_ENTITY_KINDS:
            raise PromotionError(
                f"cannot promote kind {normalized!r}; only world-scoped kinds supported"
            )
        emergent = await self.store.get_emergent(campaign_id, normalized, campaign_entity_id)
        if emergent is None:
            raise PromotionError(
                f"no emergent {normalized}/{campaign_entity_id} in campaign {campaign_id!r}"
            )
        frontmatter = dict(emergent.get("frontmatter") or {})
        frontmatter.setdefault("id", campaign_entity_id)
        library_id = make_library_id(target_world_id, normalized, campaign_entity_id)
        result = await self.store.write_library_file(
            library_id=library_id,
            frontmatter=frontmatter,
            body=emergent.get("body") or "",
            source=f"{source}:promotion",
            campaign_id=campaign_id,
        )
        return str(result.path)

    # ------------------------------------------------------------------ #
    # Composition
    # ------------------------------------------------------------------ #

    async def get_composition(self, campaign_id: str) -> Composition:
        camp_row = await self.store.db.fetchone(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if camp_row is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} not found")

        refs_raw = await self.store.list_world_refs(campaign_id)
        refs = [
            WorldRef(
                world_id=r["world_id"],
                priority=int(r["priority"]),
                include=list(r.get("include") or []),
                bound_at_version=int(r.get("bound_at_version") or 0),
                track_latest=bool(r.get("track_latest")),
            )
            for r in refs_raw
        ]
        return Composition(
            worlds=refs,
            mechanics=camp_row["mechanics_module"],
            style_guide_id=camp_row["style_guide_id"],
            image_preset_id=camp_row["image_preset_id"],
            inline_style_guide=camp_row["inline_style_guide"],
            content_boundaries=camp_row["content_boundaries"],
        )

    async def set_composition(self, campaign_id: str, composition: Composition) -> None:
        camp_row = await self.store.db.fetchone(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if camp_row is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} not found")

        await self.store.upsert_campaign(
            campaign_id=campaign_id,
            name=camp_row["name"],
            description=camp_row["description"],
            mechanics_module=composition.mechanics,
            style_guide_id=composition.style_guide_id,
            image_preset_id=composition.image_preset_id,
            inline_style_guide=composition.inline_style_guide,
            content_boundaries=composition.content_boundaries,
            greeting_id=camp_row["greeting_id"],
            config=_maybe_json(camp_row["config"]),
        )

        existing = {r["world_id"] for r in await self.store.list_world_refs(campaign_id)}
        desired_ids: set[str] = set()
        for ref in composition.worlds:
            desired_ids.add(ref.world_id)
            await self.store.upsert_world_ref(
                campaign_id=campaign_id,
                world_id=ref.world_id,
                priority=ref.priority,
                include=list(ref.include or []),
                track_latest=ref.track_latest,
                bound_at_version=(ref.bound_at_version if ref.bound_at_version else None),
            )

        for old in existing - desired_ids:
            await self.store.db.execute(
                """
                DELETE FROM campaign_world_refs
                WHERE campaign_id = ? AND world_id = ?
                """,
                (campaign_id, old),
            )
            await self.store.db.execute(
                """
                DELETE FROM library_snapshots
                WHERE campaign_id = ? AND library_id IN (
                  SELECT id FROM library_index WHERE world_id = ?
                )
                """,
                (campaign_id, old),
            )

    async def upgrade_world_ref(self, campaign_id: str, world_id: str) -> UpgradeReport:
        before_max = await self.store.db.fetchone(
            "SELECT bound_at_version FROM campaign_world_refs "
            "WHERE campaign_id = ? AND world_id = ?",
            (campaign_id, world_id),
        )
        if before_max is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} does not bind world {world_id!r}")
        from_version = int(before_max["bound_at_version"] or 0)

        report = await self.store.upgrade_world_ref(campaign_id=campaign_id, world_id=world_id)
        max_row = await self.store.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
            (world_id,),
        )
        to_version = int((max_row["v"] if max_row else 0) or 0)
        return UpgradeReport(
            campaign_id=campaign_id,
            world_id=world_id,
            from_version=from_version,
            to_version=to_version,
            changed_entities=sorted(report.diff.keys()),
            diff=report.diff,
        )

    # ------------------------------------------------------------------ #
    # Resolution cascade
    # ------------------------------------------------------------------ #

    async def resolve(self, entity_id: str, campaign_id: str) -> ResolvedEntity:
        """Resolve an entity through the campaign cascade.

        ``entity_id`` is either a composite ``library_id``
        (``worlds/<world>/<kind>/<id>``) or a campaign-local
        ``emergent/<kind>/<id>`` reference. Walks emergent → override →
        snapshot (pinned) → live index → fail.
        """
        target_kind, world_id, asset_id, is_emergent_only = _parse_resolve_ref(entity_id)

        branch_id = f"{campaign_id}:main"

        if is_emergent_only:
            data = await self.store.resolve_entity(
                campaign_id=campaign_id,
                branch_id=branch_id,
                kind=target_kind,
                asset_id=asset_id,
                world_id=None,
            )
            if data is None:
                raise LibraryNotFoundError(
                    f"emergent {target_kind}/{asset_id} not found in campaign {campaign_id!r}"
                )
            return _build_resolved(target_kind, world_id, asset_id, data)

        # Worlds ref path: check emergent first (campaign-shadowed name),
        # then the store's override/snapshot/live cascade.
        emergent = await self.store.get_emergent(campaign_id, target_kind, asset_id)
        if emergent is not None:
            data = {
                "source": "campaign-emergent",
                "frontmatter": emergent.get("frontmatter") or {},
                "body": emergent.get("body") or "",
            }
            return _build_resolved(target_kind, world_id, asset_id, data)

        data = await self.store.resolve_entity(
            campaign_id=campaign_id,
            branch_id=branch_id,
            kind=target_kind,
            asset_id=asset_id,
            world_id=world_id,
        )
        if data is None:
            raise LibraryNotFoundError(f"cannot resolve {entity_id!r} for campaign {campaign_id!r}")
        return _build_resolved(target_kind, world_id, asset_id, data)

    # ------------------------------------------------------------------ #
    # Dependents
    # ------------------------------------------------------------------ #

    async def dependents(
        self, world_id: str, kind: EntityKind | str, entity_id: str
    ) -> list[CampaignRef]:
        """Return campaigns that reference ``world_id``.

        v1: a campaign is a dependent of any entity in a world it
        composes. Fine-grained per-entity dependency tracking (e.g. via
        override files) is a v2 refinement.
        """
        rows = await self.store.db.fetchall(
            """
            SELECT c.id AS id, c.name AS name
            FROM campaigns c
            JOIN campaign_world_refs r ON r.campaign_id = c.id
            WHERE r.world_id = ?
            ORDER BY c.name
            """,
            (world_id,),
        )
        return [CampaignRef(id=row["id"], name=row["name"]) for row in rows]

    # ------------------------------------------------------------------ #
    # Composition-aware listing (used by World/Context Builder)
    # ------------------------------------------------------------------ #

    async def list_for_composition(
        self,
        campaign_id: str,
        kind: EntityKind | str,
    ) -> list[LibraryEntity]:
        """Return entities of ``kind`` reachable through a campaign's composition.

        Walks world refs in priority order, respecting each ref's
        ``include`` filter. Higher-priority refs win when asset ids
        collide.
        """
        normalized = _normalize_kind(kind)
        refs = await self.store.list_world_refs(campaign_id)
        refs.sort(key=lambda r: int(r.get("priority") or 0))

        seen: dict[str, LibraryEntity] = {}
        for ref in refs:
            include_kinds = _include_to_kinds(ref.get("include"))
            if include_kinds is not None and normalized not in include_kinds:
                continue
            rows = await self.store.list_library_in_world(ref["world_id"], normalized)
            for row in rows:
                asset = row["asset_id"]
                if asset in seen:
                    continue
                seen[asset] = _entity_from_row(row)
        return list(seen.values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_row(row: Any) -> dict:
    """Make ``store.db.fetchall`` rows look like ``store.get_library_entity`` dicts.

    ``StateStore.get_library_entity`` returns a fully-decoded dict (lists, JSON
    parsed); ``db.fetchall`` returns ``aiosqlite.Row`` with raw text. This
    helper normalizes so the projection helpers can handle either.
    """
    if isinstance(row, dict):
        return row
    raw = dict(row)
    raw["frontmatter"] = _maybe_json(raw.get("frontmatter"))
    raw["tags"] = _maybe_json(raw.get("tags")) or []
    raw["keywords"] = _maybe_json(raw.get("keywords")) or []
    return raw


def _render_style_guide_body(
    *,
    name: str,
    pacing: list[str],
    voice: list[str],
    themes: list[str],
    avoid: list[str],
    intro: str = "",
    extra_sections: list[tuple[str, str]] | None = None,
) -> str:
    parts: list[str] = [f"# {name}"]
    intro_text = (intro or "").strip()
    if intro_text:
        parts.append(intro_text)
    for heading, items in (
        ("Pacing", pacing),
        ("Voice", voice),
        ("Themes", themes),
        ("Avoid", avoid),
    ):
        bullets = [item.strip() for item in (items or []) if item and item.strip()]
        if not bullets:
            continue
        section = f"## {heading}\n" + "\n".join(f"- {b}" for b in bullets)
        parts.append(section)
    for heading, raw in extra_sections or []:
        body_text = (raw or "").strip()
        if not body_text:
            continue
        parts.append(f"## {heading}\n{body_text}")
    return "\n\n".join(parts) + "\n"


_KNOWN_SECTIONS: tuple[str, ...] = ("Pacing", "Voice", "Themes", "Avoid")


def _parse_style_guide_body(body: str) -> dict[str, Any]:
    """Split a style-guide markdown body into the structured shape the form edits.

    Returns ``{intro, pacing, voice, themes, avoid, extra_sections}`` where the
    four named sections are bullet-item lists and ``extra_sections`` preserves
    any other ``## Heading`` blocks verbatim so a round-trip edit doesn't
    silently drop hand-authored prose.
    """
    lines = (body or "").splitlines()
    cursor = 0
    # Skip a leading H1; we re-emit it from the entity name.
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    if cursor < len(lines) and lines[cursor].lstrip().startswith("# "):
        cursor += 1

    intro_lines: list[str] = []
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("## "):
        intro_lines.append(lines[cursor])
        cursor += 1
    intro = "\n".join(intro_lines).strip()

    sections: dict[str, list[str]] = {h: [] for h in _KNOWN_SECTIONS}
    extras: list[tuple[str, str]] = []
    while cursor < len(lines):
        heading_line = lines[cursor].lstrip()
        if not heading_line.startswith("## "):
            cursor += 1
            continue
        heading = heading_line[3:].strip()
        cursor += 1
        block: list[str] = []
        while cursor < len(lines) and not lines[cursor].lstrip().startswith("## "):
            block.append(lines[cursor])
            cursor += 1
        canonical = next((h for h in _KNOWN_SECTIONS if h.lower() == heading.lower()), None)
        if canonical is not None:
            bullets: list[str] = []
            for raw in block:
                stripped = raw.lstrip()
                if stripped.startswith("- ") or stripped.startswith("* "):
                    bullets.append(stripped[2:].strip())
            sections[canonical] = bullets
        else:
            extras.append((heading, "\n".join(block).strip()))
    return {
        "intro": intro,
        "pacing": sections["Pacing"],
        "voice": sections["Voice"],
        "themes": sections["Themes"],
        "avoid": sections["Avoid"],
        "extra_sections": extras,
    }


def _maybe_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _parse_resolve_ref(entity_id: str) -> tuple[str, str | None, str, bool]:
    """Return ``(kind, world_id, asset_id, emergent_only)`` for resolve()."""
    if not entity_id:
        raise LibraryError("empty entity_id")
    parts = entity_id.strip("/").split("/")
    # Campaign-local emergent shorthand: 'emergent/<kind>/<id>'.
    if parts[0] == "emergent" and len(parts) >= 3:
        kind = _normalize_kind(parts[1])
        return kind, None, parts[2], True
    ref = parse_library_id(entity_id)
    if ref.kind in {"world", "style_guide", "image_preset"}:
        raise LibraryError(
            f"resolve() does not handle top-level kind {ref.kind!r}; "
            "use get_world / get_style_guide / get_image_preset"
        )
    return ref.kind, ref.world_id, ref.asset_id, False


def _build_resolved(
    kind: str,
    world_id: str | None,
    asset_id: str,
    data: dict,
) -> ResolvedEntity:
    fm = data.get("frontmatter") or {}
    source = data.get("source") or "library-live"
    layer = _LAYER_BY_SOURCE.get(source, ResolutionLayer.LIBRARY_LIVE)
    chain = [
        ResolutionSource(
            layer=layer,
            scope=_SCOPE_BY_SOURCE.get(source, "library"),
            library_id=data.get("library_id"),
            world_id=world_id,
            version=data.get("version"),
            override_applied=bool(data.get("override")),
        )
    ]
    return ResolvedEntity(
        kind=EntityKind(kind),
        asset_id=asset_id,
        world_id=world_id,
        name=fm.get("name") or fm.get("title") or asset_id,
        frontmatter=fm,
        body=data.get("body") or "",
        source_chain=chain,
        overrides_applied=(["override"] if data.get("override") else []),
    )


_LAYER_BY_SOURCE = {
    "campaign-emergent": ResolutionLayer.EMERGENT,
    "campaign-override": ResolutionLayer.OVERRIDE,
    "library-snapshot": ResolutionLayer.LIBRARY_SNAPSHOT,
    "library-live": ResolutionLayer.LIBRARY_LIVE,
    "library-fallback": ResolutionLayer.LIBRARY_LIVE,
}

_SCOPE_BY_SOURCE = {
    "campaign-emergent": "campaign-local",
    "campaign-override": "campaign-file",
    "library-snapshot": "library",
    "library-live": "library",
    "library-fallback": "library",
}
