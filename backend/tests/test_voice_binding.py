"""Campaign voice bindings: pin through the actor, select linked portrayals, override items."""

import pytest

from grimoire.core import (
    CampaignActor, CharacterDefinition, DefinitionIndex, DefinitionLink, InstanceRef,
    SourceDescriptor, SourceRef,
)
from grimoire.voice import (
    InvalidVoice, MissingVoice, VoiceAnchor, VoiceBinding, VoiceCatalog, VoiceExample,
    VoiceMismatch, VoiceProfile, resolve_voice,
)

W, ADDON, OTHER, THIRD = "world:emberfall", "addon:masks", "world:other", "addon:x"


def ref(entity, revision=None, source=W):
    return SourceRef(source, "character", entity, revision)


ODA1, ODA2 = ref("oda", "r1"), ref("oda", "r2")
BEL1 = ref("bellamy", "r1")
MASK1, MASK2 = ref("bellamy-masked", "r1"), ref("bellamy-masked", "r2")
ODA_MASK = ref("oda-masked", "r1", ADDON)
ODA_OTHER = ref("oda", "r1", OTHER)

DEFINITIONS = [
    CharacterDefinition(ODA1, "Oda"), CharacterDefinition(ODA2, "Oda"),
    CharacterDefinition(BEL1, "Bellamy"),
    CharacterDefinition(MASK1, "The Masked Singer"), CharacterDefinition(MASK2, "The Masked Singer"),
    CharacterDefinition(ODA_MASK, "Oda in a mask"),
    CharacterDefinition(ODA_OTHER, "Oda"),
]
HEADS = {ref("oda"): "r2", ref("bellamy-masked"): "r2"}
SOURCES = [SourceDescriptor(source, "synthetic") for source in (W, ADDON, OTHER, THIRD)]


def index(*links):
    return DefinitionIndex(SOURCES, DEFINITIONS, HEADS, links)


def link(subject, obj, declared_by=W, relation="alternate_portrayal"):
    return DefinitionLink(subject, relation, obj, declared_by)


def voice(portrayal, *words):
    return VoiceProfile(portrayal, anchors=(VoiceAnchor("a1", "cadence", f"{portrayal.entity_id} cadence"),
                                            VoiceAnchor("a2", "diction", "plain words")),
                        examples=tuple(VoiceExample(f"e{i}", word) for i, word in enumerate(words, 1)))


PROFILES = [voice(ODA1, "Ledger's closed.", "Counts first."), voice(ODA2, "Doors locked.", "Coin down."),
            voice(BEL1, "Darling!"), voice(MASK1, "Hush, hush."), voice(MASK2, "Behind the mask."),
            voice(ODA_MASK, "Masked ledger."), voice(ODA_OTHER, "A different Oda.")]
CATALOG = VoiceCatalog(PROFILES)


def actor(actor_id="npc-oda", source=ref("oda"), local_name=None, campaign="c-ember"):
    return CampaignActor(campaign, actor_id, source, local_name)


def test_unpinned_actor_follows_the_head_and_a_pinned_sibling_keeps_its_revision():
    head = resolve_voice(actor(), CATALOG, index())
    pinned = resolve_voice(actor("npc-oda-2", ODA1), CATALOG, index())
    assert head.portrayal == head.actor_revision == ODA2
    assert [e.text for e in head.examples] == ["Doors locked.", "Coin down."]
    assert pinned.portrayal == pinned.actor_revision == ODA1
    assert pinned.actor == InstanceRef("c-ember", "character", "npc-oda-2")
    assert pinned.name == "Oda"
    assert list(CATALOG) == PROFILES


def test_overrides_replace_append_and_suppress_without_touching_the_source():
    before = list(CATALOG)
    binding = VoiceBinding("c-ember", "npc-oda", anchors=(VoiceAnchor("a1", "cadence", "Slower now."),),
                           examples=(VoiceExample("local1", "Keep the change."),), suppress=("e1",))
    resolved = resolve_voice(actor(), CATALOG, index(), binding)
    assert [(a.anchor_id, a.text) for a in resolved.anchors] == [("a1", "Slower now."), ("a2", "plain words")]
    assert [e.example_id for e in resolved.examples] == ["e2", "local1"]
    assert dict(resolved.provenance) == {"a1": "campaign", "a2": "source", "e2": "source",
                                         "local1": "campaign"}
    assert list(CATALOG) == before
    with pytest.raises(TypeError):
        resolved.provenance["a1"] = "source"


