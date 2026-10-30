"""Native store file formats and path layout: strict JSON, lossless envelopes, injective paths."""

from __future__ import annotations

import json

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.library import V1_ROOT_MARKERS
from grimoire.security import ResourcePath
from grimoire.store import InvalidEnvelope, LinkSpec, RecordEnvelope, SourceManifest, UnstorableIdentifier
from grimoire.store.envelope import EnvelopeError, parse_record, parse_source, record_document, source_document
from grimoire.store.jsonfmt import JsonError, freeze, parse_strict, render
from grimoire.store.layout import (
    CONFLICTS, RESERVED_ROOTS, ROOT_ENTRIES, address_parts, classify, conflict_parts, definition_parts,
    instance_parts, logical, manifest_parts,
)


# -- strict JSON -----------------------------------------------------------------

@pytest.mark.parametrize("data", [
    b'{"a": 1, "a": 2}',
    b"\xef\xbb\xbf{}",
    '{}'.encode("utf-16"),
    b'{"a": NaN}',
    b'{"a": -Infinity}',
    b'{"a": 1e400}',
    b'{"a": -1e400}',
    b'{"a": ' + b"9" * 5000 + b"}",
    b'{"a": "\\ud800"}',
    b'{"\\udfff": 1}',
    b'{"a": ' + b"[" * 100_000 + b"]" * 100_000 + b"}",
    b"\xff\xfe",
    b"[1, 2]",
    b"",
    b"{" + b" " * (8 * 1024 * 1024) + b"}",
], ids=["duplicate-key", "bom", "utf16", "nan", "neg-inf", "overflow", "neg-overflow", "huge-int",
        "surrogate-value", "surrogate-key", "deep", "bad-utf8", "array", "empty", "oversized"])
def test_strict_parse_maps_every_hazard_to_one_error(data):
    with pytest.raises(JsonError):
        parse_strict(data)


def test_render_is_deterministic_and_reads_back():
    value = {"b": [1, 2.5, None, True], "a": {"z": "ünïcode ✓", "y": []}}
    data = render(value)
    assert data == render(dict(reversed(list(value.items()))))
    assert data.endswith(b"\n") and data.index(b'"a"') < data.index(b'"b"')
    assert parse_strict(data) == freeze(value)


@pytest.mark.parametrize("value", [
    {"a": float("nan")}, {"a": float("inf")}, {"a": "\ud800"}, {1: "a"}, {"a": object()},
    {"a": 10 ** 4300}, {"a": {"b": {"c": set()}}},
], ids=["nan", "inf", "surrogate", "int-key", "object", "huge-int", "nested-set"])
def test_unwritable_caller_values_are_invalid_envelopes(value):
    with pytest.raises(InvalidEnvelope):
        render(value)
    with pytest.raises(InvalidEnvelope):
        RecordEnvelope(InstanceRef("c", "k", "i"), value).check_writable()


# -- envelopes --------------------------------------------------------------------

DEF = SourceRef("world:harbor", "character", "wren", "r1")
INST = InstanceRef("c1", "character", "wren-a")


def test_record_round_trip_preserves_unknown_fields_and_absent_name():
    envelope = RecordEnvelope(INST, {"hp": 3}, owner="human:owner", derived_from=DEF.unpinned(), base=DEF,
                              base_etag="sha256:" + "a" * 64, extra={"x-z": [1, {"k": None}]})
    document = json.loads(render(record_document(envelope, None)))
    assert "name" not in document and document["x-z"] == [1, {"k": None}]
    assert document["derived_from"] == "world:harbor/character/wren"
    again, previous = parse_record(parse_strict(render(record_document(envelope, None))))
    assert again == envelope and previous is None and again.name is None


def test_manifest_round_trip_keeps_heads_links_and_unknown_fields():
    manifest = SourceManifest("world:harbor", "human:owner", "Harbor", {"character/wren": "r2"},
                              (LinkSpec("world:harbor/character/wren", "alternate_portrayal",
                                        "addon:fan/character/wren-alt", extra={"note": "fan art"}),),
                              extra={"x-notes": "keep"})
    data = render(source_document(manifest, "sha256:" + "b" * 64))
    again, previous = parse_source(parse_strict(data))
    assert again == manifest and previous == "sha256:" + "b" * 64


@pytest.mark.parametrize("build", [
    lambda: RecordEnvelope(DEF.unpinned(), {}),
    lambda: RecordEnvelope(DEF, {}, derived_from=DEF),
    lambda: RecordEnvelope(INST, {}, base_etag="sha256:" + "a" * 64),
    lambda: RecordEnvelope(INST, {}, owner=" "),
    lambda: RecordEnvelope(INST, {}, extra={"previous": None}),
    lambda: RecordEnvelope(INST, {}, extra={"format": "x"}),
    lambda: SourceManifest("world:harbor", "", None),
    lambda: SourceManifest("v1:x", "human:owner"),
    lambda: LinkSpec("a/b/c", "same_individual", "a/b/d", extra={"subject": "x"}),
], ids=["unpinned-definition", "definition-derived", "etag-without-base", "blank-owner", "store-key-previous",
        "store-key-format", "no-owner", "reserved-source", "link-field-clash"])
def test_invalid_envelopes_are_refused(build):
    with pytest.raises(InvalidEnvelope):
        build()


