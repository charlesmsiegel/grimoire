"""World-optional chats end to end on the real store (Sprint 107 proving scenario and failure cases).

Every fixture is synthetic under ``tmp_path``; no V1 or SillyTavern library is read. No model is
called: the provider package is never imported by these modules.
"""

from __future__ import annotations

import json

import pytest

from grimoire.chats import CHAT_SCHEMA, NOT_WITNESSED, BindingLimit, ChatCodecError, ChatModelError, ChatRecord
from grimoire.chats import CAMPAIGN_CONTINUITY, Launch, NewBinding, chat_to_payload
from grimoire.chatstore import ChatLibrary, InvalidBinding
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import LibraryStore, RecordEnvelope, ScopeRecord, StaleWrite, WriteDenied
from grimoire.testing.fileops import snapshot

from chat_fixtures import ANYA, CARDS, LORE, MARA, NAMESAKE, OTHER, SAM, STORM, WORLD, chat_library, seed_sources
from store_helpers import OWNER, owner_handle


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_sources(opened)
        yield opened


@pytest.fixture
def chats(store):
    return chat_library(store)


def start(chats: ChatLibrary, launch=None, **kwargs):
    return chats.start_chat(launch or Launch("character", MARA), **kwargs).commit()


def chat_files(store) -> list[str]:
    return sorted(path for path in snapshot(store.root) if "/chats/" in path)


# -- the proving scenario -------------------------------------------------------------------------

