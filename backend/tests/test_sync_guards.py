"""Guards that mutation testing showed no other test pinned (019 brutal-review round 13).

Each test fails if the guard named in its docstring is removed.
"""

from __future__ import annotations

import pytest

import grimoire.sync.session as sync_session
from grimoire.core.identity import ChatRef, InstanceRef, KnowledgeRef, ScopeRef, SourceRef
from grimoire.store import KnowledgeRecord, LinkSpec, RecordEnvelope, ScopeRecord, SourceManifest, etag
from grimoire.sync import ResolveRefused, identify, resolve, sync

from store_helpers import WREN_R1, instance
from sync_helpers import DESKTOP, PATHS, PHONE, WREN_A, edit_instance, handle, handles, kept, make_pair, put

R9 = PATHS["r1"].replace("r1.json", "r9.json")


def _harbor(store, heads, links=(), title=None):
    current = store.read_source("world:harbor")
    return SourceManifest(current.manifest.source, current.manifest.owner, title or current.manifest.title, heads,
                          links, extra=current.manifest.extra), current.etag


def _r9_on_both(desktop, phone, heads, titles=("Harbor", "Harbor")):
    for store, bio, title in ((desktop, "captain", titles[0]), (phone, "pirate", titles[1])):
        txn = store.transaction()
        txn.put(RecordEnvelope(SourceRef("world:harbor", "character", "wren", "r9"), {"bio": bio}), None)
        txn.put(*_harbor(store, heads, title=title))
        txn.commit(handle())
    return sync(desktop, phone, handles())


def test_a_pin_in_a_link_extra_is_a_dependency(tmp_path):
    """``replica.pinned_by`` reads link ``extra``: a manifest naming a revision still in conflict waits."""
    desktop, phone = make_pair(tmp_path)
    try:
        _r9_on_both(desktop, phone, {"character/wren": "r1"})
        a, b = (SourceRef("world:harbor", "lore", name, "r1") for name in ("a", "b"))
        txn = desktop.transaction()
        txn.put(RecordEnvelope(a, {}), None)
        txn.put(RecordEnvelope(b, {}), None)
        txn.put(*_harbor(desktop, {"character/wren": "r1", "lore/a": "r1", "lore/b": "r1"},
                         (LinkSpec(str(a), "derived_variant", str(b), extra={"via": "world:harbor/character/wren@r9"}),)))
        txn.commit(handle())
        assert sync(desktop, phone, handles()).conflict(PATHS["manifest"]).code == "head_dependency"
    finally:
        desktop.close()
        phone.close()


@pytest.mark.parametrize("payload, extra", [({"history": []}, {"x-actor": "campaign:c1/character/vex"}),
                                            ({"roles": {"campaign:c1/character/vex": "lead"}}, {})],
                         ids=["in_extra", "as_a_key"])
def test_an_actor_named_in_extra_fields_or_as_a_key_is_a_dependency(tmp_path, payload, extra):
    """``replica.instances_named`` walks unknown fields and mapping keys too."""
    desktop, phone = make_pair(tmp_path)
    try:
        put(desktop, instance(InstanceRef("c1", "character", "vex"), WREN_R1), None)
        put(desktop, ScopeRecord(ChatRef("c1", "talk"), "grimoire.test-chat/0", payload, extra=extra), None)
        (phone.root / "campaigns/c1/instances/character/Vex.json").write_bytes(b"{}")
        report = sync(desktop, phone, handles())
        assert report.conflict("campaigns/c1/instances/character/vex.json").code == "name_collision"
        assert report.conflict("campaigns/c1/chats/talk.json").code == "head_dependency"
    finally:
        desktop.close()
        phone.close()


def test_an_oversized_file_is_unreadable_and_receives_no_kept_copy(tmp_path):
    """SYNC.md: a device whose own file could not even be read (oversized) receives nothing."""
    desktop, phone = make_pair(tmp_path)
    try:
        edit_instance(desktop, WREN_A, 9)
        target = phone.root / PATHS["wren_a"]
        target.write_bytes(target.read_bytes().rstrip() + b" " * (9 * 1024 * 1024) + b"\n")
        report = sync(desktop, phone, handles())
        assert report.conflict(PATHS["wren_a"]).code == "blocked" and kept(phone) == []
    finally:
        desktop.close()
        phone.close()


