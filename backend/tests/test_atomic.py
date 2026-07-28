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

    with pytest.raises(RuntimeError):
        with atomic.tempfile_for(p) as tmp:
            tmp.write_text("half a record", encoding="utf-8")
            raise RuntimeError("crashed mid-write")

    assert p.read_text(encoding="utf-8") == PRIOR
    assert list(tmp_path.iterdir()) == [p], "a temp file survived the failure"


def test_temp_files_are_invisible_to_the_record_listers(tmp_path):
    """Every list endpoint in the store globs by extension. A temp that showed
    up as a record would surface a half-written scene in the UI."""
    seen = {}

    with atomic.tempfile_for(tmp_path / "scene.md") as tmp:
        tmp.write_text("x", encoding="utf-8")
        seen["md"] = list(tmp_path.glob("*.md"))
        seen["json"] = list(tmp_path.glob("*.json"))
        seen["name"] = tmp.name

    assert seen["md"] == [] and seen["json"] == []
    assert seen["name"].startswith(".") and seen["name"].endswith(".tmp")


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
def test_a_new_record_is_not_created_owner_only(tmp_path):
    p = tmp_path / "fresh.md"
    atomic.write_text(p, "new")
    assert stat.S_IMODE(os.stat(p).st_mode) & 0o044, "new record is not readable"


def test_the_mode_carried_over_is_the_targets(tmp_path, monkeypatch):
    """The two tests above only run on POSIX, but the bug they guard (0600
    temps narrowing every record) would ship from a Windows machine. Assert the
    chmod argument directly so the logic is covered on either platform."""
    p = tmp_path / "rec.md"
    _write_prior(p)
    monkeypatch.setattr(atomic.os, "stat", lambda _p: os.stat_result(
        (0o100644, 0, 0, 1, 0, 0, 0, 0, 0, 0)))
    chmodded = []
    monkeypatch.setattr(atomic.os, "chmod", lambda t, m: chmodded.append(m))

    atomic.write_text(p, "body")

    assert chmodded == [0o644]


def test_a_new_record_does_not_inherit_the_0600_temp_mode(tmp_path, monkeypatch):
    """No existing target to copy from: fall back to the umask default, not to
    mkstemp's owner-only 0600."""
    chmodded = []
    monkeypatch.setattr(atomic.os, "chmod", lambda t, m: chmodded.append(m))

    atomic.write_text(tmp_path / "fresh.md", "new")

    assert chmodded and chmodded[0] & 0o044, f"created owner-only: {chmodded!r}"


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


def test_data_is_flushed_before_the_replace(tmp_path, monkeypatch):
    """The fsync is what makes the new bytes complete if the rename lands.
    Asserting the order catches a refactor that moves the replace earlier."""
    order = []
    real_fsync, real_replace = atomic.os.fsync, atomic.os.replace

    monkeypatch.setattr(atomic.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(atomic.os, "replace",
                        lambda s, d: (order.append("replace"), real_replace(s, d))[1])
    atomic.write_text(tmp_path / "rec.md", "body")

    assert order == ["fsync", "replace"]


def test_concurrent_readers_never_see_a_partial_record(tmp_path):
    """The whole point of the issue: a reader interleaved with a writer gets
    one whole version or the other, never a truncated transcript."""
    p = tmp_path / "scene.md"
    _write_prior(p)
    new = PRIOR + "\n**Seraphine:** a second message\n"
    observed = []

    with atomic.tempfile_for(p) as tmp:
        tmp.write_text(new, encoding="utf-8")
        observed.append(p.read_text(encoding="utf-8"))  # mid-write
    observed.append(p.read_text(encoding="utf-8"))      # after publish

    assert observed == [PRIOR, new]
