"""Where records live: an injective, portable mapping from identity to paths.

Each ``:`` segment of a source id is one directory and a source's own files
live in its ``_source/`` directory. An ID segment never starts with ``_``, so
``_source`` cannot collide with a sub-source: ``world:harbor:dlc`` is a
*sibling* of harbor's ``_source/``. Every authored path segment must be a valid
``ResourcePath`` segment, so it is portable (no Windows device names, no
trailing dots) and the whole path is the record's logical address for
authorization. Identifiers that cannot be mapped are refused, never renamed.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Literal

from grimoire.core.identity import (
    KNOWLEDGE_FAMILIES, AcceptedRef, ChatRef, HoldingsRef, ImageRef, InstanceRef, KnowledgeRef, PlacementRef,
    RelationRef, ScopeRef, SourceRef, TranscriptRef, TurnRef, WrapUpRef, validate_segment, validate_source_id,
)
from grimoire.security import InvalidResourcePath, ResourcePath

from .errors import UnstorableIdentifier
from .media import MEDIA

Parts = tuple[str, ...]

MARKER = "grimoire-library.json"
LIBRARY_FORMAT = "grimoire.library/1"
SOURCES = "sources"
CAMPAIGNS = "campaigns"
TXN = ".txn"
CONFLICTS = ".conflicts"
SYNC = ".sync"  # device-local sync state (019): never transferred, never loaded as records
LOCK = "lock"
JOURNAL = "journal.json"
DISCARD_MARKER = "discard.json"  # an interrupted discard_journal: never publish, finish the discard
REPAIR_MARKER = "repair.json"  # an unfinished repair_ledger: the ledger is undefined until it finishes
LEDGERS = "authority"
KEPT = "kept"
SYNC_DEVICE = "device.json"
SYNC_PEERS = "peers"
SYNC_PEER_FILES = frozenset({"archive.json", "pending.json", "keeps.json"})
#: The widest shard prefix (hex digits of a path's hash) a pair's archive or pending marker is split by (028).
SYNC_MAX_SHARD_WIDTH = 2
#: 028's shards of a pair's archive and pending marker, ``archive.<prefix>.json``: the store accepts the
#: names; ``grimoire.sync`` decides which of them the root document trusts.
_SYNC_SHARD = re.compile(r"(?:archive|pending)\.[0-9a-f]{1,%d}\.json" % SYNC_MAX_SHARD_WIDTH)
#: Device credentials (028): one pairing record per peer device under ``secrets/peers/``. Trusted
#: bookkeeping like ``.sync``: written without a grant, never a record, never transferred.
SECRETS = "secrets"
SECRET_PEERS = "peers"
_LEDGER_FILE = re.compile(r"[0-9]{10}\.json")
SOURCE_DIR = "_source"
MANIFEST = "manifest.json"
RECORDS = "records"
INSTANCES = "instances"
SUFFIX = ".json"
CHATS = "chats"  # campaigns/<id>/chats/<chat_id>.json: one chat record (107)
RELATIONS = "relations"  # campaigns/<id>/relations/<relation_id>.json: one relation record (017)
ACCEPTED = "accepted"  # campaigns/<id>/accepted/<event_id>.json: one accepted fictional event (021)
WRAPUPS = "wrapups"  # campaigns/<id>/wrapups/<wrapup_id>.json: one scene wrap-up (021)
TURNS = "turns"  # campaigns/<id>/turns/<turn_id>.json: one played chat turn (035)
SCOPE = "scope.json"  # campaigns/<id>/scope.json: the play-scope record (107)
EVENTS = "events"  # campaigns/<id>/events/<event_id>.json: accepted events (020)
EVIDENCE = "evidence"  # campaigns/<id>/evidence/<family>/<id>.json: everything else, and the batch log (020)
#: families stored under ``evidence/`` (an event lives under ``events/``)
EVIDENCE_FAMILIES = frozenset(KNOWLEDGE_FAMILIES) - {"event"}
IMAGES = "images"  # <owner>/images/<image_id>.json: one image object (018)
PLACEMENTS = "placements"  # <owner>/placements/<kind>/<id>/<slot>.json: one placement (018)
ASSETS = "assets"  # assets/blobs/<sha[:2]>/<sha256>.<ext>: content-addressed bytes (018)
BLOBS = "blobs"
BLOB_EXTS = tuple(MEDIA)  # the sniffer's formats are the only blob extensions
_BLOB_NAME = re.compile(rf"(?P<sha>[0-9a-f]{{64}})\.(?P<ext>{'|'.join(map(re.escape, BLOB_EXTS))})")
TRANSCRIPTS = "transcripts"  # campaigns/<id>/transcripts/<chat_id>/<seq:010d>.json: one transcript page (036)
_PAGE_NAME = re.compile(r"[0-9]{10}\.json")
HOLDINGS = "holdings"  # campaigns/<id>/holdings/<kind>/<instance_id>.json: counted holdings (023)

ROOT_ENTRIES = frozenset({MARKER, SOURCES, CAMPAIGNS, TXN, CONFLICTS, SYNC})
#: Reserved for later record families; the loader skips them. ``kept`` is never a folder: it
#: is the authorization namespace of kept versions (``kept_address``), so no record root may alias it.
#: ``events``/``evidence`` belong to 020 and are read only by ``campaign_knowledge``, and ``holdings``
#: (023, whose per-holder operation logs are history too) only by ``campaign_holdings``, so ``load()``'s
#: cost never grows with history.
RESERVED_ROOTS = frozenset({"authority", "config", "assets", "secrets", KEPT})
RESERVED_CAMPAIGN_ENTRIES = frozenset({"scenes", "events", "evidence", HOLDINGS})
_NOISE = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

Kind = Literal["definition", "manifest", "instance", "ledger", "scope", "chat", "relation", "knowledge", "blob",
               "image", "placement", "accepted", "wrapup", "holdings", "transcript", "turn"]


def is_noise(name: str) -> bool:
    """Operating-system and editor droppings, ignored without a report."""
    return (name in _NOISE or name.endswith("~") or name.endswith((".swp", ".swo"))
            or name.startswith(".#"))


def name_key(name: str) -> str:
    """The comparison a case-insensitive or normalizing filesystem might make."""
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", name).casefold())


def logical(parts: Parts) -> ResourcePath:
    """The record's logical address; refuses what cannot be a portable path."""
    try:
        return ResourcePath(tuple(parts))
    except InvalidResourcePath as error:
        raise UnstorableIdentifier(f"{'/'.join(parts)!r} cannot be stored portably: {error}") from None


