"""Whole-pack administration: duplicate, create, delete, export and import.

Each publishing entry point stages a whole directory and publishes it with the
single rename in ``staging._publish``, under the global module-edit lock.
``recover`` and ``_campaign_locks`` are reached through the ``migrate`` module
object rather than imported by name: ``test_module_edit.py`` patches
``_campaign_locks`` to prove ``delete_module`` rejects a built-in *before*
taking every campaign lock, and a by-value import here would leave this file
calling the original with the assertion never reached.
"""

from __future__ import annotations

import io
import re
import shutil
import uuid
import zipfile
from pathlib import Path

from .. import atomic
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..modules import admin as modules_admin, pack as modules_pack
from . import migrate
from .staging import _M, _publish, _staging_root, locked, new_mid

MAX_MEMBERS = 2000
MAX_UNCOMPRESSED = 64 * 1024 * 1024


def duplicate_module(mid: str, name: str) -> str:
    """Copy any pack (builtin or user) to staging, publish by single rename
    into user_dir() under _M. Content copied as-is, valid or not."""
    with _M:
        migrate.recover()
        root, _source = modules_pack.pack_root(mid)   # raises ModuleNotFound
        new = new_mid(name or f"{mid} copy")
        nonce = uuid.uuid4().hex
        base = _staging_root() / nonce
        try:
            staging = base / new
            base.mkdir(parents=True)
            shutil.copytree(root, staging)
            if name:
                manifest = staging / "module.md"
                meta, body = parse_frontmatter(manifest.read_text(encoding="utf-8"))
                meta["name"] = name
                atomic.write_text(manifest, dump_frontmatter(meta, body))
            return _publish(staging, new)
        finally:
            shutil.rmtree(base, ignore_errors=True)


def create_module(name: str) -> str:
    """Staged scaffold + single-rename publish (a crash never leaves a
    partial live pack, unlike modules.create_module's in-place mkdir)."""
    with _M:
        migrate.recover()
        clean = " ".join(str(name).split()) or "Untitled"
        mid = new_mid(clean)
        nonce = uuid.uuid4().hex
        base = _staging_root() / nonce
        try:
            staging = base / mid
            staging.mkdir(parents=True)
            atomic.write_text(staging / "module.md", dump_frontmatter(
                {"name": clean, "description": "", "version": "0.1"}, ""))
            atomic.write_text(staging / "sheets.json", '{\n  "groups": {},\n  "sheet_types": {}\n}\n')
            return _publish(staging, mid)
        finally:
            shutil.rmtree(base, ignore_errors=True)


def delete_module(mid: str) -> None:
    """Locked deletion: a bound module vanishing (or a same-id user shadow
    falling through to the builtin) mid-LLM-computation must be impossible —
    the campaign locks are exactly what those consumers hold."""
    with _M:
        migrate.recover()
        _root, source = modules_pack.pack_root(mid)  # 404 before taking every lock
        if source != "user":
            raise modules_pack.ModuleError("built-in modules cannot be deleted")
        with migrate._campaign_locks():
            modules_admin.delete_module(mid)


def export_module(mid: str) -> bytes:
    with locked():
        root, _source = modules_pack.pack_root(mid)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    z.write(p, f"{mid}/{p.relative_to(root).as_posix()}")
        return buf.getvalue()


_DRIVE_OR_UNC = re.compile(r"^[A-Za-z]:|^[/\\]{2}")


def _member_parts(raw_name: str) -> list[str]:
    """Normalized path components for a zip member, or raise. Rejects
    absolute paths, drive-qualified and UNC names, and EMPTY / '.' / '..'
    components (codex plan review: 'pack//module.md' passes a naive split —
    the stripped remainder '/module.md' then resolves to the drive root).
    Also rejects any component containing ':' — the whole-name
    `_DRIVE_OR_UNC` check only anchors at the start, so a mid-path drive
    segment like 'pack/C:evil.txt' would otherwise pass here and then get
    collapsed onto the drive root by Path.joinpath, escaping staging before
    the containment recheck ever runs (review finding: all checks must
    happen before any extraction, not be caught mid-extraction)."""
    name = raw_name.replace("\\", "/")
    if _DRIVE_OR_UNC.match(name) or name.startswith("/"):
        raise modules_pack.ModuleError(f"unsafe zip entry: {raw_name}")
    parts = name.split("/")
    if len(parts) < 2 or any(p in ("", ".", "..") or ":" in p for p in parts):
        raise modules_pack.ModuleError(f"unsafe zip entry: {raw_name}")
    return parts


def _check_archive(z: zipfile.ZipFile) -> str:
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise modules_pack.ModuleError(f"zip has too many entries (> {MAX_MEMBERS})")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise modules_pack.ModuleError("zip expands past the size cap")
    roots: set[str] = set()
    seen_ci: set[str] = set()
    for i in infos:
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise modules_pack.ModuleError(f"zip contains a symlink: {i.filename}")
        parts = _member_parts(i.filename)
        roots.add(parts[0])
        ci = "/".join(parts).casefold()   # normalized + case-folded collisions
        if ci in seen_ci:
            raise modules_pack.ModuleError(f"case-colliding zip entries: {i.filename}")
        seen_ci.add(ci)
    if len(roots) != 1:
        raise modules_pack.ModuleError("zip must contain exactly one top-level module directory")
    return next(iter(roots))


def import_module(path: Path) -> str:
    with _M:
        migrate.recover()
        try:
            z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as e:
            raise modules_pack.ModuleError(f"not a zip archive: {e}")
        with z:
            src_root = _check_archive(z)
            mid = new_mid(src_root)
            nonce = uuid.uuid4().hex
            base = _staging_root() / nonce
            try:
                staging = base / mid
                staging.mkdir(parents=True)
                staging_resolved = staging.resolve()
                for i in z.infolist():
                    if i.is_dir():
                        continue
                    parts = _member_parts(i.filename)
                    dest = staging.joinpath(*parts[1:])
                    try:  # containment check (no Path.is_relative_to — 3.8-safe)
                        dest.resolve().relative_to(staging_resolved)
                    except ValueError:
                        raise modules_pack.ModuleError(f"unsafe zip entry: {i.filename}")
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        # atomic-ok: unpublished staging tree, published as a
                        # unit by _publish's single rename; a per-member
                        # temp+fsync would only slow large imports
                        dest.write_bytes(z.read(i))
                    except (OSError, RuntimeError, NotImplementedError,
                            zipfile.BadZipFile):
                        # pathological names (reserved device names CON/NUL,
                        # trailing dots/spaces on Windows) can raise a raw
                        # OSError from mkdir/write_bytes; z.read(i) itself
                        # can raise RuntimeError (encrypted member),
                        # NotImplementedError (unsupported compression), or
                        # BadZipFile (bad CRC/corrupt data) — none of those
                        # may escape uncontained (codex review finding).
                        raise modules_pack.ModuleError(f"unextractable zip entry: {i.filename}")
                pack = modules_pack.load_pack_at(staging, mid)
                if pack["errors"]:
                    raise modules_pack.ModuleError(
                        "invalid module pack: " + "; ".join(pack["errors"]))
                return _publish(staging, mid)
            finally:
                shutil.rmtree(base, ignore_errors=True)
