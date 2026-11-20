"""The voice casting's file inside one library (083): ``config/voices/casting.json``.

The casting is the person's own configuration of how the library's text is read aloud: which local voice speaks
for the narrator, a character or one campaign's actor. It lives under the root name ``config``, which the native
store reserves and never reads, beside the shelf (docs/SHELF.md): a copy or backup of the library carries it, and
``load()`` is unaffected. Device sync does not carry it yet (the voices it names are this device's engines).

Reading never fails: a file that is not exactly a casting is reported as a ``Problem`` with its reason, and the
library has no casting meanwhile. A write names ``expected``, the etag of the file it read (``None``: it read
none), and is refused ``StaleWrite`` when the file moved on; a file that cannot be read is never written over
(``unreadable_record``). A write is one temp file renamed over the target: a crash leaves the old casting or the
new one, and at worst a temp file the next write sweeps. All file access goes through the ``FileOps`` seam; the
caller serializes writers (the app's one writer lock).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grimoire.store import DiskOps, FileOps, StaleWrite, etag
from grimoire.store.diskops import is_link
from grimoire.store.jsonfmt import JsonError, parse_strict, render, thaw
from grimoire.voicecast import Casting, CastingError, casting_from_document, casting_to_document

CONFIG, VOICES, FILE = "config", "voices", "casting.json"
PATH = f"{CONFIG}/{VOICES}/{FILE}"
TEMP_PREFIX, TEMP_SUFFIX = ".casting-", ".tmp"
#: A casting of a thousand entries with the longest notes is under this (about 700 KiB); a larger file is not one.
MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Problem:
    path: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class CastingView:
    """The casting as it is now (empty when there is no file), its etag (``None``: no file) and any problem."""

    casting: Casting
    etag: str | None
    problem: Problem | None = None


class CastingStore:
    """The casting of the library at ``root``. Holds no state between calls."""

    def __init__(self, root: str | os.PathLike[str], *, ops: FileOps | None = None,
                 check_read: Callable[[], None] | None = None, check_write: Callable[[], None] | None = None) -> None:
        """``check_read``/``check_write`` are the open library's root guards (``LibraryStore.check_root`` and
        ``check_destination``), so a root swapped for a link after it was opened is never followed."""
        self.root = Path(root)
        self.ops: FileOps = ops if ops is not None else DiskOps()
        self._check_read = check_read or (lambda: None)
        self._check_write = check_write or (lambda: None)

    @property
    def _folder(self) -> Path:
        return self.root / CONFIG / VOICES

    def _linked(self) -> str | None:
        steps = [CONFIG, VOICES, FILE]
        at = self.root
        for index, step in enumerate(steps):
            at = at / step
            if is_link(at):
                return "/".join(steps[:index + 1])
        return None

    def _bytes(self) -> bytes | None:
        self._check_read()
        linked = self._linked()
        if linked is not None:
            raise CastingError("link", f"{linked} is a link; the casting does not follow links")
        target = self._folder / FILE
        if not self.ops.exists(target):
            return None
        return self.ops.read_bytes(target)

    @staticmethod
    def _decode(data: bytes) -> Casting:
        if len(data) > MAX_BYTES:
            raise CastingError("too_large", f"larger than {MAX_BYTES} bytes")
        try:
            document = thaw(parse_strict(data))
        except JsonError as error:
            raise CastingError("malformed", str(error)) from None
        return casting_from_document(document)

    def view(self) -> CastingView:
        """The casting, or an empty one and the reason the file could not be read."""
        try:
            data = self._bytes()
            if data is None:
                return CastingView(Casting(), None)
            return CastingView(self._decode(data), etag(data))
        except CastingError as error:
            return CastingView(Casting(), None, Problem(PATH, error.reason, str(error)))
        except OSError as error:
            return CastingView(Casting(), None, Problem(PATH, "unreadable", error.strerror or type(error).__name__))

    def write(self, change: Callable[[Casting], Casting], expected: str | None) -> CastingView:
        """Apply ``change`` to the casting read now and keep the result, if the file is still at ``expected``.
        An empty casting removes the file."""
        if expected is not None and (type(expected) is not str or not expected.startswith("sha256:")):
            raise CastingError("invalid_field", "expected must be the etag that was read, or null")
        current = self._bytes()
        if current is not None:
            try:
                held = self._decode(current)
            except CastingError as error:
                raise CastingError("unreadable_record", f"{PATH} cannot be read ({error.reason}: {error}); mend or "
                                   "remove that file first") from None
        else:
            held = Casting()
        self._check(current, expected)
        casting = change(held)
        if type(casting) is not Casting:
            raise CastingError("invalid_casting", "a change gives a Casting")
        data = render(casting_to_document(casting)) if casting.entries else None
        if data is not None and len(data) > MAX_BYTES:
            raise CastingError("too_large", f"the casting would be larger than {MAX_BYTES} bytes")
        if data == current:
            return CastingView(casting, None if data is None else etag(data))
        self._check_write()
        target = self._folder / FILE
        if data is None:
            self.ops.remove(target, durable=True)
            return CastingView(casting, None)
        self.ops.make_dirs(self._folder)
        linked = self._linked()
        if linked is not None:
            raise CastingError("link", f"{linked} is a link; the casting does not follow links")
        self._sweep()
        temp = self.ops.make_temp(self._folder, prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX)
        try:
            self.ops.write_bytes(temp, data)
            self._check(self._bytes(), expected)  # an edit made meanwhile wins: this write is refused
            self._check_write()
            self.ops.replace(temp, target)
        except Exception:
            try:
                self.ops.remove(temp)
            except OSError:
                pass
            raise
        return CastingView(casting, etag(data))

    @staticmethod
    def _check(data: bytes | None, expected: str | None) -> None:
        held = None if data is None else etag(data)
        if held != expected:
            raise StaleWrite([(PATH, "changed" if held is not None else "removed")])

    def _sweep(self) -> None:
        if self._linked() is not None or not self.ops.exists(self._folder):
            return
        for name in self.ops.list_dir(self._folder):
            if name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX):
                self.ops.remove(self._folder / name)

    def recover(self) -> None:
        """Remove what a write that died left behind: its temp file. The casting is never touched."""
        self._check_write()
        self._sweep()


__all__ = ["CONFIG", "FILE", "MAX_BYTES", "PATH", "VOICES", "CastingStore", "CastingView", "Problem"]
