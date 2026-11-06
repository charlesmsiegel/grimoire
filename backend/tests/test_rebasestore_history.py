"""Sprint 042 audit fixes through a real native store: a card's uncomposed payload (example dialogue,
greetings, system prompt) is reviewed before a rebase adopts it, the review lists every kind of history
that names the actor, and one commit re-pins several chats of a campaign and a persona binding.
Synthetic Wren of a synthetic harbor world; no user content.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from rebase_fixtures import (  # noqa: F401  (fixtures)
    OWNER, R1, R2, SECTIONS, WORLD, WREN, chats, envelope, publish, put, rebase, reason, start, store, wren,
)
from store_helpers import owner_handle

from grimoire.chats import Launch
from grimoire.core.identity import InstanceRef, KnowledgeRef, SourceRef, TranscriptRef, TurnRef
from grimoire.core.json_value import JsonObject
from grimoire.rebasestore import HISTORY_REVIEW, RebaseHistory
from grimoire.store import KnowledgeRecord, ScopeRecord, SourceManifest
from grimoire.testing.fileops import snapshot as files_of

GULL = SourceRef(WORLD, "character", "gull")
R3 = WREN.at("r3")


def carded(ref, example, prompt, greeting):
    """Wren as a 037 card character: its card texts are uncomposed payload, which 035 reads."""
    definition = wren(ref)
    card = {"format": "chara_card_v3", "texts": {"example-messages": example},
            "instructions": {"system_prompt": prompt}, "greetings": [{"id": "first", "text": greeting}]}
    return replace(definition, extras=JsonObject({**definition.extras, "card": card}))


@pytest.fixture
def card_store(store):
    """Wren's head is r2, a card character (r1, the plain definition, stays as it is: revisions are immutable)."""
    publish(store, carded(R2, "<START>\n{{char}}: Mind the shoals.", "Stay gentle.", "Welcome aboard."))
    return store


# -- B1: uncomposed payload is reviewed, never adopted silently -------------------------------------

def test_a_card_change_alone_is_a_row_that_needs_an_explicit_take(card_store, chats, rebase):
    started, actor = start(chats, R2)
    publish(card_store, carded(R3, "<START>\n{{char}}: Mind the reef.", "Be curt.", "Welcome aboard."))
    before = files_of(card_store.root)

    review = rebase.review(actor)
    assert [(row.field, row.status) for row in review.rows] == [("extra:card", "upstream")]
    [row] = review.rows
    assert row.changed == ("card.instructions.system_prompt", "card.texts.example-messages")
    assert row.ancestor[0]["instructions"]["system_prompt"] == "Stay gentle."
    assert row.upstream[0]["instructions"]["system_prompt"] == "Be curt."
    assert review.decisions_needed == ("extra:card",)
    assert review.notices == (HISTORY_REVIEW,)  # the chat names the actor: its history may disagree

    assert reason(lambda: rebase.apply(review, {})) == "undecided"
    assert reason(lambda: rebase.apply(review, {"extra:card": "keep"})) == "not_overridable"
    assert files_of(card_store.root) == before  # neither refusal wrote anything

    rebase.apply(review, {"extra:card": "take"}).commit()
    assert card_store.read(actor).envelope.base == str(R3)
    record, _ = chats.read_chat(started.chat)
    assert [binding.ref for binding in record.active_bindings()] == [R3]


def test_an_unchanged_card_gives_no_row(card_store, chats, rebase):
    _, actor = start(chats, R2)
    publish(card_store, replace(carded(R3, "<START>\n{{char}}: Mind the shoals.", "Stay gentle.", "Welcome aboard."),
                                name="Wren Tidewater"))
    assert [row.field for row in rebase.review(actor).rows] == ["name"]


def test_a_card_added_or_removed_upstream_is_a_row(store, chats, rebase):
    """A plain definition gains a card in r2: every new path is listed, and taking it is explicit."""
    _, actor = start(chats)
    publish(store, carded(R2, "", "Stay gentle.", "Ahoy."))
    [row] = rebase.review(actor).rows
    assert (row.field, row.ancestor, row.changed) == ("extra:card", None, ("card",))
    assert row.upstream[0]["greetings"][0]["text"] == "Ahoy."


# -- G1: history lists turns, transcript pages and knowledge that name the actor -------------------

