"""The list of everything the app noticed, and the ignore that silences one.

Every entry is derived on the way out. There is no chore table on disk and
nothing is cached: a chore whose count is zero does not appear, and the number
in its label is the number this request computed. That is what makes the list
worth opening twice -- a to-do list that can go stale teaches the reader to
distrust it, and then the one entry that mattered is the one they scroll past.

The cost rule from `routes/shell.py` applies here for the same reason: a chore
that cannot be counted cheaply is not offered. This page is opened casually and
answers about a whole library, so a count proportional to the library's age
would make it a page nobody opens. Where a useful chore has no cheap source it
waits for one rather than shipping a guess.

`store.chores` owns the ignore set; everything else here is a read of the
stores the rest of the app already reads.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import store
from .common import _dump  # noqa: F401  (kept for the pydantic-agnostic rule)

router = APIRouter()


def _slugish(name: str) -> str:
    """`name` as it would most obviously slugify, for deciding whether an id is
    worth showing beside it. Not `paths.safe_id` -- this is a display question
    ("does the id tell the reader anything the name did not"), not an
    addressing one, and being approximate here costs nothing."""
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


class _Ctx:
    """One request's inputs, and the derivations more than one chore wants.

    It exists for the second half. `anchors` and `taglines` are the same walk
    asked two different questions, and as two independent builders they ran it
    twice -- the single most expensive thing this module did, paid on every
    `/api/shell` read, which is every navigation. A builder that needs a
    derivation asks the ctx for it and the second asker gets the first one's
    answer.

    Deliberately per-request and thrown away after: nothing here is a cache
    that can outlive the read it was computed for. "Every chore is a live
    count" is the contract, and a memo with a lifetime is how it stops being
    one.
    """

    def __init__(self, cid: str) -> None:
        self.cid = cid
        self._memo: dict[str, object] = {}

    def _once(self, key: str, compute):
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    def _scenes_or_none(self) -> list[dict] | None:
        def read() -> list[dict] | None:
            if not self.cid:
                return None
            try:
                return store.scenes.read.list_scenes(self.cid)
            except (store.CampaignNotFound, OSError):
                return None
        return self._once("scenes", read)

    def scenes(self) -> list[dict]:
        return self._scenes_or_none() or []

    def has_campaign(self) -> bool:
        """Whether there is a readable campaign to ask campaign chores about.

        The gate `_chores` used to take from `list_scenes` raising, kept
        deliberately: an unknown or unreadable id must answer no campaign
        chores at all, rather than reach seven builders each with its own idea
        of what a bad cid means -- and several of them raise on the way to
        saying nothing, which is a 500 where the shell promises a null.

        Distinct from "has no scenes", which `scenes()` flattens to the same
        empty list. That is why the memo holds `None` rather than `[]`.
        """
        return self._scenes_or_none() is not None

    def character_gaps(self) -> tuple[list[dict], list[dict]]:
        return self._once("gaps", lambda: _character_gaps(self.cid))

    def world_char_gaps(self) -> tuple[list[dict], list[dict]]:
        """(untagged, anchorless) over `other_worlds`, computed once.

        The same sharing `character_gaps` does, for the same reason and against
        the same mistake: `world-taglines` and `world-anchors` are one walk
        asked two questions, and as two independent builders they ran it twice.
        """
        return self._once("world_gaps",
                          lambda: _world_char_gaps(self.other_worlds()))

    def worlds(self) -> list[dict]:
        """Every world, newest first."""
        def read() -> list[dict]:
            try:
                return store.worlds.read.list_worlds()
            except OSError:
                return []
        return self._once("worlds", read)

    def other_worlds(self) -> list[dict]:
        """The worlds the open campaign does NOT draw on.

        The exclusion behind `world-taglines` and `world-anchors`: those two
        facts are already reported for the campaign's own world, over its
        effective copy-on-write roster, which is the more accurate of the two
        answers (see `_character_gaps`). Reporting them again from the world
        side would double-count the same character and disagree with itself
        about the campaign-side taglines it cannot see.

        `world-describe` deliberately does NOT use this. Nothing else reports
        an image backlog, so excluding the campaign's world there would hide it
        rather than de-duplicate it.
        """
        def read() -> list[dict]:
            if not self.cid:
                return self.worlds()
            try:
                used = {w for c, _n, w in store.campaigns.read.world_refs()
                        if c == self.cid and w}
            except OSError:
                # "We could not tell" must not become "it uses nothing", which
                # would double-report every world. `world_refs`' own rule.
                return []
            return [w for w in self.worlds() if w["id"] not in used]
        return self._once("other_worlds", read)


def _character_gaps(cid: str) -> tuple[list[dict], list[dict]]:
    """(no tagline, no voice anchor) across the campaign's EFFECTIVE roster.

    Through `overlay.list_characters`, and that is the whole point of this
    function rather than an implementation note. A campaign is copy-on-write
    over its world, so reading the world root directly gets three things wrong
    at once, and this got all three:

    - a character the campaign DELETED is still in the world, so it was
      reported as missing a tagline in a campaign it is not in;
    - `tagline.md` and `voice_anchor.md` are sidecars that overlay per file, so
      a tagline written campaign-side did not count and the character was
      reported as lacking one;
    - a materialized actor is authoritative for its own meta, so a campaign
      copy's tagline was invisible behind the world's.

    `list_characters` already resolves both fields -- `tagline` per file and
    `has_voice_anchor` off one directory scan rather than a read per row -- so
    this is also cheaper than the walk it replaces.
    """
    try:
        rows = store.overlay.list_characters(cid)
    except (store.CampaignNotFound, OSError):
        return [], []
    no_tagline: list[dict] = []
    no_anchor: list[dict] = []
    for row in rows:
        name = str(row.get("name") or row["id"])
        # The slug, when it is not just the name lowercased. It is what
        # addresses the character in a ref and what names the file, so it is
        # the one fact that turns a list of names into something you can act
        # on -- and a roster with two Maras is exactly when it matters.
        slug = row["id"] if row["id"] != _slugish(name) else ""
        if not str(row.get("tagline") or "").strip():
            no_tagline.append({"id": row["id"], "label": name, "detail": slug})
        if not row.get("has_voice_anchor"):
            no_anchor.append({"id": row["id"], "label": name, "detail": slug})
    return no_tagline, no_anchor


def _chore_unreviewed(ctx: _Ctx) -> dict | None:
    cid = ctx.cid
    # Off the same walk the expansion uses, so the count on the row and the
    # rows behind it cannot disagree about what is waiting.
    waiting = _items_unreviewed(cid)
    n = sum(int(w.get("proposals", 0)) for w in waiting)
    sids = [w["id"] for w in waiting]
    if not n:
        return None
    return {
        "id": "unreviewed", "scope": "campaign", "group": "Waiting on you", "severity": "alert", "n": n,
        "what": f"{n} absorb proposal{'s' if n != 1 else ''} unreviewed",
        "why": "A scene was absorbed but never reviewed. Until it is, none of what "
               "it found has reached the world.",
        "fix": f"/campaigns/{cid}/scenes/{sids[0]}" if sids else f"/campaigns/{cid}/scenes",
        "fix_label": "Open wrap-up",
    }


def _chore_open_scenes(ctx: _Ctx) -> dict | None:
    cid, scenes = ctx.cid, ctx.scenes()
    n = sum(1 for s in scenes if not s["done"])
    if n < 2:
        return None
    return {
        "id": "open-scenes", "scope": "campaign", "group": "Continuity", "severity": "note", "n": n,
        "what": f"{n} scenes are open at once",
        "why": "Each one holds part of the campaign's present. Wrapping one up is "
               "what moves the chronicle forward.",
        "fix": f"/campaigns/{cid}/scenes", "fix_label": "See the scenes",
    }


def _chore_sheets(ctx: _Ctx) -> dict | None:
    cid = ctx.cid
    try:
        cov = store.sheets.coverage(cid)
    except (OSError, KeyError, ValueError):
        return None
    n = sum(k["total"] - k["sheeted"] for k in cov.values()) if cov else 0
    if not n:
        return None
    return {
        "id": "sheets", "scope": "campaign", "group": "World content", "severity": "warn", "n": n,
        "what": f"{n} cast member{'s' if n != 1 else ''} without a sheet",
        "why": "A character with no sheet cannot be rolled for, so the module this "
               "campaign binds does not apply to them.",
        "fix": f"/campaigns/{cid}/sheets", "fix_label": "Sheet coverage",
    }


def _chore_anchors(ctx: _Ctx) -> dict | None:
    cid = ctx.cid
    who = ctx.character_gaps()[1]
    n = len(who)
    if not n:
        return None
    return {
        "id": "anchors", "scope": "campaign", "group": "Voice & character", "severity": "warn", "n": n,
        "what": f"{n} character{'s' if n != 1 else ''} with no voice anchor",
        "why": "Without one nothing measures whether a reply still sounds like them, "
               "so drift goes unreported rather than absent.",
        "fix": f"/campaigns/{cid}/world", "fix_label": "The cast",
    }


def _chore_taglines(ctx: _Ctx) -> dict | None:
    cid = ctx.cid
    who = ctx.character_gaps()[0]
    n = len(who)
    if not n:
        return None
    return {
        "id": "taglines", "scope": "campaign", "group": "World content", "severity": "note", "n": n,
        "what": f"{n} character{'s' if n != 1 else ''} with no tagline",
        "why": "The tagline is what a browse grid and a scene suggestion have to go "
               "on before anything else is read.",
        "fix": f"/campaigns/{cid}/world", "fix_label": "The cast",
    }


def _chore_owed(ctx: _Ctx) -> dict | None:
    cid = ctx.cid
    try:
        owed = [c for c in store.commitments.open_commitments(cid) if c.get("due")]
    except (OSError, ValueError):
        return None
    if not owed:
        return None
    return {
        "id": "owed", "scope": "campaign", "group": "Continuity", "severity": "warn", "n": len(owed),
        "what": f"{len(owed)} open thread{'s' if len(owed) != 1 else ''} with a deadline",
        "why": "A promise with a date is the kind the campaign is expected to answer, "
               "and the ledger is where it is still waiting.",
        "fix": f"/campaigns/{cid}/ledger", "fix_label": "The ledger",
    }


def _chore_unpriced(ctx: _Ctx) -> dict | None:
    """Models the ledger holds that no pricing entry matches.

    The chore exists because the failure is silent: a table is matched by model
    string, so an entry whose key does not match what was actually recorded
    prices nothing and says nothing. Naming the strings is the whole fix -- the
    reader can then see that their entry reads `z.ai/...` where the ledger says
    `z-ai/...`, which no amount of staring at a rollup would reveal.
    """
    try:
        models = store.usage.unpriced_models()
    except (OSError, ValueError):
        return None
    if not models:
        return None
    calls = sum(m["calls"] for m in models)
    names = ", ".join(m["model"] for m in models[:3])
    more = "" if len(models) <= 3 else f", and {len(models) - 3} more"
    return {
        "id": "unpriced", "scope": "library", "group": "Housekeeping", "severity": "warn", "n": calls,
        "what": f"{calls} call{'s' if calls != 1 else ''} no pricing entry matches",
        "why": f"Nobody reported a price for these and your table has no rate that "
               f"matches them, so they are counted rather than costed: {names}{more}. "
               f"The model string has to match exactly.",
        "fix": "/config", "fix_label": "Pricing",
    }


def _world_char_gaps(worlds: list[dict]) -> tuple[list[dict], list[dict]]:
    """(untagged, anchorless) across `worlds`, by stat rather than by reading.

    `taglines.untagged_ids` and `voice_anchors.anchorless_ids` carry the rules;
    their docstrings carry why a stat is sound for each and where it would stop
    being. What is here is the reason it has to be a stat at all: this runs for
    every character of every world on every `/api/shell` read, and opening the
    two sidecars instead costs orders of magnitude more on a cold cache -- one
    small file per record spread across the whole store is the worst shape a
    filesystem can be handed, and the first read after a restart would stall
    the page this list is drawn on.

    The character's NAME is not resolved here, only its id and world. A name is
    a frontmatter read per row, which is the cost this avoids; the expansion
    pays for it, for the one chore a reader opened. That split is the same one
    `ITEMS` exists for.
    """
    untagged: list[dict] = []
    anchorless: list[dict] = []
    for w in worlds:
        try:
            root = store.worlds.paths.world_root(w["id"])
            ids = store.characters.character_refs(root)
        except (OSError, store.worlds.paths.WorldNotFound):
            continue
        where = {"wid": w["id"], "world": w["name"]}
        untagged.extend({**where, "id": c}
                        for c in store.taglines.untagged_ids(root, ids))
        anchorless.extend({**where, "id": c}
                          for c in store.voice_anchors.anchorless_ids(root, ids))
    return untagged, anchorless


def _world_describe_counts(worlds: list[dict]) -> list[dict]:
    """Per world, how many stored images carry no description.

    `undescribed_count`, not `len(undescribed(...))`: the list resolves an
    extension and a cache-busting token per image and those are a stat apiece,
    which on a whole-library sweep is most of the walk. The two are held to the
    same answer by a test, because a cheap count that can drift from the list
    behind it is the stale number this whole module is arranged to not have.
    """
    out = []
    for w in worlds:
        try:
            root = store.worlds.paths.world_root(w["id"])
            n = (sum(store.image_descriptions.undescribed_count(root, base)
                     for base in _DESCRIBE_BASES)
                 # The world's own library hangs off no record, so no base walk
                 # can reach it. Inside the same `try`: a leak here 500s the
                 # whole chore sweep, which is what its `except` is for.
                 + store.world_images.undescribed_count(w["id"]))
        except (OSError, store.worlds.paths.WorldNotFound):
            continue
        if n:
            out.append({"wid": w["id"], "world": w["name"], "n": n})
    return out


#: The RECORD bases the describe queue walks -- `routes/characters.list_undescribed_images`'
#: list, and it must stay that list. The queue also offers the world's own image
#: library, which hangs off no record and so appears in none of these; every
#: reader of this roster adds `world_images` alongside it rather than expecting
#: this tuple to cover the whole backlog. Greetings are deliberately absent there
#: (their sidecar is `image_subjects`, a different question), so a chore that
#: counted them would offer a backlog the queue cannot empty.
_DESCRIBE_BASES = ("characters", store.pcs.ASSET_BASE, *store.entities.ENTITY_KINDS)


def _chore_world_describe(ctx: _Ctx) -> dict | None:
    rows = _world_describe_counts(ctx.worlds())
    n = sum(r["n"] for r in rows)
    if not n:
        return None
    return {
        "id": "world-describe", "scope": "world", "group": "World content",
        "severity": "note", "n": n,
        "what": f"{n} image{'s' if n != 1 else ''} with no description",
        "why": "An undescribed image is one nothing can offer a scene, because "
               "what it depicts is written down nowhere.",
        # The Images tab, not the cast: this backlog spans every base a world
        # holds art on -- characters, PCs, the five entity kinds, and now the
        # world's own library, which is not a record at all. "The cast" named
        # one of them and sent a reader with a library-only backlog to a page
        # showing none of it.
        "fix": f"/worlds/{rows[0]['wid']}?section=images" if len(rows) == 1 else "/worlds",
        "fix_label": "Images" if len(rows) == 1 else "The worlds",
    }


def _chore_world_taglines(ctx: _Ctx) -> dict | None:
    who = ctx.world_char_gaps()[0]
    if not who:
        return None
    return {
        "id": "world-taglines", "scope": "world", "group": "World content",
        "severity": "note", "n": len(who),
        "what": f"{len(who)} character{'s' if len(who) != 1 else ''} with no tagline",
        # No "in worlds the campaign does not use" here any more: the row
        # carries a scope chip that says which body of content it is about,
        # and the sentence was the same fact spelled out where only a reader
        # who finished the paragraph would find it.
        "why": "The tagline is what a browse grid and a scene suggestion have to go "
               "on before anything else is read.",
        "fix": f"/worlds/{who[0]['wid']}" if len({c['wid'] for c in who}) == 1 else "/worlds",
        "fix_label": "The cast" if len({c["wid"] for c in who}) == 1 else "The worlds",
    }


def _chore_world_anchors(ctx: _Ctx) -> dict | None:
    who = ctx.world_char_gaps()[1]
    if not who:
        return None
    return {
        "id": "world-anchors", "scope": "world", "group": "Voice & character",
        "severity": "note", "n": len(who),
        "what": f"{len(who)} character{'s' if len(who) != 1 else ''} with no voice anchor",
        "why": "Without one nothing measures whether a reply still sounds like them, "
               "so drift goes unreported rather than absent.",
        "fix": f"/worlds/{who[0]['wid']}" if len({c['wid'] for c in who}) == 1 else "/worlds",
        "fix_label": "The cast" if len({c["wid"] for c in who}) == 1 else "The worlds",
    }


#: Builders that only have an answer inside a campaign, in the order they are
#: worth doing, each beside the id it emits. Proposals holding the world back
#: lead regardless of count -- they are the only thing here that blocks play.
CAMPAIGN_BUILDERS = (
    ("unreviewed", _chore_unreviewed),
    ("open-scenes", _chore_open_scenes),
    ("sheets", _chore_sheets),
    ("anchors", _chore_anchors),
    ("taglines", _chore_taglines),
    ("owed", _chore_owed),
)

#: Builders that answer with no campaign open: the library's own backlog.
#: Everything here is a fact about a world (or, for `unpriced`, about the whole
#: store), so the list is worth opening before a campaign is chosen -- which is
#: exactly when a freshly imported world's backlog is largest.
LIBRARY_BUILDERS = (
    ("world-describe", _chore_world_describe),
    ("world-taglines", _chore_world_taglines),
    ("world-anchors", _chore_world_anchors),
    ("unpriced", _chore_unpriced),
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

#: The order the groups are read in, most urgent first.
#:
#: Declared rather than derived, and that is the fix rather than the taste.
#: `BUILDERS` orders CHORES deliberately -- unreviewed proposals before open
#: scenes before sheet coverage -- and the view groups them for display, which
#: silently reordered the groups by whichever chore happened to be first in
#: each. So a library whose only voice chore was a world anchor put "Voice &
#: character" last, and the same library one tagline later put it third: an
#: order that moves with the data is one nobody can learn.
#:
#: A group a chore names but this tuple does not is appended in chore order, so
#: adding a chore under a new heading is never invisible -- but it does belong
#: here, and `test_todo_route.py` says so.
GROUP_ORDER = (
    "Waiting on you",
    "Voice & character",
    "World content",
    "Continuity",
    "Housekeeping",
)


def _any_undescribed(ctx: _Ctx) -> bool:
    """`_chore_world_describe` at `n > 0`, stopping at the first image it finds.

    The `try` is per world and catches what `_world_describe_counts` catches,
    which is the whole point of it being here rather than inline: a world that
    cannot be read is skipped there, so it has to be skipped here too. Letting
    an `OSError` out instead would not merely disagree with the page -- the
    badge is computed for `/api/shell`, so an unreadable world directory would
    500 every navigation over a backlog the page below it shrugs off and
    renders without.
    """
    for w in ctx.worlds():
        try:
            root = store.worlds.paths.world_root(w["id"])
            # `has_undescribed`, never a count: this probe exists because the
            # `_CHEAP` roster is for chores whose COUNT costs far more than
            # their presence, and summing a backlog to answer "is it empty" is
            # the thing that roster avoids.
            if (any(store.image_descriptions.has_undescribed(root, base)
                    for base in _DESCRIBE_BASES)
                    or store.world_images.has_undescribed(w["id"])):
                return True
        except (OSError, store.worlds.paths.WorldNotFound):
            continue
    return False


#: Cheap yes/no tests for chores whose COUNT costs far more than their
#: presence. Only the rail's badge may use these, and only because it counts
#: chores rather than instances -- `live` always computes the real number,
#: because the number is what the row says out loud.
#:
#: `test_todo_route.py` holds `badge_count` and `live(...)["count"]` to the
#: same answer, including over a world neither of them can read. A badge that
#: can disagree with the page under it is the stale number this module is
#: arranged to not have, arrived at from the other side.
PRESENCE = {
    "world-describe": _any_undescribed,
}


def _builders_for(ctx: _Ctx):
    """Which tables answer for this request.

    The library builders always; the campaign ones only where there is a
    campaign to read. With no campaign open, or an id naming none, a campaign
    builder would answer zero -- which is the same output for "nothing waiting"
    as for "nothing to ask", and the second is what is true.
    """
    return BUILDERS if ctx.has_campaign() else LIBRARY_BUILDERS


def _chores(cid: str) -> list[dict]:
    """Every chore with a non-zero count. A chore at zero is not in the list."""
    ctx = _Ctx(cid)
    return [c for c in (b(ctx) for _i, b in _builders_for(ctx)) if c]


def badge_count(cid: str) -> int:
    """`live(cid)["count"]`, without paying for the totals behind the labels.

    The rail reads this on every navigation and renders one number: how many
    chores are outstanding. Summing a whole-library image backlog to learn that
    one of them is non-empty is the walk that would make the badge cost more
    than the page it sits beside -- so a chore with an entry in `PRESENCE` is
    asked the yes/no directly and stops at the first instance it finds.

    Identical output to `live`, by construction and by test: same builders,
    same ignore set, and a presence test that is the same predicate as
    `n > 0`.
    """
    ctx = _Ctx(cid)
    off = store.chores.ignored()
    n = 0
    for cid_, builder in _builders_for(ctx):
        if cid_ in off:
            continue
        test = PRESENCE.get(cid_)
        if test(ctx) if test else builder(ctx) is not None:
            n += 1
    return n


#: How many instances one chore will list. A chore can cover a whole roster,
#: and a page that renders every one of several hundred is a page that stops
#: being a to-do list. What is dropped is REPORTED (`truncated`), because a
#: silently short list reads as a complete one -- the same rule the rest of the
#: app keeps about capped reads.
ITEM_CAP = 200


def _items_unreviewed(cid: str) -> list[dict]:
    """One row per waiting scene, naming what it is holding."""
    out = []
    d = store.scenes.paths._scenes_dir(cid)   # paths-ok: the resolver itself
    if not d.exists():
        return []
    titles = {s["id"]: s["title"] for s in store.scenes.read.list_scenes(cid)}
    for path in sorted(d.glob("*.review.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            edits = rec.get("review", {}).get("edits", [])
        except (OSError, ValueError, AttributeError):
            continue
        sid = path.name[: -len(".review.json")]
        # The kinds it is proposing, which is what tells the reader whether
        # this is a dossier rewrite or one fact nobody cited.
        kinds: dict[str, int] = {}
        for e in edits:
            if isinstance(e, dict):
                k = str(e.get("kind", "?"))
                kinds[k] = kinds.get(k, 0) + 1
        detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        out.append({"id": sid, "label": titles.get(sid, sid),
                    "detail": detail or f"{len(edits)} proposals",
                    # Carried so the chore's headline count comes off this same
                    # walk. Harmless on the wire; the page ignores it.
                    "proposals": len(edits),
                    "fix": f"/campaigns/{cid}/scenes/{sid}"})
    return out


def _items_open_scenes(cid: str) -> list[dict]:
    out = []
    for sc in store.scenes.read.list_scenes(cid):
        if sc["done"]:
            continue
        where = " · ".join(x for x in (sc.get("date"), sc.get("place")) if x)
        out.append({"id": sc["id"], "label": sc["title"],
                    "detail": where or "no time or place set",
                    "fix": f"/campaigns/{cid}/scenes/{sc['id']}"})
    return out


def _items_sheets(cid: str) -> list[dict]:
    """Cast members with no sheet, by kind.

    `roster` rather than `coverage`: a tally cannot say WHICH, and a chore that
    offers to fix a gap has to name it.
    """
    try:
        roster = store.sheets.roster(cid)
    except (OSError, KeyError, ValueError):
        return []
    out = []
    for kind, rows in sorted(roster.items()):
        for r in rows:
            if r.get("sheeted"):
                continue
            out.append({"id": f"{kind}:{r['id']}", "label": r.get("name", r["id"]),
                        "detail": kind, "fix": f"/campaigns/{cid}/sheets"})
    return out


def _items_anchors(cid: str) -> list[dict]:
    return [{**c, "fix": f"/campaigns/{cid}/world"} for c in _character_gaps(cid)[1]]


def _items_taglines(cid: str) -> list[dict]:
    return [{**c, "fix": f"/campaigns/{cid}/world"} for c in _character_gaps(cid)[0]]


def _items_owed(cid: str) -> list[dict]:
    try:
        owed = [c for c in store.commitments.open_commitments(cid) if c.get("due")]
    except (OSError, ValueError):
        return []
    return [{"id": c["id"], "label": c["title"],
             "detail": " · ".join(x for x in (f"due {c['due']}", c.get("kind"),
                                              c.get("latest_beat")) if x),
             "fix": f"/campaigns/{cid}/ledger"} for c in owed]


def _items_unpriced(cid: str) -> list[dict]:
    try:
        models = store.usage.unpriced_models()
    except (OSError, ValueError):
        return []
    return [{"id": m["model"], "label": m["model"],
             "detail": f"{m['calls']} calls that a rate would price",
             "fix": "/config"} for m in models]


def _items_world_describe(cid: str) -> list[dict]:
    """One row per WORLD, not per image.

    A per-image row would be hundreds of rows saying nothing individually
    useful, and would hit `ITEM_CAP` on any real library. The world is the
    grain the fix is applied at -- the describe queue runs over a whole world
    from its cast page -- so it is the grain the reader needs.
    """
    ctx = _Ctx(cid)
    return [{"id": r["wid"], "label": r["world"],
             "detail": f"{r['n']} image{'s' if r['n'] != 1 else ''}",
             "fix": f"/worlds/{r['wid']}"}
            for r in _world_describe_counts(ctx.worlds())]


def _world_gap_items(cid: str, which: int) -> list[dict]:
    """One row per character, naming it -- the read `_world_char_gaps` skips.

    The name costs a frontmatter read per row, which is why the count does not
    pay it. Here it is the point: a list of slugs is not something a reader can
    act on, and a library with two Maras is exactly when it matters.
    """
    ctx = _Ctx(cid)
    out = []
    for c in ctx.world_char_gaps()[which]:
        try:
            root = store.worlds.paths.world_root(c["wid"])
            name = store.characters.read_character(root, c["id"])["meta"].get("name") or c["id"]
        except (OSError, KeyError, store.characters.CharacterNotFound,
                store.worlds.paths.WorldNotFound):
            name = c["id"]
        out.append({"id": f"{c['wid']}:{c['id']}", "label": str(name),
                    "detail": c["world"], "fix": f"/worlds/{c['wid']}"})
    return out


def _items_world_taglines(cid: str) -> list[dict]:
    return _world_gap_items(cid, 0)


def _items_world_anchors(cid: str) -> list[dict]:
    return _world_gap_items(cid, 1)


#: What expanding a chore shows. Deliberately a SECOND pass rather than part of
#: the list: naming every instance of every chore on every read is the cost the
#: list is built to avoid, and the reader only ever expands one.
ITEMS = {
    "unreviewed": _items_unreviewed,
    "open-scenes": _items_open_scenes,
    "sheets": _items_sheets,
    "anchors": _items_anchors,
    "taglines": _items_taglines,
    "owed": _items_owed,
    "unpriced": _items_unpriced,
    "world-describe": _items_world_describe,
    "world-taglines": _items_world_taglines,
    "world-anchors": _items_world_anchors,
}


def live(cid: str) -> dict:
    """The chore list split into what counts and what has been waved off."""
    off = store.chores.ignored()
    every = _chores(cid)
    live_chores = [c for c in every if c["id"] not in off]
    return {
        "chores": live_chores,
        "ignored": [c for c in every if c["id"] in off],
        # The badge number, and it is the one the reader still cares about:
        # an ignored chore is not counted anywhere.
        "count": sum(1 for c in every if c["id"] not in off),
        # The headings, in reading order, and only the ones that have
        # something under them. Sent rather than inferred by the view: a view
        # that derived the order from the chore list would reorder its own
        # headings whenever the data moved, which is the defect this replaces.
        "groups": _groups(live_chores),
    }


def _groups(chores: list[dict]) -> list[str]:
    """The groups present in `chores`, in `GROUP_ORDER`, unknowns last.

    Unknowns are appended in chore order rather than dropped: a heading this
    module can emit but did not think to list is a bug in `GROUP_ORDER`, and
    hiding the chores under it would make that bug invisible instead of merely
    misplaced.
    """
    present = {c["group"] for c in chores}
    known = [g for g in GROUP_ORDER if g in present]
    rest: list[str] = []
    for c in chores:
        if c["group"] not in GROUP_ORDER and c["group"] not in rest:
            rest.append(c["group"])
    return known + rest


@router.get("/todo")
def get_todo(campaign: str = ""):
    return live(campaign)


@router.get("/todo/{chore_id}/items")
def get_todo_items(chore_id: str, campaign: str = ""):
    """The instances behind one chore's count.

    On demand, because this is the expensive half: `sheets` sweeps the cast and
    `taglines` walks the roster, and doing that for every chore whenever the
    page opens is what would make the page not worth opening. The reader
    expands one.
    """
    if chore_id not in KNOWN:
        raise HTTPException(400, f"unknown chore: {chore_id}")
    if not campaign and chore_id not in LIBRARY_IDS:
        # A campaign chore with no campaign has nothing to enumerate, and its
        # item function would be asked to resolve an empty id.
        return {"items": [], "total": 0, "truncated": False}
    items = ITEMS[chore_id](campaign)
    return {"items": items[:ITEM_CAP], "total": len(items),
            # Said out loud rather than left to be inferred from a short list:
            # a cap nobody mentions reads as "that is all of them".
            "truncated": len(items) > ITEM_CAP}


@router.put("/todo/{chore_id}/ignored")
def put_todo_ignored(chore_id: str, body: dict):
    """Wave a chore off, or take it back.

    The id is checked against the chores this store can actually produce rather
    than accepted blind: an ignore set that accumulates ids nothing emits is a
    file that grows forever and silences things nobody can name.
    """
    if chore_id not in KNOWN:
        raise HTTPException(400, f"unknown chore: {chore_id}")
    on = bool(body.get("ignored"))
    store.chores.set_ignored(chore_id, on)
    return {"ok": True, "ignored": sorted(store.chores.ignored())}
