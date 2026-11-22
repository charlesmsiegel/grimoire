"""One chat's cast as 057 and 060 take it: each character's exact ref, name and present controller, and whether
022's effective snapshot composes it as an agent. Read only; nothing here is saved.

057's ``decide`` and 060's ``admit`` check every actor against a ``CampaignRoster`` and a ``DefinitionIndex``. The
library's characters are composed through 022's effective snapshot (a card's portrayal included), not as bare
definitions, so the roster handed to them carries what that snapshot says of each cast member, its agent capability,
on an instance with no source of its own. Control is the campaign's recorded control (Sprint 102's sovereignty).
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.app.adapter import AdapterContext
from grimoire.core import DIRECTOR
from grimoire.core.entity import EntityInstance, is_agent
from grimoire.core.facets import Agent, FacetBundle
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.index import DefinitionIndex, MissingDefinition
from grimoire.core.model import ControlAssignment
from grimoire.core.roster import CampaignRoster
from grimoire.effective import chat_snapshot
from grimoire.store.errors import StoreError

from ..campaign import hub_of


@dataclass(frozen=True)
class Member:
    actor: InstanceRef
    name: str | None
    controller: str | None
    agent: bool

    @property
    def role(self) -> str:
        return "unseated" if self.controller is None else "npc" if self.controller == DIRECTOR else "player"

    def view(self) -> dict:
        return {"actor": str(self.actor), "name": self.name, "role": self.role, "agent": self.agent}


@dataclass(frozen=True)
class Cast:
    chat: ChatRef
    members: tuple[Member, ...]
    controls: tuple[ControlAssignment, ...]

    def get(self, actor: InstanceRef) -> Member | None:
        return next((member for member in self.members if member.actor == actor), None)

    def name(self, actor: InstanceRef) -> str | None:
        found = self.get(actor)
        return None if found is None else found.name

    def roster(self, *extra: EntityInstance) -> CampaignRoster:
        """The cast as 057/060 check it, with ``extra`` instances laid out beside it (never saved)."""
        return CampaignRoster(self.chat.campaign_id, (*extra, *(EntityInstance(
            member.actor, local_name=member.name, added=FacetBundle((Agent(),)) if member.agent else FacetBundle())
            for member in self.members)))

    @staticmethod
    def definitions() -> DefinitionIndex:
        return DefinitionIndex((), ())


def cast_of(ctx: AdapterContext, chat: ChatRef) -> Cast:
    scene = hub_of(ctx).runner.open(chat)
    try:
        captured = chat_snapshot(ctx.store.load(), chat).snapshot
    except (ValueError, LookupError, StoreError):
        captured = None
    members = []
    for actor in scene.cast:
        agent = False
        if captured is not None:
            try:
                agent = is_agent(captured.entity(actor))
            except (ValueError, MissingDefinition, LookupError):
                agent = False  # an actor whose definition cannot be composed now is no agent here
        members.append(Member(actor, scene.names.get(actor), scene.controller(actor), agent))
    controls = tuple(item for item in scene.controls if item.campaign_id == chat.campaign_id)
    return Cast(chat, tuple(members), controls)


__all__ = ["Cast", "Member", "cast_of"]
