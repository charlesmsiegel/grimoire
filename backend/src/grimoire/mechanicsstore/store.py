"""An author's mechanics modules inside one library: isolated drafts and immutable published revisions (086).

    config/mechanics/drafts/<id>.json            the author's working copy, exactly as saved (any bytes)
    config/mechanics/modules/<id>/<digest>.json  one validated revision, named by its content digest

A draft is the author's own file and may be unfinished or malformed: saving never validates, so work is never
refused or lost, and nothing reads a draft but this store's check and publish. ``publish`` validates the draft
and, only when it has no problem at all, writes the revision file it describes; a module with any problem
cannot be published, so it can never be loaded or bound. A revision file is never rewritten: publishing the
same content again is a no-op, and an edited draft publishes a new file beside the old one, so a sheet or a
check recorded under the old revision keeps the module it was computed with. ``load`` refuses a revision file
whose content no longer has its digest (a hand edit), rather than serving another module under that name.

Every write is a temp file renamed over its target and names the etag of the draft it read (``None``: it saw
no draft); a draft moved on meanwhile is refused ``StaleWrite``. A crash leaves the old file or the new one and
at worst a temp file the next write of that folder, or ``recover``, sweeps. Links are never followed. All file
access goes through the ``FileOps`` seam so the writer runs under the fault harness (docs/TESTING.md). The
caller serializes writers.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grimoire.mechanics import MechanicsModule, ModuleInvalid, Problem, parse_module, revision_of
from grimoire.mechanics.expr import NAME
from grimoire.store import DiskOps, FileOps, StaleWrite, etag
from grimoire.store.diskops import is_link
from grimoire.store.jsonfmt import JsonError, parse_strict, render, thaw
from grimoire.store.layout import is_noise

CONFIG, MECHANICS, DRAFTS, MODULES = "config", "mechanics", "drafts", "modules"
SUFFIX = ".json"
TEMP_PREFIX, TEMP_SUFFIX = ".mechanics-", ".tmp"
#: A module is a few hundred lines; a larger file is not one.
MAX_DRAFT_BYTES = 256 * 1024
_DIGEST = 64


class MechanicsStoreError(ValueError):
    """A refused store operation: a short ``reason`` and a message a person can act on."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class Draft:
    """A draft exactly as saved, and the etag of those bytes."""

    data: bytes
    etag: str


@dataclass(frozen=True)
class DraftCheck:
    """What a draft would publish as: its problems, or the module when there are none."""

    etag: str
    problems: tuple[Problem, ...]
    module: MechanicsModule | None


def _module_id(module_id: object) -> str:
    if type(module_id) is not str or NAME.fullmatch(module_id) is None:
        raise MechanicsStoreError("invalid_id", "a module id is 1-64 lowercase letters, digits and _, "
                                  "starting with a letter")
    return module_id


def _digest(revision: object) -> str:
    if type(revision) is not str or not revision.startswith("sha256:") or len(revision) != 7 + _DIGEST \
            or any(c not in "0123456789abcdef" for c in revision[7:]):
        raise MechanicsStoreError("invalid_revision", "a revision is 'sha256:' and 64 lowercase hex digits")
    return revision[7:]


def _temp(name: str) -> bool:
    return name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)


def decode(data: bytes, expected_id: str | None = None) -> tuple[MechanicsModule | None, tuple[Problem, ...]]:
    """The module ``data`` holds, or why it holds none. JSON errors are problems of the whole (``$``)."""
    if len(data) > MAX_DRAFT_BYTES:
        return None, (Problem("$", f"a module is at most {MAX_DRAFT_BYTES} bytes"),)
    try:
        document = thaw(parse_strict(data))
    except JsonError as error:
        return None, (Problem("$", f"not a JSON object: {error}"),)
    try:
        module = parse_module(document)
    except ModuleInvalid as error:
        return None, error.problems
    if expected_id is not None and module.id != expected_id:
        return None, (Problem("id", f"is {module.id!r}, but this module is saved as {expected_id!r}"),)
    return module, ()


