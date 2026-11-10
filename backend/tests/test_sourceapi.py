"""The Library pages' source authoring and its JSON ops (Sprint 109). Synthetic libraries under ``tmp_path``.

The proving scenario: two same-named characters from different sources, a portrayal derived from one with
an override and an explicit clear, and its exact revision attached to a chat. Around it: stale writes that
write nothing, unknown-field fidelity in the structured and raw editors, the card namespace, typed clears,
and the actions this slice does not offer, which are named rather than faked.
"""

from __future__ import annotations

import json

import pytest
from card_fixtures import SCENARIO, encode, land
from chat_fixtures import Clock, Ids
from scene_fixtures import MARA_LORE, MARA_VELL, STORM, VOICED, secure
from store_helpers import OWNER, authorizer
from ledger_helpers import fixed_clock

from grimoire.chats import Launch
from grimoire.core.identity import SourceRef
from grimoire.ledgers import DurableAuthority
from grimoire.sourceapi import UNAVAILABLE, SourceApi, next_revision
from grimoire.store import LibraryStore
from grimoire.testing.fileops import snapshot as files_of
from grimoire.textapi import ApiRefused

GOTHIC, MODERN = "catalog:gothic", "catalog:modern"


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        yield opened


@pytest.fixture
def api(store):
    return SourceApi(store, authorizer("sources", "campaigns"), owner=OWNER, clock=Clock(), new_id=Ids())


def refused(api, op, **args):
    with pytest.raises(ApiRefused) as caught:
        api.call(op, args)
    return caught.value


def source(api, source_id, title):
    return api.call("sources.create_source", {"source": source_id, "title": title})["manifest_etag"]


def two_maras(api):
    """Two characters both named Mara, one per source: (gothic mara, modern mara) as written."""
    gothic = api.call("sources.create", {"source": GOTHIC, "kind": "character", "expected": source(api, GOTHIC, "Gothic"),
                                         "fields": {"name": "Mara", "description": "A lamplighter of the quay.",
                                                    "sections": {"personality": "Wry and patient."}}})
    modern = api.call("sources.create", {"source": MODERN, "kind": "character", "expected": source(api, MODERN, "Modern"),
                                         "fields": {"name": "Mara", "description": "A bicycle courier."}})
    return gothic, modern


def edit(api, ref, op, **args):
    """``op`` on the head of ``ref``'s entity, naming what a client just read."""
    read = api.call("sources.read", {"ref": str(SourceRef.parse(ref).unpinned())})
    return api.call(op, {"ref": read["entity"], "expected": read["manifest_etag"], "head": read["head"],
                         "etag": read["etag"], **args})


def states(read):
    return {field["field"]: field["state"] for field in read["effective"]["fields"]}


# -- the proving scenario --------------------------------------------------------------------------------

