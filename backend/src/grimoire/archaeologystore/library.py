"""Import a SillyTavern chat export into a world-less 107 chat with its transcript (Sprint 036). Adapter.

``StImporter.scan(data)`` reads an export and reports what it holds (speakers, posts, swipes, edits,
hidden messages and everything kept but not interpreted). It writes nothing and calls no provider.

``StImporter.plan(data, speakers=...)`` maps speakers to character definitions **only as the caller
says**: a speaker the caller does not map stays a label on its posts, never matched to a card by its
display name. The plan is one ``ChatChange`` (107's writer): the play scope, the chat with its
bindings and new actors, and the transcript pages, committed in one transaction. ``preview()`` shows
the exact bytes; ``commit(approvals)`` writes them.

The campaign and chat ids are ``st-``, 16 hex digits of the export's SHA-256 and this library's
device tag (028), so a repeat of the same file on this device finds its earlier import
(``already_imported``, nothing to write) instead of a second copy, and two paired devices that each
import it offline make two chats rather than two versions of one.
Nothing here writes events, evidence, beliefs or relations: a transcript records what was written.
See docs/ARCHAEOLOGY.md.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from grimoire.archaeology import FORMAT, Loss, StChat, StError, StSpeaker, read_st_chat
from grimoire.chats import ChatCodecError, ChatRecord, Launch, chat_from_payload
from grimoire.chatstore import (
    ChatChange, ChatCommit, ChatLibrary, DeviceIdentityUnusable, InvalidBinding, device_tag,
)
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef, TranscriptRef
from grimoire.ledgers import security_campaign_ids
from grimoire.store import InvalidEnvelope, LibraryStore, RecordNotFound, ScopeRecord, StaleWrite
from grimoire.store.jsonfmt import MAX_FILE_BYTES, JsonError, is_imprecise, parse_strict, render
from grimoire.transcripts import (
    TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptError, TranscriptPage, page_from_payload,
    page_to_payload, paginate,
)

MAX_EXPORT_BYTES = 64 * 1024 * 1024
#: rendered bytes of posts one page holds at most: half a store file, so the page's own JSON fits
PAGE_BUDGET = MAX_FILE_BYTES // 2
#: rendered bytes of page 1's header and origin at most; page 1's posts get the rest of PAGE_BUDGET
HEADER_BUDGET = PAGE_BUDGET // 2
#: the premise a chat with no mapped character or lorebook launches from (display only)
IMPORTED_PREMISE = "Imported SillyTavern chat"
_BOM = b"\xef\xbb\xbf"


class ImportRefused(ValueError):
    """An import this importer refuses; ``reason`` is machine-readable, ``line`` 1-based or 0."""

    def __init__(self, message: str, reason: str, line: int = 0) -> None:
        super().__init__(message)
        self.reason = reason
        self.line = line

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.line))


@dataclass(frozen=True)
class StScan:
    """What an export holds, before any mapping: nothing written, no provider called."""

    sha256: str
    byte_length: int
    chat: StChat
    losses: tuple[Loss, ...]

    @property
    def speakers(self) -> tuple[StSpeaker, ...]:
        return self.chat.speakers

    @property
    def counts(self) -> Mapping[str, int]:
        posts = self.chat.posts
        return {"posts": len(posts), "variants": sum(len(p.variants) for p in posts),
                "unchosen_variants": sum(len(p.unchosen) for p in posts),
                "hidden": sum(p.hidden for p in posts), "speakers": len(self.chat.speakers)}

    @property
    def campaign_id(self) -> str:
        """The export's id, ``st-`` and 16 hex digits of its digest; a library with a device identity
        imports it under this id plus its device tag (``StImport.chat`` is the id actually used)."""
        return "st-" + self.sha256[:16]


@dataclass(frozen=True)
class StImport:
    """A planned import. ``status`` is ``new`` (``change`` writes it) or ``already_imported`` (this very
    export, with the same speaker mapping, is already in the library; ``change`` is ``None``)."""

    status: str
    scan: StScan
    chat: ChatRef
    speakers: Mapping[str, SourceRef]
    actors: Mapping[str, InstanceRef]
    losses: tuple[Loss, ...]
    pages: tuple[TranscriptPage, ...]
    change: ChatChange | None

    #: provider calls this import makes, ever: scanning and applying are text only
    provider_calls: int = 0
    #: ``already_imported`` only: the earlier import's active bindings that follow their head (an import
    #: made before chats were pinned by default). Reported, never rewritten: 042's reviewed rebase re-pins.
    following: tuple[SourceRef, ...] = ()

    def preview(self) -> dict[str, bytes]:
        return {} if self.change is None else self.change.preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> ChatCommit | None:
        if self.change is None:
            return None
        return self.change.commit(approvals)


def _lines(data: bytes) -> tuple[list[tuple[int, Mapping]], list[Loss]]:
    losses: list[Loss] = []
    if data.startswith(_BOM):
        data = data[len(_BOM):]
        losses.append(Loss(1, "", "byte_order_mark"))
    out: list[tuple[int, Mapping]] = []
    for number, raw in enumerate(data.split(b"\n"), start=1):
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if not raw.strip(b" \t"):
            continue
        try:
            value = parse_strict(raw)
        except JsonError:
            raise ImportRefused(f"line {number} is not one strict UTF-8 JSON object", "invalid_json",
                                number) from None
        if _imprecise(raw):
            losses.append(Loss(number, "", "imprecise_number"))
        out.append((number, value))
    return out, losses


def _imprecise(raw: bytes) -> bool:
    """Whether a number on the line becomes a nearby double (its exact digits are not kept)."""
    found = False

    def on_float(text: str) -> float:
        nonlocal found
        found = found or is_imprecise(text)
        return 0.0

    if b"." not in raw and b"e" not in raw and b"E" not in raw:
        return False
    json.loads(raw.decode("utf-8"), parse_float=on_float)
    return found


def measure_post(post: Post) -> int:
    """The bytes one post adds to a stored page (rendered as the store renders, one level shallower)."""
    campaign = post.speaker.actor.campaign_id if post.speaker.actor is not None else "probe"
    probe = TranscriptPage(TranscriptRef(campaign, "probe", 1), (post,))
    return len(render({"post": page_to_payload(probe)["posts"][0]}))


def scan_st_export(data: bytes) -> StScan:
    """Read an export (pure apart from decoding): speakers, posts and losses; ``ImportRefused`` if malformed."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("an export is bytes")
    data = bytes(data)
    if len(data) > MAX_EXPORT_BYTES:
        raise ImportRefused(f"the export is larger than {MAX_EXPORT_BYTES} bytes", "too_large")
    lines, losses = _lines(data)
    try:
        chat = read_st_chat(lines)
    except StError as error:
        raise ImportRefused(str(error), error.reason, error.line) from None
    return StScan(hashlib.sha256(data).hexdigest(), len(data), chat, (*losses, *chat.losses))


