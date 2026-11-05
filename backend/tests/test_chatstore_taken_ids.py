"""One reservation set for new instance ids (``campaign_taken_ids``): no new individual inherits a past.

A hand-deleted actor's instance file must not free its id for a different individual, who would
otherwise read the old one's private 020 knowledge, show its 018 art, or take its place in another
chat's roster. Everything that could name an id but cannot be read or listed fails closed with
``TakenIdsUnlisted`` rather than letting an allocation guess.
"""

from __future__ import annotations

import json
import shutil

import pytest

from grimoire.chats import Launch
from grimoire.chatstore import campaign_taken_ids
from grimoire.core import Event, Moment, Perception
from grimoire.core.identity import ChatRef, ImageRef, InstanceRef, ScopeRef
from grimoire.effectstore import accepted_instance_ids
from grimoire.evidencestore import EvidenceLibrary, evidence_instance_ids
from grimoire.holdingstore import HoldingsUnlisted
from grimoire.images import Hidden, Placement
from grimoire.imagestore import ImageLibrary, placement_subject_ids
from grimoire.ledgers import DurableAuthority
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, StaleWrite, TakenIdsUnlisted

from chat_fixtures import ANYA, LORE, MARA, chat_library, seed_sources
from effects_fixtures import CONTROLS, Handles, contribution, effects_library, give, seed
from image_fixtures import PNG, handle as image_handle
from ledger_helpers import open_authority
from store_helpers import OWNER, make_dir_link, owner_handle

Q = "q-1"
MARA_1 = InstanceRef(Q, "character", "mara-1")


def _first(store: LibraryStore) -> ChatRef:
    """Scope q-1 with chat t-1 and its actor mara-1."""
    seed_sources(store)
    started = chat_library(store).start_chat(Launch("character", MARA)).commit()
    assert started.actors == (MARA_1,)
    return started.chat


def _hand_delete(store: LibraryStore, ref: InstanceRef = MARA_1) -> None:
    store.root.joinpath("campaigns", ref.campaign_id, "instances", ref.kind, f"{ref.instance_id}.json").unlink()


def _next_mara(store: LibraryStore, handle=None) -> InstanceRef:
    chats = chat_library(store, handle)
    chats._new_id.counts.update({"q": 1, "t": 1})
    (actor,) = chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q)).commit().actors
    return actor


def _witness(store: LibraryStore, chat: ChatRef, actor: InstanceRef = MARA_1) -> EvidenceLibrary:
    evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
    evidence.append(Q, [Event(Q, "e1", "the vault is opened", Moment(1), "director"),
                        Perception(Q, "v1", actor, "e1", "witnessed", "PRIVATE-PERCEPT", Moment(1), "director",
                                   confirmed=True)], chat=chat).commit()
    return evidence


def _write(store: LibraryStore, relative: str, data: bytes) -> None:
    path = store.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# -- what stays taken ---------------------------------------------------------------------------