def test_two_namesakes_a_portrayal_with_an_override_and_a_clear_attached_exactly(api):
    gothic, modern = two_maras(api)
    assert gothic["ref"] == "catalog:gothic/character/mara@r1" and modern["ref"] == "catalog:modern/character/mara@r1"

    catalog = api.call("sources.catalog", {"query": "mara"})
    entries = {entry["ref"]: entry for entry in catalog["entries"]}
    assert set(entries) == {"catalog:gothic/character/mara", "catalog:modern/character/mara"}
    assert entries["catalog:gothic/character/mara"]["namesakes"] == ["catalog:modern/character/mara"]

    derived = api.call("sources.derive", {"base": gothic["ref"], "source": MODERN, "expected": modern["manifest_etag"],
                                          "id": "mara-after-dark", "name": "Mara"})
    portrayal = "catalog:modern/character/mara-after-dark"
    assert derived["ref"] == f"{portrayal}@r1"
    fresh = api.call("sources.read", {"ref": portrayal})
    assert fresh["derived"] and fresh["base"] == gothic["ref"]
    assert states(fresh) == {"name": "restated", "description": "inherited", "section:personality": "inherited"}

    edit(api, portrayal, "sources.portrayal", edit="override_section", name="personality", text="Hungry, and sorry.")
    cleared = edit(api, portrayal, "sources.portrayal", edit="clear_description")
    assert cleared["ref"] == f"{portrayal}@r3"
    read = api.call("sources.read", {"ref": portrayal})
    assert states(read) == {"name": "restated", "description": "cleared", "section:personality": "overridden"}
    values = {field["field"]: field["value"] for field in read["effective"]["fields"]}
    assert values["section:personality"] == "Hungry, and sorry." and values["description"] is None
    assert [node["ref"] for node in read["effective"]["chain"]] == [f"{portrayal}@r3", gothic["ref"]]
    assert {link["subject"] for link in read["links"]} == {f"{portrayal}@r{n}" for n in (1, 2, 3)}
    assert all(link["declared_by"] == MODERN and link["object"] == gothic["ref"] for link in read["links"])
    # the base and the namesake are untouched, and the base lists its portrayal
    base = api.call("sources.read", {"ref": "catalog:gothic/character/mara"})
    assert base["revision"] == "r1" and base["portrayals"] == [portrayal]
    assert api.call("sources.read", {"ref": "catalog:modern/character/mara"})["revision"] == "r1"

    api.chats.start_chat(Launch("premise", premise="A night on the quay.")).commit()
    target = api.call("sources.chats", {})["chats"][0]
    attached = api.call("sources.attach", {"ref": cleared["ref"], "chat": target["chat"], "expected": target["etag"]})
    assert attached["role"] == "character" and len(attached["actors"]) == 1
    record, _etag = api.chats.read_chat(api.chats.navigation().recent(1)[0])
    binding = next(b for b in record.active_bindings("character"))
    assert str(binding.ref) == cleared["ref"] and binding.resolved_revision == "r3"
    linked = api.call("sources.read", {"ref": portrayal})["chats"]
    assert linked == [{**linked[0], "chat": target["chat"], "revisions": ["r3"], "active": True}]
    # a later revision does not move the chat: it stays on exactly what was attached
    edit(api, portrayal, "sources.portrayal", edit="reset_description")
    record, _etag = api.chats.read_chat(api.chats.navigation().recent(1)[0])
    assert next(b for b in record.active_bindings("character")).resolved_revision == "r3"
    assert api.call("sources.read", {"ref": f"{portrayal}@r3"})["is_head"] is False


def test_the_proving_scenario_writes_through_a_secured_library(tmp_path):
    with LibraryStore.create(tmp_path / "lib"):
        pass
    secure(tmp_path / "lib")
    with LibraryStore.open(tmp_path / "lib") as store:
        authority = DurableAuthority.open(store, [OWNER], clock=fixed_clock())
        try:
            api = SourceApi(store, authority, owner=OWNER, clock=Clock(), new_id=Ids())
            gothic, modern = two_maras(api)
            derived = api.call("sources.derive", {"base": gothic["ref"], "source": MODERN,
                                                  "expected": modern["manifest_etag"]})
            assert derived["ref"] == "catalog:modern/character/mara-2@r1"  # the slug "mara" is taken there
            api.chats.start_chat(Launch("premise", premise="Fog.")).commit()
            target = api.call("sources.chats", {})["chats"][0]
            assert api.call("sources.attach", {"ref": derived["ref"], "chat": target["chat"],
                                               "expected": target["etag"]})["actors"]
        finally:
            authority.close()


# -- stale writes, refusals ------------------------------------------------------------------------------

def test_a_stale_edit_is_refused_and_writes_nothing(api, store):
    gothic, _modern = two_maras(api)
    read = api.call("sources.read", {"ref": gothic["ref"]})
    edit(api, gothic["ref"], "sources.save", fields={"description": "Someone else's edit."})
    before = files_of(store.root)
    for op, extra in (("sources.save", {"fields": {"description": "Mine."}}),
                      ("sources.save_raw", {"payload": {"name": "Mara", "traits": []}})):
        stale = refused(api, op, ref=read["entity"], expected=read["manifest_etag"], head=read["head"], etag=read["etag"], **extra)
        assert stale.reason == "changed" and stale.status == 409
    stale = refused(api, "sources.create", source=GOTHIC, kind="character", expected=read["manifest_etag"],
                    fields={"name": "Wren"})
    assert stale.reason == "changed"
    assert files_of(store.root) == before


