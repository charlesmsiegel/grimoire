"""Campaigns: strict ledgers, appearances, overlay inheritance and scene transcripts.

V1 reads ``deleted.json`` fail-soft for display: a corrupt file means "nothing
hidden", which resurrects everything the user deleted. An inventory that an
importer will act on cannot do that, so ledgers are read strictly here and a
campaign whose ledgers cannot be trusted is reported ``unresolved``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import _scenes, _text
from ._context import Context
from ._records import Actor, Library

INHERITED_KINDS = ("locations", "lore", "items", "groups", "creatures", "greetings", "characters", "pcs")
ACTOR_KINDS = ("characters", "pcs")
CAMPAIGN_KEYS_SHOWN = ("name", "world", "world_copy", "module", "inference_format", "inference_retired")


@dataclass
class Campaign:
    id: str
    path: str
    name: str | None = None
    world: str | None = None
    world_status: str = "none"     # resolved | none | not_found | case_variant
    layout: str = "overlay"        # overlay | legacy_full_copy
    ledgers_ok: bool = True
    deleted: set[str] = field(default_factory=set)
    detached: set[str] = field(default_factory=set)
    sync: dict[str, str] = field(default_factory=dict)
    appearances: dict[str, dict] = field(default_factory=dict)
    unreadable_scene_membership: bool = False
    scene_texts: dict[str, list[str]] = field(default_factory=dict)
    shown_meta: dict[str, str] = field(default_factory=dict)
    settings_fields: list[str] = field(default_factory=list)

    @property
    def overlay_ok(self) -> bool:
        """Whether V1's overlay rules can be applied to this campaign faithfully."""
        return (self.ledgers_ok and self.layout == "overlay"
                and self.world_status in ("resolved", "none", "not_found"))

    @property
    def inherits(self) -> bool:
        return self.overlay_ok and self.world_status == "resolved"


def _ledger_entry_ok(ref: object, *, visibility: bool) -> bool:
    """V1's ``_valid_ledger_ref``."""
    if not isinstance(ref, str):
        return False
    parts = ref.split("/")
    if len(parts) == 2:
        return (parts[0] in INHERITED_KINDS and _text.safe_id(parts[1])) or parts == ["plotmap", "plotmap"]
    if not visibility:
        return False
    if ref == "plotmap":
        return True
    if len(parts) == 3 and parts[:2] == ["assets", "library"]:
        return _text.safe_id(parts[2])
    return (len(parts) == 5 and parts[0] == "assets" and parts[1] in INHERITED_KINDS
            and all(_text.safe_id(p) for p in parts[2:]))


def read_campaigns(ctx: Context, lib: Library) -> dict[str, Campaign]:
    campaigns: dict[str, Campaign] = {}
    folded = {}
    for wid in lib.worlds:
        folded.setdefault(wid.casefold(), []).append(wid)
    for entry in ctx.snap.files("campaign_meta"):
        cid = entry.layout.coords["scope_id"]
        campaign = Campaign(cid, entry.path.rsplit("/", 1)[0])
        campaigns[cid] = campaign
        if not _text.safe_id(cid):
            ctx.finding("invalid_v1_id", entry.path, detail="campaign")
        text = ctx.read_text(entry)
        meta = _text.parse_frontmatter(text)[0] if text is not None else {}
        campaign.name = meta.get("name")
        world = meta.get("world") or None
        campaign.world = world
        if world is None:
            campaign.world_status = "none"
            ctx.finding("world_missing", entry.path, detail="campaign names no world")
        elif world in lib.worlds and lib.worlds[world]["v1_listed"]:
            campaign.world_status = "resolved"
        elif world in lib.worlds:
            campaign.world_status = "not_found"  # present, but V1 never lists that id
            ctx.finding("world_missing", entry.path, detail="world id V1 does not list")
        elif len(folded.get(world.casefold(), [])) == 1:
            campaign.world_status = "case_variant"
            ctx.finding("world_case_variant", entry.path, detail=world)
            ctx.unresolve("campaign_inheritance", entry.path, "world named in another letter case")
        else:
            campaign.world_status = "not_found"
            ctx.finding("world_missing", entry.path, detail=world)
        if meta.get("world_copy") != "overlay":
            campaign.layout = "legacy_full_copy"
            ctx.finding("legacy_full_copy_campaign", entry.path)
            ctx.unresolve("campaign_inheritance", entry.path, "pre-overlay full-copy campaign")
        if _text.newer_format(meta.get("inference_format")):
            ctx.finding("newer_format", entry.path, detail="inference_format")
        campaign.shown_meta = {k: meta[k] for k in CAMPAIGN_KEYS_SHOWN if k in meta}
        campaign.settings_fields = sorted(k for k in meta if k not in CAMPAIGN_KEYS_SHOWN
                                          and k not in ("created", "updated"))
    for family, reader in (("ledger_deleted", _read_list_ledger), ("ledger_detached", _read_list_ledger),
                           ("ledger_sync", _read_sync), ("ledger_appearances", _read_appearances)):
        for entry in ctx.snap.files(family):
            campaign = campaigns.get(entry.layout.coords["scope_id"])
            if campaign is None:
                ctx.read(entry)
                continue
            reader(ctx, campaign, entry, family)
    return campaigns


