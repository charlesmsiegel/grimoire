"""A scene's stable identity token, and the reverse lookup over it.

Scene ids are *not* stable and are *not* unique over time. ``serialize._numbering``
derives the next number from the files on disk with no stored counter, so
deleting a scene frees its number, and a same-titled replacement lands on the
same ``sid``. A rename moves the id too, because the slug is part of it.

That leaves two things with no answer:

* a long-running turn that captured ``sid`` cannot tell, when it finally
  publishes, whether that id still names the scene it started on -- so a reply
  can land on a *different* scene that recycled the id;
* a notification posted minutes ago cannot route back to its scene, because the
  id it stored may since have moved.

``identity`` answers both: an opaque 32-hex value minted at creation, carried in
the frontmatter, and never reused. It is a correctness token, not scene content,
which is why ``read.read_scene`` filters it out of the payload -- see
``read._without_identity`` for what that protects.
"""

from __future__ import annotations

import re
import uuid

from .. import atomic
from ..frontmatter import parse_frontmatter_head
from ..paths import safe_id
from . import locking, paths

_TOKEN = re.compile(r"\A[0-9a-f]{32}\Z")
"""What a valid identity looks like.

Enforced on READ, not just on write, because a pre-feature scene may already
carry an unrelated `identity` key that a user or another tool put there. Adopting
any non-empty string as the correctness token has two failure modes: a value
containing `/` or a space cannot survive the by-identity route's path segment,
and a value duplicated across scenes makes the reverse lookup return whichever
file happens to sort first. Anything that is not a token is treated as absent,
so the backfill mints a real one.
"""


def mint() -> str:
    """A fresh identity. One place, so the shape cannot drift between the
    creation path and the backfill."""
    return uuid.uuid4().hex


class UnreadableError(OSError):
    """The header could not be read at all -- which is not the same as "has no
    identity", and review caught the two being conflated.

    ``_read_token`` used to answer ``None`` for both, so a scene whose file was
    momentarily unopenable -- a sync client mid-write, a Windows sharing
    violation, the exact conditions a synced library invites -- read as
    identity-less. ``ensure_identity`` would then mint a *second* token over a
    scene that already had a perfectly good one, and every run and notification
    already holding the old value would stop matching the scene they name.

    An ``OSError`` subclass on purpose: the backfill and the lazy callers
    already skip a scene on ``OSError``, and "cannot read the file" is what this
    is. Nothing has to learn a new exception to do the safe thing with it.
    """


def _read_token(p) -> str | None:
    """This file's identity if it has a valid one, else ``None``.

    Never raises -- for the *lookup* sweep, where one unreadable file must not
    blind a notification tap. Callers that would WRITE on a ``None`` must use
    ``_read_token_strict`` instead, because here an absent identity and an
    unreadable file are the same answer.
    """
    try:
        return _read_token_strict(p)
    except UnreadableError:
        return None


def _read_token_strict(p) -> str | None:
    """This file's identity, ``None`` if it genuinely has none or has one that
    is not a token, and ``UnreadableError`` if the question could not be asked.

    The invalid-token case stays ``None`` deliberately: that is a value we read
    and rejected, so replacing it loses nothing that ever worked.
    """
    try:
        value = parse_frontmatter_head(p).get("identity", "")
    except (OSError, UnicodeDecodeError) as exc:
        raise UnreadableError(f"{p.name}: {exc}") from exc
    return value if _TOKEN.match(value) else None


def _drop_identity_line(raw: bytes) -> bytes:
    """``raw`` without its frontmatter ``identity`` line, however it is spelled.

    Only the header is scanned: a transcript may legitimately contain a line
    beginning with ``identity``, and rewriting the body is exactly what this
    module goes to lengths to avoid.
    """
    for eol in (b"\r\n", b"\n"):
        head = b"---" + eol
        if not raw.startswith(head):
            continue
        end = raw.find(eol + b"---", len(head) - len(eol))
        if end == -1:
            return raw
        block, rest = raw[len(head):end + len(eol)], raw[end + len(eol):]
        kept = [ln for ln in block.split(eol)
                if ln.split(b":", 1)[0].strip() != b"identity"]
        return head + eol.join(kept) + rest
    return raw


def _splice(raw: bytes, line: bytes) -> bytes | None:
    """``raw`` with ``line`` inserted at the end of its frontmatter block, every
    other byte untouched. ``None`` if there is no block to splice into.

    BYTES, not text. ``Path.read_text`` performs universal-newline translation,
    so a scene saved with CRLF comes back with every ``\r\n`` already collapsed
    and writing it out again publishes the normalized copy. Other mutators only
    touch a file the user is actively editing; this one touches every file in
    the library on first boot, unprompted, so it must not reformat anything.

    Deliberately NOT parse-then-dump. `parse_frontmatter` models only
    ``key: value`` lines -- it drops anything without a colon and collapses
    duplicate keys -- and `dump_frontmatter` requotes what survives. Round-
    tripping a hand-edited header through that pair deletes the parts it does
    not model, and this runs over every scene file in a real library on first
    boot. A transcript is the one artifact here that cannot be regenerated, so
    the migration edits the header surgically instead.
    """
    for eol in (b"\r\n", b"\n"):
        head = b"---" + eol
        if not raw.startswith(head):
            continue
        # The CLOSING fence is found independently of the opener's newline
        # style. A hand-edited file -- or one a tool rewrote halfway -- can open
        # with CRLF and close with LF; text reads normalize that away, so the
        # app sees perfectly good frontmatter while a style-matched byte search
        # finds no closing fence at all. Returning None there makes the caller
        # prepend a SECOND header, demoting the scene's real title and model
        # into the transcript. Searching for `\n---` matches both, since a CRLF
        # fence contains it.
        end = raw.find(b"\n---", len(head) - 1)
        if end == -1:
            return None
        at = end + 1                       # immediately after that newline
        return raw[:at] + line + eol + raw[at:]
    return None


