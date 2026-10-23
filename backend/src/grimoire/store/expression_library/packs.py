"""Bounded portable expression packs: inspect, choose mappings, publish once.

ZIP paths are logical names only; no member is extracted into a live actor.
A short-lived preview binds the exact upload, target and effective revision.
Publication revalidates everything and stages one atomic collection mutation.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import stat
import tempfile
import time
import unicodedata
import zipfile
from collections import Counter
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path

from .. import atomic, image_refs, locks, ziputil
from .. import paths as store_paths
from ..tracker import fields
from . import contracts, images, read, write

MAX_UPLOAD = 128 * 1024 * 1024
MAX_MEMBERS = 512
MAX_EXPANDED = 512 * 1024 * 1024
MAX_PIXELS = 128_000_000
MAX_METADATA = 1024 * 1024
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
now = time.time


def _cache() -> Path:
    identity = hashlib.sha256(str(store_paths.home()).encode()).hexdigest()
    return Path(tempfile.gettempdir()) / "grimoire-expression-previews" / identity


def _bounded(stream, cap: int) -> bytes:
    chunks = []
    size = 0
    while chunk := stream.read(min(1024 * 1024, cap + 1 - size)):
        size += len(chunk)
        if size > cap:
            raise contracts.ExpressionBudgetError("Expression pack exceeds its size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _checked_members(archive):
    if len(archive.infolist()) > MAX_MEMBERS or sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED:
        raise contracts.ExpressionBudgetError("Pack exceeds 512 members or 512 MiB expanded data")
    infos = ziputil.scan(archive, max_members=MAX_MEMBERS,
                         max_uncompressed=MAX_EXPANDED, err=contracts.ExpressionInvalidError)
    normalized = set()
    for info in archive.infolist():
        name = info.filename.rstrip("/") if info.is_dir() else info.filename
        ziputil.member_parts(name, err=contracts.ExpressionInvalidError)
        if "\x00" in info.orig_filename or info.flag_bits & 1:
            raise contracts.ExpressionInvalidError("Encrypted or unsafe ZIP member")
        if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise contracts.ExpressionInvalidError("Linked or special ZIP member")
        norm = unicodedata.normalize("NFC", name).replace("\\", "/").casefold()
        if norm in normalized:
            raise contracts.ExpressionInvalidError("Duplicate normalized ZIP member")
        normalized.add(norm)
        if info.file_size > max(1, info.compress_size) * 200:
            raise contracts.ExpressionBudgetError("ZIP compression ratio exceeds 200")
    return infos


def _pack_metadata(raw: bytes) -> dict:
    metadata = json.loads(raw)
    if not isinstance(metadata, dict) or type(metadata.get("format")) is not int or metadata["format"] != 1:
        raise contracts.ExpressionInvalidError("Unsupported expression metadata")
    outfits = metadata.get("outfits", {})
    if not isinstance(outfits, dict) or len(outfits) > MAX_MEMBERS:
        raise contracts.ExpressionInvalidError("Invalid expression outfit metadata")
    for outfit, patch in outfits.items():
        contracts.slug(outfit)
        contracts.metadata(patch)
    if metadata.get("default_outfit") is not None:
        contracts.slug(metadata["default_outfit"])
    return metadata


def _candidate(parts, mode: str, outfit_id: str | None, issues: list[str]):
    name = "/".join(parts)
    if mode == "grimoire":
        if len(parts) != 3 or parts[0] != "expressions":
            issues.append(f"Expected expressions/<outfit>/<expression>: {name}")
            return None
        outfit = contracts.slug(parts[1])
    else:
        if len(parts) != 1:
            issues.append(f"Expected a flat SillyTavern pack: {name}")
            return None
        outfit = contracts.slug(outfit_id)
    stem = Path(parts[-1]).stem.lower()
    alternate = re.fullmatch(r"(.+?)(?:[_-]\d+| \(\d+\))", stem)
    expression = alternate[1] if alternate else stem
    try:
        contracts.slug(expression)
    except contracts.ExpressionInvalidError:
        expression = None
        issues.append(f"Choose an expression name for: {name}")
    return {"file": name, "outfit": outfit, "expression": expression}


def _sprite(raw: bytes, name: str, issues: list[str]):
    try:
        return images.validate(raw)
    except contracts.UnsupportedSpriteError as exc:
        issues.append(f"Unsupported sprite: {name}: {exc}")
        return None, exc.pixels


def _inspect_members(archive, mode, outfit_id):
    rows, issues, payloads = [], [], {}
    metadata = {"format": 1, "outfits": {}}
    total = pixels = 0
    for info in _checked_members(archive):
        parts = ziputil.member_parts(info.filename, err=contracts.ExpressionInvalidError)
        name = "/".join(parts)
        is_metadata = mode == "grimoire" and name == "expressions/manifest.json"
        with archive.open(info) as stream:
            raw = _bounded(stream, MAX_METADATA if is_metadata else images.MAX_BYTES)
        total += len(raw)
        if total > MAX_EXPANDED:
            raise contracts.ExpressionBudgetError("Expanded pack exceeds 512 MiB")
        if is_metadata:
            metadata = _pack_metadata(raw)
            continue
        if Path(parts[-1]).suffix.lower() not in EXTENSIONS:
            issues.append(f"Unsupported file: {name}")
            continue
        ext, count = _sprite(raw, name, issues)
        pixels += count
        if pixels > MAX_PIXELS:
            raise contracts.ExpressionBudgetError("Decoded pack exceeds 128 megapixels")
        candidate = _candidate(parts, mode, outfit_id, issues)
        if ext and candidate:
            rows.append({**candidate, "ext": ext})
            payloads[name] = raw
    counts = Counter((r["outfit"], r["expression"]) for r in rows)
    for row in rows:
        row["ambiguous"] = row["expression"] not in fields.VISIBLE_MOODS or counts[row["outfit"], row["expression"]] > 1
    return rows, metadata, issues, payloads


def _inspect(data: bytes, mode: str, outfit_id: str | None) -> tuple[list[dict], dict, list[str], dict[str, bytes]]:
    if mode not in ("grimoire", "sillytavern"):
        raise contracts.ExpressionInvalidError("Unknown expression pack format")
    if mode == "sillytavern":
        contracts.slug(outfit_id)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return _inspect_members(archive, mode, outfit_id)
    except contracts.ExpressionInvalidError:
        raise
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, ValueError, TypeError, AttributeError) as exc:
        raise contracts.ExpressionInvalidError(str(exc)) from exc


def preview(target: contracts.ExpressionTarget, upload, *, mode: str, outfit_id: str | None = None) -> dict:
    collection = read.effective(target)
    data = _bounded(upload, MAX_UPLOAD)
    rows, metadata, issues, _ = _inspect(data, mode, outfit_id)
    if not rows and not metadata.get("outfits"):
        raise contracts.ExpressionInvalidError("Pack has no supported sprite images")
    token = secrets.token_hex(32)
    expires = now() + 1800
    record = {"target": asdict(target), "home": str(store_paths.home()),
              "effective_revision": collection["effective_revision"], "expires_at": expires,
              "sha256": hashlib.sha256(data).hexdigest(), "mode": mode, "outfit_id": outfit_id}
    directory = _cache()
    directory.mkdir(parents=True, exist_ok=True)
    # Keep preview storage bounded across abandoned dialogs. Cleanup touches
    # only generated token filenames, never anything named inside an archive.
    with locks.world_actor_lock("expression-preview-cache"):
        pending = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in pending[:-7]:
            if re.fullmatch(r"[0-9a-f]{64}", old.stem):
                old.with_suffix(".zip").unlink(missing_ok=True)
                old.unlink(missing_ok=True)
        atomic.write_bytes(directory / (token + ".zip"), data)
        atomic.write_bytes(directory / (token + ".json"), read.encoded(record))
    return {"token": token, "expires_at": expires, "effective_revision": collection["effective_revision"],
            "images": rows, "metadata": metadata, "issues": issues}


def preview_folder(target: contracts.ExpressionTarget, files: list[tuple[str, object]], *,
                   mode: str = "grimoire", outfit_id: str | None = None) -> dict:
    if not 1 <= len(files) <= MAX_MEMBERS:
        raise contracts.ExpressionInvalidError("Folder must contain at most 512 files")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, stream in files:
            ziputil.member_parts(name, err=contracts.ExpressionInvalidError)
            data = _bounded(stream, min(images.MAX_BYTES, MAX_UPLOAD - buffer.tell()))
            archive.writestr(name, data)
            if buffer.tell() > MAX_UPLOAD:
                raise contracts.ExpressionInvalidError("Expression folder exceeds 128 MiB")
    buffer.seek(0)
    return preview(target, buffer, mode=mode, outfit_id=outfit_id)


def publish(target: contracts.ExpressionTarget, token: str, selection: list[dict], *,
            expected_effective_revision: str) -> dict:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise contracts.ExpressionInvalidError("Invalid preview token")
    directory = _cache()
    try:
        with locks.world_actor_lock("expression-preview-cache"):
            record = json.loads(atomic.read_text(directory / (token + ".json")))
            with (directory / (token + ".zip")).open("rb") as stream:
                data = _bounded(stream, MAX_UPLOAD)
    except (OSError, ValueError) as exc:
        raise contracts.ExpressionInvalidError("Preview is unavailable; upload again") from exc
    if (record["expires_at"] < now() or record["target"] != asdict(target)
            or record["home"] != str(store_paths.home()) or record["sha256"] != hashlib.sha256(data).hexdigest()):
        raise contracts.ExpressionInvalidError("Preview expired or does not match this target/upload")
    current = read.effective(target)
    if record["effective_revision"] != expected_effective_revision or current["effective_revision"] != expected_effective_revision:
        raise contracts.ExpressionConflictError("Library changed after preview; preview again")
    rows, metadata, _, payloads = _inspect(data, record["mode"], record["outfit_id"])
    mutations = _selected_mutations(rows, metadata, payloads, selection, current)
    result = write.apply_effective(target, mutations, expected_effective_revision=expected_effective_revision)
    with locks.world_actor_lock("expression-preview-cache"), suppress(OSError):
        (directory / (token + ".json")).unlink(missing_ok=True)
        (directory / (token + ".zip")).unlink(missing_ok=True)
    return result



def _selected_mutations(rows, metadata, payloads, selection, current):
    files = {row["file"]: row for row in rows}
    if (not isinstance(selection, list) or len(selection) > MAX_MEMBERS
            or (not selection and not metadata.get("outfits"))):
        raise contracts.ExpressionInvalidError("Select sprites or import outfit metadata")
    mutations, destinations, chosen_files = [], set(), set()
    created = set(current["outfits"])
    # Empty outfits are authored collections too. Preserve their metadata on
    # portable round trips; existing destination settings remain user-owned.
    for outfit, patch in metadata.get("outfits", {}).items():
        if outfit not in created:
            mutations.append({"op": "create-outfit", "outfit": outfit, "metadata": patch})
            created.add(outfit)
    for item in selection:
        if not isinstance(item, dict):
            raise contracts.ExpressionInvalidError("Invalid import selection")
        name = item.get("file")
        outfit, expression = contracts.slug(item.get("outfit")), contracts.slug(item.get("expression"))
        if name not in files or name in chosen_files or (outfit, expression) in destinations:
            raise contracts.ExpressionInvalidError("Choose one source per expression destination")
        chosen_files.add(name)
        destinations.add((outfit, expression))
        existing = current["outfits"].get(outfit, {}).get("expressions", {})
        if expression in existing and item.get("overwrite") is not True:
            raise contracts.ExpressionConflictError(f"Explicit replacement required: {outfit}/{expression}")
        if outfit not in created:
            mutations.append({"op": "create-outfit", "outfit": outfit,
                              "metadata": metadata.get("outfits", {}).get(outfit, {"label": outfit})})
            created.add(outfit)
        mutations.append({"op": "put-expression", "outfit": outfit, "expression": expression,
                          "data": payloads[name], "ext": files[name]["ext"]})
    default = metadata.get("default_outfit")
    if default in created:
        mutations.append({"op": "set-default", "outfit": default})
    return mutations


def export(target: contracts.ExpressionTarget, destination: Path) -> None:
    with read.pinned(target) as collection:
        _export_collection(collection, destination)


def _export_collection(collection: dict, destination: Path) -> None:
    metadata = {"format": 1, "default_outfit": collection["default_outfit"], "outfits": {}}
    buffer = io.BytesIO()
    pixels = 0
    count = 1  # Include the metadata member in the importer's member budget.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for outfit, data in collection["outfits"].items():
            metadata["outfits"][outfit] = {k: data[k] for k in
                ("label", "clothing_aliases", "fallback_expression", "presentation")}
            for expression, row in data["expressions"].items():
                count += 1
                if count > MAX_MEMBERS:
                    raise contracts.ExpressionBudgetError("Expression pack exceeds 512 files")
                image = image_refs.resolve_ref(image_refs.Ref(expression, row["image_id"], None))
                if image is None:
                    raise contracts.ExpressionUnavailableError("A sprite has not synchronized; retry export later")
                with image.blob_path.open("rb") as stream:
                    raw = _bounded(stream, min(images.MAX_BYTES, MAX_UPLOAD - buffer.tell()))
                _, decoded_pixels = images.validate(raw)
                pixels += decoded_pixels
                if pixels > MAX_PIXELS:
                    raise contracts.ExpressionBudgetError("Decoded pack exceeds 128 megapixels")
                archive.writestr(f"expressions/{outfit}/{expression}.{image.ext}", raw)
        archive.writestr("expressions/manifest.json", read.encoded(metadata))
    if buffer.tell() > MAX_UPLOAD:
        raise contracts.ExpressionBudgetError("Expression pack exceeds 128 MiB")
    atomic.write_bytes(Path(destination), buffer.getvalue())