def _invalid_ledger(ctx: Context, campaign: Campaign, path: str, why: str) -> None:
    campaign.ledgers_ok = False
    ctx.finding("invalid_ledger", path, detail=why)
    ctx.unresolve("campaign_semantics", campaign.path, f"{path.rsplit('/', 1)[-1]} cannot be read")


def _read_list_ledger(ctx: Context, campaign: Campaign, entry, family: str) -> None:
    value, ok = ctx.read_json(entry)
    if not ok or not isinstance(value, list):
        _invalid_ledger(ctx, campaign, entry.path, "not a JSON list" if ok else "unreadable")
        return
    visibility = family == "ledger_deleted"
    good = set()
    for ref in value:
        if _ledger_entry_ok(ref, visibility=visibility):
            good.add(ref)
        else:
            ctx.finding("invalid_ledger_entry", entry.path, detail=repr(ref)[:80])
    if visibility:
        campaign.deleted = good
    else:
        campaign.detached = good


def _read_sync(ctx: Context, campaign: Campaign, entry, family: str) -> None:
    text = ctx.read_text(entry)
    if text is None:
        _invalid_ledger(ctx, campaign, entry.path, "unreadable")
        return
    meta = _text.parse_frontmatter(text)[0]
    for ref, base in meta.items():
        if base != "" and not _text.SHA256.match(base):
            ctx.finding("invalid_ledger_entry", entry.path, detail=ref)
            ctx.unresolve("sync_base", f"{campaign.path}/{ref}", "invalid sync base")
            base = None
        campaign.sync[ref] = base


def _read_appearances(ctx: Context, campaign: Campaign, entry, family: str) -> None:
    value, ok = ctx.read_json(entry)
    if not ok or not isinstance(value, dict):
        _invalid_ledger(ctx, campaign, entry.path, "not a JSON object" if ok else "unreadable")
        return
    for ref in sorted(value):
        record = value[ref]
        base, _, aid = ref.partition("/")
        problems = []
        if base not in ACTOR_KINDS or not _text.safe_id(aid):
            problems.append("key")
        if not isinstance(record, dict):
            problems.append("record")
            record = {}
        if not isinstance(record.get("version"), str):
            problems.append("version")
        if record.get("role") not in ("player", "npc"):
            problems.append("role")
        scenes = record.get("scenes")
        scenes_ok = isinstance(scenes, list) and all(isinstance(s, str) for s in scenes)
        if not scenes_ok:
            problems.append("scenes")
        if "presence" in record and not isinstance(record["presence"], dict):
            problems.append("presence")
        if "base" in record and not isinstance(record["base"], str):
            problems.append("base")
        row = {"ref": ref, "base_kind": base, "actor": aid, "valid": not problems,
               "version": record.get("version") if isinstance(record.get("version"), str) else None,
               "role": record.get("role") if record.get("role") in ("player", "npc") else None,
               "scenes": sorted(set(scenes)) if scenes_ok else None,
               "base": record.get("base") if isinstance(record.get("base"), str) else None}
        if problems:
            ctx.finding("invalid_ledger_entry", entry.path, detail=f"{ref}: {','.join(problems)}"[:120])
            ctx.unresolve("appearance", f"{campaign.path}/{ref}", ",".join(problems))
            if not scenes_ok:
                campaign.unreadable_scene_membership = True
        campaign.appearances[ref] = row


