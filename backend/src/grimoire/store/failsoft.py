"""A fail-soft JSON read that reports itself.

The store is a folder the user can edit, sync and corrupt, so most of its JSON
readers answer a malformed file with an empty value instead of an exception: a
garbled ledger should not make the app unusable. Staying silent about it is the
right call when the empty value is *less* than the file held -- the user sees
something missing and comes looking.

Three readers are not like that, and they are why this module exists:

- ``overlay.deleted`` -- an empty tombstone set means "nothing was deleted", so
  every record the user deleted campaign-side comes back, inherited from the
  world. Failure *adds* content.
- ``overlay.detached`` -- an empty set means "still attached", so a campaign
  record whose world original was deleted resumes inheriting sync updates,
  images, tagline and voice anchor from whatever unrelated record now holds its
  id (#225). Failure *adds* content, and content from a stranger.
- ``paths._read_pointer`` -- an empty pointer drops the configured ``data_dir``,
  so the store reverts to ``~/.grimoire`` and a library kept in a synced folder
  opens empty. Failure *relocates* the store.

Neither surfaces as something a user can trace back to a corrupt file, so both
say so in the log. The fallback itself stays: an unopenable campaign, or a
backend that refuses to start over a bad dotfile, is worse than a wrong answer
that is written down.

Warnings are deduplicated on the file's (mtime, size). Both callers run tens to
hundreds of times per request -- a 20-character listing costs 41 tombstone reads
and 224 pointer reads -- and one report is diagnosable where four hundred
identical lines are just noise. Any read that does not warn -- parsed, or simply
absent -- forgets the path, so a repair that does not hold gets reported again
even if the file comes back byte-for-byte identical, which is exactly what a sync
client rolling a file back does.

Reads alone cannot bound the cache, though, because a path can leave the store
without a final read: ``campaigns.delete_campaign`` drops the whole tree with one
``shutil.rmtree``, and nothing ever asks about that campaign's tombstones again
(Codex review). Rather than hook every present and future deletion -- the
copy-pasted-guard failure ``paths.safe_id`` already records as #240 -- the cache
carries a hard cap and evicts oldest-first. Reaching it means the store has
``_MAX_WARNED`` files corrupt at once, which no dedup policy improves on, and the
cost of an eviction is one repeated warning.

The cache is plain module state, deliberately unlocked, which is safe only
because every operation on it is a *single* mapping call -- get, set, pop,
popitem. Those cannot interleave, so the worst race is two threads warning about
the same file at once, which costs a duplicate log line.

Eviction is where that stops being free, and it is worth spelling out because
the obvious spelling is wrong. ``del _warned[next(iter(_warned))]`` reads a key
and then deletes it, and two threads at the cap will pick the same oldest key:
the loser's ``del`` raises ``KeyError``, and merely making the delete tolerant
does not help, because ``next(iter(...))`` raises ``RuntimeError: dictionary
changed size during iteration`` when the size moves between the ``iter`` and the
``next``. Both escape ``read_json`` and turn a campaign read into a 500 -- the
exact opposite of what fail-soft is for (Codex review; reproduced with 32
concurrent reads at the cap under a forced switch interval). ``popitem`` picks
and removes in one call, so there is no window between the two.

Nothing else should be routed through here without the same argument. The other
fail-soft sites (``audit``, ``changes``, ``commits``, ...) are deliberately
quiet, and making them chatty would bury these two.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Paths known corrupt -> the (mtime_ns, size) that was warned about. ``None``
#: means the signature could not be taken, which never matches, so an unstattable
#: file is reported on every read rather than silently once.
#: An OrderedDict, not a dict, for `popitem(last=False)` -- oldest-first removal
#: in one call. A plain dict's `popitem()` takes the newest, which would evict
#: the entry just recorded and so dedup nothing.
_warned: OrderedDict[Path, tuple[int, int] | None] = OrderedDict()

#: Ceiling on `_warned`, so a path that vanishes by rmtree cannot pin a row for
#: the life of the process. Far above any real number of simultaneously-corrupt
#: store files, so eviction is the pathological case, not the common one.
_MAX_WARNED = 256


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _warn(path: Path, detail: str, consequence: str) -> None:
    signature = _signature(path)
    if signature is not None and _warned.get(path) == signature:
        return
    _warned[path] = signature
    while len(_warned) > _MAX_WARNED:
        try:
            _warned.popitem(last=False)
        except KeyError:      # a concurrent evictor got there first; nothing left to trim
            break
    log.warning("%s is unusable (%s) -- %s", path, detail, consequence)


def read_json(path: Path, expect: type, consequence: str) -> Any | None:
    """Read ``path`` as JSON, or return ``None`` if it is missing or unusable.

    Missing is silent: absence is the normal state of most store files, and it
    is what the caller's fallback is *for*. Unusable -- unreadable, malformed,
    or parsing to something other than ``expect`` -- reaches that same fallback
    by a route the user did not choose, so it is logged at WARNING with the
    path and ``consequence``, a plain-language sentence naming what the caller
    now does instead.

    The wrong-type case is checked here rather than left to the caller because
    it is the same failure: ``{"lore/x": true}`` in a file that should hold a
    list falls back exactly as far as a truncated one does.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _warned.pop(path, None)   # gone is not corrupt, and holds no entry
        return None
    except (ValueError, OSError) as exc:   # ValueError covers JSON and UTF-8 decoding
        _warn(path, f"{type(exc).__name__}: {exc}", consequence)
        return None
    if not isinstance(data, expect):
        _warn(path, f"expected a JSON {expect.__name__}, got {type(data).__name__}", consequence)
        return None
    _warned.pop(path, None)
    return data
