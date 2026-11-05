"""Sync against the families another store writes several files at a time (brutal-review round 6).

020 commits an append as its records plus one batch; 021 commits an event with the relations it
changes. Sync must never deliver half of either, nor forget what it held back.
"""

from __future__ import annotations

import shutil

import pytest

from grimoire.core import DIRECTOR, Event
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore, etag
from grimoire.store import engine
from grimoire.sync import ResolveRefused, identify, resolve, sync

from effects_fixtures import C as EFFECTS_C, CONTROLS, Handles, contribution, effects_library, give
from effects_fixtures import seed as seed_effects
from evidence_fixtures import C as EVIDENCE_C, m
from store_helpers import OWNER, make_dir_link, owner_handle
from sync_helpers import DESKTOP, PHONE, handles
from test_evidencestore_scenarios import seed_actors

SCOPES = ("sources", "campaigns", "kept")


def _evidence_pair(tmp_path):
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed_actors(desktop)
    phone = LibraryStore.create(tmp_path / PHONE)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    sync(desktop, phone, handles())
    return desktop, phone


def _evidence(store):
    return EvidenceLibrary(store, owner_handle(*SCOPES), owner=OWNER)


def test_020_two_offline_appends_never_freeze_either_knowledge_log(tmp_path):
    """The batches conflict; no record of either append crosses without its batch, so both logs stay healthy."""
    desktop, phone = _evidence_pair(tmp_path)
    try:
        _evidence(desktop).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-desk", "desk event", m(1), "director")]).commit()
        _evidence(phone).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-phone", "phone event", m(1), "director")]).commit()
        report = sync(desktop, phone, handles())
        assert [conflict.code for conflict in report.conflicts] == ["missing_ancestor"]  # the batch
        assert not [path for path, _ in report.copied if "/events/" in path or "/evidence/" in path]
        assert any("/events/" in path for path in report.skipped[DESKTOP])  # reported, never silently gone
        assert any("/events/" in path for path in report.skipped[PHONE])
        for store, device in ((desktop, DESKTOP), (phone, PHONE)):
            log = _evidence(store).read(EVIDENCE_C, requested_by="director")
            assert log.problems == () and log.quarantined == ()
            _evidence(store).append(EVIDENCE_C, [Event(EVIDENCE_C, f"e-next-{device}", "later", m(2),
                                                       "director")]).commit()  # never refused
    finally:
        desktop.close()
        phone.close()


def test_020_one_devices_append_still_syncs_whole(tmp_path):
    desktop, phone = _evidence_pair(tmp_path)
    try:
        _evidence(desktop).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-desk", "desk event", m(1), "director")]).commit()
        report = sync(desktop, phone, handles())
        copied = {path for path, device in report.copied if device == PHONE}
        assert any("/batch/" in path for path in copied) and any("/events/" in path for path in copied)
        assert report.conflicts == ()
        assert _evidence(phone).read(EVIDENCE_C, requested_by="director").problems == ()
    finally:
        desktop.close()
        phone.close()


def _effects_pair(tmp_path):
    keys = Handles()
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed_effects(desktop, keys)
    phone = LibraryStore.create(tmp_path / PHONE)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    first = sync(desktop, phone, handles())
    return desktop, phone, keys, sorted(path for path, _ in first.copied if "/relations/" in path)


def _accept(desktop, keys):
    proposal = give()
    effects_library(desktop, keys).accept(proposal, controls=CONTROLS, contributions=[contribution()],
                                          endorsements=[keys.endorse(proposal)]).commit()


@pytest.mark.parametrize("how", ["unreadable", "link"])
def test_021_an_accepted_folder_that_cannot_be_listed_still_holds_relations_back(tmp_path, monkeypatch, how):
    desktop, phone, keys, _ = _effects_pair(tmp_path)
    try:
        _accept(desktop, keys)
        if how == "unreadable":
            real = engine.Access.list_dir

            def busy(self, parts):
                if tuple(parts) == ("campaigns", EFFECTS_C, "accepted"):
                    raise PermissionError(13, "held by another program")
                return real(self, parts)
            monkeypatch.setattr(engine.Access, "list_dir", busy)
        else:
            folder = desktop.root / "campaigns" / EFFECTS_C / "accepted"
            shutil.move(str(folder), str(tmp_path / "elsewhere"))
            make_dir_link(folder, tmp_path / "elsewhere")
        report = sync(desktop, phone, handles())
        assert not [path for path, _ in report.copied if "/relations/" in path]  # fails closed
    finally:
        desktop.close()
        phone.close()


