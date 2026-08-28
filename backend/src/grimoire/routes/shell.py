"""Everything the nav rail badges, in one read.

The rail is persistent chrome: it renders beside every page and refetches on
every navigation. That is the whole constraint this module is built around, and
it is why the payload looks thinner than the design that asked for it.

Three rules, none of them invented here:

*A count nobody can answer cheaply is ``None``, never ``0``.* The rail renders
no tail for ``None`` and renders ``0`` for ``0``. It is ``CLAUDE.md``'s cost
rule -- "a price nobody reported is never rendered as zero" -- applied to
counts, for the same reason: an answer nobody computed is worse than silence.
Every optional field is nullable from the first commit, so a later slice
filling one in is a value change rather than a schema change.

*Nothing here reads a transcript body to find which scenes are open.*
Open-scene detection comes off scene frontmatter (``done``), which is what
``scenes.read.list_scenes`` already does through ``parse_frontmatter_head``.
A campaign can hold a great many scenes and this runs on every navigation, so
the rail must never be the thing that scans all of them.

Once a scene is known to be open, ITS OWN transcript is fair game: ``turns``
reads exactly that scene's body to count model replies, off the same walk
``scenes._model_blocks`` counts turn boundaries in for reroll and drift
measurement -- reusing that walk rather than a second opinion about what
counts as a reply. What this route pays for is bounded by how many scenes are
open at once, not by the campaign's whole history: the same number ``open``
already carries, and the one ``_chore_open_scenes`` treats as worth a note
only past two.

*The money in this payload is all-time, and it is three figures.* The design
puts a spend figure on the rail's Costs row, and this route shipped without one
because the rollup behind it is built on ``usage.lifetime_since`` -- "backs the
all-time view and nothing on the play path", and the rail *is* the play path.
Substituting a bounded window instead would have given the same unlabelled
figure a different meaning, which is the drift the three-money-columns rule
exists to prevent. So the Costs row waited for the maintained aggregate rather
than for a cheaper lie, and ``store.usage_rollup`` is it: the same all-time
figure, read through a byte bookmark into each month file so the cost is what
has been played since the last navigation rather than the library's age.

The three columns arrive apart and are never summed here or anywhere else, and
``partial`` says when the aggregate could not be brought up to date -- which is
the one case a badge must render as silence rather than as ``$0.00``.

Read-only throughout: no campaign lock is taken and none is needed, which is
also why this module wants no ``store.locks`` classification -- that guard
covers modules that *mutate* campaign-scoped state.
"""

from __future__ import annotations

import json

from fastapi import APIRouter

from .. import store
from . import todo as todo_routes

router = APIRouter()


