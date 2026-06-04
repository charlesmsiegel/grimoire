"""Character sheet CRUD, imports, and compressed-view rendering."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from grimoire.files import slugify
from grimoire.library import LibraryService
from grimoire.library.reclassify import _lore_entry_from_ingested, apply_mapping
from grimoire.state_store import StateStore
from grimoire.types.characters import (
    CapsuleDraft,
    Character,
    CharacterData,
    CharacterImage,
    CharacterImageKind,
    ImagePromptTemplate,
    ImportResult,
    IngestedCharacterCard,
    IngestedLoreEntry,
    IngestOptions,
    LoreOverride,
    StructuralRelationship,
    VoiceAnchor,
)
from grimoire.types.common import CampaignId, EntityKind
from grimoire.types.composition import LibraryEntity

from .errors import CharacterNotFoundError
from .imports import parse_plaintext
from .ingest import LLMEnrichCallable, enrich_with_llm, ingest_character_card_v2
from .view_cache import CharacterViewCache
from .views import render_capsule, render_compressed, render_full, render_voice_only

LLMCapsuleDrafter = Callable[[CharacterData], Awaitable[CapsuleDraft]]


class CharacterSheetManager:
    """Sheet CRUD, bulk import, and compressed-view rendering."""

    def __init__(
        self,
        *,
        library: LibraryService,
        store: StateStore,
        cache: CharacterViewCache,
        ingest_llm: LLMEnrichCallable | None = None,
        auto_capsule_llm: LLMCapsuleDrafter | None = None,
    ) -> None:
        self._library = library
        self._store = store
        self._cache = cache
        self._ingest_llm = ingest_llm
        self._auto_capsule_llm = auto_capsule_llm

    # ------------------------------------------------------------------
    # Library CRUD
    # ------------------------------------------------------------------

    async def list_in_world(self, world_id: str) -> list[Character]:
        rows = await self._library.list_in_world(world_id, "character")
        return [character_from_entity(r) for r in rows]

    async def get(self, world_id: str, character_id: str) -> Character:
        ent = await self._library.get_entity(world_id, "character", character_id)
        return character_from_entity(ent)

    async def create(self, world_id: str, payload: CharacterData) -> Character:
        fm = frontmatter_from_payload(payload)
        ent = await self._library.create_entity(
            world_id, "character", payload.id, fm, payload.body, source="characters:create"
        )
        return character_from_entity(ent)

    async def update(self, world_id: str, character_id: str, patch: dict) -> Character:
        body = patch.pop("body", None)
        ent = await self._library.update_entity(
            world_id,
            "character",
            character_id,
            frontmatter_patch=patch or None,
            body=body,
            source="characters:update",
        )
        self._cache.view_invalidate()
        return character_from_entity(ent)

    async def delete(self, world_id: str, character_id: str) -> None:
        await self._library.delete_entity(
            world_id, "character", character_id, source="characters:delete"
        )
        self._cache.view_invalidate()

    # ------------------------------------------------------------------
    # Emergent + override
    # ------------------------------------------------------------------

    async def create_emergent(
        self,
        campaign_id: CampaignId,
        payload: CharacterData,
        *,
        source: str = "characters:emergent",
    ) -> str:
        fm = frontmatter_from_payload(payload)
        await self._store.write_emergent(
            campaign_id=campaign_id,
            kind="character",
            entity_id=payload.id,
            frontmatter=fm,
            body=payload.body,
            source=source,
        )
        if self._auto_capsule_llm is not None and _is_sparse_payload(payload):
            try:
                draft = await self._auto_capsule_llm(payload)
            except Exception:
                draft = None
            if draft is not None:
                patch = _capsule_draft_to_patch(draft)
                if patch:
                    await self.update_emergent(
                        campaign_id,
                        payload.id,
                        patch,
                        source="characters:auto-capsule",
                    )
        return f"campaign:emergent/character/{payload.id}"

    async def update_emergent(
        self,
        campaign_id: CampaignId,
        character_id: str,
        patch: dict,
        *,
        source: str = "characters:emergent-update",
    ) -> Character:
        existing = await self._store.get_emergent(campaign_id, "character", character_id)
        if existing is None:
            raise CharacterNotFoundError(
                f"no emergent character {character_id!r} in campaign {campaign_id!r}"
            )
        fm = dict(existing.get("frontmatter") or {})
        body = patch.pop("body", existing.get("body") or "")
        fm.update(patch or {})
        fm["id"] = character_id
        await self._store.write_emergent(
            campaign_id=campaign_id,
            kind="character",
            entity_id=character_id,
            frontmatter=fm,
            body=body,
            source=source,
        )
        emergent_ref = f"campaign:emergent/character/{character_id}"
        self._cache.view_invalidate(ref=emergent_ref, campaign_id=campaign_id)
        return character_from_frontmatter(fm, body, world_id=None)

    async def delete_emergent(self, campaign_id: CampaignId, character_id: str) -> None:
        from grimoire.state_store.paths import emergent_path

        target = emergent_path(self._store.data_root, campaign_id, "character", character_id)
        if not target.exists():
            raise CharacterNotFoundError(
                f"no emergent character {character_id!r} in campaign {campaign_id!r}"
            )
        target.unlink()
        emergent_ref = f"campaign:emergent/character/{character_id}"
        self._cache.view_invalidate(ref=emergent_ref, campaign_id=campaign_id)

    async def upsert_override(
        self,
        campaign_id: CampaignId,
        character_ref: str,
        patch: dict,
        *,
        source: str = "characters:override",
    ) -> None:
        from grimoire.state_store.indexers import make_library_id

        view = _parse_character_ref(character_ref)
        if view.is_emergent or view.world_id is None:
            from .errors import CharactersError

            raise CharactersError(f"cannot derive library_id from emergent ref {character_ref!r}")
        library_id = make_library_id(view.world_id, "character", view.asset_id)
        await self._store.write_override(
            campaign_id=campaign_id,
            library_id=library_id,
            patch=patch,
            source=source,
        )
        self._cache.view_invalidate(ref=character_ref, campaign_id=campaign_id)

    # ------------------------------------------------------------------
    # Compressed views
    # ------------------------------------------------------------------

    async def get_full_card(
        self, ref: str, campaign_id: CampaignId, resolved: Any, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "full", seed)
        if cached is not None:
            return cached
        rendered = render_full(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "full", seed, rendered)
        return rendered

    async def get_compressed_card(
        self, ref: str, campaign_id: CampaignId, resolved: Any, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "compressed", seed)
        if cached is not None:
            return cached
        rendered = render_compressed(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "compressed", seed, rendered)
        return rendered

    async def get_voice_only(
        self, ref: str, campaign_id: CampaignId, resolved: Any, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "voice_only", seed)
        if cached is not None:
            return cached
        rendered = render_voice_only(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "voice_only", seed, rendered)
        return rendered

    async def get_capsule(
        self, ref: str, campaign_id: CampaignId, resolved: Any, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "capsule", seed)
        if cached is not None:
            return cached
        rendered = render_capsule(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "capsule", seed, rendered)
        return rendered

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    async def import_sillytavern(
        self,
        card: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> ImportResult:
        ingested = await self._ingest(card, options=options)
        return await self._finalize_import(target_world_id, ingested, options=options)

    async def import_charx(
        self,
        charx_bytes: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> ImportResult:
        ingested = await self._ingest(charx_bytes, options=options)
        return await self._finalize_import(target_world_id, ingested, options=options)

    async def import_plaintext(self, text: str, target_world_id: str) -> ImportResult:
        data, warnings = parse_plaintext(text)
        return await self._finalize_import(
            target_world_id,
            IngestedCharacterCard(data=data, warnings=warnings),
        )

    async def import_character_card(
        self,
        payload: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> tuple[ImportResult, IngestedCharacterCard]:
        ingested = await self._ingest(payload, options=options)
        result = await self._finalize_import(target_world_id, ingested, options=options)
        return result, ingested

    async def _ingest(
        self,
        payload: bytes,
        *,
        options: IngestOptions | None,
    ) -> IngestedCharacterCard:
        opts = options or IngestOptions()
        ingested = ingest_character_card_v2(payload, options=opts)
        if opts.enrich_with_llm and self._ingest_llm is not None:
            ingested = await enrich_with_llm(ingested, self._ingest_llm, options=opts)
        return ingested

    async def add_character_image(
        self,
        world_id: str,
        character_id: str,
        image: CharacterImage,
        *,
        image_bytes: bytes | None = None,
        source: str = "characters:add-image",
    ) -> Character:
        ent = await self._library.get_entity(world_id, "character", character_id)
        existing = list(character_from_entity(ent).images)
        stored = image
        if image_bytes is not None:
            stored = await self._write_image_bytes(
                world_id=world_id,
                character_id=character_id,
                image=image,
                payload=image_bytes,
            )
        existing.append(stored)
        fm = dict(ent.frontmatter or {})
        fm["images"] = [_image_to_dict(img) for img in existing]
        updated = await self._library.update_entity(
            world_id,
            "character",
            character_id,
            frontmatter_patch=fm,
            body=None,
            source=source,
        )
        return character_from_entity(updated)

    async def _write_image_bytes(
        self,
        *,
        world_id: str,
        character_id: str,
        image: CharacterImage,
        payload: bytes,
    ) -> CharacterImage:
        from grimoire.state_store.paths import library_root, relative_to_root

        filename = image.path or f"{image.kind.value}.png"
        if "/" in filename:
            filename = filename.rsplit("/", 1)[-1]
        target_dir = (
            library_root(self._store.data_root) / "worlds" / world_id / "characters" / character_id
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_bytes(payload)
        return image.model_copy(update={"path": relative_to_root(self._store.data_root, target)})

    async def _finalize_import(
        self,
        target_world_id: str,
        ingested: IngestedCharacterCard,
        *,
        options: IngestOptions | None = None,
        lore_overrides: list[LoreOverride] | None = None,
    ) -> ImportResult:
        opts = options or IngestOptions()
        data = ingested.data
        result = ImportResult(warnings=list(ingested.warnings))
        try:
            existing = await self._library.get_entity(target_world_id, "character", data.id)
        except Exception:
            existing = None
        if existing is not None:
            result.skipped.append(data.id)
            result.warnings.append(
                f"character {data.id!r} already exists in {target_world_id!r}; not overwriting"
            )
            return result

        if ingested.avatar_bytes and data.images:
            avatar_index = next(
                (i for i, img in enumerate(data.images) if img.source == "embedded_avatar"),
                None,
            )
            if avatar_index is not None:
                placeholder = data.images[avatar_index]
                stored = await self._write_image_bytes(
                    world_id=target_world_id,
                    character_id=data.id,
                    image=placeholder,
                    payload=ingested.avatar_bytes,
                )
                images = list(data.images)
                images[avatar_index] = stored
                data = data.model_copy(update={"images": images})

        try:
            await self.create(target_world_id, data)
            result.created.append(data.id)
        except Exception as exc:
            result.errors.append(f"character {data.id!r}: {exc}")
            return result

        if opts.import_primary_greeting or opts.import_alternate_greetings:
            await self._write_greetings(
                target_world_id=target_world_id,
                char_slug=data.id,
                char_name=data.name,
                ingested=ingested,
                opts=opts,
                result=result,
            )
        if opts.import_character_book and ingested.lore_entries:
            await self._write_lore_entries(
                target_world_id=target_world_id,
                char_slug=data.id,
                ingested=ingested,
                result=result,
                lore_overrides=lore_overrides or [],
            )

        try:
            await self._write_import_report(
                target_world_id=target_world_id,
                ingested=ingested,
                result=result,
                opts=opts,
            )
        except Exception as exc:
            result.warnings.append(f"import report failed: {exc}")
        return result

    async def _write_greetings(
        self,
        *,
        target_world_id: str,
        char_slug: str,
        char_name: str,
        ingested: IngestedCharacterCard,
        opts: IngestOptions,
        result: ImportResult,
    ) -> None:
        for greeting in ingested.greetings:
            if greeting.is_primary and not opts.import_primary_greeting:
                continue
            if not greeting.is_primary and not opts.import_alternate_greetings:
                continue
            if greeting.is_primary:
                suffix = "default"
                kind = "sillytavern_first_mes"
                name = f"Default greeting from {char_name}"
            else:
                suffix = f"alt-{greeting.source_index:02d}"
                kind = "sillytavern_alternate_greeting"
                name = f"Alternate greeting {greeting.source_index} from {char_name}"
            base_id = f"{char_slug}--{suffix}"
            entity_id = await self._unique_id(target_world_id, "greeting", base_id, result)
            tags = ["imported", "from-card", char_slug]
            if not greeting.is_primary:
                tags.append("alternate-greeting")
            frontmatter = {
                "id": entity_id,
                "name": name,
                "present_characters": [char_slug],
                "tags": tags,
                "import_source": {
                    "kind": kind,
                    "card_asset_id": char_slug,
                    "source_index": greeting.source_index,
                },
            }
            try:
                await self._library.create_entity(
                    target_world_id,
                    "greeting",
                    entity_id,
                    frontmatter,
                    body=greeting.body,
                    source="characters:import",
                )
                result.created.append(f"greeting:{entity_id}")
            except Exception as exc:
                result.errors.append(f"greeting {entity_id!r}: {exc}")

    async def _write_lore_entries(
        self,
        *,
        target_world_id: str,
        char_slug: str,
        ingested: IngestedCharacterCard,
        result: ImportResult,
        lore_overrides: list[LoreOverride] = (),
    ) -> None:
        overrides_by_index = {o.source_index: o for o in lore_overrides}
        for entry in ingested.lore_entries:
            override = overrides_by_index.get(entry.source_index)
            target_kind = override.kind if override else "lore"

            if target_kind == "skip":
                result.warnings.append(f"lore entry {entry.source_index} skipped by user override")
                continue

            if target_kind == "lore":
                await self._write_one_lore_entry(
                    target_world_id=target_world_id,
                    char_slug=char_slug,
                    entry=entry,
                    result=result,
                )
                continue

            await self._promote_lore_entry(
                target_world_id=target_world_id,
                char_slug=char_slug,
                entry=entry,
                target_kind=EntityKind(target_kind),
                overrides=override.overrides if override else {},
                result=result,
            )

    async def _write_one_lore_entry(
        self,
        *,
        target_world_id: str,
        char_slug: str,
        entry: IngestedLoreEntry,
        result: ImportResult,
    ) -> None:
        entry_slug = _slug_for_lore_entry(entry, char_slug)
        base_id = f"{char_slug}--{entry_slug}"
        entity_id = await self._unique_id(target_world_id, "lore", base_id, result)
        frontmatter: dict[str, Any] = {
            "id": entity_id,
            "name": entry.name or entity_id,
            "title": entry.name or entity_id,
            "keywords": entry.keys,
            "secondary_keys": entry.secondary_keys,
            "selective_logic": entry.selective_logic,
            "constant": entry.constant,
            "enabled": entry.enabled,
            "case_sensitive": entry.case_sensitive,
            "match_whole_words": entry.match_whole_words,
            "priority": entry.priority,
            "probability": entry.probability,
            "position": entry.position,
            "comment": entry.comment,
            "tags": ["imported", "from-card", char_slug],
            "import_source": {
                "kind": "sillytavern_character_book",
                "card_asset_id": char_slug,
                "source_index": entry.source_index,
            },
        }
        if entry.at_depth is not None:
            frontmatter["at_depth"] = entry.at_depth
        if entry.scan_depth is not None:
            frontmatter["scan_depth"] = entry.scan_depth
        try:
            await self._library.create_entity(
                target_world_id,
                "lore",
                entity_id,
                frontmatter,
                body=entry.body,
                source="characters:import",
            )
            result.created.append(f"lore:{entity_id}")
        except Exception as exc:
            result.errors.append(f"lore {entity_id!r}: {exc}")

    async def _promote_lore_entry(
        self,
        *,
        target_world_id: str,
        char_slug: str,
        entry: IngestedLoreEntry,
        target_kind: EntityKind,
        overrides: dict[str, Any],
        result: ImportResult,
    ) -> None:
        proxy = _lore_entry_from_ingested(entry, world_id=target_world_id)
        fm, body, _kept, _dropped, _into_notes, warnings = apply_mapping(
            proxy, target_kind, overrides
        )
        entry_slug = _slug_for_lore_entry(entry, char_slug)
        base_id = f"{char_slug}--{entry_slug}"
        kind_str = target_kind.value
        entity_id = await self._unique_id(target_world_id, kind_str, base_id, result)
        fm["id"] = entity_id
        fm["import_source"] = {
            "kind": "sillytavern_character_book",
            "card_asset_id": char_slug,
            "source_index": entry.source_index,
        }
        try:
            await self._library.create_entity(
                target_world_id,
                kind_str,
                entity_id,
                fm,
                body=body,
                source="characters:import",
            )
            result.created.append(f"{kind_str}:{entity_id}")
            for w in warnings:
                result.warnings.append(f"{kind_str} {entity_id}: {w}")
        except Exception as exc:
            result.errors.append(f"{kind_str} {entity_id!r}: {exc}")

    async def _unique_id(
        self,
        world_id: str,
        kind: str,
        base_id: str,
        result: ImportResult,
    ) -> str:
        candidate = base_id
        for suffix in range(2, 100):
            try:
                existing = await self._library.get_entity(world_id, kind, candidate)
            except Exception:
                existing = None
            if existing is None:
                return candidate
            candidate = f"{base_id}-{suffix}"
            if suffix == 2:
                result.warnings.append(f"{kind} {base_id!r} already exists; trying suffix")
        result.warnings.append(f"{kind} {base_id!r} collided 99 times; using {candidate!r}")
        return candidate

    async def _write_import_report(
        self,
        *,
        target_world_id: str,
        ingested: IngestedCharacterCard,
        result: ImportResult,
        opts: IngestOptions,
    ) -> None:
        from grimoire.state_store.paths import library_root

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        char_slug = ingested.data.id
        report_dir = library_root(self._store.data_root) / "imports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{timestamp}-{char_slug}.md"
        lines: list[str] = [
            f"# Import: {ingested.data.name} ({char_slug})",
            "",
            f"- World: `{target_world_id}`",
            f"- Spec: `{ingested.spec or 'unknown'}`",
            f"- Imported at: `{timestamp}`",
            "",
            "## Created",
        ]
        for ref in result.created:
            lines.append(f"- `{ref}`")
        if result.skipped:
            lines += ["", "## Skipped"]
            for ref in result.skipped:
                lines.append(f"- `{ref}`")
        if result.errors:
            lines += ["", "## Errors"]
            for err in result.errors:
                lines.append(f"- {err}")
        if result.warnings:
            lines += ["", "## Warnings"]
            for warn in result.warnings:
                lines.append(f"- {warn}")
        lines += ["", "## Discarded inputs"]
        if ingested.system_prompt:
            lines.append(
                "- `system_prompt`: routed to campaign-scoped addendum "
                "(not written into the character body)."
            )
        if ingested.post_history_instructions:
            lines.append("- `post_history_instructions`: discarded (anti-pattern).")
        ext = ingested.extensions or {}
        for key in ("depth_prompt", "risuai", "chub", "regex_scripts"):
            if key in ext:
                lines.append(f"- `extensions.{key}`: discarded.")
        lines.append("")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        result.created.append(f"report:{report_path.relative_to(self._store.data_root).as_posix()}")


# ---------------------------------------------------------------------------
# Helpers (shared with service.py)
# ---------------------------------------------------------------------------


def character_from_entity(ent: LibraryEntity) -> Character:
    return character_from_frontmatter(ent.frontmatter, ent.body, world_id=ent.world_id)


def character_from_frontmatter(frontmatter: dict, body: str, *, world_id: str | None) -> Character:
    from grimoire.types.characters import CharacterRole

    fm: dict[str, Any] = dict(frontmatter or {})
    try:
        role = CharacterRole(fm.get("role") or "major_npc")
    except ValueError:
        role = CharacterRole.MAJOR_NPC
    voice_data = fm.get("voice") or {}
    voice = VoiceAnchor(
        summary=str(voice_data.get("summary") or ""),
        voice_register=str(voice_data.get("register") or voice_data.get("voice_register") or ""),
        samples=[str(s) for s in (voice_data.get("samples") or [])],
        speech_patterns=[str(s) for s in (voice_data.get("speech_patterns") or [])],
        address_terms=dict(voice_data.get("address_terms") or {}),
        dos=[str(s) for s in (voice_data.get("dos") or [])],
        donts=[str(s) for s in (voice_data.get("donts") or [])],
    )
    image_data = fm.get("image")
    image = (
        ImagePromptTemplate(
            base_prompt=str(image_data.get("base_prompt") or ""),
            negative_prompt=str(image_data.get("negative_prompt") or ""),
            canonical_seed=image_data.get("canonical_seed"),
            extra={
                k: v
                for k, v in image_data.items()
                if k not in {"base_prompt", "negative_prompt", "canonical_seed"}
            },
        )
        if isinstance(image_data, dict)
        else None
    )
    images = [_image_from_dict(img) for img in (fm.get("images") or []) if isinstance(img, dict)]
    structural = [
        StructuralRelationship(
            to_ref=str(r.get("to_ref") or ""),
            kind=str(r.get("kind") or ""),
            note=str(r.get("note") or ""),
        )
        for r in (fm.get("structural_relationships") or [])
        if isinstance(r, dict)
    ]
    household_raw = fm.get("household_id")
    household_id = str(household_raw) if household_raw else None
    return Character(
        id=str(fm.get("id") or ""),
        name=str(fm.get("name") or fm.get("id") or ""),
        role=role,
        world_id=world_id,
        aliases=[str(a) for a in (fm.get("aliases") or [])],
        age=fm.get("age"),
        tags=[str(t) for t in (fm.get("tags") or [])],
        role_tags=[str(t) for t in (fm.get("role_tags") or [])],
        voice=voice,
        image=image,
        images=images,
        structural_relationships=structural,
        description=str(fm.get("description") or ""),
        body=body or "",
        household_id=household_id,
    )


def frontmatter_from_payload(payload: CharacterData) -> dict:
    voice_dict = payload.voice.model_dump()
    voice_dict["register"] = voice_dict.pop("voice_register", "") or voice_dict.pop("register", "")
    fm: dict[str, Any] = {
        "id": payload.id,
        "name": payload.name,
        "role": payload.role.value,
        "aliases": list(payload.aliases),
        "tags": list(payload.tags),
        "role_tags": list(payload.role_tags),
        "description": payload.description,
        "voice": voice_dict,
    }
    if payload.age:
        fm["age"] = payload.age
    if payload.image is not None:
        img = payload.image
        fm["image"] = {
            "base_prompt": img.base_prompt,
            "negative_prompt": img.negative_prompt,
            "canonical_seed": img.canonical_seed,
            **(img.extra or {}),
        }
    if payload.images:
        fm["images"] = [_image_to_dict(img) for img in payload.images]
    if payload.structural_relationships:
        fm["structural_relationships"] = [
            {"to_ref": r.to_ref, "kind": r.kind, "note": r.note}
            for r in payload.structural_relationships
        ]
    if payload.household_id:
        fm["household_id"] = payload.household_id
    return fm


def _image_to_dict(image: CharacterImage) -> dict:
    out: dict[str, Any] = {
        "path": image.path,
        "kind": image.kind.value,
        "description": image.description,
        "tags": list(image.tags),
        "source": image.source,
    }
    if image.seed is not None:
        out["seed"] = image.seed
    if image.prompt_used:
        out["prompt_used"] = image.prompt_used
    if image.created_at is not None:
        out["created_at"] = image.created_at.isoformat()
    if image.extra:
        out["extra"] = dict(image.extra)
    return out


def _image_from_dict(raw: dict) -> CharacterImage:
    try:
        kind = CharacterImageKind(str(raw.get("kind") or "portrait"))
    except ValueError:
        kind = CharacterImageKind.PORTRAIT
    created_at_raw = raw.get("created_at")
    created_at: datetime | None = None
    if isinstance(created_at_raw, str) and created_at_raw:
        try:
            created_at = datetime.fromisoformat(created_at_raw)
        except ValueError:
            created_at = None
    elif isinstance(created_at_raw, datetime):
        created_at = created_at_raw
    return CharacterImage(
        path=str(raw.get("path") or ""),
        description=str(raw.get("description") or ""),
        kind=kind,
        tags=[str(t) for t in (raw.get("tags") or []) if t],
        seed=raw.get("seed"),
        prompt_used=str(raw.get("prompt_used") or ""),
        source=str(raw.get("source") or ""),
        created_at=created_at,
        extra=dict(raw.get("extra") or {}),
    )


class _CharacterRefView:
    def __init__(self, is_emergent: bool, world_id: str | None, asset_id: str) -> None:
        self.is_emergent = is_emergent
        self.world_id = world_id
        self.asset_id = asset_id


def _parse_character_ref(ref: str) -> _CharacterRefView:
    from .errors import CharactersError

    if not ref:
        raise CharactersError("empty character_ref")
    if ref.startswith("campaign:emergent/"):
        _, _, rest = ref.partition("campaign:emergent/")
        parts = rest.strip("/").split("/")
        if parts[0] == "character" and len(parts) == 2:
            return _CharacterRefView(True, None, parts[1])
        if len(parts) == 1:
            return _CharacterRefView(True, None, parts[0])
    if ref.startswith("emergent/"):
        parts = ref.split("/")
        return _CharacterRefView(True, None, parts[-1])
    if ref.startswith("library:"):
        _, _, path = ref.partition("library:")
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
            return _CharacterRefView(False, parts[1], parts[3])
    parts = ref.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
        return _CharacterRefView(False, parts[1], parts[3])
    raise CharactersError(f"unrecognized character_ref {ref!r}")


def _slug_for_lore_entry(entry: IngestedLoreEntry, char_slug: str) -> str:
    candidates: list[str] = []
    if entry.name:
        candidates.append(entry.name)
    candidates.extend(entry.keys[:1])
    for candidate in candidates:
        slug = slugify(candidate, fallback="")
        if slug:
            return slug
    return f"entry-{entry.source_index:02d}"


def _is_sparse_payload(payload: CharacterData) -> bool:
    return not (payload.description or "").strip() and not payload.tags


def _capsule_draft_to_patch(draft: CapsuleDraft) -> dict[str, object]:
    patch: dict[str, object] = {}
    summary = (draft.summary_line or "").strip()
    if summary:
        patch["description"] = summary
    if draft.tags:
        patch["tags"] = [t for t in draft.tags if t]
    return patch
