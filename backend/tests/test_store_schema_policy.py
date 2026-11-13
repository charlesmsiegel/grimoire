"""Shared metadata policy across opaque record families; synthetic records only."""

import pytest

from grimoire.core.identity import ImageRef, KnowledgeRef, ScopeRef
from grimoire.store.envelope import (
    AssetRecord, KnowledgeRecord, ScopeRecord, asset_record_document, knowledge_record_document,
    parse_asset_record, parse_knowledge_record, parse_scope_record, scope_record_document,
)
from grimoire.store.errors import InvalidEnvelope


class TextSubclass(str):
    pass


@pytest.fixture(params=[
    (ScopeRecord, ScopeRef("c1"), scope_record_document, parse_scope_record),
    (AssetRecord, ImageRef("campaign:c1", "x"), asset_record_document, parse_asset_record),
    (KnowledgeRecord, KnowledgeRef("c1", "event", "e1"), knowledge_record_document, parse_knowledge_record),
], ids=["scope", "asset", "knowledge"])
def family(request):
    return request.param


@pytest.mark.parametrize("schema", ["schema/1", "x" * 128, " leading ", "a\0b", r"a\0b", "\u200b"])
def test_schema_round_trip_preserves_text_and_unknown_fields(family, schema):
    cls, address, document, parse = family
    record = cls(address, schema, {"items": [1]}, owner="owner", extra={"future": {"flag": True}})
    wire = document(record) if cls is KnowledgeRecord else document(record, None)
    loaded, previous = parse(wire)
    assert previous is None
    assert loaded == record
    assert wire["schema"] == schema
    assert wire["future"] == {"flag": True}


@pytest.mark.parametrize("schema", [None, False, 1, b"schema", TextSubclass("schema"), "", " \t\n", "\u2003", "x" * 129])
def test_schema_policy_rejects_invalid_values(family, schema):
    cls, address, _, _ = family
    with pytest.raises(InvalidEnvelope) as error:
        cls(address, schema)
    assert str(error.value) == "schema must be nonempty text of at most 128 characters"


def test_address_validation_precedes_schema_and_owner(family):
    cls, _, _, _ = family
    with pytest.raises(InvalidEnvelope) as error:
        cls(object(), None, owner=0)
    assert "address must be" in str(error.value)


def test_schema_validation_precedes_owner(family):
    cls, address, _, _ = family
    with pytest.raises(InvalidEnvelope) as error:
        cls(address, "", owner=0)
    assert str(error.value) == "schema must be nonempty text of at most 128 characters"
