"""Tests for the hardlink-probe image fork helper."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grimoire.orchestrator.fork_images import fork_image_files


async def test_hardlink_probe_succeeds_on_same_volume(tmp_path: Path) -> None:
    src = tmp_path / "campaigns/orig"
    (src / "images").mkdir(parents=True)
    (src / "images" / "img1.png").write_bytes(b"img-content")

    dst = tmp_path / "campaigns/new"
    dst.mkdir(parents=True, exist_ok=True)

    result = await fork_image_files(src, dst)
    assert result.handling == "hardlink"
    assert result.files_copied == 1
    assert (dst / "images" / "img1.png").read_bytes() == b"img-content"
    assert (src / "images" / "img1.png").stat().st_ino == (
        dst / "images" / "img1.png"
    ).stat().st_ino


async def test_hardlink_failure_falls_back_to_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*_a, **_kw):
        raise OSError("simulated cross-device")

    monkeypatch.setattr(os, "link", _fail)

    src = tmp_path / "campaigns/orig"
    (src / "images").mkdir(parents=True)
    (src / "images" / "img1.png").write_bytes(b"copied-bytes")

    dst = tmp_path / "campaigns/new"
    result = await fork_image_files(src, dst)
    assert result.handling == "deep_copy"
    assert result.files_copied == 1
    assert (dst / "images" / "img1.png").read_bytes() == b"copied-bytes"
    # Different inode → not a hardlink.
    assert (src / "images" / "img1.png").stat().st_ino != (
        dst / "images" / "img1.png"
    ).stat().st_ino


async def test_empty_image_dir_is_a_noop(tmp_path: Path) -> None:
    src = tmp_path / "campaigns/orig"
    src.mkdir(parents=True)
    dst = tmp_path / "campaigns/new"
    result = await fork_image_files(src, dst)
    assert result.handling == "hardlink"
    assert result.files_copied == 0


async def test_mid_run_failure_downgrades_to_mixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = os.link
    call_count = {"n": 0}

    def _link_then_fail(src, dst):
        call_count["n"] += 1
        # First call is the probe, second is the first real image: let
        # the probe through, fail on the real copy so handling stays
        # "hardlink" through the probe but downgrades to "mixed" after.
        if call_count["n"] <= 1:
            return real_link(src, dst)
        raise OSError("simulated mid-run failure")

    monkeypatch.setattr(os, "link", _link_then_fail)

    src = tmp_path / "campaigns/orig"
    (src / "images").mkdir(parents=True)
    (src / "images" / "img1.png").write_bytes(b"first")
    (src / "images" / "img2.png").write_bytes(b"second")

    dst = tmp_path / "campaigns/new"
    result = await fork_image_files(src, dst)
    assert result.handling == "mixed"
    assert result.files_copied == 2
