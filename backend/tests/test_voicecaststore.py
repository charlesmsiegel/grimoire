"""The voice casting's file (083, docs/VOICE_CASTING.md): one etag-checked file in the library's own config
folder, a file that is not a casting reported and never written over, no link followed, and every write explored
under the fault harness (docs/TESTING.md). Synthetic content only."""

from __future__ import annotations

import json
import os

import pytest
from store_helpers import FAIL_RECOVERY, drop_empty_dirs, explore_mode

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.store import StaleWrite, etag
from grimoire.testing import (
    CrashScenario, Interleave, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery,
    no_residue, preserves_interleave,
)
from grimoire.voicecast import CastEntry, Casting, CastingError, SpeechVoice
from grimoire.voicecaststore import PATH, CastingStore

WREN = SourceRef.parse("world:harbor/character/wren")
ODO = InstanceRef.parse("campaign:quay/character/odo-1")
NARRATOR = CastEntry("narrator", SpeechVoice("flite", "slt"))
WREN_KAL = CastEntry("character", SpeechVoice("flite", "kal"), WREN)
ODO_RMS = CastEntry("actor", SpeechVoice("flite", "rms"), ODO)


def _refused(reason: str, work) -> CastingError:  # type: ignore[no-untyped-def]
    with pytest.raises(CastingError) as refused:
        work()
    assert refused.value.reason == reason, str(refused.value)
    return refused.value


def _files(root) -> list[str]:  # type: ignore[no-untyped-def]
    return sorted(str(path.relative_to(root)).replace(os.sep, "/") for path in root.rglob("*") if path.is_file())


def _add(entry: CastEntry):  # type: ignore[no-untyped-def]
    return lambda casting: casting.with_entry(entry)


def test_a_library_with_no_casting_reads_as_empty_and_reading_makes_nothing(tmp_path):
    view = CastingStore(tmp_path).view()
    assert (view.casting, view.etag, view.problem) == (Casting(), None, None)
    assert list(tmp_path.iterdir()) == []


def test_the_casting_is_one_file_whose_etag_every_write_names(tmp_path):
    store = CastingStore(tmp_path)
    first = store.write(_add(WREN_KAL), None)
    second = store.write(_add(NARRATOR), first.etag)
    assert _files(tmp_path) == [PATH]
    data = (tmp_path / PATH).read_bytes()
    assert second.etag == etag(data) and CastingStore(tmp_path).view() == second
    document = json.loads(data)
    assert document["format"] == "grimoire.voice-casting/1"
    assert [entry["target"] for entry in document["cast"]] == ["character", "narrator"]
    # recasting a target replaces its voice; the same casting is the same bytes whoever writes it
    recast = store.write(_add(CastEntry("character", SpeechVoice("flite", "awb"), WREN)), second.etag)
    assert recast.casting.get("character", WREN).voice.voice == "awb" and len(recast.casting.entries) == 2
    other = tmp_path / "other"
    again = CastingStore(other)
    again.write(_add(NARRATOR), None)
    again.write(_add(CastEntry("character", SpeechVoice("flite", "awb"), WREN)), again.view().etag)
    assert (other / PATH).read_bytes() == (tmp_path / PATH).read_bytes()


def test_uncasting_everything_leaves_no_file_and_an_unchanged_casting_is_not_rewritten(tmp_path):
    store = CastingStore(tmp_path)
    cast = store.write(_add(ODO_RMS), None)
    stamp = (tmp_path / PATH).stat().st_mtime_ns
    same = store.write(_add(ODO_RMS), cast.etag)
    assert same == cast and (tmp_path / PATH).stat().st_mtime_ns == stamp
    gone = store.write(lambda casting: casting.without("actor", ODO), cast.etag)
    assert (gone.casting, gone.etag) == (Casting(), None) and _files(tmp_path) == []
    _refused("not_cast", lambda: store.write(lambda casting: casting.without("actor", ODO), None))


def test_a_stale_write_is_refused_and_nothing_changes(tmp_path):
    store = CastingStore(tmp_path)
    first = store.write(_add(WREN_KAL), None)
    store.write(_add(NARRATOR), first.etag)
    held = (tmp_path / PATH).read_bytes()
    with pytest.raises(StaleWrite):
        store.write(_add(ODO_RMS), first.etag)
    with pytest.raises(StaleWrite):
        store.write(_add(ODO_RMS), None)  # it read no file, but there is one now
    assert (tmp_path / PATH).read_bytes() == held
    _refused("invalid_field", lambda: store.write(_add(ODO_RMS), "an etag"))


def test_a_file_that_is_not_a_casting_is_reported_and_never_written_over(tmp_path):
    target = tmp_path / PATH
    target.parent.mkdir(parents=True)
    for data, reason in ((b"{not json", "malformed"),
                         (json.dumps({"format": "grimoire.voice-casting/9", "cast": []}).encode(), "unsupported_format"),
                         (b" " * (1024 * 1024 + 1), "too_large")):
        target.write_bytes(data)
        view = CastingStore(tmp_path).view()
        assert (view.casting, view.etag) == (Casting(), None)
        assert (view.problem.path, view.problem.reason) == (PATH, reason)
        _refused("unreadable_record", lambda: CastingStore(tmp_path).write(_add(NARRATOR), None))
        assert target.read_bytes() == data


