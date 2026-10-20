"""sync: demote responsibilities."""

from __future__ import annotations

from pathlib import Path

from .. import entities, greetings, journal, overlay, revision
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..worlds import paths as worlds_paths
from . import _move_policy, _publication, _read_shapes

# ---- demote ---------------------------------------------------------------

def dependents(wid: str, kind: str, eid: str) -> list[dict]:
    """The campaigns that would notice this library record going away.

    Every campaign of the world, not only the ones holding a manifest ref: under
    the overlay a campaign with no copy is the one depending on the world record
    *most*, since it has nothing else. Two are excluded, and neither is a
    dependent in any sense -- one that tombstoned the record (it deliberately
    does not have it) and one whose ref is detached (its record is its own, and
    this world record is a stranger sharing the slug).
    """
    wroot = _require_world_record(wid, kind, eid)
    ref = _read_shapes._ref_str(kind, eid)
    out: list[dict] = []
    for cid in overlay.dependent_campaigns(wroot):
        if ref in overlay.deleted(cid) or ref in overlay.detached(cid):
            continue
        try:
            name = campaigns_read.read_campaign(cid)["meta"].get("name", cid)
        except campaigns_paths.CampaignNotFound:
            continue    # deleted between the enumeration and its turn
        out.append({"id": cid, "name": name,
                    "has_copy": overlay.record_text(cid, kind, eid) is not None})
    return sorted(out, key=lambda d: d["id"])


def _require_world_record(wid: str, kind: str, eid: str) -> Path:
    if not worlds_paths.world_exists(wid):
        raise worlds_paths.WorldNotFound(wid)
    wroot = worlds_paths.world_root(wid)
    if kind in appearances_paths.ACTOR_KINDS:
        raise _move_policy.NotDemotableError(f"{kind} cannot be demoted — see #52's v1 boundary")
    _move_policy._flat_kind(kind)
    _move_policy._safe_ref(kind, eid)
    if not _move_policy._world_file(wroot, kind, eid).exists():
        raise entities.EntityNotFound(_read_shapes._ref_str(kind, eid))
    return wroot


def _demote_scope(wroot: Path, kind: str, eid: str, captured: list[str]) -> tuple[list[str], list[dict]]:
    """Current bound scope and copy candidates, inside its one sorted hold."""
    bound: list[str] = []
    deps: list[dict] = []
    for cid in captured:
        try:
            meta = campaigns_read.read_campaign(cid)["meta"]
        except campaigns_paths.CampaignNotFound:
            continue
        if not worlds_paths.references_world(meta.get("world", ""), wroot):
            continue
        severed, gone = overlay.require_identity_ledgers(cid)
        journal.require_readable(cid)
        bound.append(cid)  # hidden/detached campaigns still need cleanup preflight
        if (overlay.record_is_marked(cid, kind, eid, gone)
                or overlay.record_is_marked(cid, kind, eid, severed)):
            continue
        deps.append({"id": cid, "name": meta.get("name", cid),
                     "has_copy": overlay.record_text(cid, kind, eid) is not None})
    return bound, deps


def _prepare_demote(wroot: Path, kind: str, eid: str, held: list[str],
                    deps: list[dict], *, copy_down: bool) -> str | None:
    """Validate the whole mutation scope and freeze its copy source.

    Called inside the sorted campaign hold, before the effect/stamp span.
    A refusal here must leave every earlier dependent and the World untouched.
    """
    for cid in held:
        overlay.require_identity_ledgers(cid)
        journal.require_readable(cid)
    overlay.require_world_identity_scope(wroot, kind, eid, held)
    if not copy_down:
        return None
    text = _move_policy._world_file(wroot, kind, eid).read_text(encoding="utf-8")
    for dep in deps:
        overlay.require_group_materialization(dep["id"], kind, eid, source_text=text)
    return text


