"""071: a new actor role is one entry in the chat role table, written by the one chat writer.

The proving scenario of the theory-driven simplification sprint: a hypothetical ``companion`` role
binds ``creature`` definitions, one campaign creature per bound source, created or reused. Registering
that one ``RoleRule`` is the whole change; the model's bindings and roster, the codec and the chat
library's checks before it writes all follow it, and every file still comes from the chat library's
single commit (no second writer creates the creature). The role is a test-only registration, not a
shipped role: a build without it refuses a chat that uses it rather than reinterpreting the binding.
"""

import pytest

from grimoire.chats import CAMPAIGN_CONTINUITY, NOT_WITNESSED, PER_SOURCE, ChatCodecError, ChatModelError, Launch
from grimoire.chats import RoleRule
from grimoire.chats import model as chat_model
from grimoire.chatstore import InvalidBinding
from grimoire.core.entity import EntityDefinition
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload
from grimoire.core.facets import Agent, Biological, FacetSet, Physical
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.effective import chat_snapshot
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest
from grimoire.testing.fileops import snapshot

from chat_fixtures import MARA, chat_library, seed_sources
from store_helpers import OWNER, owner_handle

BEASTS = "catalog:beasts"
WOLF = SourceRef(BEASTS, "creature", "wolf", "r1")
ASH = EntityDefinition(WOLF, "Ash", "individual", FacetSet([Physical(), Biological(), Agent()]), "A grey wolf")
COMPANION = RoleRule("creature", PER_SOURCE)


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "v2"
    with LibraryStore.create(path) as store:
        seed_sources(store)
        transaction = store.transaction()
        transaction.put(SourceManifest(BEASTS, OWNER, "Beasts"), None)
        transaction.put(RecordEnvelope(WOLF, definition_to_payload(ASH), name="Ash", extra={"schema": ENTITY_SCHEMA}),
                        None)
        transaction.commit(owner_handle())
    return path


@pytest.fixture
def companion(monkeypatch):
    """The whole change: one more entry in the role table."""
    monkeypatch.setattr(chat_model, "ROLES", {**chat_model.ROLES, "companion": COMPANION})


def written(before, after):
    return sorted(path for path in after if after[path] != before.get(path))


def test_a_new_role_creates_its_actor_of_the_definitions_kind_in_the_one_chat_commit(root, companion):
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        before = snapshot(root)
        change = chats.start_chat(Launch("premise", premise="A night hike"), attach=[(WOLF, "companion")])
        assert snapshot(root) == before  # nothing is written before the commit
        planned = sorted(change.preview())
        commit = change.commit()
        (actor,) = commit.actors
        assert actor.kind == "creature" and actor.campaign_id == commit.scope.campaign_id
        # Every record that changed is one the chat library's change planned, in its one commit.
        new_records = {path for path in written(before, snapshot(root)) if path.endswith(".json")}
        assert set(planned) <= new_records
        assert f"campaigns/{actor.campaign_id}/instances/creature/{actor.instance_id}.json" in planned
        record = chats.open_chat(commit.chat).record
        (binding,) = record.active_bindings("companion")
        assert (binding.ref, binding.actor) == (WOLF, actor)
        assert record.roster_entry(actor).role == "companion"
        assert record.roster_entry(actor).prior_history == NOT_WITNESSED
        assert store.read(actor).envelope.derived_from == str(WOLF)
    with LibraryStore.open(root) as store:  # a fresh process reads the same chat and actor
        view = chat_library(store).open_chat(commit.chat)
        assert view.broken == () and view.problems == ()
        captured = chat_snapshot(store.load(), commit.chat)
        assert captured.problems == ()
        assert captured.snapshot.entity(actor).name == "Ash"


def test_a_new_role_reuses_its_campaign_actor_and_keeps_the_kind_rule(root, companion):
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        first = chats.start_chat(Launch("premise", premise="A night hike"), attach=[(WOLF, "companion")]).commit()
        (actor,) = first.actors
        before = snapshot(root)
        second = chats.start_chat(Launch("premise", premise="The next morning"), attach=[(WOLF, "companion")],
                                  scope=first.scope, reuse_actors={("companion", WOLF.entity_key): actor}).commit()
        assert second.actors == ()  # reused, not created again
        entry = chats.open_chat(second.chat).record.roster_entry(actor)
        assert entry.role == "companion" and entry.prior_history == CAMPAIGN_CONTINUITY
        assert not any("/instances/" in path for path in written(before, snapshot(root)))

        # A character card, or a character instance, never plays the creature role.
        character = InstanceRef(first.scope.campaign_id, "character", actor.instance_id)
        before = snapshot(root)
        with pytest.raises(InvalidBinding) as refused:
            chats.attach_source(first.chat, MARA, "companion", expected=chats.open_chat(first.chat).etag)
        assert refused.value.reason == "role_kind"
        with pytest.raises(InvalidBinding) as refused:
            chats.start_chat(Launch("premise", premise="A swap"), attach=[(WOLF, "companion")], scope=first.scope,
                             reuse_actors={("companion", WOLF.entity_key): character})
        assert refused.value.reason == "actor_mismatch"
        with pytest.raises(ChatModelError, match="instance-targeted"):  # keyed by its source, like a character
            chats.detach_source(first.chat, WOLF, "companion", actor=actor, expected=chats.open_chat(first.chat).etag)
        assert snapshot(root) == before

        chats.detach_source(first.chat, WOLF, "companion", expected=chats.open_chat(first.chat).etag).commit()
        record = chats.open_chat(first.chat).record
        assert record.active_bindings("companion") == () and record.roster_entry(actor) is not None


def test_a_build_without_the_role_refuses_its_chats_rather_than_reinterpreting_them(root, monkeypatch):
    monkeypatch.setattr(chat_model, "ROLES", {**chat_model.ROLES, "companion": COMPANION})
    with LibraryStore.open(root) as store:
        chat = chat_library(store).start_chat(Launch("premise", premise="A night hike"),
                                              attach=[(WOLF, "companion")]).commit().chat
    monkeypatch.undo()
    with LibraryStore.open(root) as store:
        before = snapshot(root)
        with pytest.raises(ChatCodecError) as refused:
            chat_library(store).read_chat(chat)
        assert refused.value.reason == "role_kind"
        assert snapshot(root) == before
