"""What a reviewed V0.2.1 manifest asks for, as V2 records: the importer's desired state (026).

Pure mapping: it reads the verified manifest rows and the verified bytes the caller hands it,
and decides nothing about the destination library. Every candidate it maps is the scanner's own
proposal (``identity_candidates``), never a match by display name. Whatever has no V2 form in
this slice is reported, never guessed: ``unresolved`` rows name a reason, ``not_imported`` rows
name a family this slice does not carry yet.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import Agent, FacetSet
from grimoire.core.identity import (
    ChatRef, ImageRef, InstanceRef, LegacyId, PlacementRef, ScopeRef, SourceRef, validate_segment, validate_source_id,
)
from grimoire.images import Expression, Focus, ImageModelError, Placement, slot_role, validate_placement
from grimoire.store.errors import UnstorableIdentifier
from grimoire.store.layout import address_parts, manifest_parts

#: The marker an imported source manifest and play scope carry, so a later run knows them as its own.
MARKER = "v1_import"
#: Families this slice carries as definitions; everything else a world holds is listed, not imported.
ACTOR_FAMILIES = ("characters", "pcs")
RESERVED_SOURCE = "import"  # first segment of the receipt source, ``import:<archive>``
NAME_LIMIT = 200


class _Refused(Exception):
    """A V1 path that has no legacy id (011 reported it as ``legacy_id_refused``): its record is left out."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


class ImportRefused(ValueError):
    """The import cannot be planned at all; ``reason`` is machine-readable. Nothing was written."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def storable(address: str | SourceRef | InstanceRef | ScopeRef | PlacementRef) -> bool:
    """Whether a library on every platform can hold ``address`` (``con`` and ``nul`` are device names on Windows)."""
    try:
        manifest_parts(address) if isinstance(address, str) else address_parts(address)
    except (UnstorableIdentifier, ValueError):
        return False
    return True


@dataclass(frozen=True)
class Selection:
    """The reviewer's decisions: which worlds and campaigns to import, under which V2 ids, and
    which legacy ids to leave out. Anything not selected is not imported (and not reported as lost).

    ``worlds`` maps a V1 world id to the V2 source id it becomes; ``campaigns`` maps a V1 campaign
    id to its V2 campaign id; ``skip`` holds ``LegacyId`` texts declined in review (a world, a
    portrayal, a scene, an image object or a placement), whose dependents are skipped with them.
    """

    worlds: Mapping[str, str]
    campaigns: Mapping[str, str] = field(default_factory=dict)
    skip: frozenset[str] = frozenset()
    unproposed: tuple[tuple[str, str], ...] = ()  # ("world" | "campaign", V1 id) no V2 id could be proposed for

    @classmethod
    def proposed(cls, manifest: Mapping) -> Selection:
        """Every readable world as its proposed ``world:<id>`` source and every campaign under its own id.

        A V1 id that is no valid V2 id (``Old_Coast``) is not proposed but reported (``no_id_candidate``):
        the reviewer maps it to an id of their choosing."""
        worlds, campaigns, left = {}, {}, []
        for row in manifest["worlds"]:
            if row.get("readable"):
                try:
                    validate_source_id(f"world:{row['id']}")
                    if not storable(f"world:{row['id']}"):
                        raise ValueError("no library can store it")
                    worlds[row["id"]] = f"world:{row['id']}"
                except ValueError:
                    left.append(("world", row["id"]))
        for row in manifest["campaigns"]:
            try:
                validate_segment(row["id"], "campaign_id")
                if not storable(ScopeRef(row["id"])):
                    raise ValueError("no library can store it")
                campaigns[row["id"]] = row["id"]
            except ValueError:
                left.append(("campaign", row["id"]))
        return cls(worlds, campaigns, frozenset(), tuple(left))

    def describe(self) -> dict:
        return {"worlds": dict(sorted(self.worlds.items())), "campaigns": dict(sorted(self.campaigns.items())),
                "skip": sorted(self.skip), "unproposed": [list(row) for row in self.unproposed]}


@dataclass
class Definition:
    ref: SourceRef  # pinned
    legacy_id: str
    envelope_parts: dict  # name and payload for the RecordEnvelope
    world: str


@dataclass
class Actor:
    ref: InstanceRef
    definition: SourceRef  # pinned
    extras: dict
    control: str
    role: str


@dataclass
class Scene:
    ref: ChatRef
    legacy_id: str
    label: str | None
    cast: list[InstanceRef]
    extras: dict


@dataclass
class Campaign:
    v1_id: str
    campaign_id: str
    legacy_id: str
    title: str | None
    world: str  # V2 source id
    actors: list[Actor] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)


@dataclass
class Picture:
    ref: ImageRef
    legacy_id: str
    data: bytes
    provenance: dict


@dataclass
class Placing:
    subject: SourceRef | InstanceRef
    slot: str
    placement: Placement
    legacy_id: str
    needs: tuple[object, ...]  # the definitions, actors and pictures it relies on


@dataclass
class Desired:
    """Everything the manifest asks for under one selection, before the destination is consulted."""

    sources: dict[str, dict] = field(default_factory=dict)  # source id -> title, legacy_id, links
    definitions: dict[SourceRef, Definition] = field(default_factory=dict)
    campaigns: list[Campaign] = field(default_factory=list)
    pictures: dict[ImageRef, Picture] = field(default_factory=dict)
    placings: list[Placing] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    not_imported: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def revision_of(v1_sha256: str) -> str:
    """The revision a V1 file becomes: fixed by its exact bytes, so a re-run mints the same one."""
    return f"v1-{v1_sha256[:16]}"


def image_id_of(v1_object_id: str) -> str:
    """A short, deterministic V2 image id for a V1 object id (kept verbatim in provenance)."""
    return f"v1-{_sha(v1_object_id)[:24]}"


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def chat_id_of(campaign_legacy_id: str, scene_identity: str) -> str:
    """A scene's chat id, from its campaign and the identity V1 keeps in it, so a renamed scene keeps its chat."""
    return f"v1-{_sha(f'{campaign_legacy_id}#{scene_identity}')[:16]}"


