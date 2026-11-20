"""An author's mechanics modules on disk (086): drafts kept as saved, publish only when valid, immutable
content-named revisions, exact loads and etag-checked writes. Crash explorations: test_mechanicsstore_recovery.py."""

from __future__ import annotations

import json
import os

import pytest

from grimoire.mechanics import ModuleInvalid, fill_sheet, parse_module, run_check, template
from grimoire.mechanicsstore import MAX_DRAFT_BYTES, MechanicsStore, MechanicsStoreError
from grimoire.store import StaleWrite


def raw(document: object) -> bytes:
    return json.dumps(document, indent=1).encode()


def _files(root) -> list[str]:  # type: ignore[no-untyped-def]
    return sorted(str(path.relative_to(root)).replace(os.sep, "/") for path in root.rglob("*") if path.is_file())


def _refused(reason: str, work) -> MechanicsStoreError:  # type: ignore[no-untyped-def]
    with pytest.raises(MechanicsStoreError) as refused:
        work()
    assert refused.value.reason == reason, str(refused.value)
    return refused.value


def test_an_empty_library_has_no_drafts_or_revisions_and_reading_makes_nothing(tmp_path):
    store = MechanicsStore(tmp_path)
    assert store.draft("d20_basic") is None and store.revisions("d20_basic") == ()
    _refused("no_draft", lambda: store.check_draft("d20_basic"))
    _refused("unpublished", lambda: store.load("d20_basic", "sha256:" + "0" * 64))
    assert _files(tmp_path) == []


def test_a_malformed_draft_is_kept_exactly_and_cannot_be_published(tmp_path):
    store = MechanicsStore(tmp_path)
    unfinished = b'{"format": "grimoire.mechanics/1", "id": "d20_basic", '
    tag = store.save_draft("d20_basic", unfinished, None)
    assert store.draft("d20_basic").data == unfinished  # type: ignore[union-attr]
    checked = store.check_draft("d20_basic")
    assert checked.module is None and checked.problems[0].path == "$"
    with pytest.raises(ModuleInvalid):
        store.publish("d20_basic", tag)
    document = template("d20_basic")
    document["checks"]["stealth"]["total"] = "natural + stelth"
    tag = store.save_draft("d20_basic", raw(document), tag)
    with pytest.raises(ModuleInvalid, match="did you mean 'stealth'"):
        store.publish("d20_basic", tag)
    assert store.revisions("d20_basic") == ()
    assert _files(tmp_path) == ["config/mechanics/drafts/d20_basic.json"]


def test_publish_writes_one_immutable_revision_and_load_returns_exactly_it(tmp_path):
    store = MechanicsStore(tmp_path)
    tag = store.save_draft("pool_basic", raw(template("pool_basic")), None)
    module = store.publish("pool_basic", tag)
    assert module == parse_module(template("pool_basic"))
    assert store.publish("pool_basic", tag) == module  # the same content again: no new file
    assert store.revisions("pool_basic") == (module.revision,)
    assert store.load("pool_basic", module.revision) == module
    assert _files(tmp_path) == ["config/mechanics/drafts/pool_basic.json",
                                f"config/mechanics/modules/pool_basic/{module.revision[7:]}.json"]


def test_proving_scenario_on_disk_an_edit_publishes_beside_the_old_revision(tmp_path):
    store = MechanicsStore(tmp_path)
    tag = store.save_draft("d20_basic", raw(template("d20_basic")), None)
    first = store.publish("d20_basic", tag)
    old_file = tmp_path / "config/mechanics/modules/d20_basic" / f"{first.revision[7:]}.json"
    old_bytes = old_file.read_bytes()
    record = run_check(first, fill_sheet(first, "warrior", {"dexterity": 14}), "stealth", dice=(9,))
    assert (record.total, record.outcome) == (11, "failure")

    bad = template("d20_basic")
    bad["checks"]["stealth"]["dice"]["sides"] = 12
    bad_tag = store.save_draft("d20_basic", raw(bad), tag)
    with pytest.raises(ModuleInvalid, match="exactly one d20"):
        store.publish("d20_basic", bad_tag)  # a malformed edit cannot bind

    edited = template("d20_basic")
    edited["checks"]["stealth"]["difficulty"] = 10
    second = store.publish("d20_basic", store.save_draft("d20_basic", raw(edited), bad_tag))
    assert second.revision != first.revision and set(store.revisions("d20_basic")) == {first.revision,
                                                                                        second.revision}
    assert old_file.read_bytes() == old_bytes
    again = store.load("d20_basic", record.revision)
    from grimoire.mechanics import confirm
    confirm(again, record)
    fresh = run_check(second, fill_sheet(second, "warrior", {"dexterity": 14}), "stealth", dice=(9,))
    assert fresh.outcome == "success" and record.outcome == "failure"


