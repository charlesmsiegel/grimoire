"""Plan a reviewed V0.2.1 import against one V2 library, then apply it as one guarded commit (026).

Every target is decided against the library and the receipts of earlier runs of the same archive:

- ``write``: absent, and no earlier run wrote it;
- ``present``: what an earlier run wrote is still there unchanged, or the exact bytes already are;
- ``kept``: an earlier run wrote it and it has been edited since; the V2 edit wins, nothing is written;
- ``removed``: an earlier run wrote it and it has been deleted since; it is not recreated.

Anything else at a target (a source, campaign, revision, actor, chat, picture or placement this
archive did not write) is ``unresolved``, as is everything that depends on it. A run with nothing to
``write`` writes nothing at all, not even a receipt. A ``present`` target whose V1 content has
changed since it was written is reported (``v1_changed``), never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from grimoire.chats import (
    CAMPAIGN_CONTINUITY, CHAT_SCHEMA, NOT_WITNESSED, SCOPE_SCHEMA, ChatModelError, ChatRecord, Launch, NewBinding,
    PlayScope, WorldBinding, chat_from_payload, chat_to_payload, scope_to_payload,
)
from grimoire.chatstore import campaign_taken_ids
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload, instance_to_payload
from grimoire.core.identity import (
    ChatRef, ImageRef, InstanceRef, LegacyId, PlacementRef, ScopeRef, SourceRef, TranscriptRef, paired_instances,
)
from grimoire.core.index import LegacyIdMap
from grimoire.images import placement_to_payload
from grimoire.imagestore import ImageBatch, ImageLibrary
from grimoire.ledgers import security_campaign_ids
from grimoire.migrate_v1 import open_verified
from grimoire.security import payload_digest
from grimoire.store import (
    InvalidEnvelope, LibraryStore, LinkSpec, RecordEnvelope, ScopeRecord, SourceManifest, StaleWrite,
    StoredDefinition, StoreError, TakenIdsUnlisted, UnstorableIdentifier, etag,
)
from grimoire.store.jsonfmt import MAX_FILE_BYTES, render, thaw
from grimoire.transcripts import (
    TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptError, TranscriptPage, Variant, page_to_payload, paginate,
)
from grimoire.store.layout import address_parts, manifest_parts

from ._map import MARKER, Campaign, Definition, Desired, ImportRefused, Scene, Selection, desired_state

RECEIPT_SCHEMA = "grimoire.v1-import-receipt/0"
#: the ``format`` a V0.2.1 scene's transcript pages name in their origin (036)
V1_SCENE_FORMAT = "grimoire-v0.2.1-scene"
_PAGE_BUDGET = MAX_FILE_BYTES // 2
RECEIPT_KIND, RECEIPT_ENTITY = "receipt", "runs"
WRITE, PRESENT, KEPT, REMOVED = "write", "present", "kept", "removed"
_USABLE = frozenset({WRITE, PRESENT, KEPT})


def receipt_source(archive_id: str) -> str:
    """The source that holds an archive's import receipts, one revision per applied run."""
    return f"import:{archive_id}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path_of(target: object) -> str:
    if isinstance(target, SourceManifest):
        return "/".join(manifest_parts(target.source))
    return "/".join(address_parts(target))  # type: ignore[arg-type]


