"""Pictures beyond character slots: covers, lore entry pictures, scene backdrops and pictures on posts.

All of them are ordinary 018 placements (docs/IMAGES.md), so provenance, compare-and-swap, sync and keep-both
conflicts are the image model's own. This module only fixes which **slot** of which **subject** each one is,
and what the slot's name must point at before a picture may be put there:

=====================  ==========================================  ===========================================
what                   subject                                     slot
=====================  ==========================================  ===========================================
a cover                any entry (an unpinned definition), a       ``cover``
                       source as a whole, a campaign as a whole
a lore entry's picture the lorebook (an unpinned definition)       ``entry.<key>``: ``entry_slot`` of the entry
a scenario's backdrops the scenario (an unpinned definition)       ``backdrop.<name>``
a chat's backdrop      the chat's campaign as a whole              ``backdrop.<chat id>``
a picture on a post    the chat's campaign as a whole              ``post.<turn id>``
=====================  ==========================================  ===========================================

A lorebook's entries carry no id of their own (a card may repeat the one it gives), so an entry is keyed by
what it says: its keys and its text. Reordering a lorebook keeps every picture on its entry; editing an entry's
keys or text leaves its picture an **orphan**, listed and never shown on another entry. A post's picture is
keyed by the turn's id, beside the transcript like a bookmark: the turn record is never read or written for it,
no context builder reads placements, and a turn that was set aside or is gone leaves an orphan too.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from grimoire.cards import LOREBOOK_SCHEMA
from grimoire.chats.codec import ChatCodecError
from grimoire.core.identity import ChatRef, ImageRef, InstanceRef, OwnerRef, PlacementRef, SourceRef
from grimoire.images import Crop, Focus, Shown
from grimoire.imagestore import InvalidImage
from grimoire.scene import SceneRefused
from grimoire.store import StoredDefinition, StoreError
from grimoire.textapi import ApiRefused

from .views import provenance_view, slot_view, subject_ref

COVER = "cover"
ENTRY = "entry."
BACKDROP = "backdrop."
POST = "post."
#: The most subjects one ``media.shown`` reads.
MAX_SHOWN = 200
#: Provenance key of a campaign's copy of a scenario's backdrop: the image it was copied from.
COPIED_FROM = "x-copied-from"
Subject = SourceRef | InstanceRef | OwnerRef


def entry_slot(entry: object) -> str | None:
    """The slot of one lorebook entry's picture, from what the entry says (its keys and text), or ``None``
    for a value that is no entry. Two entries that say exactly the same share one picture."""
    if not isinstance(entry, Mapping):
        return None
    keys = entry.get("keys")
    content = entry.get("content")
    basis = [[key for key in keys if isinstance(key, str)] if isinstance(keys, (list, tuple)) else [],
             content if isinstance(content, str) else ""]
    text = json.dumps(basis, ensure_ascii=False, separators=(",", ":"))
    return ENTRY + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _chat_ref(text: object) -> ChatRef:
    try:
        return ChatRef.parse(text)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", "chat must be a chat reference") from None


class BeyondSlots:
    """The ops and slot rules of this module, mixed into ``MediaSyncApi`` (which supplies the library)."""

    # -- what a slot's name must point at ----------------------------------------------------------------
    def _lore_entries(self, subject: SourceRef) -> list[str | None] | None:
        """The picture slot of each entry of a lorebook's head revision, in order; ``None`` when ``subject``
        is not a readable lorebook."""
        try:
            found = self.store.check_definition(subject)  # type: ignore[attr-defined]
        except (StoreError, OSError, ValueError):
            return None
        if not isinstance(found, StoredDefinition) or found.envelope.extra.get("schema") != LOREBOOK_SCHEMA:
            return None
        payload = found.envelope.payload
        entries = payload.get("entries") if isinstance(payload, Mapping) else None
        return [entry_slot(entry) for entry in entries] if isinstance(entries, (list, tuple)) else []

    def _line(self, chat: ChatRef) -> tuple[dict[str, bool], frozenset[str]] | None:
        """The turns on the chat's line of play, each with whether it holds a post, and the turns a fork choice
        set aside (kept, heard by no one, and not on the line); ``None`` when the chat cannot be opened."""
        try:
            scene = self.text.runner.open(chat)  # type: ignore[attr-defined]
        except (SceneRefused, StoreError, ChatCodecError, OSError, ValueError):
            return None
        return ({stored.record.turn_id: stored.record.post is not None for stored in scene.turns.turns},
                frozenset(stored.record.turn_id for stored in scene.turns.set_aside))

    def _check_target(self, subject: Subject, slot: str) -> None:
        """Refuse a picture put where its slot's name points at nothing: an entry the lorebook does not hold,
        a chat that is not there, a turn that is not a post on its chat's line. A ``hide`` is never checked:
        it is how an orphan is taken off."""
        if isinstance(subject, SourceRef) and slot.startswith(ENTRY):
            entries = self._lore_entries(subject)
            if entries is not None and slot not in entries:
                raise ApiRefused("missing_entry", f"{subject} has no entry that reads as {slot} names: the entry "
                                                  "was edited or removed")
        if not isinstance(subject, OwnerRef) or subject.campaign_id is None:
            return
        campaign = subject.campaign_id
        if slot.startswith(BACKDROP):
            try:
                self.text.chats.read_chat(ChatRef(campaign, slot[len(BACKDROP):]))  # type: ignore[attr-defined]
            except (StoreError, ChatCodecError, OSError, ValueError):
                raise ApiRefused("missing_chat", f"{subject} has no chat {slot[len(BACKDROP):]}") from None
        elif slot.startswith(POST):
            turn = slot[len(POST):]
            try:
                chat = ChatRef(campaign, turn.rsplit(".", 1)[0])
            except ValueError:
                raise ApiRefused("missing_turn", f"{turn} names no turn of a chat") from None
            found = self._line(chat)
            line = {} if found is None else found[0]
            if turn not in line:
                raise ApiRefused("missing_turn", f"{turn} is not a turn of {chat} as it is played")
            if not line[turn]:
                raise ApiRefused("not_a_post", f"{turn} is not a post: a picture illustrates a person's own post")

    # -- reading -----------------------------------------------------------------------------------------
    def _slot_state(self, subject: Subject, slot: str, resolution=None, own=None) -> dict:  # type: ignore[no-untyped-def]
        """One slot of one subject as it shows (``empty`` when nothing was ever placed there), with the etag
        of the subject's own placement for a compare-and-swap edit."""
        if resolution is None:
            resolution = self.images.effective_placements(subject)  # type: ignore[attr-defined]
        found = resolution.slot(slot)
        view = {"slot": slot, "state": "empty"} if found is None else slot_view(found, resolution)
        if own is None:
            own, _ = self._own_placements(subject)  # type: ignore[attr-defined]
        mine = next((entry for entry in own if entry["slot"] == slot), None)
        view["etag"] = None if mine is None else mine.get("etag")
        return view

    def _shown(self, args: Mapping[str, object]) -> dict:
        """One slot of many subjects in one read (a grid's covers): each as it shows, or why not. A subject
        that cannot be parsed or read is answered ``unavailable``; it never fails the others."""
        slot = args.get("slot", COVER)
        subjects = args.get("subjects")
        if type(slot) is not str or not isinstance(subjects, list) or not all(type(item) is str for item in subjects):
            raise ApiRefused("invalid_field", "subjects must be a list of subject references and slot a slot name")
        if len(subjects) > MAX_SHOWN:
            raise ApiRefused("invalid_field", f"at most {MAX_SHOWN} subjects at a time")
        try:
            PlacementRef(OwnerRef("x"), slot)
        except ValueError as error:
            raise ApiRefused("invalid_field", f"slot: {error}") from None
        shown = []
        with self.store.read_pass():  # type: ignore[attr-defined]
            for text in dict.fromkeys(subjects):
                try:
                    view = self._slot_state(subject_ref(text), slot)
                except (ValueError, TypeError):
                    view = {"slot": slot, "state": "unavailable", "reason": "invalid_subject"}
                except (StoreError, OSError) as error:
                    view = {"slot": slot, "state": "unavailable", "reason": type(error).__name__}
                shown.append({"subject": text, **view})
        return {"slot": slot, "shown": shown}

    def _entries(self, args: Mapping[str, object]) -> dict:
        """A lorebook's entries, each with the slot its picture goes in and what shows there; the lorebook's
        art (for the picker); and the entry pictures no entry of the head reads as any more (``orphans``)."""
        text = args.get("subject")
        try:
            subject = subject_ref(text)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            subject = None
        if not isinstance(subject, SourceRef):
            raise ApiRefused("invalid_field", "subject must be a lorebook: an unpinned definition")
        with self.store.read_pass():  # type: ignore[attr-defined]
            slots = self._lore_entries(subject)
            if slots is None:
                raise ApiRefused("not_a_lorebook", f"{subject} is not a readable lorebook")
            art = self._art_of(subject)  # type: ignore[attr-defined]
        shown = {slot["slot"]: slot for slot in art["slots"]}
        etags = {entry["slot"]: entry.get("etag") for entry in art["own"]}

        def state(slot: str) -> dict:
            return {**shown.get(slot, {"slot": slot, "state": "empty"}), "etag": etags.get(slot)}
        entries = [{"index": index, "slot": slot, "picture": None if slot is None else state(slot)}
                   for index, slot in enumerate(slots)]
        orphans = [state(slot) for slot in sorted(set(shown) | set(etags))
                   if slot.startswith(ENTRY) and slot not in slots and state(slot)["state"] != "hidden"]
        return {"subject": str(subject), "entries": entries, "orphans": orphans, "art": art}

    def _offered(self, chat: ChatRef) -> list[dict]:
        """The backdrops of the scenarios this chat is played from: pictures the chat may take as its own."""
        try:
            record, _tag = self.text.chats.read_chat(chat)  # type: ignore[attr-defined]
        except (StoreError, ChatCodecError, OSError, ValueError):
            return []
        scenarios = [binding.ref.unpinned() for binding in record.bindings
                     if binding.role == "scenario" and binding.detached_seq is None]
        offered = []
        for scenario in dict.fromkeys(scenarios):
            try:
                resolution = self.images.effective_placements(scenario)  # type: ignore[attr-defined]
            except (StoreError, OSError, ValueError):
                continue
            for slot in resolution.slots:
                if isinstance(slot, Shown) and slot.slot.startswith(BACKDROP):
                    try:  # the picture's own record says what it is (its description), not the placement's
                        image, _tag = self.images.read_image(slot.image)  # type: ignore[attr-defined]
                    except InvalidImage:
                        continue
                    offered.append({"image": str(slot.image), "from": str(scenario), "slot": slot.slot,
                                    "provenance": provenance_view(image.provenance)})
        return offered

    def _chat_art(self, args: Mapping[str, object]) -> dict:
        return self._chat_art_of(_chat_ref(args.get("chat")))

    def _chat_art_of(self, chat: ChatRef) -> dict:
        """What one chat shows beyond its cast: its backdrop, the picture on each post of its line, the
        post pictures whose turn was set aside or is gone (``orphans``: listed, never shown), and the scenario
        backdrops it may take. ``art`` is the campaign's own art, for the picker."""
        owner = OwnerRef(f"campaign:{chat.campaign_id}")
        with self.store.read_pass():  # type: ignore[attr-defined]
            try:
                self.text.chats.read_chat(chat)  # type: ignore[attr-defined]
            except (StoreError, ChatCodecError, OSError, ValueError):
                raise ApiRefused("missing_chat", f"no chat {chat}", status=404) from None
            art = self._art_of(owner)  # type: ignore[attr-defined]
            found = self._line(chat)
            line, set_aside = (None, frozenset()) if found is None else found
            offered = self._offered(chat)
        shown = {slot["slot"]: slot for slot in art["slots"]}
        etags = {entry["slot"]: entry.get("etag") for entry in art["own"]}

        def state(slot: str) -> dict:
            return {**shown.get(slot, {"slot": slot, "state": "empty"}), "etag": etags.get(slot)}
        prefix = POST + chat.chat_id + "."
        posts, orphans = [], []
        for slot in sorted(set(shown) | set(etags)):
            if not slot.startswith(prefix):
                continue
            turn = slot[len(POST):]
            if turn.rsplit(".", 1)[0] != chat.chat_id:
                continue  # another chat's turn whose chat id starts with this one's
            view = {"turn": turn, **state(slot)}
            if view["state"] == "hidden":
                continue  # taken off: nothing to show and nothing to list
            if line is not None and line.get(turn):
                posts.append(view)
            else:
                orphans.append({**view, "why": "unknown" if line is None else "not_a_post" if turn in line
                                else "set_aside" if turn in set_aside else "turn_gone"})
        return {"chat": str(chat), "subject": str(owner), "backdrop": state(BACKDROP + chat.chat_id),
                "posts": posts, "orphans": orphans, "offered": offered, "art": art}

    # -- a scenario's backdrop, taken by a chat ------------------------------------------------------------
    def _backdrop_from(self, args: Mapping[str, object]) -> dict:
        """Make a backdrop of one of the chat's scenarios (``media.chat``'s ``offered``) the chat's own. A
        campaign may place only its own pictures (038 W7), so the picture becomes a campaign image over the
        same stored bytes, its provenance naming the scenario's image it was copied from; a copy made before
        is used again. The slot is written compare-and-swap on ``expected``. Returns the campaign's art."""
        from .api import _basis, _expected  # the adapter's own argument readers

        chat = _chat_ref(args.get("chat"))
        try:
            image = ImageRef.parse(args.get("image"))
        except (ValueError, TypeError):
            raise ApiRefused("invalid_field", "image must be an image reference") from None
        expected = _expected(args)
        crop = _basis(args, "crop", Crop, ("x", "y", "w", "h"))
        focus = _basis(args, "focus", Focus, ("x", "y"))
        owner = OwnerRef(f"campaign:{chat.campaign_id}")
        slot = BACKDROP + chat.chat_id
        offered = next((item for item in self._offered(chat) if item["image"] == str(image)), None)
        if offered is None:
            raise ApiRefused("wrong_scope", f"{image} is not a backdrop of a scenario {chat} is played from")
        target = self._check_slot(owner, slot, "set")  # type: ignore[attr-defined]
        try:
            data = self.images.read_image_bytes(image)  # type: ignore[attr-defined]
            original, _tag = self.images.read_image(image)  # type: ignore[attr-defined]
        except InvalidImage as error:
            raise ApiRefused(error.reason, str(error)) from None
        batch = self.images.change()  # type: ignore[attr-defined]
        copy = self._copy_of(owner.scope, image)
        if copy is None:
            from grimoire.devicetag import device_tag

            provenance = {"origin": "a scenario's backdrop", COPIED_FROM: str(image)}
            note = (original.provenance or {}).get("note")
            if isinstance(note, str) and note:
                provenance["note"] = note
            copy = batch.add_image(owner.scope, self._new_id(f"art{device_tag(self.store)}"),  # type: ignore[attr-defined]
                                   data, provenance)
        batch.place(owner, slot, self._placement(target, "set", copy, crop, focus, expected), expected)  # type: ignore[attr-defined]
        batch.commit()
        return self._art_of(owner)  # type: ignore[attr-defined]

    def _copy_of(self, scope: str, image: ImageRef) -> ImageRef | None:
        """The campaign's readable copy of ``image`` (same bytes, provenance naming it), if it made one."""
        try:
            original, _tag = self.images.read_image(image)  # type: ignore[attr-defined]
            refs, _problems = self.store.list_images(scope)  # type: ignore[attr-defined]
        except (InvalidImage, StoreError, OSError, ValueError):
            return None
        for ref in refs:
            try:
                found, _tag = self.images.read_image(ref)  # type: ignore[attr-defined]
            except InvalidImage:
                continue
            if found.blob == original.blob and (found.provenance or {}).get(COPIED_FROM) == str(image):
                return ref
        return None


__all__ = ["BACKDROP", "COPIED_FROM", "COVER", "ENTRY", "MAX_SHOWN", "POST", "BeyondSlots", "entry_slot"]
