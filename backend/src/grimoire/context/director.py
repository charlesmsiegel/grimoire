"""Lore for the scene-level director reply (031 for 027's ``director_brief``).

A scenario-only chat has no speaking NPC, so no actor pack carries its lorebook: without this the
narrator would play with none of the setting. The director is no actor, so its pack has no
portrayal and no memory, and it may read ``director`` entries as well as ``common`` ones. What
triggers an entry is still only **table-visible** text: a whisper, a private observation or
another entry's text never does, so what the table has not touched stays untriggered.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.snapshot import EffectiveSnapshot

from .actor import MEASUREMENTS
from .basis import selection_basis
from .lore import Unpack, check_readers, lore, lore_problems
from .model import (
    AUDIENCES, MAX_SCENE_TEXT, Budget, ContextError, EntryReader, LorePack, TokenCounter, estimate_tokens,
)
from .pack import pack
from .trigger import SceneWords


def director_lore(snapshot: EffectiveSnapshot, *, campaign_id: str, table_text: str, budget: Budget,
                  readers: Mapping[str, EntryReader] | None = None, unpack: Unpack | None = None,
                  count: TokenCounter = estimate_tokens, measurement: str = "estimated") -> LorePack:
    """The director's bounded lore from ``snapshot``'s bound ``lorebook`` and ``scenario`` sources.

    ``common`` and ``director`` entries alike are candidates: ``constant`` ones always, the rest when
    a key occurs, as whole words, in ``table_text``. **Caller obligation:** ``table_text`` is what the
    whole table saw (the visible transcript window), never a whisper, an NPC's private perception or
    a player's interiority, so the director's lore follows only what play has made public.
    Ranking, the budget's ``lore`` lane and what is shed or reported are as in ``pack_actor_context``.
    The result goes only to the director brief: a ``director`` entry is never an actor's.
    ``campaign_id`` is the campaign whose chat ``snapshot`` is (``invalid_campaign`` unless an id
    segment); the pack records it and the lore bindings read as its ``basis``, which
    ``director_brief`` checks against the snapshot it renders for.
    """
    if not isinstance(snapshot, EffectiveSnapshot):
        raise TypeError(f"expected an EffectiveSnapshot, not {type(snapshot).__name__}")
    readers = {} if readers is None else readers
    if type(table_text) is not str or len(table_text) > MAX_SCENE_TEXT:
        raise ContextError(f"table_text must be text of at most {MAX_SCENE_TEXT} characters", "invalid_scene_text")
    if not isinstance(budget, Budget):
        raise ContextError("budget must be a Budget", "invalid_budget")
    check_readers(readers)
    if not callable(count):
        raise ContextError("count must be a token counter", "invalid_count")
    if unpack is not None and not callable(unpack):
        raise ContextError("unpack must be callable", "invalid_unpack")
    if type(measurement) is not str or measurement not in MEASUREMENTS:
        raise ContextError(f"measurement must be one of {sorted(MEASUREMENTS)}", "invalid_measurement")
    basis = selection_basis(snapshot, campaign_id)
    found, untriggered, problems = lore(snapshot, SceneWords([table_text]), readers, unpack, AUDIENCES)
    items, omitted = pack(found, budget, count)
    problems += lore_problems(snapshot)
    return LorePack(budget, measurement, items, omitted, tuple(sorted(untriggered, key=lambda pair: str(pair[0]))),
                    tuple(sorted(set(problems), key=lambda p: (str(p.source), p.code, p.detail or ""))), basis,
                    table_text)


__all__ = ["director_lore"]