def _digest(value: object) -> str:
    """A digest of what a target is meant to hold, independent of when it was written."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("ascii")).hexdigest()


def _measure_post(post: Post) -> int:
    probe = TranscriptPage(TranscriptRef("probe", "probe", 1), (post,))
    return len(render({"post": page_to_payload(probe)["posts"][0]}))


def _render(store: LibraryStore, envelope: object) -> bytes:
    transaction = store.transaction()
    key = transaction.put(envelope, None)  # type: ignore[arg-type]
    return transaction.preview()[key]


@dataclass(frozen=True)
class Receipts:
    """What earlier applied runs of one archive wrote, from the newest receipt (cumulative)."""

    run: int
    written: Mapping[str, str]
    manifest_etag: str | None
    head_etag: str | None
    mapping: Mapping = field(default_factory=dict)  # every run's mapping rows, cumulative
    desired: Mapping[str, str] = field(default_factory=dict)  # path -> digest of the content it was written for


def read_receipts(store: LibraryStore, archive_id: str) -> Receipts:
    """The newest receipt of ``archive_id``, or an empty one; ``ImportRefused`` if it cannot be read
    (an import that cannot tell what it wrote before must not guess)."""
    source = receipt_source(archive_id)
    found = store.check_source(source)
    if found == "missing_source":
        return Receipts(0, {}, None, None)
    if isinstance(found, str):
        raise ImportRefused(f"the receipts of {archive_id} cannot be read ({found})", "receipt_unavailable")
    if found.manifest.extra.get(MARKER) != {"archive_id": archive_id, "receipts": True}:
        raise ImportRefused(f"{source} exists but holds no import receipts", "receipt_unavailable")
    head = store.check_definition(SourceRef(source, RECEIPT_KIND, RECEIPT_ENTITY))
    if head == "missing_entity":
        return Receipts(0, {}, found.etag, None)
    if not isinstance(head, StoredDefinition):
        raise ImportRefused(f"the receipts of {archive_id} cannot be read ({head})", "receipt_unavailable")
    payload = head.envelope.payload
    fields = payload if isinstance(payload, Mapping) else {}
    written, run, mapping, desired = (fields.get("written"), fields.get("run"), fields.get("mapping"),
                                      fields.get("desired", {}))
    if (head.envelope.extra.get("schema") != RECEIPT_SCHEMA or type(run) is not int
            or not _text_map(written) or not _text_map(desired) or not isinstance(mapping, Mapping)
            or not all(type(k) is str and isinstance(v, (list, tuple)) and all(isinstance(row, Mapping) for row in v)
                       for k, v in mapping.items())):
        raise ImportRefused(f"the newest receipt of {archive_id} is not a {RECEIPT_SCHEMA} receipt",
                            "receipt_unavailable")
    return Receipts(run, dict(written), found.etag, head.etag, thaw(mapping), dict(desired))  # type: ignore[arg-type]


def _text_map(value: object) -> bool:
    return isinstance(value, Mapping) and all(type(k) is str and type(v) is str for k, v in value.items())


def legacy_map(receipt_payload: Mapping) -> LegacyIdMap:
    """The receipt's portrayal mapping as 002's reviewed ``LegacyIdMap`` (V1 file -> pinned revision)."""
    return LegacyIdMap((LegacyId.parse(row["legacy_id"]), SourceRef.parse(row["ref"]))
                       for row in receipt_payload["mapping"]["definitions"])


@dataclass
class _Target:
    path: str
    kind: str
    legacy_id: str | None
    outcome: str
    etag: str | None = None  # what a dependent pins: the current file's etag or the one being written
    envelope: object = None


@dataclass
class ImportResult:
    etags: Mapping[str, str]
    receipt: SourceRef | None
    review: Mapping


@dataclass
class ImportPlan:
    """A planned import: ``review()`` what it maps and refuses, ``preview()`` the exact bytes, ``apply()``.

    ``apply`` commits everything ``write`` as **one** store transaction through 018's image batch,
    with the receipt, under the given handle: an untrusted handle needs a covering grant and an
    approval of each previewed target's exact bytes, keyed by its path."""

    store: LibraryStore
    handle: object
    owner: str
    at: str
    archive_id: str
    fingerprint: str
    selection: Selection
    desired: Desired
    receipts: Receipts
    targets: dict[str, _Target] = field(default_factory=dict)
    unresolved: list[dict] = field(default_factory=list)
    new_campaigns: set[str] = field(default_factory=set)
    new_actors: dict[str, set[str]] = field(default_factory=dict)
    images: list[tuple] = field(default_factory=list)
    places: list[tuple] = field(default_factory=list)
    puts: list[object] = field(default_factory=list)
    receipt: SourceRef | None = None
    digests: dict[str, str] = field(default_factory=dict)  # this run's writes: path -> desired digest
    relied_scopes: dict[str, str] = field(default_factory=dict)  # existing campaign -> scope its writes need
    bound: dict[str, set[InstanceRef]] = field(default_factory=dict)  # campaign -> actors this run's new chats bind
    _paired: dict[str, frozenset[InstanceRef]] = field(default_factory=dict)
    _probe: object = None
    _review: dict = field(default_factory=dict)
    _preview: dict = field(default_factory=dict)
    _placed: set = field(default_factory=set)
    _batch: object = None
    _committed: bool = False

    # -- deciding ---------------------------------------------------------------------------
    def _decide(self, path: str, kind: str, legacy_id: str | None, rendered: bytes | None,
                envelope: object = None, desired: str | None = None) -> _Target:
        prior = self.receipts.written.get(path)
        try:
            current = self.store.read_exact(path)
        except (StoreError, ValueError, OSError) as error:  # a look-alike, a link, an unreadable file
            target = _Target(path, kind, legacy_id, "conflict", None, envelope)
            self.targets[path] = target
            self._unresolve(legacy_id, f"{kind}_unreadable", f"{path}: {type(error).__name__}")
            return target
        if prior is not None:
            outcome = REMOVED if current is None else PRESENT if etag(current) == prior else KEPT
        elif current is None:
            outcome = WRITE
        else:
            outcome = PRESENT if rendered is not None and current == rendered else "conflict"
        found = etag(current) if current is not None else etag(rendered) if outcome == WRITE and rendered else None
        target = _Target(path, kind, legacy_id, outcome, found, envelope)
        self.targets[path] = target
        if outcome == "conflict":
            self._unresolve(legacy_id, f"{kind}_exists", path)
        if desired is not None:
            if outcome == WRITE:
                self.digests[path] = desired
            elif prior is not None and self.receipts.desired.get(path, desired) != desired:
                self._unresolve(legacy_id, "v1_changed", path)  # what V1 holds now is not what was imported
        return target

    def _rendered(self, path: str, kind: str, legacy_id: str | None, envelope: object) -> bytes | None:
        """The bytes ``envelope`` would be stored as, or ``None`` (unresolved) if no library could hold them."""
        try:
            return _render(self.store, envelope)
        except InvalidEnvelope as error:  # e.g. past the store's document size limit
            self.targets[path] = _Target(path, kind, legacy_id, "conflict")
            self._unresolve(legacy_id, "too_large", f"{path}: {error}")
            return None

    def _unresolve(self, legacy_id: str | None, reason: str, detail: str | None = None) -> None:
        row: dict = {"legacy_id": legacy_id, "reason": reason}
        if detail is not None:
            row["detail"] = detail
        self.unresolved.append(row)

    def _usable(self, path: str) -> bool:
        target = self.targets.get(path)
        return target is not None and target.outcome in _USABLE

    def _put(self, target: _Target) -> None:
        if target.outcome == WRITE:
            self.puts.append(target.envelope)

    # -- sources and definitions ------------------------------------------------------------
    def _prior(self, key: str, field_name: str) -> dict[str, str]:
        """legacy id -> what an earlier run of this archive imported it as (from the cumulative receipt)."""
        return {row["legacy_id"]: row[field_name] for row in self.receipts.mapping.get(key, ())
                if isinstance(row.get("legacy_id"), str) and isinstance(row.get(field_name), str)}

    def plan_sources(self) -> None:
        prior = self._prior("sources", "source")
        for source, info in sorted(self.desired.sources.items()):
            marker = {"archive_id": self.archive_id, "legacy_id": info["legacy_id"]}
            path = "/".join(manifest_parts(source))
            earlier = prior.get(info["legacy_id"])
            if earlier is not None and earlier != source:  # one V1 world is imported once, under one id
                self.targets[path] = _Target(path, "source", info["legacy_id"], "conflict")
                self._unresolve(info["legacy_id"], "already_imported_as", earlier)
                continue
            links = tuple(LinkSpec(*link) for link in info["links"])
            manifest = SourceManifest(source, self.owner, info["title"], {}, links, extra={MARKER: marker})
            path = _path_of(manifest)
            if path not in self.receipts.written:
                found = self.store.check_source(source)
                if not isinstance(found, str) or found != "missing_source":
                    if isinstance(found, str) or found.manifest.extra.get(MARKER) != marker:
                        self.targets[path] = _Target(path, "source", info["legacy_id"], "conflict")
                        self._unresolve(info["legacy_id"], "source_exists", source)
                        continue
                    raise ImportRefused(f"{source} was imported from {self.archive_id}, but its receipt is "
                                        "gone: the import cannot tell what was deleted since", "receipt_missing")
            rendered = _render(self.store, manifest)
            target = self._decide(path, "source", info["legacy_id"], rendered, manifest, etag(rendered))
            if target.outcome in (PRESENT, KEPT):
                stored = self.store.check_source(source)
                if isinstance(stored, str) or stored.manifest.extra.get(MARKER) != marker:
                    target.outcome = "conflict"
                    self._unresolve(info["legacy_id"], "source_unavailable", str(stored) if isinstance(stored, str)
                                    else source)
                    continue
                self._links_kept(stored.manifest, links, info["legacy_id"])
            self._put(target)

    def _links_kept(self, manifest: SourceManifest, links: Iterable[LinkSpec], legacy_id: str) -> None:
        have = {(link.subject, link.relation, link.object) for link in manifest.links}
        for link in links:
            if (link.subject, link.relation, link.object) not in have:
                self._unresolve(legacy_id, "link_not_added", f"{link.subject} {link.relation} {link.object}")

    def _source_usable(self, source: str) -> bool:
        return self._usable("/".join(manifest_parts(source)))

    def plan_definitions(self) -> None:
        for ref, item in sorted(self.desired.definitions.items(), key=lambda pair: str(pair[0])):
            if not self._source_usable(ref.source):
                self._unresolve(item.legacy_id, "source_not_imported", ref.source)
                continue
            envelope = self._definition_envelope(item)
            path = _path_of(ref)
            entity = path.rsplit("/", 1)[0] + "/"
            earlier = sorted(p for p in self.receipts.written if p.startswith(entity) and p != path)
            if path not in self.receipts.written and earlier and not self._any_present(earlier, item.legacy_id):
                for old in earlier:  # deleted in V2 since: a new V1 revision never brings the entity back
                    self.targets.setdefault(old, _Target(old, "definition", item.legacy_id, REMOVED))
                self._unresolve(item.legacy_id, "entity_removed", earlier[-1])
                continue
            if path not in self.receipts.written:
                existing = self.store.check_definition(ref.unpinned())
                if isinstance(existing, StoredDefinition) and existing.ref != ref:
                    # another revision would leave the entity with two and no head: load() drops it
                    self.targets[path] = _Target(path, "definition", item.legacy_id, "conflict")
                    self._unresolve(item.legacy_id, "entity_has_revision", str(existing.ref))
                    continue
                if isinstance(existing, str) and existing not in ("missing_source", "missing_entity",
                                                                  "missing_revision"):
                    self.targets[path] = _Target(path, "definition", item.legacy_id, "conflict")
                    self._unresolve(item.legacy_id, "entity_unavailable", existing)
                    continue
            rendered = self._rendered(path, "definition", item.legacy_id, envelope)
            if rendered is None:
                continue
            self._put(self._decide(path, "definition", item.legacy_id, rendered, envelope, etag(rendered)))

    def _any_present(self, paths: Iterable[str], legacy_id: str) -> bool:
        """Whether any of ``paths`` still holds a record (an unreadable one counts as present)."""
        for path in paths:
            try:
                if self.store.read_exact(path) is not None:
                    return True
            except (StoreError, ValueError, OSError):
                return True
        return False

    def _definition_envelope(self, item: Definition) -> RecordEnvelope:
        definition = item.envelope_parts["definition"]
        return RecordEnvelope(item.ref, definition_to_payload(definition), name=item.envelope_parts["name"],
                              owner=self.owner, extra={"schema": ENTITY_SCHEMA})

    # -- campaigns ---------------------------------------------------------------------------
    def plan_campaigns(self) -> None:
        for campaign in self.desired.campaigns:
            self._plan_campaign(campaign)

    def _scope_readable(self, path: str, legacy: str) -> bool:
        try:
            self.store.read_exact(path)
        except (StoreError, ValueError, OSError) as error:  # a link, a look-alike, an unreadable file
            self.targets[path] = _Target(path, "campaign", legacy, "conflict")
            self._unresolve(legacy, "campaign_unreadable", f"{path}: {type(error).__name__}")
            return False
        return True

    def _plan_campaign(self, campaign: Campaign) -> None:
        cid, legacy = campaign.campaign_id, campaign.legacy_id
        earlier = self._prior("campaigns", "campaign_id").get(legacy)
        if earlier is not None and earlier != cid:  # one V1 campaign is imported once, under one id
            self._unresolve(legacy, "already_imported_as", earlier)
            return
        if not self._source_usable(campaign.world):
            self._unresolve(legacy, "campaign_world_not_imported", campaign.world)
            return
        scope_path = _path_of(ScopeRef(cid))
        marker = {"archive_id": self.archive_id, "legacy_id": legacy}
        presence = self.store.campaign_presence(cid)
        ours = scope_path in self.receipts.written
        if presence == "absent":
            if ours:  # deleted since an earlier run: never recreated under the same id
                self.targets[scope_path] = _Target(scope_path, "campaign", legacy, REMOVED)
                self._unresolve(legacy, "campaign_scope_removed", cid)
                return
            try:
                taken_by_ledger = cid in security_campaign_ids(self.store)
            except TakenIdsUnlisted as error:
                self._unresolve(legacy, "taken_ids_unlisted", str(error))
                return
            if self.store.campaign_authority_presence(cid) != "absent" or taken_by_ledger:
                self._unresolve(legacy, "campaign_id_taken", cid)
                return
        elif presence != "present":
            self._unresolve(legacy, "campaign_id_taken", cid)
            return
        elif ours and not self._scope_readable(scope_path, legacy):
            return
        elif ours and self.store.read_exact(scope_path) is not None and not self._ours(cid, marker):
            self._unresolve(legacy, "campaign_exists", cid)  # this archive's, but imported from another V1 campaign
            return
        elif not ours:
            if self._ours(cid, marker):
                raise ImportRefused(f"campaign {cid} was imported from {self.archive_id}, but its receipt is gone: "
                                    "the import cannot tell what was deleted since", "receipt_missing")
            self._unresolve(legacy, "campaign_exists", cid)
            return
        world_etag = self.targets["/".join(manifest_parts(campaign.world))].etag
        scope = PlayScope(ScopeRef(cid), self.at, campaign.title, WorldBinding(campaign.world, world_etag, self.at),
                          extras={MARKER: marker})
        record = ScopeRecord(ScopeRef(cid), SCOPE_SCHEMA, scope_to_payload(scope), owner=self.owner)
        desired = _digest({"title": campaign.title, "world": campaign.world, "marker": marker})
        target = self._decide(scope_path, "campaign", legacy, None, record, desired)
        if target.outcome not in _USABLE:  # its scope was deleted in V2: nothing is added to the campaign
            self._unresolve(legacy, "campaign_scope_removed", cid)
            return
        self._put(target)
        if target.outcome != WRITE:
            self.relied_scopes[cid] = scope_path
        if presence == "absent":
            self.new_campaigns.add(cid)
            taken: frozenset[str] = frozenset()  # nothing names a campaign no folder or ledger knows
        else:
            try:
                taken = campaign_taken_ids(self.store, cid)
            except TakenIdsUnlisted as error:
                self._unresolve(legacy, "taken_ids_unlisted", str(error))
                return
        for actor in campaign.actors:
            self._plan_actor(campaign, actor, taken)
        self._plan_chats(campaign)

    def _ours(self, cid: str, marker: Mapping) -> bool:
        """Whether campaign ``cid``'s play scope carries this archive's marker for this V1 campaign."""
        try:
            stored = self.store.read(ScopeRef(cid))
        except (StoreError, ValueError, OSError):
            return False
        payload = stored.record.payload
        return isinstance(payload, Mapping) and payload.get(MARKER) == marker

    def _plan_actor(self, campaign: Campaign, actor, taken: frozenset[str]) -> None:  # type: ignore[no-untyped-def]
        legacy = actor.extras["v1"]["legacy_id"]
        base_path = _path_of(actor.definition)
        if not self._usable(base_path):
            self._unresolve(legacy, "actor_base_not_imported", str(actor.ref))
            return
        base = self.targets[base_path]
        source = actor.definition.unpinned()
        instance = EntityInstance(actor.ref, source=source, extras=actor.extras)
        envelope = RecordEnvelope(actor.ref, instance_to_payload(instance), owner=self.owner, derived_from=str(source),
                                  base=str(actor.definition), base_etag=base.etag, extra={"schema": ENTITY_SCHEMA})
        path = _path_of(actor.ref)
        if path not in self.receipts.written and actor.ref.instance_id in taken:
            try:
                absent = self.store.read_exact(path) is None
            except (StoreError, ValueError, OSError) as error:  # a link or look-alike at the actor's path
                self.targets[path] = _Target(path, "actor", legacy, "conflict")
                self._unresolve(legacy, "actor_unreadable", f"{path}: {type(error).__name__}")
                return
            if absent:
                self._unresolve(legacy, "actor_id_taken", str(actor.ref))
                return
        rendered = self._rendered(path, "actor", legacy, envelope)
        if rendered is None:
            return
        target = self._decide(path, "actor", legacy, rendered, envelope, etag(rendered))
        if target.outcome == WRITE:
            self.new_actors.setdefault(campaign.campaign_id, set()).add(actor.ref.instance_id)
        self._put(target)

    def _plan_chats(self, campaign: Campaign) -> None:
        seen: set[InstanceRef] = set()
        actors = {actor.ref: actor for actor in campaign.actors}
        for scene in campaign.scenes:
            bindings, persona, dropped = [], None, []
            # with a usable player the chat has its persona: another player is only ever a second one
            ok = {ref: self._bindable(actors[ref], scene.legacy_id) for ref in scene.cast}
            player = any(actors[ref].role == "player" and ok[ref] for ref in scene.cast)
            for ref in scene.cast:
                actor = actors[ref]
                if not ok[ref]:
                    if actor.role == "player" and player:
                        self._unresolve(scene.legacy_id, "second_persona", str(ref))
                    else:
                        dropped.append(str(ref))
                    continue
                prior = CAMPAIGN_CONTINUITY if ref in seen else NOT_WITNESSED
                binding = NewBinding(actor.definition.unpinned(), "character", actor.definition.revision, ref, prior)
                if actor.role == "player":
                    if persona is not None:  # V2 chats have one persona: the chat starts with the first
                        self._unresolve(scene.legacy_id, "second_persona", str(ref))
                        continue
                    persona = replace(binding, role="persona")
                    continue
                bindings.append(binding)
            for ref in dropped:
                self._unresolve(scene.legacy_id, "cast_not_imported", ref)
            if dropped and _path_of(scene.ref) not in self.receipts.written:
                continue  # a chat's cast is fixed when it starts: it waits until all of it is imported
            if persona is not None:
                bindings.append(persona)
            seen.update(binding.actor for binding in bindings)
            npc = next((b for b in bindings if b.role == "character"), None)
            launch = (Launch("character", npc.ref) if npc is not None
                      else Launch("premise", premise=scene.label or scene.extras[MARKER]["sid"]))
            try:
                record = replace(ChatRecord.start(scene.ref, launch, self.at, tuple(bindings), scene.label),
                                 extras=scene.extras)
            except ChatModelError as error:
                self._unresolve(scene.legacy_id, "scene_unmapped", getattr(error, "reason", str(error)))
                continue
            envelope = ScopeRecord(scene.ref, CHAT_SCHEMA, chat_to_payload(record), owner=self.owner)
            if self._rendered(_path_of(scene.ref), "chat", scene.legacy_id, envelope) is None:
                continue
            # what the V1 scene holds, never what V2 could bind today, so a V2 change is not a V1 change
            desired = _digest({"label": scene.label, "extras": scene.extras})
            target = self._decide(_path_of(scene.ref), "chat", scene.legacy_id, None, envelope, desired)
            if target.outcome in (PRESENT, KEPT):
                self._cast_bound(scene.ref, scene.legacy_id, [b.actor for b in bindings])
            elif target.outcome == WRITE:
                self.bound.setdefault(campaign.campaign_id, set()).update(b.actor for b in bindings)
            self._put(target)
            path = _path_of(scene.ref)
            # pages only for the scene as the chat was imported: a changed scene is v1_changed, and its
            # rows (and texts) are no longer what the chat records
            imported_as = path not in self.receipts.written or self.receipts.desired.get(path, desired) == desired
            if target.outcome in _USABLE and scene.texts is not None and imported_as:
                self._plan_transcript(scene)

    def _plan_transcript(self, scene: Scene) -> None:
        """The scene's posts as transcript pages (036): exact V1 text, label-only speakers, no audience.

        A page is create-only: a V1 scene that gained posts since it was imported is ``v1_changed`` and its
        pages stay as written (none are added). A chat imported before 036 gets its pages on the next run,
        if its scene is still as imported. Pages are decided per chat, all or none: if any page is neither
        absent nor exactly as this run would write it, none is written (``transcript_exists``)."""
        marker = scene.extras[MARKER]
        posts = []
        try:
            for number, (row, text) in enumerate(zip(marker["posts"], scene.texts or ()), start=1):
                role = "system" if row["synthetic"] else "user" if row["role"] == "user" else "assistant"
                source = {"v1_index": row["index"], "v1_post_id": row["post_id"],
                          "content_sha256": row["content_sha256"]}
                posts.append(Post(f"p{number:06d}", Speaker(role, None, row["speaker"]),
                                  (Variant(text, None, {"field": "content"}),), 0, source=source))
        except TranscriptError as error:  # a post no page can record exactly (a speaker label too long, say)
            self._unresolve(scene.legacy_id, "transcript_unrepresentable", f"{scene.ref}: {error}")
            return
        if not posts:
            return
        origin = {"format": V1_SCENE_FORMAT, "legacy_id": scene.legacy_id, "sha256": marker["sha256"]}
        try:
            groups = paginate(posts, _measure_post, _PAGE_BUDGET)
        except (TranscriptError, InvalidEnvelope) as error:  # a post past what any page could hold
            self._unresolve(scene.legacy_id, "too_large", f"{scene.ref}: {error}")
            return
        planned = []
        for seq, group in enumerate(groups, start=1):
            ref = TranscriptRef(scene.ref.campaign_id, scene.ref.chat_id, seq)
            envelope = ScopeRecord(ref, TRANSCRIPT_SCHEMA, page_to_payload(TranscriptPage(ref, group, origin)),
                                   owner=self.owner)
            path = _path_of(ref)
            rendered = self._rendered(path, "transcript", scene.legacy_id, envelope)
            if rendered is None:
                return
            desired = _digest({"origin": origin, "seq": seq, "posts": [p.source for p in group]})
            if not self._page_fits(path, rendered, desired):
                self._unresolve(scene.legacy_id, "transcript_exists", path)
                return
            planned.append((path, rendered, envelope, desired))
        for path, rendered, envelope, desired in planned:
            self._put(self._decide(path, "transcript", scene.legacy_id, rendered, envelope, desired))

    def _page_fits(self, path: str, rendered: bytes, desired: str) -> bool:
        """Whether a planned page is absent (and never written) or already exactly this run's page."""
        prior = self.receipts.written.get(path)
        try:
            current = self.store.read_exact(path)
        except (StoreError, ValueError, OSError):
            return False
        if prior is not None:
            return current is not None and etag(current) == prior and self.receipts.desired.get(path) == desired
        return current is None or current == rendered

    def _bindable(self, actor, legacy_id: str) -> bool:  # type: ignore[no-untyped-def]
        """Whether a chat can bind ``actor`` to its V1 definition (a V2 edit may have re-sourced it)."""
        path = _path_of(actor.ref)
        if not self._usable(path):
            return False
        campaign = actor.ref.campaign_id
        if campaign not in self._paired:
            self._paired[campaign] = frozenset(paired_instances(self.store.campaign_instances(campaign)))
        if actor.ref in self._paired[campaign]:  # load() leaves both members of the pair out: no one to bind
            self._unresolve(legacy_id, "instance_id_conflict", str(actor.ref))
            return False
        if self.targets[path].outcome != KEPT:
            return True
        try:
            stored = self.store.read(actor.ref)
            source = SourceRef.parse(stored.envelope.derived_from)  # type: ignore[union-attr, arg-type]
        except (StoreError, ValueError, TypeError):
            source = None
        if source is not None and source.entity_key == actor.definition.entity_key:
            return True
        self._unresolve(legacy_id, "actor_changed_in_v2", str(actor.ref))
        return False

    def _cast_bound(self, ref: ChatRef, legacy_id: str, cast: list[InstanceRef]) -> None:
        """An imported chat keeps the cast it started with: an actor imported since is reported, never bound."""
        try:
            stored = self.store.read(ref)
            bound = {b.actor for b in chat_from_payload(ref, stored.record.schema, stored.record.payload).bindings}
        except (StoreError, ValueError, ChatModelError):
            return  # a V2 edit that no longer parses is the chat's own business
        for actor in cast:
            if actor not in bound:
                self._unresolve(legacy_id, "cast_not_bound", str(actor))

    # -- pictures ---------------------------------------------------------------------------
    def _need_ok(self, need: object) -> bool:
        if isinstance(need, (SourceRef, InstanceRef, ImageRef)):
            return self._usable(_path_of(need))
        return id(need) in self._placed  # an earlier placement this one adjusts

    def plan_pictures(self, library: ImageLibrary) -> None:
        for ref, picture in self.desired.pictures.items():
            scope_ok = (self._source_usable(ref.scope) if ref.campaign_id is None
                        else self._usable(_path_of(ScopeRef(ref.campaign_id))))
            if not scope_ok:
                self._unresolve(picture.legacy_id, "image_scope_not_imported", ref.scope)
                continue
            path = _path_of(ref)
            desired = _digest({"sha256": hashlib.sha256(picture.data).hexdigest(), "provenance": picture.provenance})
            target = self._decide(path, "image", picture.legacy_id, None, None, desired)
            if target.outcome == "conflict":  # an image object we did not write: the same picture is fine
                try:
                    image, found = library.read_image(ref)
                except (StoreError, ValueError, OSError):
                    continue
                if image.blob.sha256 == hashlib.sha256(picture.data).hexdigest():
                    target.outcome, target.etag = PRESENT, found
                    self.unresolved.pop()
                continue
            if target.outcome == WRITE:
                self.images.append((ref.scope, ref.image_id, picture.data, picture.provenance, target))
        # each image and placement is validated as it would be committed, so none can fail later
        staged, self.images = self.images, []
        probe = self._batch_of()
        for scope, image_id, data, provenance, target in staged:
            try:
                probe.add_image(scope, image_id, data, provenance)
            except (ValueError, StoreError) as error:  # e.g. a damaged blob already in the library
                target.outcome = "conflict"
                self.digests.pop(target.path, None)
                self._unresolve(target.legacy_id, "image_unavailable", f"{target.path}: {error}")
                continue
            self.images.append((scope, image_id, data, provenance))
        for placing in self.desired.placings:
            if not all(self._need_ok(need) for need in placing.needs):
                self._unresolve(placing.legacy_id, "placement_needs_not_imported", f"{placing.subject} {placing.slot}")
                continue
            desired = _digest({"subject": str(placing.subject), "slot": placing.slot,
                               "placement": placement_to_payload(placing.placement)})
            target = self._decide(_path_of(PlacementRef(placing.subject, placing.slot)), "placement",
                                  placing.legacy_id, None, None, desired)
            if target.outcome == "conflict":
                target.outcome = KEPT  # someone else's art choice in this slot wins; nothing is written
                self.unresolved.pop()
            if target.outcome == WRITE:
                try:
                    probe.place(placing.subject, placing.slot, placing.placement, None)
                except (ValueError, StoreError) as error:  # e.g. an adjust whose inherited art changed meanwhile
                    target.outcome = "conflict"
                    self.digests.pop(target.path, None)
                    self._unresolve(placing.legacy_id, "invalid_placement", f"{target.path}: {error}")
                    continue
                self.places.append((placing.subject, placing.slot, placing.placement))
            if target.outcome in (WRITE, PRESENT):  # a dependent builds only on art that is as imported
                self._placed.add(id(placing))
        self._probe = probe

    # -- the batch, the receipt and the review -------------------------------------------------
    def _batch_of(self, receipt: Iterable[tuple[object, str | None]] = ()) -> ImageBatch:
        batch = ImageLibrary(self.store, self.handle, owner=self.owner).change()
        for envelope in self.puts:
            batch.put(envelope, None)  # type: ignore[arg-type]
        for scope, image_id, data, provenance in self.images:
            batch.add_image(scope, image_id, data, provenance)
        for subject, slot, placement in self.places:
            batch.place(subject, slot, placement, None)
        for envelope, expected in receipt:
            batch.put(envelope, expected)  # type: ignore[arg-type]
        return batch

    def _mapping(self) -> dict:
        usable = self._usable
        desired = self.desired
        definitions = [{"legacy_id": d.legacy_id, "ref": str(ref)} for ref, d in desired.definitions.items()
                       if usable(_path_of(ref))]
        sources = [{"legacy_id": info["legacy_id"], "source": source} for source, info in desired.sources.items()
                   if self._source_usable(source)]
        campaigns, actors, scenes = [], [], []
        for campaign in desired.campaigns:
            if not usable(_path_of(ScopeRef(campaign.campaign_id))):
                continue
            campaigns.append({"legacy_id": campaign.legacy_id, "campaign_id": campaign.campaign_id,
                              "world": campaign.world})
            for actor in campaign.actors:
                if usable(_path_of(actor.ref)):
                    v1 = actor.extras["v1"]
                    actors.append({"legacy_id": v1["legacy_id"], "v1_ref": v1["ref"], "ref": str(actor.ref),
                                   "derives_from": str(actor.definition), "control": actor.control,
                                   "role": actor.role})
            for scene in campaign.scenes:
                if usable(_path_of(scene.ref)):
                    scenes.append({"legacy_id": scene.legacy_id, "chat": str(scene.ref),
                                   "sha256": scene.extras[MARKER]["sha256"], "cast": [str(c) for c in scene.cast]})
        images = [{"legacy_id": p.legacy_id, "ref": str(ref)} for ref, p in desired.pictures.items()
                  if usable(_path_of(ref))]
        placements = [{"legacy_id": p.legacy_id, "ref": str(PlacementRef(p.subject, p.slot)),
                       "mode": p.placement.mode} for p in desired.placings
                      if usable(_path_of(PlacementRef(p.subject, p.slot))) and id(p) in self._placed]
        return {"sources": sources, "definitions": definitions, "campaigns": campaigns, "actors": actors, "scenes": scenes,
                "images": images, "placements": placements}

    def finish(self, manifest: Mapping) -> None:
        writes = sorted(path for path, target in self.targets.items() if target.outcome == WRITE)
        outcomes = {path: target.outcome for path, target in sorted(self.targets.items())}
        self._review = {
            "archive_id": self.archive_id, "source_fingerprint": self.fingerprint, "provider_calls": 0,
            "selection": self.selection.describe(), "writes": writes, "outcomes": outcomes,
            "mapping": self._mapping(), "unresolved": self.desired.unresolved + self.unresolved,
            "not_imported": self.desired.not_imported, "skipped": sorted(self.desired.skipped),
            "flags": self.desired.flags, "manifest_unresolved": list(manifest.get("unresolved", ())),
            "losses": list(manifest.get("losses", ())),
        }
        if not writes:
            self._preview = {}
            return
        rendered = (self._probe or self._batch_of()).preview()  # type: ignore[attr-defined]
        written = dict(self.receipts.written)
        written.update({path: etag(rendered[path]) for path in writes})
        desired = {**self.receipts.desired, **{path: self.digests[path] for path in writes if path in self.digests}}
        mapping = {key: list(rows) for key, rows in self.receipts.mapping.items()}
        for key, rows in self._review["mapping"].items():
            have = {json.dumps(row, sort_keys=True) for row in mapping.setdefault(key, [])}
            mapping[key].extend(row for row in rows if json.dumps(row, sort_keys=True) not in have)
        run = self.receipts.run + 1
        source = receipt_source(self.archive_id)
        self.receipt = SourceRef(source, RECEIPT_KIND, RECEIPT_ENTITY).at(f"r{run}")
        payload = {**self._review, "run": run, "applied_at": self.at, "written": written, "desired": desired,
                   "mapping": mapping, "run_mapping": self._review["mapping"]}
        receipt = RecordEnvelope(self.receipt, json.loads(json.dumps(payload)), name=f"V1 import, run {run}",
                                 owner=self.owner, extra={"schema": RECEIPT_SCHEMA})
        manifest_envelope = SourceManifest(source, self.owner, "V1 import receipts",
                                           {f"{RECEIPT_KIND}/{RECEIPT_ENTITY}": f"r{run}"},
                                           extra={MARKER: {"archive_id": self.archive_id, "receipts": True}})
        try:
            self._batch = self._batch_of([(manifest_envelope, self.receipts.manifest_etag), (receipt, None)])
            self._preview = self._batch.preview()
        except InvalidEnvelope as error:  # one receipt holds every run: past the store's document limit
            raise ImportRefused(f"the import receipt would be too large to store: {error}", "receipt_too_large") \
                from None

    # -- the public face -------------------------------------------------------------------------
    def review(self) -> dict:
        """What this import maps, writes and leaves out, JSON-ready: the plan a human reviews."""
        return json.loads(json.dumps(self._review))

    def preview(self) -> dict[str, bytes]:
        """The exact bytes of every target the apply writes, keyed by path: what a human approves."""
        return dict(self._preview)

    def apply(self, approvals: Mapping[str, str] | None = None) -> ImportResult:
        """Commit the plan as one transaction, after re-checking under the store lock what it relied on.

        ``StaleWrite`` if the receipts moved, a new campaign's id or a new actor's id was taken, or a
        target changed since the plan; replan and review again."""
        review = self.review()
        if not self._preview:
            return ImportResult({}, None, review)
        store = self.store
        with store.lock:
            if self._committed:
                raise StoreError("this import was already applied")
            now = read_receipts(store, self.archive_id)
            if (now.manifest_etag, now.head_etag) != (self.receipts.manifest_etag, self.receipts.head_etag):
                raise StaleWrite([("/".join(manifest_parts(receipt_source(self.archive_id))), "changed")])
            spent = frozenset((approval, address, payload_digest(self._preview[address]))
                              for address, approval in (approvals or {}).items()
                              if type(address) is str and type(approval) is str and address in self._preview)
            for cid in sorted(self.new_campaigns):
                if store.campaign_presence(cid) != "absent":
                    raise StaleWrite([(f"campaigns/{cid}", "exists")])
                if store.campaign_authority_presence(cid) != "absent":
                    raise StaleWrite([(f"authority/campaigns/{cid}", "exists")])
                if cid in security_campaign_ids(store, spent=spent):
                    raise StaleWrite([("authority/security", f"a grant or approval names campaigns/{cid}")])
            for path, target in sorted(self.targets.items()):  # nothing the writes build on moved since the plan
                if target.outcome in (PRESENT, KEPT) and target.etag is not None:
                    try:
                        now_bytes = store.read_exact(path)
                    except (StoreError, ValueError, OSError):
                        now_bytes = None
                    if now_bytes is None or etag(now_bytes) != target.etag:
                        raise StaleWrite([(path, "changed since the plan")])
            for cid, scope_path in sorted(self.relied_scopes.items()):
                if any(path.startswith(f"campaigns/{cid}/") for path in self._preview) and (
                        store.campaign_presence(cid) != "present" or store.read_exact(scope_path) is None):
                    raise StaleWrite([(scope_path, "removed")])  # never add to a campaign deleted since the plan
            for cid, ids in sorted(self.new_actors.items()):
                taken = campaign_taken_ids(store, cid, spent=spent)
                clash = sorted(ids & taken)
                if clash:
                    raise StaleWrite([(_path_of(InstanceRef(cid, "character", i)), "id taken") for i in clash])
            for cid, actors in sorted(self.bound.items()):  # another kind may have taken a bound actor's id since
                clash = sorted(str(ref) for ref in actors & paired_instances(store.campaign_instances(cid)))
                if clash:
                    raise StaleWrite([(ref, "id also used by another kind") for ref in clash])
            committed = self._batch.commit(approvals)  # type: ignore[union-attr]
            self._committed = True
        return ImportResult(committed.etags, self.receipt, review)


