"""Sprint 115's Prep ops: preparing a campaign. Called as ``prep.<op>``; see docs/PREP.md.

Only Sprint 115 edits this package. Each op is ``handler(ctx, args) -> dict`` over an existing domain package (see
``grimoire.app.adapter``); nothing here keeps a record of its own.

- ``catalogs`` (056): the library's catalogs and what a campaign's chats bind from them, with each binding's pinned
  revision, the catalogs' exclusions and each NPC's context cost. The director's.
- ``rulesets`` and ``ruleset_check`` (062): the four reference action/result contracts, and one proposed action
  checked against one of them. Public; the table proposes only for its own characters. Never a roll or a record.
- ``preparation``, ``prepare_plan``, ``prepare`` and ``prepare_review`` (064): one optional scene prepared from the
  evidence the library holds, its cost estimated and approved before 064's one metered ``planning`` call is made,
  then reviewed. The director's; in memory only.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter
from grimoire.calendars import CalendarError
from grimoire.catalogs import CatalogError
from grimoire.chats.codec import ChatCodecError
from grimoire.core.knowledge import KnowledgeError
from grimoire.goals import GoalError
from grimoire.preparation import PreparationError
from grimoire.store.errors import LedgerInvalid

from .catalogs import prep_catalogs
from .preparation import _preparation, _prepare, _prepare_plan, _prepare_review
from .rulesets import _ruleset_check, _rulesets

ADAPTER = Adapter(
    "prep", owner="115",
    ops={"catalogs": prep_catalogs, "rulesets": _rulesets, "ruleset_check": _ruleset_check,
         "preparation": _preparation, "prepare_plan": _prepare_plan, "prepare": _prepare,
         "prepare_review": _prepare_review},
    # what a domain refuses that no op above already answered with its own reason: never a server error
    refusals={CatalogError: "catalog_refused", CalendarError: "calendar_refused", ChatCodecError: "chat_unreadable",
              KnowledgeError: "knowledge_refused", GoalError: "agenda_refused", PreparationError: "preparation_refused",
              LedgerInvalid: "ledger_unreadable"})

__all__ = ["ADAPTER"]