def test_proving_scenario_one_card_no_world_many_views(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_sources(store)
        chats = chat_library(store)
        first = start(chats, attach=[(LORE, "lorebook")], persona=SAM, label="Mara")
    assert first.chat == ChatRef(first.scope.campaign_id, first.chat.chat_id)

    with LibraryStore.open(root) as store:  # save, close, reopen
        chats = chat_library(store)
        view = chats.open_chat(first.chat)
        assert view.scope.world is None and view.scope.title is None  # no world, no campaign setup
        assert view.record.launch == Launch("character", MARA)
        assert view.record.persona.ref == SAM and view.broken == () and view.problems == ()
        nav = chats.navigation()
        assert nav.recent(10) == (first.chat,) and nav.by_source(MARA.unpinned()) == (first.chat,)

        chats._clock.ticks = 100  # the second chat happens later
        second = chats.start_chat(Launch("character", MARA)).commit()
        assert second.scope != first.scope and second.chat != first.chat
        first_actors = {e.actor for e in chats.open_chat(first.chat).record.roster}
        second_actors = {e.actor for e in chats.open_chat(second.chat).record.roster}
        assert first_actors.isdisjoint(second_actors)  # same card, independent lives

        etag = chats.open_chat(first.chat).etag
        etag = chats.attach_source(first.chat, STORM, "scenario", expected=etag).commit().etags[
            f"campaigns/{first.scope.campaign_id}/chats/{first.chat.chat_id}.json"]
        chats.attach_source(first.chat, ANYA, "character", expected=etag).commit()
        nav = chats.navigation()
        assert nav.by_source(STORM.unpinned()) == (first.chat,)
        assert nav.by_source(ANYA.unpinned()) == (first.chat,)
        assert set(nav.by_source(MARA.unpinned())) == {first.chat, second.chat}
        assert sorted(map(str, nav.all_chats())) == sorted(map(str, (first.chat, second.chat)))

        before = chats.open_chat(first.chat).record
        scope_etag = chats.open_chat(first.chat).scope_etag
        scope_etag = chats.name_scope(first.scope, "Harbor nights", expected=scope_etag).commit().etags[
            f"campaigns/{first.scope.campaign_id}/scope.json"]
        chats.attach_world(first.scope, WORLD, expected=scope_etag).commit()
        after = chats.open_chat(first.chat)
        assert after.record == before  # naming and world attach touch only the scope
        assert after.scope.title == "Harbor nights" and after.scope.world.source == WORLD
        nav = chats.navigation()
        assert nav.campaigns() == (first.scope,) and nav.by_world(WORLD) == (first.chat,)
        assert len(chat_files(store)) == 2


def test_scenario_only_opening_creates_no_actor(store, chats):
    before = snapshot(store.root)
    change = chats.start_chat(Launch("scenario", STORM), attach=[(LORE, "lorebook")])
    assert sorted(change.preview()) == [f"campaigns/{change.scope.campaign_id}/chats/{change.chat.chat_id}.json",
                                        f"campaigns/{change.scope.campaign_id}/scope.json"]
    result = change.commit()
    record = chats.open_chat(result.chat).record
    assert record.roster == () and result.actors == ()
    assert record.bindings[0].resolved_revision == "r2"  # what "follow the head" meant at launch
    added = set(snapshot(store.root)) - set(before)
    assert not [path for path in added if "/instances/" in path]


def test_a_premise_launch_binds_nothing_and_needs_no_source(store, chats):
    result = chats.start_chat(Launch("premise", premise="A storm closes the harbor")).commit()
    record = chats.open_chat(result.chat).record
    assert record.bindings == () and record.roster == () and result.actors == ()


def test_same_name_different_source_never_merges(store, chats):
    one = start(chats)
    other = start(chats, Launch("character", NAMESAKE))
    nav = chats.navigation()
    assert nav.by_source(MARA.unpinned()) == (one.chat,)
    assert nav.by_source(NAMESAKE.unpinned()) == (other.chat,)
    etag = chats.open_chat(one.chat).etag
    chats.attach_source(one.chat, NAMESAKE, "character", expected=etag).commit()
    roster = chats.open_chat(one.chat).record.roster
    assert [entry.source for entry in roster] == [MARA, NAMESAKE]
    assert len({entry.actor for entry in roster}) == 2


def test_a_late_character_is_new_and_has_witnessed_nothing(store, chats):
    first = start(chats)
    view = chats.open_chat(first.chat)
    before = snapshot(store.root)
    change = chats.attach_source(first.chat, ANYA, "character", expected=view.etag)
    campaign = first.scope.campaign_id
    assert sorted(change.preview()) == [f"campaigns/{campaign}/chats/{first.chat.chat_id}.json",
                                        f"campaigns/{campaign}/instances/character/anya-1.json"]
    change.commit()
    after = snapshot(store.root)
    assert sorted(path for path in set(after) | set(before) if after.get(path) != before.get(path)) == sorted(
        change.preview())  # nothing else: no event, belief or evidence record
    record = chats.open_chat(first.chat).record
    entry = record.roster_entry(InstanceRef(campaign, "character", "anya-1"))
    assert entry.prior_history == NOT_WITNESSED and entry.introduced_seq == record.seq > 0
    instance = store.read(entry.actor)
    assert instance.envelope.extra["schema"] == "grimoire.entity/0"
    assert instance.envelope.derived_from == str(ANYA) and instance.envelope.base == str(ANYA)


def test_edits_never_change_identity_provenance_or_history(store, chats):
    first = start(chats, attach=[(LORE, "lorebook")])
    origin = chats.open_chat(first.chat).record
    actors = [entry.actor for entry in origin.roster]
    files = chat_files(store)

    def etag():
        return chats.open_chat(first.chat).etag

    chats.set_label(first.chat, "Renamed", expected=etag()).commit()
    chats.attach_source(first.chat, STORM, "scenario", expected=etag()).commit()
    chats.detach_source(first.chat, LORE, "lorebook", expected=etag()).commit()
    chats.detach_source(first.chat, MARA, "character", expected=etag()).commit()
    view = chats.open_chat(first.chat)
    chats.name_scope(first.scope, "Named", expected=view.scope_etag).commit()
    record = chats.open_chat(first.chat).record
    assert (record.ref, record.created_at, record.launch) == (origin.ref, origin.created_at, origin.launch)
    assert [entry.actor for entry in record.roster] == actors  # detaching Mara kept her actor
    assert chat_files(store) == files
    nav = chats.navigation()
    assert nav.by_source(LORE.unpinned()) == () and nav.by_source(LORE.unpinned(), include_detached=True) == (
        first.chat,)
    assert nav.by_source(MARA.unpinned()) == (first.chat,)  # the launch is provenance


@pytest.mark.parametrize("make, reason", [
    (lambda c: c.start_chat(Launch("character", SourceRef(CARDS, "character", "ghost", "r1"))), "missing_entity"),
    (lambda c: c.start_chat(Launch("character", MARA.at("r9"))), "missing_revision"),
    (lambda c: c.start_chat(Launch("character", SourceRef("catalog:nowhere", "character", "x"))),
     "missing_source"),
    (lambda c: c.start_chat(Launch("character", MARA), attach=[(LORE, "character")]), "role_kind"),
    (lambda c: c.start_chat(Launch("character", MARA), persona=LORE), "role_kind"),
    (lambda c: c.start_chat(Launch("character", MARA), attach=[(MARA, "character")]), "duplicate_binding"),
    (lambda c: c.start_chat(Launch("character", MARA), reuse_actors={("character", MARA.entity_key):
                                                                     InstanceRef("q", "character", "x")}),
     "reuse_needs_scope"),
])
def test_invalid_bindings_write_nothing(store, chats, make, reason):
    before = snapshot(store.root)
    with pytest.raises((InvalidBinding, ChatModelError)) as error:
        make(chats).commit()
    assert error.value.reason == reason
    assert snapshot(store.root) == before


def test_a_second_persona_and_a_foreign_actor_are_refused(store, chats):
    first = start(chats, persona=SAM)
    view = chats.open_chat(first.chat)
    before = snapshot(store.root)
    with pytest.raises(ChatModelError) as error:
        chats.attach_source(first.chat, NAMESAKE, "persona", expected=view.etag)
    assert error.value.reason == "second_persona"
    mara_actor = view.record.roster[0].actor
    with pytest.raises(InvalidBinding) as error:
        chats.start_chat(Launch("character", ANYA), scope=first.scope,
                         reuse_actors={("character", ANYA.entity_key): mara_actor})
    assert error.value.reason == "actor_mismatch"
    assert snapshot(store.root) == before
    chats.set_persona(first.chat, NAMESAKE, expected=view.etag).commit()  # replacing is explicit
    record = chats.open_chat(first.chat).record
    assert record.persona.ref == NAMESAKE and len(record.roster) == 3


def test_validation_follows_load_rules_without_loading_the_library(store, chats, monkeypatch):
    # an implicit single-revision head is accepted
    result = chats.start_chat(Launch("character", MARA.unpinned())).commit()
    assert chats.open_chat(result.chat).record.bindings[0].resolved_revision == "r1"
    # a pinned revision in a source load() excludes is refused
    forged = json.loads((store.root / "sources/catalog/other/_source/records/character/mara/r1.json").read_bytes())
    forged["address"] = str(SourceRef("catalog:me", "character", "intruder", "r1"))
    (store.root / "sources/catalog/other/_source/records/character/intruder/r1.json").parent.mkdir(parents=True)
    (store.root / "sources/catalog/other/_source/records/character/intruder/r1.json").write_text(json.dumps(forged))
    with pytest.raises(InvalidBinding) as error:
        chats.start_chat(Launch("character", NAMESAKE))
    assert error.value.reason == "excluded_source"

    def no_load(*args, **kwargs):
        raise AssertionError("a write must not load the whole library")
    view = chats.open_chat(result.chat)
    monkeypatch.setattr(LibraryStore, "load", no_load)
    chat, scope = result.chat, result.scope
    chats.set_label(chat, "x", expected=view.etag).commit()
    etag = chats.read_chat(chat)[1]
    etag = chats.attach_source(chat, ANYA, "character", expected=etag).commit().etags[
        f"campaigns/{scope.campaign_id}/chats/{chat.chat_id}.json"]
    etag = chats.detach_source(chat, ANYA, "character", expected=etag).commit().etags[
        f"campaigns/{scope.campaign_id}/chats/{chat.chat_id}.json"]
    chats.set_persona(chat, SAM, expected=etag).commit()
    scope_etag = chats.read_scope(scope)[1]
    for change in (lambda e: chats.name_scope(scope, "N", expected=e), lambda e: chats.attach_world(
            scope, WORLD, expected=e), lambda e: chats.set_session_zero(scope, {"lines": []}, expected=e)):
        scope_etag = change(scope_etag).commit().etags[f"campaigns/{scope.campaign_id}/scope.json"]
    chats.start_chat(Launch("scenario", STORM), scope=scope).commit()
    chats.start_chat(Launch("premise", premise="fog")).commit()


def test_the_binding_bound_is_named_and_writes_nothing(store, chats):
    first = start(chats)
    record, etag = chats.read_chat(first.chat)
    for n in range(255):
        record = record.attach(NewBinding(SourceRef(CARDS, "lorebook", f"l{n}", "r1"), "lorebook", "r1"),
                               record.last_activity)
    txn = store.transaction()
    txn.put(ScopeRecord(first.chat, CHAT_SCHEMA, chat_to_payload(record)), etag)
    txn.commit(owner_handle())
    _, etag = chats.read_chat(first.chat)
    before = snapshot(store.root)
    with pytest.raises(BindingLimit):
        chats.attach_source(first.chat, LORE, "lorebook", expected=etag)
    assert snapshot(store.root) == before


def test_authorization_and_approvals(store):
    before = snapshot(store.root)
    denied = chat_library(store, owner_handle("sources"))
    with pytest.raises(WriteDenied):
        denied.start_chat(Launch("character", MARA)).commit()
    assert snapshot(store.root) == before

    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:chat", frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"), OWNER))
    gated = chat_library(store, authority.handle_for("app:chat"))
    change = gated.start_chat(Launch("character", MARA))
    preview = change.preview()  # what the human approves
    approvals = {}
    for number, (address, data) in enumerate(sorted(preview.items())):
        authority.register_approval(Approval(f"a{number}", "app:chat", Permission.APPLY,
                                             ResourcePath.parse(address), payload_digest(data), OWNER))
        approvals[address] = f"a{number}"
    result = change.commit(approvals)
    assert set(result.etags) == set(preview)
    with pytest.raises(Exception):
        change.commit(approvals)  # once only


