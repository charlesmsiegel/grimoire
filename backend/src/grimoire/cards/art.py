"""Card art: which slot each V3 ``assets`` entry, and the caller's portrait, fills (Sprint 038). Pure.

A Character Card V3 lists its pictures in ``data.assets``: a ``type`` (``icon``, ``emotion``,
``background``, ``user_icon`` or anything else), a ``uri`` and a ``name``. This module decides,
from the card alone, the 018 slot each one belongs in and whether its bytes are here:

- a ``data:`` URI with base64 content is decoded **locally**: the bytes are in the card;
- ``ccdefault:`` is the card's own picture, which this slice receives only as the importer's
  ``portrait`` bytes;
- anything else (an ``http(s)`` address, a CHARX ``embeded://`` path, a ``data:`` URI that cannot
  be decoded) is **missing**. It still gets its slot, as an explicit ``unresolved`` placement
  (018's mode for a picture that belongs there but is not), so a missing picture never becomes
  an empty slot that a later picture, or another actor's file with the same name, quietly fills.

Nothing is fetched and no file is read. Whether decoded bytes are a supported picture is the
store's question (its sniffer); ``grimoire.cardstore`` asks it and turns a refusal into an
``unresolved`` placement too.

Slots, in the order the card lists its assets (the first claim on a slot wins; a later one is a
``duplicate_slot`` loss):

| asset | slot |
|---|---|
| the caller's ``portrait`` | ``avatar`` |
| the card's main ``icon`` (named ``main``, else its first icon) | ``avatar``, unless a ``portrait`` fills it: then a main icon that is ``ccdefault:`` *is* that portrait, and any other main icon goes to ``icon-main`` |
| any other ``icon`` | ``icon-<name>`` (``icon-<index>`` when its name is ``main`` too) |
| ``emotion`` | ``expr.default.<name>``: a sprite of the card's one outfit, ``default`` |
| the main ``background`` (named ``main``, else the first) | ``background`` |
| any other ``background`` | ``background-<name>`` |
| ``user_icon`` | none: it pictures the user, not the card (``not_applicable``) |
| any other type | ``asset-<name>`` |

``<name>`` is the asset's name as an identity segment (``slugify``). A name with no ASCII letter
or digit (``笑顔``) gives none, so it is the first 8 hex digits of the sha256 of the name
(stripped, NFC): ``expr.default.emotion-<hash>``, ``icon-<hash>``. The slot follows the name, so
reordering the card's assets never moves a picture (or an individual's own picture over it) to
another sprite. Only an asset with no name at all takes its place in the array (``emotion-3``).
"""

from __future__ import annotations

import binascii
import hashlib
import re
import unicodedata
from dataclasses import dataclass

from grimoire.images.model import expression_slot, slot_role

from .model import CCV3, Asset, LorebookCard, Loss, ParsedCard
from .records import slugify

#: the one outfit a card's emotion sprites belong to: a V3 card has no outfits
CARD_OUTFIT = "default"
#: the most slots one import fills from a card's assets; a later asset is a ``too_many`` loss
MAX_ART = 64
#: a ``data:`` URI: a media type and parameters, then ``;base64,`` and the content
_DATA_URI = re.compile(r"data:(?P<head>[^,]*?);base64,(?P<body>.*)", re.IGNORECASE | re.DOTALL)
_REMOTE = ("http://", "https://")
_EMBEDDED = ("embeded://", "embedded://")
_DEFAULT = "ccdefault:"


@dataclass(frozen=True)
class ArtPiece:
    """One slot a card fills. ``data`` is the picture's bytes, or ``None`` when it is missing; then
    ``reason`` says why (``not_fetched``, ``not_embedded``, ``no_default``, ``invalid_data``,
    ``unsupported_uri``). ``index`` is the asset's place in the card's ``assets`` (``None`` for the
    caller's portrait), so a report or a placement's provenance names the right entry."""

    slot: str
    data: bytes | None
    reason: str | None = None
    index: int | None = None
    asset_type: str | None = None
    name: str | None = None

    @property
    def role(self) -> str:
        return slot_role(self.slot) or "gallery"

    @property
    def from_portrait(self) -> bool:
        return self.index is None


