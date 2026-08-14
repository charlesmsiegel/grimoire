"""store.atomic: crash-safe record writes (#233).

The guarantee under test is *atomicity*, not durability: a reader sees the
whole previous version or the whole new one, never a partial one. Power-loss
recovery is a platform property these tests cannot exercise -- see the spec
(docs/superpowers/specs/2026-07-28-atomic-store-writes-design.md).
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from grimoire.store import atomic

PRIOR = "---\ntitle: before\n---\n\nthe original body\n"


def _write_prior(p: Path) -> None:
    p.write_text(PRIOR, encoding="utf-8")


def test_write_text_round_trips(tmp_path):
    p = tmp_path / "rec.md"
    atomic.write_text(p, "hello\nworld\n")
    assert p.read_text(encoding="utf-8") == "hello\nworld\n"


def test_write_bytes_round_trips(tmp_path):
    p = tmp_path / "img.webp"
    atomic.write_bytes(p, b"\x00\x01\x02")
    assert p.read_bytes() == b"\x00\x01\x02"


def test_bytes_are_identical_to_the_old_write_text(tmp_path):
    """The retrofit must not rewrite the user's whole store. Path.write_text
    translates \\n to the platform newline; so must we."""
    old, new = tmp_path / "old.md", tmp_path / "new.md"
    text = "---\ntitle: x\n---\n\nbody line\n"
    old.write_text(text, encoding="utf-8")
    atomic.write_text(new, text)
    assert new.read_bytes() == old.read_bytes()


def test_replace_failure_leaves_the_previous_record_intact(tmp_path, monkeypatch):
    p = tmp_path / "scene.md"
    _write_prior(p)

    def boom(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(atomic.os, "replace", boom)
    with pytest.raises(OSError):
        atomic.write_text(p, "replacement that must not land")

    assert p.read_text(encoding="utf-8") == PRIOR
    assert list(tmp_path.iterdir()) == [p], "a temp file survived the failure"


def test_write_failure_leaves_the_previous_record_intact(tmp_path):
    p = tmp_path / "scene.md"
    _write_prior(p)

    # Fail after the bytes are in the temp but before anything is published --
    # the moment the old truncate-then-write could not survive.
    def boom(fd):
        raise RuntimeError("crashed mid-write")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(atomic.os, "fsync", boom)
        with pytest.raises(RuntimeError):
            atomic.write_text(p, "half a record")

    assert p.read_text(encoding="utf-8") == PRIOR
    assert list(tmp_path.iterdir()) == [p], "a temp file survived the failure"


def test_temp_files_are_invisible_to_the_record_listers(tmp_path):
    """Every list endpoint in the store globs by extension. A temp that showed
    up as a record would surface a half-written scene in the UI."""
    seen = {}
    real_fsync = atomic.os.fsync

    def peek(fd):                       # after the write, before the replace
        seen["md"] = list(tmp_path.glob("*.md"))
        seen["json"] = list(tmp_path.glob("*.json"))
        seen["names"] = [q.name for q in tmp_path.iterdir()]
        return real_fsync(fd)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(atomic.os, "fsync", peek)
        atomic.write_text(tmp_path / "scene.md", "x")

    assert seen["md"] == [] and seen["json"] == []
    assert seen["names"], "the temp did not exist during the write"
    assert all(n.startswith(".") and n.endswith(".tmp") for n in seen["names"])


def test_missing_parent_still_raises_file_not_found(tmp_path):
    """Callers relied on this from write_text; several guard on it."""
    with pytest.raises(FileNotFoundError):
        atomic.write_text(tmp_path / "no" / "such" / "dir" / "rec.md", "x")


def test_long_target_name_stays_within_the_component_limit(tmp_path):
    """The temp name embeds the target's, so a near-limit name must not push
    the temp past 255 characters and fail a write that used to succeed."""
    p = tmp_path / ("a" * 200 + ".md")
    atomic.write_text(p, "fits")
    assert p.read_text(encoding="utf-8") == "fits"


@pytest.mark.skipif(sys.platform == "win32", reason="mode bits are vestigial on Windows")
def test_the_target_keeps_its_mode(tmp_path):
    """mkstemp creates 0600. Without an explicit chmod the first atomic write
    would silently narrow every readable record to owner-only."""
    p = tmp_path / "rec.md"
    _write_prior(p)
    p.chmod(0o644)
    atomic.write_text(p, "new body")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o644


@pytest.mark.skipif(sys.platform == "win32", reason="mode bits are vestigial on Windows")
def test_a_new_record_gets_the_umask_default_not_0600(tmp_path):
    """Asserting the read bits directly would be wrong: under a legitimate
    umask of 0o077 the correct answer IS 0600. Compare against what the umask
    actually implies, so the test rejects mkstemp's 0600 without also
    rejecting a strict-umask environment."""
    p = tmp_path / "fresh.md"
    atomic.write_text(p, "new")
    # Sample the umask independently rather than reusing atomic._UMASK: an
    # implementation that sampled it wrongly would otherwise agree with its own
    # assertion and the test would pass vacuously.
    live = os.umask(0o022)
    os.umask(live)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o666 & ~live


def test_the_mode_carried_over_is_the_targets(tmp_path, monkeypatch):
    """The two tests above only run on POSIX, but the bug they guard (0600
    temps narrowing every record) would ship from a Windows machine. Assert the
    chmod argument directly so the logic is covered on either platform."""
    p = tmp_path / "rec.md"
    _write_prior(p)
    # `atomic.os` *is* the os module, so these patches are global for the
    # duration of the test -- pathlib reaches os.stat internally too. Accept any
    # keyword: Path.exists() calls os.stat(self, follow_symlinks=...) on 3.11
    # and not on 3.14, so a fixed one-positional signature passes on the
    # development interpreter and raises TypeError on the version pyproject
    # declares as its floor.
    monkeypatch.setattr(atomic.os, "stat", lambda _p, **_kw: os.stat_result(
        (0o100644, 0, 0, 1, 0, 0, 0, 0, 0, 0)))
    chmodded = []
    monkeypatch.setattr(atomic.os, "chmod", lambda t, m, **_kw: chmodded.append(m))

    atomic.write_text(p, "body")

    assert chmodded == [0o644]


def test_a_new_record_does_not_inherit_the_0600_temp_mode(tmp_path, monkeypatch):
    """No existing target to copy from: fall back to the umask default, not to
    mkstemp's owner-only 0600."""
    chmodded = []
    monkeypatch.setattr(atomic.os, "chmod", lambda t, m, **_kw: chmodded.append(m))

    atomic.write_text(tmp_path / "fresh.md", "new")

    live = os.umask(0o022)   # sampled independently -- see the test above
    os.umask(live)
    assert chmodded == [0o666 & ~live], f"not the umask default: {chmodded!r}"


