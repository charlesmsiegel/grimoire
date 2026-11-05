"""027: a snapshot actor's voice follows 022's pinned derivation chain, never a name match."""

from __future__ import annotations

import pytest
from portrayal_fixtures import (
    BASE, MODERN_MARA, NAMESAKE, VAMPIRE_MARA, base, derived, elderfen_index, modern, namesake, vampire,
)

from grimoire.core import InstanceRef, SourceRef
from grimoire.core.entity import EntityInstance
from grimoire.core.portrayal import PortrayalError
from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection
from grimoire.generation import resolve_snapshot_voice
from grimoire.voice import (
    InvalidVoice, MissingVoice, VoiceAnchor, VoiceBinding, VoiceCatalog, VoiceExample, VoiceMismatch, VoiceProfile,
)

CHAT = "chat-modern"
MARA = InstanceRef(CHAT, "character", "mara-1")
BASE_VOICE = VoiceProfile(BASE, (VoiceAnchor("cadence", "cadence", "Answers questions with questions."),
                                 VoiceAnchor("diction", "diction", "Archive words: folio, ledger, margin.")),
                          (VoiceExample("e1", "Does the margin not say so already?"),))
NAMESAKE_VOICE = VoiceProfile(NAMESAKE, (VoiceAnchor("x", "register", "Booming sea-shanty bluster."),))


def snapshot(source: SourceRef, *, index=None, actor=MARA):
    selection = SourceSelection(None, (SelectedSource(source, "character", actor=actor),))
    return EffectiveSnapshot(index or elderfen_index(), selection,
                             instances=[(EntityInstance(actor, source), None)])


def test_a_derived_portrayal_without_its_own_profile_inherits_its_bases_voice():
    voice = resolve_snapshot_voice(MARA, snapshot(MODERN_MARA), VoiceCatalog([BASE_VOICE, NAMESAKE_VOICE]))
    assert voice.actor == MARA and voice.actor_revision == MODERN_MARA
    assert voice.portrayal == BASE  # the profile used, recorded
    assert [a.item_id for a in voice.anchors] == ["cadence", "diction"]
    assert set(voice.provenance.values()) == {"source"}


def test_a_leaf_with_its_own_profile_does_not_inherit():
    own = VoiceProfile(MODERN_MARA, (VoiceAnchor("now", "register", "Dry, modern, texts in lowercase."),))
    voice = resolve_snapshot_voice(MARA, snapshot(MODERN_MARA), VoiceCatalog([BASE_VOICE, own]))
    assert voice.portrayal == MODERN_MARA and [a.item_id for a in voice.anchors] == ["now"]


def test_clearing_voice_evidence_stops_inheritance():
    cleared = derived(VAMPIRE_MARA, sections={"species": "Vampire."}, clear_sections=["voice-evidence"])
    index = elderfen_index(base(), modern(), cleared, namesake())
    with pytest.raises(MissingVoice) as caught:
        resolve_snapshot_voice(MARA, snapshot(VAMPIRE_MARA, index=index), VoiceCatalog([BASE_VOICE]))
    assert caught.value.reason == "no_profile"


def test_a_layer_that_clears_other_sections_still_inherits():
    voice = resolve_snapshot_voice(MARA, snapshot(VAMPIRE_MARA), VoiceCatalog([BASE_VOICE]))
    assert voice.portrayal == BASE


def test_a_missing_base_profile_is_missing_never_a_namesake():
    with pytest.raises(MissingVoice) as caught:
        resolve_snapshot_voice(MARA, snapshot(MODERN_MARA), VoiceCatalog([NAMESAKE_VOICE]))
    assert caught.value.reason == "no_profile"


def test_an_unlinked_derivation_is_refused_by_the_snapshot():
    index = elderfen_index(base(), modern(), vampire(), namesake(), links=[])
    with pytest.raises(PortrayalError) as caught:
        resolve_snapshot_voice(MARA, snapshot(MODERN_MARA, index=index), VoiceCatalog([BASE_VOICE]))
    assert caught.value.reason == "unlinked_derivation"


def test_the_campaign_binding_applies_last():
    binding = VoiceBinding(CHAT, "mara-1", anchors=(VoiceAnchor("diction", "diction", "Startup slang, ironic."),),
                           suppress=("e1",))
    voice = resolve_snapshot_voice(MARA, snapshot(MODERN_MARA), VoiceCatalog([BASE_VOICE]), binding)
    assert dict(voice.provenance) == {"cadence": "source", "diction": "campaign"}
    assert voice.examples == ()


def test_an_alternate_portrayal_binding_is_refused_in_the_snapshot_path():
    binding = VoiceBinding(CHAT, "mara-1", portrayal=VAMPIRE_MARA)
    with pytest.raises(VoiceMismatch) as caught:
        resolve_snapshot_voice(MARA, snapshot(MODERN_MARA), VoiceCatalog([BASE_VOICE]), binding)
    assert caught.value.reason == "alternate_portrayal"


def test_a_binding_for_another_actor_is_refused():
    binding = VoiceBinding(CHAT, "someone-else")
    with pytest.raises(VoiceMismatch):
        resolve_snapshot_voice(MARA, snapshot(MODERN_MARA), VoiceCatalog([BASE_VOICE]), binding)


def test_inputs_are_typed():
    with pytest.raises(InvalidVoice):
        resolve_snapshot_voice("mara-1", snapshot(MODERN_MARA), VoiceCatalog([BASE_VOICE]))  # type: ignore[arg-type]
    with pytest.raises(InvalidVoice):
        resolve_snapshot_voice(MARA, snapshot(MODERN_MARA), [BASE_VOICE])  # type: ignore[arg-type]


def test_a_campaign_original_speaks_with_its_binding_only():
    original = InstanceRef(CHAT, "character", "innkeeper")
    selection = SourceSelection(None, (SelectedSource(MODERN_MARA, "character", actor=MARA),))
    snap = EffectiveSnapshot(elderfen_index(), selection, instances=[
        (EntityInstance(MARA, MODERN_MARA), None), (EntityInstance(original, None, local_name="Innkeeper"), None)])
    with pytest.raises(MissingVoice):
        resolve_snapshot_voice(original, snap, VoiceCatalog([BASE_VOICE]))
    voice = resolve_snapshot_voice(original, snap, VoiceCatalog([BASE_VOICE]),
                                   VoiceBinding(CHAT, "innkeeper", anchors=(VoiceAnchor("a", "register", "Warm."),)))
    assert voice.portrayal is None and voice.name == "Innkeeper"