# ---- hashing as V1 does -------------------------------------------------------------

def actor_dir_hash(actor: Actor) -> str | None:
    """V1 ``dir_hash``: meta then each addressable version, name and text, no separators."""
    if actor.meta_text is None:
        return None
    versions = sorted(v for v in actor.versions if _text.safe_id(v))
    if not versions or any(actor.versions[v].text is None for v in versions):
        return None
    digest = hashlib.sha256()
    meta_name = "character.md" if actor.base == "characters" else "pc.md"
    digest.update(meta_name.encode("utf-8"))
    digest.update(actor.meta_text.encode("utf-8"))
    for vid in versions:
        version = actor.versions[vid]
        digest.update(version.path.rsplit("/", 1)[-1].encode("utf-8"))
        digest.update(version.text.encode("utf-8"))
    return digest.hexdigest()


# ---- appearances: provenance and sync ---------------------------------------------

def appearance_rows(ctx: Context, lib: Library, campaign: Campaign) -> list[dict]:
    rows = []
    for ref, row in sorted(campaign.appearances.items()):
        out = dict(row)
        out["control"] = {"player": "player", "npc": "director"}.get(row["role"])
        if not row["valid"] or not campaign.ledgers_ok:
            out["provenance"] = out["sync"] = "unresolved"
            rows.append(out)
            continue
        base, aid, vid = row["base_kind"], row["actor"], row["version"]
        copy = lib.actors.get(("campaign", campaign.id, base, aid))
        copy_version = copy.versions.get(vid) if copy is not None else None
        if copy_version is None:
            ctx.finding("missing_locked_version", f"{campaign.path}/{base}/{aid}", detail=vid)
        world_actor = (lib.actors.get(("world", campaign.world, base, aid))
                       if campaign.world_status == "resolved" else None)
        world_present = world_actor is not None and world_actor.meta_path is not None
        if campaign.world_status == "case_variant":
            out["provenance"] = out["sync"] = "unresolved"
        elif ref in campaign.detached or not world_present:
            out["provenance"], out["sync"] = "emergent", "not_applicable"
        else:
            copy_hash = copy_version.v1_hash if copy_version is not None else None
            out["provenance"] = "library" if copy_hash is not None and copy_hash == row["base"] else "override"
            world_version = world_actor.versions.get(vid)
            if world_version is None or world_version.v1_hash is None:
                out["sync"] = "world_missing"
            else:
                out["sync"] = "current" if world_version.v1_hash == row["base"] else "world_changed"
        rows.append(out)
    return rows


# ---- inheritance ---------------------------------------------------------------------

def _present(lib: Library, scope: str, sid: str | None, kind: str, rid: str) -> bool:
    if sid is None:
        return False
    if kind in ACTOR_KINDS:
        actor = lib.actors.get((scope, sid, kind, rid))
        return actor is not None and actor.meta_path is not None
    return (scope, sid, kind, rid) in lib.records


def _world_hash(lib: Library, world: str, kind: str, rid: str) -> str | None:
    if kind in ACTOR_KINDS:
        return actor_dir_hash(lib.actors[("world", world, kind, rid)])
    return lib.records[("world", world, kind, rid)].v1_hash


def _copy_hash(lib: Library, cid: str, kind: str, rid: str) -> str | None:
    if kind in ACTOR_KINDS:
        return actor_dir_hash(lib.actors[("campaign", cid, kind, rid)])
    return lib.records[("campaign", cid, kind, rid)].v1_hash