def _pending(cid: str) -> tuple[int, list[dict]]:
    """Undecided proposals across every scene holding a pending review.

    A directory glob, then one small read per sidecar -- and there is normally
    at most one. The scenes themselves are never opened: a review lives beside
    its transcript as ``<sid>.review.json``, so "which scenes are waiting" is a
    listing rather than a scan.

    A sidecar that cannot be read is skipped rather than fatal. This feeds
    chrome, and one malformed record must not take the whole app's navigation
    with it.
    """
    total = 0
    scenes: list[dict] = []
    d = store.scenes.paths._scenes_dir(cid)   # paths-ok: the resolver itself
    if not d.exists():
        return 0, []
    for path in sorted(d.glob("*.review.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            edits = record.get("review", {}).get("edits", [])
        except (OSError, ValueError, AttributeError):
            continue
        sid = path.name[: -len(".review.json")]
        total += len(edits)
        scenes.append({"sid": sid, "proposals": len(edits)})
    return total, scenes


def _scene_turns(cid: str, sid: str) -> int | None:
    """How many of `sid`'s transcript blocks are actual model replies.

    `scenes._model_blocks`, not a raw count of assistant-role messages: a
    manual dice roll and a scene transition are both stored as assistant-role
    blocks (`serialize.ROLL_SPEAKER`, `TRANSITION_SPEAKER`) but neither is a
    reply a model wrote, and `_model_blocks` is the one place that distinction
    is already made -- the same walk `turn_sizes` is expressed in, so this
    cannot answer a different question than reroll and drift measurement do.

    `None`, not `0`, when the scene cannot be read or its transcript cannot be
    decoded: a scene nobody could open is not a scene confirmed to hold no
    replies, and the rail tells those two apart the way every other count
    here does. A scene that opens cleanly and truly has none -- a fresh scene
    still waiting on its opener -- reports the real `0`.
    """
    try:
        messages = store.scenes.read.read_scene(cid, sid)["messages"]
    except (OSError, UnicodeDecodeError, store.CampaignNotFound, store.SceneNotFound):
        return None
    return len(store.scenes._model_blocks(messages))


def _images_undescribed(wid: str) -> int | None:
    """How many stored images across `wid` carry no description, or `None`
    when the world cannot be read.

    Same bases and the same per-world error contract as
    `todo._world_describe_counts` -- an unreadable world directory must not
    500 `/api/shell`, which this feeds on every navigation just as `/api/todo`
    does. `undescribed_count`, not `len(undescribed(...))`, for the reason
    that function's own docstring gives: the list resolves an extension and a
    cache-busting token per image that a count does not need.
    """
    try:
        root = store.worlds.paths.world_root(wid)
        return sum(store.image_descriptions.undescribed_count(root, base)
                   for base in todo_routes._DESCRIBE_BASES)
    except (OSError, store.worlds.paths.WorldNotFound):
        return None


def _campaign_block(cid: str) -> dict | None:
    """The open campaign's badges, or ``None`` if `cid` does not resolve.

    ``None`` rather than a 404 on purpose: the rail asks with an id remembered
    in the browser, which may name a campaign that has since been deleted or
    that belongs to a library the store no longer points at. That is an
    ordinary state, not an error, and answering it with a status code would
    make the client treat a dropped connection and a deleted campaign as the
    same event -- which is exactly the confusion that erases valid state.
    """
    try:
        meta = store.campaigns.read.read_campaign(cid)["meta"]
    except store.CampaignNotFound:
        return None

    wid = str(meta.get("world", ""))
    scenes = store.scenes.read.list_scenes(cid)
    # `done` is the absorbed mark. Read with the same tolerance
    # `list_scenes` applies to it: the file is hand-editable, and a rail that
    # called a scene open while the absorb guard called it done would be worse
    # than either answer alone.
    #
    # `turns` is read off the scene's OWN transcript, not derived from
    # frontmatter -- there is no cheap field for it. `_scene_turns` bounds the
    # cost to the scenes that are actually open, which is what makes reading a
    # transcript body acceptable on a route that runs on every navigation; see
    # the module docstring.
    open_scenes = [{"sid": s["id"], "title": s["title"], "turns": _scene_turns(cid, s["id"])}
                   for s in scenes if not s["done"]]

    unreviewed, pending = _pending(cid)

    # `coverage` reads one sheet per cast member, which is bounded by the cast
    # rather than by the campaign's age -- the reason it is here and the
    # ledger's lifetime rollup is not. `{}` means no mechanics module is
    # bound, which is a legal state and not a missing answer, so it reports
    # None and the row goes quiet rather than claiming 0 of 0.
    cov = store.sheets.coverage(cid)
    sheets = None
    if cov:
        sheets = {"sheeted": sum(k["sheeted"] for k in cov.values()),
                  "total": sum(k["total"] for k in cov.values())}

    return {
        "id": cid,
        "name": str(meta.get("name", cid)),
        # The id alongside the name, matching `CampaignMeta`'s own
        # `world`/`world_name` pairing -- the hub needs it to link at the
        # world's own pages (`/worlds/{wid}?section=images` for the row
        # below), and `world_name` alone cannot address one.
        "world": wid,
        "world_name": store.worlds.read.world_name(wid) or wid,
        "scenes": len(scenes),
        "open": open_scenes,
        "ledger_open": len(store.commitments.open_commitments(cid)),
        "sheets": sheets,
        "unreviewed": unreviewed,
        # Which scenes are holding one, so the hub can name them and link
        # straight at the wrap-up rather than making the reader hunt.
        "pending": pending,
        # `undescribed` is images with no description text -- deliberately
        # not `untagged`, which the design keeps as a separate word for
        # greeting art with no subjects recorded.
        "images_undescribed": _images_undescribed(wid),
        # All-time money, and the three columns arrive apart because there is
        # no shape in which they arrive together. See `_money`.
        "money": _money(cid),
    }


def _money(cid: str) -> dict:
    """What this campaign has cost, over the ledger's whole history.

    This is the field the module docstring above spent its third rule saying
    could not exist. What changed is not the rule but what it costs to obey:
    ``store.usage_rollup`` keeps a byte bookmark into each month file, so the
    steady-state read is one ``stat`` per month plus the handful of lines the
    last turn appended -- bounded by what has been played since the last
    navigation rather than by the library's age. The figure is still the
    all-time one ``CostsView`` shows, arrived at without re-reading history.

    Three columns and never a total, exactly as everywhere else, plus
    ``partial`` -- which is what stops a rail badge rendering "could not
    count" as ``$0.00``. Never raises: the aggregate fail-softs to a partial
    answer, and a cost badge must not be able to take the app's navigation
    with it.
    """
    return store.usage_rollup.campaign_totals(cid)


def _most_recent(campaigns: list[dict]) -> str:
    """Which campaign the rail opens on when the browser remembers none.

    A fresh browser has no id to ask with, so without this the rail stays one
    tier tall until the reader navigates into a campaign -- on exactly the
    devices where navigating there is hardest. The campaign last played is the
    one they are most likely to have meant.

    Ranked the way the shelf ranks, and for the shelf's reason: `campaign.md`'s
    `updated` only moves on a metadata write, so ordering by it alone puts a
    campaign renamed months ago above one played into last night. The activity
    stamp is the campaign's high-water mark, and `best_stamp` is what keeps a
    `zzzz` out of a bad sync from winning the comparison and then blocking its
    own replacement.

    What this deliberately does NOT do is what `GET /campaigns` does -- fold in
    every scene's `updated`. That is a directory listing per campaign, and this
    route runs on every navigation; the standalone stamp is one small read per
    campaign and is written by every campaign-scoped mutation, so it answers
    the same question without the walk. A campaign whose stamp file is missing
    or unreadable falls back to `updated`, and a store where none of them has
    either keeps `list_campaigns`' own order -- newest `updated` first -- by
    accepting only a strictly greater stamp.
    """
    best_id, best = "", ""
    for c in campaigns:
        cid = str(c["id"])
        stamp = store.campaigns.read.best_stamp(
            str(c.get("updated", "")), store.campaigns.read.read_activity(cid))
        if not best_id or stamp > best:
            best_id, best = cid, stamp
    return best_id


@router.get("/shell")
def get_shell(campaign: str = ""):
    """The rail's badges. `campaign` is the id the browser remembers, if any.

    An id that was asked for and does not resolve stays `campaign: null` rather
    than falling back to `_most_recent`, and the asymmetry is load-bearing. The
    client clears its remembered id only when a successful read says that id
    resolves to nothing; handing it a substitute would leave a deleted
    campaign's id in storage permanently, and the rail would go on asking with
    it. The fallback answers the read *after* that clear.

    `library` is deliberately absent: the number of library sections is a fact
    the frontend already holds in `librarySections.ts`, and answering it from
    here as well would be one manifest in two languages with nothing holding
    them level -- a seventh section would ship a badge of six.
    """
    campaigns = store.campaigns.read.list_campaigns()
    block = _campaign_block(campaign) if campaign else None
    if not campaign:
        fallback = _most_recent(campaigns)
        block = _campaign_block(fallback) if fallback else None
    # The badge is scoped to the campaign the payload actually carries, so the
    # rail's count and the Todo page below it are answering about the same
    # campaign. An id that resolved to nothing scopes to the library, which is
    # what the reader is left looking at.
    return {
        "campaigns": len(campaigns),
        "campaign": block,
        # How many things the app noticed that the user has not waved off. The
        # badge is the number they still care about, so an ignored chore is not
        # in it -- see `store.chores`.
        # Not campaign-gated any more: the library chores (an undescribed
        # image backlog, a world whose cast has no taglines) have an answer
        # before a campaign is chosen, and that is exactly when a freshly
        # imported world's backlog is largest. A `null` here would draw no
        # tail over a list that has entries.
        "todo": todo_routes.badge_count(block["id"] if block else ""),
    }
