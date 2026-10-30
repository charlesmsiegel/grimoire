"""The inventory manifest, its review summary and the guarded report writer."""

from __future__ import annotations

import json
import os
import stat
import unicodedata
from collections import Counter
from pathlib import Path

from . import _layout, _text
from ._context import SEVERITY, Context

SCHEMA = "grimoire.v1-inventory/1"
CONTRACT = "grimoire-0.2.1"
BLOCKING_SHOWN = 50


def entry_rows(ctx: Context, legacy) -> list[dict]:
    rows = []
    for path in sorted(ctx.snap.entries):
        entry = ctx.snap.entries[path]
        rows.append({"path": path, "legacy_id": legacy(path), "kind": entry.kind,
                     "disposition": entry.disposition, "family": entry.layout.family,
                     "size": entry.size, "sha256": entry.sha256, "reason": entry.reason})
    return rows


def fingerprint(entries: list[dict]) -> str:
    material = [[e["path"], e["disposition"], e["size"], e["sha256"] or ""]
                for e in entries if e["disposition"] != _layout.EXCLUDED]
    return _text.sha256(json.dumps(material, sort_keys=True, ensure_ascii=True).encode("ascii"))


def path_findings(ctx: Context) -> None:
    for note in ctx.snap.notes:
        ctx.finding(note.code, note.path, detail=note.detail)
    for path, entry in ctx.snap.entries.items():
        if entry.disposition == _layout.UNKNOWN:
            ctx.finding("unknown_entry", path)
            if (variant := _layout.case_variant(tuple(path.split("/")))) is not None:
                ctx.finding("case_variant_name", path, detail=variant)
        if entry.disposition == _layout.INTERPRETED and path not in ctx.snap.read_paths:
            ctx.finding("uninterpreted", path)
    normal, case = _text.collision_groups(ctx.snap.entries)
    for group in normal:
        ctx.finding("normalization_collision", *group)
    for group in case:
        ctx.finding("case_collision", *group)


def counts(manifest: dict) -> dict:
    images = manifest["images"]
    findings = Counter(f["severity"] for f in manifest["findings"])
    return {
        "entries": dict(sorted(Counter(e["disposition"] for e in manifest["entries"]).items())),
        "worlds": len(manifest["worlds"]),
        "campaigns": len(manifest["campaigns"]),
        "actors": len(manifest["actors"]),
        "portrayals": sum(len(a["versions"]) for a in manifest["actors"]),
        "records": len(manifest["records"]),
        "scenes": len(manifest["scenes"]),
        "posts": sum(len(s["posts"]) for s in manifest["scenes"]),
        "image_objects": len(images["objects"]),
        "image_blobs": len(images["blobs"]),
        "placements": dict(sorted(Counter(p["status"] for p in images["placements"]).items())),
        "legacy_images": len(images["legacy_images"]),
        "expression_layers": dict(sorted(Counter(x["status"] for x in images["expression_layers"]).items())),
        "image_references": dict(sorted(Counter(r["status"] for r in images["references"]).items())),
        "findings": {k: findings.get(k, 0) for k in ("blocking", "warning", "info")},
        "losses": len(manifest["losses"]),
        "unresolved": len(manifest["unresolved"]),
    }


def finalize(manifest: dict, ctx: Context) -> dict:
    manifest["findings"] = [f.as_json() for f in sorted(ctx.findings)]
    manifest["unresolved"] = sorted(ctx.unresolved, key=lambda u: (u["path"], u["what"], u["reason"]))
    losses = [{"path": e["path"], "what": e["family"], "detail": ctx.loss_details.get(e["path"], "")}
              for e in manifest["entries"] if e["disposition"] == _layout.UNSUPPORTED]
    losses += ctx.losses
    manifest["losses"] = sorted(losses, key=lambda x: (x["path"], x["what"]))
    blocked = any(f["severity"] == "blocking" for f in manifest["findings"])
    manifest["status"] = "blocked" if blocked else "ready_for_review"
    manifest["counts"] = counts(manifest)
    return manifest


def header(archive_id: str, markers: dict, source: Path, was_link: bool, destination: Path) -> dict:
    return {"schema": SCHEMA, "archive_id": archive_id,
            "source_format": {"contract": CONTRACT, "markers": markers},
            "unicode_version": unicodedata.unidata_version,
            "roots": {"source": str(source), "source_root_was_link": was_link,
                      "destination": str(destination)},
            "inference": {"provider_calls": 0}}


def render_json(manifest: dict) -> bytes:
    return (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
            + "\n").encode("ascii")


# ---- summary ----------------------------------------------------------------------------

