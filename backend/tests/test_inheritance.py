"""Inherited defaults and local overrides across four portrayals of one Mara (Sprint 046 proving scenario).

Regency, Modern, Mecha and Vampire Mara each inherit, replace or clear authored fields of one catalog
Mara without copying her. The inspector names every field's state and how the base's head differs;
one portrayal's override never changes the base or another portrayal. Synthetic data only.
"""

from __future__ import annotations

import pytest
from portrayal_fixtures import (
    BASE, BASE_SECTIONS, CAST, MECHA_MARA, MODERN_MARA, REGENCY, REGENCY_MARA, VAMPIRE, VAMPIRE_MARA, base, derives,
    envelope, manifest, seed_four_portrayals,
)
from store_helpers import owner_handle

from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_from_payload
from grimoire.core.inheritance import (
    OverrideError, clear_description, clear_facet, clear_section, inspect, override_description,
    override_section, reset_description, reset_facet, reset_section,
)
from grimoire.core.portrayal import PortrayalError
from grimoire.effective import compose_in, inspect_definition
from grimoire.store import LibraryStore


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_four_portrayals(opened, owner_handle())
        yield opened


def states(inspection):
    return {view.field: view.state for view in inspection.fields}


def test_each_portrayal_inherits_replaces_or_clears_fields(store):
    view = store.load()
    regency, modern = inspect_definition(view, REGENCY_MARA), inspect_definition(view, MODERN_MARA)
    mecha, vampire = inspect_definition(view, MECHA_MARA), inspect_definition(view, VAMPIRE_MARA)
    assert states(regency)["section:personality"] == "inherited"
    assert states(regency)["section:occupation"] == "overridden"
    assert states(regency)["section:period-customs"] == "restated"
    assert states(regency)["description"] == "inherited"
    assert states(modern)["section:age"] == "overridden" and states(modern)["section:period-customs"] == "cleared"
    assert states(mecha)["description"] == "overridden"
    assert states(mecha)["section:mortal-family"] == states(mecha)["section:period-customs"] == "cleared"
    assert states(vampire)["facet:biological"] == "cleared" and states(vampire)["section:species"] == "overridden"
    # nothing is copied: each inherited value is attributed to the catalog revision that authored it
    assert regency.field("section:personality").source == BASE and regency.base == BASE
    assert {view.upstream for view in regency.fields} == {""}  # the base is its head: nothing to compare
    assert inspect_definition(view, BASE).fields[0].state == "local"  # a standalone card is all local


def _base_r2(store):
    """The catalog Mara moves on: a new personality, period customs dropped, a new hobbies section."""
    sections = {key: text for key, text in BASE_SECTIONS.items() if key != "period-customs"}
    sections.update({"personality": "Wry, patient, keeps every promise.", "hobbies": "Restores clocks."})
    r2 = base(sections)
    r2 = type(r2)(BASE.unpinned().at("r2"), r2.name, r2.nature, r2.traits, r2.description, r2.extras)
    txn = store.transaction()
    txn.put(manifest(CAST, heads={"character/mara": "r2"}), store.check_source(CAST).etag)
    txn.put(envelope(r2), None)
    txn.commit(owner_handle())


def test_a_base_update_is_pinned_and_shown_as_a_source_diff(store):
    before = {ref: compose_in(store.load(), ref) for ref in (REGENCY_MARA, MODERN_MARA)}
    _base_r2(store)
    view = store.load()
    regency = inspect_definition(view, REGENCY_MARA)
    assert regency.base == BASE and regency.base_head == BASE.unpinned().at("r2")
    personality = regency.field("section:personality")
    assert (personality.state, personality.upstream) == ("pinned", "changed")
    assert regency.field("section:background").upstream == "same"
    # the restated customs no longer exist upstream: a deprecated source value, shown, not dropped
    assert regency.field("section:period-customs").upstream == "removed"
    hobbies = regency.field("section:hobbies")
    assert (hobbies.state, hobbies.upstream, hobbies.source) == ("missing", "added", BASE.unpinned().at("r2"))
    # Modern's clear of the customs is now stale against the head, and says so
    assert inspect_definition(view, MODERN_MARA).field("section:period-customs").upstream == "removed"
    # and the pinned compositions did not move: a source update never silently changes a portrayal
    for ref, composed in before.items():
        assert compose_in(view, ref).sections == composed.sections


