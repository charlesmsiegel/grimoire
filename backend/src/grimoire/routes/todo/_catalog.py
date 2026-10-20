"""todo: catalog responsibilities."""

from __future__ import annotations

from . import _campaign, _continuity, _library, _pricing, _sources
from ._router import router as router

#: Builders that only have an answer inside a campaign, in the order they are
#: worth doing, each beside the id it emits. Proposals holding the world back
#: lead regardless of count -- they are the only thing here that blocks play.
CAMPAIGN_BUILDERS = (
    ("unreviewed", _campaign._chore_unreviewed),
    ("open-scenes", _campaign._chore_open_scenes),
    ("sheets", _campaign._chore_sheets),
    ("anchors", _campaign._chore_anchors),
    ("taglines", _campaign._chore_taglines),
    ("avatars", _campaign._chore_avatars),
    ("cover", _campaign._chore_cover),
    ("owed", _continuity._chore_owed),
    ("continuity-overlaps", _continuity._chore_continuity_overlaps),
    ("continuity-closures", _continuity._chore_continuity_closures),
)

#: Builders that answer with no campaign open: the library's own backlog.
#: Everything here is a fact about a world (or, for `unpriced`, about the whole
#: store), so the list is worth opening before a campaign is chosen -- which is
#: exactly when a freshly imported world's backlog is largest.
LIBRARY_BUILDERS = (
    ("world-describe", _library._chore_world_describe),
    ("world-taglines", _library._chore_world_taglines),
    ("world-anchors", _library._chore_world_anchors),
    ("world-avatars", _library._chore_world_avatars),
    ("world-covers", _library._chore_world_covers),
    ("world-subjects", _library._chore_world_subjects),
    ("unpriced", _pricing._chore_unpriced),
    ("unpriced-models", _pricing._chore_unpriced_models),
    ("embeddings", _pricing._chore_embeddings),
)

#: Both, in display order. Adding a chore is one entry and one function.
BUILDERS = CAMPAIGN_BUILDERS + LIBRARY_BUILDERS

#: The ids each table emits, DERIVED rather than typed out again. Two lists of
#: the same ids is how a chore ends up in one and not the other, and the
#: symptom -- an expansion that silently returns nothing, an ignore the route
#: rejects -- is a long way from the cause.
LIBRARY_IDS = frozenset(i for i, _b in LIBRARY_BUILDERS)

#: Every id `BUILDERS` can emit. The ignore route checks against this rather
#: than accepting an id blind: a set that accumulates ids nothing emits grows
#: forever and silences things nobody can name.
KNOWN = frozenset(i for i, _b in BUILDERS)
CAMPAIGN_IDS = frozenset(i for i, _b in CAMPAIGN_BUILDERS)

#: The order the themes are read in: scenes, wrap-up, commitments, continuity review, character, art,
#: rules, costs, then housekeeping.
#:
#: Declared rather than derived, and that is the fix rather than the taste.
#: `BUILDERS` orders CHORES deliberately -- unreviewed proposals before open
#: scenes before sheet coverage -- and the view groups them for display, which
#: silently reordered the groups by whichever chore happened to be first in
#: each. Before the thematic regrouping, a library whose only voice chore was a
#: world anchor put its character heading last, and the same library one tagline
#: later put it third: an order that moves with the data is one nobody can learn.
#: Themes now collect related work across scopes; severity still belongs to the
#: individual chore rather than deciding where its theme appears.
#:
#: A group a chore names but this tuple does not is appended in chore order, so
#: adding a chore under a new heading is never invisible -- but it does belong
#: here, and `test_todo_route.py` says so.
GROUP_ORDER = (
    "Scenes",
    "Wrap-up",
    "Commitments",
    "Continuity",
    "Character & voice",
    "Artwork",
    "Game mechanics",
    "Costs & pricing",
    "Housekeeping",
)


#: How many instances one chore will list. A chore can cover a whole roster,
#: and a page that renders every one of several hundred is a page that stops
#: being a to-do list. What is dropped is REPORTED (`truncated`), because a
#: silently short list reads as a complete one -- the same rule the rest of the
#: app keeps about capped reads.
ITEM_CAP = 200


#: What expanding a chore shows. Deliberately a SECOND pass rather than part of
#: the list: naming every instance of every chore on every summary read is the
#: cost the list is built to avoid. The page asks for previews as they enter view.
ITEMS = {
    "unreviewed": _campaign._items_unreviewed,
    "open-scenes": _campaign._items_open_scenes,
    "sheets": _campaign._items_sheets,
    "anchors": _campaign._items_anchors,
    "taglines": _campaign._items_taglines,
    "avatars": _sources._items_avatars,
    "cover": _sources._items_cover,
    "world-avatars": _library._items_world_avatars,
    "world-covers": _library._items_world_covers,
    "world-subjects": _library._items_world_subjects,
    "owed": _continuity._items_owed,
    "continuity-overlaps": _continuity._items_continuity_overlaps,
    "continuity-closures": _continuity._items_continuity_closures,
    "unpriced": _pricing._items_unpriced,
    "unpriced-models": _pricing._items_unpriced_models,
    "embeddings": _pricing._items_embeddings,
    "world-describe": _library._items_world_describe,
    "world-taglines": _library._items_world_taglines,
    "world-anchors": _library._items_world_anchors,
}