def test_local_name_wins_over_the_definition_name():
    assert resolve_voice(actor(local_name="Old Oda"), CATALOG, index()).name == "Old Oda"


def test_a_linked_alternate_portrayal_can_be_selected_but_the_speaker_stays_the_actor():
    bellamy = actor("npc-bellamy", ref("bellamy"))
    binding = VoiceBinding("c-ember", "npc-bellamy", portrayal=ref("bellamy-masked"))
    resolved = resolve_voice(bellamy, CATALOG, index(link(ref("bellamy"), ref("bellamy-masked"))), binding)
    assert resolved.portrayal == MASK2 and resolved.actor_revision == BEL1
    assert resolved.name == "Bellamy"


def test_links_are_symmetric_for_selection():
    bellamy = actor("npc-bellamy", ref("bellamy"))
    binding = VoiceBinding("c-ember", "npc-bellamy", portrayal=MASK1)
    assert resolve_voice(bellamy, CATALOG, index(link(ref("bellamy-masked"), ref("bellamy"))),
                         binding).portrayal == MASK1


def test_a_link_pinned_on_the_actor_side_applies_to_that_resolved_revision():
    binding = VoiceBinding("c-ember", "npc-oda", portrayal=ODA_MASK)
    linked = index(link(ODA2, ODA_MASK, declared_by=ADDON))
    assert resolve_voice(actor(), CATALOG, linked, binding).portrayal == ODA_MASK
    with pytest.raises(VoiceMismatch) as raised:
        resolve_voice(actor("npc-oda-2", ODA1), CATALOG, linked,
                      VoiceBinding("c-ember", "npc-oda-2", portrayal=ODA_MASK))
    assert raised.value.reason == "unlinked_portrayal"


def test_a_link_pinned_on_the_portrayal_side_allows_exactly_that_revision():
    bellamy = actor("npc-bellamy", ref("bellamy"))
    linked = index(link(ref("bellamy"), MASK1))
    assert resolve_voice(bellamy, CATALOG, linked,
                         VoiceBinding("c-ember", "npc-bellamy", portrayal=MASK1)).portrayal == MASK1
    for wanted in (MASK2, ref("bellamy-masked")):
        with pytest.raises(VoiceMismatch) as raised:
            resolve_voice(bellamy, CATALOG, linked, VoiceBinding("c-ember", "npc-bellamy", portrayal=wanted))
        assert raised.value.reason == "unlinked_portrayal"


def test_either_endpoints_source_may_declare_the_link_but_a_third_party_may_not():
    binding = VoiceBinding("c-ember", "npc-oda", portrayal=ODA_MASK)
    for declarer in (W, ADDON):
        assert resolve_voice(actor(), CATALOG, index(link(ref("oda"), ODA_MASK, declared_by=declarer)),
                             binding).portrayal == ODA_MASK
    with pytest.raises(VoiceMismatch) as raised:
        resolve_voice(actor(), CATALOG, index(link(ref("oda"), ODA_MASK, declared_by=THIRD)), binding)
    assert raised.value.reason == "unlinked_portrayal"


@pytest.mark.parametrize("links", [
    (),
    (link(ref("oda"), ref("bellamy"), relation="same_individual"),),
    (link(ODA2, BEL1, relation="derived_variant"),),
    (link(ODA2, BEL1, relation="overrides"),),
    (link(ref("bellamy"), ref("bellamy-masked")),),  # a link elsewhere grants nothing here
])
def test_unlinked_or_wrongly_linked_portrayals_are_refused(links):
    with pytest.raises(VoiceMismatch) as raised:
        resolve_voice(actor(), CATALOG, index(*links),
                      VoiceBinding("c-ember", "npc-oda", portrayal=ref("bellamy")))
    assert raised.value.reason == "unlinked_portrayal"


def test_a_namesake_in_another_source_is_never_used():
    resolved = resolve_voice(actor(), CATALOG, index())
    assert resolved.portrayal.source == W
    with pytest.raises(VoiceMismatch):
        resolve_voice(actor(), CATALOG, index(), VoiceBinding("c-ember", "npc-oda", portrayal=ODA_OTHER))


@pytest.mark.parametrize("binding, reason", [
    (VoiceBinding("c-ember", "npc-bellamy"), "other_actor"),
    (VoiceBinding("c-other", "npc-oda"), "other_actor"),
    (VoiceBinding("c-ember", "npc-oda", portrayal=ODA1), "conflicting_pin"),
    (VoiceBinding("c-ember", "npc-oda", portrayal=ref("oda")), "conflicting_pin"),
])
def test_mismatched_bindings_are_refused(binding, reason):
    with pytest.raises(VoiceMismatch) as raised:
        resolve_voice(actor(), CATALOG, index(), binding)
    assert raised.value.reason == reason