def _checked(parts: Parts) -> Parts:
    logical(parts)
    return parts


def source_parts(source_id: str) -> Parts:
    return _checked((SOURCES, *source_id.split(":"), SOURCE_DIR))


def manifest_parts(source_id: str) -> Parts:
    return _checked(source_parts(source_id) + (MANIFEST,))


def definition_parts(ref: SourceRef) -> Parts:
    if not ref.pinned:
        raise ValueError(f"a definition file is one pinned revision, not {ref}")
    return _checked(source_parts(ref.source) + (RECORDS, ref.kind, ref.entity_id, ref.revision + SUFFIX))


def instance_parts(ref: InstanceRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, INSTANCES, ref.kind, ref.instance_id + SUFFIX))


def scope_parts(ref: ScopeRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, SCOPE))


def chat_parts(ref: ChatRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, CHATS, ref.chat_id + SUFFIX))


def relation_parts(ref: RelationRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, RELATIONS, ref.relation_id + SUFFIX))


def knowledge_parts(ref: KnowledgeRef) -> Parts:
    if ref.family == "event":
        return _checked((CAMPAIGNS, ref.campaign_id, EVENTS, ref.record_id + SUFFIX))
    return _checked((CAMPAIGNS, ref.campaign_id, EVIDENCE, ref.family, ref.record_id + SUFFIX))


def accepted_parts(ref: AcceptedRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, ACCEPTED, ref.event_id + SUFFIX))


def wrapup_parts(ref: WrapUpRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, WRAPUPS, ref.wrapup_id + SUFFIX))


def turn_parts(ref: TurnRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, TURNS, ref.turn_id + SUFFIX))


def image_parts(ref: ImageRef) -> Parts:
    if ref.campaign_id is not None:
        return _checked((CAMPAIGNS, ref.campaign_id, IMAGES, ref.image_id + SUFFIX))
    return _checked(source_parts(ref.scope) + (IMAGES, ref.image_id + SUFFIX))


def placement_parts(ref: PlacementRef) -> Parts:
    subject = ref.subject
    if isinstance(subject, InstanceRef):
        return _checked((CAMPAIGNS, subject.campaign_id, PLACEMENTS, subject.kind, subject.instance_id,
                         ref.slot + SUFFIX))
    return _checked(source_parts(subject.source) + (PLACEMENTS, subject.kind, subject.entity_id, ref.slot + SUFFIX))