def test_021_resolve_refuses_a_held_relation(tmp_path):
    desktop, phone, keys, _ = _effects_pair(tmp_path)
    try:
        path = f"campaigns/{EFFECTS_C}/relations/r-seed-1.json"
        _accept(desktop, keys)  # the event ends this relation on the desktop
        assert path in sync(desktop, phone, handles()).skipped[DESKTOP]
        mine, theirs = (desktop.root / path).read_bytes(), (phone.root / path).read_bytes()
        with pytest.raises(ResolveRefused, match="held back"):
            resolve(desktop, phone, path, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
        assert (phone.root / path).read_bytes() == theirs
        assert effects_library(phone, keys).continuity(EFFECTS_C, as_of=8, requested_by=DIRECTOR).problems == ()
    finally:
        desktop.close()
        phone.close()


def test_021_a_held_relation_keeps_its_uncertainty_until_the_hold_lifts(tmp_path):
    """A relation deleted before a re-identification stays uncertain while held: no create-only copy later."""
    desktop, phone, keys, relations = _effects_pair(tmp_path)
    try:
        victim = relations[0]
        (phone.root / victim).unlink()
        identify(phone, "tablet", replace=True)  # retires the phone's archive, which named the relation
        renamed = {DESKTOP: handles()[DESKTOP], "tablet": handles()[PHONE]}
        _accept(desktop, keys)
        sync(desktop, phone, renamed)  # held: fresh archives, but the relation's uncertainty is kept
        shutil.move(str(desktop.root / "campaigns" / EFFECTS_C / "accepted"), str(tmp_path / "moved-away"))
        report = sync(desktop, phone, renamed)  # the hold lifts
        assert not (phone.root / victim).exists()
        assert report.conflict(victim) is not None
    finally:
        desktop.close()
        phone.close()


# -- round 7: every reference, not only manifest heads, is a dependency ------------------------------

def _mara_r2_pair(tmp_path):
    from grimoire.store import RecordEnvelope, SourceManifest
    from chat_fixtures import CARDS, MARA, seed_sources
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    seed_sources(desktop)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    sync(desktop, phone, handles())
    for store, bio in ((desktop, "a pirate"), (phone, "a sailor")):  # one revision id, two contents
        txn = store.transaction()
        txn.put(RecordEnvelope(MARA.at("r2"), {"bio": bio}, name="Mara"), None)
        txn.put(SourceManifest(CARDS, OWNER, "Cards", {"scenario/storm": "r2", "character/mara": "r2"}),
                store.read_source(CARDS).etag)
        txn.commit(owner_handle())
    return desktop, phone


MARA_R2 = "sources/catalog/cards/_source/records/character/mara/r2.json"


def test_107_a_chat_waits_for_its_actor_and_the_revision_it_binds(tmp_path):
    """A chat binding a revision that differs on the destination, or naming an actor it lacks, never lands:
    it would mean other bytes there, and pin a revision conflict so that no resolve could end it."""
    from grimoire.chats.model import Launch
    from chat_fixtures import MARA, chat_library
    desktop, phone = _mara_r2_pair(tmp_path)
    try:
        chat_library(desktop).start_chat(Launch("character", MARA.at("r2"))).commit()
        report = sync(desktop, phone, handles())
        assert not [path for path, _ in report.copied if "/chats/" in path]  # (the scope names nothing: it may go)
        assert {conflict.code for conflict in report.conflicts if "/chats/" in conflict.path} == {"head_dependency"}
        mine, theirs = (desktop.root / MARA_R2).read_bytes(), (phone.root / MARA_R2).read_bytes()
        assert resolve(desktop, phone, MARA_R2, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)},
                       handles()) == PHONE
        later = sync(desktop, phone, handles())  # now the revision agrees: actor, chat and scope travel
        assert any("/chats/" in path for path, _ in later.copied)
    finally:
        desktop.close()
        phone.close()


