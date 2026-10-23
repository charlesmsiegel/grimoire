"""Read one immutable placement revision, rejecting incomplete synchronization.

The digest covers metadata and exact placement bytes. Missing blobs are ordinary
unavailable sprites; missing placement metadata makes the entire layer unavailable
so sync order cannot masquerade as deliberate removal.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .. import assets, atomic, filesystem_links, image_refs
from .. import paths as store_paths
from . import contracts


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def checksum(manifest: dict, placements: dict[str, bytes]) -> str:
    header = {k: v for k, v in manifest.items() if k not in ("content_digest", "placement_count")}
    digest = hashlib.sha256(encoded(header))
    for name, data in sorted(placements.items()):
        digest.update(encoded([name, len(data)]))
        digest.update(data)
    return digest.hexdigest()


def empty() -> dict:
    return {"format": 1, "revision": None, "default_outfit": None, "outfits": {},
            "hidden_outfits": [], "hidden_expressions": {}}


def layer(directory: Path) -> tuple[dict, dict[tuple[str, str], image_refs.Ref]]:
    with assets.sidecar_locks_held((directory, "manifest.json")):
        return _layer(directory)


def _labels(value) -> None:
    if not isinstance(value, list):
        raise contracts.ExpressionUnavailableError("Expected a list of expression labels")
    for label in value:
        contracts.slug(label)


def _validate(manifest: dict) -> None:
    if (type(manifest.get("format")) is not int or manifest["format"] != 1
            or not re.fullmatch(r"[0-9a-f]{32}", manifest.get("revision", ""))):
        raise contracts.ExpressionUnavailableError("Unsupported expression collection format")
    if type(manifest.get("placement_count")) is not int or manifest["placement_count"] < 0:
        raise contracts.ExpressionUnavailableError("Invalid expression placement count")
    for outfit, meta in manifest["outfits"].items():
        contracts.slug(outfit)
        contracts.metadata(meta)
    if manifest.get("default_outfit") is not None:
        contracts.slug(manifest["default_outfit"])
    _labels(manifest["hidden_outfits"])
    for outfit, labels in manifest["hidden_expressions"].items():
        contracts.slug(outfit)
        _labels(labels)


def _layer(directory: Path) -> tuple[dict, dict[tuple[str, str], image_refs.Ref]]:
    if filesystem_links.linked(store_paths.home(), directory / "manifest.json"):
        raise contracts.ExpressionUnavailableError("Linked expression collection")
    try:
        raw = atomic.read_text(directory / "manifest.json")
    except FileNotFoundError:
        return empty(), {}
    try:
        manifest = json.loads(raw)
        _validate(manifest)
        revision = directory / "revisions" / manifest["revision"]
        placements = {}
        refs = {}
        for path in sorted(revision.glob("*/image-refs/*.json")):
            outfit, expression = contracts.slug(path.parent.parent.name), contracts.slug(path.stem)
            if outfit not in manifest["outfits"] or filesystem_links.linked(directory, path):
                raise contracts.ExpressionUnavailableError("Invalid expression placement")
            placements[path.relative_to(revision).as_posix()] = path.read_bytes()
            ref = image_refs.read(path.parent.parent, expression)
            if ref is None or ref.image is None:
                raise contracts.ExpressionUnavailableError("Unreadable expression placement")
            refs[outfit, expression] = ref
        if len(placements) != manifest["placement_count"] or checksum(manifest, placements) != manifest["content_digest"]:
            raise contracts.ExpressionUnavailableError("Expression revision has not completely synchronized")
        return manifest, refs
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise contracts.ExpressionUnavailableError(str(exc)) from exc



def _expressions(prior: dict, refs: dict, outfit: str, source: str, revision: str) -> dict:
    expressions = dict(prior.get("expressions", {}))
    for (oid, label), ref in sorted(refs.items()):
        if oid != outfit:
            continue
        resolved = image_refs.resolve_ref(ref)
        expressions[label] = {"image_id": ref.image, "available": resolved is not None,
            "token": resolved.blob_sha256 if resolved else None,
            "source": "overridden" if source == "local" and label in expressions else source,
            "revision": revision}
    return expressions


def merge(layers: list) -> dict:
    outfits: dict = {}
    default = None
    hidden_outfits = set()
    hidden_expressions: dict[str, set[str]] = {}
    for source, _, manifest, refs in layers:
        default = manifest["default_outfit"] or default
        for outfit in manifest["hidden_outfits"]:
            outfits.pop(outfit, None)
            hidden_outfits.add(outfit)
        for outfit, meta in sorted(manifest["outfits"].items()):
            if outfit in hidden_outfits:
                continue
            prior = outfits.get(outfit, {})
            expressions = _expressions(prior, refs, outfit, source, manifest["revision"])
            outfits[outfit] = {**contracts.merge_metadata(outfit, prior, meta),
                              "expressions": expressions,
                              "source": "overridden" if source == "local" and prior else source}
        for outfit, labels in manifest["hidden_expressions"].items():
            hidden_expressions.setdefault(outfit, set()).update(labels)
            for label in labels:
                if outfit in outfits:
                    outfits[outfit]["expressions"].pop(label, None)
    outfits = dict(sorted(outfits.items()))
    default, warnings = _defaults(outfits, default)
    return {"default_outfit": default, "outfits": outfits, "hidden_outfits": sorted(hidden_outfits),
            "hidden_expressions": {k: sorted(v) for k, v in hidden_expressions.items()}, "warnings": warnings}


def _defaults(outfits, default):
    warnings = []
    if default not in outfits:
        if default is not None:
            warnings.append(f"Default outfit {default} is unavailable; using another outfit when possible.")
        default = next((oid for oid, data in outfits.items()
                        if any(row["available"] for row in data["expressions"].values())), next(iter(outfits), None))
    for outfit, data in outfits.items():
        fallback = data["fallback_expression"]
        if not data["expressions"].get(fallback, {}).get("available"):
            warnings.append(f"Fallback sprite {outfit}/{fallback} is unavailable; other available sprites will be used.")
    return default, warnings