def blob_parts(sha256: str, ext: str) -> Parts:
    """Where the blob with these exact bytes lives; refuses anything but 64 lowercase hex and a known ext."""
    if not isinstance(sha256, str) or not isinstance(ext, str) or not _BLOB_NAME.fullmatch(f"{sha256}.{ext}"):
        raise ValueError(f"not a blob name: {sha256!r}.{ext!r}")
    return _checked((ASSETS, BLOBS, sha256[:2], f"{sha256}.{ext}"))


def transcript_parts(ref: TranscriptRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, TRANSCRIPTS, ref.chat_id, f"{ref.seq:010d}{SUFFIX}"))


def holdings_parts(ref: HoldingsRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, HOLDINGS, ref.kind, ref.instance_id + SUFFIX))


Address = (SourceRef | InstanceRef | ScopeRef | ChatRef | RelationRef | KnowledgeRef | ImageRef | PlacementRef
           | AcceptedRef | WrapUpRef | HoldingsRef | TranscriptRef | TurnRef)


def address_parts(address: Address) -> Parts:
    if isinstance(address, KnowledgeRef):
        return knowledge_parts(address)
    if isinstance(address, ImageRef):
        return image_parts(address)
    if isinstance(address, PlacementRef):
        return placement_parts(address)
    if isinstance(address, InstanceRef):
        return instance_parts(address)
    if isinstance(address, ScopeRef):
        return scope_parts(address)
    if isinstance(address, ChatRef):
        return chat_parts(address)
    if isinstance(address, RelationRef):
        return relation_parts(address)
    if isinstance(address, AcceptedRef):
        return accepted_parts(address)
    if isinstance(address, WrapUpRef):
        return wrapup_parts(address)
    if isinstance(address, HoldingsRef):
        return holdings_parts(address)
    if isinstance(address, TranscriptRef):
        return transcript_parts(address)
    if isinstance(address, TurnRef):
        return turn_parts(address)
    return definition_parts(address)


#: Per-campaign authority ledgers: ``campaigns/`` (003 sovereignty), ``adjudication/`` (029) and
#: ``obstacles/`` (040).
CAMPAIGN_LEDGERS = ("campaigns/", "adjudication/", "obstacles/")


def ledger_name(name: object) -> str:
    """A ledger name: ``security``, or a campaign ledger such as ``campaigns/<campaign_id>``; anything else is refused."""
    if name == "security":
        return name
    for prefix in CAMPAIGN_LEDGERS:
        if isinstance(name, str) and name.startswith(prefix):
            try:
                validate_segment(name[len(prefix):], "campaign_id")
            except ValueError:
                break
            return name
    raise ValueError(f"not a ledger name: {name!r}")


def ledger_folder(name: str) -> Parts:
    return _checked((LEDGERS, *ledger_name(name).split("/")))


def ledger_parts(name: str, seq: int) -> Parts:
    if type(seq) is not int or not 1 <= seq < 10 ** 10:
        raise ValueError(f"ledger sequence out of range: {seq!r}")
    return _checked(ledger_folder(name) + (f"{seq:010d}.json",))


def ledger_seq(file_name: str) -> int | None:
    """The sequence number a canonical batch file name holds, or ``None``."""
    if not _LEDGER_FILE.fullmatch(file_name):
        return None
    seq = int(file_name[:10])
    return seq if seq >= 1 else None


def _stem(name: str) -> str | None:
    if not name.endswith(SUFFIX):
        return None
    stem = name[: -len(SUFFIX)]
    try:
        validate_segment(stem, "file name")
    except ValueError:
        return None
    return stem


