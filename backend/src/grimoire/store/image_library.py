"""An image library's rules, with no idea whose library it is.

The half of ``store.campaign_images`` that never needed a campaign: what a name
may be, how big an upload may get, and how a flat directory enumerates.
``store.world_images`` and ``store.campaign_images`` are the two scopes over it,
and they agree about all three because there is one copy of each rule rather
than two that match until one of them is fixed.

**``put`` and ``delete`` are deliberately NOT here.**
``tests/test_lock_domain_guard.py`` recognizes a mutating module by its
``assets.put_in`` / ``assets.delete_in`` call sites (``_ASSETS_WRITERS``), and
mutation does not propagate across an import -- so a scope module that wrote
through this one would silently leave the lock domain's survey, taking with it
the guard's grip on the very modules whose locking matters most. Each scope
keeps its own two-line write, next to the lock it is taken under. The
duplication is the point.
"""

from __future__ import annotations

from pathlib import Path

from . import assets

#: Ceiling on one stored image, and the same number and the same reason as
#: ``covers.MAX_BYTES``: the backend is packaged verbatim into the Android app
#: (Chaquopy), where an upload exists as the request body, again as one `bytes`
#: object, and again inside the EPUB the export builds in memory. The route
#: refuses an oversized upload from ``UploadFile.size`` before that `bytes`
#: allocation happens.
MAX_BYTES = 25 * 1024 * 1024

#: The 413 text, shared by the route's pre-read check and ``validate_size``.
TOO_LARGE = "image is too large (max 25 MB)"

#: Characters an addressable name may not contain. Every one of them breaks the
#: single thing this library exists for -- putting the image into a post as
#: ``![alt](url)`` -- rather than breaking the filesystem, which ``assets``
#: already guards:
#:
#:   - whitespace ends a markdown link destination, and ends the URL the export's
#:     own scanner (``export._IMG_URL``) matches;
#:   - ``(`` and ``)`` nest and close that destination;
#:   - ``<`` and ``>`` are its alternate syntax;
#:   - ``#`` and ``?`` truncate the path in a URL;
#:   - ``%`` would leave the path ambiguous about whether it is already encoded,
#:     which is how a name round-trips to a different file;
#:   - ``"``, ``'``, `` ` `` and ``\\`` quote or escape the thing they sit in.
#:
#: ``[`` and ``]`` -- the alt-text delimiters, and glob metacharacters -- are
#: already refused by ``assets``, as is ``.``.
#:
#: Stated as a denylist rather than an ASCII allowlist on purpose: a library is
#: not English, and a name in any script survives a URL and a markdown link
#: unharmed. What cannot survive is punctuation the surrounding syntax owns.
UNADDRESSABLE = frozenset("()<>#?%\"'`\\") | frozenset(" \t\n\r\v\f")


#: Names the routes have spent on something else. ``GET
#: /campaigns/{cid}/images/undescribed`` is the library's describe backlog, and
#: it is registered BEFORE ``/images/{name}`` so that it is reachable at all --
#: which means an image stored under that name could never be served: every URL
#: for it would answer with the backlog JSON, so the picker tile showed a broken
#: preview and an inserted post rendered as broken markdown (PR review).
#:
#: Reserved rather than tolerated, exactly as ``assets`` reserves
#: ``promote-tmp``, and case-folded for its reason: on Windows and macOS
#: ``Undescribed.png`` *is* ``undescribed.png``, so a case variant would claim
#: the same file as the name the route owns.
RESERVED = frozenset({"undescribed"})


class ImageTooLarge(Exception):
    """The upload is bigger than `MAX_BYTES` (HTTP 413)."""


def validate_size(data: bytes) -> None:
    """Re-check the bytes actually received.

    The check that *matters* is the route's, from ``UploadFile.size``, before
    ``read()`` materializes the body as one `bytes` object -- that allocation is
    the whole thing ``MAX_BYTES`` exists to bound, and a cap enforced only after
    reading protects nothing. This is the belt to those braces, and it earns its
    place because ``size`` is Optional in the ASGI contract: a client, or a
    future transport, that leaves it None would otherwise buy an unbounded read.
    Two checks, one answer -- the split ``store.covers`` already makes, and here
    rather than inline in the route so both halves are reachable from a test.
    """
    if len(data) > MAX_BYTES:
        raise ImageTooLarge(TOO_LARGE)


def addressable(name: str) -> bool:
    """Can a post link to this image?

    The rule ``put_image`` gates on and ``list_images`` filters by, and they
    have to be the SAME rule, including the half this module does not own:
    #373's lesson was a token that named a file the server would not serve, and
    offering a picker tile whose insert 404s -- or renders as broken markdown --
    is that bug wearing a different hat.

    So it is a conjunction, not just the URL half. ``assets.storable`` is what
    ``put_in`` will write under and ``path_in`` will resolve back (``safe_id``,
    no ``.``, no glob metacharacter, ``promote-tmp`` reserved); ``RESERVED`` is
    what the routes have already spent; ``UNADDRESSABLE`` is what a link can
    carry. Dropping the first half is not hypothetical: this
    directory is one a sync client writes into, ``assets.list_in`` shows a
    ``promote-tmp.png`` deliberately (it is crash residue worth seeing in a
    per-version folder), and here that is simply a file nothing can ever serve.
    """
    return (assets.storable(name) and name.casefold() not in RESERVED
            and not any(c in UNADDRESSABLE for c in name))


def listing(d: Path) -> list[dict]:
    """``[{"name", "ext", "v"}, ...]`` for every image a post could link to.

    ``assets.list_in``'s newest-wins enumeration -- one entry per logical image,
    whatever extensions its siblings wear -- filtered by ``addressable``, which
    is the same conjunction ``put`` gates on. Offering a tile whose insert 404s
    is #373 wearing a different hat, so the offer and the gate are one rule.

    A directory that is not there enumerates as empty rather than raising: that
    is every library before its first upload, and the world half of a campaign
    whose world is gone.
    """
    return [i for i in assets.list_in(d) if addressable(i["name"])]
