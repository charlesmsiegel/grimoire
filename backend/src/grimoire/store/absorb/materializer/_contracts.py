"""Shared staging aliases and identity-review reason vocabulary, without importing the higher continuity identity service."""

from __future__ import annotations

from .. import staging

_CARD_FIELDS = staging._CARD_FIELDS
APPEND_KINDS = staging.APPEND_KINDS
_char_name = staging._char_name
_actor_exists = staging._actor_exists
_entity_target = staging._entity_target

#: `continuity.identity.AS_NEW_KEY`: the private row key an accepted
#: ``existing`` retarget carries its original row under, popped here to build
#: the as-new alternative. Spelled out rather than imported because identity
#: imports this module (it shares `assign_ids`), so the edge cannot run back.
AS_NEW_KEY = "_identity_as_new"

#: The parsed sections the identity check examines, and the record kind each
#: opens -- `continuity.identity.SECTIONS`, restated for the same reason.
_IDENTITY_SECTIONS = {"plot_movements": "thread", "commitment_movements": "commitment"}

#: The `identity_check` decisions a row stages at band ``low`` whatever its
#: citation says: a verdict that it may be a duplicate, and a row the check
#: never answered. Every examined row has a plausible candidate, so an
#: unanswered one is a possible duplicate nobody ruled out; keeping its
#: routing band would pre-approve it outside NEEDS YOU.
_LOW_DECISIONS = ("uncertain", "unchecked")

#: The reason a staged accepted ``existing`` is downgraded with when its
#: target stopped being live between the resolver's answer and staging.
CLOSED_SINCE = "that record was closed while the review was being prepared"

#: The reason a staged accepted ``existing`` is downgraded with when a merge
#: made between the resolver's answer and staging folded its target into a
#: record another row of the batch already moves -- `identity.ALREADY_MOVED`,
#: the same verdict the resolver guard gives, restated as `AS_NEW_KEY` is.
MOVED_SINCE = "another row in this scene already moves that record"
#: The noun a reviewer reads in "New <noun> — <name>", per kind a proposal creates.
_NEW_RECORD_NOUNS = staging._NEW_RECORD_NOUNS