def test_knowledge_whose_origin_scope_is_blocked_waits(tmp_path):
    """A blocked origin scope is unknown, never "no campaign": the knowledge does not travel."""
    desktop, phone = make_pair(tmp_path)
    try:
        txn = desktop.transaction()
        txn.put(ScopeRecord(ScopeRef("c9"), "grimoire.scope/0", {}), None)
        txn.put(KnowledgeRecord(KnowledgeRef("c9", "event", "e1"), "grimoire.test-knowledge/0", {"text": "x"}), None)
        txn.commit(handle())
        (desktop.root / "campaigns/c9/scope.json").write_bytes(b"{")  # malformed: blocked on the origin
        report = sync(desktop, phone, handles())
        assert report.conflict("campaigns/c9/events/e1.json").code == "head_dependency"
        assert not (phone.root / "campaigns/c9/events/e1.json").exists()
    finally:
        desktop.close()
        phone.close()


def test_resolve_refuses_knowledge_whose_campaign_the_receiver_lacks(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        txn = desktop.transaction()
        txn.put(ScopeRecord(ScopeRef("c9"), "grimoire.scope/0", {}), None)
        txn.put(KnowledgeRecord(KnowledgeRef("c9", "event", "e1"), "grimoire.test-knowledge/0", {"text": "x"}), None)
        txn.commit(handle())
        identify(phone, "tablet", replace=True)  # every base uncertain: nothing is copied
        grants = {DESKTOP: handle(), "tablet": handle()}
        sync(desktop, phone, grants)
        event = "campaigns/c9/events/e1.json"
        assert not (phone.root / event).exists() and not (phone.root / "campaigns/c9/scope.json").exists()
        mine = (desktop.root / event).read_bytes()
        with pytest.raises(ResolveRefused, match="campaign"):
            resolve(desktop, phone, event, etag(mine), {DESKTOP: etag(mine), "tablet": None}, grants)
        assert not (phone.root / event).exists()
    finally:
        desktop.close()
        phone.close()


def test_a_revision_resolve_refuses_while_the_manifests_naming_it_differ(tmp_path):
    """The identical-manifest exemption needs the receiver's manifest to equal the winner's: otherwise
    the receiver's manifest would silently mean the winner's bytes (SYNC.md: neither resolves first)."""
    desktop, phone = make_pair(tmp_path)
    try:
        _r9_on_both(desktop, phone, {"character/wren": "r9"}, ("Harbor", "Harbor (phone)"))
        mine, theirs = (desktop.root / R9).read_bytes(), (phone.root / R9).read_bytes()
        with pytest.raises(ResolveRefused):
            resolve(desktop, phone, R9, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
        assert (desktop.root / R9).read_bytes() == mine
    finally:
        desktop.close()
        phone.close()


def test_a_restore_after_a_crash_before_the_archives_is_never_overwritten(tmp_path, monkeypatch):
    """The marker rides the data commit, before any archive: a crash there, then the old bytes restored
    by hand (ABA), is a conflict, never a silent re-copy."""
    desktop, phone = make_pair(tmp_path)
    try:
        old = (phone.root / PATHS["wren_a"]).read_bytes()
        edit_instance(desktop, WREN_A, 9)

        def die(*args, **kwargs):
            raise RuntimeError("power loss before the archives")
        monkeypatch.setattr(sync_session, "_commit_archive", die)
        with pytest.raises(RuntimeError):
            sync(desktop, phone, handles())
        monkeypatch.undo()
        assert (phone.root / PATHS["wren_a"]).read_bytes() == (desktop.root / PATHS["wren_a"]).read_bytes()
        (phone.root / PATHS["wren_a"]).write_bytes(old)
        report = sync(desktop, phone, handles())
        assert (phone.root / PATHS["wren_a"]).read_bytes() == old and report.conflict(PATHS["wren_a"]) is not None
    finally:
        desktop.close()
        phone.close()


ZZ = InstanceRef("c1", "character", "zz")
ZZ_PATH = "campaigns/c1/instances/character/zz.json"


def _crash_before_archives(monkeypatch):
    def die(*args, **kwargs):
        raise RuntimeError("power loss before the archives")
    monkeypatch.setattr(sync_session, "_commit_archive", die)
    with pytest.raises(RuntimeError):
        sync(*_crash_before_archives.pair, handles())
    monkeypatch.undo()


@pytest.mark.parametrize("how", ["ended_by_hand", "created_identically", "ended_by_hand_with_a_copy"])
@pytest.mark.parametrize("crash", [False, True])
def test_an_agreement_on_a_base_that_does_not_say_so_survives_a_crash_before_the_archives(
        tmp_path, monkeypatch, how, crash):
    """A conflict ended by hand (a `none` base) or byte-identical records made offline (107's actors)
    are agreed and marked in the data commits: a crash before the archives never lets a later
    one-sided deletion be undone by a create-only copy."""
    desktop, phone = make_pair(tmp_path)
    try:
        put(desktop, instance(ZZ, WREN_R1, hp=1), None)
        put(phone, instance(ZZ, WREN_R1, hp=1 if how == "created_identically" else 2), None)
        if how != "created_identically":
            assert sync(desktop, phone, handles()).conflict(ZZ_PATH).code == "missing_ancestor"
            (desktop.root / ZZ_PATH).write_bytes((phone.root / ZZ_PATH).read_bytes())  # ended by hand
        if how == "ended_by_hand_with_a_copy":
            edit_instance(phone, InstanceRef("c2", "character", "wren-b"), 7)  # the sync visibly copies too
        if crash:
            _crash_before_archives.pair = (desktop, phone)
            _crash_before_archives(monkeypatch)
        else:
            sync(desktop, phone, handles())
        (phone.root / ZZ_PATH).unlink()
        report = sync(desktop, phone, handles())
        assert not (phone.root / ZZ_PATH).exists(), report.copied
        if crash:
            assert report.conflict(ZZ_PATH) is not None
        else:  # the archives record the agreement: the deletion propagates (028)
            assert (ZZ_PATH, DESKTOP) in report.deleted and not (desktop.root / ZZ_PATH).exists()
    finally:
        desktop.close()
        phone.close()


@pytest.mark.parametrize("resolving", [False, True])
def test_a_chat_never_lands_without_its_play_scope(tmp_path, resolving):
    """107 homes a chat in its campaign's scope: a chat whose scope cannot land (here: denied) waits,
    and resolve refuses to deliver it alone, rather than leaving an `orphan_chat` on the receiver."""
    from chat_fixtures import MARA, chat_library, seed_sources
    from grimoire.chats.model import Launch
    from grimoire.store import LibraryStore
    from store_helpers import owner_handle
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    try:
        seed_sources(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())
        started = chat_library(desktop).start_chat(Launch("character", MARA)).commit()
        c = started.chat.campaign_id
        chat = f"campaigns/{c}/chats/{started.chat.chat_id}.json"
        if resolving:
            identify(phone, "tablet", replace=True)  # every base uncertain: nothing is copied
            sync(desktop, phone, {DESKTOP: handle(), "tablet": handle()})
            (phone.root / f"campaigns/{c}/scope.json").unlink(missing_ok=True)
            for actor in started.actors:  # the chat's actor is delivered first: only the scope is missing
                path = f"campaigns/{actor.campaign_id}/instances/{actor.kind}/{actor.instance_id}.json"
                data = (desktop.root / path).read_bytes()
                resolve(desktop, phone, path, etag(data), {DESKTOP: etag(data), "tablet": None},
                        {DESKTOP: handle(), "tablet": handle()})
            mine = (desktop.root / chat).read_bytes()
            assert not (phone.root / chat).exists()
            with pytest.raises(ResolveRefused, match="scope"):
                resolve(desktop, phone, chat, etag(mine), {DESKTOP: etag(mine), "tablet": None},
                        {DESKTOP: handle(), "tablet": handle()})
        else:
            narrow = owner_handle("sources", "kept", f"campaigns/{c}/chats", f"campaigns/{c}/instances")
            report = sync(desktop, phone, {DESKTOP: handle(), PHONE: narrow})
            assert report.conflict(f"campaigns/{c}/scope.json").code == "denied"
            assert report.conflict(chat) is not None
        assert not (phone.root / chat).exists()
    finally:
        desktop.close()
        phone.close()


def test_a_pin_in_a_manifest_extra_is_a_dependency(tmp_path):
    """``replica.pinned_by`` reads a manifest's unknown fields too."""
    desktop, phone = make_pair(tmp_path)
    try:
        _r9_on_both(desktop, phone, {"character/wren": "r1"})
        current = desktop.read_source("world:harbor")
        m = current.manifest
        txn = desktop.transaction()
        txn.put(SourceManifest(m.source, m.owner, m.title, {"character/wren": "r1"}, m.links,
                               extra={**m.extra, "x-pin": "world:harbor/character/wren@r9"}), current.etag)
        txn.commit(handle())
        assert sync(desktop, phone, handles()).conflict(PATHS["manifest"]).code == "head_dependency"
    finally:
        desktop.close()
        phone.close()


def test_an_uncertain_marker_is_cleared_once_the_path_agrees_again(tmp_path):
    """Otherwise every later edit of the path would be a conflict forever."""
    desktop, phone = make_pair(tmp_path)
    try:
        (phone.root / ".sync/peers/desktop/archive.json").unlink()  # one archive lost: every base uncertain
        edit_instance(desktop, WREN_A, 9)
        assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]).code == "missing_ancestor"
        (phone.root / PATHS["wren_a"]).write_bytes((desktop.root / PATHS["wren_a"]).read_bytes())  # ended by hand
        assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]) is None
        edit_instance(desktop, WREN_A, 12)
        assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]) is None
        assert (phone.root / PATHS["wren_a"]).read_bytes() == (desktop.root / PATHS["wren_a"]).read_bytes()
    finally:
        desktop.close()
        phone.close()


