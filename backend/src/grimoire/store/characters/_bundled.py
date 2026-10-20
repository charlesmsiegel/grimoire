"""characters: bundled responsibilities."""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from pathlib import Path

from .. import assets, cards, fetch, image_refs, image_store, ziputil

# How many bundled members one import may open looking for an avatar. A card
# needs one; each lookup re-parses the archive, so the cap is what keeps a
# hostile CHARX from buying thousands of those (Codex review).
_MAX_BUNDLED_READS = 4
# Galleries need more than the avatar's four candidate attempts, but neither
# unreadable members nor aliases of one picture may buy unbounded archive
# reparses or placement writes. One import opens at most this many distinct
# members and places at most this many non-avatar images (#26).
_MAX_BUNDLED_IMAGES = 128
# Gallery entries examined at most, whatever they turn out to be: a list of
# entries that never place (missing members, bad names) would otherwise be
# walked to its end on the request thread (#26).
_MAX_BUNDLED_CANDIDATES = 4 * _MAX_BUNDLED_IMAGES
# The V3 asset types that mean "this is the character's picture".
_AVATAR_TYPES = ("icon", "avatar")
# Stems a version directory's sidecars already use. An image named after one
# would share its `<name>.*` glob, and deleting or replacing the image would
# remove the sidecar as though it were a legacy copy (#26). `subjects.json` is
# spelled here because `image_subjects` imports this module; a test holds the
# literal to `image_subjects.SUBJECTS_FILE`.
_SIDECAR_STEMS = frozenset(
    image_refs.fold(Path(f).stem)
    for f in (assets.FOCUS_FILE, assets.DESCRIPTIONS_FILE, "subjects.json"))


class _BundledImages:
    """One CHARX's image reads, shared by its avatar and named gallery (#26).

    Reserve each member's advertised bytes before reading, including failures.
    Memoizing by member lets one picture serve several placements without
    spending the budget again; misses count too, so empty or unreadable members
    cannot buy unlimited ZIP reparses. Card text remains the source metadata;
    placements, as for avatars, belong to the existing image store.
    """

    def __init__(self, data: bytes):
        self.data = data
        self.remaining = cards.MAX_ASSET_BYTES
        self.images: dict[str, tuple[bytes, str] | None] = {}
        # Parsed once on first use: every member read shares it, so an archive
        # with a huge entry table costs one central-directory scan, not one per
        # lookup (#26).
        self._archive: zipfile.ZipFile | None = None
        self._opened = False

    def _zip(self) -> zipfile.ZipFile | None:
        if not self._opened:
            self._opened = True
            self._archive = cards.open_charx(self.data)
        return self._archive

    def read(self, path: str) -> tuple[bytes, str] | None:
        if path in self.images:
            return self.images[path]
        if self.remaining <= 0 or len(self.images) >= _MAX_BUNDLED_IMAGES:
            return None
        archive = self._zip()
        if archive is None:
            self.images[path] = None
            return None
        blob = cards.read_charx_asset(self.data, path, max_bytes=self.remaining,
                                      spend=self._spend, archive=archive)
        image = None
        if blob is not None:
            ext = fetch.sniff_ext(blob)
            if ext:
                image = (blob, ext)
        self.images[path] = image
        return image

    def _spend(self, size: int) -> None:
        self.remaining -= size


def _bundled_image_name(name: str) -> bool:
    # assets.storable predates placements and admits NUL and Win32 device/
    # invalid names. Imported names must work on every synced platform,
    # including the placement's .json suffix, and in an image URL, whose
    # builders do not all escape the name (#26).
    if not assets.storable(name) or any(ord(c) < 32 or c in '<>"|#%?' for c in name):
        return False
    if image_refs.fold(name) in _SIDECAR_STEMS:
        return False  # `<name>.*` cleanup would treat the sidecar as this image's
    if image_refs.fold(name).startswith(assets.LOCALIZED_PREFIX):
        return False  # the grid counts these names as localized, not gallery art
    try:
        ziputil.member_parts(name)
        placement = image_refs.ref_path(Path("."), name)  # only the file name matters
        return len(placement.name.encode("utf-8")) <= 255
    except (ValueError, ziputil.ArchiveError):
        return False

def _stage_bundled(card: dict, bundled: _BundledImages,
                   occupied: Callable[[str], bool] = lambda name: False) -> list[tuple[str, str]]:
    """Ingest every named gallery picture the card places: (name, image id) pairs.

    Runs BEFORE any record is written. Ingest is the one fallible byte write an
    import makes, so a failure here leaves no character behind, only orphaned
    blobs that collection reclaims. Each distinct member is ingested once however
    many names share it (#26).
    """
    data = cards.card_data(card)
    extensions = data.get("extensions")
    holders = (data, extensions) if isinstance(extensions, dict) else (data,)
    used: set[str] = set()
    ingested: dict[str, str] = {}
    staged: list[tuple[str, str]] = []
    examined = 0
    for holder in holders:
        entries = holder.get("assets")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if len(used) >= _MAX_BUNDLED_IMAGES or examined >= _MAX_BUNDLED_CANDIDATES:
                return staged
            examined += 1
            placed = _stage_entry(entry, bundled, ingested, used, occupied)
            if placed is not None:
                staged.append(placed)
                used.add(image_refs.fold(placed[0]))
    return staged


def _stage_entry(entry: object, bundled: _BundledImages, ingested: dict[str, str],
                 used: set[str], occupied: Callable[[str], bool]) -> tuple[str, str] | None:
    if not isinstance(entry, dict) or entry.get("type") in _AVATAR_TYPES:
        return None
    name, uri = entry.get("name"), entry.get("uri")
    if not (isinstance(name, str) and isinstance(uri, str) and _bundled_image_name(name)):
        return None
    # Fold as the store does (`image_refs.fold`, the image lock's rule): a store
    # may sync to Windows. The first valid entry owns its name, and only avatar
    # resolution may claim the avatar slot, even when this card has no avatar.
    folded = image_refs.fold(name)
    if folded == image_refs.fold(assets.AVATAR) or folded in used or occupied(name):
        return None  # an update fills empty slots only, so never ingest for a taken one
    path = cards.embedded_path(uri)
    if path is None:
        return None
    image = bundled.read(path)
    if image is None:
        return None
    image_id = ingested.get(path)
    if image_id is None:
        image_id = ingested[path] = image_store.ingest(image[0], image[1]).id
    return name, image_id