def test_stale_expectations_write_nothing(store, chats):
    first = start(chats)
    old = chats.open_chat(first.chat).etag
    pending = chats.set_label(first.chat, "A", expected=old)
    chats.set_label(first.chat, "B", expected=old).commit()
    before = snapshot(store.root)
    with pytest.raises(StaleWrite):
        chats.set_label(first.chat, "C", expected=old)
    with pytest.raises(StaleWrite):
        pending.commit()  # the store's CAS is the authority
    assert snapshot(store.root) == before


def test_commit_rechecks_what_preview_allocated(store, chats):
    change = chats.start_chat(Launch("character", MARA))
    (store.root / "campaigns" / change.scope.campaign_id).mkdir(parents=True)
    with pytest.raises(StaleWrite):
        change.commit()
    (store.root / "campaigns" / change.scope.campaign_id).rmdir()

    first = start(chats)
    etag = chats.open_chat(first.chat).etag
    attach = chats.attach_source(first.chat, ANYA, "character", expected=etag)
    squatter = InstanceRef(first.scope.campaign_id, "item", attach.new_actors[0].instance_id)
    txn = store.transaction()
    txn.put(RecordEnvelope(squatter, {}), None)
    txn.commit(owner_handle())
    before = snapshot(store.root)
    with pytest.raises(StaleWrite, match="id taken"):
        attach.commit()
    assert snapshot(store.root) == before


