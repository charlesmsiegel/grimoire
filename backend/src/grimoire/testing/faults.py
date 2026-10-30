"""Deterministic fault injection around a confined, recording file seam.

The model is a *process crash*: operations that completed are on disk, the
crashed process does no further I/O, and nothing else is lost. Power loss
(unsynced data vanishing) is not simulated; see ``docs/TESTING.md``.
"""

from __future__ import annotations

import errno
import hashlib
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePath
from typing import Literal, Union

from .fileops import (
    MUTATING_KINDS, OP_KINDS, OsFileOps, PathArg, SandboxViolation, Tree, is_link, snapshot,
)

Timing = Literal["before", "after", "torn"]
_TIMINGS = ("before", "after", "torn")
_UNSAFE_NAME_PARTS = ("/", "\\", "..", ":")
_ABSENT = object()


class SimulatedCrash(BaseException):
    """The process died. ``BaseException`` so ``except Exception`` cannot absorb it."""


class OperationBudgetExceeded(BaseException):
    """A phase performed more file operations than its budget allows."""


@dataclass(frozen=True, slots=True)
class FileOp:
    """One recorded operation, described by root-relative POSIX paths."""

    index: int
    kind: str
    path: str
    target: str | None = None
    digest: str | None = None
    size: int | None = None

    @property
    def mutating(self) -> bool:
        return self.kind in MUTATING_KINDS

    def describe(self) -> str:
        text = f"#{self.index} {self.kind} {self.path}"
        if self.target is not None:
            text += f" -> {self.target}"
        if self.digest is not None:
            text += f" sha={self.digest} len={self.size}"
        return text


@dataclass(frozen=True, slots=True)
class Target:
    """Select the ``nth`` operation matching kind and root-relative path globs.

    Globs use :func:`fnmatch.fnmatchcase`, so ``*`` also matches ``/``.
    ``target`` matches the destination of a ``replace``.
    """

    kind: str | None = None
    path: str | None = None
    target: str | None = None
    nth: int = 1

    def __post_init__(self) -> None:
        if self.kind is not None and self.kind not in OP_KINDS:
            raise ValueError(f"unknown operation kind: {self.kind!r}")
        if isinstance(self.nth, bool) or not isinstance(self.nth, int) or self.nth < 1:
            raise ValueError("nth must be a positive integer")

    def matches(self, op: FileOp) -> bool:
        if self.kind is not None and op.kind != self.kind:
            return False
        if self.path is not None and not fnmatchcase(op.path, self.path):
            return False
        if self.target is not None and (op.target is None or not fnmatchcase(op.target, self.target)):
            return False
        return True


At = Union[int, Target]


def _check_at(at: At) -> None:
    if isinstance(at, Target):
        return
    if isinstance(at, bool) or not isinstance(at, int) or at < 0:
        raise ValueError("a fault target is a non-negative op index or a Target")


@dataclass(frozen=True, slots=True)
class Crash:
    """Kill the process before, after, or halfway through (``torn``) an operation."""

    at: At
    timing: Timing = "before"

    def __post_init__(self) -> None:
        _check_at(self.at)
        if self.timing not in _TIMINGS:
            raise ValueError(f"timing must be one of {_TIMINGS}")
        if self.timing == "torn" and isinstance(self.at, Target) and self.at.kind not in (None, "write_bytes"):
            raise ValueError("a torn crash can only target write_bytes")


@dataclass(frozen=True, slots=True)
class Fail:
    """Raise a non-fatal ``OSError`` instead of performing an operation."""

    at: At
    error: OSError | None = None

    def __post_init__(self) -> None:
        _check_at(self.at)
        if self.error is not None and not isinstance(self.error, OSError):
            raise ValueError("Fail.error must be an OSError")


@dataclass(frozen=True, slots=True)
class Interleave:
    """Run ``action(ops, root)`` (an external edit) just before an operation."""

    at: At
    action: Callable[..., object]

    def __post_init__(self) -> None:
        _check_at(self.at)
        if not callable(self.action):
            raise ValueError("Interleave.action must be callable")


Fault = Union[Crash, Fail, Interleave]


def validate_plan(plan: Sequence[Fault]) -> tuple[Fault, ...]:
    plan = tuple(plan)
    for fault in plan:
        if not isinstance(fault, (Crash, Fail, Interleave)):
            raise ValueError(f"not a fault: {fault!r}")
    if sum(isinstance(fault, (Crash, Fail)) for fault in plan) > 1:
        raise ValueError("a plan may contain at most one Crash or Fail")
    return plan


def nth_match(trace: Sequence[FileOp], at: At) -> int | None:
    """Index of the operation ``at`` selects in a finished trace."""
    if not isinstance(at, Target):
        return at if at < len(trace) else None
    seen = 0
    for op in trace:
        if at.matches(op):
            seen += 1
            if seen == at.nth:
                return op.index
    return None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _eligible(fault: Fault, op: FileOp) -> bool:
    if isinstance(fault, Crash) and fault.timing == "torn":
        return op.kind == "write_bytes" and bool(op.size)
    return True