def classify(parts: Parts) -> tuple[Kind, object] | None:
    """The record a canonical path holds, or ``None``; exact inverse of the mappings.

    Values: a ref for records, the source id for a manifest, ``(name, seq)`` for a ledger batch
    and ``(sha256, ext)`` for a blob.
    """
    parts = tuple(parts)
    found: tuple[Kind, object] | None = None
    try:
        if len(parts) == 4 and parts[0] == ASSETS and parts[1] == BLOBS:
            match = _BLOB_NAME.fullmatch(parts[3])
            if match is not None:
                found = ("blob", (match["sha"], match["ext"]))
        elif len(parts) == 4 and parts[0] == CAMPAIGNS and parts[2] == IMAGES:
            stem = _stem(parts[3])
            if stem is not None:
                found = ("image", ImageRef(f"campaign:{parts[1]}", stem))
        elif len(parts) == 6 and parts[0] == CAMPAIGNS and parts[2] == PLACEMENTS:
            stem = _stem(parts[5])
            if stem is not None:
                found = ("placement", PlacementRef(InstanceRef(parts[1], parts[3], parts[4]), stem))
        elif len(parts) == 5 and parts[0] == CAMPAIGNS and parts[2] == INSTANCES:
            stem = _stem(parts[4])
            if stem is not None:
                found = ("instance", InstanceRef(parts[1], parts[3], stem))
        elif len(parts) == 3 and parts[0] == CAMPAIGNS and parts[2] == SCOPE:
            found = ("scope", ScopeRef(parts[1]))
        elif len(parts) == 5 and parts[0] == CAMPAIGNS and parts[2] == HOLDINGS:
            stem = _stem(parts[4])
            if stem is not None:
                found = ("holdings", HoldingsRef(parts[1], parts[3], stem))
        elif len(parts) == 4 and parts[0] == CAMPAIGNS and parts[2] == CHATS:
            stem = _stem(parts[3])
            if stem is not None:
                found = ("chat", ChatRef(parts[1], stem))
        elif len(parts) == 4 and parts[0] == CAMPAIGNS and parts[2] == RELATIONS:
            stem = _stem(parts[3])
            if stem is not None:
                found = ("relation", RelationRef(parts[1], stem))
        elif len(parts) == 4 and parts[0] == CAMPAIGNS and parts[2] == EVENTS:
            stem = _stem(parts[3])
            if stem is not None:
                found = ("knowledge", KnowledgeRef(parts[1], "event", stem))
        elif len(parts) == 5 and parts[0] == CAMPAIGNS and parts[2] == EVIDENCE and parts[3] in EVIDENCE_FAMILIES:
            stem = _stem(parts[4])
            if stem is not None:
                found = ("knowledge", KnowledgeRef(parts[1], parts[3], stem))
        elif len(parts) == 4 and parts[0] == CAMPAIGNS and parts[2] == ACCEPTED:
            stem = _stem(parts[3])
            if stem is not None:
                found = ("accepted", AcceptedRef(parts[1], stem))
        elif len(parts) == 5 and parts[0] == CAMPAIGNS and parts[2] == TRANSCRIPTS:
            if _PAGE_NAME.fullmatch(parts[4]) and int(parts[4][:10]) >= 1:
                found = ("transcript", TranscriptRef(parts[1], parts[3], int(parts[4][:10])))
        elif len(parts) == 4 and parts[0] == CAMPAIGNS and parts[2] == WRAPUPS:
            stem = _stem(parts[3])
            if stem is not None:
                found = ("wrapup", WrapUpRef(parts[1], stem))
        elif len(parts) == 4 and parts[0] == CAMPAIGNS and parts[2] == TURNS:
            stem = _stem(parts[3])
            if stem is not None:
                found = ("turn", TurnRef(parts[1], stem))
        elif len(parts) >= 3 and parts[0] == LEDGERS:
            seq = ledger_seq(parts[-1])
            if seq is not None:
                found = ("ledger", (ledger_name("/".join(parts[1:-1])), seq))
        elif parts and parts[0] == SOURCES and SOURCE_DIR in parts:
            at = parts.index(SOURCE_DIR)
            source_id = ":".join(parts[1:at])
            validate_source_id(source_id)
            tail = parts[at + 1:]
            if tail == (MANIFEST,):
                found = ("manifest", source_id)
            elif len(tail) == 4 and tail[0] == RECORDS:
                stem = _stem(tail[3])
                if stem is not None:
                    found = ("definition", SourceRef(source_id, tail[1], tail[2], stem))
            elif len(tail) == 2 and tail[0] == IMAGES:
                stem = _stem(tail[1])
                if stem is not None:
                    found = ("image", ImageRef(source_id, stem))
            elif len(tail) == 4 and tail[0] == PLACEMENTS:
                stem = _stem(tail[3])
                if stem is not None:
                    found = ("placement", PlacementRef(SourceRef(source_id, tail[1], tail[2]), stem))
    except ValueError:
        return None
    if found is None:
        return None
    kind, value = found
    try:
        if kind == "ledger":
            canonical = ledger_parts(*value)  # type: ignore[misc]
        elif kind == "manifest":
            canonical = manifest_parts(value)  # type: ignore[arg-type]
        elif kind == "blob":
            canonical = blob_parts(*value)  # type: ignore[misc]
        else:
            canonical = address_parts(value)  # type: ignore[arg-type]
    except (ValueError, UnstorableIdentifier):
        return None
    return found if canonical == parts else None