def read_transcript(store: LibraryStore, chat: ChatRef) -> tuple[tuple[TranscriptPage, ...], tuple[object, ...]]:
    """A chat's decoded pages in order, and the problems (store problems, undecodable pages, a gap)."""
    found = store.chat_transcript(chat)
    pages: list[TranscriptPage] = []
    problems: list[object] = list(found.problems)
    for ref, stored in found.records.items():
        try:
            pages.append(page_from_payload(ref, stored.record.schema, stored.record.payload))  # type: ignore[arg-type]
        except TranscriptError as error:
            problems.append((str(ref), error.reason))
    for expected, page in enumerate(pages, start=1):
        if page.ref.seq != expected:
            problems.append((str(page.ref), "page_gap"))
            break
    return tuple(pages), tuple(problems)


class StImporter:
    """SillyTavern chat import over one open, writable ``LibraryStore`` (trusted application code)."""

    def __init__(self, store: LibraryStore, handle: object, *, clock: Callable[[], str],
                 owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self._clock = clock
        self._owner = owner

    def scan(self, data: bytes) -> StScan:
        return scan_st_export(data)

    def plan(self, data: bytes, *, speakers: Mapping[str, SourceRef] | None = None,
             lorebooks: Sequence[SourceRef] = (), label: str | None = None, name: str | None = None) -> StImport:
        """Plan the import. ``speakers`` maps a speaker id (from ``scan``) to the character definition it
        is: a ``user`` speaker becomes the chat's persona, a ``character`` speaker one of its characters.
        ``lorebooks`` are attached as they are. ``name`` is the export's file name, kept as provenance."""
        scan = scan_st_export(data)
        mapping = dict(speakers or {})
        known = {s.id: s for s in scan.chat.speakers}
        for sid, ref in mapping.items():
            speaker = known.get(sid)
            if speaker is None:
                raise ImportRefused(f"{sid} is not a speaker of this export", "unknown_speaker")
            if not isinstance(ref, SourceRef) or ref.kind != "character":
                raise ImportRefused(f"{sid} must map to a character definition", "role_kind")
            if speaker.role not in ("user", "character"):
                raise ImportRefused(f"a {speaker.role} speaker is not mapped to a definition in this slice",
                                    "role_kind")
        if name is not None and (type(name) is not str or len(name) > 1000):
            raise ImportRefused("name must be text of at most 1000 characters", "invalid_field")
        if label is not None and (type(label) is not str or len(label) > 1000):
            raise ImportRefused("label must be text of at most 1000 characters", "invalid_field")
        persona, characters = self._bindings(scan, mapping)
        try:
            campaign = scan.campaign_id + device_tag(self.store)
        except DeviceIdentityUnusable as error:
            raise ImportRefused(str(error), "device_identity") from None
        chat = ChatRef(campaign, campaign)
        origin = self._origin(scan, mapping, name, lorebooks, label)
        presence = self.store.campaign_presence(campaign)
        if presence != "absent":
            return self._existing(scan, chat, mapping, origin, presence)
        if campaign != scan.campaign_id and self.store.campaign_presence(scan.campaign_id) == "present":
            # imported here before this library had a device identity: that import is this one's
            untagged = ChatRef(scan.campaign_id, scan.campaign_id)
            try:
                return self._existing(scan, untagged, mapping, origin, "present")
            except ImportRefused as refused:
                if refused.reason != "campaign_exists":
                    raise
        if (self.store.campaign_authority_presence(campaign) != "absent"
                or campaign in security_campaign_ids(self.store)):
            raise ImportRefused(f"a ledger still names campaigns/{campaign}, whose folder is gone; this export "
                                "cannot be imported again on this device until it is cleared", "campaign_exists")
        if characters:
            launch, attach = Launch("character", characters[0]), [(ref, "character") for ref in characters[1:]]
        elif lorebooks:
            launch, attach = Launch("lorebook", lorebooks[0]), []
        else:
            launch, attach = Launch("premise", premise=IMPORTED_PREMISE), []
        attach += [(ref, "lorebook") for ref in lorebooks[(0 if characters else 1):]]
        ids = {"q": campaign, "t": campaign}
        library = ChatLibrary(self.store, self.handle, clock=self._clock, new_id=lambda prefix: ids[prefix],
                              owner=self._owner)
        try:
            change = library.start_chat(launch, attach=attach, persona=persona, label=label)
        except InvalidBinding as error:
            raise ImportRefused(str(error), error.reason) from None
        record = _chat_record(change)
        actors = self._actors(scan, mapping, record)
        pages = self._pages(scan, chat, actors, origin)
        puts = tuple((ScopeRecord(page.ref, TRANSCRIPT_SCHEMA, page_to_payload(page), owner=self._owner), None)
                     for page in pages)
        change = dataclasses.replace(change, puts=change.puts + puts)
        losses = (*scan.losses, *(Loss(s.first_line, "speaker", "unmapped_speaker")
                                  for s in scan.chat.speakers if s.id not in mapping))
        return StImport("new", scan, chat, mapping, actors, tuple(losses), pages, change)

    # -- helpers ---------------------------------------------------------------------------------

    def _bindings(self, scan: StScan, mapping: Mapping[str, SourceRef]
                  ) -> tuple[SourceRef | None, list[SourceRef]]:
        """The persona and the characters (first-speaking first), one per definition entity."""
        persona: SourceRef | None = None
        characters: dict[tuple[str, str, str], SourceRef] = {}
        for speaker in sorted(scan.chat.speakers, key=lambda s: s.first_line):
            ref = mapping.get(speaker.id)
            if ref is None:
                continue
            if speaker.role == "user":
                if persona is not None and persona != ref:
                    raise ImportRefused("two user speakers map to different personas; a chat has one",
                                        "second_persona")
                persona = ref
                continue
            seen = characters.get(ref.entity_key)
            if seen is not None and seen != ref:
                raise ImportRefused(f"{ref.unpinned()} is mapped at two revisions", "conflicting_revisions")
            characters[ref.entity_key] = ref
        if persona is not None and persona.entity_key in characters:
            raise ImportRefused(f"{persona.unpinned()} is mapped as both the persona and a character",
                                "persona_is_character")
        return persona, list(characters.values())

    def _origin(self, scan: StScan, mapping: Mapping[str, SourceRef], name: str | None,
                lorebooks: Sequence[SourceRef], label: str | None) -> dict:
        origin: dict = {"format": FORMAT, "sha256": scan.sha256, "bytes": scan.byte_length,
                        "posts": len(scan.chat.posts),
                        "speakers": {s.id: {"role": s.role, "label": s.label, "avatar": s.avatar,
                                            "definition": str(mapping[s.id]) if s.id in mapping else None}
                                     for s in scan.chat.speakers},
                        "lorebooks": [str(ref) for ref in lorebooks], "label": label}
        if name is not None:
            origin["name"] = name
        return origin

    def _actors(self, scan: StScan, mapping: Mapping[str, SourceRef], record: ChatRecord) -> dict[str, InstanceRef]:
        bound = {(b.role, b.ref.entity_key): b.actor for b in record.bindings if b.actor is not None}
        actors: dict[str, InstanceRef] = {}
        for speaker in scan.chat.speakers:
            ref = mapping.get(speaker.id)
            if ref is not None:
                role = "persona" if speaker.role == "user" else "character"
                actor = bound.get((role, ref.entity_key))
                assert actor is not None  # start_chat bound every mapped definition
                actors[speaker.id] = actor
        return actors

    def _pages(self, scan: StScan, chat: ChatRef, actors: Mapping[str, InstanceRef],
               origin: Mapping) -> tuple[TranscriptPage, ...]:
        posts: list[Post] = []
        for post, sid in zip(scan.chat.posts, scan.chat.speaker_ids):
            speaker = Speaker(post.speaker.role, actors.get(sid), post.speaker.label)
            posts.append(dataclasses.replace(post, speaker=speaker, source={**post.source, "speaker": sid}))
        try:
            first = len(render({"origin": {**origin, "pages": 10**9}, "header": scan.chat.header}))
        except InvalidEnvelope as error:  # past the store's document limit, or nested past what it writes
            raise ImportRefused(f"the export's header cannot be stored on a transcript page: {error}",
                                "header_too_large") from None
        if first > HEADER_BUDGET:
            raise ImportRefused(f"the export's header and speaker table take more than {HEADER_BUDGET} bytes",
                                "header_too_large")
        try:
            groups = paginate(posts, measure_post, PAGE_BUDGET, first_budget=PAGE_BUDGET - first)
        except TranscriptError as error:
            raise ImportRefused(str(error), error.reason) from None
        except InvalidEnvelope:  # one post past the store's document limit
            raise ImportRefused("a message is too large for one transcript page", "post_too_large") from None
        origin = {**origin, "pages": len(groups)}
        pages = []
        for seq, group in enumerate(groups, start=1):
            page_origin = dict(origin) if seq == 1 else {k: origin[k] for k in ("format", "sha256")}
            pages.append(TranscriptPage(TranscriptRef(chat.campaign_id, chat.chat_id, seq), tuple(group),
                                        page_origin, scan.chat.header if seq == 1 else None))
        return tuple(pages)

    def _existing(self, scan: StScan, chat: ChatRef, mapping: Mapping[str, SourceRef], origin: Mapping,
                  presence: str) -> StImport:
        """This export's campaign id is taken: the earlier import of it, or a refusal."""
        if presence != "present":
            raise ImportRefused(f"campaigns/{chat.campaign_id} exists but cannot be read ({presence})",
                                "campaign_exists")
        try:
            stored_chat = self.store.read(chat)
        except RecordNotFound:
            raise ImportRefused(f"campaigns/{chat.campaign_id} exists and is not this export's import",
                                "campaign_exists") from None
        pages, problems = read_transcript(self.store, chat)
        if problems or not _complete(pages, scan):
            raise ImportRefused(f"campaigns/{chat.campaign_id} exists and is not a complete import of this export",
                                "campaign_exists")
        earlier = pages[0].origin
        stored = earlier.get("speakers")
        if (not isinstance(stored, Mapping)
                or {k: v.get("definition") for k, v in stored.items() if isinstance(v, Mapping)}
                != {k: v.get("definition") for k, v in origin["speakers"].items()}
                or list(earlier.get("lorebooks", ())) != origin["lorebooks"] or earlier.get("label") != origin["label"]):
            raise ImportRefused("this export was already imported with a different speaker mapping, lorebooks "
                                "or label", "imported_differently")
        actors = {}
        for page in pages:
            for post in page.posts:
                sid = post.source.get("speaker")
                if post.speaker.actor is not None and type(sid) is str:
                    actors[sid] = post.speaker.actor
        return StImport("already_imported", scan, chat, mapping, actors, scan.losses, pages, None,
                        following=_following(chat, stored_chat))


def _complete(pages: Sequence[TranscriptPage], scan: StScan) -> bool:
    """Whether the stored pages begin with this export's whole import: page 1 names the export and
    how many pages and posts it wrote, and exactly those are there (a live runner may append more)."""
    if not pages or pages[0].origin.get("sha256") != scan.sha256 or pages[0].origin.get("format") != FORMAT:
        return False
    count, posts = pages[0].origin.get("pages"), pages[0].origin.get("posts")
    if type(count) is not int or type(posts) is not int or not 1 <= count <= len(pages):
        return False
    return posts == len(scan.chat.posts) == sum(len(page.posts) for page in pages[:count])


def _following(chat: ChatRef, stored: object) -> tuple[SourceRef, ...]:
    """The chat's active bindings that follow their head; none for a chat this version cannot interpret."""
    try:
        record = chat_from_payload(chat, stored.record.schema, stored.record.payload)  # type: ignore[attr-defined]
    except (ChatCodecError, AttributeError):
        return ()
    return tuple(binding.ref for binding in record.active_bindings() if not binding.ref.pinned)


def _chat_record(change: ChatChange) -> ChatRecord:
    for envelope, _ in change.puts:
        if isinstance(envelope, ScopeRecord) and envelope.address == change.chat:
            return chat_from_payload(change.chat, envelope.schema, envelope.payload)  # type: ignore[arg-type]
    raise StaleWrite([("chat", "missing from the planned change")])  # pragma: no cover - start_chat writes it


__all__ = ["IMPORTED_PREMISE", "MAX_EXPORT_BYTES", "PAGE_BUDGET", "ImportRefused", "StImport", "StImporter",
           "StScan", "measure_post", "read_transcript", "scan_st_export"]