def test_transient_sharing_violation_is_retried(tmp_path, monkeypatch):
    """A concurrent reader or a scanner holding the target briefly is the
    normal Windows case; it must not surface as a failed save."""
    p = tmp_path / "rec.md"
    _write_prior(p)
    real, calls = atomic.os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            err = PermissionError("sharing violation")
            err.winerror = 32
            raise err
        return real(src, dst)

    monkeypatch.setattr(atomic.os, "replace", flaky)
    atomic.write_text(p, "landed")

    assert p.read_text(encoding="utf-8") == "landed"
    assert calls["n"] == 3


def test_permanent_permission_error_is_not_retried(tmp_path, monkeypatch):
    """A read-only target or a directory ACL denying child-create is permanent.
    Retrying it only delays the correct error -- and every retry widens the
    unlocked read-modify-write window."""
    p = tmp_path / "rec.md"
    _write_prior(p)
    calls = {"n": 0}

    def denied(src, dst):
        calls["n"] += 1
        err = PermissionError("access denied")
        err.winerror = 5
        raise err

    monkeypatch.setattr(atomic.os, "replace", denied)
    with pytest.raises(PermissionError):
        atomic.write_text(p, "never lands")

    assert calls["n"] == 1, "a permanent error was retried"
    assert p.read_text(encoding="utf-8") == PRIOR