def _display(value: object, fallback: str) -> str:
    """A name or title as V2 can show it: control characters (pasted escapes, say) become spaces."""
    text = _CONTROL.sub(" ", value) if isinstance(value, str) else ""
    return (text if text.strip() else fallback)[:NAME_LIMIT]


def _persona_body(text: str) -> str:
    """The Markdown body of a V1 persona file (after its front matter), or ``""``."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # a copy for parsing: V1 accepts CRLF files
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---\n", 3)
    return "" if end < 0 else text[end + 5:].strip()


def _card_description(text: str) -> str:
    try:
        data = json.loads(text).get("data")
    except (ValueError, AttributeError):
        return ""
    value = data.get("description") if isinstance(data, Mapping) else None
    return value if isinstance(value, str) else ""


def _focus(value: object) -> Focus | None:
    if type(value) is int and 0 <= value <= 100:
        return Focus(5000, value * 100)
    return None


class _Mapper:
    def __init__(self, manifest: Mapping, selection: Selection, read: Callable[[str], bytes], owner: str) -> None:
        self.m = manifest
        self.archive = manifest["archive_id"]
        self.sel = selection
        self.read = read
        self.owner = owner
        self.out = Desired()
        #: (world, base, record, version) -> pinned definition ref
        self.portrayals: dict[tuple[str, str, str, str], SourceRef] = {}
        #: candidate text (unpinned, scanner's) -> pinned definition ref
        self.candidates: dict[str, SourceRef] = {}
        #: candidate texts of the portrayals the selection declines, and the (campaign, appearance) built on them
        self.declined: set[str] = set()
        self.declined_appearances: set[tuple[str, str]] = set()
        self.metadata_reported: set[str] = set()
        #: V1 path -> legacy id of every text imported verbatim (a portrayal or a campaign's own copy)
        self.imported_texts: dict[str, str] = {}
        self.refused_paths: set[str] = set()
        self.entries = {row["path"]: row for row in manifest["entries"]}

    def legacy(self, path: str) -> str:
        try:
            return str(LegacyId(self.archive, path))
        except ValueError:  # a backslash or a control character: 011 reported it, and the record stays out
            raise _Refused(path) from None

    def legacy_of(self, row: Mapping) -> str:
        """The legacy id 011 gave a row (``None`` when it refused the row's path)."""
        if not isinstance(row.get("legacy_id"), str):
            raise _Refused(row["path"])
        return row["legacy_id"]

    def guarded(self, step: Callable[..., object], *args: object) -> None:
        """Run one record's step; a path with no legacy id leaves that record out, reported, not the run."""
        try:
            step(*args)
        except _Refused as refused:
            if refused.path not in self.refused_paths:
                self.refused_paths.add(refused.path)
                self.out.unresolved.append({"path": refused.path, "reason": "legacy_id_refused"})

    def unresolved(self, legacy_id: str, reason: str, detail: str | None = None) -> None:
        row = {"legacy_id": legacy_id, "reason": reason}
        if detail is not None:
            row["detail"] = detail
        self.out.unresolved.append(row)

    def skipped(self, legacy_id: str) -> bool:
        if legacy_id in self.sel.skip:
            self.out.skipped.append(legacy_id)
            return True
        return False

    # -- selection --------------------------------------------------------------------------
    def check_selection(self) -> None:
        worlds = {row["id"]: row for row in self.m["worlds"]}
        campaigns = {row["id"] for row in self.m["campaigns"]}
        targets: set[str] = set()
        for v1_id, source in self.sel.worlds.items():
            if v1_id not in worlds:
                raise ImportRefused(f"the manifest has no world {v1_id!r}", "unknown_world")
            try:
                validate_source_id(source)
            except ValueError as error:
                raise ImportRefused(str(error), "invalid_source_id") from None
            if not storable(source):
                raise ImportRefused(f"{source} cannot be stored on every platform", "unstorable_id")
            if source.split(":", 1)[0] == RESERVED_SOURCE:
                raise ImportRefused(f"{source}: the {RESERVED_SOURCE}: scheme holds import receipts", "reserved_source")
            if source in targets:
                raise ImportRefused(f"two worlds would both become {source}", "duplicate_target")
            targets.add(source)
        ids: set[str] = set()
        for v1_id, campaign_id in self.sel.campaigns.items():
            if v1_id not in campaigns:
                raise ImportRefused(f"the manifest has no campaign {v1_id!r}", "unknown_campaign")
            try:
                validate_segment(campaign_id, "campaign_id")
            except ValueError as error:
                raise ImportRefused(str(error), "invalid_campaign_id") from None
            if not storable(ScopeRef(campaign_id)):
                raise ImportRefused(f"{campaign_id} cannot be stored on every platform", "unstorable_id")
            if campaign_id in ids:
                raise ImportRefused(f"two campaigns would both become {campaign_id}", "duplicate_target")
            ids.add(campaign_id)
        rows = {(kind, row["id"]): row for kind, family in (("world", "worlds"), ("campaign", "campaigns"))
                for row in self.m[family]}
        for key in self.sel.unproposed:
            row = rows.get(tuple(key))
            if row is not None:
                self.guarded(lambda row=row, v1_id=key[1]: self.unresolved(self.legacy(row["path"]),
                                                                          "no_id_candidate", v1_id))

    # -- worlds and portrayals -------------------------------------------------------------
    def worlds(self) -> None:
        for row in self.m["worlds"]:
            self.guarded(self.world, row)

    def world(self, row: Mapping) -> None:
        source = self.sel.worlds.get(row["id"])
        if source is None:
            return
        legacy = self.legacy(row["path"])
        if self.skipped(legacy):
            return
        if not row.get("readable"):
            self.unresolved(legacy, "unreadable_world")
            return
        self.out.sources[source] = {"title": _display(row.get("name"), row["id"]), "legacy_id": legacy,
                                    "world": row["id"], "links": []}

    def portrayal_rows(self) -> None:
        by_legacy = {row["legacy_id"]: row for row in self.m["identity_candidates"]["definitions"]}
        for actor in self.m["actors"]:
            if actor["scope"] != "world" or actor["base"] not in ACTOR_FAMILIES:
                continue
            source = self.sel.worlds.get(actor["scope_id"])
            if source is None or source not in self.out.sources:
                continue
            for version in actor["versions"]:
                self.guarded(self.version_row, by_legacy, source, actor, version)

    def version_row(self, by_legacy: Mapping, source: str, actor: Mapping, version: Mapping) -> None:
        legacy = self.legacy(version["path"])
        candidate = (by_legacy.get(legacy) or {}).get("candidate")
        if self.skipped(legacy):
            if isinstance(candidate, Mapping) and isinstance(candidate.get("text"), str):
                self.declined.add(candidate["text"])
            return
        if version.get("malformed") or not isinstance(candidate, Mapping):
            self.unresolved(legacy, "malformed_portrayal" if version.get("malformed") else "no_candidate")
            return
        self.portrayal(source, actor, version, legacy, candidate)

    def portrayal(self, source: str, actor: Mapping, version: Mapping, legacy: str, candidate: Mapping) -> None:
        entry = self.entries.get(version["path"]) or {}
        digest = entry.get("sha256")
        try:
            text = self.read(version["path"]).decode("utf-8")
        except UnicodeDecodeError:
            self.unresolved(legacy, "not_utf8")
            return
        except ValueError as error:  # too large to read into memory
            self.unresolved(legacy, "too_large", type(error).__name__)
            return
        # V1 hashes the newline-normalized text (a card saved on Windows has CRLF); the bytes name the revision
        if digest is None or _sha(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")) \
                != version.get("v1_hash"):
            self.unresolved(legacy, "unhashed_portrayal")
            return
        ref = SourceRef(source, candidate["kind"], candidate["entity_id"]).at(revision_of(digest))
        if not storable(ref):  # e.g. a character called con: map it to another id, or skip it
            self.unresolved(legacy, "unstorable_id", str(ref))
            return
        if actor["base"] == "pcs":
            fmt, description = "persona_md", _persona_body(text)
        else:
            fmt, description = version.get("card_spec") or "card_json", _card_description(text)
        name = _display(version.get("display_name"), _display(actor.get("name"), actor["id"]))
        v1 = {"legacy_id": legacy, "sha256": digest, "format": fmt, "label": version.get("label"),
              "record": actor["id"], "version": version["id"], "text": text}
        definition = EntityDefinition(ref, name, "individual", FacetSet([Agent()]), description, {"v1": v1})
        self.out.definitions[ref] = Definition(ref, legacy, {"name": name, "definition": definition},
                                               actor["scope_id"])
        self.portrayals[(actor["scope_id"], actor["base"], actor["id"], version["id"])] = ref
        self.candidates[candidate["text"]] = ref
        self.imported_texts[version["path"]] = legacy

    def links(self) -> None:
        for link in self.m["identity_candidates"].get("proposed_links", ()):
            subject, obj = self.candidates.get(link["subject"]), self.candidates.get(link["object"])
            if subject is None or obj is None:
                continue  # one side was not imported; the other side's report says why
            if link["relation"] != "alternate_portrayal" or subject.source != obj.source:
                self.out.flags.append({"code": "link_not_carried", "subject": str(subject), "object": str(obj)})
                continue
            self.out.sources[subject.source]["links"].append((str(subject), link["relation"], str(obj)))

    def not_imported(self) -> None:
        for row in self.m["identity_candidates"]["definitions"]:
            if row["family"] in (*ACTOR_FAMILIES, "world") or row.get("status") == "campaign_copy":
                continue
            world = (row.get("v1") or {}).get("world")
            if world in self.sel.worlds:
                self.guarded(lambda row=row: self.out.not_imported.append(
                    {"legacy_id": self.legacy_of(row), "family": row["family"]}))

    # -- campaigns -------------------------------------------------------------------------
    def campaigns(self) -> None:
        instances = {(row["campaign"], row["v1_ref"]): row for row in self.m["identity_candidates"]["instances"]}
        scenes: dict[str, list[Mapping]] = {}
        for row in self.m["scenes"]:
            scenes.setdefault(row["campaign"], []).append(row)
        for row in self.m["campaigns"]:
            self.guarded(self.campaign, row, instances, scenes)

    def campaign(self, row: Mapping, instances: Mapping, scenes: Mapping) -> None:
        campaign_id = self.sel.campaigns.get(row["id"])
        if campaign_id is None:
            return
        legacy = self.legacy(f"{row['path']}/campaign.md")
        if self.skipped(legacy):
            return
        source = self.sel.worlds.get(row.get("world") or "")
        if row.get("world_status") != "resolved" or source is None or source not in self.out.sources:
            self.unresolved(legacy, "campaign_world", "its world is not resolved or not imported")
            return
        if row.get("semantics") != "resolved":
            self.unresolved(legacy, "campaign_semantics", str(row.get("semantics")))
            return
        campaign = Campaign(row["id"], campaign_id, legacy, _display(row.get("name"), row["id"]), source)
        for appearance in row["appearances"]:
            self.guarded(self.appearance, campaign, row, appearance, instances.get((row["id"], appearance["ref"])))
        for scene in sorted(scenes.get(row["id"], ()), key=lambda s: s["order"]):
            self.guarded(self.scene, campaign, scene)
        self.out.campaigns.append(campaign)

    def appearance(self, campaign: Campaign, row: Mapping, appearance: Mapping, candidate: Mapping | None) -> None:
        where = f"{row['path']}/appearances.json#{appearance['ref']}"
        legacy = self.legacy(f"{row['path']}/appearances.json")
        if not appearance.get("valid") or candidate is None or candidate.get("status") != "proposed":
            self.unresolved(legacy, "appearance_unmapped", appearance["ref"])
            return
        base = self.candidates.get(candidate.get("derives_from") or "")
        if base is None:
            if candidate.get("derives_from") in self.declined:  # the user's choice: its scenes go on without it
                self.declined_appearances.add((campaign.v1_id, appearance["ref"]))
            self.unresolved(legacy, "appearance_base_not_imported", appearance["ref"])
            return
        try:
            ref = InstanceRef.parse(candidate["candidate"])
            ref = InstanceRef(campaign.campaign_id, ref.kind, ref.instance_id)
        except ValueError as error:
            self.unresolved(legacy, "appearance_unmapped", str(error))
            return
        if not storable(ref):
            self.unresolved(legacy, "unstorable_id", str(ref))
            return
        v1 = {"legacy_id": legacy, "ref": appearance["ref"], "control": appearance.get("control"),
              "role": appearance.get("role"), "provenance": appearance.get("provenance"),
              "sync": appearance.get("sync"), "base_sha256": appearance.get("base")}
        if appearance.get("provenance") == "override" or appearance.get("sync") not in (None, "current"):
            # the campaign plays its own copy (an override, or a lock at a card the world has changed since):
            # the copy is kept verbatim, never replaced by the world's current text
            found = self.campaign_copy(row, appearance)
            if found is None:
                self.unresolved(legacy, "campaign_copy_unreadable", appearance["ref"])
                return
            v1["campaign_copy"], copy_path = found
            self.imported_texts[copy_path] = v1["campaign_copy"]["legacy_id"]
            self.out.flags.append({"code": "campaign_copy_preserved", "instance": str(ref), "where": where})
        if appearance.get("sync") not in (None, "current"):
            self.out.flags.append({"code": f"appearance_{appearance.get('sync')}", "instance": str(ref),
                                   "where": where})
        campaign.actors.append(Actor(ref, base, {"v1": v1}, str(appearance.get("control")),
                                     str(appearance.get("role"))))

    def campaign_copy(self, row: Mapping, appearance: Mapping) -> tuple[dict, str] | None:
        """The campaign's own copy of an overridden portrayal, verbatim (``None`` if it cannot be read)."""
        version = appearance.get("version")
        actor = next((a for a in self.m["actors"] if a["scope"] == "campaign" and a["scope_id"] == row["id"]
                      and f"{a['base']}/{a['id']}" == appearance["ref"]), None)
        path = next((v["path"] for v in (actor or {}).get("versions", ()) if v["id"] == version), None)
        if path is None:
            return None
        try:
            data = self.read(path)
            text = data.decode("utf-8")
        except (ValueError, KeyError):
            return None
        return {"legacy_id": self.legacy(path), "sha256": _sha(data), "text": text}, path

    def scene(self, campaign: Campaign, row: Mapping) -> None:
        legacy = self.legacy(row["path"])
        if self.skipped(legacy):
            return
        if row.get("authorship") != "resolved":
            # 011 could not say who wrote its posts (unreadable, an unknown role, a malformed card)
            self.unresolved(legacy, "scene_unresolved", str(row.get("authorship")))
            return
        if row.get("identity") is None:  # the chat id is the scene's V1 identity, so a rename keeps it
            self.unresolved(legacy, "scene_no_identity")
            return
        if sum(1 for other in self.m["scenes"] if other.get("campaign") == row.get("campaign")
               and other.get("identity") == row["identity"]) > 1:
            self.unresolved(legacy, "scene_identity_duplicate", row["identity"])  # which one is the chat is unknown
            return
        v1 = next(r for r in self.m["campaigns"] if r["id"] == campaign.v1_id)
        mapped = {actor.extras["v1"]["ref"] for actor in campaign.actors}
        here = [a for a in v1["appearances"] if row["sid"] in (a.get("scenes") or ())]
        # with a mapped player the scene has its persona: another player is only ever a second one
        player = any(a["ref"] in mapped and a.get("role") == "player" for a in here)
        missing = sorted(a["ref"] for a in here
                         if a["ref"] not in mapped and (campaign.v1_id, a["ref"]) not in self.declined_appearances
                         and not (player and a.get("role") == "player"))
        if missing:  # a chat's cast is fixed when it starts: it waits until all of it is imported (or declined)
            for ref in missing:
                self.unresolved(legacy, "cast_not_imported", ref)
            return
        entry = self.entries.get(row["path"]) or {}
        cast = [actor.ref for actor in campaign.actors
                if row["sid"] in (self._scenes_of(campaign, actor) or ())]
        posts = [{"post_id": post.get("post_id"), "index": post.get("index"), "role": post.get("role"),
                  "speaker": post.get("speaker"), "synthetic": post.get("synthetic"),
                  "content_sha256": post.get("content_sha256")} for post in row.get("posts", ())]
        extras = {MARKER: {"legacy_id": legacy, "sha256": entry.get("sha256"), "sid": row["sid"],
                           "identity": row.get("identity"), "number": row.get("number"),
                           "title": row.get("title"), "posts": posts}}
        label = _display(row.get("title"), row["sid"])
        chat = ChatRef(campaign.campaign_id, chat_id_of(campaign.legacy_id, row["identity"]))
        campaign.scenes.append(Scene(chat, legacy, label, cast, extras))

    def _scenes_of(self, campaign: Campaign, actor: Actor) -> list[str] | None:
        row = next(r for r in self.m["campaigns"] if r["id"] == campaign.v1_id)
        found = next((a for a in row["appearances"] if a["ref"] == actor.extras["v1"]["ref"]), None)
        return None if found is None else found.get("scenes")

    # -- pictures --------------------------------------------------------------------------
    def pictures(self) -> None:
        images = self.m["images"]
        # 011 lists a sidecar in the wrong shard too, but V1 only ever reads the addressable one
        objects = {row["id"]: row for row in images["objects"] if row.get("addressable", True)}
        blobs = {row["sha256"]: row for row in images["blobs"]}
        world_rows: dict[str, tuple[Placing | None, ImageRef | None]] = {}
        layered = {layer.get("campaign_placement") for layer in images["campaign_layering"]}
        selected = {campaign.v1_id for campaign in self.out.campaigns}
        for row in images["placements"]:
            self.guarded(self.placement_row, row, layered, selected, world_rows, objects, blobs)
        for layer in images.get("expression_layers", ()):
            owner = layer.get("owner") or {}
            if owner.get("scope") == "world" and self.sel.worlds.get(owner.get("scope_id")) in self.out.sources:
                self.guarded(lambda path=layer["path"]: self.out.not_imported.append(
                    {"legacy_id": self.legacy(path), "family": "expression_settings"}))
        for row in images.get("legacy_images", ()) + images.get("collections", ()):
            if isinstance(row, Mapping) and "path" in row:
                self.guarded(lambda path=row["path"]: self.out.not_imported.append(
                    {"legacy_id": self.legacy(path), "family": "legacy_image"}))
        for layer in images["campaign_layering"]:
            self.guarded(self.layering, layer, world_rows, objects, blobs, images["placements"])
        self.references(images.get("references", ()))

    def placement_row(self, row: Mapping, layered: set, selected: set, world_rows: dict, objects: Mapping,
                      blobs: Mapping) -> None:
        owner = row["owner"]
        if owner["scope"] != "world":
            if owner["scope"] == "campaign" and owner.get("scope_id") in selected and row["path"] not in layered:
                # a campaign's own slot over no world art: not carried by this slice, and said so
                self.unresolved(self.legacy_of(row), "campaign_only_placement", row["path"])
            return
        source = self.sel.worlds.get(owner["scope_id"])
        subject_pinned = self.portrayals.get((owner["scope_id"], owner["base"], owner["record"],
                                              owner.get("version")))
        if source is None or source not in self.out.sources:
            return
        world_rows[row["path"]] = (None, None)  # until it is placed: what builds on it waits
        legacy = self.legacy_of(row)
        if self.skipped(legacy):
            return
        if subject_pinned is None:
            self.unresolved(legacy, "placement_subject_not_imported")
            return
        world_rows[row["path"]] = self.world_placement(row, source, subject_pinned, objects, blobs)

    def references(self, rows: object) -> None:
        """Markdown image references inside imported text keep their V1 URLs: reported, never rewritten."""
        counts: dict[str, int] = {}
        for row in rows if isinstance(rows, list) else ():
            legacy = self.imported_texts.get(row.get("source")) if isinstance(row, Mapping) else None
            if legacy is not None:
                counts[legacy] = counts.get(legacy, 0) + 1
        for legacy, count in sorted(counts.items()):
            self.unresolved(legacy, "image_reference_not_translated", f"{count} reference(s)")

    def picture(self, scope: str, object_id: str | None, objects: Mapping, blobs: Mapping,
                legacy: str) -> ImageRef | None:
        """The image object this V1 object becomes in ``scope``, staged once; ``None`` (reported) if it cannot."""
        obj = objects.get(object_id) if object_id is not None else None
        if obj is None:
            self.unresolved(legacy, "dangling_object", object_id)
            return None
        obj_legacy = self.legacy(obj["path"])
        if self.skipped(obj_legacy):
            return None
        ref = ImageRef(scope, image_id_of(obj["id"]))
        if ref in self.out.pictures:
            return ref
        blob = blobs.get(obj.get("blob_sha256"))
        if not (obj.get("valid") and obj.get("blob") == "present" and blob is not None and blob.get("intact")):
            self.unresolved(obj_legacy, "image_bytes_unavailable", str(obj.get("blob")))
            return None
        entry = self.entries.get(blob["path"]) or {}
        if entry.get("sha256") != obj["blob_sha256"]:
            self.unresolved(obj_legacy, "image_bytes_unavailable", "hash_mismatch")
            return None
        try:
            data = self.read(blob["path"])
        except ValueError as error:
            self.unresolved(obj_legacy, "too_large", type(error).__name__)
            return None
        if _sha(data) != obj["blob_sha256"]:  # the reader verified the walk's digest; this is the object's
            self.unresolved(obj_legacy, "image_bytes_unavailable", "hash_mismatch")
            return None
        omitted = [name for name, present in (("description", obj.get("has_description")),
                                               ("sources", obj.get("sources")), ("subjects", obj.get("subjects")),
                                               ("unknown_fields", obj.get("unknown_fields"))) if present]
        if omitted and obj_legacy not in self.metadata_reported:  # the picture imports; its metadata does not
            self.metadata_reported.add(obj_legacy)
            self.unresolved(obj_legacy, "image_metadata_not_carried", ", ".join(omitted))
        provenance = {"legacy_id": obj_legacy, "v1_object": obj["id"], "v1_blob": self.legacy(blob["path"])}
        self.out.pictures[ref] = Picture(ref, obj_legacy, data, provenance)
        return ref

    def world_placement(self, row: Mapping, source: str, pinned: SourceRef, objects: Mapping,
                        blobs: Mapping) -> tuple[Placing | None, ImageRef | None]:
        legacy = self.legacy_of(row)
        if row.get("status") != "resolved":
            self.unresolved(legacy, str(row.get("status")), row.get("image"))
            return None, None
        if row["area"] == "expression":
            if not row.get("live"):
                self.unresolved(legacy, "stale_expression")
                return None, None
            layer = next((layer for layer in self.m["images"].get("expression_layers", ())
                          if layer.get("owner") == row["owner"]), None)
            if layer is None or layer.get("status") != "available":  # 011 left the whole layer unresolved
                self.unresolved(legacy, "expression_layer_unavailable", (layer or {}).get("reason"))
                return None, None
            slot = f"expr.{row.get('outfit')}.{row['name']}"
        elif row["area"] == "record" and row.get("slot") in ("avatar", "gallery"):
            slot = row["name"]
        else:
            self.unresolved(legacy, "slot_unsupported", str(row.get("slot")))
            return None, None
        subject = pinned.unpinned()
        try:
            role = slot_role(slot)
            expected_role = "expression" if row["area"] == "expression" else row["slot"]
            if role != expected_role:
                self.unresolved(legacy, "role_slot_mismatch", slot)
                return None, None
            image = self.picture(source, row.get("image"), objects, blobs, legacy)
            if image is None:
                return None, None
            expression = Expression(row["outfit"], row["name"]) if role == "expression" else None
            placement = Placement("set", role, image, focus=_focus(row.get("focus")), expression=expression,
                                  provenance={"legacy_id": legacy})
        except (ImageModelError, ValueError) as error:
            self.unresolved(legacy, "invalid_placement", str(error))
            return None, None
        problem = validate_placement(subject, slot, placement)
        if problem is not None:
            self.unresolved(legacy, problem[0], problem[1])
            return None, None
        if not storable(PlacementRef(subject, slot)):  # a picture called con, say
            self.unresolved(legacy, "unstorable_id", slot)
            return None, None
        placing = Placing(subject, slot, placement, legacy, (pinned, image))
        self.out.placings.append(placing)
        return placing, image

    def layering(self, layer: Mapping, world_rows: Mapping, objects: Mapping, blobs: Mapping,
                 placements: list) -> None:
        campaign = next((c for c in self.out.campaigns if c.v1_id == layer["campaign"]), None)
        if campaign is None or layer["state"] == "inherited":  # inherited art needs no campaign placement
            return
        world = world_rows.get(layer["world_placement"])
        legacy = self.legacy(layer.get("campaign_placement") or layer["world_placement"])
        if world is None or world[0] is None:
            self.unresolved(legacy, "layering_base_not_imported", layer["state"])
            return
        if self.skipped(legacy):
            return
        placing, image = world
        actors = [a for a in campaign.actors if a.definition.unpinned() == placing.subject]
        if not actors:
            self.unresolved(legacy, "layering_no_actor", layer["state"])
            return
        campaign_row = next((p for p in placements if p["path"] == layer.get("campaign_placement")), None)
        for actor in actors:
            needs: tuple[object, ...] = (actor.ref,)
            if layer["state"] == "hidden":  # hides the world's art only while that art is what was reviewed
                placement = Placement("hide", provenance={"legacy_id": legacy})
                needs += (placing,)
            elif layer["state"] == "focus_override" and campaign_row is not None:
                focus = _focus(campaign_row.get("focus"))
                if focus is None:
                    self.unresolved(legacy, "invalid_placement", "focus")
                    continue
                placement = Placement("adjust", image=image, focus=focus, provenance={"legacy_id": legacy})
                needs += (placing.placement.image, placing)
            elif layer["state"] == "overridden" and campaign_row is not None:
                scope = f"campaign:{campaign.campaign_id}"
                own = self.picture(scope, campaign_row.get("image"), objects, blobs, legacy)
                if own is None:
                    continue
                try:
                    placement = Placement("set", placing.placement.role, own,
                                          focus=_focus(campaign_row.get("focus")),
                                          expression=placing.placement.expression, provenance={"legacy_id": legacy})
                except ImageModelError as error:
                    self.unresolved(legacy, "invalid_placement", str(error))
                    continue
                needs += (own,)
            else:
                self.unresolved(legacy, f"layering_{layer['state']}")
                continue
            if not storable(PlacementRef(actor.ref, placing.slot)):
                self.unresolved(legacy, "unstorable_id", placing.slot)
                continue
            self.out.placings.append(Placing(actor.ref, placing.slot, placement, legacy, needs))


def desired_state(manifest: Mapping, selection: Selection, read: Callable[[str], bytes], owner: str) -> Desired:
    """Map a verified manifest under ``selection`` (``read`` returns verified source bytes)."""
    mapper = _Mapper(manifest, selection, read, owner)
    mapper.check_selection()
    mapper.worlds()
    mapper.portrayal_rows()
    mapper.links()
    mapper.not_imported()
    mapper.campaigns()
    mapper.pictures()
    return mapper.out


__all__ = ["ACTOR_FAMILIES", "MARKER", "Desired", "ImportRefused", "Selection", "chat_id_of", "desired_state",
           "image_id_of", "revision_of", "storable"]