class RootedFileOps:
    """File operations confined to one root, recorded, budgeted and faultable.

    State flags are set *before* the corresponding exception is raised, so the
    harness can classify a phase correctly even if the writer converts or
    swallows the exception.
    """

    def __init__(
        self,
        root: PathArg,
        *,
        phase: str = "action",
        plan: Sequence[Fault] = (),
        max_ops: int = 10_000,
        record: bool = True,
    ) -> None:
        self.root = os.path.realpath(os.fspath(root))
        if not os.path.isdir(self.root):
            raise ValueError(f"test root is not a directory: {self.root}")
        self.phase = phase
        self.plan = validate_plan(plan)
        self.max_ops = max_ops
        self.record = record
        self.trace: list[FileOp] = []
        self.shadow: Tree | None = snapshot(self.root) if record else None
        self.sandbox_violation: str | None = None
        self.budget_exceeded = False
        self.crashed = False
        self.failed = False
        self.dead = False
        self.fault_index: int | None = None  # earliest fired fault of any kind
        self.crash_index: int | None = None  # the fired Crash or Fail
        self.fault_timing: str | None = None
        self.interleaved_paths: set[str] = set()
        self.interleave_error: Exception | None = None
        self._fired = [False] * len(self.plan)
        self._matches = [0] * len(self.plan)
        self._count = 0
        self._os = OsFileOps()

    # -- harness state -------------------------------------------------
    @property
    def fired(self) -> bool:
        return all(self._fired)

    @property
    def unfired(self) -> tuple[Fault, ...]:
        return tuple(fault for fault, fired in zip(self.plan, self._fired) if not fired)

    # -- confinement ---------------------------------------------------
    def _resolve(self, path: PathArg, *, mutating: bool, allow_root: bool = False) -> str:
        if self.dead:
            raise SimulatedCrash("operation attempted after the simulated process died")
        raw = os.fspath(path)
        if not isinstance(raw, str):
            raise TypeError("paths must be str or os.PathLike[str]")
        if not os.path.isabs(raw):
            raw = os.path.join(self.root, raw)
        normalized = os.path.normpath(raw)
        if not self._inside(normalized):
            self._sandbox(f"path escapes test root: {os.fspath(path)!r}")
        if mutating and not allow_root and os.path.normcase(normalized) == os.path.normcase(self.root):
            self._sandbox("refusing to mutate the test root itself")
        # The normalized path is what executes; the raw walk also catches ``link/../x``.
        for spelling in (normalized, raw):
            prefix = ""
            for part in PurePath(spelling).parts:
                prefix = os.path.join(prefix, part) if prefix else part
                candidate = os.path.normpath(prefix)
                if self._inside(candidate, strict=True) and is_link(prefix):
                    self._sandbox(f"link inside test root: {self._rel(candidate)}")
        return normalized

    def _inside(self, path: str, *, strict: bool = False) -> bool:
        root, path = os.path.normcase(self.root), os.path.normcase(path)
        try:
            common = os.path.commonpath([root, path])
        except ValueError:  # different drives
            return False
        return common == root and not (strict and path == root)

    def _sandbox(self, message: str) -> None:
        if self.sandbox_violation is None:
            self.sandbox_violation = message
        raise SandboxViolation(message)

    def _rel(self, absolute: str) -> str:
        return Path(os.path.relpath(absolute, self.root)).as_posix()

    # -- the operation pipeline ---------------------------------------
    def _op(self, kind: str, path: str, *, target: str | None = None, data: bytes | None = None,
            execute: Callable[[], object]) -> object:
        if self.dead:
            raise SimulatedCrash("operation attempted after the simulated process died")
        if self._count >= self.max_ops:
            self.budget_exceeded = True
            self.dead = True
            raise OperationBudgetExceeded(f"more than {self.max_ops} file operations in {self.phase}")
        self._count += 1
        op = FileOp(
            index=len(self.trace), kind=kind, path=self._rel(path),
            target=None if target is None else self._rel(target),
            digest=None if data is None else _digest(data),
            size=None if data is None else len(data),
        )
        if self.record:
            self.trace.append(op)
        interleaves, fatal = self._match(op)
        for fault in interleaves:
            before = snapshot(self.root) if self.record else {}
            try:
                fault.action(RootedFileOps(self.root, phase=f"{self.phase}-interleave", record=False),
                             Path(self.root))
            except Exception as error:  # a fixture bug: stop the writer, let the harness re-raise
                self.interleave_error = error
                self.dead = True
                raise SimulatedCrash("interleaved action failed") from error
            if self.record:
                self.shadow = snapshot(self.root)
                self.interleaved_paths |= {key for key in set(before) | set(self.shadow)
                                           if before.get(key, _ABSENT) != self.shadow.get(key, _ABSENT)}
        if fatal is not None:
            self.crash_index = op.index
        if isinstance(fatal, Fail):
            self.failed = True
            self.fault_timing = "fail"
            raise fatal.error or OSError(errno.EIO, f"injected I/O error at {self.phase}:{op.describe()}")
        if isinstance(fatal, Crash):
            self.fault_timing = fatal.timing
            self.crashed = True
            self.dead = True
            crash = SimulatedCrash(f"{self.phase}:{op.describe()} @{fatal.timing}")
            try:
                if fatal.timing == "torn":
                    assert data is not None
                    with open(path, "wb") as handle:  # partial data, never fsynced
                        handle.write(data[: len(data) // 2])
                    self._shadow_apply(kind, path, target, data[: len(data) // 2])
                elif fatal.timing == "after":
                    self._shadow_apply(kind, path, target, data, execute())
            except Exception as error:  # the operation itself failed, then the process died
                raise crash from error
            raise crash
        result = execute()
        self._shadow_apply(kind, path, target, data, result)
        return result

    def _match(self, op: FileOp) -> tuple[list[Interleave], Crash | Fail | None]:
        interleaves: list[Interleave] = []
        fatal: Crash | Fail | None = None
        for i, fault in enumerate(self.plan):
            if self._fired[i]:
                continue
            if not _eligible(fault, op):
                continue
            if isinstance(fault.at, Target):
                if not fault.at.matches(op):
                    continue
                self._matches[i] += 1
                if self._matches[i] != fault.at.nth:
                    continue
            elif fault.at != op.index:
                continue
            self._fired[i] = True
            if self.fault_index is None:
                self.fault_index = op.index
            if isinstance(fault, Interleave):
                interleaves.append(fault)
            else:
                fatal = fault
        return interleaves, fatal

    def _shadow_apply(self, kind: str, path: str, target: str | None, data: bytes | None,
                      result: object = None) -> None:
        if self.shadow is None:
            return
        if kind == "write_bytes":
            self.shadow[self._rel(path)] = data
        elif kind == "make_temp":
            self.shadow[self._rel(path)] = b""
        elif kind == "replace":
            assert target is not None
            source, destination = self._rel(path), self._rel(target)
            if source != destination:
                moved = self.shadow.pop(source, _ABSENT)
                if moved is _ABSENT:  # source made outside the seam: mediation check reports it
                    self.shadow.pop(destination, None)
                else:
                    self.shadow[destination] = moved
        elif kind == "remove":
            self.shadow.pop(self._rel(path), None)
        elif kind == "make_dirs":
            parts = PurePath(self._rel(path)).parts
            for i in range(1, len(parts) + 1):
                if parts[:i] == (".",):
                    continue
                self.shadow.setdefault(PurePath(*parts[:i]).as_posix(), None)

    # -- the FileOps protocol -------------------------------------------
    def read_bytes(self, path: PathArg) -> bytes:
        resolved = self._resolve(path, mutating=False)
        return self._op("read_bytes", resolved, execute=lambda: self._os.read_bytes(resolved))  # type: ignore[return-value]

    def exists(self, path: PathArg) -> bool:
        resolved = self._resolve(path, mutating=False)
        return self._op("exists", resolved, execute=lambda: self._os.exists(resolved))  # type: ignore[return-value]

    def list_dir(self, path: PathArg) -> list[str]:
        resolved = self._resolve(path, mutating=False)
        return self._op("list_dir", resolved, execute=lambda: self._os.list_dir(resolved))  # type: ignore[return-value]

    def write_bytes(self, path: PathArg, data: bytes) -> None:
        resolved = self._resolve(path, mutating=True)
        data = bytes(data)
        self._op("write_bytes", resolved, data=data, execute=lambda: self._os.write_bytes(resolved, data))

    def make_temp(self, directory: PathArg, prefix: str = "", suffix: str = "") -> str:
        """Deterministic: the lowest free ``<prefix>~tmpN<suffix>``, created exclusively."""
        for part in (prefix, suffix):
            if any(unsafe in part for unsafe in _UNSAFE_NAME_PARTS):
                self._sandbox(f"unsafe temporary name part: {part!r}")
        folder = self._resolve(directory, mutating=False)
        number = 1
        while os.path.lexists(os.path.join(folder, f"{prefix}~tmp{number}{suffix}")):
            number += 1
        path = self._resolve(os.path.join(folder, f"{prefix}~tmp{number}{suffix}"), mutating=True)

        def create() -> str:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
            os.close(os.open(path, flags, 0o600))
            return path

        self._op("make_temp", path, execute=create)
        return path

    def replace(self, src: PathArg, dst: PathArg) -> None:
        source = self._resolve(src, mutating=True)
        destination = self._resolve(dst, mutating=True)
        if os.path.isdir(source):
            raise ValueError("the harness does not support replacing directories")
        self._op("replace", source, target=destination, execute=lambda: self._os.replace(source, destination))

    def remove(self, path: PathArg) -> None:
        resolved = self._resolve(path, mutating=True)
        self._op("remove", resolved, execute=lambda: self._os.remove(resolved))

    def make_dirs(self, path: PathArg) -> None:
        resolved = self._resolve(path, mutating=True, allow_root=True)  # makedirs(root) is a no-op
        self._op("make_dirs", resolved, execute=lambda: self._os.make_dirs(resolved))
