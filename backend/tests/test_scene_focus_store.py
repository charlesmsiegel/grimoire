"""Focus is authorial metadata: no transcript, identity or timestamp side effects."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from grimoire.store import atomic, campaigns, frontmatter, locks, scenes, worlds
from grimoire.store.scenes import focus


@pytest.fixture
def scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Saltmarch", worlds.create_world("Realm"))
    sid = scenes.create_scene(cid, "Mara")
    return cid, sid, scenes._scene_path(cid, sid)


@pytest.mark.parametrize("boundary", sorted(frontmatter.LINE_BREAKS))
def test_focus_normalization_cannot_inject_metadata(boundary):
    value = f"Offer Mara a choice.{boundary}clock: changed{boundary}---"
    assert focus.normalize(value) == "Offer Mara a choice. clock: changed ---"


@pytest.mark.parametrize("text, expected", [
    ("\x1f Offer Mara a choice. \x1f", "Offer Mara a choice."),
    (" \ufeffOffer Mara a choice. ", "\ufeffOffer Mara a choice."),
    ("Mara\r\nchooses.", "Mara  chooses."),
    ("  Mara  chooses.  ", "Mara  chooses."),
])
def test_focus_edge_whitespace(text, expected):
    assert focus.normalize(text) == expected


def test_create_publishes_focus_before_first_opener(scene, monkeypatch):
    cid, _, _ = scene
    real_write = atomic.write_text
    publications = []

    def observe(path, text):
        if path.parent == scenes._scenes_dir(cid) and path.suffix == ".md":
            publications.append(frontmatter.parse_frontmatter(text))
        return real_write(path, text)

    monkeypatch.setattr(atomic, "write_text", observe)
    sid = scenes.create_scene(cid, "Seraphine", focus="  Let Mara choose.\nGently.  ")
    assert len(publications) == 1
    assert publications[0][0]["focus"] == "Let Mara choose. Gently."
    assert publications[0][1] == ""
    assert focus.read(cid, sid) == {"text": "Let Mara choose. Gently.", "status": "ok"}


def test_empty_initial_focus_is_omitted(scene):
    cid, sid, path = scene
    assert "focus" not in frontmatter.read_record(path, "scene")[0]
    blank = scenes.create_scene(cid, "Seraphine", focus=" \n\x1f ")
    assert "focus" not in frontmatter.read_record(scenes._scene_path(cid, blank), "scene")[0]
    before = path.read_bytes()
    assert focus.read(cid, sid) == {"text": "", "status": "empty"}
    assert path.read_bytes() == before


@pytest.mark.parametrize("text", ["Mara's choice: 'yes' or \"no\".", "---", "# Offer: Mara's choice."])
def test_scalar_round_trip(scene, text):
    cid, sid, path = scene
    assert focus.set(cid, sid, text) == {"text": text, "status": "ok"}
    assert focus.read(cid, sid) == {"text": text, "status": "ok"}
    assert frontmatter.read_record(path, "scene")[0]["focus"] == text


def test_clear_preserves_body_metadata_and_updated(scene):
    cid, sid, path = scene
    scenes.append_message(cid, sid, "user", "Mara waits.\n\n")
    before = frontmatter.read_record(path, "scene")
    focus.set(cid, sid, "Offer Mara a choice.")
    edited = frontmatter.read_record(path, "scene")
    assert edited == ({**before[0], "focus": "Offer Mara a choice."}, before[1])
    assert focus.set(cid, sid, " \n ") == {"text": "", "status": "empty"}
    assert frontmatter.read_record(path, "scene") == before


@pytest.mark.parametrize("raw", ["", "Mara waits.", "---\ntitle: Mara\n", "---\n---\n\nMara waits."])
def test_malformed_scene_is_not_overwritten(scene, raw):
    cid, sid, path = scene
    atomic.write_text(path, raw)
    before = path.read_bytes()
    assert focus.read(cid, sid) == {"text": "", "status": "unreadable"}
    with pytest.raises(frontmatter.RecordUnreadableError):
        focus.set(cid, sid, "Offer Mara a choice.")
    assert path.read_bytes() == before


def test_legacy_focus_read_and_save_mint_no_identity(scene):
    cid, sid, path = scene
    atomic.write_text(path, frontmatter.dump_frontmatter({"title": "Mara", "updated": "unchanged"}, "Mara waits.\n"))
    before = path.read_bytes()
    assert focus.read(cid, sid)["status"] == "empty"
    assert path.read_bytes() == before
    focus.set(cid, sid, "Offer Mara a choice.")
    meta, body = frontmatter.read_record(path, "scene")
    assert meta == {"title": "Mara", "updated": "unchanged", "focus": "Offer Mara a choice."}
    assert body == "Mara waits.\n"


def test_failed_publication_preserves_the_entire_scene(scene, monkeypatch):
    cid, sid, path = scene
    before = path.read_bytes()

    def refuse(_path, _text):
        raise PermissionError("publication refused")

    monkeypatch.setattr(atomic, "write_text", refuse)
    with pytest.raises(PermissionError):
        focus.set(cid, sid, "Offer Mara a choice.")
    assert path.read_bytes() == before
    existing = list(scenes._scenes_dir(cid).glob("*.md"))
    with pytest.raises(PermissionError):
        scenes.create_scene(cid, "Seraphine", focus="Offer Mara a choice.")
    assert list(scenes._scenes_dir(cid).glob("*.md")) == existing


@pytest.mark.parametrize("error", [PermissionError("read refused"), UnicodeError("invalid encoding")])
def test_unreadable_scene_warns_and_cannot_be_overwritten(scene, monkeypatch, error):
    cid, sid, path = scene
    before = path.read_bytes()
    real_read = atomic.read_text

    def refuse(selected):
        if selected == path:
            raise error
        return real_read(selected)

    monkeypatch.setattr(atomic, "read_text", refuse)
    assert focus.read(cid, sid) == {"text": "", "status": "unreadable"}
    with pytest.raises(type(error)):
        focus.set(cid, sid, "Offer Mara a choice.")
    assert path.read_bytes() == before


@pytest.mark.parametrize("sid", ["../mara", "0001--seraphine"])
def test_unknown_or_unsafe_scene_does_not_create_files(scene, sid):
    cid, _, path = scene
    before = sorted(path.parent.iterdir())
    with pytest.raises(scenes.SceneNotFound):
        focus.read(cid, sid)
    with pytest.raises(scenes.SceneNotFound):
        focus.set(cid, sid, "Offer Mara a choice.")
    assert sorted(path.parent.iterdir()) == before


def test_focus_edit_and_append_both_survive(scene, monkeypatch):
    cid, sid, path = scene
    real_lock = locks.campaign_lock(cid)
    real_read = frontmatter.read_record
    editing = threading.Event()
    append_requested = threading.Event()
    release = threading.Event()

    @contextmanager
    def observed_lock(selected):
        assert selected == cid
        if editing.is_set():
            append_requested.set()
        with real_lock:
            yield

    def held_read(selected, what, **kwargs):
        result = real_read(selected, what, **kwargs)
        if selected == path:
            editing.set()
            assert release.wait(10), "focus read was not released"
        return result

    monkeypatch.setattr(locks, "campaign_lock", observed_lock)
    monkeypatch.setattr(frontmatter, "read_record", held_read)
    with ThreadPoolExecutor(max_workers=2) as workers:
        edit = workers.submit(focus.set, cid, sid, "Offer Mara a choice.")
        try:
            assert editing.wait(10), "focus did not read its scene"
            append = workers.submit(scenes.append_message, cid, sid, "user", "Mara waits.")
            assert append_requested.wait(10), "append did not request the same lock"
        finally:
            release.set()
        edit.result(timeout=10)
        append.result(timeout=10)
    meta, body = real_read(path, "scene")
    assert meta["focus"] == "Offer Mara a choice."
    assert "Mara waits." in body


def test_focus_follows_rename_repad_and_reused_id(scene):
    cid, sid, _ = scene
    focus.set(cid, sid, "Offer Mara a choice.")
    renamed = scenes.rename_scene(cid, sid, "Seraphine")
    assert focus.read(cid, renamed)["text"] == "Offer Mara a choice."
    scenes.repad(cid, 4)
    repadded = next(scenes._scenes_dir(cid).glob("*.md")).stem
    assert focus.read(cid, repadded)["text"] == "Offer Mara a choice."
    scenes.delete_scene(cid, repadded)
    fresh = scenes.create_scene(cid, "Seraphine")
    assert focus.read(cid, fresh) == {"text": "", "status": "empty"}
    focus.set(cid, fresh, "Offer Seraphine a choice.")
    scenes.delete_scene(cid, fresh)
    reused = scenes.create_scene(cid, "Seraphine")
    assert reused == fresh
    assert focus.read(cid, reused) == {"text": "", "status": "empty"}
