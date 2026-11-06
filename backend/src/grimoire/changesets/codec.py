"""The change set's JSON form, ``grimoire.changeset/0`` (provisional).

This is the representation a later authoring worker submits (043 milestone 3), so decoding is
strict: an unknown key anywhere is refused rather than kept, because a proposal a reviewer
approves must not carry fields the reviewer never saw. Encoding is canonical and
``decode(encode(x)) == x``.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.json_value import thaw

from .model import ChangeSet, ChangeSetError, Precondition, RecordChange

SCHEMA = "grimoire.changeset/0"

_TOP = frozenset({"schema", "changeset_id", "source", "title", "author", "rationale", "changes", "preconditions"})
_TOP_REQUIRED = frozenset({"schema", "changeset_id", "source", "title", "author", "changes"})
_CHANGE = frozenset({"kind", "entity_id", "revision", "payload", "name", "base", "base_etag", "evidence"})
_CHANGE_REQUIRED = frozenset({"kind", "entity_id", "revision", "payload"})
_PRECONDITION = frozenset({"kind", "entity_id", "revision", "etag"})


def _object(value: object, label: str, allowed: frozenset[str], required: frozenset[str]) -> Mapping:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise ChangeSetError("invalid_field", f"{label} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ChangeSetError("invalid_field", f"{label} has unknown keys {unknown}")
    missing = sorted(required - set(value))
    if missing:
        raise ChangeSetError("invalid_field", f"{label} is missing {missing}")
    return value


def _list(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise ChangeSetError("invalid_field", f"{label} must be a list")
    return value


def encode(changeset: ChangeSet) -> dict[str, object]:
    """Plain JSON data, deterministic for equal change sets."""
    changes = []
    for change in changeset.changes:
        item: dict[str, object] = {"kind": change.kind, "entity_id": change.entity_id,
                                   "revision": change.revision, "payload": thaw(change.payload),
                                   "evidence": list(change.evidence)}
        if change.name is not None:
            item["name"] = change.name
        if change.base is not None:
            item["base"] = change.base
            item["base_etag"] = change.base_etag
        changes.append(item)
    return {
        "schema": SCHEMA, "changeset_id": changeset.changeset_id, "source": changeset.source,
        "title": changeset.title, "author": changeset.author, "rationale": changeset.rationale,
        "changes": changes,
        "preconditions": [{"kind": item.kind, "entity_id": item.entity_id, "revision": item.revision,
                           "etag": item.etag} for item in changeset.preconditions],
    }


def decode(data: object) -> ChangeSet:
    """A change set from JSON data, or ``ChangeSetError`` (``unsupported_schema``, ``invalid_field``,
    or any refusal the values make)."""
    if isinstance(data, Mapping) and "schema" in data and data["schema"] != SCHEMA:
        raise ChangeSetError("unsupported_schema", f"not {SCHEMA}: {data['schema']!r}")
    top = _object(data, "change set", _TOP, _TOP_REQUIRED)
    changes = []
    for index, raw in enumerate(_list(top["changes"], "changes")):
        item = _object(raw, f"changes[{index}]", _CHANGE, _CHANGE_REQUIRED)
        if "payload" in item and not isinstance(item["payload"], Mapping):
            raise ChangeSetError("invalid_field", f"changes[{index}].payload must be an object")
        changes.append(RecordChange(
            kind=item["kind"], entity_id=item["entity_id"], revision=item["revision"], payload=item["payload"],
            name=item.get("name"), base=item.get("base"), base_etag=item.get("base_etag"),
            evidence=tuple(_list(item.get("evidence", []), f"changes[{index}].evidence"))))
    preconditions = []
    for index, raw in enumerate(_list(top.get("preconditions", []), "preconditions")):
        item = _object(raw, f"preconditions[{index}]", _PRECONDITION, _PRECONDITION)
        preconditions.append(Precondition(item["kind"], item["entity_id"], item["revision"], item["etag"]))
    return ChangeSet(changeset_id=top["changeset_id"], source=top["source"], title=top["title"],
                     author=top["author"], changes=tuple(changes), rationale=top.get("rationale", ""),
                     preconditions=tuple(preconditions))