def render_summary(manifest: dict, *, mode: str = "full") -> str:
    """The review summary. `counts` mode prints no names or paths at all."""
    c = manifest["counts"]
    lines = [f"Grimoire 0.2.1 inventory ({manifest['schema']})",
             f"archive: {_text.escape(manifest['archive_id'])}",
             f"status: {manifest['status']}",
             f"provider calls: {manifest['inference']['provider_calls']}", "",
             "counts:"]
    for key in ("worlds", "campaigns", "actors", "portrayals", "records", "scenes", "posts",
                "image_objects", "image_blobs", "legacy_images", "losses", "unresolved"):
        lines.append(f"  {key}: {c[key]}")
    for key in ("entries", "placements", "expression_layers", "image_references", "findings"):
        detail = ", ".join(f"{k} {v}" for k, v in c[key].items()) or "none"
        lines.append(f"  {key}: {detail}")
    blocking = [f for f in manifest["findings"] if f["severity"] == "blocking"]
    lines += ["", f"blocking findings: {len(blocking)}"]
    if mode == "counts":
        for code, n in sorted(Counter(f["code"] for f in blocking).items()):
            lines.append(f"  {code}: {n}")
    else:
        for f in blocking[:BLOCKING_SHOWN]:
            where = ", ".join(_text.escape(p) for p in f["paths"]) or "-"
            lines.append(f"  {f['code']}: {where}")
        if len(blocking) > BLOCKING_SHOWN:
            lines.append(f"  +{len(blocking) - BLOCKING_SHOWN} more")
    for severity, label in (("warning", "warnings"), ("info", "info")):
        by_code = Counter(f["code"] for f in manifest["findings"] if f["severity"] == severity)
        lines.append(f"{label}: " + (", ".join(f"{k} {v}" for k, v in sorted(by_code.items())) or "none"))
    lines.append("")
    if manifest["status"] == "blocked":
        lines.append("next step: fix the blocking findings in a copy of the source and scan again; "
                     "an importer must refuse a blocked inventory.")
    else:
        lines.append("next step: review warnings, losses and unresolved items in the report; "
                     "nothing has been imported.")
    return "\n".join(lines) + "\n"


# ---- report writer ------------------------------------------------------------------------

class ReportWriteError(OSError):
    """The report could not be published; no partial report was left behind."""


def parent_identity(target: Path) -> tuple[int, int]:
    info = os.stat(target.parent)
    return info.st_dev, info.st_ino


def _publish(temp: str, target: Path, dfd: int | None, *, overwrite: bool) -> None:
    """Move the finished temp onto `target`. Without `overwrite` this must not clobber a
    file created meanwhile: a hard link fails if the name exists (Windows' rename does too)."""
    src = temp if dfd is not None else target.parent / temp
    dst = target.name if dfd is not None else target
    fds = {"src_dir_fd": dfd, "dst_dir_fd": dfd} if dfd is not None else {}
    if overwrite:
        os.replace(src, dst, **fds)
        return
    if os.name == "nt":
        os.rename(src, dst)  # refuses an existing destination
        return
    try:
        os.link(src, dst, follow_symlinks=False, **fds)
    except FileExistsError:
        raise ReportWriteError(f"{target} appeared before writing; nothing was replaced") from None
    os.unlink(src, dir_fd=dfd) if dfd is not None else os.unlink(src)


def write_report(target: Path, data: bytes, expected_parent: tuple[int, int], *, overwrite: bool) -> None:
    """Publish `data` at `target` atomically: an exclusive 0600 temp beside it, then a rename."""
    temp = f".{target.name}.{os.getpid()}.tmp"
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0))
    use_fd = os.rename in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")
    dfd = None
    created = False
    try:
        if use_fd:
            dfd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
            info = os.fstat(dfd)
        else:
            info = os.stat(target.parent)
        if (info.st_dev, info.st_ino) != expected_parent:
            raise ReportWriteError(f"the report directory {target.parent} changed before writing")
        fd = os.open(temp, flags, 0o600, dir_fd=dfd) if use_fd else os.open(target.parent / temp, flags, 0o600)
        created = True
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            existing = (os.stat(target.name, dir_fd=dfd, follow_symlinks=False) if use_fd
                        else os.lstat(target))
        except FileNotFoundError:
            existing = None
        if existing is not None and not (overwrite and stat.S_ISREG(existing.st_mode)):
            raise ReportWriteError(f"{target} appeared or changed before writing; nothing was replaced")
        _publish(temp, target, dfd, overwrite=overwrite)
        created = False
    except OSError as error:
        if created:
            try:
                if use_fd:
                    os.unlink(temp, dir_fd=dfd)
                else:
                    os.unlink(target.parent / temp)
            except OSError:
                pass
        if isinstance(error, ReportWriteError):
            raise
        raise ReportWriteError(f"cannot write the report {target}: {error.strerror or error}") from error
    finally:
        if dfd is not None:
            os.close(dfd)


__all__ = ["SCHEMA", "SEVERITY", "render_json", "render_summary", "write_report"]
