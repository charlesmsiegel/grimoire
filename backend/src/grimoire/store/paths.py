"""Filesystem location + id helpers for the ~/.grimoire store."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from . import atomic, failsoft

DEFAULT_HOME = Path.home() / ".grimoire"  # paths-ok: this IS the resolver's default root


def _pointer_path() -> Path:
    """Fixed location of the bootstrap pointer that records the data dir.

    This must live *outside* the data dir itself — the data dir is what it
    points at, so it cannot also store the pointer (chicken/egg). It sits
    beside the default store as a sibling dotfile.
    """
    return Path.home() / ".grimoire.json"  # paths-ok: the bootstrap pointer cannot live inside the directory it names


def _read_pointer() -> dict:
    """The bootstrap pointer's contents, empty when it has none.

    A corrupt pointer reads as empty too -- refusing to start over a bad
    dotfile is the worse failure -- but that drops the user's ``data_dir`` and
    sends the whole library back to ``~/.grimoire``, so someone who pointed
    grimoire at a synced folder opens it to nothing. Silent relocation is not a
    symptom anyone can trace to this file, so `failsoft` logs it.
    """
    return failsoft.read_json(
        _pointer_path(), dict,
        "its data_dir is ignored -- the store falls back to $GRIMOIRE_HOME if "
        f"set, else {DEFAULT_HOME}") or {}


def _pointer_data_dir() -> Path | None:
    raw = _read_pointer().get("data_dir")
    return Path(raw).expanduser() if raw else None  # paths-ok: expanding the user's own configured storage path is the feature


def home() -> Path:
    """Resolve the data root.

    Order: ``GRIMOIRE_HOME`` env var (override / test isolation) → the
    user-chosen path from the bootstrap pointer → the default ``~/.grimoire``.
    Resolved live on every call so a path change takes effect immediately.
    """
    env = os.environ.get("GRIMOIRE_HOME")
    if env:
        return Path(env)
    pointer = _pointer_data_dir()
    if pointer:
        return pointer
    return DEFAULT_HOME


def ensure_home() -> Path:
    base = home()
    (base / "worlds").mkdir(parents=True, exist_ok=True)
    (base / "campaigns").mkdir(parents=True, exist_ok=True)
    return base


def set_data_dir(path: str | Path | None) -> Path:
    """Persist the data dir to the bootstrap pointer and return the new root.

    A falsy ``path`` clears the override, reverting to the default. The target
    directory (and its ``worlds``/``campaigns`` subtrees) is created if missing.
    Raises ``ValueError`` if the target exists but is not a directory.
    """
    pointer = _pointer_path()
    data = _read_pointer()

    if not path or not str(path).strip():
        data.pop("data_dir", None)
        atomic.write_text(pointer, json.dumps(data, indent=2) + "\n")
        return ensure_home()

    resolved = Path(str(path).strip()).expanduser()  # paths-ok: same, for a path arriving from the Configuration page
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{resolved} exists but is not a directory")
    resolved.mkdir(parents=True, exist_ok=True)

    data["data_dir"] = str(resolved)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(pointer, json.dumps(data, indent=2) + "\n")
    return ensure_home()


def data_dir_info() -> dict:
    """Describe the active data dir for the settings UI."""
    env = os.environ.get("GRIMOIRE_HOME")
    pointer = _pointer_data_dir()
    current = home()
    return {
        "data_dir": str(current),
        "default": str(DEFAULT_HOME),
        "is_default": not env and pointer is None,
        "source": "env" if env else ("custom" if pointer else "default"),
        "exists": current.exists(),
    }


def any_child_record(base: Path, meta_name: str) -> bool:
    """Whether `base` holds at least one entry a listing would return.

    The same filter `worlds.read.list_worlds` / `campaigns.read.list_campaigns`
    apply — a directory, its meta file present, an id the resolvers accept —
    but it stops at the first hit and parses nothing, so "does this store hold
    anything?" costs a directory scan rather than a full read of every record.
    That distinction is the whole point: the caller is `first_run` detection on
    GET /api/config, which must not turn every config read into a walk of the
    library.

    A missing `base` is emptiness, not an error — an un-created worlds dir
    holds no worlds. Any other OSError propagates: the caller has to be able
    to tell "nothing here" from "could not look", because those two answers
    send a first-run check in opposite directions.

    The listing is drained before it is filtered so that `except
    FileNotFoundError` covers only opening the directory. `iterdir` is lazy, so
    with the generator inside the `try` a failure part-way through the scan
    would land in the same handler and be reported as an empty store.
    """
    try:
        entries = list(base.iterdir())
    except FileNotFoundError:
        return False
    return any(safe_id(d.name) and _names_a_record(d, meta_name) for d in entries)


def _names_a_record(entry: Path, meta_name: str) -> bool:
    """Whether `entry` is a directory holding `meta_name`.

    Deliberately `stat` rather than `is_dir()` / `exists()`: those answer False
    for a permission or I/O error exactly as they do for absence, and here the
    two must stay apart. `any_child_record`'s caller reads "no records" as an
    empty store, so a library it merely could not open would be reported empty
    — and an empty store is precisely what sends a user with a full library
    into first-run setup. Only genuine absence is absence; everything else
    propagates to the caller's fail-closed handler.
    """
    try:
        if not stat.S_ISDIR(entry.stat().st_mode):
            return False
    except FileNotFoundError:
        return False        # removed between the listing and this probe
    try:
        (entry / meta_name).stat()
    except FileNotFoundError:
        return False
    return True


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def safe_id(value: object) -> bool:
    """Reject ids that could escape their parent directory (defense in depth).

    For every value this accepts, ``parent / value`` names a direct child of
    ``parent`` *and no other id names that same child*:

    - no path separator, no ``.`` or ``..``, no empty string (which would
      resolve to ``parent`` itself);
    - no colon -- on Windows a drive-relative id replaces the base outright
      (``Path("store") / "C:evil"`` is ``C:evil``), and any colon names an
      NTFS alternate data stream;
    - no trailing dot or space. Win32 trims those off a path component, so
      ``realm.`` and ``realm`` are one directory. Aliasing is as dangerous as
      escaping: `delete_world("realm.")` opened the live world but compared
      the raw ``realm.`` against campaigns' stored ``realm``, decided nothing
      used it, and deleted it (#259 review). Rejected on every platform --
      a store is synced between them, and an id must mean the same thing on
      both.

    Non-strings are rejected too, so ids read back out of on-disk JSON need no
    separate type check.

    Every id-to-path resolver in the store goes through this one function --
    it used to be copy-pasted per module, and the copies that were never made
    were exactly the resolvers that lacked the guard (#240). Enumeration has
    to agree with it: a listing that hands back an id this rejects turns its
    own next call into an error, so every listing filters on it too.
    """
    return (isinstance(value, str) and value not in ("", ".", "..")
            and not any(c in value for c in "/\\:")
            and value == value.rstrip(". "))


def natural_key(text: str) -> tuple:
    """Sort key that orders digit runs numerically: A2 before A10, SoL 2 before
    SoL 19. Case-insensitive. Splitting on digit runs keeps types aligned
    (str at even positions, int at odd), so mixed keys always compare."""
    return tuple(int(tok) if tok.isdigit() else tok.lower()
                 for tok in re.split(r"(\d+)", text))


def uniquify(base_id: str, exists: Callable[[str], bool]) -> str:
    """Return base_id, or base_id-2, base_id-3, ... until `exists` is False."""
    candidate = base_id
    n = 2
    while exists(candidate):
        candidate = f"{base_id}-{n}"
        n += 1
    return candidate