def test_another_sources_edit_does_not_make_this_one_stale(api):
    gothic, modern = two_maras(api)
    read = api.call("sources.read", {"ref": gothic["ref"]})
    edit(api, modern["ref"], "sources.save", fields={"description": "A courier at night."})
    saved = api.call("sources.save", {"ref": read["entity"], "expected": read["manifest_etag"], "head": read["head"],
                                      "etag": read["etag"], "fields": {"description": "Mine."}})
    assert saved["ref"] == "catalog:gothic/character/mara@r2"


def test_sources_are_never_made_in_the_card_namespace_or_over_another(api):
    assert refused(api, "sources.create_source", source="card:mara").reason == "reserved_namespace"
    source(api, GOTHIC, "Gothic")
    assert refused(api, "sources.create_source", source=GOTHIC).reason == "source_exists"
    assert refused(api, "sources.create_source", source="not a source").reason == "invalid_field"


def test_ids_are_never_reused_and_imports_are_read_only(api):
    gothic, _modern = two_maras(api)
    etag = api.call("sources.read", {"ref": gothic["ref"]})["manifest_etag"]
    assert refused(api, "sources.create", source=GOTHIC, kind="location", expected=etag, id="mara",
                   fields={"name": "Mara's quay"}).reason == "entity_exists"
    assert refused(api, "sources.create", source=GOTHIC, kind="card-import", expected=etag,
                   fields={"name": "x"}).reason == "import_record_read_only"
    assert next_revision(("r1", "r2", "r10")) == "r11"


def test_a_clear_of_something_not_inherited_is_refused_up_front(api):
    gothic, modern = two_maras(api)
    derived = api.call("sources.derive", {"base": gothic["ref"], "source": MODERN, "expected": modern["manifest_etag"]})
    assert refused(api, "sources.portrayal", **_head(api, derived["ref"]), edit="clear_section",
                   name="history").reason == "stale_clear"
    edit(api, derived["ref"], "sources.portrayal", edit="override_description", text="Pale.")
    cleared = edit(api, derived["ref"], "sources.portrayal", edit="clear_description")  # an override clears too
    assert states(api.call("sources.read", {"ref": cleared["ref"]}))["description"] == "cleared"
    assert refused(api, "sources.portrayal", **_head(api, derived["ref"]), edit="clear_description").reason \
        == "stale_clear"
    # only a portrayal takes portrayal edits
    assert refused(api, "sources.portrayal", **_head(api, gothic["ref"]), edit="clear_description").reason \
        == "stale_clear"


def _head(api, ref):
    read = api.call("sources.read", {"ref": str(SourceRef.parse(ref).unpinned())})
    return {"ref": read["entity"], "expected": read["manifest_etag"], "head": read["head"], "etag": read["etag"]}


def test_an_old_revision_is_read_exactly_but_not_edited(api):
    gothic, _modern = two_maras(api)
    edit(api, gothic["ref"], "sources.save", fields={"description": "Older now."})
    old = api.call("sources.read", {"ref": gothic["ref"]})
    assert old["revision"] == "r1" and old["head"] == "r2" and not old["is_head"]
    assert old["payload"]["description"] == "A lamplighter of the quay."
    assert old["actions"]["save"] and old["actions"]["save_raw"]  # each names why not
    assert old["actions"]["attach"] is None  # an old revision can still be attached exactly
    assert old["actions"]["delete"] == UNAVAILABLE["delete"]


def test_unknown_ops_and_arguments_are_refused(api):
    assert refused(api, "sources.delete").reason == "unknown_op"
    assert refused(api, "sources.read", ref="nonsense").reason == "invalid_field"
    assert refused(api, "sources.read", ref="catalog:none/character/x").status == 409
    two_maras(api)
    target_refusal = refused(api, "sources.attach", ref="catalog:gothic/character/mara", chat="campaign:x#chat/y",
                             expected="sha256:" + "0" * 64)
    assert target_refusal.reason == "invalid_field"  # attach names one exact revision


# -- fidelity of the structured and raw editors --------------------------------------------------------

def test_the_raw_editor_keeps_unknown_fields_and_the_structured_one_keeps_them_too(api):
    gothic, _modern = two_maras(api)
    read = api.call("sources.read", {"ref": gothic["ref"]})
    payload = {**read["payload"], "x_custom": {"keep": [1, 2, "three"]}}
    raw = api.call("sources.save_raw", {"ref": read["entity"], "expected": read["manifest_etag"], "head": read["head"],
                                        "etag": read["etag"], "payload": payload})
    assert api.call("sources.read", {"ref": raw["ref"]})["payload"] == payload
    saved = edit(api, gothic["ref"], "sources.save", fields={"description": "Edited in the form."})
    after = api.call("sources.read", {"ref": saved["ref"]})
    assert after["payload"]["x_custom"] == {"keep": [1, 2, "three"]}
    assert after["payload"]["description"] == "Edited in the form."
    assert after["payload"]["sections"] == {"personality": "Wry and patient."}


