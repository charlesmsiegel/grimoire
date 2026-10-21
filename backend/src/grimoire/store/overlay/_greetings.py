"""Greeting and plot-map inheritance, edits and edge cleanup."""

from __future__ import annotations

from .. import atomic, cards, characters, greetings, locks
from ..paths import natural_key
from . import _actors, _copying, _identity, _ledgers, _record_paths, _transitions, _views

# ---- greetings + plot map ----

def list_greetings(cid: str) -> list[dict]:
    mine = greetings.list_greetings(_record_paths.croot_of(cid))
    have = {g["id"] for g in mine}
    gone = _ledgers.deleted(cid)
    inherited = [g for g in greetings.list_greetings(_record_paths.wroot_of(cid))
                 if g["id"] not in have and _record_paths.flat_ref("greetings", g["id"]) not in gone]
    out = mine + inherited
    out.sort(key=lambda m: natural_key(m["name"]))
    return out


def read_greeting(cid: str, gid: str, *, v: _views.View | None = None) -> dict:
    v = _views._view(cid, v)
    try:
        return greetings.read_greeting(v.croot, gid)
    except greetings.GreetingNotFound:
        if _record_paths.flat_ref("greetings", gid) in v.gone:
            raise
        return greetings.read_greeting(v.wroot, gid)


def read_greeting_rev(cid: str, gid: str) -> dict:
    """`read_greeting` carrying the rev of whichever layer answered (#35).

    Its write-side counterpart is `entity_rev(cid, "greetings", gid)`: greetings
    are a flat `<root>/greetings/<id>.md` record with the same overlay layering
    as any entity, so the resolution rule is one rule, not two.
    """
    try:
        return greetings.read_greeting_rev(_record_paths.croot_of(cid), gid)
    except greetings.GreetingNotFound:
        if _record_paths.flat_ref("greetings", gid) in _ledgers.deleted(cid):
            raise
        return greetings.read_greeting_rev(_record_paths.wroot_of(cid), gid)


def create_greeting(cid: str, name: str, character: str, version: str, body: str = "",
                    requires_tags=None, predecessor_join: str = "all",
                    present=None, pcless: bool = False, location: str = "",
                    phase: str = "", sequence: int | None = None,
                    optional: bool = False) -> str:
    wroot, gone = _record_paths.wroot_of(cid), _ledgers.deleted(cid)

    def taken(gid: str) -> bool:
        return (_record_paths._flat_path(wroot, "greetings", gid).exists()
                or _record_paths._record_dir(wroot, "greetings", gid).is_dir()
                or _record_paths.flat_ref("greetings", gid) in gone)

    # #137: bake {{char}} here, not inside greetings.create_greeting -- a thin
    # campaign's character commonly still lives only in the world, and
    # char_root (not croot_of) is the resolver that finds it there.
    body = cards.bake_char_token(body, greetings.char_name(_actors.char_root(cid, character), character, version))
    gid = greetings.create_greeting(_record_paths.croot_of(cid), name, character, version, body,
                                    requires_tags=requires_tags,
                                    predecessor_join=predecessor_join, present=present,
                                    pcless=pcless, location=location, phase=phase,
                                    sequence=sequence, optional=optional, taken=taken)
    return gid


def update_greeting(cid: str, gid: str, **kwargs) -> None:
    # The greeting's character commonly still lives only in the world (a thin
    # campaign), so both the `{{char}}` bake and a re-point's validation (#17)
    # must resolve through `char_root`, not `croot_of` -- passed down as the
    # store's `char_root` so validation, bake and write agree on one root. The
    # effective character is the incoming re-point when the patch carries one,
    # the stored pointer otherwise; validated HERE, before `_materialize_flat`,
    # so a refused re-point does not leave a world greeting copied campaign-side.
    character = kwargs.get("character")
    version = kwargs.get("version")
    repoint = character is not None or version is not None
    if repoint or kwargs.get("body") is not None:
        meta = read_greeting(cid, gid)["meta"]
        stored = (meta.get("character", ""), meta.get("version", ""))
        effective = stored[0] if character is None else character
        new_ver = stored[1] if version is None else version
        if effective:
            kwargs["char_root"] = _actors.char_root(cid, effective)
            # Only an ACTUAL re-point is validated -- the same skip `_repoint`
            # makes, so a save echoing a dangling pointer still lands, but made
            # here too because by `_repoint` the materialize below has happened.
            if repoint and (effective, new_ver) != stored:
                characters.require_version(kwargs["char_root"], effective, new_ver)
        elif repoint and version and (effective, new_ver) != stored:
            raise characters.CharacterNotFound(effective)
    if not _copying._materialize_flat(cid, "greetings", gid):
        raise greetings.GreetingNotFound(gid)
    greetings.update_greeting(_record_paths.croot_of(cid), gid, **kwargs)


