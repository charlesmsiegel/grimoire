"""Derived chat navigation (Sprint 107): one canonical chat, many views; pure and rebuildable."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.chats import (
    ORPHAN_CHAT, UNDECODABLE, ChatRecord, Launch, NavigationIndex, NewBinding, PlayScope, WorldBinding,
    nav_entry, scope_entry,
)
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef

SRC = Path(__file__).resolve().parents[1] / "src"
T = ["2026-10-31T10:00:0%dZ" % n for n in range(10)]
MARA = SourceRef("catalog:cards", "character", "mara", "r1")
MARA_R2 = MARA.at("r2")
NAMESAKE = SourceRef("catalog:other", "character", "mara", "r1")
LORE = SourceRef("catalog:cards", "lorebook", "notes", "r1")
STORM = SourceRef("catalog:cards", "scenario", "storm", "r1")
SECRET = "do-not-index-this-premise"


def chat(campaign: str, chat_id: str, at: str, launch_ref=MARA, *more: NewBinding) -> ChatRecord:
    ref = ChatRef(campaign, chat_id)
    actor = InstanceRef(campaign, "character", f"{launch_ref.entity_id}-1")
    record = ChatRecord.start(ref, Launch("character", launch_ref), at,
                              (NewBinding(launch_ref, "character", launch_ref.revision, actor),))
    for binding in more:
        record = record.attach(binding, at)
    return record


def library():
    one = chat("q1", "t1", T[1], MARA, NewBinding(LORE, "lorebook", "r1"), NewBinding(STORM, "scenario", "r1"))
    two = chat("q2", "t1", T[3], MARA_R2)
    three = chat("q3", "t1", T[2], NAMESAKE).detach(NAMESAKE, "character", T[4])
    premise = ChatRecord.start(ChatRef("q4", "t1"), Launch("premise", premise=SECRET), T[0])
    chats = [nav_entry(c.ref, c) for c in (one, two, three, premise)]
    chats.append(nav_entry(ChatRef("q1", "t2"), None))  # a chat this version cannot decode
    chats.append(nav_entry(ChatRef("lost", "t1"), chat("lost", "t1", T[5])))  # no scope record
    scopes = [scope_entry(PlayScope(ScopeRef("q1"), T[0], "Harbor nights",
                                    WorldBinding("world:harbor", "sha256:" + "0" * 64, T[0]),
                                    session_zero={"note": SECRET}, extras={"x": SECRET})),
              scope_entry(PlayScope(ScopeRef("q2"), T[0])), scope_entry(PlayScope(ScopeRef("q3"), T[0])),
              scope_entry(PlayScope(ScopeRef("q4"), T[0]))]
    return chats, scopes


@pytest.fixture
def index():
    return NavigationIndex.build(*library())


def test_every_chat_file_is_listed_once_newest_first_including_problem_chats(index):
    refs = index.all_chats()
    assert len(refs) == len(set(refs)) == 6
    assert refs[:4] == (ChatRef("lost", "t1"), ChatRef("q3", "t1"), ChatRef("q2", "t1"), ChatRef("q1", "t1"))
    assert refs[-1] == ChatRef("q1", "t2")  # undecodable: listed, never lost
    assert dict(index.problems()) == {ChatRef("q1", "t2"): UNDECODABLE, ChatRef("lost", "t1"): ORPHAN_CHAT}
    assert index.recent(2) == refs[:2]
    for bad in (0, 1001, "5"):
        with pytest.raises(ValueError):
            index.recent(bad)


def test_by_source_matches_the_qualified_entity_never_the_name(index):
    assert set(index.by_source(MARA.unpinned())) == {ChatRef("q1", "t1"), ChatRef("q2", "t1"),
                                                     ChatRef("lost", "t1")}
    assert index.by_source(MARA) == (ChatRef("lost", "t1"), ChatRef("q1", "t1"))  # pinned: that revision only
    assert index.by_source(MARA_R2) == (ChatRef("q2", "t1"),)
    assert index.by_source(NAMESAKE.unpinned()) == (ChatRef("q3", "t1"),)  # launch is provenance
    assert index.by_source(STORM.unpinned(), role="scenario") == (ChatRef("q1", "t1"),)
    assert index.by_source(STORM.unpinned(), role="lorebook") == ()
    assert index.by_source(LORE.unpinned()) == (ChatRef("q1", "t1"),)


def test_detached_bindings_count_only_on_request():
    record = chat("q1", "t1", T[1], MARA, NewBinding(LORE, "lorebook", "r1")).detach(LORE, "lorebook", T[2])
    index = NavigationIndex.build([nav_entry(record.ref, record)], [scope_entry(PlayScope(ScopeRef("q1"), T[0]))])
    assert index.by_source(LORE.unpinned()) == ()
    assert index.by_source(LORE.unpinned(), include_detached=True) == (record.ref,)


def test_scope_campaign_and_world_views(index):
    assert index.by_scope("q1") == (ChatRef("q1", "t1"), ChatRef("q1", "t2"))
    assert index.campaigns() == (ScopeRef("q1"),)  # unnamed quick scopes are shown as chats
    assert index.by_world("world:harbor") == (ChatRef("q1", "t1"), ChatRef("q1", "t2"))
    assert index.by_world("world:elsewhere") == ()


def test_the_index_is_a_pure_function_of_canonical_records():
    chats, scopes = library()
    first = NavigationIndex.build(chats, scopes)
    for seed in range(5):
        shuffled_chats, shuffled_scopes = chats[:], scopes[:]
        random.Random(seed).shuffle(shuffled_chats)
        random.Random(seed).shuffle(shuffled_scopes)
        again = NavigationIndex.build(shuffled_chats, shuffled_scopes)
        assert again == first and again.to_json() == first.to_json()
        assert again.all_chats() == first.all_chats()


def test_the_index_holds_no_premise_extras_or_session_zero(index):
    text = json.dumps(index.to_json())
    assert SECRET not in text
    assert "Harbor nights" in text  # titles and labels are navigation data


def test_duplicate_canonical_records_are_refused():
    chats, scopes = library()
    with pytest.raises(ValueError):
        NavigationIndex.build(chats + chats[:1], scopes)
    with pytest.raises(ValueError):
        NavigationIndex.build(chats, scopes + scopes[:1])


def test_navigation_loads_no_provider_and_no_store():
    code = ("import json, sys; import grimoire.chats; "
            "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'grimoire')))")
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                            env={**os.environ, "PYTHONPATH": str(SRC)}).stdout
    loaded = json.loads(output)
    assert not [m for m in loaded if m.startswith(("grimoire.providers", "grimoire.store", "grimoire.chatstore"))]