def inheritance_rows(ctx: Context, lib: Library, campaign: Campaign) -> list[dict]:
    """How each inherited-kind record looks from inside the campaign."""
    if not campaign.overlay_ok:
        return []
    ids: set[tuple[str, str]] = set()
    for scope, sid in (("campaign", campaign.id), ("world", campaign.world if campaign.inherits else None)):
        if sid is None:
            continue
        ids |= {(k[2], k[3]) for k in lib.records if k[0] == scope and k[1] == sid}
        ids |= {(k[2], k[3]) for k, a in lib.actors.items()
                if k[0] == scope and k[1] == sid and a.meta_path is not None}
    for ref in campaign.deleted | campaign.detached:
        parts = ref.split("/")
        if len(parts) == 2 and parts[0] in INHERITED_KINDS:
            ids.add((parts[0], parts[1]))
    rows = []
    world = campaign.world if campaign.inherits else None
    for kind, rid in sorted(ids):
        ref = f"{kind}/{rid}"
        in_campaign = _present(lib, "campaign", campaign.id, kind, rid)
        in_world = _present(lib, "world", world, kind, rid)
        row = {"kind": kind, "id": rid, "ref": ref}
        if ref in campaign.deleted and not in_campaign:
            row["state"] = "tombstoned"
        elif ref in campaign.detached:
            row["state"] = "detached" if in_campaign else "detached_absent"
        elif in_campaign and in_world:
            row["state"] = "materialized"
            locked = ref in campaign.appearances
            base = campaign.sync.get(ref, "missing")
            world_hash, copy_hash = _world_hash(lib, world, kind, rid), _copy_hash(lib, campaign.id, kind, rid)
            if locked:
                row["sync"] = "locked_by_appearance"
            elif base is None:
                row["sync"] = "unresolved"
            elif base in ("missing", ""):
                row["sync"] = "no_base"
            elif world_hash is None:
                row["sync"] = "unresolved"
            else:
                row["sync"] = "in_sync" if world_hash == base else "diverged"
            row["local_edit"] = (None if locked or base in (None, "missing", "") or copy_hash is None
                                 else copy_hash != base)
        elif in_campaign and campaign.sync.get(ref):
            # Copied from a world record that is gone: V1 now reads it as the campaign's own.
            row["state"], row["sync"] = "materialized", "world_missing"
        elif in_campaign:
            row["state"] = "campaign_origin"
        elif in_world:
            row["state"] = "inherited"
        else:
            continue
        rows.append(row)
    return rows


# ---- scenes ---------------------------------------------------------------------------

def _players(lib: Library, campaign: Campaign, sid: str) -> tuple[list[str], bool]:
    """(player display names for this scene, whether the cast is fully known)."""
    if campaign.unreadable_scene_membership or not campaign.ledgers_ok:
        return [], False
    names, known = [], True
    for ref, row in sorted(campaign.appearances.items()):
        if row["scenes"] is None or sid not in row["scenes"]:
            continue
        if not row["valid"]:
            known = False
            continue
        if row["role"] != "player":
            continue
        copy = lib.actors.get(("campaign", campaign.id, row["base_kind"], row["actor"]))
        version = copy.versions.get(row["version"]) if copy is not None else None
        if version is None:
            continue  # V1 omits a seat whose locked copy is missing
        if version.malformed:
            known = False
            continue
        names.append(version.display_name)
    return names, known