def test_the_raw_editor_still_refuses_a_payload_that_is_no_entity(api, store):
    gothic, _modern = two_maras(api)
    head = _head(api, gothic["ref"])
    before = files_of(store.root)
    for payload in ({"traits": []}, {"name": "Mara", "traits": "nope"},
                    {"name": "Mara", "traits": [], "derivation": {"base": "catalog:gothic/character/mara"}}):
        assert refused(api, "sources.save_raw", **head, payload=payload).reason != "changed"
    assert files_of(store.root) == before


def test_a_card_records_structured_edits_keep_its_import_fields(api, store):
    land(store, encode(VOICED))
    before = api.call("sources.read", {"ref": str(MARA_VELL)})
    assert before["family"] == "entity" and before["source"] == "card:mara-vell"
    saved = edit(api, str(MARA_VELL), "sources.save", fields={"description": "Edited after import."})
    after = api.call("sources.read", {"ref": saved["ref"]})
    untouched = {key: value for key, value in before["payload"].items() if key != "description"}
    assert {key: after["payload"][key] for key in untouched} == untouched
    lore = api.call("sources.read", {"ref": str(MARA_LORE)})
    entries = lore["payload"]["entries"]
    kept = [{"index": number, "keys": entry["keys"], "content": entry["content"]} for number, entry in enumerate(entries)]
    kept[0]["content"] = "The ledger is wet."
    saved = edit(api, str(MARA_LORE), "sources.save", fields={"entries": kept})
    edited = api.call("sources.read", {"ref": saved["ref"]})["payload"]["entries"]
    assert edited[0] == {**entries[0], "content": "The ledger is wet."} and edited[1:] == entries[1:]
    assert api.call("sources.catalog", {"kind": "card-import"})["entries"] == []


def test_a_scenario_edit_keeps_its_openings_extra_keys(api, store):
    land(store, encode(SCENARIO))
    before = api.call("sources.read", {"ref": str(STORM)})
    assert before["family"] == "scenario"
    openings = [{"id": item["id"], "text": item["text"]} for item in before["payload"]["openings"]]
    openings[0]["text"] = "Rain on the quay."
    saved = edit(api, str(STORM), "sources.save", fields={"openings": openings})
    after = api.call("sources.read", {"ref": saved["ref"]})["payload"]
    assert after["openings"][0] == {**before["payload"]["openings"][0], "text": "Rain on the quay."}
    assert after["roles"] == before["payload"]["roles"]


def test_new_scenarios_and_lorebooks_are_written_in_their_own_schemas(api):
    etag = source(api, "world:harbor", "Harbor")
    lore = api.call("sources.create", {"source": "world:harbor", "kind": "lorebook", "expected": etag,
                                       "fields": {"name": "Harbor notes",
                                                  "entries": [{"keys": ["lighthouse"], "content": "It is dark."}]}})
    scenario = api.call("sources.create", {"source": "world:harbor", "kind": "scenario", "expected": lore["manifest_etag"],
                                           "fields": {"title": "Fog", "premise": "Fog rolls in.",
                                                      "openings": [{"text": "The horn sounds."}]}})
    read = api.call("sources.read", {"ref": scenario["ref"]})
    assert read["family"] == "scenario" and read["payload"]["openings"] == [{"id": "first", "text": "The horn sounds."}]
    assert read["actions"]["derive"] and read["actions"]["portrayal"]  # each names why not
    location = api.call("sources.create", {"source": "world:harbor", "kind": "location",
                                           "expected": scenario["manifest_etag"], "fields": {"name": "The quay"}})
    assert api.call("sources.read", {"ref": location["ref"]})["actions"]["attach"]  # not attachable, named
    assert refused(api, "sources.attach", ref=location["ref"], chat="campaign:x#chat/y",
                   expected="sha256:" + "0" * 64).reason == "not_attachable"
    assert {entry["kind"] for entry in api.call("sources.catalog", {"source": "world:harbor"})["entries"]} == \
        {"lorebook", "scenario", "location"}
    assert refused(api, "sources.create", source="world:harbor", kind="scenario",
                   expected=location["manifest_etag"], fields={"title": "Empty"}).reason == "invalid_field"


