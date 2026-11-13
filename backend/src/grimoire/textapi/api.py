"""The text API (044): what the phone (or any client) calls to play a V2 library, as JSON ops.

``TextApi`` is trusted application code over one open library, acting for its owner: it writes only
through 107's ``ChatLibrary``, 035's ``SceneRunner`` (with 029's ``AdjudicationLibrary`` on the app's own
adjudication key, so a turn framed and decided at the campaign hub (113) is read when it is narrated), 052's ``QuickStart``, 038's ``ImageLibrary`` and
028's ``transfer``, and keeps no record of its own. Every op takes a JSON object and returns one; a
refusal is ``ApiRefused`` with the service's own reason. See docs/TEXT_API.md.
"""

from __future__ import annotations

import base64
import binascii
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from grimoire.adjudicationstore import AdjudicationLibrary
from grimoire.decisionstore import DecisionLibrary
from grimoire.goals import Agenda
from grimoire.chats import NavEntry
from grimoire.chats.codec import ChatCodecError, chat_from_payload
from grimoire.chatstore import ChatLibrary, InvalidBinding
from grimoire.core.identity import (
    ChatRef,
    ImageRef,
    InstanceRef,
    PlacementRef,
    ScopeRef,
    SourceRef,
)
from grimoire.devicetag import DeviceIdentityUnusable, device_tag
from grimoire.images import Placement
from grimoire.imagestore import ImageLibrary, InvalidImage, InvalidPlacement
from grimoire.peersync import (
    AuthenticationFailed,
    NotPaired,
    read_pairing,
    resolve_conflict,
    transfer,
)
from grimoire.peersync.pairing import pairing_ended
from grimoire.effective import chat_snapshot
from grimoire.play import SceneRunner
from grimoire.play.history import chat_transcript
from grimoire.quickstart import QuickStart, QuickStartRefused
from grimoire.scene import SceneRefused
from grimoire.store import BlobCorrupt, LibraryStore, RecordNotFound, StaleWrite, StoreError, etag
from grimoire.store.jsonfmt import is_etag
from grimoire.store.layout import placement_parts
from grimoire.sync import NoDeviceIdentity, SyncError, SyncInterrupted, device_of
from grimoire.sync.turns import open_conflicts

from .errors import ApiRefused
from .views import (
    FILTER_ROLES,
    art_view,
    chat_entry,
    filter_sources,
    fork_view,
    report_view,
    role_of,
    set_aside_view,
    source_key,
    turn_view,
)

#: The app's own adjudication key (029): never a player's, never the owner's, never a labeller's. The text API
#: only reads with it (a framed turn's outcome, before narration); the campaign hub (113) frames and decides.
ADJUDICATOR = "app:adjudicator"
#: 038's own cap on one picture's bytes (W1-W2); the decoded upload may be no larger.
MAX_ART_BYTES = 32 * 1024 * 1024
MAX_POST_CHARS = 20_000
MAX_LIMIT = 1000
#: The slot a new picture takes when the client names none: the actor's avatar.
DEFAULT_SLOT = "avatar"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def field(args: Mapping[str, object], key: str, kind: type, *, required: bool = True, default=None):  # type: ignore[no-untyped-def]
    """``args[key]`` as exactly ``kind`` (``bool`` is not an ``int``), else ``invalid_field``. Shared with the app's
    adapter ops (108) so every op checks its fields the same way."""
    value = args.get(key, default)
    if value is None and not required:
        return default
    if type(value) is not kind:
        raise ApiRefused("invalid_field", f"{key} must be a {kind.__name__}")
    return value


def parse_ref(parse: Callable[[str], object], args: Mapping[str, object], key: str, *, required: bool = True):  # type: ignore[no-untyped-def]
    """``args[key]`` parsed by an owning backend's own parser (``SourceRef.parse``, ``ChatRef.parse``...), else
    ``invalid_field``: a reference travels as its canonical string and is never guessed from a name."""
    text = field(args, key, str, required=required)
    if text is None:
        return None
    try:
        return parse(text)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", f"{key} is not a valid reference") from None


def _refused(error: Exception) -> ApiRefused:
    reason = getattr(error, "reason", None)
    return ApiRefused(reason if isinstance(reason, str) and reason else type(error).__name__, str(error))


