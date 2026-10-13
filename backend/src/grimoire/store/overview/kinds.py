"""The overview projections' computes, and the live path beside each.

A compute here receives bytes, never a path (spec 04 section 3.3): its answer
is a property of the bytes it was handed, which is what lets a key built from
those bytes remember it. Each also has a live path -- today's code over the
store's own reads -- which answers whenever the bytes could not be had, and
which is the same code: the byte-fed form runs it under
`ledger_texts.feeding`, so the two cannot disagree.

No compute reads a clock. `continuity` is the one with a call graph deep
enough to need saying so: `effective` and `pending.verdict` read none, and the
owed count "does no calendar work" by design (`routes/todo._owed`).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .. import commitments, events, ledger_texts, plot
from ..campaigns import read as campaigns_read
from ..continuity import candidates as continuity_candidates
from ..continuity import doc as continuity_doc
from ..continuity import effective as continuity_effective
from ..continuity import pending as continuity_pending
from ..frontmatter import parse_frontmatter
from ..scenes import read as scenes_read
from ..scenes import serialize as scenes_serialize
from ..scenes import turns as scenes_turns


def world_row(text: str) -> dict:
    """A world card's row off `world.md`'s text: `{name, created, updated,
    genre, module}`, with `name` None when the key is absent so the caller
    applies the directory-name fallback (path-derived fields stay outside the
    cache; spec 04 section 3.2). `module` is the world's raw default, the
    campaign card's chip falls back to it (Open question 4, option b)."""
    meta, _ = parse_frontmatter(text)
    return {"name": meta.get("name"), "created": meta.get("created", ""),
            "updated": meta.get("updated", ""), "genre": meta.get("genre", ""),
            "module": (meta.get("module") or "").strip()}


def campaign_row(text: str) -> dict:
    """`campaigns.read._campaign_row_value`'s row off `campaign.md`'s text,
    with `name` None when absent (see `world_row`) and the campaign's raw
    `module` setting ("" inherit, "none" off, else a module id)."""
    meta, body = parse_frontmatter(text)
    return {"name": meta.get("name"), "world": meta.get("world", ""),
            "created": meta.get("created", ""), "updated": meta.get("updated", ""),
            "parent": meta.get(campaigns_read.PARENT_KEY, ""),
            "forked_from_scene": meta.get(campaigns_read.FORKED_AT_KEY, ""),
            "blurb": campaigns_read._first_paragraph(body),
            "module": (meta.get("module") or "").strip()}


def card_module(campaign_setting: str, world_default: str) -> str:
    """The chip a campaign card draws: the campaign's own setting over its
    world's default, with no pack loaded (spec 04 Open question 4, option b).
    `"none"` means mechanics off, and draws no chip whatever the world says."""
    if campaign_setting == "none":
        return ""
    chosen = campaign_setting or world_default
    return "" if chosen == "none" else chosen


def scene_turns(data: bytes, players: tuple[str, ...]) -> int | None:
    """How many of a scene file's transcript blocks are model replies, from
    its bytes and the display names of its seated players (spec 04 section
    3.2) -- `None` when the bytes do not decode, which `read_scene` would
    have raised on. The count is `turns._model_blocks` over the same parse
    `read_scene` makes, so the shell cannot answer a different question than
    reroll and drift measurement do."""
    try:
        text = ledger_texts.decode(data)
    except UnicodeDecodeError:
        return None
    _meta, body = parse_frontmatter(text)
    messages = scenes_serialize._parse_messages(body, frozenset(players))
    return len(scenes_turns._model_blocks(messages))


def scene_turns_live(cid: str, sid: str) -> int:
    """`scene_turns`' live path: `read_scene` and `_model_blocks`, the
    shell's count before 04. Raises what `read_scene` raises."""
    messages = scenes_read.read_scene(cid, sid)["messages"]
    return len(scenes_turns._model_blocks(messages))


def continuity_paths(cid: str) -> dict[str, Path]:
    """The five files `continuity` reads, by the name `ledger_texts` feeds them
    under -- each from its own module's resolver, so this cannot name a file
    the reader does not read."""
    return {"plot": plot._path(cid),
            "commitments": commitments._path(cid),
            "events": events._path(cid),
            "continuity": continuity_doc._path(cid),
            "candidates": continuity_candidates._path(cid)}


def continuity(cid: str, files: Mapping[str, bytes | None]) -> dict:
    """`continuity_live` over exactly `files` (name -> bytes, None for an
    absent file), opening nothing."""
    with ledger_texts.feeding(cid, files):
        return continuity_live(cid)


def continuity_live(cid: str) -> dict:
    """What the shell's ledger badge and Todo's three continuity chores read
    off the campaign's ledgers (spec 04 section 3.5)::

        {"ledger_open": int|None,   # live canonical commitments
         "owed": int|None,          # ... of them with a stated `due`
         "overlaps": int,           # live findings for continuity-overlaps
         "closures": int,           # live findings for continuity-closures
         "closures_threads": bool}  # any of those a possible_thread_closure

    Any exception reads as unknown -- `None` for a count, no findings for the
    chores -- which is the one deliberate widening (spec 04 section 3.5): a
    commitments.json holding `[]` used to answer `null` on the shell and a 500
    on the to-do page, and one remembered value cannot do both. Failures that
    come from the bytes are part of the value; a failed READ never reaches
    here, because the caller takes the live path for that request instead.
    """
    try:
        rows = continuity_effective.commitments(cid)
        ledger_open: int | None = len(rows)
        owed: int | None = sum(1 for c in rows if c.get("due"))
    except Exception:  # noqa: BLE001 -- an uncountable ledger is unknown, never a 500 (spec 04 section 3.5)
        ledger_open = owed = None
    try:
        live = [record for _key, record, verdict in continuity_pending.findings(cid)
                if verdict == "live"]
    except Exception:  # noqa: BLE001 -- unreadable findings are no findings, never a 500
        live = []
    overlaps = [r for r in live if continuity_pending.CHORE_OF.get(r["kind"]) == "continuity-overlaps"]
    closures = [r for r in live if continuity_pending.CHORE_OF.get(r["kind"]) == "continuity-closures"]
    return {"ledger_open": ledger_open, "owed": owed,
            "overlaps": len(overlaps), "closures": len(closures),
            "closures_threads": any(r["kind"] == "possible_thread_closure" for r in closures)}