def test_a_private_witness_id_is_never_given_to_a_new_actor(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        evidence = _witness(store, _first(store))
        _hand_delete(store)
        new = _next_mara(store)
        assert new == InstanceRef(Q, "character", "mara-2")
        assert evidence.actor_knowledge(Q, new, as_of=9).evidence == ()


@pytest.mark.parametrize("art", ["hide", "set"])
def test_a_campaign_placement_subject_id_is_never_given_to_a_new_actor(tmp_path, art):
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        images = ImageLibrary(store, image_handle(), owner=OWNER)
        batch = images.change()
        if art == "hide":
            batch.place(MARA_1, "avatar", Placement("hide"), None)
        else:
            face = batch.add_image(f"campaign:{Q}", "old-face", PNG)
            batch.place(MARA_1, "avatar", Placement("set", "avatar", face), None)
        batch.commit()
        _hand_delete(store)
        new = _next_mara(store)
        assert new.instance_id == "mara-2"
        slot = images.effective_placements(new).slot("avatar")
        assert not isinstance(slot, Hidden)
        assert getattr(slot, "image", None) != ImageRef(f"campaign:{Q}", "old-face")


def test_an_actor_in_another_chats_roster_keeps_its_id(tmp_path):
    """A hand-deleted instance file no longer frees its id for a different chat in the same campaign."""
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        _hand_delete(store)  # nothing but chat t-1's roster names mara-1 now
        assert "mara-1" in campaign_taken_ids(store, Q)
        assert _next_mara(store).instance_id == "mara-2"


def test_an_undecodable_knowledge_record_still_protects_its_ids(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        _write(store, f"campaigns/{Q}/events/e-9.json", json.dumps({
            "format": "grimoire.knowledge-record/1", "address": f"campaign:{Q}#event/e-9",
            "schema": "grimoire.event/0", "payload": {"note": {f"campaign:{Q}/character/mara-7": [1]}}}).encode())
        assert EvidenceLibrary(store).read(Q, requested_by="director").quarantined  # the ledger cannot use it
        assert "mara-7" in evidence_instance_ids(store, Q)
        assert "mara-7" in campaign_taken_ids(store, Q)


def test_a_look_alike_placement_folder_keeps_the_id_it_folds_to(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        _write(store, f"campaigns/{Q}/placements/Character/Mara-2/avatar.json", b"{}")
        assert placement_subject_ids(store, Q) == {"mara-2"}
        _hand_delete(store)
        assert _next_mara(store).instance_id == "mara-3"


def test_an_absent_campaign_reserves_nothing(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        assert campaign_taken_ids(store, "q-404") == frozenset()


def test_a_chat_commit_rechecks_the_reservation_set(tmp_path):
    """An id that gained private knowledge between a chat's preview and its commit is not taken by it."""
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 1})
        change = chats.start_chat(Launch("character", ANYA), scope=ScopeRef(Q))
        (anya,) = change.new_actors
        _write(store, f"campaigns/{Q}/placements/character/{anya.instance_id}/avatar.json", b"{}")
        with pytest.raises(StaleWrite):
            change.commit()
        assert anya not in store.campaign_instances(Q)


# -- fail closed --------------------------------------------------------------------------------

def _linked(store: LibraryStore, tmp_path, folder: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    make_dir_link(store.root / "campaigns" / Q / folder, outside)


@pytest.mark.parametrize("damage", [
    "linked-events", "linked-evidence", "linked-placements", "malformed-event", "malformed-chat",
    "malformed-accepted", "malformed-relation", "linked-holdings",
])
def test_an_unreadable_family_refuses_allocation_by_name(tmp_path, damage):
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        kind, family = damage.split("-")
        if kind == "linked":
            _linked(store, tmp_path, family)
        else:
            folder = {"event": "events", "chat": "chats", "accepted": "accepted", "relation": "relations"}[family]
            _write(store, f"campaigns/{Q}/{folder}/x-9.json", b"{not json")
        before = _files(store)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 1})
        with pytest.raises(TakenIdsUnlisted) as caught:
            chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q))
        assert caught.value.paths  # names what could not be read, never content
        if family == "holdings":
            assert isinstance(caught.value, HoldingsUnlisted)
        assert _files(store) == before  # nothing allocated, nothing written


def _files(store: LibraryStore) -> dict[str, bytes]:
    """Every regular file not under a link (the sandbox snapshot refuses a linked folder)."""
    return {str(path.relative_to(store.root)): path.read_bytes() for path in sorted(store.root.rglob("*"))
            if path.is_file() and not any(parent.is_symlink() for parent in path.parents)}


def test_binding_without_a_new_actor_never_lists_the_reservation_set(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        chat = _first(store)
        _linked(store, tmp_path, "events")
        chats = chat_library(store)
        _, etag = chats.read_chat(chat)
        chats.attach_source(chat, LORE, "lorebook", expected=etag).commit()  # no actor id is allocated


def test_accepted_problem_files_are_never_skipped(tmp_path):
    """#586: ``accepted_instance_ids`` used to skip a file the store cannot read; now it fails closed."""
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        _write(store, f"campaigns/{Q}/accepted/e-9.json", b"{not json")
        with pytest.raises(TakenIdsUnlisted) as caught:
            accepted_instance_ids(store, Q)
        assert caught.value.paths == (f"campaigns/{Q}/accepted/e-9.json",)


def test_a_linked_events_folder_gives_accept_the_named_error(tmp_path):
    """#586: a symlinked ``events/`` made 021's event-id allocation give up with an unnamed StoreError."""
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store, handles)
        outside = tmp_path / "outside"
        outside.mkdir()
        make_dir_link(store.root / "campaigns/c/events", outside)
        proposal = give()
        with pytest.raises(TakenIdsUnlisted) as caught:
            effects_library(store, handles).accept(proposal, controls=CONTROLS, contributions=[contribution()],
                                                   endorsements=[handles.endorse(proposal)])
        assert caught.value.paths == ("campaigns/c/events",)