def demote(wid: str, kind: str, eid: str, *, copy_down: bool = True,
           target: str | None = None) -> dict:
    """Take a record out of the library, optionally leaving each dependent
    campaign holding its own copy.

    The delete itself is the world route's ordinary one, followed by
    `overlay.forget_world_record` -- the sweep that already knows how to detach
    the campaigns holding copies and drop their now-meaningless bases. This
    function's own work is the copy-down that happens *first*, so that a
    campaign which would otherwise simply lose the record keeps it.

    Copy-down before delete, and aborting if any of it fails: a partial
    copy-down leaves campaigns holding copies they would have been given anyway
    and the library record still standing, which is the state before the call.
    Deleting first and copying after would lose the record outright for every
    campaign the copy never reached.

    `target` narrows who gets the copy — it does NOT narrow the delete, which
    is the whole operation. Every other dependent loses the record exactly as
    `copy_down=False` would have them lose it. That is destructive by request
    rather than by accident (a target naming no dependent is refused below),
    and the app's own demote UI does not offer it: `DemotePanel` always demotes
    to every dependent.
    """
    wroot = _require_world_record(wid, kind, eid)
    copied: list[str] = []
    # `every`, not `deps`: the copy-down may be narrowed to one campaign, but
    # the sweep below rewrites sync.md and detached.json in ALL of them, and a
    # lock has to cover every file this call touches. One demotion mutating
    # several campaigns is exactly the shape `hold_all` exists for -- it is the
    # only sanctioned way to hold more than one campaign lock, and it sorts, so
    # this cannot be the holder that wedges against another (#267, Codex review).
    # The sweep also reaches campaigns already hiding this record, which
    # `dependents` deliberately omits from copy-down. Their markers still
    # change, so the hold and preflight must include them (spec 07).
    with overlay.world_identity_scope(wroot) as lifetime:
        # Discovery is a preview, not a publication scope. A campaign may be
        # deleted/recreated or rebound while this operation waits for its hold.
        # Recheck its World and every copy/marker inside that same hold; never
        # process an earlier dependency outside the one captured set (spec 07).
        held, every = _demote_scope(wroot, kind, eid, list(lifetime.bound))
        deps = every
        if target is not None:
            # Refused, not filtered to nothing. A typo or retired target must
            # not mean "copy down nowhere, then delete the World record".
            if not any(d["id"] == target for d in every):
                raise _move_policy.UnknownTargetError(
                    f"{target} is not a campaign that depends on {kind}/{eid}")
            deps = [d for d in every if d["id"] == target]
        source_text = _prepare_demote(wroot, kind, eid, held, deps, copy_down=copy_down)
        # Every dependent's write token (#409), inside the hold that covers the
        # writes and in a `finally` so the failure path reaches it too. This is
        # a WORLD route, so nothing in `/api/campaigns/...` stamps these
        # campaigns -- and the sweep below rewrites sync.md and detached.json in
        # all of them, whether or not they took a copy. A caller holding one of
        # their tokens would otherwise confirm an operation priced before a
        # record it depends on was demoted into it.
        #
        # The `finally` is what a success-only stamp got wrong: the copy-down
        # writes one dependent at a time, so a raise part-way through leaves the
        # earlier ones written, and a world route's 5xx reaches no middleware
        # that could repair it (Codex review). Which ones were reached is not
        # knowable here without threading a record through every helper, so all
        # of them are stamped -- the ones that were not written pay a re-price,
        # which is the direction this value is deliberately wrong in.
        # `revision.bump` never raises, so this cannot displace the error that
        # got here.
        try:
            if copy_down:
                for dep in deps:
                    # The art goes down for EVERY dependent in scope, including the
                    # ones that already hold their own text. Assets overlay per
                    # file, so a campaign that diverged on wording is still reading
                    # the world's pictures -- and the delete below takes the world's
                    # record directory with it (`overlay.forget_world_record`).
                    overlay.copy_record_dir_down(dep["id"], kind, eid)
                    if kind == "greetings":
                        # The greeting's edges live in the plot map, not in the
                        # greeting -- and `delete_greeting` strips them from the
                        # world's. A campaign still inheriting that map would watch
                        # the record it was just given lose its prerequisites and
                        # unlock itself, which is the opposite of keeping it
                        # (Codex review). Its own map, taken before the delete.
                        overlay.materialize_plotmap(dep["id"])
                    # Even an owned preview must pass through the locked
                    # materializer: take-world may have removed it before this
                    # hold, and the copy is what survives World deletion.
                    if not overlay._materialize_flat(dep["id"], kind, eid, source_text=source_text):
                        raise entities.EntityNotFound(_read_shapes._ref_str(kind, eid))
                    if not dep["has_copy"]:
                        copied.append(dep["id"])
            overlay.retire_world_reviews(wroot, kind, eid, lifetime.captured)
            if kind == "greetings":
                greetings.delete_greeting(wroot, eid)   # also unwires the world's plot map
            else:
                entities.delete_entity(wroot, kind, eid)
            overlay.forget_world_record(wroot, kind, eid, cids=held)
        finally:
            for cid in held:
                revision.bump(cid)
    _publication._touch_world(wroot)
    return {"copied_down": sorted(copied),
            "dependents": [d["id"] for d in deps]}