def test_a_denied_revision_holds_back_the_instance_that_pins_it(tmp_path):
    from grimoire.core.identity import InstanceRef
    from store_helpers import HARBOR, WREN_R2, harbor_manifest, instance, wren
    from sync_helpers import handle, make_pair
    desktop, phone = make_pair(tmp_path)
    try:
        txn = desktop.transaction()
        txn.put(wren("r2", "a pirate"), None)
        txn.put(harbor_manifest({"character/wren": "r2"}), desktop.read_source(HARBOR).etag)
        txn.put(instance(InstanceRef("c1", "character", "zed"), WREN_R2, base=WREN_R2), None)
        txn.commit(handle())
        report = sync(desktop, phone, {DESKTOP: handle(), PHONE: owner_handle("campaigns", "kept")})  # no sources
        zed = "campaigns/c1/instances/character/zed.json"
        assert report.conflict(zed).code == "denied" and not (phone.root / zed).exists()
    finally:
        desktop.close()
        phone.close()


def test_resolve_refuses_a_record_whose_pinned_revision_still_differs(tmp_path):
    from grimoire.core.identity import InstanceRef
    from store_helpers import WREN, WREN_R2, instance
    from sync_helpers import PATHS, make_pair, new_revision, put
    desktop, phone = make_pair(tmp_path)
    try:
        new_revision(desktop, "r2", "a captain")
        new_revision(phone, "r2", "a pirate")
        zed = "campaigns/c1/instances/character/zed.json"
        put(desktop, instance(InstanceRef("c1", "character", "zed"), WREN, base=WREN_R2), None)
        assert sync(desktop, phone, handles()).conflict(zed).code == "head_dependency"
        data = (desktop.root / zed).read_bytes()
        with pytest.raises(ResolveRefused, match="resolve it first"):
            resolve(desktop, phone, zed, etag(data), {DESKTOP: etag(data), PHONE: None}, handles())
        assert not (phone.root / zed).exists()
        assert PATHS["r2"]  # resolving the revision first is the way out (see test_sync_scenarios)
    finally:
        desktop.close()
        phone.close()


@pytest.mark.parametrize("crash", [True, False])
def test_an_interrupted_first_sync_never_undoes_a_later_deletion(tmp_path, monkeypatch, crash):
    """Two copies of one library: what they already agree on is marked in the first sync's data commits,
    so a crash before the archives never leaves a "first" base that would re-create a deletion."""
    from grimoire.sync import session as sync_session
    from store_helpers import seed
    from sync_helpers import PATHS, WREN_A, edit_instance, handle
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed(desktop, handle())
    desktop.close()
    shutil.copytree(tmp_path / DESKTOP, tmp_path / PHONE)
    desktop, phone = LibraryStore.open(tmp_path / DESKTOP), LibraryStore.open(tmp_path / PHONE)
    try:
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        edit_instance(desktop, WREN_A, 9)
        if crash:
            def die(*args, **kwargs):
                raise RuntimeError("power loss before the archives")
            monkeypatch.setattr(sync_session, "_commit_archive", die)
            with pytest.raises(RuntimeError):
                sync(desktop, phone, handles())
            monkeypatch.undo()
        else:
            sync(desktop, phone, handles())
        (phone.root / PATHS["wren_b"]).unlink()  # a record both devices agreed on
        report = sync(desktop, phone, handles())
        assert not (phone.root / PATHS["wren_b"]).exists() and report.conflict(PATHS["wren_b"]) is not None
    finally:
        desktop.close()
        phone.close()


def test_021_a_held_relation_keeps_archive_borne_uncertainty_too(tmp_path):
    """The same promise when only the (damaged) archives make the base uncertain and the markers are valid."""
    desktop, phone, keys, relations = _effects_pair(tmp_path)
    try:
        victim = relations[0]
        (phone.root / victim).unlink()
        for store, peer in ((phone, DESKTOP), (desktop, PHONE)):
            (store.root / ".sync" / "peers" / peer / "archive.json").write_bytes(b"{")
        _accept(desktop, keys)
        assert any("archive ignored" in notice for notice in sync(desktop, phone, handles()).notices)
        shutil.move(str(desktop.root / "campaigns" / EFFECTS_C / "accepted"), str(tmp_path / "moved-away"))
        report = sync(desktop, phone, handles())  # the hold lifts
        assert not (phone.root / victim).exists() and report.conflict(victim) is not None
    finally:
        desktop.close()
        phone.close()