# -- the security ledger: a grant outlives what it was scoped to --------------------------------------
#
# A grant (``authority/security``) is scoped to an address, not to an individual: deleting
# ``campaigns/q-1`` or one instance file by hand leaves the grant, which would then cover a new
# campaign or actor given the same id. So every id a grant or approval names stays taken.

GRANTEE = "app:importer"


def _grant(store: LibraryStore, scope: str, *, revoke: bool = False) -> DurableAuthority:
    """Open the durable authority and record a real grant; the caller closes it.

    Once a library has a security ledger every write needs its authority, so the owner is also granted
    ``campaigns``, which names no one campaign or individual and so reserves nothing."""
    authority = open_authority(store)
    authority.add_grant(Grant("g0", OWNER, frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"), OWNER))
    authority.add_grant(Grant("g1", GRANTEE, frozenset({Permission.APPLY}), ResourcePath.parse(scope), OWNER))
    if revoke:
        authority.revoke_grant("g1")
    return authority


def _security_batch(entries: list[dict]) -> bytes:
    """A hand-written security batch: the scan reads it raw, so no chain, genesis or replay is needed."""
    return json.dumps({"format": "grimoire.ledger/1", "ledger": "security", "seq": 1,
                       "previous": None, "entries": entries}).encode()


def _granted(scope: str) -> dict:
    return {"schema": 1, "kind": "grant_added", "recorded_at": "2026-01-01T00:00:00+00:00",
            "grant": {"grant_id": "g1", "principal": GRANTEE, "permissions": ["apply"], "scope": scope,
                      "granted_by": OWNER}}


def _hand_delete_chat_and_actor(store: LibraryStore, chat: ChatRef) -> None:
    """Leave nothing in the campaign that names mara-1: only the security ledger still could."""
    (store.root / "campaigns" / Q / "chats" / f"{chat.chat_id}.json").unlink()
    _hand_delete(store)


def test_a_grant_on_a_hand_deleted_campaign_keeps_its_id_from_a_new_scope(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        authority = _grant(store, f"campaigns/{Q}")
        shutil.rmtree(store.root / "campaigns" / Q)
        chats = chat_library(store, authority.handle_for(OWNER, trusted=True))  # a fresh counter offers q-1 first
        change = chats.start_chat(Launch("character", MARA))
        assert change.scope == ScopeRef("q-2")
        assert change.commit().actors == (InstanceRef("q-2", "character", "mara-1"),)
        assert not (store.root / "campaigns" / Q).exists()
        authority.close()


def test_a_revoked_grant_on_one_file_of_a_hand_deleted_campaign_keeps_its_id(tmp_path):
    """Revoked still counts: repairing a broken ledger tail can undo the revocation."""
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        authority = _grant(store, f"campaigns/{Q}/relations/r-1.json", revoke=True)
        shutil.rmtree(store.root / "campaigns" / Q)
        chats = chat_library(store, authority.handle_for(OWNER, trusted=True))
        assert chats.start_chat(Launch("character", MARA)).scope == ScopeRef("q-2")
        authority.close()


def test_a_grant_on_every_campaign_names_none(tmp_path):
    """The control for the tests above: ``campaigns`` covers every campaign, new ones included, by design."""
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        authority = _grant(store, "campaigns")
        shutil.rmtree(store.root / "campaigns" / Q)
        chats = chat_library(store, authority.handle_for(OWNER, trusted=True))
        assert chats.start_chat(Launch("character", MARA)).scope == ScopeRef(Q)
        authority.close()


def test_a_new_scope_commit_rechecks_for_a_grant_made_since_the_plan(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        seed_sources(store)
        change = chat_library(store).start_chat(Launch("character", MARA))
        assert change.scope == ScopeRef(Q)
        _write(store, "authority/security/0000000001.json", _security_batch([_granted(f"campaigns/{Q}")]))
        with pytest.raises(StaleWrite) as caught:
            change.commit()
        assert caught.value.stale == (("authority/security", f"a grant or approval names campaigns/{Q}"),)
        assert not (store.root / "campaigns" / Q).exists()


@pytest.mark.parametrize("scope", [
    "instances/character/mara-1.json",  # the instance file itself
    "holdings/character/mara-1.json",  # its holder file (023)
    "placements/character/mara-1",  # its placement folder (018)
    "placements/character/mara-1/avatar.json",  # anything finer that names it
])
def test_a_grant_on_a_hand_deleted_instance_keeps_its_id_from_a_new_actor(tmp_path, scope):
    with LibraryStore.create(tmp_path / "lib") as store:
        chat = _first(store)
        authority = _grant(store, f"campaigns/{Q}/{scope}")
        _hand_delete_chat_and_actor(store, chat)
        assert "mara-1" in campaign_taken_ids(store, Q)
        assert _next_mara(store, authority.handle_for(OWNER, trusted=True)) == InstanceRef(Q, "character", "mara-2")
        authority.close()


def test_a_grant_on_a_kind_folder_names_no_individual(tmp_path):
    """The control for the test above: with nothing naming mara-1, its id is free again."""
    with LibraryStore.create(tmp_path / "lib") as store:
        chat = _first(store)
        authority = _grant(store, f"campaigns/{Q}/instances/character")
        _hand_delete_chat_and_actor(store, chat)
        assert _next_mara(store, authority.handle_for(OWNER, trusted=True)) == MARA_1
        authority.close()


def test_approvals_and_garbled_grants_are_read_raw_but_decisions_are_not(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        _write(store, "authority/security/0000000001.json", _security_batch([
            _granted(f"campaigns/{Q}/instances/character/Zed-1.json"),  # replay refuses it; it folds to zed-1
            {"kind": "approval_registered", "approval": {
                "resource": f"campaigns/{Q}/instances/character/anya-4.json", "payload_digest": "sha256:x"}},
            {"kind": "decision", "decision": {
                "resource": f"campaigns/{Q}/instances/character/wren-2.json", "allowed": True}},
            _granted("campaigns/q-9/instances/character/other-1.json"),  # another campaign
        ]))
        taken = campaign_taken_ids(store, Q)
        assert {"zed-1", "anya-4"} <= taken
        assert not {"wren-2", "other-1"} & taken


@pytest.mark.parametrize("damage", ["malformed batch", "look-alike batch", "look-alike folder", "unlistable folder"])
def test_an_unreadable_security_ledger_fails_both_allocations_closed(tmp_path, monkeypatch, damage):
    with LibraryStore.create(tmp_path / "lib") as store:
        _first(store)
        good = _security_batch([_granted(f"campaigns/{Q}/instances/character/zed-1.json")])
        if damage == "malformed batch":
            _write(store, "authority/security/0000000001.json", b"{not json")
        elif damage == "look-alike batch":
            _write(store, "authority/security/0000000001.JSON", good)
        elif damage == "look-alike folder":
            _write(store, "authority/Security/0000000001.json", good)
            if (store.root / "authority" / "security").exists():
                pytest.skip("a case-insensitive filesystem: Security is the real folder")
        else:
            _write(store, "authority/security/0000000001.json", good)
            real = store._access.list_dir

            def failing(parts):
                if tuple(parts) == ("authority", "security"):
                    raise PermissionError("denied")
                return real(parts)

            monkeypatch.setattr(store._access, "list_dir", failing)
        before = _files(store)
        with pytest.raises(TakenIdsUnlisted) as caught:
            _next_mara(store)  # a new actor in an existing scope
        assert caught.value.paths
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1})
        with pytest.raises(TakenIdsUnlisted):
            chats.start_chat(Launch("character", MARA))  # a new scope
        assert _files(store) == before


def test_a_new_scope_reads_the_security_ledger_once_to_plan_and_once_to_commit(tmp_path, monkeypatch):
    """The security ledger is the whole library's audit and is read under the store lock: a new scope
    pays one read to allocate (its individuals cannot be named once its campaign id is not) and one
    at commit, not one more for each reservation set."""
    with LibraryStore.create(tmp_path / "lib") as store:
        seed_sources(store)
        reads: list[str] = []
        real = store.ledger_documents

        def counting(name):
            reads.append(name)
            return real(name)

        monkeypatch.setattr(store, "ledger_documents", counting)
        change = chat_library(store).start_chat(Launch("character", MARA))
        assert reads.count("security") == 1
        change.commit()
        assert reads.count("security") == 2
