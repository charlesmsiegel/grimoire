"""Move expression collections as complete semantic layers, never merged files.

This physical-directory boundary is shared by overlay and archive operations;
it deliberately knows no actor resolver, avoiding an overlay dependency cycle.
The final manifest is published after every immutable reference is in place.
"""

import copy
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .. import assets, atomic, filesystem_links, image_refs
from . import contracts, pins, storage


def materialize(directories: list[Path], destination: Path) -> None:
    revision_id = uuid.uuid4().hex
    with pinned_directories([*directories, destination]) as captured, pins.hold(destination, revision_id):
        layers = [("local" if d == destination else "inherited", d, *captured[d]) for d in directories]
        _materialize(layers, destination, captured[destination][0], revision_id)


def _materialize(layers: list, destination: Path, before: dict, revision_id: str) -> None:
    if not any(m["revision"] for _, _, m, _ in layers):
        return
    # Flatten the complete authored layers; hide flags must remain reversible.
    merged = storage.merge(layers, include_hidden=True)
    manifest = storage.empty()
    manifest.update({k: copy.deepcopy(merged[k]) for k in
                     ("default_outfit", "hidden_outfits", "hidden_expressions")})
    manifest["revision"] = revision_id
    revision = destination / "revisions" / manifest["revision"]
    for outfit, data in merged["outfits"].items():
        manifest["outfits"][outfit] = {k: copy.deepcopy(data[k]) for k in
            ("label", "clothing_aliases", "fallback_expression", "presentation")}
        for label, row in data["expressions"].items():
            assets.link_in(revision / outfit, label, row["image_id"])
    placements = {p.relative_to(revision).as_posix(): p.read_bytes()
                  for p in revision.glob("*/image-refs/*.json")}
    manifest["placement_count"] = len(placements)
    manifest["previous_revision"] = before["revision"]
    manifest["content_digest"] = storage.checksum(manifest, placements)
    with assets.sidecar_locks_held(*[(d, "manifest.json") for _, d, _, _ in layers], (destination, "manifest.json")):
        if storage.layer(destination)[0] != before:
            raise contracts.ExpressionConflictError("Destination expressions changed during copy")
        for _, directory, captured, _ in layers:
            if storage.layer(directory)[0] != captured:
                # The old effective collection remains intact; caller can retry.
                raise contracts.ExpressionConflictError("Expression collection changed during copy")
        destination.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(destination / "manifest.json", storage.encoded(manifest))


def copy_local(source: Path, destination: Path) -> None:
    revision_id = uuid.uuid4().hex
    with pinned_directories([source, destination]) as captured, pins.hold(destination, revision_id):
        _copy_local(source, destination, captured[source], captured[destination][0], revision_id)


def _copy_local(source: Path, destination: Path, captured: tuple, before: dict, revision_id: str) -> None:
    manifest, refs = captured
    if not manifest["revision"]:
        return
    revision = manifest["revision"]
    manifest = copy.deepcopy(manifest)
    manifest["revision"] = revision_id
    manifest["previous_revision"] = before["revision"]
    placements = {}
    for outfit, expression in refs:
        relative = Path("revisions") / revision / outfit / "image-refs" / (expression + ".json")
        name = Path(outfit) / "image-refs" / (expression + ".json")
        data = (source / relative).read_bytes()
        placements[name.as_posix()] = data
        target = destination / "revisions" / manifest["revision"] / name
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(target, data)
    manifest["content_digest"] = storage.checksum(manifest, placements)
    with assets.sidecar_locks_held((destination, "manifest.json")):
        if storage.layer(destination)[0] != before:
            raise contracts.ExpressionConflictError("Destination expressions changed during copy")
        destination.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(destination / "manifest.json", storage.encoded(manifest))


def directories(root: Path) -> list[Path]:
    return sorted(directory for kind in ("characters", "pcs")
                  for directory in root.glob(f"{kind}/*/assets/*/expressions")
                  if not filesystem_links.linked(root, directory))


@contextmanager
def pinned_directories(directories: list[Path]):
    with ExitStack() as leases:
        with assets.sidecar_locks_held(*[(d, "manifest.json") for d in directories]):
            captured = {d: storage.layer(d) for d in directories}
            for directory, (manifest, _) in captured.items():
                leases.enter_context(pins.hold(directory, manifest["revision"]))
        yield captured


def snapshot(root: Path) -> tuple[dict[Path, bytes], dict[str, str]]:
    """Capture exact complete active revisions for an archive, plus required objects."""
    files, objects = {}, {}
    for directory in directories(root):
        manifest, refs = storage.layer(directory)
        if not manifest["revision"]:
            continue
        files[directory / "manifest.json"] = storage.encoded(manifest)
        for (outfit, expression), ref in refs.items():
            assert ref.image is not None  # storage.layer requires an image identity
            path = directory / "revisions" / manifest["revision"] / outfit / "image-refs" / (expression + ".json")
            if filesystem_links.linked(root, path):
                raise contracts.ExpressionUnavailableError("Linked expression placement")
            files[path] = path.read_bytes()
            objects[ref.image] = str(path.relative_to(root))
        placements = {p.relative_to(directory / "revisions" / manifest["revision"]).as_posix(): data
                      for p, data in files.items() if p != directory / "manifest.json" and p.is_relative_to(directory)}
        if storage.checksum(manifest, placements) != manifest["content_digest"]:
            raise contracts.ExpressionUnavailableError("Expression changed during archive snapshot")
    return files, objects


def rehome(root: Path, id_map: dict[str, str], contained: set[str]) -> None:
    """Validate a staged layer before rewriting placements to ingested identities."""
    for directory in directories(root):
        manifest, refs = storage.layer(directory)
        if not manifest["revision"]:
            continue
        revision = directory / "revisions" / manifest["revision"]
        for (outfit, expression), ref in refs.items():
            assert ref.image is not None  # storage.layer requires an image identity
            local = id_map.get(ref.image, ref.image if ref.image in contained else None)
            if local is None:
                raise contracts.ExpressionInvalidError("Expression archive does not contain a referenced image")
            if local != ref.image:
                image_refs.write(revision / outfit, expression, local, focus=ref.focus)
        placements = {p.relative_to(revision).as_posix(): p.read_bytes()
                      for p in revision.glob("*/image-refs/*.json")}
        manifest["content_digest"] = storage.checksum(manifest, placements)
        atomic.write_bytes(directory / "manifest.json", storage.encoded(manifest))