@contextmanager
def _no_unstorable() -> Iterator[None]:
    """The mapper leaves out every address no library could store; one it missed refuses the run, never escapes."""
    try:
        yield
    except UnstorableIdentifier as error:
        raise ImportRefused(str(error), "unstorable_id") from None


def plan_import(store: LibraryStore, source, manifest: Mapping, *, handle: object, owner: str,
                selection: Selection | None = None, clock: Callable[[], str] | None = None,
                environ: Mapping[str, str] | None = None, home=None, _options=None) -> ImportPlan:
    """Plan importing a reviewed 011 ``manifest`` of the V1 library at ``source`` into ``store``.

    The source is first proven to hold exactly what the manifest reviewed (``open_verified``);
    every byte the plan uses is read from it then, and it is walked again before the plan is
    returned. Nothing is written until ``apply``. No model is called and nothing is fetched.
    """
    with open_verified(source, manifest, destination=store.root, environ=environ, home=home,
                       _options=_options) as verified:
        chosen = selection if selection is not None else Selection.proposed(manifest)
        desired = desired_state(manifest, chosen, verified.read, owner)
    at = (clock or _now)()
    archive = manifest["archive_id"]
    with store.read_pass(), _no_unstorable():
        plan = ImportPlan(store, handle, owner, at, archive, manifest["source_fingerprint"], chosen, desired,
                          read_receipts(store, archive))
        plan.plan_sources()
        plan.plan_definitions()
        plan.plan_campaigns()
        plan.plan_pictures(ImageLibrary(store, handle, owner=owner))
        plan.finish(manifest)
    return plan


__all__ = ["ImportPlan", "ImportResult", "RECEIPT_SCHEMA", "Receipts", "legacy_map", "plan_import", "read_receipts",
           "receipt_source"]
