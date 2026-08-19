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
import shutil
import uuid
import zipfile
from pathlib import Path

from .. import atomic, ziputil
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..modules import admin as modules_admin
from ..modules import pack as modules_pack
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


def _member_parts(raw_name: str) -> list[str]:
    """A pack member's path components: ``<root>/<file>``, so at least two.

    The checks themselves live in ``store.ziputil`` -- world bundles (#54) need
    the identical defense, and keeping a second copy here is how one of the two
    ends up missing a hardening the other got.
    """
    return ziputil.member_parts(raw_name, min_parts=2, err=modules_pack.ModuleError)


def _check_archive(z: zipfile.ZipFile) -> tuple[str, list[zipfile.ZipInfo]]:
    """Validate the whole archive; return its single top-level directory and
    the file members to extract. The members come back rather than being
    re-derived so extraction cannot walk a different set than the one that was
    checked -- every check happens before any file is written."""
    infos = ziputil.scan(z, max_members=MAX_MEMBERS, max_uncompressed=MAX_UNCOMPRESSED,
                         min_parts=2, err=modules_pack.ModuleError)
    roots = ziputil.top_level_names(infos)
    if len(roots) != 1:
        raise modules_pack.ModuleError("zip must contain exactly one top-level module directory")
    return next(iter(roots)), infos


def import_module(path: Path) -> str:
    with _M:
        migrate.recover()
        try:
            z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as e:
            raise modules_pack.ModuleError(f"not a zip archive: {e}")
        with z:
            src_root, infos = _check_archive(z)
            mid = new_mid(src_root)
            nonce = uuid.uuid4().hex
            base = _staging_root() / nonce
            try:
                staging = base / mid
                staging.mkdir(parents=True)
                ziputil.extract(z, infos, staging, strip=1,
                                err=modules_pack.ModuleError)
                pack = modules_pack.load_pack_at(staging, mid)
                if pack["errors"]:
                    raise modules_pack.ModuleError(
                        "invalid module pack: " + "; ".join(pack["errors"]))
                return _publish(staging, mid)
            finally:
                shutil.rmtree(base, ignore_errors=True)