def scene_identity(cid: str, sid: str) -> str | None:
    """This scene's identity, or ``None`` if it predates the field.

    ``None`` is a real answer, not an error: a campaign whose lock was held at
    startup is skipped by the backfill, so its scenes stay identity-less until
    something calls ``ensure_identity``. Callers that need a value must ask for
    one rather than treating ``None`` as a comparable token -- comparing ``None``
    with ``None`` always matches, which is the corruption this exists to stop.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return None
    return _read_token(p)


def scene_identity_strict(cid: str, sid: str) -> str | None:
    """``scene_identity``, but an unreadable file raises instead of answering
    ``None``.

    The read-only counterpart to ``ensure_identity``'s strict read, for callers
    that use the answer to decide who OWNS something. There, "this scene has no
    identity" and "I could not read this scene" have to be different: the run
    registry treats an absent identity as a wildcard, so a replacement scene
    whose header was momentarily unopenable would match -- and be handed the
    dead scene's run to read or cancel, which is the recycled-id hazard this
    whole module exists to close.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return None
    return _read_token_strict(p)


@locking._serialized
def ensure_identity(cid: str, sid: str, replace: bool = False) -> str:
    """This scene's identity, assigning one first if it has none.

    Under the campaign lock because it is a read-modify-write of the whole scene
    file like every other mutator here; two concurrent callers would otherwise
    mint two values and one would win, handing the loser a token that no longer
    matches what is on disk.

    ``replace`` forces a fresh token over an existing valid one. Only the
    backfill uses it, and only for a duplicate: two scenes carrying the same
    token make the reverse lookup answer with whichever file sorts first, so a
    notification for one would open the other.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    # STRICT, so a file we merely failed to open does not read as one with no
    # identity. Minting there would publish a new token over a valid one and
    # orphan every reference already holding it -- see `UnreadableError`.
    existing = _read_token_strict(p)
    if existing and not replace:
        return existing
    token = mint()
    raw = p.read_bytes()
    # Drop any existing `identity` line before splicing the new one in, whatever
    # `_read_token_strict` made of it. Two cases reach here with a line already
    # on disk: a real token being replaced as a duplicate, and a value that is
    # not a token at all. Gating the drop on the first left the second with TWO
    # `identity:` lines -- the parser collapses duplicates, so it read as fixed
    # while the rejected value stayed in the file, one header edit away from
    # winning. Dropping unconditionally is also why the drop matches on the KEY
    # rather than the canonical byte sequence: `_read_token` accepts spellings a
    # person would type (`identity : x`, a quoted value), and a byte-exact match
    # would find none of them.
    spliced = _splice(_drop_identity_line(raw), f"identity: {token}".encode())
    if spliced is None:
        # No frontmatter block to splice into. Give it one rather than
        # rewriting anything: the body is carried through byte for byte, and a
        # scene with no header was already unreadable to `read_scene`.
        spliced = b"---\nidentity: " + token.encode() + b"\n---\n\n" + raw
    atomic.write_bytes(p, spliced)
    return token


def find_by_identity(cid: str, identity: str) -> str | None:
    """The scene's current ``sid``, or ``None`` if it is genuinely gone.

    The inverse the notification tap needs: an intent carries the identity
    precisely because the id goes stale on rename, so without this the tap can
    only open a stale route or fall back to the campaign unnecessarily.

    Raises ``UnreadableError`` when the scan finished without a match but could
    not read every candidate, and the distinction is the whole point. One
    unreadable file must not blind the scan -- that is why the loop skips and
    carries on -- but "I looked everywhere and it is not here" and "I could not
    look everywhere" are different answers, and only the first should send a tap
    back to the campaign. A sync client holding one header for a moment used to
    turn a scene that exists into a scene reported gone.
    """
    if not _TOKEN.match(identity or ""):
        return None
    d = paths._scenes_dir(cid)
    if not d.exists():
        return None
    blind = False
    for p in sorted(d.glob("*.md")):
        if not safe_id(p.stem):   # enumeration agrees with the resolvers
            continue
        try:
            if _read_token_strict(p) == identity:
                return p.stem
        except UnreadableError:
            # Keep scanning: the match may well be a later file, and one
            # unopenable transcript must not cost the tap its answer.
            blind = True
    if blind:
        raise UnreadableError(
            f"{cid}: some scenes could not be read, so the identity cannot be "
            f"ruled out")
    return None