def scene_rows(ctx: Context, lib: Library, campaigns: dict[str, Campaign]) -> list[dict]:
    rows = []
    identities: dict[tuple[str, str], list[str]] = {}
    for entry in ctx.snap.files("scene"):
        cid, sid = entry.layout.coords["scope_id"], entry.layout.coords["sid"]
        campaign = campaigns.get(cid)
        grammar, number = _scenes.sid_grammar(sid)
        if grammar == "legacy_date":
            ctx.finding("legacy_scene_name", entry.path)
        elif grammar == "invalid":
            ctx.finding("invalid_scene_name", entry.path)
        text = ctx.read_text(entry)
        row = {"campaign": cid, "sid": sid, "path": entry.path, "grammar": grammar, "number": number}
        if text is None:
            row.update(identity=None, title=None, authorship="unresolved", posts=[])
            ctx.unresolve("scene", entry.path, "unreadable")
            rows.append(row)
            continue
        meta, body = _text.parse_frontmatter(text)
        identity = meta.get("identity")
        if not (identity and _text.HEX32.match(identity)):
            ctx.finding("missing_scene_identity", entry.path)
            identity = None
        else:
            identities.setdefault((cid, identity), []).append(entry.path)
        players, known = _players(lib, campaign, sid) if campaign else ([], False)
        posts, preamble = _scenes.parse_posts(body, players)
        if preamble.strip():
            ctx.loss(entry.path, "scene_preamble", f"{len(preamble.strip())} characters before the first post")
        if not known:
            ctx.unresolve("scene_authorship", entry.path, "player cast cannot be determined")
        unknown = _scenes.scene_unknown_fields(meta)
        if unknown:
            ctx.finding("unknown_fields", entry.path, detail=",".join(unknown))
        row.update(identity=identity, title=meta.get("title"),
                   authorship="resolved" if known else "unresolved",
                   players=sorted(players), unknown_fields=unknown,
                   posts=[_scenes.post_row(p) for p in posts])
        if campaign is not None:
            campaign.scene_texts[sid] = [p.content for p in posts]
        rows.append(row)
    for (cid, identity), paths in identities.items():
        if len(paths) > 1:
            ctx.finding("duplicate_scene_identity", *sorted(paths), detail=identity)
    rows.sort(key=lambda r: (r["campaign"], r["sid"]))
    for cid in {r["campaign"] for r in rows}:
        ordered = [r for r in rows if r["campaign"] == cid]
        for order, row in enumerate(ordered):
            row["order"] = order
    return rows


def count_unsupported(ctx: Context) -> None:
    """Count what is cheaply countable in recognized-but-unsupported scene state."""
    for entry in ctx.snap.files("scene_alts"):
        value, ok = ctx.read_json(entry)
        runs = value.get("runs") if ok and isinstance(value, dict) else None
        ctx.loss_details[entry.path] = f"{len(runs)} reroll runs" if isinstance(runs, list) else "unreadable"
    for entry in ctx.snap.files("campaign_state"):
        if entry.path.rsplit("/", 1)[-1] != "responses.json":
            continue
        value, ok = ctx.read_json(entry)
        scenes = value.get("scenes") if ok and isinstance(value, dict) else None
        if not isinstance(scenes, dict):
            ctx.loss_details[entry.path] = "unreadable"
            continue
        responses = [r for s in scenes.values() if isinstance(s, dict)
                     and isinstance(s.get("responses"), dict) for r in s["responses"].values()]
        variants = sum(len(r["variants"]) for r in responses
                       if isinstance(r, dict) and isinstance(r.get("variants"), list))
        ctx.loss_details[entry.path] = f"{variants} response variants"


def campaign_rows(ctx: Context, lib: Library, campaigns: dict[str, Campaign]) -> list[dict]:
    rows = []
    for cid in sorted(campaigns):
        campaign = campaigns[cid]
        rows.append({
            "id": cid, "path": campaign.path, "name": campaign.name, "world": campaign.world,
            "world_status": campaign.world_status, "layout": campaign.layout,
            "v1_listed": _text.safe_id(cid), "meta": campaign.shown_meta,
            "settings_fields": campaign.settings_fields,
            "semantics": "resolved" if campaign.overlay_ok else "unresolved",
            "deleted": sorted(campaign.deleted), "detached": sorted(campaign.detached),
            "appearances": appearance_rows(ctx, lib, campaign),
            "inheritance": inheritance_rows(ctx, lib, campaign),
        })
    return rows