def test_resolve_refuses_a_chat_whose_actor_was_deleted_on_the_receiver(tmp_path):
    """The plain "absent on the receiver" branch (no look-alike, nothing blocked)."""
    from chat_fixtures import ANYA, MARA, chat_library
    from grimoire.chats.model import Launch
    from test_sync_dependencies import _chat_pair
    desktop, phone = _chat_pair(tmp_path)
    try:
        chat = chat_library(desktop).start_chat(Launch("character", MARA)).commit().chat
        sync(desktop, phone, handles())
        on_desktop, on_phone = chat_library(desktop), chat_library(phone)
        on_desktop.set_label(chat, "Harbor night", expected=on_desktop.read_chat(chat)[1]).commit()
        actor = on_phone.attach_source(chat, ANYA, "character", expected=on_phone.read_chat(chat)[1]).commit().actors[0]
        path = f"campaigns/{chat.campaign_id}/chats/{chat.chat_id}.json"
        assert sync(desktop, phone, handles()).conflict(path).code == "concurrent"
        (desktop.root / f"campaigns/{actor.campaign_id}/instances/character/{actor.instance_id}.json").unlink()
        mine, theirs = (desktop.root / path).read_bytes(), (phone.root / path).read_bytes()
        with pytest.raises(ResolveRefused, match="missing"):
            resolve(desktop, phone, path, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
        assert (desktop.root / path).read_bytes() == mine
    finally:
        desktop.close()
        phone.close()


def test_resolve_refuses_knowledge_whose_campaign_scope_is_blocked_on_the_winner(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        txn = desktop.transaction()
        txn.put(ScopeRecord(ScopeRef("c9"), "grimoire.scope/0", {}), None)
        txn.put(KnowledgeRecord(KnowledgeRef("c9", "event", "e1"), "grimoire.test-knowledge/0", {"text": "x"}), None)
        txn.commit(handle())
        identify(phone, "tablet", replace=True)
        grants = {DESKTOP: handle(), "tablet": handle()}
        sync(desktop, phone, grants)
        (desktop.root / "campaigns/c9/scope.json").write_bytes(b"{")  # unreadable on the winner
        event = "campaigns/c9/events/e1.json"
        mine = (desktop.root / event).read_bytes()
        with pytest.raises(ResolveRefused, match="campaign"):
            resolve(desktop, phone, event, etag(mine), {DESKTOP: etag(mine), "tablet": None}, grants)
        assert not (phone.root / event).exists()
    finally:
        desktop.close()
        phone.close()


def test_resolve_verifies_its_copy_so_a_restore_right_after_it_is_never_archived_as_agreed(tmp_path, monkeypatch):
    """The old bytes saved again right after resolve's data commit: resolve's verification stops it before
    the archives, and the chosen version survives the next syncs."""
    from grimoire.store.library import Transaction
    from grimoire.sync import SyncInterrupted
    desktop, phone = make_pair(tmp_path)
    try:
        edit_instance(desktop, WREN_A, 9)
        edit_instance(phone, WREN_A, 4)
        sync(desktop, phone, handles())
        mine, theirs = (desktop.root / PATHS["wren_a"]).read_bytes(), (phone.root / PATHS["wren_a"]).read_bytes()
        real = Transaction.commit

        def commit_then_restore(self, *args, **kwargs):
            result = real(self, *args, **kwargs)
            if PATHS["wren_a"] in result.etags:
                (desktop.root / PATHS["wren_a"]).write_bytes(mine)
            return result
        monkeypatch.setattr(Transaction, "commit", commit_then_restore)
        with pytest.raises(SyncInterrupted, match="verification"):
            resolve(desktop, phone, PATHS["wren_a"], etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)},
                    handles())
        monkeypatch.undo()
        for _ in range(3):
            sync(desktop, phone, handles())
        assert (phone.root / PATHS["wren_a"]).read_bytes() == theirs
    finally:
        desktop.close()
        phone.close()
