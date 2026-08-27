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

*Nothing here reads a transcript body.* Open-scene detection comes off scene
frontmatter (``done``), which is what ``scenes.read.list_scenes`` already
does through ``parse_frontmatter_head``. A campaign can hold a great many
scenes and this runs on every navigation, so the rail must never be the thing
that reads them.

*There is no money in this payload, deliberately.* The design puts a spend
figure on the rail's Costs row. The rollup behind that figure is built on
``usage.lifetime_since``, whose own docstring says it "backs the all-time view
and nothing on the play path" -- and the rail *is* the play path, on every
navigation. Substituting a bounded window instead would give the same
unlabelled figure a different meaning, which is the drift the three-money-
columns rule exists to prevent. The Costs row ships with no tail until a
maintained aggregate exists to feed it.

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
    # `turns` is None and stays None in this slice. Scene frontmatter carries
    # no turn count, and the only cheap candidate would undercount a scene
    # written before it existed -- so the rail renders no tail rather than a
    # number that is wrong for exactly the oldest scenes.
    open_scenes = [{"sid": s["id"], "title": s["title"], "turns": None}
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
        "world_name": store.worlds.read.world_name(wid) or wid,
        "scenes": len(scenes),
        "open": open_scenes,
        "ledger_open": len(store.commitments.open_commitments(cid)),
        "sheets": sheets,
        "unreviewed": unreviewed,
        # Which scenes are holding one, so the hub can name them and link
        # straight at the wrap-up rather than making the reader hunt.
        "pending": pending,
        # Filled by the images slice. `undescribed` is images with no
        # description text -- deliberately not `untagged`, which the design
        # keeps as a separate word for greeting art with no subjects recorded.
        "images_undescribed": None,
    }


@router.get("/shell")
def get_shell(campaign: str = ""):
    """The rail's badges. `campaign` is the id the browser remembers, if any.

    `library` is deliberately absent: the number of library sections is a fact
    the frontend already holds in `librarySections.ts`, and answering it from
    here as well would be one manifest in two languages with nothing holding
    them level -- a seventh section would ship a badge of six.
    """
    return {
        "campaigns": len(store.campaigns.read.list_campaigns()),
        "campaign": _campaign_block(campaign) if campaign else None,
        # How many things the app noticed that the user has not waved off. The
        # badge is the number they still care about, so an ignored chore is not
        # in it -- see `store.chores`.
        "todo": todo_routes.live(campaign)["count"] if campaign else None,
    }