def test_a_broken_source_is_reported_and_repaired_never_fatal(store, chats):
    first = start(chats, attach=[(LORE, "lorebook")])
    import shutil
    shutil.rmtree(store.root / "sources" / "catalog" / "cards")
    view = chats.open_chat(first.chat)
    assert {(b.field, b.reason) for b in view.broken} == {("launch", "missing_source"),
                                                          ("bindings.character", "missing_source"),
                                                          ("bindings.lorebook", "missing_source")}
    etag = chats.set_label(first.chat, "Still here", expected=view.etag).commit().etags[
        f"campaigns/{first.scope.campaign_id}/chats/{first.chat.chat_id}.json"]
    chats.detach_source(first.chat, LORE, "lorebook", expected=etag).commit()
    view = chats.open_chat(first.chat)
    assert ("bindings.lorebook", "missing_source") not in {(b.field, b.reason) for b in view.broken}
    assert [entry.actor for entry in view.record.roster]  # the actor is never lost
    nav = chats.navigation()
    assert nav.all_chats() == (first.chat,) and nav.by_scope(first.scope.campaign_id) == (first.chat,)


def test_world_drift_and_missing_actor_are_reported(store, chats):
    first = start(chats)
    view = chats.open_chat(first.chat)
    chats.attach_world(first.scope, WORLD, expected=view.scope_etag).commit()
    manifest = store.root / "sources/world/harbor/_source/manifest.json"
    manifest.write_text(manifest.read_text().replace('"Harbor"', '"Harbor (edited)"'))
    actor = view.record.roster[0].actor
    (store.root / f"campaigns/{actor.campaign_id}/instances/character/{actor.instance_id}.json").unlink()
    found = {(b.field, b.reason) for b in chats.open_chat(first.chat).broken}
    assert found == {("world", "world_drift"), ("roster.actor", "missing_actor")}
    with pytest.raises(InvalidBinding) as error:
        chats.attach_world(first.scope, "world:nowhere", expected=chats.read_scope(first.scope)[1])
    assert error.value.reason == "missing_source"