def _revise(store, source, ref, payload, previous):
    """Write ``payload`` as revision r2 of a portrayal, linked by its own world and made its head."""
    definition = definition_from_payload(ref.unpinned().at("r2"), ENTITY_SCHEMA, payload)
    txn = store.transaction()
    txn.put(manifest(source, derives(previous), derives(definition.ref), heads={"character/mara": "r2"}),
            store.check_source(source).etag)
    txn.put(envelope(definition), None)
    txn.commit(owner_handle())
    return definition.ref


def _payload(store, ref):
    return dict(store.read(ref).envelope.payload)


def test_override_clear_and_reset_edit_exactly_one_field(store):
    others = {ref: store.read(ref).etag for ref in (BASE, MODERN_MARA, MECHA_MARA, REGENCY_MARA)}
    payload = _payload(store, VAMPIRE_MARA)
    payload = reset_section(payload, "mortal-family")  # inherit the family again
    payload = override_section(payload, "personality", "Patient for centuries.")
    payload = reset_facet(payload, "biological")
    r2 = _revise(store, VAMPIRE, VAMPIRE_MARA, payload, VAMPIRE_MARA)
    view = store.load()
    vampire = inspect_definition(view, r2)
    assert vampire.field("section:mortal-family").state == "inherited"
    assert vampire.field("section:personality").state == "overridden"
    assert vampire.field("facet:biological").state == "inherited"
    assert vampire.field("section:species").state == "overridden"  # untouched fields keep their state
    # one world's override never changes the core definition or another portrayal
    assert {ref: store.read(ref).etag for ref in others} == others
    assert states(inspect_definition(view, MODERN_MARA))["section:personality"] == "inherited"


def test_clearing_and_resetting_the_description(store):
    payload = clear_description(_payload(store, REGENCY_MARA))
    cleared = _revise(store, REGENCY, REGENCY_MARA, payload, REGENCY_MARA)
    view = store.load()
    assert inspect_definition(view, cleared).field("description").state == "cleared"
    assert compose_in(view, cleared).description == ""
    payload = override_description(payload, "A court archivist.")
    assert "clear_description" not in payload["derivation"]
    assert reset_description(payload).get("description") is None
    assert "clear_description" not in reset_description(payload)["derivation"]


def test_a_clear_of_a_value_the_base_lacks_is_refused_on_compose_not_written_silently(store):
    payload = clear_section(_payload(store, REGENCY_MARA), "no-such-section")
    stale = _revise(store, REGENCY, REGENCY_MARA, payload, REGENCY_MARA)
    with pytest.raises(PortrayalError) as caught:
        compose_in(store.load(), stale)
    assert caught.value.reason == "stale_clear"


@pytest.mark.parametrize("edit", [
    lambda p: override_section(p, "personality", "   "),  # blank text is never a clear
    lambda p: override_section(p, "Bad Name", "text"),
    lambda p: override_description(p, ""),
    lambda p: clear_facet(p, "../x"),
])
def test_edits_refuse_blank_text_and_bad_names(store, edit):
    with pytest.raises(OverrideError) as caught:
        edit(_payload(store, VAMPIRE_MARA))
    assert caught.value.reason == "invalid_field"


def test_a_standalone_card_has_nothing_to_inherit(store):
    with pytest.raises(OverrideError) as caught:
        reset_section(_payload(store, BASE), "personality")
    assert caught.value.reason == "not_derived"
    with pytest.raises(OverrideError):
        clear_section({"derivation": None, "name": "x"}, "personality")  # explicit null is no derivation


def test_inspect_refuses_another_entitys_base(store):
    view = store.load()
    leaf, wrong = compose_in(view, VAMPIRE_MARA), compose_in(view, MODERN_MARA)
    with pytest.raises(ValueError):
        inspect(leaf, wrong)
    with pytest.raises(ValueError):
        inspect(leaf)  # a derived portrayal is never inspected as if standalone


def test_an_unlinked_portrayal_cannot_be_inspected(store):
    txn = store.transaction()
    txn.put(manifest(VAMPIRE), store.check_source(VAMPIRE).etag)
    txn.commit(owner_handle())
    with pytest.raises(PortrayalError) as caught:
        inspect_definition(store.load(), VAMPIRE_MARA)
    assert caught.value.reason == "unlinked_derivation"
