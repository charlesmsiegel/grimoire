"""Read-only description/subject folding previews shared by planning and real-run verification."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import assets, image_store, image_subjects
from . import _contracts

# ---- metadata preview -------------------------------------------------------

def _sidecar(cache: dict[Path, dict], path: Path) -> dict:
    got = cache.get(path)
    if got is None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            raw = {}
        got = cache[path] = raw if isinstance(raw, dict) else {}
    return got


def _label(plan: _contracts.MigrationPlan, occ: _contracts.Occurrence) -> str:
    return f"{plan.rel(occ.dir)}/{occ.name}"


@dataclass
class _Sources:
    """Where each group's legacy metadata would come from."""
    #: image id -> [(label, sidecar path, key)] for description keys.
    texts: dict[str, list[tuple[str, Path, str]]] = field(default_factory=dict)
    #: image id -> [(scope, sidecar path, key)] for subject keys.
    subjects: dict[str, list[tuple[str, Path, str]]] = field(default_factory=dict)


def _sources(plan: _contracts.MigrationPlan) -> _Sources:
    out = _Sources()
    for item in plan.items:
        occ = item.occurrence
        out.texts.setdefault(item.image_id, []).append(
            (_label(plan, occ), occ.dir / assets.DESCRIPTIONS_FILE, occ.name))
        if occ.kind == "greetings" and occ.scope.startswith("world:"):
            # A campaign greeting's subjects are never folded (M5).
            out.subjects.setdefault(item.image_id, []).append(
                (occ.scope, occ.dir / image_subjects.SUBJECTS_FILE, occ.name))
    for occ, image_id in plan.metadata:
        if image_id is None or occ.metadata_only not in ("a", "c") or occ.sidecar is None:
            continue
        where = occ.dir / occ.sidecar
        if occ.sidecar == assets.DESCRIPTIONS_FILE:
            out.texts.setdefault(image_id, []).append((_label(plan, occ), where, occ.name))
        else:
            out.subjects.setdefault(image_id, []).append((occ.scope, where, occ.name))
    return out


def _key_text(plan: _contracts.MigrationPlan, cache: dict[Path, dict], where: Path,
              key: str) -> str | None:
    """A description key's text, or None when absent, or not foldable (then
    reported: it stays where it is)."""
    sidecar = _sidecar(cache, where)
    if key not in sidecar:
        return None
    value = sidecar[key]
    reason = ("not-a-string" if not isinstance(value, str)
              else "too-long" if len(value) > image_store.MAX_DESCRIPTION else None)
    if reason is not None:
        entry = {"path": plan.rel(where), "key": key, "reason": reason}
        if entry not in plan.overcap_keys:
            plan.overcap_keys.append(entry)
        return None
    return value


def _object_texts(obj: image_store.ImageObject | None) -> list[tuple[str, str]]:
    """``(text, from)`` from the object itself: its description, then the
    conflicts it already carries (never dropped here, M9)."""
    return [] if obj is None else _raw_texts(obj.raw)


def _raw_texts(raw: dict) -> list[tuple[str, str]]:
    """`_object_texts` of a sidecar's raw dict."""
    out = []
    desc = raw.get("description")
    if isinstance(desc, str):
        out.append((desc, _contracts._OBJECT))
    held = raw.get("description_conflicts")
    out.extend((c["text"], str(c.get("from", "")))
               for c in (held if isinstance(held, list) else [])
               if isinstance(c, dict) and isinstance(c.get("text"), str))
    return out


def _decide(texts: list[tuple[str, str]]) -> dict:
    """§11.D over ``(text, from)``: absent, one text (``""`` beside it or
    alone), or a conflict listing every distinct non-empty text once."""
    if not texts:
        return {}
    first: dict[str, str] = {}
    for text, src in texts:
        if text:
            first.setdefault(text, src)
    if not first:
        return {"text": ""}
    if len(first) == 1:
        return {"text": next(iter(first))}
    return {"conflicts": sorted(({"text": t, "from": s} for t, s in first.items()),
                                key=lambda c: (c["text"], c["from"]))}


def _describe(plan: _contracts.MigrationPlan, sources: _Sources) -> None:
    cache: dict[Path, dict] = {}
    for image_id, keys in sorted(sources.texts.items()):
        g = plan.groups[image_id]
        texts = _object_texts(image_store.read_fresh(image_id, root=plan.root))
        folded = 0
        for label, where, key in keys:
            text = _key_text(plan, cache, where, key)
            if text is not None:
                texts.append((text, label))
                folded += 1
        g.description = _decide(texts)
        if "conflicts" not in g.description:
            plan.descriptions_merged += folded


def _list_of_str(v: object) -> list[str] | None:
    return [s for s in v if isinstance(s, str)] if isinstance(v, list) else None


def _object_subjects(obj: image_store.ImageObject | None, scope: str) -> list[str] | None:
    """The object's subjects in `scope` when it was reviewed there (R1)."""
    if obj is None:
        return None
    reviews = obj.raw.get("reviews")
    listed = reviews.get("subjects") if isinstance(reviews, dict) else None
    if not isinstance(listed, list) or scope not in listed:
        return None
    assoc = obj.raw.get("associations")
    return [a["id"] for a in (assoc if isinstance(assoc, list) else [])
            if isinstance(a, dict) and a.get("scope") == scope
            and a.get("kind") == "character" and a.get("relation") == "subject"
            and isinstance(a.get("id"), str)]


def _subjects(plan: _contracts.MigrationPlan, sources: _Sources) -> None:
    cache: dict[Path, dict] = {}
    for image_id, keys in sorted(sources.subjects.items()):
        obj = image_store.read_fresh(image_id, root=plan.root)
        per_scope: dict[str, list[frozenset[str]]] = {}
        for scope, where, key in keys:
            got = _list_of_str(_sidecar(cache, where).get(key))
            if got is not None:
                per_scope.setdefault(scope, []).append(frozenset(got))
        for scope, sets in sorted(per_scope.items()):
            held = _object_subjects(obj, scope)
            if held is not None:
                sets.append(frozenset(held))
            plan.groups[image_id].subjects[scope] = sorted(frozenset().union(*sets))
            if len(set(sets)) > 1:
                plan.subject_disagreements.append(
                    {"image_id": image_id, "scope": scope,
                     "sets": sorted(sorted(s) for s in set(sets))})
