"""Independent #480 regressions; neighboring collection and maintenance work is held."""

import json
import threading

import pytest

from grimoire.store import (
    assets,
    campaign_images,
    campaigns,
    characters,
    greetings,
    image_descriptions,
    image_library,
    image_refs,
    image_subjects,
    locks,
    world_images,
    worlds,
)
from grimoire.store.campaigns import read as campaigns_read


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_world_dir_tolerates_world_lookup_io_failure(monkeypatch):
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)

    def unavailable(_wid):
        raise PermissionError("World directory is temporarily unavailable")

    monkeypatch.setattr(world_images, "images_dir", unavailable)
    assert campaign_images.world_dir(cid) is None


def test_world_dir_does_not_hide_campaign_read_failure(monkeypatch):
    def unreadable(_cid):
        raise PermissionError("campaign.md is unreadable")

    monkeypatch.setattr(campaigns_read, "read_campaign", unreadable)
    with pytest.raises(PermissionError, match=r"campaign\.md"):
        campaign_images.world_dir("saltmarch")


@pytest.mark.parametrize("subjects", [{"art_1": []}, {"missing": []}])
def test_bulk_subject_write_waits_for_sidecar_before_validation(
        monkeypatch, tmp_path, subjects):
    cid, vid = characters.create_character(tmp_path, "Seraphine", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "body")
    assets.put_image(tmp_path, gid, "default", "art_1", b"png", "png", base="greetings")
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid]})
    p = image_subjects.subjects_path(tmp_path, gid)
    before = p.read_bytes()
    held, release = threading.Event(), threading.Event()

    def holder():
        with assets.sidecar_lock(p.parent, image_subjects.SUBJECTS_FILE):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    monkeypatch.setattr(locks._runtime, "LOCK_TIMEOUT", 0.05)
    try:
        assert held.wait(10), "holder did not acquire the subjects sidecar"
        with pytest.raises(locks.StoreBusy):
            image_subjects.write_subjects(tmp_path, gid, subjects)
        assert p.read_bytes() == before
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    # An unlocked retry still replaces the whole legacy snapshot, including
    # an explicit reviewed-empty answer, rather than merging old keys.
    image_subjects.write_subjects(tmp_path, gid, {"art_1": []})
    assert json.loads(p.read_text(encoding="utf-8")) == {"art_1": []}
    image_subjects.write_subjects(tmp_path, gid, {})
    assert json.loads(p.read_text(encoding="utf-8")) == {}


def test_bulk_subject_write_refuses_an_unsafe_greeting_id_before_writing(tmp_path):
    # With no subjects, nothing else refuses the id: the write must not land.
    escaped = image_subjects.subjects_path(tmp_path, "../../escaped")
    with pytest.raises(ValueError, match="unsafe greeting id"):
        image_subjects.write_subjects(tmp_path, "../../escaped", {})
    assert not escaped.exists()


def test_identity_projections_keep_unresolved_ids_and_supplied_order():
    image_id = "px1-" + "1" * 64
    refs = {
        "gallery_1": image_refs.Ref("gallery_1", image_id, None),
        "avatar": image_refs.Ref("avatar", None, 43),
    }
    assert image_refs.image_ids(refs) == {"gallery_1": image_id}
    assert image_refs.name_rows(["coastline", "gallery_1", "avatar"], refs) == [
        {"name": "coastline"},
        {"name": "gallery_1", "image_id": image_id},
        {"name": "avatar"},
    ]
    assert image_refs.name_rows([], {}) == []


def test_backlog_projection_preserves_surface_policies_without_resolving(tmp_path):
    image_id = "px1-" + "1" * 64
    image_refs.write(tmp_path, "gallery_1", image_id)
    image_refs.write(tmp_path, "avatar", None, focus=43)
    (tmp_path / "coastline.png").write_bytes(b"legacy")
    (tmp_path / "undescribed.png").write_bytes(b"legacy")
    image_descriptions.carry_legacy(tmp_path, "coastline", "A grey quay.")

    assert image_library.backlog_rows(tmp_path) == [
        {"name": "coastline"}, {"name": "gallery_1", "image_id": image_id},
    ]
    rows, raw = image_descriptions._backlog_rows(tmp_path)
    assert sorted(rows, key=lambda row: row["name"]) == [
        {"name": "coastline"}, {"name": "gallery_1", "image_id": image_id},
        {"name": "undescribed"},
    ]
    assert raw == {"coastline": "A grey quay."}
    assert image_descriptions._placed_ids(tmp_path) == {"gallery_1": image_id}
