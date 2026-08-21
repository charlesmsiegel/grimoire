"""The scene identity token and its backfill (detached runs, task 1).

A run captures the identity of the scene it started on and refuses to publish
if it changed. That is what stops an old run writing onto a *different* scene
that recycled its `sid` -- `serialize._numbering` derives the next number from
the files on disk with no stored counter, so deleting a scene frees its number
and a same-titled replacement lands on the same id.
"""

from __future__ import annotations

import logging

import pytest

import grimoire.store as store
from grimoire.store import frontmatter, migrations


def _new_campaign() -> str:
    wid = store.worlds.create_world("Realm")
    return store.campaigns.create_campaign("Saltmarch", wid)


def _strip_identity_from_disk(cid: str, sid: str) -> None:
    """Rewrite a scene's frontmatter without `identity`, simulating a record
    written before this feature existed."""
    p = store.scenes.paths._scene_path(cid, sid)
    meta, body = frontmatter.parse_frontmatter(p.read_text(encoding="utf-8"))
    meta.pop("identity", None)
    p.write_text(frontmatter.dump_frontmatter(meta, body), encoding="utf-8")


def test_created_scene_has_a_stable_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    first = store.scenes.scene_identity(cid, sid)
    assert first and len(first) == 32
    # Stable across an unrelated mutation that rewrites the whole file.
    store.scenes.append_message(cid, sid, "user", "hello")
    assert store.scenes.scene_identity(cid, sid) == first


def test_identity_survives_a_rename(tmp_path, monkeypatch):
    """A rename mints a new `sid`. The identity is what the notification tap
    and the publish fence carry precisely because the id does not survive."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    before = store.scenes.scene_identity(cid, sid)
    new_sid = store.scenes.rename_scene(cid, sid, "Winifred")
    assert new_sid != sid
    assert store.scenes.scene_identity(cid, new_sid) == before


def test_identity_is_not_in_the_read_scene_payload(tmp_path, monkeypatch):
    """`read_scene` returns `{"meta": {"id": sid, **frontmatter}, ...}`, and the
    frozen-campaign sweep snapshots that whole payload. A freshly minted uuid
    per scene would make `snapshot.json` differ on every regeneration, which
    destroys the only fixture in the repo whose value is being old."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    assert store.scenes.scene_identity(cid, sid)          # it exists on disk
    assert "identity" not in store.scenes.read_scene(cid, sid)["meta"]
    assert "identity" not in store.scenes.read_scene_meta(cid, sid)


def test_a_recycled_sid_gets_a_different_identity(tmp_path, monkeypatch):
    """The whole point. Same title after a delete lands on the same `sid`."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    old = store.scenes.scene_identity(cid, sid)
    store.scenes.delete_scene(cid, sid)
    again = store.scenes.create_scene(cid, "Mara")
    assert again == sid, "precondition: the id was recycled"
    assert store.scenes.scene_identity(cid, again) != old


def test_scene_identity_is_none_for_a_pre_feature_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    _strip_identity_from_disk(cid, sid)
    assert store.scenes.scene_identity(cid, sid) is None


def test_ensure_identity_assigns_once_and_then_returns_it(tmp_path, monkeypatch):
    """The lazy path. Startup skips a campaign whose lock is contended, so a
    legacy campaign can still be identity-less when its first run starts;
    without this the fence would compare None with None, which always matches."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    _strip_identity_from_disk(cid, sid)
    assert store.scenes.scene_identity(cid, sid) is None
    got = store.scenes.ensure_identity(cid, sid)
    assert got and len(got) == 32
    assert store.scenes.scene_identity(cid, sid) == got
    assert store.scenes.ensure_identity(cid, sid) == got     # idempotent