def test_the_full_publish_order_holds(tmp_path, monkeypatch):
    """write -> fsync -> close -> chmod -> replace. Asserting only
    fsync-before-replace (as this first did) would miss a refactor that
    published while the flushed handle was still open, or that chmod'd after
    the file was already visible under its real name."""
    order = []
    real = {n: getattr(atomic.os, n) for n in ("fsync", "close", "chmod", "replace")}

    def trace(name):
        def wrapper(*a, **kw):
            order.append(name)
            return real[name](*a, **kw)
        return wrapper

    for n in real:
        monkeypatch.setattr(atomic.os, n, trace(n))
    atomic.write_text(tmp_path / "rec.md", "body")

    # os.close also fires for the mkstemp descriptor before any write happens,
    # so assert on the tail: the flush, its close, the mode, then publication.
    assert order[-4:] == ["fsync", "close", "chmod", "replace"], order


@pytest.mark.skipif(sys.platform == "win32",
                    reason="creating symlinks on Windows needs elevation")
def test_a_symlinked_record_is_replaced_not_written_through(tmp_path):
    """Documents a real semantic change rather than pretending to prevent it:
    Path.write_text follows a leaf symlink and writes to its target, while
    os.replace swaps the link itself. Nothing in grimoire creates linked
    records, but if that ever changes, this test says what happens."""
    target = tmp_path / "real.md"
    target.write_text("original\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)

    atomic.write_text(link, "new content\n")

    assert not link.is_symlink(), "the symlink survived; behavior changed"
    assert link.read_text(encoding="utf-8") == "new content\n"
    assert target.read_text(encoding="utf-8") == "original\n", \
        "the link's target was written through, which is the OLD behavior"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="creating hard links on Windows needs privileges")
def test_a_hard_linked_record_is_detached_not_updated_in_place(tmp_path):
    """The other half of the same declared limitation: write_text updated the
    shared inode, so every hard link saw the change. os.replace swaps only the
    directory entry it targets, so the links diverge."""
    a = tmp_path / "a.md"
    a.write_text("original\n", encoding="utf-8")
    b = tmp_path / "b.md"
    os.link(a, b)
    assert os.stat(a).st_ino == os.stat(b).st_ino

    atomic.write_text(a, "new content\n")

    assert a.read_text(encoding="utf-8") == "new content\n"
    assert b.read_text(encoding="utf-8") == "original\n", \
        "the shared inode was updated, which is the OLD behavior"
    assert os.stat(a).st_ino != os.stat(b).st_ino, "the links did not detach"


def test_concurrent_readers_never_see_a_partial_record(tmp_path):
    """The whole point of the issue: a reader interleaved with a writer gets
    one whole version or the other, never a truncated transcript."""
    p = tmp_path / "scene.md"
    _write_prior(p)
    new = PRIOR + "\n**Seraphine:** a second message\n"
    observed = []

    real_fsync = atomic.os.fsync

    def read_mid(fd):                   # temp written, record not yet replaced
        observed.append(p.read_text(encoding="utf-8"))
        return real_fsync(fd)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(atomic.os, "fsync", read_mid)
        atomic.write_text(p, new)
    observed.append(p.read_text(encoding="utf-8"))      # after publish

    assert observed == [PRIOR, new]