def test_a_hand_edited_revision_is_refused_not_served(tmp_path):
    store = MechanicsStore(tmp_path)
    module = store.publish("pool_basic", store.save_draft("pool_basic", raw(template("pool_basic")), None))
    path = tmp_path / "config/mechanics/modules/pool_basic" / f"{module.revision[7:]}.json"
    document = json.loads(path.read_bytes())
    document["checks"]["brawl"]["difficulty"] = 5
    path.write_text(json.dumps(document))
    _refused("altered", lambda: store.load("pool_basic", module.revision))
    path.write_text("{")
    _refused("invalid", lambda: store.load("pool_basic", module.revision))
    assert path.read_text() == "{"  # left as it is


def test_republishing_over_an_altered_revision_is_refused(tmp_path):
    store = MechanicsStore(tmp_path)
    tag = store.save_draft("pool_basic", raw(template("pool_basic")), None)
    module = store.publish("pool_basic", tag)
    path = tmp_path / "config/mechanics/modules/pool_basic" / f"{module.revision[7:]}.json"
    path.write_bytes(b"{}")
    _refused("altered", lambda: store.publish("pool_basic", tag))
    assert path.read_bytes() == b"{}"


def test_a_draft_saved_under_another_id_is_a_problem(tmp_path):
    store = MechanicsStore(tmp_path)
    tag = store.save_draft("mine", raw(template("pool_basic")), None)
    checked = store.check_draft("mine")
    assert [str(p) for p in checked.problems] == ["id: is 'pool_basic', but this module is saved as 'mine'"]
    with pytest.raises(ModuleInvalid):
        store.publish("mine", tag)


def test_writes_name_what_they_read(tmp_path):
    store = MechanicsStore(tmp_path)
    tag = store.save_draft("duel", b"{}", None)
    with pytest.raises(StaleWrite):
        store.save_draft("duel", b"{ }", None)
    with pytest.raises(StaleWrite):
        store.save_draft("duel", b"{ }", "sha256:" + "1" * 64)
    good = store.save_draft("duel", raw(template("pool_basic") | {"id": "duel"}), tag)
    with pytest.raises(StaleWrite):
        store.publish("duel", tag)
    assert store.save_draft("duel", raw(template("pool_basic") | {"id": "duel"}), good) == good
    with pytest.raises(StaleWrite):
        store.remove_draft("duel", tag)
    store.remove_draft("duel", good)
    assert store.draft("duel") is None


@pytest.mark.parametrize("module_id", ["", "Duel", "../x", "a/b", "x" * 65, 3, "1abc"])
def test_ids_that_could_escape_or_collide_are_refused(tmp_path, module_id):
    store = MechanicsStore(tmp_path)
    _refused("invalid_id", lambda: store.save_draft(module_id, b"{}", None))
    _refused("invalid_id", lambda: store.draft(module_id))


@pytest.mark.parametrize("revision", ["", "sha256:" + "A" * 64, "sha256:../" + "0" * 61, "md5:" + "0" * 64])
def test_revisions_are_exact_digests(tmp_path, revision):
    _refused("invalid_revision", lambda: MechanicsStore(tmp_path).load("duel", revision))


def test_size_and_type_bounds(tmp_path):
    store = MechanicsStore(tmp_path)
    _refused("too_large", lambda: store.save_draft("duel", b" " * (MAX_DRAFT_BYTES + 1), None))
    _refused("invalid_draft", lambda: store.save_draft("duel", "{}", None))  # type: ignore[arg-type]
    _refused("invalid_field", lambda: store.save_draft("duel", b"{}", "nope"))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_links_are_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "lib"
    (root / "config").mkdir(parents=True)
    try:
        os.symlink(outside, root / "config" / "mechanics", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    store = MechanicsStore(root)
    _refused("link", lambda: store.save_draft("duel", b"{}", None))
    _refused("link", lambda: store.draft("duel"))
    store.recover()
    assert list(outside.iterdir()) == []


def test_recover_sweeps_only_temp_files(tmp_path):
    store = MechanicsStore(tmp_path)
    module = store.publish("pool_basic", store.save_draft("pool_basic", raw(template("pool_basic")), None))
    drafts = tmp_path / "config/mechanics/drafts"
    revisions = tmp_path / "config/mechanics/modules/pool_basic"
    (drafts / ".mechanics-x.tmp").write_bytes(b"half")
    (revisions / ".mechanics-y.tmp").write_bytes(b"half")
    (drafts / "notes.txt").write_bytes(b"mine")
    store.recover()
    assert _files(tmp_path) == ["config/mechanics/drafts/notes.txt", "config/mechanics/drafts/pool_basic.json",
                                f"config/mechanics/modules/pool_basic/{module.revision[7:]}.json"]