def test_history_lists_turns_transcript_pages_and_knowledge_that_name_the_actor(store, chats, rebase):
    started, actor = start(chats)
    campaign = actor.campaign_id
    other = InstanceRef(campaign, "character", "gull-1")
    txn = store.transaction()
    txn.put(ScopeRecord(TurnRef(campaign, "u-1"), "test.turn/1", {"cast": [str(actor)]}), None)
    txn.put(ScopeRecord(TurnRef(campaign, "u-2"), "test.turn/1", {"cast": [str(other)]}), None)
    txn.put(ScopeRecord(TranscriptRef(campaign, started.chat.chat_id, 1), "test.page/1",
                        {"posts": [{"speaker": str(actor), "text": "Mind the shoals."}]}), None)
    txn.put(ScopeRecord(TranscriptRef(campaign, started.chat.chat_id, 2), "test.page/1",
                        {"posts": [{"speaker": str(other), "text": "Aye."}]}), None)
    txn.put(KnowledgeRecord(KnowledgeRef(campaign, "event", "ev-1"), "test.event/1",
                            {"participants": [str(actor)]}), None)
    txn.put(KnowledgeRecord(KnowledgeRef(campaign, "evidence", "obs-1"), "test.evidence/1",
                            {"holder": str(other), "about": str(actor)}), None)
    txn.put(KnowledgeRecord(KnowledgeRef(campaign, "evidence", "obs-2"), "test.evidence/1",
                            {"holder": str(other)}), None)
    txn.commit(owner_handle())
    publish(store, wren(R2, sections={**SECTIONS, "biography": "Born in the harbor town."}))
    before = files_of(store.root)

    review = rebase.review(actor)
    history = review.history
    assert history.turns == (TurnRef(campaign, "u-1"),)
    assert history.transcripts == (TranscriptRef(campaign, started.chat.chat_id, 1),)
    assert history.knowledge == (KnowledgeRef(campaign, "event", "ev-1"), KnowledgeRef(campaign, "evidence", "obs-1"))
    assert history.problems == ()
    assert review.notices == (HISTORY_REVIEW,)

    change = rebase.apply(review, {"section:biography": "take"})
    instance_path = f"campaigns/{campaign}/instances/character/{actor.instance_id}.json"
    chat_path = f"campaigns/{campaign}/chats/{started.chat.chat_id}.json"
    assert set(change.preview()) == {instance_path, chat_path}  # history is referenced, never rewritten
    change.commit()
    after = files_of(store.root)
    assert {path for path in set(before) | set(after) if before.get(path) != after.get(path)} == {
        instance_path, chat_path}


@pytest.mark.parametrize("kind", ["turns", "transcripts", "knowledge"])
def test_any_kind_of_history_alone_makes_the_history_non_empty(kind):
    """``history_review`` is raised whenever ``history`` is not empty, so each new kind counts alone."""
    ref = {"turns": TurnRef("c", "u-1"), "transcripts": TranscriptRef("c", "t-1", 1),
           "knowledge": KnowledgeRef("c", "event", "ev-1")}[kind]
    assert RebaseHistory().empty and not RebaseHistory(**{kind: (ref,)}).empty


def test_an_unreadable_history_file_is_reported_not_refused(store, chats, rebase):
    _, actor = start(chats)
    put(store, ScopeRecord(TurnRef(actor.campaign_id, "u-1"), "test.turn/1", {"cast": [str(actor)]}))
    path = store.root / "campaigns" / actor.campaign_id / "turns" / "u-1.json"
    assert path.is_file()
    path.write_bytes(b"{ not json")
    publish(store, wren(R2, name="Wren Tidewater"))
    review = rebase.review(actor)
    assert review.history.turns == ()
    assert review.history.problems == (f"campaigns/{actor.campaign_id}/turns/u-1.json",)


# -- T3: several chats of one campaign, and a persona binding ---------------------------------------

def test_one_commit_repins_every_chat_of_the_campaign_that_pins_the_actor(store, chats, rebase):
    first, actor = start(chats)
    key = ("character", R1.entity_key)
    second = chats.start_chat(Launch("character", R1), scope=first.scope, reuse_actors={key: actor}).commit()
    third = chats.start_chat(Launch("character", R1), scope=first.scope, reuse_actors={key: actor}).commit()
    elsewhere, other_actor = start(chats)  # another campaign of the same definition keeps r1
    publish(store, wren(R2, name="Wren Tidewater"))
    elsewhere_before = {path: data for path, data in files_of(store.root).items()
                        if path.startswith(f"campaigns/{elsewhere.scope.campaign_id}/")}

    review = rebase.review(actor)
    assert review.repinned == tuple(sorted((first.chat, second.chat, third.chat), key=str))
    change = rebase.apply(review, {"name": "take"})
    assert len(change.preview()) == 4  # the instance and three chats
    change.commit()
    for started in (first, second, third):
        record, _ = chats.read_chat(started.chat)
        [binding] = record.active_bindings()
        assert (binding.ref, binding.actor, binding.role) == (R2, actor, "character")
    assert {path: data for path, data in files_of(store.root).items()
            if path.startswith(f"campaigns/{elsewhere.scope.campaign_id}/")} == elsewhere_before
    assert store.read(other_actor).envelope.payload["source"] == str(R1)


def test_a_persona_binding_is_repinned_as_a_persona(store, chats, rebase):
    put(store, envelope(wren(GULL.at("r1"), name="Gull")),
        SourceManifest(WORLD, OWNER, "Harbor", {"character/wren": "r1", "character/gull": "r1"}))
    started = chats.start_chat(Launch("character", GULL.at("r1")), persona=R1).commit()
    record, _ = chats.read_chat(started.chat)
    persona = record.persona
    assert persona is not None and persona.ref == R1
    put(store, envelope(wren(R2, name="Wren Tidewater")),
        SourceManifest(WORLD, OWNER, "Harbor", {"character/wren": "r2", "character/gull": "r1"}))

    review = rebase.review(persona.actor)
    assert review.repinned == (started.chat,)
    rebase.apply(review, {"name": "take"}).commit()
    record, _ = chats.read_chat(started.chat)
    assert record.persona is not None
    assert (record.persona.ref, record.persona.actor, record.persona.role) == (R2, persona.actor, "persona")
    gull = [binding for binding in record.active_bindings() if binding.role == "character"]
    assert [binding.ref for binding in gull] == [GULL.at("r1")]  # the other binding is untouched
