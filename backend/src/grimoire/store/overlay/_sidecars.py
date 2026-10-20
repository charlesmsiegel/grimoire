"""Per-file tagline and voice-anchor identity, tombstones and deliberate opt-in."""

from __future__ import annotations

from .. import taglines, voice_anchors
from . import _ledgers, _record_paths, _views


def tagline(cid: str, char_id: str, *, v: _views.View | None = None) -> str:
    v = _views._view(cid, v)
    mine = taglines.read(v.croot, char_id)
    if mine or _record_paths.flat_ref("characters", char_id) in v.off:
        return mine
    return taglines.read(v.wroot, char_id)


def set_tagline(cid: str, char_id: str, text: str) -> None:
    """Write the campaign's own capsule for `char_id`.

    A per-file sidecar, like the anchor's writer but without its tombstone: the
    campaign copy of `tagline.md` shadows the library's, so writing it needs no
    materialized actor, and an inherited character stays inherited (no copied
    cards, no whole-actor sync base). A blank save is the campaign's blank copy,
    which `tagline()` reads as "no sentence here" and falls back from.
    """
    taglines.write(_record_paths.croot_of(cid), char_id, text)


def set_voice_anchor(cid: str, char_id: str, text: str) -> None:
    """Write the campaign's own anchor for `char_id`; blank opts the character
    out of voice checks in this campaign.

    Campaign-side by definition: this IS the per-file divergence, the same shape
    as any other campaign edit to an inherited record, and it lives here rather
    than at the call site so nothing outside this module hands `voice_anchors` a
    raw campaign root.

    Blank is the one place the anchor overlay does NOT behave like tagline's.
    Deleting the campaign copy would let the world's anchor show through again,
    so a user who cleared the field would still be judged -- against the very
    text they just erased -- while the editor told them clearing skips the
    checks. Blankness is this feature's opt-out switch, so it has to survive
    resolution: a blank save writes a TOMBSTONE.

    The one exception is a blank that erased nothing -- no world anchor, no
    campaign anchor, no tombstone already. There is no decision in that save to
    record, and a tombstone would turn it into a standing promise to ignore an
    anchor the world might add later. Anything else -- an inherited anchor, the
    campaign's own, or an opt-out already made -- is a decision, and once made
    only the user typing an anchor back in may end it. Not the world removing
    its anchor, and not a later save that changed nothing.

    The nonce `voice_anchors.write` preserves is read from the CAMPAIGN copy, so
    a campaign that overrides the world's anchor mints its own identity. That is
    correct rather than incidental: an override is a different standard, and
    findings judged against the world's should stop applying here.
    """
    croot = _record_paths.croot_of(cid)
    inherited = ("" if _record_paths.flat_ref("characters", char_id) in _ledgers.detached(cid)
                 else voice_anchors.read(_record_paths.wroot_of(cid), char_id))
    mine = voice_anchors.read_record(croot, char_id)
    if not text.strip():
        # Tombstone unless this blank erased nothing at all. Each of the three
        # is an opt-out the resolver would otherwise undo:
        #   inherited        -- the world's anchor would show through again
        #   mine["text"]     -- clearing an override IS the opt-out; deleting it
        #                       re-exposes the world's, or lets a world anchor
        #                       created later start judging a character whose
        #                       owner just erased one
        #   mine["disabled"] -- an opt-out already made, which must outlive the
        #                       world anchor being removed and restored
        if inherited or mine["text"] or mine["disabled"]:
            voice_anchors.disable(croot, char_id)
            return
        voice_anchors.write(croot, char_id, text)   # nothing to erase: just delete
        return
    if not mine["text"] and not mine["disabled"] and text.split() == inherited.split():
        # Still inheriting, and the submitted text IS the inherited text: the
        # editor shows the resolved anchor, so re-saving an untouched form
        # lands here. Materializing a copy would mint a new nonce -- silently
        # suppressing every committed flag fingerprinted against the identical
        # world anchor -- and detach the campaign from later world edits, all
        # for a save that changed nothing. Only a real divergence diverges.
        #
        # `.split()`, not `.strip()`, so "the same anchor" means here exactly
        # what it means to `voice_drift.anchor_fingerprint` -- which normalizes
        # whitespace THROUGHOUT because rewrapping a line is presentation, not a
        # new standard. With `.strip()` the two disagreed, and the disagreement
        # was the bug: rewrapping an inherited anchor materialized a campaign
        # copy whose nonce suppressed every flag judged against text the
        # fingerprint still considered identical. The rewrap itself is not
        # persisted, which is the same outcome as re-saving an untouched form
        # and for the same reason: by the only definition of anchor identity
        # this feature has, nothing changed.
        #
        # Deliberately not extended to a standing TOMBSTONE: re-entering the
        # world's words there is a decision to be judged again, and it gets a
        # fresh identity like any other opt-back-in.
        return
    voice_anchors.write(croot, char_id, text)


def voice_anchor_record(cid: str, char_id: str, *, v: _views.View | None = None) -> dict:
    """The winning anchor's {"text", "id"} — same per-file overlay as
    `voice_anchor`, but carrying the nonce a fingerprint needs. Resolved as one
    record so text and identity always come from the SAME file: reading the
    text campaign-side and the nonce world-side would fingerprint an anchor that
    exists nowhere."""
    v = _views._view(cid, v)
    mine = voice_anchors.read_record(v.croot, char_id)
    if mine["disabled"] or _record_paths.flat_ref("characters", char_id) in v.off:
        return mine   # campaign opt-out, or a world id-mate that is a stranger
    return mine if mine["text"] else voice_anchors.read_record(v.wroot, char_id)


def voice_anchor(cid: str, char_id: str, *, v: _views.View | None = None) -> str:
    """The character's voice anchor as this campaign sees it.

    Same per-file overlay as tagline, with one difference tagline has no need
    for: a campaign can hold a TOMBSTONE saying it wants no anchor here, and
    that beats the world's rather than falling back to it (see
    `set_voice_anchor`). The anchor is world-level (a voice is a library
    property), but a campaign that has materialized its own copy of the
    character is entitled to its own reference text -- or to none."""
    return voice_anchor_record(cid, char_id, v=v)["text"]