def test_a_link_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    (outside / "voices").mkdir(parents=True)
    library = tmp_path / "library"
    library.mkdir()
    try:
        os.symlink(outside, library / "config", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system makes no links")
    store = CastingStore(library)
    assert store.view().problem.reason == "link"
    _refused("link", lambda: store.write(_add(NARRATOR), None))
    store.recover()
    assert list((outside / "voices").iterdir()) == []


def test_the_open_librarys_guards_are_asked_before_every_read_and_write(tmp_path):
    asked: list[str] = []

    def guard(name: str):  # type: ignore[no-untyped-def]
        return lambda: asked.append(name)

    store = CastingStore(tmp_path, check_read=guard("read"), check_write=guard("write"))
    store.write(_add(NARRATOR), None)
    assert asked.count("write") >= 2 and "read" in asked

    def moved() -> None:
        raise OSError("the library root moved")

    with pytest.raises(OSError):
        CastingStore(tmp_path, check_write=moved).write(_add(ODO_RMS), store.view().etag)
    assert CastingStore(tmp_path).view().casting == Casting((NARRATOR,))


def test_an_edit_made_before_the_last_uncast_removes_the_file_is_kept(tmp_path):
    newer = json.dumps({"format": "grimoire.voice-casting/1", "cast": [
        {"target": "narrator", "ref": None, "campaign": None, "engine": "flite", "voice": "kal", "rights": "engine",
         "note": None}]}, sort_keys=True, indent=2).encode() + b"\n"
    for meddled in (newer, b"{not json"):
        root = tmp_path / str(len(meddled))
        cast = CastingStore(root).write(_add(NARRATOR), None)
        edits = [lambda: (root / PATH).write_bytes(meddled)]  # another editor writes as this write begins

        def check_write() -> None:
            if edits:
                edits.pop()()

        with pytest.raises(StaleWrite):
            CastingStore(root, check_write=check_write).write(lambda casting: Casting(), cast.etag)
        assert (root / PATH).read_bytes() == meddled


def test_recover_sweeps_a_dead_writes_temp_file_and_nothing_else(tmp_path):
    store = CastingStore(tmp_path)
    cast = store.write(_add(NARRATOR), None)
    stray = tmp_path / "config" / "voices" / ".casting-abc.tmp"
    stray.write_bytes(b"half a casting")
    assert CastingStore(tmp_path).view() == cast  # a temp file is no problem to report
    store.recover()
    assert _files(tmp_path) == [PATH] and store.view() == cast
    stray.write_bytes(b"again")
    store.write(_add(ODO_RMS), cast.etag)  # the next write sweeps it too
    assert not stray.exists()


# -- crashes ---------------------------------------------------------------------------------------------
def _seed(ops, root):  # type: ignore[no-untyped-def]
    store = CastingStore(root, ops=ops)
    store.write(_add(NARRATOR), None)
    store.write(_add(WREN_KAL), store.view().etag)


def _empty(ops, root):  # type: ignore[no-untyped-def]
    ops.write_bytes(root / "grimoire-library.json", b"{}\n")


def first_cast(ops, root):  # type: ignore[no-untyped-def]
    CastingStore(root, ops=ops).write(_add(ODO_RMS), None)


def recast(ops, root):  # type: ignore[no-untyped-def]
    store = CastingStore(root, ops=ops)
    store.write(_add(CastEntry("character", SpeechVoice("flite", "awb"), WREN)), store.view().etag)


def uncast_all(ops, root):  # type: ignore[no-untyped-def]
    store = CastingStore(root, ops=ops)
    store.write(lambda casting: Casting(), store.view().etag)


def _recover(ops, root):  # type: ignore[no-untyped-def]
    store = CastingStore(root, ops=ops)
    store.recover()
    assert store.view().problem is None  # whatever happened, the casting still reads


SCENARIOS = {
    "first-cast": (first_cast, _empty, Target("replace", target=PATH)),
    "recast": (recast, _seed, Target("replace", target=PATH)),
    "uncast-all": (uncast_all, _seed, Target("remove", path=PATH)),
}


def _scenario(name: str) -> CrashScenario:
    action, setup, commit = SCENARIOS[name]
    return CrashScenario(name=f"casting {name}", setup=setup, action=action, recover=_recover,
                         invariants=(atomic_outcome(), idempotent_recovery(), no_residue("*.tmp"),
                                     completes_after(commit)),
                         normalize=drop_empty_dirs)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("mode", ["errors", "recovery-crashes", FAIL_RECOVERY])
def test_casting_writes_survive_every_crash_error_and_recovery_crash(tmp_path, name, mode):
    report = explore_crashes(_scenario(name), tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored >= (1 if name == "uncast-all" else 3)


def test_an_edit_made_while_a_write_is_under_way_is_refused_not_lost(tmp_path):
    other = json.dumps({"format": "grimoire.voice-casting/1", "cast": [
        {"target": "narrator", "ref": None, "campaign": None, "engine": "flite", "voice": "kal", "rights": "engine",
         "note": None}]}, sort_keys=True, indent=2).encode() + b"\n"

    def meddle(ops, root):  # type: ignore[no-untyped-def]
        ops.write_bytes(root / PATH, other)

    def action(ops, root):  # type: ignore[no-untyped-def]
        with pytest.raises(StaleWrite):
            recast(ops, root)

    scenario = CrashScenario(name="casting interleaved edit", setup=_seed, action=action, recover=_recover,
                             invariants=(idempotent_recovery(), no_residue("*.tmp"), preserves_interleave()))
    report = explore_crashes(scenario, tmp_path, interleaves=[
        Interleave(Target("write_bytes", path="config/voices/.casting-*"), meddle)])
    report.assert_ok()
    assert report.runs[0].recovered[PATH] == other and report.points_explored >= 3
