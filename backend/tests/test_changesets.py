"""World Change Set values and their JSON form (043): what a reviewer approves is exactly representable."""

from __future__ import annotations

import pytest

from grimoire.changesets import (
    MAX_CHANGES, SCHEMA, ChangeSet, ChangeSetError, Precondition, RecordChange, decode, encode, unified,
)

ETAG = "sha256:" + "a" * 64
HARBOR = "world:harbor"


def docks(**overrides) -> RecordChange:
    values = dict(kind="location", entity_id="saltmarch-docks", revision="r1", payload={"traits": {"spatial": {}}},
                  name="Saltmarch Docks", evidence=("session 12",))
    values.update(overrides)
    return RecordChange(**values)


def guild(**overrides) -> RecordChange:
    values = dict(kind="organization", entity_id="ashen-guild", revision="r2", payload={"seat": "docks"},
                  base="r1", base_etag=ETAG)
    values.update(overrides)
    return RecordChange(**values)


def changeset(*changes, **overrides) -> ChangeSet:
    values = dict(changeset_id="cs-1", source=HARBOR, title="Move the guild", author="human:owner",
                  changes=changes or (docks(), guild()), rationale="the hall burned",
                  preconditions=(Precondition("location", "old-hall", "r1", ETAG),))
    values.update(overrides)
    return ChangeSet(**values)


def test_the_json_form_round_trips_exactly():
    value = changeset()
    data = encode(value)
    assert data["schema"] == SCHEMA
    assert decode(data) == value
    assert encode(decode(data)) == data
    assert [change.action for change in value.changes] == ["create", "revise"]


def test_payload_types_are_kept_strictly():
    # true, 1 and 1.0 are different authored values; a reviewer must never see one become another
    value = changeset(docks(payload={"a": True, "b": 1, "c": 1.0}))
    assert decode(encode(value)).changes[0].payload == value.changes[0].payload
    assert docks(payload={"a": 1}).payload != docks(payload={"a": True}).payload


@pytest.mark.parametrize("edit, reason", [
    (lambda d: d.update(extra=1), "invalid_field"),  # an unseen field never rides along
    (lambda d: d["changes"][0].update(sneaky="x"), "invalid_field"),
    (lambda d: d["preconditions"][0].update(note="x"), "invalid_field"),
    (lambda d: d.update(schema="grimoire.changeset/9"), "unsupported_schema"),
    (lambda d: d.pop("changes"), "invalid_field"),
    (lambda d: d["changes"][0].update(payload=[1]), "invalid_field"),
    (lambda d: d["changes"][1].pop("base_etag"), "invalid_field"),
    (lambda d: d.update(changes=[]), "empty_changeset"),
    (lambda d: d.update(source="campaign:c1"), "invalid_field"),
])
def test_decoding_is_strict(edit, reason):
    data = encode(changeset())
    edit(data)
    with pytest.raises(ChangeSetError) as caught:
        decode(data)
    assert caught.value.reason == reason


@pytest.mark.parametrize("build, reason", [
    (lambda: changeset(docks(), docks(name="again")), "duplicate_target"),
    (lambda: changeset(docks(), docks(kind="character")), "duplicate_target"),  # one id, two kinds
    (lambda: changeset(preconditions=(Precondition("organization", "ashen-guild", "r1", ETAG),)),
     "precondition_on_target"),
    (lambda: changeset(preconditions=(Precondition("location", "old-hall", "r1", ETAG),) * 2),
     "duplicate_precondition"),
    (lambda: changeset(*(docks(entity_id=f"d{n}") for n in range(MAX_CHANGES + 1))), "too_many_changes"),
    (lambda: guild(base_etag=None), "invalid_field"),
    (lambda: guild(revision="r1"), "invalid_field"),
    (lambda: guild(base_etag="sha256:short"), "invalid_field"),
    (lambda: docks(entity_id="../escape"), "invalid_field"),
    (lambda: docks(payload={"x": float("nan")}), "invalid_field"),
    (lambda: docks(evidence="one string"), "invalid_field"),
    (lambda: changeset(changeset_id="CS 1"), "invalid_field"),
    (lambda: changeset(author=" "), "invalid_field"),
])
def test_values_refuse_what_cannot_be_reviewed(build, reason):
    with pytest.raises(ChangeSetError) as caught:
        build()
    assert caught.value.reason == reason


def test_a_diff_of_a_new_file_and_of_a_missing_final_newline():
    created = unified(None, None, "sources/x.json", b'{\n  "a": 1\n}\n')
    assert created.startswith("--- /dev/null\n+++ b/sources/x.json\n")
    assert '+  "a": 1\n' in created
    changed = unified("sources/x.json", b"{}", "sources/x.json", b"{}\n")
    assert "-{}\n\\ No newline at end of file\n+{}\n" in changed
