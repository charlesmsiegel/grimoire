"""The Authoring page's first read (116): ``authoring.source``, one source as the page needs it.

It names the source (title, whether it is a world, its manifest etag), the existing records 087's ``context`` offers
to link to (a world only), whether a suggestion can be sent now and why not, the proposals and audits this app holds
for it in memory, the bounds the page's forms are checked against, and what is not built with its reason. It reads
only.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.authoring import (
    KINDS,
    MAX_CANDIDATES,
    MAX_LINKS,
    RELATIONS,
    ROLES,
    AuthoringError,
)
from grimoire.authoringstore.library import TOPOLOGY, _place_problem
from grimoire.textapi import ApiRefused

from .audits import BOUNDS, audits_of
from .common import refused, source_of
from .proposals import SAVED, library, model_state, proposals_of

#: What the page names as not available, with why: never a control that fails.
UNAVAILABLE = (
    {"feature": "Campaign places and groups", "slice": "087",
     "reason": "087 writes world records only; a campaign's own individuals come through accepted turn effects."},
    {"feature": "Other kinds of record and link", "slice": "087",
     "reason": "087 formalizes places and groups, joined by roads and containment, only."},
    {"feature": "Larger linked regions", "slice": "087", "reason": "087 milestone 3 is not built."},
    {"feature": "Keeping a proposal across a restart", "slice": "087",
     "reason": "087 keeps proposals in memory only: a restart loses one not yet accepted."},
    {"feature": "A model auditor", "slice": "082",
     "reason": "082 has no model worker: the audit is its deterministic consistency check."},
    {"feature": "Repairs from an audit", "slice": "082",
     "reason": "082 milestone 2 (a repair as a reviewed change set) is not built."},
    {"feature": "Resumable or queued audits", "slice": "082",
     "reason": "082 milestone 3 is not built: one audit runs while you wait, and its report is kept in memory."},
)


def _maps(heads: Mapping[tuple[str, str], object]) -> list[dict]:
    """The world's maps (059 topologies) at their heads, as 087 resolves them (a single revision is its own head):
    a draft whose links must extend one of several names it."""
    found = []
    for (kind, entity_id), definition in sorted(heads.items(), key=lambda item: item[0]):
        if kind != TOPOLOGY:
            continue
        name = getattr(getattr(definition, "envelope", None), "name", None)
        found.append({"topology": entity_id, "ref": str(definition.ref), "name": name or None})  # type: ignore[attr-defined]
    return found


def _place(heads: Mapping[tuple[str, str], object], ref) -> bool:  # type: ignore[no-untyped-def]
    """Whether an existing record can end a road or containment: 087's own rule (a ``location``, or any entity with
    ``spatial`` that is not a template), read from its capabilities and never from its kind alone."""
    head = heads.get((ref.kind, ref.entity_id))
    return head is not None and _place_problem(head, ref) is None


def source(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The source the page is about, and what the page can do with it."""
    asked = source_of(args)
    view = ctx.store.load()
    if asked in view.excluded:
        raise ApiRefused("excluded_source", f"{asked} is excluded from this library")
    stored = view.sources.get(asked)
    if stored is None:
        raise ApiRefused("missing_source", f"this library has no source {asked}")
    world = asked.startswith("world:")
    records, truncated, maps = [], False, []
    if world:
        authoring = library(ctx)
        try:
            context = authoring.context(asked)
            # the heads 087 reviews and drafts against, so the page offers exactly the endpoints and maps it takes
            _view, _basis, heads = authoring._world(asked)  # noqa: SLF001 - 087's own resolution, not a copy of it
        except AuthoringError as error:
            raise refused(error, "authoring_refused") from None
        records = [{"ref": str(record.ref), "kind": record.kind, "name": record.name,
                    "place": _place(heads, record.ref)} for record in context.records]
        truncated = context.truncated
        maps = _maps(heads)
    _config, _etag, blocked = model_state(ctx)
    title = stored.manifest.title
    return {
        "source": asked, "title": title if title and title.strip() else None, "world": world, "etag": stored.etag,
        "records": records, "records_truncated": truncated, "maps": maps,
        "suggest": {"available": world and blocked is None, "blocked": blocked},
        "proposals": proposals_of(ctx, asked) if world else [],
        "audits": audits_of(ctx, asked) if world else [],
        "limits": {"candidates": MAX_CANDIDATES, "links": MAX_LINKS, "kinds": sorted(KINDS), "roles": list(ROLES),
                   "relations": list(RELATIONS),
                   "budget": {name: {"low": low, "high": high, "default": default}
                              for name, (low, high, default) in BOUNDS.items()}},
        "unavailable": [dict(item) for item in UNAVAILABLE], "note": SAVED,
    }


__all__ = ["UNAVAILABLE", "source"]
