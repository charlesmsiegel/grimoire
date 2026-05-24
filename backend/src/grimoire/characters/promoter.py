"""Character promotion collaborator (emergent -> library)."""

from __future__ import annotations

from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.state_store.indexers import make_library_id
from grimoire.state_store.paths import library_path
from grimoire.types.characters import PromotionProposal
from grimoire.types.common import CampaignId

from .errors import PromotionError
from .protocols import SheetMigrator


class CharacterPromoter:
    """Promotion logic: emergent characters -> library (spec §9)."""

    def __init__(
        self,
        *,
        library: LibraryService,
        store: StateStore,
        sheet_migrator: SheetMigrator | None = None,
    ) -> None:
        self._library = library
        self._store = store
        self._sheet_migrator = sheet_migrator

    async def propose_promotion(
        self,
        campaign_id: CampaignId,
        character_id: str,
        target_world_id: str,
        *,
        target_character_id: str | None = None,
    ) -> PromotionProposal:
        emergent = await self._store.get_emergent(campaign_id, "character", character_id)
        if emergent is None:
            raise PromotionError(
                f"no emergent character {character_id!r} in campaign {campaign_id!r}"
            )
        target_id = target_character_id or character_id
        fm = dict(emergent.get("frontmatter") or {})
        fm["id"] = target_id
        body = emergent.get("body") or ""
        library_id = make_library_id(target_world_id, "character", target_id)
        target_path = library_path(self._store.data_root, library_id)

        warnings: list[str] = []
        try:
            await self._library.get_entity(target_world_id, "character", target_id)
        except Exception:
            pass
        else:
            warnings.append(
                f"target world {target_world_id!r} already has a character "
                f"with id {target_id!r}; promotion would overwrite it"
            )
        voice = fm.get("voice") or {}
        if not isinstance(voice, dict) or not (
            str(voice.get("summary") or "").strip() or (voice.get("samples") or [])
        ):
            warnings.append("character has no voice anchor (summary or samples)")
        if not str(fm.get("description") or "").strip():
            warnings.append("character has no description")

        return PromotionProposal(
            campaign_id=campaign_id,
            character_id=character_id,
            target_world_id=target_world_id,
            target_library_id=library_id,
            target_path=str(target_path),
            frontmatter=fm,
            body=body,
            warnings=warnings,
        )

    async def promote_to_library(
        self,
        campaign_id: CampaignId,
        character_id: str,
        target_world_id: str,
        *,
        source: str = "characters:promote",
        delete_emergent: bool = False,
        target_character_id: str | None = None,
        confirm: bool = False,
        proposal: PromotionProposal | None = None,
    ) -> str:
        if proposal is None:
            proposal = await self.propose_promotion(
                campaign_id,
                character_id,
                target_world_id,
                target_character_id=target_character_id,
            )
        if not confirm:
            if proposal.warnings:
                raise PromotionError(
                    "promotion has unresolved warnings; resolve or call with "
                    f"confirm=True: {proposal.warnings}"
                )
            raise PromotionError(
                "promote_to_library requires confirm=True; use propose_promotion "
                "first to preview the write"
            )

        result = await self._store.write_library_file(
            library_id=proposal.target_library_id,
            frontmatter=dict(proposal.frontmatter),
            body=proposal.body,
            source=source,
            campaign_id=campaign_id,
        )

        if self._sheet_migrator is not None:
            emergent_ref = f"campaign:emergent/character/{character_id}"
            try:
                await self._sheet_migrator.migrate_sheet(
                    campaign_id,
                    emergent_ref,
                    proposal.target_library_id,
                )
            except Exception as exc:
                raise PromotionError(
                    f"sheet migration failed for {character_id!r}: {exc}"
                ) from exc

        if delete_emergent:
            from grimoire.state_store.paths import emergent_path

            target = emergent_path(self._store.data_root, campaign_id, "character", character_id)
            if target.exists():
                target.unlink()
        return str(result.path)
