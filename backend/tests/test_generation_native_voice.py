"""Native Library authoring reaches ordinary NPC play without an injected voice catalog."""
from dataclasses import replace

import pytest
from portrayal_fixtures import BASE, MODERN_MARA, base, derived, elderfen_index
from test_generation_voice import MARA, snapshot
from scene_fixtures import ANA, Table, secure, say
from store_helpers import OWNER, authorizer

from grimoire.chats import Launch
from grimoire.core.identity import SourceRef
from grimoire.core.portrayal import PortrayalError
from grimoire.generation import SnapshotVoices, derive_voice
from grimoire.sourceapi import SourceApi
from grimoire.store import LibraryStore
from grimoire.voice import MissingVoice, VoiceAnchor, VoiceCatalog, VoiceProfile

DESCRIPTION = "A tide-reader who keeps the drowned ledger"


def native(**kwargs):
    return replace(base(), description=DESCRIPTION, extras={}, **kwargs)


def test_description_only_native_voice_is_bounded_and_not_an_import():
    result = derive_voice(native())
    assert result.profile.portrayal == BASE
    assert [anchor.text for anchor in result.profile.anchors] == [DESCRIPTION]
    assert result.profile.imported is None
    assert result.profile.examples == ()
    assert derive_voice(replace(native(), description="word " * 500)).profile.anchors[0].text.__len__() <= 400


@pytest.mark.parametrize("extras", [{"card": None}, {"card": {}}, {"v1": {}}, {"derivation": None}])
def test_import_or_derivation_metadata_never_falls_back_to_native(extras):
    assert derive_voice(replace(native(), extras=extras)) is None


def test_native_empty_and_unrelated_sections_never_invent_a_voice():
    result = derive_voice(replace(native(), description="", extras={"sections": {"private": "Hidden password"}}))
    assert result.reason == "no_voice_text"
    assert derive_voice(replace(native(), ref=SourceRef(BASE.source, "creature", "mara", "r1"))) is None
    result = derive_voice(replace(native(), extras={"sections": {"personality": "Speaks softly.", "private": "Hidden password"}}))
    assert [a.text for a in result.profile.anchors] == ["Speaks softly."]


def test_native_pinned_inheritance_override_clear_and_explicit_profile():
    parent = native()
    child = derived(MODERN_MARA, description="She speaks in clipped sentences.")
    snap = snapshot(MODERN_MARA, index=elderfen_index(parent, child))
    voice = SnapshotVoices(snap).resolve(MARA)
    assert voice.portrayal == MODERN_MARA
    assert [a.text for a in voice.anchors] == ["She speaks in clipped sentences."]
    explicit = VoiceProfile(BASE, (VoiceAnchor("own", "cadence", "Always rhymes."),))
    assert SnapshotVoices(snap, VoiceCatalog([explicit])).resolve(MARA).portrayal == BASE
    cleared = derived(MODERN_MARA, clear_description=True)
    with pytest.raises(MissingVoice):
        SnapshotVoices(snapshot(MODERN_MARA, index=elderfen_index(parent, cleared))).resolve(MARA)
    stopped = derived(MODERN_MARA, clear_sections=["voice-evidence"])
    with pytest.raises((MissingVoice, PortrayalError)):
        SnapshotVoices(snapshot(MODERN_MARA, index=elderfen_index(parent, stopped))).resolve(MARA)
    inherited = derived(MODERN_MARA)
    assert SnapshotVoices(snapshot(MODERN_MARA, index=elderfen_index(parent, inherited))).resolve(MARA).portrayal == BASE


def test_manual_source_create_chat_post_generate_keep_and_reopen(tmp_path):
    folder = tmp_path / "native"
    with LibraryStore.create(folder) as store:
        api = SourceApi(store, authorizer("sources", "campaigns"), owner=OWNER)
        source = api.call("sources.create_source", {"source": "local:acceptance"})
        mara = api.call("sources.create", {"source": source["source"], "kind": "character", "expected": source["manifest_etag"],
                                          "fields": {"name": "Mara", "description": DESCRIPTION}})
        winifred = api.call("sources.create", {"source": source["source"], "kind": "character", "expected": mara["manifest_etag"],
                                              "fields": {"name": "Winifred", "description": "A courier"}})
        chat = api.chats.start_chat(Launch("character", SourceRef.parse(mara["ref"]))).commit().chat
        _, etag = api.chats.read_chat(chat)
        api.chats.set_persona(chat, SourceRef.parse(winifred["ref"]), expected=etag).commit()
    secure(folder)
    with Table(folder, [say("Salt keeps. Ledgers don't.")]) as table:
        table.runner.seat(chat, ANA)
        assert not [p for p in table.runner.open(chat).problems if p[0] == "no_voice"]
        table.runner.post(chat, table.player(), '"Hello Mara."')
        table.runner.generate(chat, table.player())
        kept = table.runner.keep(chat, table.player(), 1)
        assert kept.takes[0].state == "kept"
        prompts = table.harness.prompts()
        assert len(prompts) == 1 and DESCRIPTION in prompts[0]
    with Table(folder) as table:
        assert "Salt keeps. Ledgers don't." in str(table.runner.open(chat))


def test_source_revision_pins_do_not_follow_new_native_voice():
    old = native()
    new = replace(old, ref=BASE.at("r2"), description="She speaks with a booming voice.")
    index = elderfen_index(old, new, heads={BASE.unpinned(): "r2"})
    first = SnapshotVoices(snapshot(BASE, index=index)).resolve(MARA)
    second = SnapshotVoices(snapshot(new.ref, index=index)).resolve(MARA)
    assert first.portrayal == BASE and second.portrayal == new.ref
    assert first.anchors[0].text == DESCRIPTION
    assert second.anchors[0].text == "She speaks with a booming voice."
