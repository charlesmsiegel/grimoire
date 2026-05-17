"""Cross-module protocol hooks consumed by :class:`CharactersService`.

These contracts let other modules (Mechanics, etc.) plug into character
lifecycle events without the Characters service importing those modules
directly. Implementers are wired through :class:`CharactersService` kwargs.
"""

from __future__ import annotations

from typing import Protocol

from grimoire.types.common import CampaignId, CharacterRef


class SheetMigrator(Protocol):
    """Move a campaign-local mechanical sheet to a library-level sheet.

    Invoked by ``promote_to_library`` after the markdown write so the
    promoted library character keeps the mechanics that were attached to
    its emergent form. See spec ``2026-05-17-characters-remaining-design``
    §13.
    """

    async def migrate_sheet(
        self,
        campaign_id: CampaignId,
        character_ref: CharacterRef,
        target_library_id: str,
    ) -> None: ...
