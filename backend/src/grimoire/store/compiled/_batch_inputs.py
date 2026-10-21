"""Input validation: live stats, trusted source stamps and exact bytes for compute."""

from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from .. import readstats, statcache
from . import (
    _contracts,
    _encoding,
    _filesystem,
    _path_checks,
    _runtime,
    _sqlite_support,
)
from ._batch_reads import _Reads


class _Inputs(_Reads):
    """Validate caller-listed files and collections into hashed compute inputs."""

    def _persist_sources(self, kind: _contracts.Kind) -> bool:
        return _contracts.KINDS.get(kind.name) == kind and kind.allow_persisted_sources and self._usable()

    def _sample_clock(self, kind: _contracts.Kind) -> None:
        if not self._source_clock_checked and self._persist_sources(kind):
            self._source_clock_checked = True
            # Capture before ANY source stat. A slow read cannot age its own
            # input into trust, and a failed sample has no wall-time fallback.
            try:
                self._source_clock = _filesystem.filesystem_clock(self.root)
            except _sqlite_support._CACHE_ERRORS:
                _runtime._warn_once("clock")

    def validate(self, kind: _contracts.Kind, inputs: Sequence[_contracts.Input], *,
                 with_bytes: bool = False) -> tuple[_contracts.ValidatedInput, ...]:
        """Validate listed inputs; cold reads retain exact bytes for compute."""
        # Capture before source bytes even for False-policy derivations.
        # Persistence policy changes trust, never the delete-time boundary.
        if not inputs:
            return ()
        self._usable()
        self._sample_clock(kind)
        infos = self._input_infos(kind, inputs)
        return self._validate_infos(kind, inputs, infos, with_bytes=with_bytes)

    def validate_one(self, kind: _contracts.Kind, input: _contracts.Input, *,  # noqa: A002 -- Spec03 keyword contract
                     with_bytes: bool = False) -> _contracts.ValidatedInput:
        return self.validate(kind, [input], with_bytes=with_bytes)[0]

    def _input_infos(self, kind: _contracts.Kind, inputs: Sequence[_contracts.Input]) -> dict[Path, _contracts._SourceInfo | None]:
        found: dict[Path, _contracts._SourceInfo | None] = {}
        for item in inputs:
            if item.path not in found:
                try:
                    found[item.path] = _filesystem._source_info(self.root, item.path)
                except FileNotFoundError:
                    found[item.path] = None
                except OSError as error:
                    self._abort_input(error)
            if found[item.path] is None and not (_contracts.KINDS.get(kind.name) == kind
                                                and item.role in kind.absent_ok_roles):
                self._abort_input(FileNotFoundError())
        return found

    def _trusted_sources(self, kind: _contracts.Kind, infos: Sequence[_contracts._SourceInfo]) -> dict[Path, str]:
        if not self._persist_sources(kind):
            return {}
        eligible = [info for info in infos if _filesystem._eligible(info, self._source_clock)]
        if not eligible:
            return {}
        rows = self._source_rows([info.relative for info in eligible if info.relative is not None])
        trusted = {}
        for info in eligible:
            if info.relative is None:
                continue
            row = rows.get(info.relative)
            decoded = _encoding._decode_source(info.relative, row[:7]) if row is not None else None
            if row is not None and decoded is not None and decoded[0][1:] == info.stamp[1:]:
                readstats.bump("compiled.sources_hit")
                trusted[info.path] = decoded[1]
                self._remember_metadata(self._source_known, info.relative, row)
                self._stage_source(info.relative, info.stamp, decoded[1])
        return trusted if self._usable() else {}

    def _validate_infos(self, kind: _contracts.Kind, inputs: Sequence[_contracts.Input],
                        infos: Mapping[Path, _contracts._SourceInfo | None], *,
                        with_bytes: bool) -> tuple[_contracts.ValidatedInput, ...]:
        # File and collection forms share one stat-to-bytes trust rule. A
        # collection already captured its member stats while enumerating;
        # repeating them here could associate bytes with a different stamp.
        trusted = {} if with_bytes else self._trusted_sources(
            kind, [info for info in infos.values() if info is not None])
        values: dict[Path, tuple[str, statcache.Stamp | None, bytes | None]] = {}
        for path, info in infos.items():
            if info is None:
                values[path] = (_contracts.ABSENT, None, None)
                continue
            data = None
            digest = trusted.get(path)
            if digest is None:
                try:
                    data = path.read_bytes()
                except OSError as error:
                    self._abort_input(error)
                digest = hashlib.sha256(data).hexdigest()
                readstats.bump("compiled.sources_miss")
                readstats.bump("compiled.bytes_hashed", len(data))
                if (info.relative is not None and self._persist_sources(kind)
                        and _filesystem._eligible(info, self._source_clock)):
                    self._stage_source(info.relative, info.stamp, digest)
            values[path] = (digest, info.stamp, data)
        history = self.materialized(list(infos))
        return tuple(_contracts.ValidatedInput(item.role, item.path, *values[item.path], history.get(item.path, ()))
                     for item in inputs)

    def _collection_infos(self, kind: _contracts.Kind, collection: _contracts.Collection) -> dict[Path, _contracts._SourceInfo]:
        infos: dict[Path, _contracts._SourceInfo] = {}
        try:
            pending = [collection.path]
            while pending:
                parent = pending.pop()
                with os.scandir(parent) as listing:
                    entries = list(listing)
                for entry in entries:
                    path = Path(entry.path)
                    # Reparse directories/junctions must not become recursive
                    # traversal edges. Linked regular files still supply live
                    # bytes, and _source_info keeps them out of source trust.
                    if entry.is_dir(follow_symlinks=False):
                        if collection.recursive and not _path_checks._linked(entry.stat(follow_symlinks=False)):
                            pending.append(path)
                        continue
                    relative = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(self.root))).as_posix()
                    if kind.member_filter is not None and not kind.member_filter(relative):
                        continue
                    info = _filesystem._source_info(self.root, path)
                    if info.relative is None:
                        raise OSError('collection member has no contained path')
                    if info.regular:
                        infos[path] = info
        except (OSError, ValueError) as error:
            self._abort_input(OSError(str(error)))
        return infos

    def collection(self, kind: _contracts.Kind, collection: _contracts.Collection, *,
                   with_bytes: bool = False) -> _contracts.CollectionDigest:
        """List membership live; hash sorted relative paths and exact bytes."""
        self._usable()
        self._sample_clock(kind)
        try:
            directory = _filesystem._source_info(self.root, collection.path)
            if directory.relative is None or (collection.recursive and not directory.plain):
                raise OSError('collection has no plain contained directory')
        except (OSError, ValueError) as error:
            self._abort_input(OSError(str(error)))
        infos = self._collection_infos(kind, collection)
        ordered = sorted(infos, key=lambda path: infos[path].relative or "")  # all members have an info
        members = self._validate_infos(kind, [_contracts.Input(collection.role, path) for path in ordered],
                                       infos, with_bytes=with_bytes)
        # Its digest fits a composite key even when the manifest does not.
        readstats.bump("compiled.collection_digest")
        return _contracts.CollectionDigest(collection.role, _encoding._collection_hash(self.root, members), members)

    def known_hashes(self, kind: _contracts.Kind, paths: Sequence[Path]) -> dict[Path, str | None]:
        """Stat every supplied path; never read input bytes or record a source."""
        if not paths:
            return {}
        self._sample_clock(kind)
        infos = []
        for path in paths:
            with contextlib.suppress(OSError):
                infos.append(_filesystem._source_info(self.root, path))
        trusted = self._trusted_sources(kind, infos)
        return {path: trusted.get(path) for path in paths}
