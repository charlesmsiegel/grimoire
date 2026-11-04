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