# -- round 8 -------------------------------------------------------------------------------------------

def test_resolve_of_a_manifest_refuses_a_head_whose_own_pin_still_differs(tmp_path):
    """resolve applies sync's dependency rule to every record it writes, the heads it copies included."""
    from grimoire.core.identity import SourceRef
    from grimoire.store import RecordEnvelope
    from store_helpers import HARBOR, harbor_manifest
    from sync_helpers import handle, make_pair
    mira, wren = SourceRef(HARBOR, "character", "mira"), SourceRef(HARBOR, "character", "wren")
    manifest = "sources/world/harbor/_source/manifest.json"
    m2 = "sources/world/harbor/_source/records/character/mira/m2.json"
    r2 = "sources/world/harbor/_source/records/character/wren/r2.json"
    desktop, phone = make_pair(tmp_path)

    def commit(store, puts):
        txn = store.transaction()
        for envelope, expected in puts:
            txn.put(envelope, expected)
        txn.commit(handle())
    try:
        commit(desktop, [(RecordEnvelope(mira.at("m2"), {"bio": "desk"}), None),
                         (RecordEnvelope(mira.at("m3"), {"bio": "desk later"}), None),
                         (RecordEnvelope(wren.at("r2"), {"friend": str(mira.at("m2"))}), None),
                         (harbor_manifest({"character/wren": "r2", "character/mira": "m3"}),
                          desktop.read_source(HARBOR).etag)])
        commit(phone, [(RecordEnvelope(mira.at("m2"), {"bio": "phone"}), None),
                       (harbor_manifest({"character/mira": "m2"}), phone.read_source(HARBOR).etag)])
        codes = {conflict.path: conflict.code for conflict in sync(desktop, phone, handles()).conflicts}
        assert codes[r2] == "head_dependency" and codes[m2] == "missing_ancestor"
        mine, theirs = (desktop.root / manifest).read_bytes(), (phone.root / manifest).read_bytes()
        with pytest.raises(ResolveRefused, match="m2.json, which .*r2.json pins"):
            resolve(desktop, phone, manifest, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
        assert not (phone.root / r2).exists()
    finally:
        desktop.close()
        phone.close()


@pytest.mark.parametrize("crash", [False, True])
def test_021_a_relation_held_on_a_first_sync_is_recorded_as_agreed(tmp_path, monkeypatch, crash):
    """Two copies of one library with an accepted event: the held relations already agree, and that is recorded
    (in the archives and in the data commits' markers, should the first sync stop before its archives), so a
    later one-sided deletion stays a deletion once the hold lifts."""
    from grimoire.sync import session as sync_session
    keys = Handles()
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed_effects(desktop, keys)
    _accept(desktop, keys)
    desktop.close()
    shutil.copytree(tmp_path / DESKTOP, tmp_path / PHONE)
    desktop, phone = LibraryStore.open(tmp_path / DESKTOP), LibraryStore.open(tmp_path / PHONE)
    try:
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        relations = sorted(path for path in desktop.scan().files if "/relations/" in path)
        if crash:
            def die(*args, **kwargs):
                raise RuntimeError("power loss before the archives")
            monkeypatch.setattr(sync_session, "_commit_archive", die)
            with pytest.raises(RuntimeError):
                sync(desktop, phone, handles())
            monkeypatch.undo()
        else:
            first = sync(desktop, phone, handles())
            assert relations and set(relations) <= set(first.skipped[DESKTOP])
        victim = relations[0]
        (phone.root / victim).unlink()
        sync(desktop, phone, handles())  # still held
        for store, where in ((desktop, "d"), (phone, "p")):
            shutil.move(str(store.root / "campaigns" / EFFECTS_C / "accepted"), str(tmp_path / f"away-{where}"))
        report = sync(desktop, phone, handles())  # the hold lifts
        assert not (phone.root / victim).exists(), report.copied
        assert report.conflict(victim).code == ("missing_ancestor" if crash else "deleted")  # never a silent copy
    finally:
        desktop.close()
        phone.close()


def test_107_a_chat_never_lands_where_its_roster_actor_cannot(tmp_path):
    from grimoire.chats.model import Launch
    from chat_fixtures import MARA, chat_library, seed_sources
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    try:
        seed_sources(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())
        chat_library(desktop).start_chat(Launch("character", MARA.at("r1"))).commit()
        actors = [path for path in desktop.scan().files if "/instances/" in path]
        assert actors
        for actor in actors:  # the phone cannot take the actor: a folder sits where its file belongs
            (phone.root / actor).mkdir(parents=True)
        report = sync(desktop, phone, handles())
        assert not [path for path, _ in report.copied if "/chats/" in path]
        assert {conflict.code for conflict in report.conflicts if "/chats/" in conflict.path} == {"head_dependency"}
    finally:
        desktop.close()
        phone.close()


def test_an_instance_whose_payload_pins_a_differing_revision_waits(tmp_path):
    from grimoire.core.identity import InstanceRef, SourceRef
    from grimoire.store import RecordEnvelope, SourceManifest
    from sync_helpers import handle, make_pair, put
    desktop, phone = make_pair(tmp_path)
    try:
        r9 = SourceRef("world:harbor", "character", "wren", "r9")
        for store, bio in ((desktop, "captain"), (phone, "pirate")):
            txn = store.transaction()
            txn.put(RecordEnvelope(r9, {"bio": bio}), None)
            current = store.read_source("world:harbor")
            txn.put(SourceManifest(current.manifest.source, current.manifest.owner, current.manifest.title,
                                   {"character/wren": "r1"}, current.manifest.links, extra=current.manifest.extra),
                    current.etag)
            txn.commit(handle())
        put(desktop, RecordEnvelope(InstanceRef("c1", "character", "nell"), {"portrait_of": str(r9)}), None)
        report = sync(desktop, phone, handles())
        nell = "campaigns/c1/instances/character/nell.json"
        assert report.conflict(nell).code == "head_dependency" and not (phone.root / nell).exists()
    finally:
        desktop.close()
        phone.close()


def test_021_an_unlistable_campaigns_folder_holds_every_relation(tmp_path):
    """When ``campaigns/`` itself cannot be listed on one device, no relation is decided or kept from it."""
    desktop, phone, keys, relations = _effects_pair(tmp_path)
    try:
        shutil.move(str(phone.root / "campaigns"), str(tmp_path / "phone-campaigns"))
        make_dir_link(phone.root / "campaigns", tmp_path / "phone-campaigns")
        report = sync(desktop, phone, handles())
        assert set(relations) <= set(report.skipped[DESKTOP])
        # C1: never decided, but reported: the phone's side is unknown, so each relation is a held conflict
        assert {conflict.code for conflict in report.conflicts if conflict.path in relations} == {"held"}
        assert not any("/relations/" in str(path) for path in phone.conflicts())
    finally:
        desktop.close()
        phone.close()


def test_020_a_knowledge_log_stuck_by_uncertainty_is_ended_by_one_resolve(tmp_path):
    """After a re-identification every base is uncertain, so a one-sided append is a conflict. resolve
    delivers the whole log unit create-only (records and batch), and later appends sync again."""
    desktop, phone = _evidence_pair(tmp_path)
    try:
        _evidence(desktop).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-1", "first", m(1), "director")]).commit()
        identify(phone, "phone2", replace=True)
        renamed = {DESKTOP: handles()[DESKTOP], "phone2": handles()[PHONE]}
        batch = f"campaigns/{EVIDENCE_C}/evidence/batch/0000000001.json"
        assert sync(desktop, phone, renamed).conflict(batch).code == "missing_ancestor"
        mine = (desktop.root / batch).read_bytes()
        assert resolve(desktop, phone, batch, etag(mine), {DESKTOP: etag(mine), "phone2": None}, renamed) == "phone2"
        assert (phone.root / batch).exists() and (phone.root / f"campaigns/{EVIDENCE_C}/events/e-1.json").exists()
        assert _evidence(phone).read(EVIDENCE_C, requested_by="director").problems == ()
        _evidence(desktop).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-2", "second", m(2), "director")]).commit()
        sync(desktop, phone, renamed)
        assert (phone.root / f"campaigns/{EVIDENCE_C}/events/e-2.json").exists()
    finally:
        desktop.close()
        phone.close()


def test_020_resolve_never_merges_two_diverged_knowledge_logs(tmp_path):
    desktop, phone = _evidence_pair(tmp_path)
    try:
        _evidence(desktop).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-d", "desk", m(1), "director")]).commit()
        _evidence(phone).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-p", "phone", m(1), "director")]).commit()
        sync(desktop, phone, handles())
        event = f"campaigns/{EVIDENCE_C}/events/e-d.json"
        mine = (desktop.root / event).read_bytes()
        with pytest.raises(ResolveRefused, match="diverged"):
            resolve(desktop, phone, event, etag(mine), {DESKTOP: etag(mine), PHONE: None}, handles())
        assert not (phone.root / event).exists()
    finally:
        desktop.close()
        phone.close()


# -- round 11: a knowledge log that cannot be seen whole moves nowhere --------------------------------

def _busy_folder(monkeypatch, store, parts):
    from grimoire.store import engine
    real = engine.Access.list_dir

    def busy(self, listed):
        if self.root == store.root and tuple(listed) == tuple(parts):
            raise PermissionError(13, "held by another program")
        return real(self, listed)
    monkeypatch.setattr(engine.Access, "list_dir", busy)


@pytest.mark.parametrize("how", ["unlistable", "respelled"])
def test_020_a_knowledge_log_not_seen_whole_never_crosses_by_halves(tmp_path, monkeypatch, how):
    desktop, phone = _evidence_pair(tmp_path)
    try:
        _evidence(desktop).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-desk", "desk event", m(1), "director")]).commit()
        if how == "unlistable":
            _busy_folder(monkeypatch, desktop, ("campaigns", EVIDENCE_C, "evidence", "batch"))
        else:
            base = desktop.root / "campaigns" / EVIDENCE_C
            (base / "evidence").rename(base / "Evidence")  # a case-only rename by another program
        report = sync(desktop, phone, handles())
        monkeypatch.undo()
        assert not [path for path, _ in report.copied if "/events/" in path or "/evidence/" in path]
        assert _evidence(phone).read(EVIDENCE_C, requested_by="director").quarantined == ()
    finally:
        desktop.close()
        phone.close()


def test_020_resolve_refuses_a_knowledge_log_it_cannot_see_whole(tmp_path, monkeypatch):
    desktop, phone = _evidence_pair(tmp_path)
    try:
        identify(phone, "tablet", replace=True)
        renamed = {DESKTOP: handles()[DESKTOP], "tablet": handles()[PHONE]}
        _evidence(desktop).append(EVIDENCE_C, [Event(EVIDENCE_C, "e-desk", "desk event", m(1), "director")]).commit()
        sync(desktop, phone, renamed)
        event = f"campaigns/{EVIDENCE_C}/events/e-desk.json"
        _busy_folder(monkeypatch, desktop, ("campaigns", EVIDENCE_C, "evidence", "batch"))
        data = (desktop.root / event).read_bytes()
        with pytest.raises(ResolveRefused, match="cannot be seen whole"):
            resolve(desktop, phone, event, etag(data), {DESKTOP: etag(data), "tablet": None}, renamed)
        monkeypatch.undo()
        assert not (phone.root / event).exists()
    finally:
        desktop.close()
        phone.close()


def test_021_an_unlistable_campaign_folder_reports_its_relations_as_held(tmp_path, monkeypatch):
    desktop, phone, keys, relations = _effects_pair(tmp_path)
    try:
        _accept(desktop, keys)
        _busy_folder(monkeypatch, desktop, ("campaigns", EFFECTS_C))
        report = sync(desktop, phone, handles())
        monkeypatch.undo()
        assert not [path for path, _ in report.copied if "/relations/" in path]
    finally:
        desktop.close()
        phone.close()
