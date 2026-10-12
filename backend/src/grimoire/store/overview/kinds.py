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
from ..continuity import candidates as continuity_candidates
from ..continuity import doc as continuity_doc
from ..continuity import effective as continuity_effective
from ..continuity import pending as continuity_pending


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