# -- an editor never writes a head that cannot be played ------------------------------------------------

def portrayal_of(api):
    gothic, modern = two_maras(api)
    derived = api.call("sources.derive", {"base": gothic["ref"], "source": MODERN, "expected": modern["manifest_etag"]})
    return gothic, derived["ref"]


def test_the_structured_editor_overrides_a_portrayals_cleared_fields_instead_of_breaking_it(api):
    _gothic, portrayal = portrayal_of(api)
    edit(api, portrayal, "sources.portrayal", edit="clear_description")
    edit(api, portrayal, "sources.portrayal", edit="clear_section", name="personality")
    saved = edit(api, portrayal, "sources.save", fields={"description": "New.", "sections": {"personality": "Kind."}})
    read = api.call("sources.read", {"ref": saved["ref"]})
    assert read["composition_error"] is None
    assert states(read) == {"name": "restated", "description": "overridden", "section:personality": "overridden"}
    assert "clear_description" not in read["payload"]["derivation"]
    reset = edit(api, portrayal, "sources.save", fields={"description": "", "sections": {}})
    assert states(api.call("sources.read", {"ref": reset["ref"]})) == \
        {"name": "restated", "description": "inherited", "section:personality": "inherited"}


def test_a_head_that_would_not_compose_is_refused_and_nothing_is_written(api, store):
    _gothic, portrayal = portrayal_of(api)
    head = _head(api, portrayal)
    payload = api.call("sources.read", {"ref": portrayal})["payload"]
    before = files_of(store.root)
    assert refused(api, "sources.save", **head, fields={"nature": "template"}).reason == "nature_mismatch"
    future = {**payload, "derivation": {"base": "catalog:gothic/character/mara@r2"}}  # not written yet
    assert refused(api, "sources.save_raw", **head, payload=future).reason == "missing_base"
    both = {**payload, "description": "Set.", "derivation": {**payload["derivation"], "clear_description": True}}
    assert refused(api, "sources.save_raw", **head, payload=both).reason == "clear_and_set"
    for junk in (5, None, True, ["Bad Name"]):
        malformed = {**payload, "derivation": {**payload["derivation"], "clear_sections": junk}}
        assert refused(api, "sources.save_raw", **head, payload=malformed).reason != "changed"
    assert files_of(store.root) == before


def test_a_scenario_with_odd_raw_keys_is_edited_without_a_crash_or_a_lost_key(api, store):
    land(store, encode(SCENARIO))
    read = api.call("sources.read", {"ref": str(STORM)})
    odd = {**read["payload"], "openings": [{"id": ["x"], "text": "a"}], "roles": ["custom"]}
    raw = api.call("sources.save_raw", {"ref": read["entity"], "expected": read["manifest_etag"],
                                        "head": read["head"], "etag": read["etag"], "payload": odd})
    titled = edit(api, raw["ref"], "sources.save", fields={"title": "Storm, again"})
    assert api.call("sources.read", {"ref": titled["ref"]})["payload"]["roles"] == ["custom"]
    assert refused(api, "sources.save", **_head(api, str(STORM)),
                   fields={"openings": [{"text": "b"}]}).reason == "invalid_payload"


def test_each_write_returns_the_etag_of_exactly_what_it_wrote(api, store):
    etag = source(api, GOTHIC, "Gothic")
    assert etag == store.read_source(GOTHIC).etag
    renamed = api.call("sources.rename_source", {"source": GOTHIC, "title": "Gothic tales", "expected": etag})
    assert renamed["manifest_etag"] == store.read_source(GOTHIC).etag != etag
    assert refused(api, "sources.rename_source", source=GOTHIC, title="Late", expected=etag).reason == "changed"


def test_a_blank_field_never_lifts_a_clear(api):
    _gothic, portrayal = portrayal_of(api)
    edit(api, portrayal, "sources.portrayal", edit="clear_description")
    edit(api, portrayal, "sources.portrayal", edit="clear_section", name="personality")
    saved = edit(api, portrayal, "sources.save", fields={"description": "", "sections": {"personality": ""}})
    assert states(api.call("sources.read", {"ref": saved["ref"]})) == \
        {"name": "restated", "description": "cleared", "section:personality": "cleared"}


