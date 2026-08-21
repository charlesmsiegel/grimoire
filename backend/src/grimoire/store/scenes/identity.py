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

import uuid

from .. import atomic
from ..frontmatter import dump_frontmatter, parse_frontmatter, parse_frontmatter_head
from ..paths import safe_id
from . import locking, paths


def mint() -> str:
    """A fresh identity. One place, so the shape cannot drift between the
    creation path and the backfill."""
    return uuid.uuid4().hex


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
    return parse_frontmatter_head(p).get("identity") or None


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
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    existing = meta.get("identity")
    if existing:
        return existing
    meta["identity"] = mint()
    atomic.write_text(p, dump_frontmatter(meta, body))
    return meta["identity"]


def find_by_identity(cid: str, identity: str) -> str | None:
    """The scene's current ``sid``, or ``None`` if it is gone.

    The inverse the notification tap needs: an intent carries the identity
    precisely because the id goes stale on rename, so without this the tap can
    only open a stale route or fall back to the campaign unnecessarily.
    """
    if not identity:
        return None
    d = paths._scenes_dir(cid)
    if not d.exists():
        return None
    for p in sorted(d.glob("*.md")):
        if not safe_id(p.stem):   # enumeration agrees with the resolvers
            continue
        if parse_frontmatter_head(p).get("identity") == identity:
            return p.stem
    return None