class MechanicsStore:
    """The mechanics modules of the library at ``root``. Holds no state between calls."""

    def __init__(self, root: str | os.PathLike[str], *, ops: FileOps | None = None,
                 check_read: Callable[[], None] | None = None, check_write: Callable[[], None] | None = None) -> None:
        """``check_read``/``check_write`` are the open library's own root guards (as for ``ShelfStore``)."""
        self.root = Path(root)
        self.ops: FileOps = ops if ops is not None else DiskOps()
        self._check_read = check_read or (lambda: None)
        self._check_write = check_write or (lambda: None)

    # -- paths -----------------------------------------------------------------------------------------------
    @staticmethod
    def draft_path(module_id: str) -> str:
        return f"{CONFIG}/{MECHANICS}/{DRAFTS}/{_module_id(module_id)}{SUFFIX}"

    @staticmethod
    def revision_path(module_id: str, revision: str) -> str:
        return f"{CONFIG}/{MECHANICS}/{MODULES}/{_module_id(module_id)}/{_digest(revision)}{SUFFIX}"

    def _refuse_links(self, relative: str) -> None:
        at = self.root
        steps = relative.split("/")
        for index, step in enumerate(steps):
            at = at / step
            if is_link(at):
                raise MechanicsStoreError("link", f"{'/'.join(steps[:index + 1])} is a link; the mechanics store "
                                          "does not follow links")

    def _read(self, relative: str) -> bytes | None:
        self._check_read()
        self._refuse_links(relative)
        path = self.root / relative
        if not self.ops.exists(path):
            return None
        return self.ops.read_bytes(path)

    def _write(self, relative: str, data: bytes, still: Callable[[], None]) -> None:
        """Put ``data`` at ``relative`` by temp file and rename; ``still`` re-checks just before the rename."""
        self._check_write()
        self._refuse_links(relative)
        target = self.root / relative
        directory = target.parent
        self.ops.make_dirs(directory)
        self._sweep(directory)
        temp = self.ops.make_temp(directory, prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX)
        try:
            self.ops.write_bytes(temp, data)
            still()
            self._check_write()
            self.ops.replace(temp, target)
        except Exception:  # an I/O error or a refusal; a dead process cleans nothing (``recover`` sweeps)
            try:
                self.ops.remove(temp)
            except OSError:
                pass
            raise

    def _sweep(self, directory: Path) -> None:
        if is_link(directory) or not self.ops.exists(directory):
            return
        for name in self.ops.list_dir(directory):
            if _temp(name):
                self.ops.remove(directory / name)

    # -- drafts ----------------------------------------------------------------------------------------------
    def draft(self, module_id: str) -> Draft | None:
        """The draft of ``module_id`` exactly as saved, or ``None``."""
        data = self._read(self.draft_path(module_id))
        return None if data is None else Draft(data, etag(data))

    def _unchanged(self, module_id: str, expected: str | None) -> Callable[[], None]:
        path = self.draft_path(module_id)

        def check() -> None:
            data = self._read(path)
            held = None if data is None else etag(data)
            if held != expected:
                raise StaleWrite([(path, "changed" if held is not None else "removed")])
        return check

    def save_draft(self, module_id: str, data: bytes, expected: str | None) -> str:
        """Keep ``data`` as the draft of ``module_id``, unvalidated; returns its etag. ``expected`` is the etag
        of the draft that was read (``None``: there was none)."""
        if type(data) is not bytes:
            raise MechanicsStoreError("invalid_draft", "a draft is the file's bytes")
        if len(data) > MAX_DRAFT_BYTES:
            raise MechanicsStoreError("too_large", f"a draft is at most {MAX_DRAFT_BYTES} bytes")
        if expected is not None and (type(expected) is not str or not expected.startswith("sha256:")):
            raise MechanicsStoreError("invalid_field", "expected must be the etag that was read, or null")
        unchanged = self._unchanged(module_id, expected)
        unchanged()
        current = self.draft(module_id)
        if current is not None and current.data == data:
            return current.etag
        self._write(self.draft_path(module_id), data, unchanged)
        return etag(data)

    def remove_draft(self, module_id: str, expected: str) -> None:
        """Remove the draft (published revisions stay)."""
        unchanged = self._unchanged(module_id, expected)
        unchanged()
        self._check_write()
        self.ops.remove(self.root / self.draft_path(module_id), durable=True)

    def check_draft(self, module_id: str) -> DraftCheck:
        """Every problem the draft has, or the module it would publish as. ``no_draft`` when there is none."""
        current = self.draft(module_id)
        if current is None:
            raise MechanicsStoreError("no_draft", f"there is no draft of {module_id}")
        module, problems = decode(current.data, module_id)
        return DraftCheck(current.etag, problems, module)

    # -- published revisions ---------------------------------------------------------------------------------
    def publish(self, module_id: str, expected: str) -> MechanicsModule:
        """Publish the draft read at ``expected`` as an immutable revision. ``ModuleInvalid`` lists every
        problem when it has any (nothing is written); ``StaleWrite`` when the draft moved on."""
        checked = self.check_draft(module_id)
        if checked.etag != expected:
            raise StaleWrite([(self.draft_path(module_id), "changed")])
        if checked.module is None:
            raise ModuleInvalid(checked.problems)
        module = checked.module
        data = render(module.document)
        path = self.revision_path(module_id, module.revision)
        held = self._read(path)
        if held is not None:
            if held != data:
                raise MechanicsStoreError("altered", f"{path} exists with other content; it is left as it is")
            return module
        self._write(path, data, self._unchanged(module_id, expected))
        return module

    def revisions(self, module_id: str) -> tuple[str, ...]:
        """The published revisions of ``module_id`` (by name; ``load`` verifies one). Sorted, not ordered by time:
        a revision is content, not a version number."""
        relative = f"{CONFIG}/{MECHANICS}/{MODULES}/{_module_id(module_id)}"
        self._check_read()
        self._refuse_links(relative)
        directory = self.root / relative
        if not self.ops.exists(directory):
            return ()
        found = []
        for name in self.ops.list_dir(directory):
            stem = name[:-len(SUFFIX)] if name.endswith(SUFFIX) else ""
            if len(stem) == _DIGEST and all(c in "0123456789abcdef" for c in stem):
                found.append("sha256:" + stem)
        return tuple(sorted(found))

    def load(self, module_id: str, revision: str) -> MechanicsModule:
        """The published module at exactly ``revision``. Refused when it was never published (``unpublished``),
        no longer validates (``invalid``) or no longer has that digest (``altered``): there is no fallback to
        another revision or to the draft."""
        path = self.revision_path(module_id, revision)
        data = self._read(path)
        if data is None:
            raise MechanicsStoreError("unpublished", f"{module_id} has no published revision {revision}")
        module, problems = decode(data, module_id)
        if module is None:
            raise MechanicsStoreError("invalid", f"{path} no longer validates: "
                                      + "; ".join(str(p) for p in problems[:3]))
        if module.revision != revision or revision_of(module.document) != revision:
            raise MechanicsStoreError("altered", f"{path} was changed after it was published; its content is "
                                      f"revision {module.revision}")
        return module

    def recover(self) -> None:
        """Remove what a write that died left behind: its temp files. Drafts and revisions are never touched."""
        self._check_write()
        base = self.root / CONFIG / MECHANICS
        if any(is_link(self.root / part) for part in (CONFIG, f"{CONFIG}/{MECHANICS}")):
            return
        self._sweep(base / DRAFTS)
        modules = base / MODULES
        if is_link(modules) or not self.ops.exists(modules):
            return
        for name in self.ops.list_dir(modules):
            if not is_noise(name) and NAME.fullmatch(name):
                self._sweep(modules / name)


__all__ = ["CONFIG", "DRAFTS", "MAX_DRAFT_BYTES", "MECHANICS", "MODULES", "Draft", "DraftCheck", "MechanicsStore",
           "MechanicsStoreError", "decode"]