def test_find_by_identity_returns_the_current_sid_after_a_rename(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    ident = store.scenes.scene_identity(cid, sid)
    new_sid = store.scenes.rename_scene(cid, sid, "Winifred")
    assert store.scenes.find_by_identity(cid, ident) == new_sid


def test_find_by_identity_is_none_once_the_scene_is_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    ident = store.scenes.scene_identity(cid, sid)
    store.scenes.delete_scene(cid, sid)
    assert store.scenes.find_by_identity(cid, ident) is None


def test_backfill_gives_every_pre_feature_scene_an_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    a = store.scenes.create_scene(cid, "Mara")
    b = store.scenes.create_scene(cid, "Winifred")
    _strip_identity_from_disk(cid, a)
    _strip_identity_from_disk(cid, b)

    migrations.backfill_scene_identities()

    ia, ib = store.scenes.scene_identity(cid, a), store.scenes.scene_identity(cid, b)
    assert ia and ib and ia != ib


def test_backfill_leaves_an_existing_identity_alone(tmp_path, monkeypatch):
    """Idempotent: re-running must not re-mint, or every run in flight across a
    restart would find the scene it captured has 'changed'."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    before = store.scenes.scene_identity(cid, sid)
    migrations.backfill_scene_identities()
    assert store.scenes.scene_identity(cid, sid) == before


def test_backfill_continues_past_a_contended_campaign(tmp_path, monkeypatch, caplog):
    """Per campaign, not per pass. The startup hook catches StoreBusy around the
    whole step, so letting it out of this loop abandons every campaign after the
    contended one -- while the log named only one."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    first = store.campaigns.create_campaign("Saltmarch", wid)
    second = store.campaigns.create_campaign("Winifred", wid)
    sid = store.scenes.create_scene(second, "Mara")
    _strip_identity_from_disk(second, sid)

    real = migrations._backfill_campaign

    def busy_on_first(cid: str) -> None:
        if cid == first:
            raise store.locks.StoreBusy("held by another process")
        real(cid)

    monkeypatch.setattr(migrations, "_backfill_campaign", busy_on_first)
    with caplog.at_level(logging.WARNING):
        migrations.backfill_scene_identities()

    assert store.scenes.scene_identity(second, sid), "the second campaign was abandoned"
    assert first in caplog.text


@pytest.mark.parametrize("bad", ["../escape", "not/a/scene", ""])
def test_accessors_refuse_an_unsafe_sid(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    assert store.scenes.scene_identity(cid, bad) is None
    with pytest.raises(store.scenes.SceneNotFound):
        store.scenes.ensure_identity(cid, bad)


def test_backfill_changes_nothing_but_the_identity_line(tmp_path, monkeypatch):
    """The backfill rewrites every scene file in the user's library on first
    boot, so the frontmatter round-trip has to be byte-exact for everything it
    is not deliberately adding.

    `ensure_identity` parses the file and dumps it back; a normalization
    anywhere in that pair -- requoting a value, eating a blank line, dropping a
    trailing newline -- would silently rewrite transcripts, which are the one
    artifact here that cannot be regenerated. This is the test that says so.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara: a beginning")
    store.scenes.append_message(cid, sid, "user", "Mara waits — for a while.")
    store.scenes.append_message(cid, sid, "assistant", "The lamps are lit.\n\nThen:  quiet.")
    store.scenes.append_message(cid, sid, "user", "  leading and trailing spaces  ")

    p = store.scenes.paths._scene_path(cid, sid)
    _strip_identity_from_disk(cid, sid)
    before = p.read_text(encoding="utf-8")

    migrations.backfill_scene_identities()

    after = p.read_text(encoding="utf-8")
    added = [ln for ln in after.splitlines(True) if ln not in before.splitlines(True)]
    assert len(added) == 1 and added[0].startswith("identity:"), added
    # And the transcript itself is untouched, byte for byte.
    assert after.replace(added[0], "") == before


def test_backfill_does_not_rewrite_a_scene_that_already_has_one(tmp_path, monkeypatch):
    """Idempotent in the strong sense: a second pass must not touch the file at
    all. This runs on every startup, forever, over the whole library."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "hello")
    p = store.scenes.paths._scene_path(cid, sid)
    before, before_mtime = p.read_text(encoding="utf-8"), p.stat().st_mtime_ns

    migrations.backfill_scene_identities()

    assert p.read_text(encoding="utf-8") == before
    assert p.stat().st_mtime_ns == before_mtime, "the file was rewritten"


def test_identity_survives_a_repad(tmp_path, monkeypatch):
    """`repad` is the most violent operation in the package -- the 999 -> 1000
    widen renames every scene file in the campaign and repoints their sidecars.
    Every `sid` changes at once, which is precisely the moment a live run needs
    something else to compare, so the identity has to ride through it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    ident = store.scenes.scene_identity(cid, sid)

    store.scenes.repad(cid, 5)

    ids = [s["id"] for s in store.scenes.list_scenes(cid)]
    assert sid not in ids, "precondition: repad renamed every scene"
    found = store.scenes.find_by_identity(cid, ident)
    assert found in ids
    assert store.scenes.scene_identity(cid, found) == ident


def test_backfill_preserves_hand_edited_frontmatter_verbatim(tmp_path, monkeypatch):
    """The backfill touches every scene file in a real library, including ones
    a user has edited by hand.

    `parse_frontmatter` models only `key: value` lines -- it drops anything
    without a colon and collapses duplicate keys -- and `dump_frontmatter`
    requotes what survives. Parsing and re-dumping a hand-edited header
    therefore DELETES the parts it does not model, silently, on first boot.
    The identity line has to be spliced into the raw text instead.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    p = store.scenes.paths._scene_path(cid, sid)

    hand_edited = (
        "---\n"
        "title: Mara\n"
        "# a note the user typed by hand\n"
        "created: 2024-01-01\n"
        "---\n"
        "\n"
        "**Mara:** hello\n"
    )
    p.write_text(hand_edited, encoding="utf-8")

    migrations.backfill_scene_identities()

    after = p.read_text(encoding="utf-8")
    assert "# a note the user typed by hand" in after, "a hand-edited line was deleted"
    added = [ln for ln in after.splitlines(True) if ln not in hand_edited.splitlines(True)]
    assert len(added) == 1 and added[0].startswith("identity:"), added
    assert after.replace(added[0], "") == hand_edited


def test_find_by_identity_keeps_looking_past_an_unreadable_file(tmp_path, monkeypatch):
    """One corrupt file must not blind the whole lookup. This runs when a
    notification is tapped, so aborting means the tap opens nothing."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Winifred")
    ident = store.scenes.scene_identity(cid, sid)

    # Sorts before the real scene, so a scan that aborts never reaches it.
    bad = store.scenes.paths._scenes_dir(cid) / "0000--broken.md"
    bad.write_bytes(b"---\ntitle: \xff\xfe not utf-8\n---\n\nbody\n")

    assert store.scenes.find_by_identity(cid, ident) == sid


def test_a_legacy_identity_that_is_not_a_token_is_replaced(tmp_path, monkeypatch):
    """A pre-feature scene may already carry an unrelated `identity` key. Any
    non-empty value would otherwise be adopted as the correctness token -- and
    a value like `foo/bar` cannot survive the by-identity route's path segment,
    while a value duplicated across scenes makes the reverse lookup return
    whichever file sorts first."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    p = store.scenes.paths._scene_path(cid, sid)
    meta, body = frontmatter.parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["identity"] = "foo/bar"
    p.write_text(frontmatter.dump_frontmatter(meta, body), encoding="utf-8")

    assert store.scenes.scene_identity(cid, sid) is None, "a non-token is not an identity"
    got = store.scenes.ensure_identity(cid, sid)
    assert len(got) == 32 and all(c in "0123456789abcdef" for c in got)
    assert store.scenes.scene_identity(cid, sid) == got


def test_backfill_preserves_crlf_line_endings(tmp_path, monkeypatch):
    """`Path.read_text` performs universal-newline translation, so a CRLF scene
    comes back with every `\\r\\n` already collapsed and `atomic.write_text`
    publishes the normalized copy. The backfill touches every file in the
    library unprompted, so it has to work in bytes."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    p = store.scenes.paths._scene_path(cid, sid)
    p.write_bytes(b"---\r\ntitle: Mara\r\n---\r\n\r\n**Mara:** hello\r\n")

    migrations.backfill_scene_identities()

    raw = p.read_bytes()
    assert b"\r\n" in raw, "CRLF was normalized away"
    assert raw.count(b"\n") == raw.count(b"\r\n"), "line endings became mixed"
    assert b"identity:" in raw


def test_one_corrupt_scene_does_not_abort_startup(tmp_path, monkeypatch, caplog):
    """`list_scenes` reads frontmatter as UTF-8 and raises on bytes that are
    not. `_lifespan` catches only StoreBusy, so before this a single unreadable
    scene in the user's library stopped the app from booting -- every launch,
    with no way back except finding and deleting the file."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    good = store.scenes.create_scene(cid, "Mara")
    _strip_identity_from_disk(cid, good)
    bad = store.scenes.paths._scenes_dir(cid) / "0002--broken.md"
    bad.write_bytes(b"---\ntitle: \xff\xfe\n---\n\nbody\n")

    with caplog.at_level(logging.WARNING):
        migrations.backfill_scene_identities()          # must not raise

    assert store.scenes.scene_identity(cid, good), "the readable scene was skipped"


def test_a_duplicated_identity_is_re_minted(tmp_path, monkeypatch):
    """Two scenes carrying the same token make the reverse lookup return
    whichever file sorts first, so a notification for one opens the other."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    a = store.scenes.create_scene(cid, "Mara")
    b = store.scenes.create_scene(cid, "Winifred")
    shared = store.scenes.scene_identity(cid, a)
    pb = store.scenes.paths._scene_path(cid, b)
    pb.write_text(
        pb.read_text(encoding="utf-8").replace(
            f"identity: {store.scenes.scene_identity(cid, b)}", f"identity: {shared}"),
        encoding="utf-8")
    assert store.scenes.scene_identity(cid, b) == shared     # precondition

    migrations.backfill_scene_identities()

    ia, ib = store.scenes.scene_identity(cid, a), store.scenes.scene_identity(cid, b)
    assert ia and ib and ia != ib
    assert store.scenes.find_by_identity(cid, ia) == a
    assert store.scenes.find_by_identity(cid, ib) == b


def test_a_duplicate_is_replaced_even_when_hand_formatted(tmp_path, monkeypatch):
    """`_read_token` tolerates spellings a person would type -- `identity : x`,
    or a quoted value -- so the replacement must not assume the canonical
    `identity: <token>` byte sequence. Assuming it, the swap matches nothing,
    a fresh token is returned but never written, and BOTH files keep the
    duplicate while the backfill believes it fixed one."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    a = store.scenes.create_scene(cid, "Mara")
    b = store.scenes.create_scene(cid, "Winifred")
    shared = store.scenes.scene_identity(cid, a)

    pb = store.scenes.paths._scene_path(cid, b)
    raw = pb.read_text(encoding="utf-8")
    raw = raw.replace(f"identity: {store.scenes.scene_identity(cid, b)}",
                      f"identity : '{shared}'")          # hand-typed spacing and quotes
    pb.write_text(raw, encoding="utf-8")
    assert store.scenes.scene_identity(cid, b) == shared   # precondition

    migrations.backfill_scene_identities()

    ia, ib = store.scenes.scene_identity(cid, a), store.scenes.scene_identity(cid, b)
    assert ia and ib and ia != ib, "the duplicate survived"
    assert store.scenes.find_by_identity(cid, ib) == b


def test_a_scene_that_cannot_be_written_does_not_abort_startup(tmp_path, monkeypatch, caplog):
    """A read-only transcript, or one held by a sync client, makes the write
    fail. `_lifespan` catches only StoreBusy, so an unhandled OSError here stops
    the app booting -- the same failure as the unreadable case, one layer down."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    stuck = store.scenes.create_scene(cid, "Mara")
    good = store.scenes.create_scene(cid, "Winifred")
    _strip_identity_from_disk(cid, stuck)
    _strip_identity_from_disk(cid, good)

    real = store.scenes.identity.ensure_identity

    def refuse_one(c, s, replace=False):
        if s == stuck:
            raise PermissionError("read-only transcript")
        return real(c, s, replace=replace)

    monkeypatch.setattr(store.scenes.identity, "ensure_identity", refuse_one)
    with caplog.at_level(logging.WARNING):
        migrations.backfill_scene_identities()          # must not raise

    assert store.scenes.scene_identity(cid, good), "the rest of the campaign was abandoned"
    assert stuck in caplog.text


def test_a_mixed_newline_header_is_spliced_not_replaced(tmp_path, monkeypatch):
    """Opening fence CRLF, closing fence LF -- a hand-edited file, or one a
    tool rewrote halfway.

    Text reads normalize newlines, so the app sees perfectly good frontmatter;
    a byte-level splice that looks for a CLOSING fence in the OPENER's style
    finds none. Returning None there makes `ensure_identity` prepend a second
    header, and the original title and model become transcript text -- silent
    corruption of the scene, on first boot.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    p = store.scenes.paths._scene_path(cid, sid)
    p.write_bytes(b"---\r\ntitle: Mara\nmodel: m\n---\n\n**Mara:** hello\n")

    migrations.backfill_scene_identities()

    raw = p.read_bytes()
    assert raw.count(b"---") == 2, "a second header block was prepended"
    assert store.scenes.read_scene(cid, sid)["meta"]["title"] == "Mara"
    assert store.scenes.scene_identity(cid, sid)
    assert b"**Mara:** hello" in raw


def test_an_unreadable_scenes_directory_does_not_abort_startup(tmp_path, monkeypatch):
    """`glob()` itself raises if the directory cannot be listed -- a permissions
    problem, or a synced folder mid-error. That happens BEFORE any per-scene
    handler, and the startup hook catches only StoreBusy."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    broken = store.campaigns.create_campaign("Saltmarch", wid)
    fine = store.campaigns.create_campaign("Winifred", wid)
    sid = store.scenes.create_scene(fine, "Mara")
    _strip_identity_from_disk(fine, sid)

    broken_dir = store.scenes.paths._scenes_dir(broken)

    import pathlib
    original = pathlib.Path.glob

    def refuse(self, pattern):
        if self == broken_dir:
            raise PermissionError("cannot list")
        return original(self, pattern)

    monkeypatch.setattr(pathlib.Path, "glob", refuse)
    migrations.backfill_scene_identities()          # must not raise
    monkeypatch.setattr(pathlib.Path, "glob", original)

    assert store.scenes.scene_identity(fine, sid), "the other campaign was abandoned"