def conflict_parts(target: Parts, new_etag: str, attempt: int = 0) -> Parts:
    """A bounded, flat home for a preserved version of ``target``.

    ``attempt`` distinguishes different bytes that would otherwise share a name
    (an edited conflict file, or two versions with the same digest prefix).
    """
    folder = hashlib.sha256("/".join(target).encode("utf-8")).hexdigest()[:16]
    name = f"{target[-1]}~{new_etag.removeprefix('sha256:')[:12]}"
    return (CONFLICTS, folder, name if not attempt else f"{name}.{attempt}")


def kept_address(target: Parts, new_etag: str, attempt: int = 0) -> ResourcePath:
    """The authorization address of a kept version: one-to-one with its ``conflict_parts`` slot.

    ``kept/<hash16>/<digest12>[-N]/<file name>``: four valid segments, distinct from the record's
    own address, so "keep these bytes beside p" and "overwrite p" are different approvals.
    """
    folder, name = conflict_parts(target, new_etag, attempt)[1:]
    digest = new_etag.removeprefix("sha256:")[:12]
    return logical((KEPT, folder, digest if not attempt else f"{digest}-{attempt}", target[-1]))


_KEPT_NAME = re.compile(r"(?P<base>.+)~[0-9a-f]{12}(?:\.[1-9][0-9]{0,8})?")


def is_kept_target(parts: Parts) -> bool:
    """A path the store itself could give a kept version: ``.conflicts/<16 hex>/<name>~<12 hex>[.N]``."""
    parts = tuple(parts)
    if len(parts) != 3 or parts[0] != CONFLICTS or not re.fullmatch(r"[0-9a-f]{16}", parts[1]):
        return False
    found = _KEPT_NAME.fullmatch(parts[2])
    if found is None or not found["base"].endswith(SUFFIX):
        return False
    try:
        ResourcePath((found["base"],))
    except InvalidResourcePath:
        return False
    return True


def sync_state_parts(name: str) -> Parts:
    """``device.json`` or ``peers/<device>/<file>`` under ``.sync``, where ``<file>`` is ``archive.json``,
    ``pending.json``, ``keeps.json`` or a shard ``{archive,pending}.<hex>.json``; refuses anything else."""
    if name == SYNC_DEVICE:
        return (SYNC, SYNC_DEVICE)
    pieces = name.split("/") if isinstance(name, str) else []
    if len(pieces) == 3 and pieces[0] == SYNC_PEERS and (pieces[2] in SYNC_PEER_FILES
                                                        or _SYNC_SHARD.fullmatch(pieces[2])):
        try:
            validate_segment(pieces[1], "device")
            ResourcePath((pieces[1],))
        except (ValueError, InvalidResourcePath):
            pass
        else:
            return (SYNC, *pieces)
    raise ValueError(f"not a sync state name: {name!r}")


def secret_parts(name: str) -> Parts:
    """``peers/<device>.json`` under ``secrets`` (028's pairing records); refuses anything else."""
    pieces = name.split("/") if isinstance(name, str) else []
    if len(pieces) == 2 and pieces[0] == SECRET_PEERS and pieces[1].endswith(SUFFIX):
        device = pieces[1][:-len(SUFFIX)]
        try:
            validate_segment(device, "device")
            ResourcePath((device,))
        except (ValueError, InvalidResourcePath):
            pass
        else:
            return (SECRETS, *pieces)
    raise ValueError(f"not a secret name: {name!r}")


def is_secret_state(parts: Parts) -> bool:
    parts = tuple(parts)
    if not parts or parts[0] != SECRETS:
        return False
    try:
        return secret_parts("/".join(parts[1:])) == parts
    except ValueError:
        return False


def is_sync_state(parts: Parts) -> bool:
    parts = tuple(parts)
    if not parts or parts[0] != SYNC:
        return False
    try:
        return sync_state_parts("/".join(parts[1:])) == parts
    except ValueError:
        return False
