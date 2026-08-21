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


def _read_token(p) -> str | None:
    """This file's identity if it has a valid one, else ``None``.

    Never raises: a scene that vanished between enumeration and open, or one
    whose bytes are not valid UTF-8, is simply not a match. The reverse lookup
    runs when a notification is tapped, and one corrupt file must not blind it.
    """
    try:
        value = parse_frontmatter_head(p).get("identity", "")
    except (OSError, UnicodeDecodeError):
        return None
    return value if _TOKEN.match(value) else None


def _splice(text: str, line: str) -> str | None:
    """``text`` with ``line`` inserted at the end of its frontmatter block,
    every other byte untouched. ``None`` if there is no block to splice into.

    Deliberately NOT parse-then-dump. `parse_frontmatter` models only
    ``key: value`` lines -- it drops anything without a colon and collapses
    duplicate keys -- and `dump_frontmatter` requotes what survives. Round-
    tripping a hand-edited header through that pair deletes the parts it does
    not model, and this runs over every scene file in a real library on first
    boot. A transcript is the one artifact here that cannot be regenerated, so
    the migration edits the header surgically instead.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4 - 1)     # the closing fence, from inside the block
    if end == -1:
        return None
    return text[:end + 1] + line + text[end + 1:]


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


@locking._serialized
def ensure_identity(cid: str, sid: str) -> str:
    """This scene's identity, assigning one first if it has none.

    Under the campaign lock because it is a read-modify-write of the whole scene
    file like every other mutator here; two concurrent callers would otherwise
    mint two values and one would win, handing the loser a token that no longer
    matches what is on disk.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    existing = _read_token(p)
    if existing:
        return existing
    token = mint()
    text = p.read_text(encoding="utf-8")
    spliced = _splice(text, f"identity: {token}\n")
    if spliced is None:
        # No frontmatter block to splice into. Give it one rather than
        # rewriting anything: the body is carried through byte for byte, and a
        # scene with no header was already unreadable to `read_scene`.
        spliced = f"---\nidentity: {token}\n---\n\n{text}"
    atomic.write_text(p, spliced)
    return token


def find_by_identity(cid: str, identity: str) -> str | None:
    """The scene's current ``sid``, or ``None`` if it is gone.

    The inverse the notification tap needs: an intent carries the identity
    precisely because the id goes stale on rename, so without this the tap can
    only open a stale route or fall back to the campaign unnecessarily.
    """
    if not _TOKEN.match(identity or ""):
        return None
    d = paths._scenes_dir(cid)
    if not d.exists():
        return None
    for p in sorted(d.glob("*.md")):
        if not safe_id(p.stem):   # enumeration agrees with the resolvers
            continue
        if _read_token(p) == identity:
            return p.stem
    return None
