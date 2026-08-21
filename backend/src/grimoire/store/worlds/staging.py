"""Building a world tree off to one side, then publishing it under a free id.

Two features land a *whole world* in the library at once — importing a bundle
(#54) and forking one (#41) — and both face the same two problems, so both go
through here rather than each solving them again.

**Nothing half-made is ever visible.** `list_worlds` calls a directory a world
the moment it holds a `world.md`, and both features spend real time (a
gigabyte of character art, in the worlds this is sized for) with that file
already written and the copy still running. So the tree is built inside
`staging_root()` — a dot-directory the world listing never looks in — and
enters the library by a single `rename`. A failure at any point before that
leaves the library exactly as it was; the caller discards the staging tree and
the user sees nothing.

**A world's records name the world.** `store/localize.py` writes absolute
serving URLs into card and greeting text, and a PC persona can carry one by
hand, so a tree that lands under a new id renders every localized image as a
404 until the ids inside it are repointed at the id it actually got. Which id
that is cannot be known until the rename succeeds — the one it was going to
get can be taken in between — so `publish` owns the retry and re-points on
each attempt, and the world it finally publishes always references itself.

**What this deliberately does not do is sweep.** A process killed outright
mid-copy leaves a world-sized tree under `staging_root()` that nothing lists
and nothing removes; the callers' own `finally` covers every exception but not
a `SIGKILL` or a power cut. Forking makes that more reachable than importing
did — it is a click on a shelf rather than a rare deliberate act — but a sweep
here would have to decide, from a directory's age alone, that no other process
is still filling it, and getting that wrong destroys somebody's in-flight
import. The leaked bytes are recoverable by hand and named by a directory the
user can see; a wrong sweep is not. Left as a known limit rather than guessed
at.
"""

from __future__ import annotations

from pathlib import Path

from .. import atomic
from ..paths import home, uniquify
from . import paths as worlds_paths

# What may be rewritten: exactly the two extensions the store writes its
# *records* in. Getting this wrong edits a user's asset, so it is a closed list
# rather than "textual and not under a directory called assets" -- `.svg` is a
# text format and an image at once, and no directory-name heuristic can tell
# which one a given file is.
_REWRITABLE = frozenset({".md", ".json"})

_PUBLISH_ATTEMPTS = 4


def staging_root() -> Path:
    """Where a world-to-be is assembled: a sibling of `worlds/`, not a child.

    Dot-prefixed and outside the worlds directory so no enumeration reaches it,
    and under `home()` so the publishing `rename` stays within one filesystem —
    a cross-device rename is not atomic and would fall back to a second full
    copy.
    """
    return home() / ".world-staging"


def repoint_urls(staging: Path, old_wid: str, new_wid: str) -> int:
    """Rewrite localized image URLs from `old_wid` to `new_wid` in place.

    Byte-level, and over ``.md``/``.json`` only -- the two extensions the store
    writes its records in, which is also exactly the scope #54 specified. A
    substitution that had to decode every file would fail on the first asset,
    and one that touched an asset would corrupt it. The prefix carries its
    trailing slash so a world id that is a prefix of another (``realm`` beside
    ``realm-2``) cannot be rewritten by half.

    A file's *extension* decides this, with no exception for where it sits.
    Widening to every textual suffix pulled in ``.svg``, which is a text format
    and an image at once, so a portrait got edited (Codex review); narrowing
    that back out with a "not under a directory called assets" rule only traded
    one guess for another, and would have skipped a genuine ``.md`` record that
    happened to live under such a directory. The sidecars that do sit under
    ``assets/`` are scanned along with everything else. ``subjects.json`` and
    ``focus.json`` hold ids and offsets, so for them this is a no-op.
    ``descriptions.json`` is the one that holds free prose an author wrote, and
    so *can* contain a URL — which is a reason to scan it rather than to skip
    it: a description naming an image by its world-scoped URL should follow the
    world being renamed exactly as a record body does. The prefix carries its
    trailing slash, so the substitution is as precise here as in a ``.md``.
    """
    old = f"/api/worlds/{old_wid}/".encode()
    new = f"/api/worlds/{new_wid}/".encode()
    touched = 0
    for p in staging.rglob("*"):
        if p.suffix.lower() not in _REWRITABLE or not p.is_file():
            continue
        data = p.read_bytes()
        if old in data:
            atomic.write_bytes(p, data.replace(old, new))
            touched += 1
    return touched


class WorldIdConflictError(Exception):
    """A finished world tree that could not be given an id.

    Not a statement about the tree: it is complete and would have published
    fine a moment earlier. Separated from its callers' input errors so a route
    can answer 409 rather than blaming the request (Codex review).

    Spelled with the `Error` suffix its neighbours (`WorldNotFound`,
    `WorldInUse`, `BundleConflict`) predate: those are carried in the ruff
    baseline as N818 findings, and new code is held to the rule rather than
    joining them.
    """


def publish(staging: Path, base: str, current: str) -> str:
    """Move the staged tree into the library under a free id; return that id.

    ``uniquify`` picked ``current`` a moment ago, so a concurrent import or
    fork can have taken it in between -- and a plain rename would *merge into*
    it on POSIX when the destination happens to be an empty directory. So the
    id is re-picked and retried rather than refused: losing a race is not a
    reason to reject a perfectly good world (Codex review). Each retry
    re-points the URLs from the id the records currently carry to the new
    candidate, so the published world always references itself.
    """
    for attempt in range(_PUBLISH_ATTEMPTS):
        dest = worlds_paths.world_root(current)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            try:
                staging.rename(dest)
                return current
            except OSError:
                pass          # lost the race inside the check-to-rename window
        if attempt == _PUBLISH_ATTEMPTS - 1:
            break
        nxt = uniquify(base, lambda c: worlds_paths.world_root(c).exists())
        repoint_urls(staging, current, nxt)
        current = nxt
    raise WorldIdConflictError(
        f"could not claim a world id (last tried {current})")