@pytest.mark.parametrize("envelope", [
    RecordEnvelope(INST, {}, derived_from="Not A Ref"),
    RecordEnvelope(INST, {}, base=DEF.unpinned()),
    RecordEnvelope(INST, {}, base=DEF, base_etag="md5:x"),
], ids=["bad-derived", "unpinned-base", "bad-etag"])
def test_references_are_checked_when_written_not_when_read(envelope):
    with pytest.raises(InvalidEnvelope):
        envelope.check_writable()
    # the same text read back from a hand-edited file stays visible instead of being dropped
    assert parse_record(parse_strict(render(record_document(envelope, None))))[0] == envelope


@pytest.mark.parametrize("document, code", [
    ({"address": "campaign:c/k/i", "payload": 1}, "malformed"),
    ({"format": "grimoire.record/2", "address": "campaign:c/k/i", "payload": 1}, "unsupported_format"),
    ({"format": "grimoire.record/1", "address": "campaign:c/k/i"}, "malformed"),
    ({"format": "grimoire.record/1", "address": "Wren", "payload": 1}, "malformed"),
    ({"format": "grimoire.record/1", "address": "v1:a:b", "payload": 1}, "malformed"),
    ({"format": "grimoire.record/1", "address": "w/k/i@r", "payload": 1, "base": "w/k/i@r"}, "malformed"),
    ({"format": "grimoire.record/1", "address": "campaign:c/k/i", "payload": 1, "previous": "x"}, "malformed"),
])
def test_unreadable_records_name_their_problem(document, code):
    with pytest.raises(EnvelopeError) as error:
        parse_record(document)
    assert error.value.code == code


# -- layout ------------------------------------------------------------------------

def test_paths_for_each_family():
    assert manifest_parts("world:harbor") == ("sources", "world", "harbor", "_source", "manifest.json")
    assert definition_parts(DEF) == ("sources", "world", "harbor", "_source", "records", "character", "wren",
                                     "r1.json")
    assert instance_parts(INST) == ("campaigns", "c1", "instances", "character", "wren-a.json")


@pytest.mark.parametrize("address", [
    DEF, INST, SourceRef("world", "records", "x", "r.json"), SourceRef("world:records", "k", "e", "1"),
    SourceRef("a:b:c:d", "k", "e", "v-1.2"), InstanceRef("c", "k", "manifest"),
])
def test_classify_inverts_every_mapping(address):
    assert classify(address_parts(address)) == (("instance" if isinstance(address, InstanceRef)
                                                  else "definition"), address)
    if isinstance(address, SourceRef):
        assert classify(manifest_parts(address.source)) == ("manifest", address.source)


def test_a_source_and_its_sub_source_never_share_files():
    harbor = set(manifest_parts("world:harbor")[:-1])
    dlc = manifest_parts("world:harbor:dlc")
    assert dlc[:4] == ("sources", "world", "harbor", "dlc")
    assert not ResourcePath(dlc).is_within(ResourcePath(manifest_parts("world:harbor")[:-1]))
    assert "_source" in harbor


@pytest.mark.parametrize("parts", [
    ("sources", "World", "_source", "manifest.json"),
    ("sources", "_source", "manifest.json"),
    ("sources", "world", "_source", "records", "k", "e", "r1"),
    ("sources", "world", "_source", "records", "k", "e", "R1.json"),
    ("campaigns", "c", "instances", "k", "i.JSON"),
    ("campaigns", "c", "scenes", "k", "i.json"),
    ("sources", "world", "_source", "records", "k", "e", "r1.json", "x"),
])
def test_non_canonical_paths_are_not_records(parts):
    assert classify(parts) is None


@pytest.mark.parametrize("address", [
    SourceRef("world", "con", "x", "r1"), SourceRef("world", "k", "x.", "r1"),
    InstanceRef("nul", "k", "i"), SourceRef(":".join(["s"] * 30), "k", "e", "r"),
])
def test_unmappable_identifiers_are_refused_not_renamed(address):
    with pytest.raises(UnstorableIdentifier):
        address_parts(address)


def test_root_names_are_never_v1_markers_and_reserved_roots_are_not_used():
    assert not (ROOT_ENTRIES | RESERVED_ROOTS) & set(V1_ROOT_MARKERS)
    assert not ROOT_ENTRIES & RESERVED_ROOTS


def test_logical_addresses_are_resource_paths_and_conflicts_are_bounded():
    assert str(logical(address_parts(DEF))) == "sources/world/harbor/_source/records/character/wren/r1.json"
    with pytest.raises(UnstorableIdentifier):  # a file name needs room for ".json"
        instance_parts(InstanceRef("c", "k", "i" * 128))
    longest = InstanceRef("c" * 128, "k" * 128, "i" * 123)
    parts = conflict_parts(instance_parts(longest), "sha256:" + "f" * 64)
    assert parts[0] == CONFLICTS and len(parts) == 3 and len(parts[1]) == 16
    assert len(parts[2]) == 128 + 1 + 12  # bounded whatever the depth of the target


def test_nesting_is_capped_so_whatever_is_written_reads_back():
    deep = []
    for _ in range(150):
        deep = [deep]
    with pytest.raises(InvalidEnvelope):
        RecordEnvelope(INST, deep)
    ok = []
    for _ in range(90):
        ok = [ok]
    envelope = RecordEnvelope(INST, ok)
    envelope.check_writable()
    assert parse_record(parse_strict(render(record_document(envelope, None))))[0] == envelope
    with pytest.raises(JsonError):
        parse_strict(b'{"a": ' + b"[" * 150 + b"]" * 150 + b"}")