def test_a_newer_chat_is_listed_but_never_rewritten(store, chats):
    first = start(chats)
    stored = store.read(first.chat)
    newer = ScopeRecord(first.chat, "grimoire.chat/2", {**stored.record.payload, "new": True})
    txn = store.transaction()
    txn.put(newer, stored.etag)
    txn.commit(owner_handle())
    etag = store.read(first.chat).etag
    with pytest.raises(ChatCodecError) as error:
        chats.set_label(first.chat, "x", expected=etag)
    assert error.value.reason == "unsupported_schema"
    nav = chats.navigation()
    assert nav.all_chats() == (first.chat,) and dict(nav.problems()) == {first.chat: "undecodable"}


def test_an_orphan_chat_is_reported_not_lost(store, chats):
    first = start(chats)
    (store.root / f"campaigns/{first.scope.campaign_id}/scope.json").unlink()
    view = chats.open_chat(first.chat)
    assert view.problems == ("orphan_chat",) and view.scope is None
    assert dict(chats.navigation().problems()) == {first.chat: "orphan_chat"}


def test_allocation_skips_taken_and_look_alike_campaigns(store, chats):
    (store.root / "campaigns" / "q-1").mkdir(parents=True)
    (store.root / "campaigns" / "Q-2").mkdir()
    assert start(chats).scope == ScopeRef("q-3")


def test_a_legacy_campaign_is_continued_with_a_lazy_scope_and_reused_actor(store, chats):
    legacy = InstanceRef("legacy", "character", "mara-old")
    txn = store.transaction()
    txn.put(RecordEnvelope(legacy, {}, derived_from=str(MARA)), None)
    txn.commit(owner_handle())
    path = store.root / "campaigns/legacy/instances/character/mara-old.json"
    original = path.read_bytes()
    with pytest.raises(InvalidBinding) as error:
        chats.start_chat(Launch("character", MARA), scope=ScopeRef("absent"))
    assert error.value.reason == "missing_scope"
    change = chats.start_chat(Launch("character", MARA), scope=ScopeRef("legacy"),
                              reuse_actors={("character", MARA.entity_key): legacy})
    assert "campaigns/legacy/scope.json" in change.preview()
    result = change.commit()
    assert result.actors == () and path.read_bytes() == original
    entry = chats.open_chat(result.chat).record.roster[0]
    assert entry.actor == legacy and entry.prior_history == CAMPAIGN_CONTINUITY
    second = chats.start_chat(Launch("character", MARA), scope=ScopeRef("legacy")).commit()
    assert chats.open_chat(second.chat).record.roster[0].actor == InstanceRef("legacy", "character", "mara-1")
    assert set(chats.navigation().by_scope("legacy")) == {result.chat, second.chat}


def test_navigation_is_rebuilt_identically_after_reopen(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_sources(store)
        chats = chat_library(store)
        start(chats, attach=[(LORE, "lorebook")])
        start(chats, Launch("scenario", STORM))
        first = chats.navigation()
    with LibraryStore.open(root) as store:
        again = chat_library(store).navigation()
    assert again == first and again.all_chats() == first.all_chats()


def test_session_zero_is_stored_verbatim(store, chats):
    first = start(chats)
    data = {"boundaries": [{"theme": "spiders", "treatment": "avoid"}]}
    etag = chats.read_scope(first.scope)[1]
    chats.set_session_zero(first.scope, data, expected=etag).commit()
    path = store.root / f"campaigns/{first.scope.campaign_id}/scope.json"
    assert json.loads(path.read_bytes())["payload"]["session_zero"] == data
    assert chats.open_chat(first.chat).scope.session_zero == data