def refusal(error: BaseException) -> ApiRefused | None:
    """How the text API answers ``error``: the refusal of a service it knows, or ``None`` for anything else (a
    bug, or a refusal of a service only an app adapter knows). Shared with the app's adapters (108), which map
    their own services' refusals only where this gives ``None``, so a stale write is ``changed`` everywhere."""
    if isinstance(error, (SceneRefused, QuickStartRefused, InvalidImage, InvalidPlacement, InvalidBinding)):
        return _refused(error)
    if isinstance(error, StaleWrite):
        return ApiRefused("changed", f"the library changed meanwhile; nothing was written ({error})")
    if isinstance(error, BlobCorrupt):
        return ApiRefused("blob_corrupt", str(error))
    if isinstance(error, (DeviceIdentityUnusable, SyncError)):
        return ApiRefused("device_identity", str(error))
    if isinstance(error, StoreError):  # any other refusal of the store: nothing was written
        return ApiRefused(type(error).__name__, str(error))
    return None


class TextApi:
    """The ops a client may call on one open library. ``owner`` is the library owner (a ``human:`` principal)
    and ``player`` who plays at its table: in this slice only the owner (one person, one library; a different
    player would read the table unfiltered and is refused until 053 and 102 give players their own view). ``runtime`` is the app's 027 ``GenerationRuntime`` (``None``: ``generate`` is refused
    ``no_runtime``; everything else still works). ``peers`` maps each paired device id to the loopback address
    ``sync`` dials. Calls are serialized: one library, one writer at a time."""

    def __init__(self, store: LibraryStore, authority, *, owner: str, player: str | None = None,  # type: ignore[no-untyped-def]
                 runtime=None, policy=None, voices=None, stages=None, agendas=(), peers: Mapping[str, tuple[str, int]] | None = None,
                 clock: Callable[[], str] = _utc_now, new_id: Callable[[str], str] = _new_id,
                 transfer_timeout: float = 600.0) -> None:
        self.store = store
        self.authority = authority
        if player is not None and player != owner:
            raise ValueError("this slice plays as the library owner only")
        self.owner = self.player = owner
        self.owner_handle = self.player_handle = authority.handle_for(owner, trusted=True)
        # one adjudication key for the app: a turn framed at the hub is read here with the key it was framed under
        self.adjudication = AdjudicationLibrary(store, authority, authority.handle_for(ADJUDICATOR, trusted=True))
        self.decisions = DecisionLibrary(store, authority, authority.handle_for("app:decisions", trusted=True))
        # Preserve startup ownership independently of editable provider settings.
        # Invalid container shapes still reach SceneRunner's existing validation.
        try:
            startup_agendas = agendas if isinstance(agendas, (str, bytes, Mapping)) else tuple(agendas)
        except TypeError:
            raise SceneRefused("bad_agenda", "agendas must be a collection of Agenda values") from None
        self.runner = SceneRunner(store, authority, self.owner_handle, owner=owner, runtime=runtime, policy=policy,
                                  voices=voices, adjudication=self.adjudication, stages=stages, agendas=startup_agendas,
                                  decisions=self.decisions)
        self._startup_agendas = startup_agendas
        self.chats = ChatLibrary(store, self.owner_handle, clock=clock, new_id=new_id, owner=owner)
        self.quick = QuickStart(self.chats, self.runner)
        self.images = ImageLibrary(store, self.owner_handle, owner=owner)
        self.runtime = runtime
        self.peers = {peer: (str(host), int(port)) for peer, (host, port) in dict(peers or {}).items()}
        self.reports: dict[str, dict] = {}
        self.transfer_timeout = transfer_timeout
        self._new_image = new_id
        self._lock = threading.RLock()
        self._ops: dict[str, Callable[[Mapping[str, object]], dict]] = {
            "library": self._library, "chats": self._chats, "open": self._open, "continue": self._continue,
            "post": self._post, "generate": self._generate, "keep": self._keep, "pass": self._pass,
            "pause": self._pause, "resume": self._resume, "choose": self._choose, "add_art": self._add_art,
            "sync": self._sync, "resolve": self._resolve,
        }

    @property
    def startup_agendas(self) -> tuple[Agenda, ...]:
        """Validated immutable startup policies; provider settings never own these."""
        return self._startup_agendas

    @property
    def lock(self) -> threading.RLock:
        """The lock every op runs under: an adapter beside this API (112's settings) takes it too, so nothing it
        changes on the runner lands in the middle of a turn."""
        return self._lock

    @property
    def ops(self) -> tuple[str, ...]:
        return tuple(sorted(self._ops))

    # -- dispatch ----------------------------------------------------------------------------------------
    def call(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        """Run one op. Raises ``ApiRefused``; any other exception is a bug the caller reports as such."""
        handler = self._ops.get(op) if isinstance(op, str) else None
        if handler is None:
            raise ApiRefused("unknown_op", f"no op {op!r}")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ApiRefused("invalid_field", "the arguments must be a JSON object")
        return self.serialized(handler, args)

    def serialized(self, handler: Callable[[Mapping[str, object]], dict], args: Mapping[str, object]) -> dict:
        """Run ``handler(args)`` as an op: under this library's one writer lock, with each service's refusal
        turned into ``ApiRefused`` exactly as for the core ops. 108's app adapters (``grimoire.app.adapters``)
        run through this, so an adapter op can never write beside a core op or answer a refusal differently."""
        with self._lock:
            try:
                return handler(args)
            except ApiRefused:
                raise
            except Exception as error:
                refused = refusal(error)
                if refused is None:
                    raise
                raise refused from None

    def image(self, ref: str) -> tuple[str, bytes]:
        """A picture's media type and exact bytes (038 checks them against the blob's name)."""
        try:
            image_ref = ImageRef.parse(ref) if isinstance(ref, str) else None
        except (ValueError, TypeError):
            image_ref = None
        if image_ref is None:
            raise ApiRefused("invalid_field", "image must be an image reference")
        with self._lock:
            try:
                image, _ = self.images.read_image(image_ref)
                return image.blob.media_type, self.images.read_image_bytes(image_ref)
            except InvalidImage as error:
                raise _refused(error) from None
            except BlobCorrupt as error:
                raise ApiRefused("blob_corrupt", str(error)) from None

    # -- reading -----------------------------------------------------------------------------------------
    def _device(self) -> str | None:
        try:
            return device_of(self.store)
        except (NoDeviceIdentity, SyncInterrupted, StoreError, OSError, ValueError):
            return None

    def _library(self, args: Mapping[str, object]) -> dict:
        peers = []
        for peer, (host, port) in sorted(self.peers.items()):
            try:
                paired = read_pairing(self.store, peer) is not None
            except (StoreError, OSError, ValueError):
                paired = False
            peers.append({"peer": peer, "address": f"{host}:{port}", "paired": paired,
                          "last": self.reports.get(peer)})
        return {"device": self._device(), "owner": self.owner, "player": self.player, "peers": peers,
                "generation": self.runtime is not None, "ops": list(self.ops)}

    def _source_name(self, view, ref: SourceRef) -> str | None:  # type: ignore[no-untyped-def]
        try:
            return str(view.index.resolve(ref.unpinned()).name)
        except Exception:  # noqa: BLE001 -- a name is presentation only; a missing source shows its ref
            return None

    def _imported(self, chat: ChatRef) -> tuple[tuple[str, str], ...]:
        try:
            captured = chat_snapshot(self.store.load(), chat)
        except (RecordNotFound, ChatCodecError):
            return ()
        lines, _problems = chat_transcript(self.store, chat, captured.chat, captured.snapshot)
        return lines or ()

    def _chats(self, args: Mapping[str, object]) -> dict:
        which = field(args, "filter", str, required=False, default="recent")
        limit = field(args, "limit", int, required=False, default=50)
        if isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise ApiRefused("invalid_field", f"limit must be 1 to {MAX_LIMIT}")
        view = self.store.load()
        index = self.chats.navigation()
        if which == "recent":
            found = index.recent(limit)
        elif which in FILTER_ROLES:
            source = parse_ref(SourceRef.parse, args, "source")
            if source.pinned:  # a pinned ref would find only the chats bound to that one revision
                source = source.unpinned()
            matched = {ref for role in FILTER_ROLES[which] for ref in index.by_source(source, role=role)}
            found = tuple(ref for ref in index.all_chats() if ref in matched)[:limit]  # newest first, uncapped
        else:
            raise ApiRefused("invalid_field", "filter must be recent, character or scenario")
        entries: list[NavEntry] = [index.entry(ref) for ref in index.all_chats()]
        names = self._mention_names(view, entries)
        return {"filter": which, "chats": self._entries(view, index, found, names),
                "sources": filter_sources(entries, names)}

    def _mention_names(self, view, entries) -> dict[str, str | None]:  # type: ignore[no-untyped-def]
        refs = {source_key(key) for entry in entries for _role, key, _rev, _active in entry.mentions}
        return {ref: self._source_name(view, SourceRef.parse(ref)) for ref in refs}

    def _entries(self, view, index, found, names) -> list[dict]:  # type: ignore[no-untyped-def]
        chats = []
        for ref in found:
            stored = view.chats.get(ref)
            record = None
            if stored is not None:
                try:
                    record = chat_from_payload(ref, stored.record.schema, stored.record.payload)
                except ChatCodecError:
                    record = None
            chats.append(chat_entry(index.entry(ref), index.scope(ref.campaign_id), record, names))
        return chats

    def chat_entries(self, refs) -> list[dict]:  # type: ignore[no-untyped-def]
        """``refs`` as the ``chats`` op lists them, in the order given; a ref the library does not know is left out.
        For the app's ``play.options`` (111), whose Continue list shows the chats 052 says a selection started."""
        view = self.store.load()
        index = self.chats.navigation()
        known = set(index.all_chats())
        found = [ref for ref in refs if ref in known]
        return self._entries(view, index, found, self._mention_names(view, [index.entry(ref) for ref in found]))

    def _chat(self, args: Mapping[str, object]) -> ChatRef:
        return parse_ref(ChatRef.parse, args, "chat")

    def _conflicts(self, campaign: str) -> tuple[list[dict], bool]:
        """The campaign's files that a sync left in a keep-both conflict on this device, each with the etag of
        this device's version (``mine``; ``None``: deleted here) and the etags of every other version kept here
        (``theirs``: empty for a peer's deletion, or when no kept version can be read), as ``resolve`` takes them."""
        try:
            found, complete = open_conflicts(self.store, f"campaigns/{campaign}",
                                             ended=lambda peer: pairing_ended(self.store, peer))
        except (StoreError, OSError, ValueError, SyncInterrupted):
            return [], False
        conflicts = []
        for path in sorted(found):
            try:
                mine = etag((self.store.root / path).read_bytes())
            except FileNotFoundError:
                mine = None
            except OSError:
                complete, mine = False, None
            conflicts.append({"path": path, "mine": mine, "theirs": sorted({etag(data) for data in found[path]})})
        return conflicts, complete

    def _open(self, args: Mapping[str, object]) -> dict:
        return self._scene(self._chat(args))

    def _scene(self, chat: ChatRef) -> dict:
        scene = self.runner.open(chat)
        names = scene.names
        problems = [{"code": code, "detail": detail} for code, detail in scene.problems]
        label, scope, launch = None, None, None
        try:
            view = self.chats.open_chat(chat)
        except (StoreError, ChatCodecError, OSError) as error:
            problems.append({"code": "chat_unreadable", "detail": type(error).__name__})
        else:
            label = view.record.label
            launch = {"kind": view.record.launch.kind, "ref": None if view.record.launch.ref is None
                      else str(view.record.launch.ref), "premise": view.record.launch.premise}
            if view.scope is not None:
                scope = {"campaign": chat.campaign_id, "title": view.scope.title,
                         "world": None if view.scope.world is None else view.scope.world.source}
            problems += [{"code": "broken_binding", "detail": f"{item.field} {item.text}: {item.reason}"}
                         for item in view.broken]
            problems += [{"code": problem, "detail": ""} for problem in view.problems]
        cast = []
        for actor in scene.cast:
            try:
                art = art_view(self.images.effective_placements(actor))
            except (StoreError, OSError, ValueError, InvalidImage, InvalidPlacement) as error:
                art = art_view(None, type(error).__name__)
            for slot in art["slots"]:
                slot["etag"] = self._placement_etag(actor, slot["slot"])
            cast.append({"actor": str(actor), "name": names.get(actor, actor.instance_id),
                         "role": role_of(scene.controller(actor)), "persona": actor == scene.persona, "art": art})
        conflicts, complete = self._conflicts(chat.campaign_id)
        if not complete:
            problems.append({"code": "conflicts_unknown", "detail": "this device's sync records cannot be read"})
        pending = scene.pending
        waiting = [reason for reason, holds in (
            ("paused", scene.paused), ("forked", scene.turns.fork is not None),
            ("conflicted", bool(scene.turns.conflicted)), ("turns_unreadable", not scene.turns.complete),
            ("interrupted", scene.interrupted)) if holds]
        # Who controls each actor is this device's own record (003's ledger does not sync): a chat that came
        # from another device is played here only once ``continue`` seats this device's player.
        seated = (scene.persona is None or scene.controller(scene.persona) == self.player) and all(
            scene.controller(actor) is not None for actor in scene.cast)
        history = None
        if scene.imported is not None:  # the whole imported chat, not the runner's 40-line prompt window
            history = [{"speaker": speaker, "text": text} for speaker, text in self._imported(chat)]
        return {
            "chat": str(chat), "label": label, "scope": scope, "launch": launch,
            "persona": None if scene.persona is None else {"actor": str(scene.persona),
                                                           "name": names.get(scene.persona)},
            "cast": cast, "history": history,
            "turns": [turn_view(stored, names) for stored in scene.turns.turns],
            "set_aside": [set_aside_view(stored) for stored in scene.turns.set_aside],
            "fork": fork_view(scene.turns.fork),
            "state": {"waiting": waiting, "seated": seated, "open_turn": None if pending is None else pending.turn_id,
                      "keepable": [] if pending is None else
                      [take.take for take in pending.takes if take.state == "proposed" and take.shown()],
                      "unscreened": [] if pending is None else
                      [take.take for take in pending.takes if take.state == "proposed" and take.unscreened],
                      "conflicted_turns": list(scene.turns.conflicted), "conflicts": conflicts},
            "problems": problems,
        }

    # -- playing -----------------------------------------------------------------------------------------
    def _continue(self, args: Mapping[str, object]) -> dict:
        chat = self._chat(args)
        opening = field(args, "opening", str, required=False)
        started = self.quick.continue_chat(chat, self.player_handle, opening=opening)
        result = self._scene(chat)
        result["unfinished"] = started.unfinished
        return result

    def _post(self, args: Mapping[str, object]) -> dict:
        chat = self._chat(args)
        text = field(args, "text", str)
        if len(text) > MAX_POST_CHARS:
            raise ApiRefused("invalid_field", f"a post is at most {MAX_POST_CHARS} characters")
        self.runner.post(chat, self.player_handle, text)
        return self._scene(chat)

    def _generate(self, args: Mapping[str, object]) -> dict:
        chat = self._chat(args)
        director = field(args, "director", bool, required=False, default=False)
        speakers = args.get("speakers")
        if speakers is not None:
            if not isinstance(speakers, list) or not all(isinstance(item, str) for item in speakers):
                raise ApiRefused("invalid_field", "speakers must be a list of actor references")
            try:
                speakers = [InstanceRef.parse(item) for item in speakers]
            except (ValueError, TypeError):
                raise ApiRefused("invalid_field", "speakers must be a list of actor references") from None
        self.runner.generate(chat, self.player_handle, speakers=speakers, director=director)
        return self._scene(chat)

    def _keep(self, args: Mapping[str, object]) -> dict:
        chat = self._chat(args)
        take = field(args, "take", int)
        if isinstance(take, bool) or take < 1:
            raise ApiRefused("invalid_field", "take must be a take number")
        # take numbers restart on every turn: the turn the take was read on, so a late keep never keeps
        # another turn's take nobody read
        turn = field(args, "turn", str)
        # a director take that used lore no reply screen covered is kept only once the owner, who directs,
        # says so (035: ``unscreened_lore`` otherwise)
        acknowledge = field(args, "acknowledge_unscreened", bool, required=False, default=False)
        self.runner.keep(chat, self.player_handle, take, acknowledge_unscreened=acknowledge, turn=turn)
        return self._scene(chat)

    def _pass(self, args: Mapping[str, object]) -> dict:
        chat = self._chat(args)
        turn = field(args, "turn", str)  # the turn meant: a late pass never closes a newer one
        self.runner.pass_turn(chat, self.player_handle, turn=turn)
        return self._scene(chat)

    def _pause(self, args: Mapping[str, object]) -> dict:
        chat = self._chat(args)
        self.runner.request(chat, self.player_handle, "pause")
        return self._scene(chat)

    def _resume(self, args: Mapping[str, object]) -> dict:
        chat = self._chat(args)
        self.runner.resume(chat, self.player_handle)
        return self._scene(chat)

    def _choose(self, args: Mapping[str, object]) -> dict:
        chat = self._chat(args)
        turn = field(args, "turn", str)
        self.runner.choose(chat, self.owner_handle, turn)
        return self._scene(chat)

    # -- pictures ----------------------------------------------------------------------------------------
    def _placement_etag(self, actor: InstanceRef, slot: str) -> str | None:
        """The etag of the campaign layer's placement of ``slot`` (``None``: none yet), for ``add_art``'s
        ``expected``: replacing a picture the campaign already placed needs the slot as the client read it."""
        try:
            _, found = self.images.read_placement(PlacementRef(actor, slot))
        except InvalidPlacement as error:
            return getattr(error, "etag", None)
        except (StoreError, OSError, ValueError, KeyError):
            return None
        return found

    def _add_art(self, args: Mapping[str, object]) -> dict:
        """A new campaign picture for one of the chat's cast, placed ``set`` in one slot of that actor, in one
        038 batch with compare-and-swap on the slot. The image id is drawn fresh, so two devices adding
        pictures offline never write one image object with two contents."""
        chat = self._chat(args)
        actor = parse_ref(InstanceRef.parse, args, "actor")
        slot = field(args, "slot", str, required=False, default=DEFAULT_SLOT)
        media = field(args, "media", str)
        if len(media) > (MAX_ART_BYTES // 3 + 1) * 4 + 4:
            raise ApiRefused("too_large", "a picture is at most 32 MiB")
        try:
            data = base64.b64decode(media, validate=True)
        except (binascii.Error, ValueError):
            raise ApiRefused("invalid_field", "media must be base64") from None
        if not data:
            raise ApiRefused("empty", "the picture has no bytes")
        if len(data) > MAX_ART_BYTES:
            raise ApiRefused("too_large", "a picture is at most 32 MiB")
        scene = self.runner.open(chat)
        if actor not in scene.cast or actor.campaign_id != chat.campaign_id:
            raise ApiRefused("no_actor", f"{actor} is not in this chat's cast")
        role = "avatar" if slot == "avatar" else "portrait" if slot == "portrait" else "gallery"
        if slot.startswith("expr."):
            raise ApiRefused("invalid_field", "an expression slot needs its outfit and name: not in this slice")
        try:
            target = PlacementRef(actor, slot)
        except ValueError as error:
            raise ApiRefused("invalid_field", f"slot: {error}") from None
        expected = field(args, "expected", str, required=False)
        if expected is not None and not is_etag(expected):
            raise ApiRefused("invalid_field", "expected must be the slot's etag as open gave it")
        conflicts, complete = self._conflicts(chat.campaign_id)
        if not complete or "/".join(placement_parts(target)) in {c["path"] for c in conflicts}:
            # a third version of a slot in conflict would leave nothing either device could resolve
            raise ApiRefused("art_conflict", f"{actor}'s {slot} is in conflict after a sync: resolve it first")
        batch = self.images.change()
        # a fresh id qualified by this device's tag (028), so two devices never write one image id
        image = batch.add_image(str(ScopeRef(chat.campaign_id)), self._new_image(f"art{device_tag(self.store)}"), data,
                                {"origin": "added in the app"})
        batch.place(actor, slot, Placement("set", role, image), expected)
        batch.commit()
        return self._scene(chat)

    # -- sync ----------------------------------------------------------------------------------------------
    def _sync(self, args: Mapping[str, object]) -> dict:
        peer = field(args, "peer", str)
        address = self.peers.get(peer)
        if address is None:
            raise ApiRefused("unknown_peer", f"no address is configured for {peer!r}")
        try:
            report = transfer(self.store, self.authority, peer, address, timeout=self.transfer_timeout)
        except NotPaired as error:
            raise ApiRefused("not_paired", str(error)) from None
        except AuthenticationFailed as error:
            raise ApiRefused(getattr(error, "code", "unauthenticated"), str(error)) from None
        except ValueError as error:
            raise ApiRefused("invalid_field", str(error)) from None
        result = report_view(report)
        self.reports[peer] = result
        return result

    def _resolve(self, args: Mapping[str, object]) -> dict:
        """End one keep-both conflict with ``peer`` (019 over 028's transport): ``keep`` is ``mine`` (this
        device's version) or ``theirs`` (the peer's version this device holds). Both devices must be
        reachable. ``mine`` and ``theirs`` name the versions the client read, as ``open`` reported them:
        anything since is refused ``changed``."""
        peer = field(args, "peer", str)
        address = self.peers.get(peer)
        if address is None:
            raise ApiRefused("unknown_peer", f"no address is configured for {peer!r}")
        path = field(args, "path", str)
        keep = field(args, "keep", str)
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "campaigns":
            raise ApiRefused("invalid_field", "path must be a campaign file that open reports in conflict")
        conflicts, _complete = self._conflicts(parts[1])
        found = next((item for item in conflicts if item["path"] == path), None)
        if found is None:
            raise ApiRefused("no_conflict", f"{path} is not in conflict here")
        # the versions the client read, as open gave them: a version that changed since is never resolved
        # unseen, so they are checked here and its ``mine`` is what the resolver expects on this device
        if "mine" not in args or "theirs" not in args:
            raise ApiRefused("invalid_field", "name the versions you read: mine and theirs as open gave them")
        seen_mine, seen_theirs = args["mine"], args["theirs"]
        if seen_mine is not None and not (isinstance(seen_mine, str) and is_etag(seen_mine)):
            raise ApiRefused("invalid_field", "mine must be an etag as open gave it, or null")
        if not isinstance(seen_theirs, list) or not all(isinstance(item, str) and is_etag(item) for item in seen_theirs):
            raise ApiRefused("invalid_field", "theirs must be the list of etags open gave")
        if seen_mine != found["mine"] or sorted(seen_theirs) != found["theirs"]:
            raise ApiRefused("changed", f"{path} changed since it was read: open the chat again")
        theirs = found["theirs"]
        if keep == "mine":
            chosen = found["mine"]
        elif keep == "theirs":
            if len(theirs) > 1:
                raise ApiRefused("invalid_selection", "several versions are kept here: name the one to keep by etag")
            chosen = theirs[0] if theirs else None
        elif keep in theirs:
            chosen = keep
        else:
            raise ApiRefused("invalid_field", "keep must be mine, theirs, or one of theirs by etag")
        # the peer's version as this device holds it: the chosen one, or the only one, or its deletion
        peer_version = chosen if keep != "mine" else (theirs[0] if len(theirs) == 1 else None)
        if keep == "mine" and len(theirs) > 1:
            raise ApiRefused("invalid_selection", "several versions are kept here: resolve on the other device")
        try:
            local = device_of(self.store)
        except (SyncError, StoreError, OSError, ValueError) as error:
            raise ApiRefused("device_identity", str(error)) from None
        expected = {local: seen_mine, peer: peer_version}
        try:
            report = resolve_conflict(self.store, self.authority, peer, address, path, chosen, expected,
                                      timeout=self.transfer_timeout)
        except NotPaired as error:
            raise ApiRefused("not_paired", str(error)) from None
        except AuthenticationFailed as error:
            raise ApiRefused(getattr(error, "code", "unauthenticated"), str(error)) from None
        except ValueError as error:
            raise ApiRefused("invalid_field", str(error)) from None
        return {"peer": report.peer, "path": report.path, "status": report.status, "reason": report.reason,
                "code": None if report.reason is None else report.reason.split(":", 1)[0],
                "receiver": report.receiver}


__all__ = ["ADJUDICATOR", "DEFAULT_SLOT", "MAX_ART_BYTES", "MAX_POST_CHARS", "TextApi", "field", "parse_ref", "refusal"]