def test_reading_the_umask_does_not_race_other_threads(tmp_path):
    """There is no getter for the umask -- you set it to read it -- so doing
    that per-write would briefly expose a 0 umask to every other thread
    creating a file. It must be sampled once, at import."""
    import inspect

    src = inspect.getsource(atomic._carry_metadata)
    assert "os.umask" not in src, "umask is being read inside a write call"
    assert isinstance(atomic._UMASK, int)

    before = os.umask(0o022)
    os.umask(before)
    atomic.write_text(tmp_path / "rec.md", "body")
    after = os.umask(0o022)
    os.umask(after)
    assert before == after, "a write mutated the process umask"


def test_a_read_only_record_is_not_silently_replaced(tmp_path):
    """Publishing by rename is governed by the DIRECTORY's permissions, so a
    read-only record that Path.write_text refused would otherwise be replaced
    without complaint -- bypassing a protection the user set deliberately."""
    p = tmp_path / "locked.md"
    _write_prior(p)
    p.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            atomic.write_text(p, "must not land")
        assert p.read_text(encoding="utf-8") == PRIOR
        assert list(tmp_path.glob("*.tmp")) == [], "a temp was left behind"
    finally:
        p.chmod(0o644)


def test_the_write_helpers_never_expose_the_temp_path(tmp_path, monkeypatch):
    """write_text/write_bytes write through mkstemp's own descriptor. If they
    reopened by name instead, another process could swap a symlink in between
    creation and the write -- so assert the pathname is never reopened."""
    opened = []
    real_open = atomic.os.open

    def spy(p, flags, *a, **kw):
        # mkstemp's own creating open is expected; a second open of the same
        # path without O_EXCL is the reopen-by-name this must not do.
        opened.append((str(p), bool(flags & os.O_EXCL)))
        return real_open(p, flags, *a, **kw)

    monkeypatch.setattr(atomic.os, "open", spy)
    atomic.write_text(tmp_path / "a.md", "x")
    atomic.write_bytes(tmp_path / "b.bin", b"y")

    reopens = [p for p, exclusive in opened if not exclusive]
    assert reopens == [], f"temp path was reopened by name: {reopens}"
    assert len(opened) == 2, f"expected one creating open per write, got {opened}"


def test_no_api_hands_out_the_temp_pathname(tmp_path):
    """The symlink-swap window existed only because a temp *path* was yielded
    for PIL to open. PIL takes a file object, so that API is gone rather than
    defended -- this pins it, since re-adding one would silently reopen the
    hole."""
    assert not hasattr(atomic, "tempfile_for"), \
        "a path-yielding API is back; the swap window comes with it"
    public = sorted(
        n for n, v in vars(atomic).items()
        if not n.startswith("_") and callable(v) and getattr(v, "__module__", "") == atomic.__name__)
    # `append_line` writes THROUGH a caller-named path (#152's ledger), never
    # via a temp whose name it hands back, so it adds no swap window -- the pin
    # is on the shape of the API, and this entry is the reviewed addition.
    assert public == ["append_line", "write_bytes", "write_text"], \
        f"unexpected public API: {public}"


def test_group_ownership_and_xattrs_are_carried_over(tmp_path, monkeypatch):
    """The surviving file is the temp, not the original inode, so anything the
    old record carried is lost unless copied. Asserted through the syscalls so
    it is covered on Windows too, where chown/xattrs do not exist."""
    p = tmp_path / "rec.md"
    _write_prior(p)

    fake = os.stat_result((0o100640, 0, 0, 1, 4242, 8484, 0, 0, 0, 0))
    monkeypatch.setattr(atomic.os, "stat", lambda _p, **_kw: fake)
    calls = {"chmod": [], "chown": [], "xattr": []}
    monkeypatch.setattr(atomic.os, "chmod", lambda t, m, **_kw: calls["chmod"].append(m))
    monkeypatch.setattr(atomic.os, "chown", lambda t, u, g, **_kw: calls["chown"].append((u, g)),
                        raising=False)
    monkeypatch.setattr(atomic.os, "listxattr", lambda _p, **_kw: ["user.tag"], raising=False)
    monkeypatch.setattr(atomic.os, "getxattr", lambda _p, a: b"v", raising=False)
    monkeypatch.setattr(atomic.os, "setxattr",
                        lambda t, a, v: calls["xattr"].append((a, v)), raising=False)

    atomic.write_text(p, "body")

    assert calls["chmod"] == [0o640]
    assert calls["chown"] == [(4242, 8484)], "uid/gid not carried"
    assert calls["xattr"] == [("user.tag", b"v")], "xattrs not carried"


