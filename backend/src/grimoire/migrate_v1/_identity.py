"""Proposed V2 identities for V1 records: candidates for review, never a mapping.

A candidate is an *unpinned* `SourceRef` (the importer mints revisions), keyed
by the exact `LegacyId` of the V1 file it would come from. Nothing is ever
lowercased, slugified or matched by name: a V1 id the V2 grammar refuses gets
no candidate, and two records that would claim one V2 identity both lose it.
Same-named records in different sources are listed only as review groups.
"""

from __future__ import annotations

from ..core.identity import InstanceRef, LegacyId, SourceRef, validate_source_id
from . import _text
from ._campaigns import Campaign
from ._context import Context
from ._records import Library

KIND = {"locations": "location", "lore": "lore", "items": "item", "groups": "group",
        "creatures": "creature", "greetings": "greeting", "characters": "character", "pcs": "character"}


class LegacyIds:
    """`LegacyId` text per source path; a refused path is reported once and stays unmapped."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self._cache: dict[str, str | None] = {}

    def __call__(self, path: str) -> str | None:
        if path not in self._cache:
            try:
                self._cache[path] = str(LegacyId(self.ctx.archive_id, path))
            except ValueError as error:
                self._cache[path] = None
                self.ctx.finding("legacy_id_refused", path, detail=str(error)[:160])
        return self._cache[path]


def _source(wid: str) -> str | None:
    try:
        validate_source_id(f"world:{wid}")
    except ValueError:
        return None
    return f"world:{wid}"


def _candidate(source: str | None, kind: str, entity_id: str) -> SourceRef | None:
    if source is None:
        return None
    try:
        return SourceRef(source, kind, entity_id)
    except ValueError:
        return None


def identity_section(ctx: Context, lib: Library, campaigns: dict[str, Campaign],
                     legacy: LegacyIds) -> dict:
    rows: list[dict] = []
    groups: list[dict] = []
    for wid in sorted(lib.worlds):
        world = lib.worlds[wid]
        source = _source(wid)
        rows.append({"legacy_id": legacy(world["path"]), "path": world["path"], "family": "world",
                     "candidate": {"text": source, "source": source} if source else None,
                     "status": "proposed" if source else "no_id_candidate",
                     "v1": {"world": wid}})
        if source is None:
            ctx.finding("no_id_candidate", world["path"], detail="world id is outside the V2 grammar")
    for key in sorted(lib.records):
        record = lib.records[key]
        rows.append(_definition_row(ctx, lib, campaigns, legacy, record.scope, record.scope_id,
                                    record.kind, record.id, record.path, record.id, None,
                                    readable=record.v1_hash is not None))
    for key in sorted(lib.actors):
        actor = lib.actors[key]
        if actor.meta_path is None:
            continue
        members = []
        for vid in sorted(actor.versions):
            version = actor.versions[vid]
            row = _definition_row(ctx, lib, campaigns, legacy, actor.scope, actor.scope_id, actor.base,
                                  actor.id, version.path, f"{actor.id}.{vid}", vid,
                                  readable=not version.malformed)
            rows.append(row)
            members.append(row)
        if actor.scope == "world":
            groups.append({"legacy_id": legacy(actor.meta_path), "path": actor.meta_path,
                           "default_version": actor.default_version,
                           "members": [m["legacy_id"] for m in members]})
    _withhold_collisions(ctx, rows)
    links = []
    for group in groups:
        proposed = [r for r in rows if r["legacy_id"] in group["members"] and r["status"] == "proposed"]
        for i, a in enumerate(proposed):
            for b in proposed[i + 1:]:
                links.append({"relation": "alternate_portrayal", "subject": a["candidate"]["text"],
                              "object": b["candidate"]["text"]})
    return {
        "notes": ["Candidates are unpinned proposals; the importer mints revisions.",
                  "A world:<id> source is local to this archive; reconciling archives belongs to the importer."],
        "definitions": sorted(rows, key=lambda r: r["path"]),
        "portrayal_groups": sorted(groups, key=lambda g: g["path"]),
        "proposed_links": sorted(links, key=lambda l: (l["subject"], l["object"])),
        "instances": _instances(ctx, lib, campaigns, {
            r["candidate"]["text"] for r in rows if r["status"] == "proposed" and r["candidate"]}),
        "same_name_groups": _same_names(lib),
    }


def _definition_row(ctx, lib, campaigns, legacy, scope, scope_id, base, rid, path, entity_id, vid, *,
                    readable: bool) -> dict:
    v1 = {"world" if scope == "world" else "campaign": scope_id, "base": base, "record": rid}
    if vid is not None:
        v1["version"] = vid
    row = {"legacy_id": legacy(path), "path": path, "family": base, "v1": v1, "candidate": None}
    if scope == "campaign":
        campaign = campaigns.get(scope_id)
        ref = f"{base}/{rid}"
        world_has = (campaign is not None and campaign.inherits and ref not in campaign.detached
                     and ((("world", campaign.world, base, rid) in lib.records)
                          or (lib.actors.get(("world", campaign.world, base, rid)) is not None)))
        row["status"] = "campaign_copy" if world_has else "campaign_origin"
        if not world_has:
            ctx.finding("no_id_candidate", path, detail="campaign_origin")
        return row
    if not readable:
        row["status"] = "unresolved"  # the file could not be parsed; Context listed why
        return row
    if not (_text.safe_id(rid) and (vid is None or _text.safe_id(vid))):
        row["status"] = "no_id_candidate"
        ctx.finding("no_id_candidate", path, detail="not a V1-listed id")
        return row
    candidate = _candidate(_source(scope_id), KIND[base], entity_id)
    if candidate is None:
        row["status"] = "no_id_candidate"
        ctx.finding("no_id_candidate", path, detail="outside the V2 identifier grammar")
        return row
    if row["legacy_id"] is None:
        row["status"] = "no_id_candidate"
        return row
    row["candidate"] = {"text": str(candidate), "source": candidate.source, "kind": candidate.kind,
                        "entity_id": candidate.entity_id}
    row["status"] = "proposed"
    return row


def _withhold_collisions(ctx: Context, rows: list[dict]) -> None:
    """Entity ids are unique within a source across kinds: a shared one is withheld from all."""
    claims: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if row["status"] == "proposed" and "entity_id" in (row["candidate"] or {}):
            claims.setdefault((row["candidate"]["source"], row["candidate"]["entity_id"]), []).append(row)
    for (source, entity_id), claimants in sorted(claims.items()):
        if len(claimants) < 2:
            continue
        ctx.finding("id_candidate_collision", *sorted(r["path"] for r in claimants),
                    detail=f"{source} {entity_id}")
        for row in claimants:
            row["status"], row["withheld"], row["candidate"] = "collision", row["candidate"]["text"], None


def _instances(ctx: Context, lib: Library, campaigns: dict[str, Campaign], proposed: set[str]) -> list[dict]:
    """Campaign instances; `derives_from` names only a definition candidate still proposed."""
    rows = []
    for cid in sorted(campaigns):
        campaign = campaigns[cid]
        seen: dict[str, list[dict]] = {}
        refs = set(campaign.appearances)
        refs |= {f"{k[2]}/{k[3]}" for k, a in lib.actors.items()
                 if k[0] == "campaign" and k[1] == cid and a.meta_path is not None}
        for ref in sorted(refs):
            base, _, aid = ref.partition("/")
            appearance = campaign.appearances.get(ref)
            row = {"campaign": cid, "v1_ref": ref, "candidate": None,
                   "control": None, "derives_from": None, "status": "proposed"}
            if appearance is not None and appearance["valid"]:
                row["control"] = {"player": "player", "npc": "director"}[appearance["role"]]
            elif appearance is not None:
                row["status"] = "unresolved"
            world_actor = (lib.actors.get(("world", campaign.world, base, aid))
                           if campaign.inherits else None)
            if (world_actor is not None and world_actor.meta_path is not None
                    and ref not in campaign.detached and appearance is not None and appearance["valid"]):
                derived = _candidate(_source(campaign.world), "character", f"{aid}.{appearance['version']}")
                if derived is not None and str(derived) in proposed:
                    row["derives_from"] = str(derived)
                else:
                    row["derives_from_unresolved"] = True
                    ctx.unresolve("instance_derivation", f"{campaign.path}/{ref}",
                                  "the portrayal it was locked to has no proposed identity")
            try:
                candidate = InstanceRef(cid, "character", aid)
            except ValueError:
                row["status"] = "no_id_candidate"
                ctx.finding("no_id_candidate", f"{campaign.path}/{ref}", detail="outside the V2 grammar")
            else:
                if row["status"] == "proposed":
                    row["candidate"] = str(candidate)
                seen.setdefault(str(candidate), []).append(row)
            rows.append(row)
        for text, claimants in sorted(seen.items()):
            if len(claimants) > 1:
                ctx.finding("instance_candidate_collision",
                            *sorted(f"{campaign.path}/{r['v1_ref']}" for r in claimants), detail=text)
                for row in claimants:
                    row["status"], row["candidate"] = "collision", None
    return rows


def _same_names(lib: Library) -> list[dict]:
    """Same display name, different sources: for a reviewer to look at, never a link."""
    by_name: dict[tuple[str, str], set[str]] = {}
    for key, record in lib.records.items():
        if key[0] == "world" and record.name:
            by_name.setdefault((KIND[record.kind], _text.name_key(record.name)), set()).add(record.path)
    for key, actor in lib.actors.items():
        if key[0] == "world" and actor.meta_path is not None and actor.name:
            by_name.setdefault(("character", _text.name_key(actor.name)), set()).add(actor.meta_path)
    groups = []
    for (kind, name), paths in sorted(by_name.items()):
        worlds = {p.split("/")[1] for p in paths}
        if len(worlds) > 1:
            groups.append({"kind": kind, "name_key": name, "paths": sorted(paths), "relation": "none"})
    return groups
