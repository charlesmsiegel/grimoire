"""The media and sync adapter (114): the gallery, picture editing, device status, pairing, sync, forks and
keep-both conflicts, as JSON ops for the graphical app.

``MediaSyncApi`` is trusted application code over the same open library as 044's ``TextApi``, acting for the
same owner. It keeps no record of its own and writes only through the services that own each record:

- 038's ``ImageLibrary`` (pictures and placements; every placement write is compare-and-swap on the slot)
- 028's ``pair`` (a pairing) and 019's ``identify`` (this device's name, once)
- 044's ``TextApi`` ``sync``, ``resolve`` and ``choose`` (a transfer, a keep-both resolution, a fork choice)

It shares the text API's lock, so a picture written here never interleaves with a turn played there: one
library, one writer at a time. It never substitutes one picture for another and never settles a conflict by
time: a keep-both conflict or a fork waits for the owner, and every version not kept is reported back by etag
with what it held (``not_kept``, ``set_aside``). See docs/MEDIA_SYNC_UI.md.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace

from grimoire.chats.codec import ChatCodecError
from grimoire.core.identity import ChatRef, ImageRef, InstanceRef, PlacementRef, ScopeRef, SourceRef, TurnRef
from grimoire.devicetag import DeviceIdentityUnusable, device_tag
from grimoire.images import (
    Crop,
    Focus,
    ImageModelError,
    Placement,
    placement_from_payload,
    slot_expression,
    slot_role,
)
from grimoire.imagestore import ART_PIN, InvalidBatch, InvalidImage, InvalidPlacement, pinned_base
from grimoire.peersync import PairingError, new_pairing_key, pair, read_pairing
from grimoire.peersync.pairing import KEY_BYTES, pairing_ended
from grimoire.peersync.wire import is_loopback
from grimoire.scene import SceneRefused
from grimoire.scene.codec import turn_from_payload
from grimoire.scenestore.library import StoredTurn
from grimoire.store import BlobCorrupt, StaleWrite, StoreError, etag
from grimoire.store.jsonfmt import is_etag
from grimoire.store.layout import classify, placement_parts
from grimoire.store.replica import parse_exact
from grimoire.sync import NoDeviceIdentity, SyncError, SyncInterrupted, device_of, identify
from grimoire.sync.documents import check_device_id
from grimoire.sync.turns import open_conflicts
from grimoire.textapi import ApiRefused, TextApi
from grimoire.textapi.views import fork_view, set_aside_view, turn_view

from .views import image_view, placement_view, report_problems, slot_view, subject_ref

#: 038's own cap on one picture's bytes (W1-W2).
MAX_ART_BYTES = 32 * 1024 * 1024
#: 018's cap on provenance text.
MAX_NOTE_CHARS = 2000
#: The most pictures one gallery page lists, and the default.
MAX_GALLERY = 1000
GALLERY_PAGE = 200
#: The most chats ``sync.forks`` opens, and the default.
MAX_FORK_CHATS = 1000
FORK_CHATS = 50
#: The placement modes a person may write here. ``unresolved`` is an importer's record of a missing picture,
#: never something the app writes: a person adds the picture instead.
EDIT_MODES = ("set", "adjust", "hide", "inherit")
#: What the app's sync can and cannot do, said plainly wherever devices are shown (028, 044).
TRANSPORT = {
    "loopback_only": True,
    "encrypted": False,
    "background": False,
    "note": ("Sync runs only when you press Sync, over a loopback address forwarded to the other device "
             "(adb reverse or an SSH tunnel). It is authenticated by the pairing key but not encrypted."),
}
#: Device actions the app does not offer yet, each with what it waits on.
UNAVAILABLE = (
    {"action": "unpair", "reason": "Ending a pairing drops the conflicts kept with that device; the app does not "
                                   "offer it yet."},
    {"action": "background_sync", "reason": "Waits on an encrypted transport (028): sync runs only when asked."},
    {"action": "resolve_source_conflict", "reason": "Only a campaign's files can be resolved in the app (044); a "
                                                    "source's conflict is reported by Sync and waits."},
)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _field(args: Mapping[str, object], key: str, kind: type, *, required: bool = True, default=None):  # type: ignore[no-untyped-def]
    value = args.get(key, default)
    if value is None and not required:
        return default
    if type(value) is not kind:
        raise ApiRefused("invalid_field", f"{key} must be a {kind.__name__}")
    return value


def _limit(args: Mapping[str, object], default: int, most: int) -> int:
    value = _field(args, "limit", int, required=False, default=default)
    if isinstance(value, bool) or not 1 <= value <= most:
        raise ApiRefused("invalid_field", f"limit must be 1 to {most}")
    return value


def _refused(error: Exception) -> ApiRefused:
    reason = getattr(error, "reason", None)
    return ApiRefused(reason if isinstance(reason, str) and reason else type(error).__name__, str(error))


def _scope(args: Mapping[str, object]) -> str:
    """An image scope: a source id, or ``campaign:<id>``. Checked by parsing an image ref in it."""
    text = _field(args, "scope", str)
    try:
        ImageRef(text, "x")
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", "scope must be a source id or campaign:<id>") from None
    return text


def _subject(args: Mapping[str, object]) -> SourceRef | InstanceRef:
    text = _field(args, "subject", str)
    try:
        return subject_ref(text)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", "subject must be an unpinned definition or a campaign instance") from None


def _media(args: Mapping[str, object]) -> bytes:
    media = _field(args, "media", str)
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
    return data


def _text(args: Mapping[str, object], key: str) -> str | None:
    value = _field(args, key, str, required=False)
    if value is not None and len(value) > MAX_NOTE_CHARS:
        raise ApiRefused("invalid_field", f"{key} is at most {MAX_NOTE_CHARS} characters")
    return value or None


def _basis(args: Mapping[str, object], key: str, kind: type, names: tuple[str, ...]):  # type: ignore[no-untyped-def]
    """A ``crop`` or ``focus`` in integer basis points (018), or ``None`` when not given."""
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise ApiRefused("invalid_field", f"{key} must be an object with exactly {', '.join(names)}")
    try:
        return kind(*(value[name] for name in names))
    except ImageModelError as error:
        raise ApiRefused("invalid_field", f"{key}: {error}") from None


def _expected(args: Mapping[str, object]) -> str | None:
    """The slot's etag as the client read it (``None``: it held no placement of this subject's own). It must
    be given, so a write never lands on a slot nobody looked at."""
    if "expected" not in args:
        raise ApiRefused("invalid_field", "name the slot as you read it: expected (its etag, or null)")
    value = args["expected"]
    if value is not None and not (isinstance(value, str) and is_etag(value)):
        raise ApiRefused("invalid_field", "expected must be the slot's etag as media.art gave it, or null")
    return value


class MediaSyncApi:
    """The media and sync ops over the library ``text`` serves, for the same owner. ``new_id`` draws a new
    image id from a prefix (default: random), qualified by this device's tag as 044's ``add_art`` does, so two
    devices adding pictures offline never write one image id with two contents."""

    def __init__(self, text: TextApi, *, new_id: Callable[[str], str] = _new_id) -> None:
        self.text = text
        self.store = text.store
        self.authority = text.authority
        self.images = text.images
        self.owner = text.owner
        self._new_id = new_id
        # the text API's own lock (reentrant): one writer at a time across both, and its ``sync``, ``resolve``
        # and ``choose`` are called under it
        self._lock = text._lock  # noqa: SLF001 -- the one library's writer lock, shared on purpose
        self._ops: dict[str, Callable[[Mapping[str, object]], dict]] = {
            "media.scopes": self._scopes, "media.gallery": self._gallery, "media.art": self._art,
            "media.upload": self._upload, "media.place": self._place,
            "sync.devices": self._devices, "sync.identify": self._identify, "sync.pair": self._pair,
            "sync.run": self._sync, "sync.conflicts": self._conflict_list, "sync.resolve": self._resolve,
            "sync.forks": self._forks, "sync.choose": self._choose,
        }

    @property
    def ops(self) -> tuple[str, ...]:
        return tuple(sorted(self._ops))

    def call(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        """Run one op. Raises ``ApiRefused``; any other exception is a bug the caller reports as such."""
        handler = self._ops.get(op) if isinstance(op, str) else None
        if handler is None:
            raise ApiRefused("unknown_op", f"no op {op!r}")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ApiRefused("invalid_field", "the arguments must be a JSON object")
        with self._lock:
            try:
                return handler(args)
            except ApiRefused:
                raise
            except (InvalidImage, InvalidPlacement, InvalidBatch, SceneRefused) as error:
                raise _refused(error) from None
            except ImageModelError as error:
                raise ApiRefused("invalid_field", str(error)) from None
            except StaleWrite as error:
                raise ApiRefused("changed", f"the library changed meanwhile; nothing was written ({error})") from None
            except BlobCorrupt as error:
                raise ApiRefused("blob_corrupt", str(error)) from None
            except (DeviceIdentityUnusable, SyncError) as error:
                raise ApiRefused("device_identity", str(error)) from None
            except StoreError as error:  # any other refusal of the store: nothing was written
                raise ApiRefused(type(error).__name__, str(error)) from None

    # -- the gallery ---------------------------------------------------------------------------------------
    def _campaigns(self, view) -> list[str]:  # type: ignore[no-untyped-def]
        found = {ref.campaign_id for ref in view.scopes} | {ref.campaign_id for ref in view.instances} \
            | {ref.campaign_id for ref in view.chats}
        return sorted(found)

    def _scopes(self, args: Mapping[str, object]) -> dict:
        """Every place a picture can belong: each admitted source (by its title), each source the library leaves
        out (``excluded``: it shows no art, 038 fails closed) and each campaign."""
        view = self.store.load()
        index = self.text.chats.navigation()
        sources = [{"scope": source_id, "title": stored.manifest.title, "state": "admitted"}
                   for source_id, stored in sorted(view.sources.items())]
        sources += [{"scope": source_id, "title": None, "state": "excluded"}
                    for source_id in sorted(set(view.excluded) - set(view.sources))]
        campaigns = []
        for campaign in self._campaigns(view):
            entry = index.scope(campaign)
            campaigns.append({"scope": str(ScopeRef(campaign)), "campaign": campaign,
                              "title": None if entry is None else entry.title,
                              "world": None if entry is None else entry.world})
        return {"sources": sources, "campaigns": campaigns}

    def _name(self, view, subject: SourceRef | InstanceRef) -> str:  # type: ignore[no-untyped-def]
        """A subject's display name: a definition's head name, an instance's own name or its derivation's."""
        try:
            if isinstance(subject, SourceRef):
                return str(view.index.resolve(subject).name)
            payload = view.instances[subject].envelope.payload
            name = payload.get("name") if isinstance(payload, Mapping) else None
            if isinstance(name, str) and name:
                return name
            for key in ("derived_from", "base", "source"):
                ref = payload.get(key) if isinstance(payload, Mapping) else None
                if isinstance(ref, str):
                    return str(view.index.resolve(SourceRef.parse(ref).unpinned()).name)
        except Exception:  # noqa: BLE001 -- a name is presentation only; a subject without one shows its id
            pass
        return subject.entity_id if isinstance(subject, SourceRef) else subject.instance_id

    def _scope_subjects(self, scope: str) -> tuple[list[SourceRef | InstanceRef], list[dict]]:
        """The subjects whose own placements may name ``scope``'s pictures, and listing problems."""
        if scope.startswith("campaign:"):
            campaign = ScopeRef.parse(scope).campaign_id
            placed, complete = self.store.campaign_placement_subjects(campaign)
            subjects = set(placed) | set(self.store.campaign_instances(campaign))
            problems = [] if complete else [{"path": f"campaigns/{campaign}", "code": "unlisted",
                                             "detail": "some placement folders could not be listed"}]
            return sorted(subjects, key=str), problems
        entities = self.store.source_entities(scope)
        return [SourceRef(scope, kind, entity) for kind, entity in sorted(entities)], []

    def _own_placements(self, subject: SourceRef | InstanceRef) -> tuple[list[dict], list[dict]]:
        """What ``subject`` itself placed, slot by slot, as written (not as resolved), each with its etag;
        an unreadable placement shows its reason (and its etag when it can be replaced)."""
        refs, problems = self.store.list_placements(subject)
        own = []
        for ref in refs:
            try:
                placement, tag = self.images.read_placement(ref)
            except InvalidPlacement as error:
                own.append({"slot": ref.slot, "state": "unreadable", "reason": error.reason,
                            "etag": getattr(error, "etag", None)})
                continue
            except (StoreError, OSError, ValueError) as error:
                own.append({"slot": ref.slot, "state": "unreadable", "reason": type(error).__name__, "etag": None})
                continue
            own.append({"slot": ref.slot, "state": "placed", "etag": tag, **placement_view(placement)})
        return own, report_problems(problems)

    def _gallery(self, args: Mapping[str, object]) -> dict:
        """One scope's pictures, each with its blob, provenance and the placements of the scope's own subjects
        that name it, and those subjects (for the art editor). An image that cannot be read shows why."""
        scope = _scope(args)
        limit = _limit(args, GALLERY_PAGE, MAX_GALLERY)
        with self.store.read_pass():
            view = self.store.load()
            if not scope.startswith("campaign:") and scope not in view.sources:
                raise ApiRefused("excluded_source" if scope in view.excluded else "missing_source",
                                 f"{scope} is not an admitted source: it shows no pictures")
            refs, problems = self.store.list_images(scope)
            subjects, more_problems = self._scope_subjects(scope)
            used: dict[str, list[dict]] = {}
            for subject in subjects:
                own, _ = self._own_placements(subject)
                for entry in own:
                    if entry.get("image") is not None:
                        used.setdefault(entry["image"], []).append(
                            {"subject": str(subject), "slot": entry["slot"], "mode": entry["mode"]})
            images = []
            for ref in refs[:limit]:
                try:
                    image, tag = self.images.read_image(ref)
                except InvalidImage as error:
                    images.append(image_view(ref, None, None, error.reason, used.get(str(ref), [])))
                    continue
                problem = None
                try:
                    present = self.store.blob_stat(image.blob.sha256, image.blob.ext)
                except (StoreError, OSError, ValueError):
                    present = ("unreadable", None)
                if present[0] != "present":
                    problem = "missing_blob"
                elif present[1] != image.blob.size:
                    problem = "blob_size_mismatch"
                images.append(image_view(ref, image, tag, problem, used.get(str(ref), [])))
            return {"scope": scope, "images": images, "more": len(refs) > limit,
                    "subjects": [{"subject": str(subject), "name": self._name(view, subject)} for subject in subjects],
                    "problems": report_problems(problems) + more_problems}

    def _slot_conflicts(self, subject: SourceRef | InstanceRef) -> tuple[set[str], bool]:
        """The slots of an instance a sync left in a keep-both conflict here, and whether that is known. A
        definition's placements are a source's files, which the app cannot see in conflict (``complete``)."""
        if isinstance(subject, SourceRef):
            return set(), True
        try:
            found, complete = open_conflicts(self.store, f"campaigns/{subject.campaign_id}",
                                             ended=lambda peer: pairing_ended(self.store, peer))
        except (StoreError, OSError, ValueError, SyncInterrupted):
            return set(), False
        prefix = "/".join(placement_parts(PlacementRef(subject, "x"))[:-1]) + "/"
        return {path[len(prefix):].removesuffix(".json") for path in found if path.startswith(prefix)}, complete

    def _art(self, args: Mapping[str, object]) -> dict:
        return self._art_of(_subject(args))

    def _art_of(self, subject: SourceRef | InstanceRef) -> dict:
        """What ``subject`` shows in every slot, who decided it, what it placed itself (with each slot's etag for
        a compare-and-swap edit), which modes and image scopes an edit may use here, and slots in conflict."""
        with self.store.read_pass():
            view = self.store.load()
            resolution = self.images.effective_placements(subject)
            own, own_problems = self._own_placements(subject)
            conflicts, complete = self._slot_conflicts(subject)
        lineage = [str(ref) for ref in resolution.lineage]
        inherits = None
        if isinstance(subject, InstanceRef):
            inherits = self._inherits(view, subject)
            reusable = [str(ScopeRef(subject.campaign_id))]
            if len(resolution.lineage) >= 2 and isinstance(resolution.lineage[-2], SourceRef):
                reusable.append(resolution.lineage[-2].source)  # the instance's derivation source (W7)
            modes = list(EDIT_MODES)
        else:
            reusable = [subject.source]
            modes = list(EDIT_MODES) if len(resolution.lineage) >= 2 else ["set"]  # only a portrayal inherits
        problems = report_problems(resolution.problems) + own_problems
        if not complete:
            problems.append({"path": f"campaigns/{subject.campaign_id}", "code": "conflicts_unknown",  # type: ignore[union-attr]
                             "detail": "this device's sync records cannot be read: no slot is editable"})
        return {"subject": str(subject), "name": self._name(view, subject), "lineage": lineage,
                "inherits": inherits, "slots": [slot_view(slot, resolution) for slot in resolution.slots],
                "own": own,
                "modes": modes, "reusable": reusable, "conflicts": sorted(conflicts), "editable": complete,
                "problems": problems}

    @staticmethod
    def _inherits(view, subject: InstanceRef) -> dict | None:  # type: ignore[no-untyped-def]
        """Whether an instance's inherited art is the snapshot pinned with its revision (046: a picture added
        to the definition later shows only after a reviewed rebase) or follows its definition live."""
        record = view.instances.get(subject)
        if record is None:
            return None
        envelope = record.envelope
        pinned = envelope.base is not None and pinned_base(envelope.extra.get(ART_PIN)) == envelope.base
        return {"follows": "pinned" if pinned else "live", "revision": envelope.base}

    # -- writing pictures --------------------------------------------------------------------------------
    def _check_slot(self, subject: SourceRef | InstanceRef, slot: str) -> PlacementRef:
        if slot_role(slot) is None:
            raise ApiRefused("invalid_field", "an expression slot is expr.<outfit>.<name>")
        try:
            target = PlacementRef(subject, slot)
        except ValueError as error:
            raise ApiRefused("invalid_field", f"slot: {error}") from None
        conflicts, complete = self._slot_conflicts(subject)
        if not complete or slot in conflicts:
            # a third version of a slot in conflict would leave neither device a version to resolve against
            raise ApiRefused("art_conflict", f"{subject}'s {slot} is in conflict after a sync: resolve it first")
        return target

    def _placement(self, target: PlacementRef, mode: str, image: ImageRef | None, crop: Crop | None,
                   focus: Focus | None, expected: str | None) -> Placement:
        """The placement to write: the subject's own one rewritten (so its unknown fields and provenance
        survive, docs/IMAGES.md "Rewriting") when it has one, else a new one recorded as placed in the app."""
        role = None if mode == "inherit" else slot_role(target.slot)
        expression = slot_expression(target.slot) if mode in ("set", "adjust") else None
        fields = {"mode": mode, "role": role, "image": image, "crop": crop, "focus": focus, "expression": expression}
        if expected is not None:
            try:
                existing, tag = self.images.read_placement(target)
            except InvalidPlacement as error:
                if error.reason != "invalid_placement" or getattr(error, "etag", None) != expected:
                    raise ApiRefused("changed", f"{target.slot} changed since it was read: open it again") from None
            else:
                if tag != expected:
                    raise ApiRefused("changed", f"{target.slot} changed since it was read: open it again")
                if existing.mode == mode:
                    return replace(existing, **fields)
                # a new kind of placement: its old ``note`` (an importer's reason a picture is missing, say)
                # no longer describes it, but where it came from (``legacy_id``, kept keys) still does
                kept = {key: value for key, value in (existing.provenance or {}).items() if key != "note"}
                return replace(existing, **fields, provenance={**kept, "origin": "placed in the app"})
        return Placement(**fields, provenance={"origin": "placed in the app"})

    def _place(self, args: Mapping[str, object]) -> dict:
        """Write one slot of one subject: ``set`` a picture of a reusable scope (an upload or one already
        there), ``adjust`` the inherited picture's ``crop``/``focus``, ``hide`` the slot, or ``inherit`` (reset
        a portrayal's or an instance's own placement). 038 refuses what the subject may not place, and the
        write is compare-and-swap on ``expected``. Returns the subject's art as it now reads."""
        subject = _subject(args)
        slot = _field(args, "slot", str)
        mode = _field(args, "mode", str)
        if mode not in EDIT_MODES:
            raise ApiRefused("invalid_field", f"mode must be one of {', '.join(EDIT_MODES)}")
        image = None
        if mode in ("set", "adjust"):
            try:
                image = ImageRef.parse(_field(args, "image", str))
            except (ValueError, TypeError):
                raise ApiRefused("invalid_field", "image must be an image reference") from None
        elif args.get("image") is not None:
            raise ApiRefused("invalid_field", f"a {mode} names no image")
        crop = _basis(args, "crop", Crop, ("x", "y", "w", "h"))
        focus = _basis(args, "focus", Focus, ("x", "y"))
        if mode in ("hide", "inherit") and (crop is not None or focus is not None):
            raise ApiRefused("invalid_field", f"a {mode} has no crop or focus")
        expected = _expected(args)
        target = self._check_slot(subject, slot)
        placement = self._placement(target, mode, image, crop, focus, expected)
        batch = self.images.change()
        batch.place(subject, slot, placement, expected)
        batch.commit()
        return self._art_of(subject)

    def _upload(self, args: Mapping[str, object]) -> dict:
        """A new picture in ``scope`` with its ``note`` (a description) and ``origin``, drawn a fresh id. With
        ``subject`` and ``slot`` (and ``expected``, the slot as read), it is also ``set`` there in the same 038
        batch, so a refused placement leaves no picture behind. Returns the image, and the subject's art."""
        scope = _scope(args)
        data = _media(args)
        note, origin = _text(args, "note"), _text(args, "origin")
        provenance = {"origin": origin or "added in the app"}
        if note is not None:
            provenance["note"] = note
        subject = None
        if args.get("subject") is not None:
            subject = _subject(args)
            slot = _field(args, "slot", str)
            expected = _expected(args)
            crop = _basis(args, "crop", Crop, ("x", "y", "w", "h"))
            focus = _basis(args, "focus", Focus, ("x", "y"))
            target = self._check_slot(subject, slot)
        image_id = self._new_id(f"art{device_tag(self.store)}")
        batch = self.images.change()
        ref = batch.add_image(scope, image_id, data, provenance)
        if subject is not None:
            batch.place(subject, slot, self._placement(target, "set", ref, crop, focus, expected), expected)
        batch.commit()
        image, tag = self.images.read_image(ref)
        result = {"image": image_view(ref, image, tag, None, [])}
        if subject is not None:
            result["art"] = self._art_of(subject)
        return result

    # -- devices -----------------------------------------------------------------------------------------
    def _device(self) -> tuple[str | None, str | None]:
        try:
            return device_of(self.store), None
        except NoDeviceIdentity:
            return None, "unnamed"
        except (SyncError, SyncInterrupted, StoreError, OSError, ValueError) as error:
            return None, f"unreadable: {type(error).__name__}"

    def _devices(self, args: Mapping[str, object]) -> dict:
        """This device's name, every configured peer (its address, whether it is paired, its last sync), what
        the transport can and cannot do, and the device actions the app does not offer yet."""
        device, problem = self._device()
        peers = []
        for peer, (host, port) in sorted(self.text.peers.items()):
            try:
                paired = read_pairing(self.store, peer) is not None
            except (StoreError, OSError, ValueError):
                paired = False
            peers.append({"peer": peer, "address": f"{host}:{port}", "paired": paired,
                          "last": self.text.reports.get(peer)})
        return {"device": device, "device_problem": problem, "owner": self.owner, "peers": peers,
                "transport": dict(TRANSPORT), "unavailable": [dict(item) for item in UNAVAILABLE]}

    def _identify(self, args: Mapping[str, object]) -> dict:
        """Name this device, once (019): a library that already has a name keeps it."""
        device = _field(args, "device", str)
        try:
            check_device_id(device)
        except ValueError as error:
            raise ApiRefused("invalid_field", f"device: {error}") from None
        current, problem = self._device()
        if current is not None or problem != "unnamed":
            raise ApiRefused("device_identity", "this device already has a name, or its name cannot be read")
        identify(self.store, device)
        return self._devices({})

    def _pair(self, args: Mapping[str, object]) -> dict:
        """Pair with ``peer`` (028): with ``key`` (the hex the other device showed), or with a new key, which
        is returned once to type on the other device. Pairing a device already paired replaces its key and
        ends the old one's access, so it needs ``replace: true``. ``address`` (a loopback ``host:port``) is
        where ``sync.run`` dials it while this app runs; the app's ``--peer`` keeps it across restarts."""
        peer = _field(args, "peer", str)
        try:
            check_device_id(peer)
        except ValueError as error:
            raise ApiRefused("invalid_field", f"peer: {error}") from None
        given = _field(args, "key", str, required=False)
        if given is None:
            key = new_pairing_key()
        else:
            try:
                key = bytes.fromhex(given)
            except ValueError:
                raise ApiRefused("invalid_field", "key must be the hex key the other device showed") from None
            if len(key) != KEY_BYTES:
                raise ApiRefused("invalid_field", f"key must be {KEY_BYTES * 2} hex digits")
        address = None
        if args.get("address") is not None:
            text = _field(args, "address", str)
            host, colon, port = text.rpartition(":")
            host = host.strip("[]")
            if not colon or not port.isascii() or not port.isdigit() or not 0 < int(port) <= 65535 \
                    or not is_loopback(host):
                raise ApiRefused("invalid_field", "address must be a loopback host:port")
            address = (host, int(port))
        replace_key = _field(args, "replace", bool, required=False, default=False)
        device, problem = self._device()
        if device is None:
            raise ApiRefused("device_identity", "name this device before pairing it")
        if read_pairing(self.store, peer) is not None and not replace_key:
            raise ApiRefused("already_paired", f"{peer} is already paired: pairing again ends its current key")
        try:
            pair(self.store, self.authority, peer, key, granted_by=self.owner)
        except PairingError as error:
            raise ApiRefused("pairing_refused", str(error)) from None
        if address is not None:
            self.text.peers[peer] = address
        return {"peer": peer, "device": device, "paired": True, "key": None if given is not None else key.hex(),
                "address": None if address is None else f"{address[0]}:{address[1]}"}

    # -- sync --------------------------------------------------------------------------------------------
    def _sync(self, args: Mapping[str, object]) -> dict:
        """One 028 transfer with ``peer`` (044's ``sync``). An unreachable peer is ``incomplete``: nothing
        here is deleted, and the device plays on offline."""
        return self.text.call("sync", {"peer": _field(args, "peer", str)})

    def _names(self, chat: ChatRef) -> Mapping[InstanceRef, str]:
        try:
            return self.text.runner.open(chat).names
        except (SceneRefused, StoreError, ChatCodecError, OSError, ValueError):
            return {}

    def _candidate(self, path: str, data: bytes | None) -> dict:
        """One version of a file in conflict: its etag and what it holds, shown only as the table may read it.
        A turn shows as 035's table view (never intentions, briefs or unshown lines); a placement shows its
        mode and picture; any other record shows its kind only."""
        if data is None:
            return {"etag": None, "deleted": True, "preview": None}
        parts = tuple(path.split("/"))
        found = classify(parts)
        kind = None if found is None else found[0]
        preview: dict = {"kind": kind or "other"}
        try:
            envelope = parse_exact(parts, data)
            if kind == "turn" and isinstance(found[1], TurnRef):  # type: ignore[index]
                ref: TurnRef = found[1]  # type: ignore[index]
                record = turn_from_payload(ref, envelope.schema, envelope.payload)  # type: ignore[union-attr]
                chat = ChatRef(ref.campaign_id, ref.turn_id.rsplit(".", 1)[0])
                preview["turn"] = turn_view(StoredTurn(record, etag(data)), self._names(chat))
                preview["chat"] = str(chat)
            elif kind == "placement":
                preview["placement"] = placement_view(placement_from_payload(envelope.payload))  # type: ignore[union-attr]
        except Exception as error:  # noqa: BLE001 -- a version this build cannot read is still a version
            preview["unreadable"] = type(error).__name__
        return {"etag": etag(data), "deleted": False, "size": len(data), "preview": preview}

    def _campaign_conflicts(self, campaign: str) -> tuple[list[dict], bool]:
        try:
            found, complete = open_conflicts(self.store, f"campaigns/{campaign}",
                                             ended=lambda peer: pairing_ended(self.store, peer))
        except (StoreError, OSError, ValueError, SyncInterrupted):
            return [], False
        conflicts = []
        for path in sorted(found):
            try:
                mine = (self.store.root / path).read_bytes()
            except FileNotFoundError:
                mine = None
            except OSError:
                complete, mine = False, None
            theirs = {etag(data): data for data in found[path]}
            parsed = classify(tuple(path.split("/")))
            entry = {"path": path, "campaign": campaign, "kind": "other" if parsed is None else parsed[0],
                     "mine": self._candidate(path, mine),
                     "theirs": [self._candidate(path, theirs[tag]) for tag in sorted(theirs)]}
            if parsed is not None and parsed[0] == "placement":
                entry["subject"], entry["slot"] = str(parsed[1].subject), parsed[1].slot
            conflicts.append(entry)
        return conflicts, complete

    def _conflict_list(self, args: Mapping[str, object]) -> dict:
        """Every campaign file a sync left in a keep-both conflict on this device, each with this device's
        version (``mine``) and every other version kept here (``theirs``), shown side by side, and the campaigns
        whose sync records cannot be read (``unknown``: any of their files may be in conflict)."""
        view = self.store.load()
        conflicts, unknown = [], []
        for campaign in self._campaigns(view):
            found, complete = self._campaign_conflicts(campaign)
            conflicts += found
            if not complete:
                unknown.append(campaign)
        return {"conflicts": conflicts, "unknown": unknown}

    def _resolve(self, args: Mapping[str, object]) -> dict:
        """End one keep-both conflict with ``peer`` (044's ``resolve``: ``keep`` is ``mine``, ``theirs`` or one
        of theirs by etag, and ``mine``/``theirs`` are the etags as read). Both devices must be reachable. The
        report says which version was ``kept`` and lists every version that was not (``not_kept``), with what
        it held, so no candidate leaves silently."""
        path = _field(args, "path", str)
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "campaigns":
            raise ApiRefused("invalid_field", "path must be a campaign file sync.conflicts reports")
        before, _complete = self._campaign_conflicts(parts[1])
        found = next((item for item in before if item["path"] == path), None)
        report = self.text.call("resolve", {key: args.get(key) for key in ("peer", "path", "keep", "mine", "theirs")
                                            if key in args})
        if found is None or report["status"] != "resolved":
            return {**report, "kept": None, "not_kept": []}
        keep = args.get("keep")
        candidates = [{**found["mine"], "holder": "this device"}] + \
            [{**item, "holder": "another device"} for item in found["theirs"]]
        if keep == "mine":
            kept = found["mine"]["etag"]
        elif keep == "theirs":
            kept = found["theirs"][0]["etag"] if found["theirs"] else None
        else:
            kept = keep
        not_kept = [item for item in candidates if item["etag"] != kept]
        if keep == "mine" and not found["theirs"]:
            # the other version is the peer's deletion (or none is readable here): still a version not kept
            not_kept.append({"etag": None, "deleted": True, "preview": None, "holder": "another device"})
        return {**report, "kept": kept, "not_kept": not_kept}

    def _forks(self, args: Mapping[str, object]) -> dict:
        """The recent chats that forked (both devices played on offline) and wait for the owner's choice, each
        with its branches, and the turns any earlier choice set aside (heard by no one, 035)."""
        limit = _limit(args, FORK_CHATS, MAX_FORK_CHATS)
        found, problems = [], []
        for chat in self.text.chats.navigation().recent(limit):
            try:
                scene = self.text.runner.open(chat)
            except (SceneRefused, StoreError, ChatCodecError, OSError, ValueError) as error:
                problems.append({"chat": str(chat), "code": getattr(error, "reason", type(error).__name__)})
                continue
            if scene.turns.fork is None:
                continue
            found.append({"chat": str(chat), "fork": fork_view(scene.turns.fork),
                          "set_aside": [set_aside_view(stored) for stored in scene.turns.set_aside]})
        return {"forks": found, "problems": problems}

    def _choose(self, args: Mapping[str, object]) -> dict:
        """The owner's fork choice (044's ``choose``): ``turn`` is the first turn of the branch that goes on.
        The report lists every turn the choice set aside, so the branch not kept is never lost unseen."""
        chat = _field(args, "chat", str)
        scene = self.text.call("choose", {"chat": chat, "turn": _field(args, "turn", str)})
        return {"chat": scene["chat"], "fork": scene["fork"], "set_aside": scene["set_aside"],
                "waiting": scene["state"]["waiting"]}


__all__ = ["EDIT_MODES", "MAX_ART_BYTES", "MediaSyncApi", "TRANSPORT", "UNAVAILABLE"]