# -- from the PR review ---------------------------------------------------------------------------------

def test_an_older_revision_is_never_saved_over_the_head(api, store):
    gothic, _modern = two_maras(api)
    edit(api, gothic["ref"], "sources.save", fields={"description": "Newer."})
    old = api.call("sources.read", {"ref": gothic["ref"]})  # r1, with the head's tokens
    before = files_of(store.root)
    for op, extra in (("sources.save_raw", {"payload": old["payload"]}), ("sources.save", {"fields": {"name": "M"}})):
        assert refused(api, op, ref=old["ref"], expected=old["manifest_etag"], head=old["head"], etag=old["etag"], **extra).reason \
            == "not_head"
    assert files_of(store.root) == before


def test_every_read_is_json_including_uncomposed_card_fields(api, store):
    land(store, encode(VOICED))
    read = api.call("sources.read", {"ref": str(MARA_VELL)})
    assert read["effective"]["uncomposed"] and all(set(item) == {"ref", "field"} for item in read["effective"]["uncomposed"])
    json.dumps(read)
    json.dumps(api.call("sources.catalog", {}))
    json.dumps(api.call("sources.read", {"ref": str(MARA_LORE)}))


def test_a_lore_entrys_constant_flag_is_the_one_the_lore_reader_reads(api, store):
    from grimoire.cards.lore import _entry  # noqa: PLC0415  -- 037's reader of one entry
    etag = source(api, "world:harbor", "Harbor")
    lore = api.call("sources.create", {"source": "world:harbor", "kind": "lorebook", "expected": etag, "fields": {
        "entries": [{"keys": [], "content": "The bell rings at dusk.", "constant": True}]}})
    entry = api.call("sources.read", {"ref": lore["ref"]})["payload"]["entries"][0]
    assert entry["settings"] == {"constant": True} and _entry(0, entry).constant is True  # read with no keys
    saved = edit(api, lore["ref"], "sources.save", fields={"entries": [{"index": 0, "keys": ["bell"],
                                                                         "content": entry["content"], "constant": False}]})
    edited = api.call("sources.read", {"ref": saved["ref"]})["payload"]["entries"][0]
    assert edited["settings"] == {"constant": False} and _entry(0, edited).constant is False


# -- Codex round 2: envelope fields, schema-less records, attach refusals, hand edits ----------------------

def _record_file(store, ref):
    source = SourceRef.parse(ref)
    namespace, name = source.source.split(":", 1)
    return (store.root / "sources" / namespace / name / "_source" / "records" / source.kind / source.entity_id
            / f"{source.revision}.json")


def _hand_edit(store, ref, change):
    path = _record_file(store, ref)
    record = json.loads(path.read_text(encoding="utf-8"))
    change(record)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_an_edit_keeps_the_heads_other_envelope_fields(api, store):
    gothic, _modern = two_maras(api)
    _hand_edit(store, gothic["ref"], lambda record: record.update({"x-author-note": {"draft": True}}))
    for op, extra in (("sources.save", {"fields": {"description": "Kept the note."}}),
                      ("sources.save_raw", {"payload": {"name": "Mara", "nature": "individual", "traits": []}})):
        saved = edit(api, gothic["ref"], op, **extra)
        envelope = api.call("sources.read", {"ref": saved["ref"]})["envelope"]["extra"]
        assert envelope == {"schema": "grimoire.entity/0", "x-author-note": {"draft": True}}
    derived = api.call("sources.derive", {"base": "catalog:gothic/character/mara@r3", "source": GOTHIC,
                                          "expected": api.call("sources.read", {"ref": gothic["ref"]})["manifest_etag"]})
    _hand_edit(store, derived["ref"], lambda record: record.update({"x-tag": "night"}))
    overridden = edit(api, derived["ref"], "sources.portrayal", edit="override_description", text="Pale.")
    assert api.call("sources.read", {"ref": overridden["ref"]})["envelope"]["extra"]["x-tag"] == "night"


