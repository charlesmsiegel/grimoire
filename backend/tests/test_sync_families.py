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