def test_campaign_original_actors_use_local_items_only():
    original = actor("npc-stray", None, local_name="Stray")
    local = VoiceBinding("c-ember", "npc-stray", examples=(VoiceExample("e1", "Spare a coin?"),))
    resolved = resolve_voice(original, CATALOG, index(), local)
    assert resolved.portrayal is None and resolved.actor_revision is None and resolved.name == "Stray"
    assert dict(resolved.provenance) == {"e1": "campaign"}
    with pytest.raises(MissingVoice) as missing:
        resolve_voice(original, CATALOG, index())
    assert missing.value.reason == "no_profile"
    with pytest.raises(VoiceMismatch) as mismatch:
        resolve_voice(original, CATALOG, index(), VoiceBinding("c-ember", "npc-stray", portrayal=BEL1))
    assert mismatch.value.reason == "campaign_original"
    with pytest.raises(InvalidVoice) as stale:
        resolve_voice(original, CATALOG, index(),
                      VoiceBinding("c-ember", "npc-stray", examples=local.examples, suppress=("e1",)))
    assert stale.value.reason == "stale_override"


def test_no_fallback_to_another_revision_or_a_missing_definition():
    without_r2 = VoiceCatalog([p for p in PROFILES if p.portrayal != ODA2])
    with pytest.raises(MissingVoice) as missing:
        resolve_voice(actor(), without_r2, index())
    assert missing.value.reason == "no_profile"
    with pytest.raises(MissingVoice) as undefined:
        resolve_voice(actor("npc-ghost", ref("ghost")), CATALOG, index())
    assert undefined.value.reason == "missing_definition" and undefined.value.detail == "missing_entity"
    with pytest.raises(MissingVoice) as revision:
        resolve_voice(actor("npc-oda-9", ref("oda", "r9")), CATALOG, index())
    assert revision.value.detail == "missing_revision"


@pytest.mark.parametrize("binding, reason", [
    (VoiceBinding("c-ember", "npc-oda", suppress=("nope",)), "stale_override"),
    (VoiceBinding("c-ember", "npc-oda", anchors=(VoiceAnchor("a1", "cadence", "x"),), suppress=("a1",)),
     "suppress_replaced"),
    (VoiceBinding("c-ember", "npc-oda",
                  examples=tuple(VoiceExample(f"x{i}", "more") for i in range(31))), "too_many_items"),
])
def test_bad_overrides_fail_closed(binding, reason):
    with pytest.raises(InvalidVoice) as raised:
        resolve_voice(actor(), CATALOG, index(), binding)
    assert raised.value.reason == reason


def test_suppressing_every_item_leaves_no_voice():
    binding = VoiceBinding("c-ember", "npc-oda", suppress=("a1", "a2", "e1", "e2"))
    with pytest.raises(MissingVoice) as raised:
        resolve_voice(actor(), CATALOG, index(), binding)
    assert raised.value.reason == "no_profile"


@pytest.mark.parametrize("build", [
    lambda: VoiceBinding("Bad Id", "npc-oda"),
    lambda: VoiceBinding("c-ember", "npc-oda", portrayal="world:emberfall/character/oda"),
    lambda: VoiceBinding("c-ember", "npc-oda", portrayal=SourceRef(W, "item", "lantern")),
    lambda: VoiceBinding("c-ember", "npc-oda", suppress="e1"),
    lambda: VoiceBinding("c-ember", "npc-oda", suppress=("e1", "e1")),
    lambda: VoiceBinding("c-ember", "npc-oda", suppress=("Secret Marker Text",)),
    lambda: VoiceBinding("c-ember", "npc-oda", anchors=(VoiceAnchor("a1", "cadence", "x"),
                                                       VoiceAnchor("a1", "cadence", "y"))),
])
def test_binding_values_are_validated(build):
    with pytest.raises(InvalidVoice) as raised:
        build()
    assert "Secret Marker Text" not in str(raised.value)


def test_resolve_requires_real_contract_values():
    with pytest.raises(InvalidVoice):
        resolve_voice(actor(), list(CATALOG), index())
    with pytest.raises(InvalidVoice):
        resolve_voice(actor(), CATALOG, index(), binding="npc-oda")
