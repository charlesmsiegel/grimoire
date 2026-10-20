"""todo: continuity responsibilities."""

from __future__ import annotations

from ... import store
from ...store.continuity import candidates as continuity_candidates
from ...store.continuity import pending as continuity_pending
from . import _context, _sources
from ._router import router as router


def _chore_owed(ctx: _context._Ctx) -> dict | None:
    cid = ctx.cid
    # `_owed`'s count, off the continuity summary: any failure is unknown and
    # the chore is absent (spec 04 section 3.5).
    n = ctx.continuity()["owed"]
    if not n:
        return None
    return {
        "id": "owed", "scope": "campaign", "group": "Commitments", "severity": "warn", "n": n,
        "what": f"{n} open commitment{'s' if n != 1 else ''} with a deadline",
        "why": "A promise with a date is the kind the campaign is expected to answer, "
               "and the ledger is where it is still waiting.",
        "fix": _sources._ledger_href(cid, "commitments"), "fix_label": "The ledger",
    }


def _chore_continuity_overlaps(ctx: _context._Ctx) -> dict | None:
    n = ctx.continuity()["overlaps"]
    if not n:
        return None
    return {
        "id": "continuity-overlaps", "scope": "campaign", "group": "Continuity",
        "severity": "note", "n": n,
        "what": f"{n} possible overlap{'s' if n != 1 else ''} to review",
        "why": "The last continuity sweep found records that may be the same story business "
               "or belong together, and nothing is merged or linked until you review it.",
        "fix": _sources._ledger_href(ctx.cid, "continuity", "overlaps"),
        "fix_label": "Continuity review",
    }


def _chore_continuity_closures(ctx: _context._Ctx) -> dict | None:
    summary = ctx.continuity()
    n = summary["closures"]
    if not n:
        return None
    # The chore counts both lifecycle kinds, which the Ledger lists in two
    # groups: a campaign with resolutions only must not land on an empty one.
    threads = summary["closures_threads"]
    return {
        "id": "continuity-closures", "scope": "campaign", "group": "Continuity",
        "severity": "note", "n": n,
        "what": f"{n} record{'s' if n != 1 else ''} that may be finished",
        "why": "The last continuity sweep found threads or commitments that may be finished, "
               "and nothing is closed or resolved until you review it.",
        "fix": _sources._ledger_href(ctx.cid, "continuity", "closures" if threads else "resolutions"),
        "fix_label": "Continuity review",
    }


def _items_owed(cid: str) -> list[dict]:
    try:
        owed = _sources._owed(cid)
    except (OSError, ValueError):
        return []
    return [{"id": c["id"], "label": c["title"],
             "detail": " · ".join(x for x in (f"due {c['due']}", c.get("kind"),
                                              c.get("latest_beat")) if x),
             "fix": _sources._ledger_href(cid, "commitments", c["id"])} for c in owed]


#: What a continuity item says it is, in §30's hedged wording.
_CONTINUITY_KIND = {
    "possible_duplicate": "Possible overlap",
    "possible_relation": "Possible relation",
    "possible_thread_closure": "May be finished",
    "possible_commitment_resolution": "Needs resolution review",
}


def _continuity_items(cid: str, chore_id: str) -> list[dict]:
    """One row per live finding of the chore, opening that finding in its own
    Ledger group. A proposal is named only through `pending.DECISION_LABELS`,
    so a raw decision word never reaches the page (Decision 25)."""
    try:
        if not continuity_candidates.read(cid)["records"]:
            return []
        current = continuity_pending.Current.load(cid)
        found = continuity_pending.findings(cid, current)
    except (OSError, ValueError, store.CampaignNotFound):
        return []
    out = []
    for key, record, verdict in found:
        kind = record["kind"]
        if verdict != "live" or continuity_pending.CHORE_OF.get(kind) != chore_id:
            continue
        proposal = record["proposal"] or {}
        suggested = continuity_pending.DECISION_LABELS.get(proposal.get("decision", ""), "")
        out.append({"id": key, "label": continuity_pending.label(current, record["refs"]),
                    "detail": " · ".join(x for x in (_CONTINUITY_KIND[kind], suggested) if x),
                    "fix": _sources._ledger_href(cid, "continuity", continuity_pending.GROUP_OF[kind],
                                        key)})
    return out


def _items_continuity_overlaps(cid: str) -> list[dict]:
    return _continuity_items(cid, "continuity-overlaps")


def _items_continuity_closures(cid: str) -> list[dict]:
    return _continuity_items(cid, "continuity-closures")