def delete_greeting(cid: str, gid: str) -> None:
    with locks.campaign_lock(cid):
        _ledgers.require_identity_ledgers(cid)
        gid = _identity._canonical_record_id(cid, "greetings", gid)
        ref = _record_paths.flat_ref("greetings", gid)
        source_refs = _identity.capture_record_refs(cid, ref)
        in_world = _identity._inherits_world(cid, ref) and _record_paths._flat_path(_record_paths.wroot_of(cid), "greetings", gid).exists()
        if in_world:
            materialize_plotmap(cid)   # edge cleanup must land campaign-side
        try:
            greetings.delete_greeting(_record_paths.croot_of(cid), gid)
            _transitions._drop_manifest_ref(cid, ref, source_refs=source_refs)
        except greetings.GreetingNotFound:
            if not in_world:
                raise
            greetings.remove_from_plotmap(_record_paths.croot_of(cid), gid)
        if in_world:
            _ledgers.add_deleted(cid, ref)
        _record_paths._drop_record_dir(_record_paths.croot_of(cid), "greetings", gid)
        _transitions._undetach(cid, ref, source_refs=source_refs)


def read_plotmap(cid: str) -> dict:
    croot = _record_paths.croot_of(cid)
    if (croot / "plotmap.json").exists() or "plotmap" in _ledgers.deleted(cid):
        # The campaign's OWN map. Its nodes and edges name the campaign's own
        # greetings, detached or not, and filtering them would delete plot
        # relationships the campaign authored (Codex review).
        return greetings.read_plotmap(croot)
    return _without_detached(cid, greetings.read_plotmap(_record_paths.wroot_of(cid)))


def _without_detached(cid: str, plotmap: dict) -> dict:
    """Drop detached greetings from a plot map, node and inbound edges alike.

    Only ever applied to an INHERITED map. A campaign that owns a greeting copy
    but no plot map of its own reads the WORLD's, and the world is where the
    recreated slug's edges are. Materializing a campaign map to clean would fork
    it off the world's over an unrelated record's delete, and would fix only the
    campaigns that had already been swept (Codex review)."""
    gone = {r.partition("/")[2] for r in _ledgers.detached(cid) if r.startswith("greetings/")}
    if not gone:
        return plotmap
    return {gid: {k: [x for x in v if x not in gone] if isinstance(v, list) else v
                  for k, v in edges.items()}
            for gid, edges in plotmap.items() if gid not in gone}


def materialize_plotmap(cid: str) -> None:
    croot, wroot = _record_paths.croot_of(cid), _record_paths.wroot_of(cid)
    if (croot / "plotmap.json").exists() or "plotmap" in _ledgers.deleted(cid):
        return
    src = wroot / "plotmap.json"
    if not src.exists():
        return   # nothing to copy; set_edges will create a fresh campaign map
    text = src.read_text(encoding="utf-8")
    dst = croot / "plotmap.json"
    with _copying._recorded_base(cid, "plotmap", greetings.plotmap_content_hash(text), dst):
        atomic.write_text(dst, text)


def set_edges(cid: str, gid: str, leads_to=None, excludes=None) -> None:
    materialize_plotmap(cid)
    greetings.set_edges(_record_paths.croot_of(cid), gid, leads_to, excludes)
