"""Standing settings are scope-owned text, not materialized world entities.

These tests catch fallback into the wrong root, silent clearing on read failure,
and reporting an already-published save as failed when its activity hint fails.
Copy workflows have their own regressions alongside their existing contracts.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from grimoire.store import atomic, campaigns, default_prompts, locks, worlds
from grimoire.store.worlds import lifecycle as world_lifecycle


@pytest.fixture
def scope(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return wid, campaigns.create_campaign("Saltmarch", wid), tmp_path


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def test_live_world_and_additive_campaign(scope):
    wid, cid, _ = scope
    assert default_prompts.resolve(cid) == {
        "text": "", "status": "empty",
        "world": {"id": wid, "text": "", "status": "empty"},
    }
    default_prompts.set_world(wid, "The river freezes each winter.")
    second = campaigns.create_campaign("Saltmarch", wid)
    default_prompts.set_campaign(cid, "This winter the harbor stays open.")
    assert default_prompts.resolve(cid) == {
        "text": "This winter the harbor stays open.", "status": "ok",
        "world": {"id": wid, "text": "The river freezes each winter.", "status": "ok"},
    }
    default_prompts.set_world(wid, "The river freezes each autumn.")
    assert default_prompts.resolve(second)["world"]["text"] == "The river freezes each autumn."
    assert not (campaigns.campaign_root(second) / "default_prompt.md").exists()
    default_prompts.set_campaign(cid, " \n ")
    assert default_prompts.resolve(cid)["status"] == "empty"
    assert default_prompts.resolve(cid)["world"]["status"] == "ok"
    assert (campaigns.campaign_root(cid) / "default_prompt.md").read_bytes() == b""
    default_prompts.set_world(wid, "")
    assert default_prompts.resolve(cid)["world"]["status"] == "empty"


def test_missing_reads_create_no_sidecars(scope):
    wid, cid, root = scope
    before = _files(root)
    assert default_prompts.read_world(wid) == {"text": "", "status": "empty"}
    assert default_prompts.read_campaign(cid) == {"text": "", "status": "empty"}
    assert default_prompts.resolve(cid)["world"]["status"] == "empty"
    assert _files(root) == before


def test_world_disappearing_during_resolution_does_not_fail_published_campaign(scope, monkeypatch):
    wid, cid, _ = scope

    def vanished(selected):
        assert selected == wid
        raise worlds.WorldNotFound(selected)

    # External sync/deletion can remove a world between binding validation
    # and its field read. The optional preview cannot undo the own publication.
    monkeypatch.setattr(default_prompts, "read_world", vanished)
    saved = default_prompts.set_campaign(cid, "The harbor stays open.")
    assert saved == {"text": "The harbor stays open.", "status": "ok",
                     "world": {"id": wid, "text": "", "status": "unavailable"}}
    assert default_prompts.read_campaign(cid)["text"] == "The harbor stays open."


@pytest.mark.parametrize("kind", ["world", "campaign"])
def test_nonblank_sidecar_preserves_multiline_text(scope, kind):
    wid, cid, _ = scope
    text = "\n  The river freezes.\n\n# Shared details\n  "
    if kind == "world":
        out = default_prompts.set_world(wid, text)
        read = default_prompts.read_world(wid)
    else:
        out = default_prompts.set_campaign(cid, text)
        read = default_prompts.read_campaign(cid)
    assert read == {"text": text, "status": "ok"}
    assert out["text"] == text and out["status"] == "ok"


@pytest.mark.parametrize("binding", ["", "../realm", "realm.", "winifred"])
def test_unbound_missing_and_unsafe_world_never_fall_back(scope, binding):
    _, cid, root = scope
    meta = campaigns.campaign_meta_path(cid)
    meta.write_text(f"---\nname: Saltmarch\nworld: {binding}\n---\n", encoding="utf-8")
    default_prompts.set_campaign(cid, "Only this campaign has an open harbor.")
    # An absent/unsafe reference may not read a tempting file at either a
    # parent root or the campaign's own directory as though it were a world.
    (root / "default_prompt.md").write_text("Wrong store-level context", encoding="utf-8")
    (root / "worlds" / "default_prompt.md").write_text("Wrong world-list context", encoding="utf-8")
    out = default_prompts.resolve(cid)
    assert out["text"] == "Only this campaign has an open harbor."
    assert out["world"] == {
        "id": "winifred" if binding == "winifred" else None,
        "text": "", "status": "unavailable",
    }


@pytest.mark.parametrize("kind", ["world", "campaign"])
@pytest.mark.parametrize("failure", ["decode", "permission"])
def test_unreadable_sidecar_has_status_without_prose_logging(scope, monkeypatch, caplog,
                                                           kind, failure):
    wid, cid, root = scope
    directory = worlds.world_root(wid) if kind == "world" else campaigns.campaign_root(cid)
    sidecar = directory / "default_prompt.md"
    sidecar.write_bytes(b"\xff" if failure == "decode" else b"The river freezes.")
    before = _files(root)
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == sidecar:
            raise PermissionError("The river freezes.")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if failure == "permission":
            patch.setattr(Path, "open", denied)
        out = (default_prompts.read_world(wid) if kind == "world"
               else default_prompts.read_campaign(cid))
    assert out == {"text": "", "status": "unreadable"}
    assert _files(root) == before
    assert all("The river freezes." not in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("kind", ["world", "campaign"])
def test_failed_publication_preserves_previous_text(scope, monkeypatch, kind):
    wid, cid, _ = scope
    setter = default_prompts.set_world if kind == "world" else default_prompts.set_campaign
    target = wid if kind == "world" else cid
    directory = worlds.world_root(wid) if kind == "world" else campaigns.campaign_root(cid)
    setter(target, "The river freezes.")
    sidecar = directory / "default_prompt.md"
    before = sidecar.read_bytes()
    original = atomic.write_text

    def refuse(path, text):
        if path == sidecar:
            raise OSError("publication refused")
        return original(path, text)

    monkeypatch.setattr(atomic, "write_text", refuse)
    with pytest.raises(OSError, match="publication refused"):
        setter(target, "This winter the harbor stays open.")
    assert sidecar.read_bytes() == before


@pytest.mark.parametrize("kind", ["world", "campaign"])
def test_successful_publication_survives_activity_failure(scope, monkeypatch, kind):
    wid, cid, _ = scope

    def refuse_stamp(*args):
        raise OSError("activity stamp refused")

    if kind == "world":
        monkeypatch.setattr(world_lifecycle, "touch", refuse_stamp)
        out = default_prompts.set_world(wid, "The river freezes.")
        read = default_prompts.read_world(wid)
    else:
        monkeypatch.setattr(campaigns.read, "touch_quietly", refuse_stamp)
        out = default_prompts.set_campaign(cid, "The river freezes.")
        read = default_prompts.read_campaign(cid)
    assert out["text"] == "The river freezes." and out["status"] == "ok"
    assert read == {"text": "The river freezes.", "status": "ok"}


@pytest.mark.parametrize("kind", ["world", "campaign"])
@pytest.mark.parametrize("scope_id", ["", "../realm", "realm.", "winifred"])
def test_unknown_and_unsafe_scope_saves_create_nothing(scope, kind, scope_id):
    _, _, root = scope
    before = _files(root)
    setter = default_prompts.set_world if kind == "world" else default_prompts.set_campaign
    error = worlds.WorldNotFound if kind == "world" else campaigns.CampaignNotFound
    with pytest.raises(error):
        setter(scope_id, "The river freezes.")
    assert _files(root) == before


@pytest.mark.parametrize("kind", ["world", "campaign"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_saved_default_returns_same_canonical_newlines_as_get(scope, kind, newline):
    wid, cid, _ = scope
    text = f"The river freezes.{newline}The harbor stays open."
    expected = "The river freezes.\nThe harbor stays open."
    if kind == "world":
        out = default_prompts.set_world(wid, text)
        read = default_prompts.read_world(wid)
    else:
        out = default_prompts.set_campaign(cid, text)
        read = default_prompts.read_campaign(cid)
    assert out["text"] == expected
    assert read == {"text": expected, "status": "ok"}


def test_unreadable_binding_keeps_the_own_field_and_warns(scope):
    _, cid, _ = scope
    default_prompts.set_campaign(cid, "The harbor stays open.")
    campaigns.campaign_meta_path(cid).write_bytes(b"\xff")
    assert default_prompts.resolve(cid) == {
        "text": "The harbor stays open.", "status": "ok",
        "world": {"id": None, "text": "", "status": "unreadable"},
    }


def test_concurrent_campaign_saves_validate_and_publish_under_one_lock(scope, monkeypatch):
    """Hold the first publication and observe the second lock request, not a
    guessed sleep. A missing lock fails at the real file validation/publication.
    """
    _, cid, _ = scope
    real_lock = locks.campaign_lock(cid)
    real_write = atomic.write_text
    real_exists = Path.exists
    sidecar = campaigns.campaign_root(cid) / "default_prompt.md"
    metadata = campaigns.campaign_meta_path(cid)
    first_writing = threading.Event()
    release_first = threading.Event()
    second_attempted = threading.Event()
    active = []

    @contextmanager
    def observed_lock(selected):
        assert selected == cid
        if first_writing.is_set():
            second_attempted.set()
        with real_lock:
            active.append(threading.get_ident())
            try:
                yield
            finally:
                active.pop()

    def observed_exists(path):
        if path == metadata:
            assert active == [threading.get_ident()]
        return real_exists(path)

    def observed_write(path, text):
        if path == sidecar:
            assert active == [threading.get_ident()]
            if text == "The river freezes.":
                first_writing.set()
                assert release_first.wait(10), "first publication was not released"
        return real_write(path, text)

    with monkeypatch.context() as patch:
        patch.setattr(locks, "campaign_lock", observed_lock)
        patch.setattr(atomic, "write_text", observed_write)
        patch.setattr(Path, "exists", observed_exists)
        # The standard executor reaps both workers, and result() propagates
        # their actual validation/publication errors instead of swallowing them.
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(default_prompts.set_campaign, cid, "The river freezes.")
            try:
                assert first_writing.wait(10), "first publication was not reached"
                second = workers.submit(default_prompts.set_campaign, cid, "The harbor stays open.")
                assert second_attempted.wait(10), "second save did not request the campaign lock"
            finally:
                release_first.set()
            first.result(timeout=10)
            second.result(timeout=10)
    assert default_prompts.read_campaign(cid)["text"] == "The harbor stays open."