def test_an_unprivileged_chown_falls_back_to_the_group(tmp_path, monkeypatch):
    """Only root can change a file's uid. The full chown failing must not lose
    the group too -- shared-group setups are exactly what this preserves."""
    p = tmp_path / "rec.md"
    _write_prior(p)
    monkeypatch.setattr(atomic.os, "stat",
                        lambda _p, **_kw: os.stat_result((0o100644, 0, 0, 1, 4242, 8484, 0, 0, 0, 0)))
    monkeypatch.setattr(atomic.os, "chmod", lambda t, m, **_kw: None)
    attempts = []

    def chown(t, uid, gid):
        attempts.append((uid, gid))
        if uid != -1:
            raise PermissionError("not root")

    monkeypatch.setattr(atomic.os, "chown", chown, raising=False)
    monkeypatch.setattr(atomic.os, "listxattr", lambda _p: [], raising=False)

    atomic.write_text(p, "body")

    assert attempts == [(4242, 8484), (-1, 8484)], f"no group-only retry: {attempts}"


def test_metadata_failures_never_fail_the_write(tmp_path, monkeypatch):
    """Losing a metadata bit is cosmetic; losing the write is not."""
    p = tmp_path / "rec.md"
    _write_prior(p)

    def nope(*a, **kw):
        raise OSError("unsupported filesystem")

    monkeypatch.setattr(atomic.os, "chmod", nope)
    monkeypatch.setattr(atomic.os, "chown", nope, raising=False)
    monkeypatch.setattr(atomic.os, "listxattr", nope, raising=False)

    atomic.write_text(p, "landed anyway")
    assert p.read_text(encoding="utf-8") == "landed anyway"


# ---- the append-only primitive (#152) ----
def test_append_line_creates_the_file_and_terminates_the_row(tmp_path):
    p = tmp_path / "ledger.jsonl"
    atomic.append_line(p, '{"a": 1}')
    assert p.read_text(encoding="utf-8") == '{"a": 1}\n'


def test_append_line_adds_rather_than_replacing(tmp_path):
    """The whole reason this exists beside `write_text`: a ledger grows by a
    row, and a temp-and-replace would rewrite the file to add one."""
    p = tmp_path / "ledger.jsonl"
    for n in range(3):
        atomic.append_line(p, f"row {n}")
    assert p.read_text(encoding="utf-8").splitlines() == ["row 0", "row 1", "row 2"]


def test_append_line_does_not_double_the_terminator(tmp_path):
    p = tmp_path / "ledger.jsonl"
    atomic.append_line(p, "already terminated\n")
    atomic.append_line(p, "next")
    assert p.read_text(encoding="utf-8") == "already terminated\nnext\n"


def test_concurrent_appends_interleave_whole_lines(tmp_path):
    """O_APPEND resolves the offset and the write as one step, so two writers
    land whole rows in some order rather than halves of one spliced into
    another -- the property a jsonl reader depends on."""
    import threading

    p = tmp_path / "ledger.jsonl"
    rows = [f"{who}-{n}" for who in "ab" for n in range(50)]

    def run(who):
        for n in range(50):
            atomic.append_line(p, f"{who}-{n}")

    threads = [threading.Thread(target=run, args=(who,)) for who in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(p.read_text(encoding="utf-8").splitlines()) == sorted(rows)