def test_a_schema_less_record_is_saved_raw_without_one(api, store):
    gothic, _modern = two_maras(api)
    _hand_edit(store, gothic["ref"], lambda record: record.pop("schema"))
    read = api.call("sources.read", {"ref": gothic["ref"]})
    assert read["family"] == "other" and read["schema"] is None
    saved = edit(api, gothic["ref"], "sources.save_raw", payload={"anything": ["goes", 1]})
    after = api.call("sources.read", {"ref": saved["ref"]})
    assert after["family"] == "other" and after["schema"] is None and after["payload"] == {"anything": ["goes", 1]}
    assert "schema" not in json.loads(_record_file(store, saved["ref"]).read_text(encoding="utf-8"))
    assert refused(api, "sources.save", **_head(api, gothic["ref"]), fields={"name": "x"}).reason \
        == "no_structured_editor"


def test_a_hand_edit_of_the_head_after_the_read_is_a_stale_write(api, store):
    gothic, _modern = two_maras(api)
    read = api.call("sources.read", {"ref": "catalog:gothic/character/mara"})
    _hand_edit(store, gothic["ref"], lambda record: record["payload"].update({"description": "Edited by hand."}))
    before = files_of(store.root)
    for op, extra in (("sources.save", {"fields": {"description": "Mine."}}),
                      ("sources.save_raw", {"payload": read["payload"]})):
        assert refused(api, op, ref=read["entity"], expected=read["manifest_etag"], head=read["head"],
                       etag=read["etag"], **extra).reason == "changed"
    assert files_of(store.root) == before
    # read again and the edit goes on top of the hand edit
    saved = edit(api, gothic["ref"], "sources.save", fields={"sections": {"history": "Kept."}})
    assert api.call("sources.read", {"ref": saved["ref"]})["payload"]["description"] == "Edited by hand."


def test_an_attach_the_chat_refuses_is_a_refusal_with_its_reason(api):
    gothic, _modern = two_maras(api)
    api.chats.start_chat(Launch("premise", premise="A night on the quay.")).commit()
    target = api.call("sources.chats", {})["chats"][0]
    attached = api.call("sources.attach", {"ref": gothic["ref"], "chat": target["chat"], "expected": target["etag"]})
    again = refused(api, "sources.attach", ref=gothic["ref"], chat=target["chat"], expected=attached["etag"])
    assert again.reason == "duplicate_binding"


def test_a_character_made_here_is_said_to_need_a_voice_profile(api):
    gothic, _modern = two_maras(api)
    assert "voice" in api.call("sources.read", {"ref": gothic["ref"]})["unavailable"]


def test_lore_the_reader_would_skip_is_refused_but_an_imported_quirk_stays_editable(api):
    etag = source(api, "world:harbor", "Harbor")
    for entry, reason in (({"keys": ["bell"], "content": "x" * 8001}, "unusable_lore"),
                          ({"keys": [], "content": "Keyed by nothing."}, "unusable_lore"),
                          ({"keys": ["k" * 101], "content": "A key too long to match."}, "invalid_field"),
                          ({"keys": ["one\ntwo"], "content": "A key of two lines."}, "invalid_field"),
                          ({"keys": [f"key {n}" for n in range(65)], "content": "Too many keys."}, "invalid_field")):
        assert refused(api, "sources.create", source="world:harbor", kind="lorebook", expected=etag,
                       fields={"entries": [entry]}).reason == reason
    lore = api.call("sources.create", {"source": "world:harbor", "kind": "lorebook", "expected": etag, "fields": {
        "entries": [{"keys": ["bell"], "content": "x" * 8000}]}})
    # an entry the head already holds, which the reader skips (a regex key), keeps being editable as it is
    read = api.call("sources.read", {"ref": lore["ref"]})
    quirk = {"keys": ["/b.ll/"], "content": "Regex lore.", "settings": {"use_regex": True}}
    raw = edit(api, lore["ref"], "sources.save_raw", payload={**read["payload"], "entries": [quirk]})
    saved = edit(api, raw["ref"], "sources.save", fields={"name": "Harbor lore", "entries": [
        {"index": 0, "keys": ["/b.ll/"], "content": "Regex lore, retold."}]})
    entry = api.call("sources.read", {"ref": saved["ref"]})["payload"]["entries"][0]
    assert entry["settings"] == {"use_regex": True} and entry["content"] == "Regex lore, retold."
