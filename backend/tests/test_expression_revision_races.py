"""Maintenance and concurrent edits cannot destroy a copied collection."""

import os
import time
from dataclasses import replace

import pytest

from grimoire.store import assets, atomic, characters, worlds
from grimoire.store.expression_library import contracts, lifecycle, paths, read, transfer
from tests.test_expressions_store import apply, outfit
from tests.test_expressions_store import target as target


def _destination(target):
    aid, vid = characters.create_character(worlds.world_root(target.owner_id), "Winifred")
    return replace(target, actor_id=aid, version_id=vid)


def test_cleanup_refuses_linked_revisions_without_deleting_target(target, tmp_path):
    directory = paths.directory(target)
    directory.mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    revision = unrelated / ("a" * 32)
    revision.mkdir(parents=True)
    sentinel = revision / "keep.txt"
    sentinel.write_text("synthetic unrelated data", encoding="utf-8")
    os.utime(revision, (time.time() - 7200,) * 2)
    try:
        (directory / "revisions").symlink_to(unrelated, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")
    with pytest.raises(contracts.ExpressionUnavailableError):
        lifecycle.cleanup_revisions(target)
    assert sentinel.read_text(encoding="utf-8") == "synthetic unrelated data"


@pytest.mark.parametrize("copy_mode", ["local", "effective"])
def test_cleanup_preserves_active_copy_after_grace_period(target, monkeypatch, copy_mode):
    outfit(target)
    destination = _destination(target)
    directory = paths.directory(destination)
    original = atomic.write_bytes
    observed = []

    def clean_during_stage(path, payload, *args, **kwargs):
        result = original(path, payload, *args, **kwargs)
        if path.is_relative_to(directory / "revisions"):
            revision = directory / "revisions" / path.relative_to(directory / "revisions").parts[0]
            os.utime(revision, (time.time() - 7200,) * 2)
            observed.append(lifecycle.cleanup_revisions(destination))
        return result

    monkeypatch.setattr(atomic, "write_bytes", clean_during_stage)
    if copy_mode == "local":
        transfer.copy_local(paths.directory(target), directory)
    else:
        lifecycle.copy_effective(target, destination)
    assert observed and all(count == 0 for count in observed)
    assert read.effective(destination)["outfits"]["everyday"]["expressions"]["neutral"]["available"]


def test_effective_copy_cannot_overwrite_successful_destination_edit(target, monkeypatch):
    outfit(target)
    destination = _destination(target)
    outfit(destination)
    original = assets.link_in
    edited = False

    def edit_during_stage(*args, **kwargs):
        nonlocal edited
        result = original(*args, **kwargs)
        if not edited:
            edited = True
            apply(destination, {"op": "patch-outfit", "outfit": "everyday",
                                "metadata": {"label": "Travel"}})
        return result

    monkeypatch.setattr(assets, "link_in", edit_during_stage)
    with pytest.raises(contracts.ExpressionConflictError):
        lifecycle.copy_effective(target, destination)
    assert edited
    assert read.effective(destination)["outfits"]["everyday"]["label"] == "Travel"
