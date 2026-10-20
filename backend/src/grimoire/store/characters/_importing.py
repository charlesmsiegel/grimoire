"""characters: importing responsibilities."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import assets, atomic, cards, image_refs
from . import _avatar, _bundled, _card_fields, _paths, _reads, _records


@dataclass
class _Written:
    """What one import has written to the record so far, so a failure can undo it."""
    new_character: str | None = None
    new_version: tuple[str, str] | None = None
    #: An update's card as it stood before the write.
    prior_text: str | None = None
    #: The card text this import wrote, so rollback touches the card (restores
    #: an update's, removes a new record) only while it still holds that text.
    written_text: str | None = None
    #: Gallery placements this import added, as (name, image id).
    placed: list[tuple[str, str]] = field(default_factory=list)


def _card_text(root: Path, cid: str, vid: str) -> str | None:
    try:
        # atomic's reader: it retries the transient Windows sharing error a
        # just-replaced file can raise, which would otherwise read as "not ours".
        return atomic.read_text(_paths._card_path(root, cid, vid))
    except (OSError, _paths.CharacterNotFound):
        return None


def _unwind_import(root: Path, written: _Written, cid: str, vid: str) -> None:
    """Undo a failed import's record writes; the caller re-raises its error.

    A new character is removed whole, a new version with its art. An updated
    version gets its card text back, and each gallery placement this import
    added is removed -- but only while it still holds the image this import
    put there, so a picture uploaded in the meantime is left alone. An update
    never overwrites an image the version already had, so nothing else needs
    restoring (#26).
    """
    ours = written.written_text is not None and _card_text(root, cid, vid) == written.written_text
    if written.new_character is not None and ours:
        shutil.rmtree(_paths._char_dir(root, written.new_character), ignore_errors=True)
        return
    if written.new_version is not None and ours:
        new_cid, new_vid = written.new_version
        _paths._card_path(root, new_cid, new_vid).unlink(missing_ok=True)
        assets.delete_version_images(root, new_cid, new_vid)
        return
    if written.prior_text is not None and ours:
        # A version save that landed meanwhile must not be written back over.
        atomic.write_text(_paths._card_path(root, cid, vid), written.prior_text)
    if written.placed:  # only then do the ids name a version
        directory = assets.version_dir(root, cid, vid)
        for name, image_id in written.placed:
            assets.unlink_if_holds(directory, name, image_id)


def _write_record(root: Path, card: dict, written: _Written, *, into_cid: str | None,
                  update_vid: str | None, name: str | None, version_name: str | None) -> tuple[str, str]:
    """Write the card as a new character, a new version, or over an existing version."""
    if update_vid is not None:
        if into_cid is None:
            raise ValueError("update_vid names a version of a character, so into_cid is required")
        # The incoming card REPLACES the stored one, so anything grimoire owns
        # rather than the source does has to be carried across or a re-download
        # silently un-names a version somebody named. `chub_source` is already
        # re-applied by the caller for the same reason; the label is not
        # re-derivable from anywhere, so it is carried here.
        written.prior_text = _paths.require_version(root, into_cid, update_vid).read_text(encoding="utf-8")
        _card_fields._carry_label(_reads.read_card(root, into_cid, update_vid), card)
        _records.update_version(root, into_cid, update_vid, card)
        written.written_text = _card_fields._dumps(card)  # what update_version just wrote
        return into_cid, update_vid
    cname = name or card["data"].get("name", "Imported")
    if into_cid is None:
        cid, vid = _records.create_character(root, cname, version_name or "default", card)
        written.new_character = cid
        written.written_text = _card_fields._dumps(card)  # what create_character just wrote
        return cid, vid
    # What the importer asked this version be called, else the card spec's own
    # answer, else the character's name -- which is a poor label but has always
    # been the last resort for the SLUG, and `_version_label` no longer repeats
    # it on screen.
    vid = _records.create_version(root, into_cid,
                         version_name
                         or cards.card_data(card).get("character_version")
                         or cname, card)
    written.new_version = (into_cid, vid)
    written.written_text = _card_fields._dumps(card)  # what create_version just wrote
    return into_cid, vid


def _place_import(root: Path, cid: str, vid: str, avatar: tuple[bytes, str, str] | None,
                  staged: list[tuple[str, str]], written: _Written) -> None:
    """Place the gallery, then the avatar, and record what was added.

    A name the version already has an image for keeps it: an update fills
    empty slots and never replaces a picture, so no legacy file is dropped
    and rollback has nothing to put back. The avatar goes last, so every other
    write has landed before the one step that replaces an existing image.
    """
    directory = assets.version_dir(root, cid, vid)
    for placed, image_id in staged:
        # Vacancy is checked under the name's lock, so an upload landing
        # between the check and the placement cannot be overwritten (#26).
        if assets.link_if_vacant(directory, placed, image_id):
            written.placed.append((placed, image_id))
    if avatar:
        source = avatar[2] if avatar[2].startswith(("http://", "https://")) else None
        assets.put_image(root, cid, vid, assets.AVATAR, avatar[0], avatar[1], source_url=source)


def _occupied_in(root: Path, cid: str | None, vid: str | None) -> Callable[[str], bool]:
    """Which gallery names an update target already holds a picture under.

    Read before staging, so an update ingests nothing it will not place (#26).
    A picture arriving after this read is caught by `assets.link_if_vacant`;
    only that race can leave an unplaced object, which collection reclaims.
    """
    if cid is None or vid is None:
        return lambda name: False
    taken = assets.taken_slots(assets.version_dir(root, cid, vid))  # read once

    def occupied(name: str) -> bool:
        return image_refs.fold(name) in taken
    return occupied


def _check_destination(root: Path, into_cid: str | None, update_vid: str | None) -> None:
    """Refuse a missing destination before any picture is stored.

    Staging ingests objects into the shared image store, which an unwind cannot
    reach; a refusal raised after that would leave them behind until a manual
    collection (#26).
    """
    if update_vid is not None:
        if into_cid is None:
            raise ValueError("update_vid names a version of a character, so into_cid is required")
        _paths.require_version(root, into_cid, update_vid)
    elif into_cid is not None:
        _paths._require_char(root, into_cid)


def import_card(root: Path, data: bytes, fmt: str, into_cid: str | None = None,
                name: str | None = None, update_vid: str | None = None,
                version_name: str | None = None,
                validate_name: Callable[[str], None] | None = None) -> tuple[str, str]:
    card = cards.loads(data, fmt)  # raises cards.CardParseError on bad input
    if validate_name is not None:
        validate_name(name or str(cards.card_data(card).get("name") or "Imported"))
        validate_name(str(cards.card_data(card).get("name") or ""))
    cards.bake_char_name(card)
    # Resolve (and unhook) a carried avatar BEFORE the card is written: the
    # writes below persist whatever `card` holds at that moment.
    bundled = _bundled._BundledImages(data) if fmt == "charx" else None
    avatar = _avatar._resolve_avatar(card, data, fmt, network=(fmt != "png"), bundled=bundled)
    if fmt == "png" and avatar and not _avatar._plane_is_the_carried_avatar(data, avatar[0]):
        # A PNG's own pixels are the character's picture -- as they have always
        # been on import. The card's copy only wins when those pixels are the
        # placeholder our export writes for an avatar it could not encode, or
        # are that very picture re-encoded as a PNG (Codex review: preferring
        # it outright would swap the portrait of any third-party card whose
        # payload happens to carry an embedded icon).
        avatar = None
    if avatar and _avatar._carried_uri(avatar[2]):
        _avatar._drop_avatar_uri(card, avatar[2])
    if avatar is None and fmt == "png":
        avatar = (data, "png", "")  # the PNG file itself is the avatar
    # Every fallible byte write happens before the first record write, so a
    # failure there leaves nothing behind (#26).
    _check_destination(root, into_cid, update_vid)
    occupied = _occupied_in(root, into_cid, update_vid)
    staged = _bundled._stage_bundled(card, bundled, occupied) if bundled is not None else []
    written = _Written()
    cid = vid = ""
    try:
        cid, vid = _write_record(root, card, written, into_cid=into_cid, update_vid=update_vid,
                                 name=name, version_name=version_name)
        _place_import(root, cid, vid, avatar, staged, written)
    except BaseException:
        _unwind_import(root, written, cid, vid)
        raise
    return cid, vid