def asset_path(card: ParsedCard, index: int) -> str:
    """The card field path of asset ``index`` (``data.assets[3]``), as losses name it."""
    return f"{'data.assets' if card.format == CCV3 else 'assets'}[{index}]"


def _picture(asset: Asset, portrait: bytes | None) -> tuple[bytes | None, str | None]:
    """The asset's bytes, or ``(None, reason)``: read from the card itself, never fetched."""
    uri = asset.uri.strip()
    lowered = uri.lower()
    if lowered.startswith(_REMOTE):
        return None, "not_fetched"
    if lowered.startswith(_EMBEDDED):
        return None, "not_embedded"  # a CHARX package's file: this slice reads card JSON only
    if lowered == _DEFAULT:
        return (portrait, None) if portrait is not None else (None, "no_default")
    match = _DATA_URI.fullmatch(uri)
    if match is None:
        return None, "invalid_data" if lowered.startswith("data:") else "unsupported_uri"
    try:
        # strict: only the base64 alphabet and correct padding, nothing after it
        data = binascii.a2b_base64("".join(match.group("body").split()), strict_mode=True)
    except (binascii.Error, ValueError):
        return None, "invalid_data"
    return (data, None) if data else (None, "invalid_data")


def _main(assets: list[Asset], kind: str) -> Asset | None:
    """The asset of ``kind`` named ``main`` (V3's convention), else the first of that kind."""
    of_kind = [asset for asset in assets if asset.type == kind]
    named = [asset for asset in of_kind if (asset.name or "").strip().casefold() == "main"]
    return (named or of_kind or [None])[0]


def _name(asset: Asset, prefix: str) -> str:
    """The asset's name as a slot segment: its slug, else a hash of the name, else (no name) its index."""
    name = unicodedata.normalize("NFC", (asset.name or "").strip())
    if not name:
        return f"{prefix}{asset.index}"
    return slugify(name, f"{prefix}{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}")


def card_art(card: ParsedCard, portrait: bytes | None) -> tuple[tuple[ArtPiece, ...], tuple[Loss, ...]]:
    """The slots ``card`` and ``portrait`` fill, and the losses of the assets that fill none or
    whose picture is missing (``not_fetched`` for a remote one, ``unavailable`` otherwise)."""
    if isinstance(card, LorebookCard):
        return (), ()
    pieces: dict[str, ArtPiece] = {}
    losses: list[Loss] = []
    if portrait is not None:
        pieces["avatar"] = ArtPiece("avatar", portrait)
    assets = list(card.assets)
    main_icon, main_background = _main(assets, "icon"), _main(assets, "background")
    for asset in assets:
        path = asset_path(card, asset.index)
        if asset.type == "user_icon":
            losses.append(Loss(path, "not_applicable"))
            continue
        data, reason = _picture(asset, portrait)
        if asset is main_icon:
            if portrait is not None and asset.uri.strip().lower() == _DEFAULT:
                continue  # the main icon is the card's own picture: the portrait already fills avatar
            slot = "avatar" if portrait is None else "icon-main"
        elif asset.type == "icon":
            name = _name(asset, "")
            # ``icon-main`` is the main icon's alone: another icon named like it takes its place
            slot = f"icon-{str(asset.index) if name == 'main' else name}"
        elif asset.type == "emotion":
            slot = expression_slot(CARD_OUTFIT, _name(asset, "emotion-"))
        elif asset is main_background:
            slot = "background"
        elif asset.type == "background":
            slot = f"background-{_name(asset, '')}"
        else:
            slot = f"asset-{_name(asset, '')}"
        if slot in pieces:
            losses.append(Loss(path, "duplicate_slot"))
            continue
        if len(pieces) >= MAX_ART:
            losses.append(Loss(path, "too_many"))
            continue
        pieces[slot] = ArtPiece(slot, data, reason, asset.index, asset.type, asset.name)
        if reason is not None:
            losses.append(Loss(path, "not_fetched" if reason == "not_fetched" else "unavailable"))
    return tuple(pieces.values()), tuple(losses)


__all__ = ["CARD_OUTFIT", "MAX_ART", "ArtPiece", "asset_path", "card_art"]
