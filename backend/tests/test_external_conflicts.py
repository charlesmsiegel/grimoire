"""The store's external-conflict scan (#35): sync-tool leftovers, flagged."""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire import store
from grimoire.main import create_app
from grimoire.store import config, external


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def paths_of(result: dict) -> list[str]:
    return [c["path"] for c in result["conflicts"]]


def test_each_sync_tool_artifact_is_flagged_with_its_source(home):
    write(home / "worlds/realm/lore/pact.sync-conflict-20260101-120000-ABCDEFG.md")
    write(home / "worlds/realm/lore/pact (Winifred's conflicted copy 2026-01-01).md")
    write(home / "campaigns/saltmarch/locations/quay.md.orig")

    found = {c["path"]: c["tool"] for c in external.scan()["conflicts"]}
    assert found == {
        "campaigns/saltmarch/locations/quay.md.orig": "merge",
        "worlds/realm/lore/pact (Winifred's conflicted copy 2026-01-01).md": "dropbox",
        "worlds/realm/lore/pact.sync-conflict-20260101-120000-ABCDEFG.md": "syncthing",
    }


def test_ordinary_records_are_not_conflicts(home):
    write(home / "worlds/realm/lore/pact.md")
    write(home / "worlds/realm/world.md")
    assert external.scan() == {"conflicts": [], "truncated": False}


def test_a_conflict_reports_what_the_user_needs_to_find_it(home):
    write(home / "worlds/realm/lore/pact.sync-conflict-20260101-120000-A.md", "seven!")
    (conflict,) = external.scan()["conflicts"]
    assert conflict["name"] == "pact.sync-conflict-20260101-120000-A.md"
    assert conflict["kind"] == "file"
    assert conflict["size"] == 6
    # An ISO-8601 UTC stamp, the same shape every other store timestamp uses.
    assert conflict["modified"].endswith("Z") and conflict["modified"][4] == "-"


def test_the_scan_skips_caches_backups_and_sync_metadata(home):
    # `.stversions` / `.dropbox.cache` hold the sync client's own *copies* of
    # records; reporting those as conflicts would flag every ordinary edit.
    write(home / ".cache/derived/pact.sync-conflict-1.md")
    write(home / "backups/2026-01-01/pact.sync-conflict-1.md")
    write(home / "worlds/realm/.stversions/pact.sync-conflict-1.md")
    assert external.scan()["conflicts"] == []


def test_the_configured_backup_directory_is_skipped_wherever_it_sits(home):
    # `backup_dir` is a setting (#32), so the default name is not the whole
    # answer: an archive unpacked anywhere inside the store would otherwise
    # report a second copy of every conflict already listed from the live tree.
    config.write_config(backup_dir=str(home / "archives"))
    write(home / "archives/restored/pact.sync-conflict-1.md")
    write(home / "worlds/realm/lore/pact.sync-conflict-1.md")
    assert paths_of(external.scan()) == ["worlds/realm/lore/pact.sync-conflict-1.md"]


def test_a_backup_directory_outside_the_store_prunes_nothing(home, tmp_path):
    # Pointing it at an ancestor of the store is the natural "somewhere else",
    # and must not match every path in the walk and report a clean library.
    config.write_config(backup_dir=str(tmp_path.parent))
    write(home / "worlds/realm/lore/pact.sync-conflict-1.md")
    assert paths_of(external.scan()) == ["worlds/realm/lore/pact.sync-conflict-1.md"]


def test_a_world_named_backups_is_still_scanned(home):
    # Only the store root's reserved names are skipped -- `backups` deeper in
    # the tree is somebody's world, campaign or record directory.
    write(home / "worlds/backups/lore/pact.sync-conflict-1.md")
    assert paths_of(external.scan()) == ["worlds/backups/lore/pact.sync-conflict-1.md"]


def test_a_store_that_does_not_exist_yet_scans_clean(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "never-created"))
    assert external.scan() == {"conflicts": [], "truncated": False}


def test_results_are_capped_and_say_so(home, monkeypatch):
    monkeypatch.setattr(external, "MAX_RESULTS", 2)
    for i in range(5):
        write(home / f"worlds/realm/lore/pact-{i}.sync-conflict-1.md")
    result = external.scan()
    assert len(result["conflicts"]) == 2
    assert result["truncated"] is True
    # Capped, not sampled: the list stays in the same sorted order, so the two
    # shown are the first two and paging by eye works.
    assert paths_of(result) == sorted(paths_of(result))


def test_the_walk_itself_is_bounded(home, monkeypatch):
    monkeypatch.setattr(external, "MAX_ENTRIES", 3)
    for i in range(10):
        write(home / f"worlds/realm/lore/pact-{i}.md")
    write(home / "worlds/realm/lore/zzz.sync-conflict-1.md")
    result = external.scan()
    # Truncation is reported even though nothing was found: "no conflicts" and
    # "gave up looking" must not read the same to the caller.
    assert result["truncated"] is True


def test_a_conflicted_directory_is_reported_and_not_walked(home):
    # Dropbox renames whole folders too, and a shadow world resolves to
    # nothing: it is exactly the thing a user cannot see and must be told about.
    shadow = home / "worlds/Realm (Winifred's conflicted copy 2026-01-01)"
    write(shadow / "lore/pact.md")
    write(shadow / "lore/oath.sync-conflict-1.md")

    (row,) = external.scan()["conflicts"]
    assert row["path"] == "worlds/Realm (Winifred's conflicted copy 2026-01-01)"
    assert row["kind"] == "directory"
    assert row["size"] is None
    # Not descended: everything inside is a copy of something already reported
    # from the live tree, and listing it all would bury the line that matters.


def test_an_unreadable_root_is_an_error_not_an_empty_report(home, monkeypatch):
    def refuse(path):
        raise PermissionError(path)

    monkeypatch.setattr(external.os, "scandir", refuse)
    with pytest.raises(PermissionError):
        external.scan()


def test_an_unreadable_subtree_does_not_abort_the_scan(home, monkeypatch):
    write(home / "worlds/realm/lore/pact.sync-conflict-1.md")
    real_scandir = external.os.scandir

    def flaky(path):
        if str(path).endswith("campaigns"):
            raise PermissionError(path)
        return real_scandir(path)

    (home / "campaigns").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(external.os, "scandir", flaky)
    assert paths_of(external.scan()) == ["worlds/realm/lore/pact.sync-conflict-1.md"]


# ---- the route -------------------------------------------------------------

@pytest.fixture
def client(home):
    importlib.reload(store)
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(create_app()) as c:
        yield c


def test_the_route_reports_what_the_scan_found(client, home):
    write(home / "worlds/realm/lore/pact.sync-conflict-20260101-120000-A.md")
    body = client.get("/api/store/conflicts").json()
    assert body["truncated"] is False
    (row,) = body["conflicts"]
    assert row["path"] == "worlds/realm/lore/pact.sync-conflict-20260101-120000-A.md"
    assert row["tool"] == "syncthing"


def test_a_clean_store_reports_an_empty_list(client, home):
    write(home / "worlds/realm/lore/pact.md")
    assert client.get("/api/store/conflicts").json() == {"conflicts": [], "truncated": False}


def test_a_store_that_cannot_be_scanned_is_an_error_not_a_clean_bill(client, monkeypatch):
    monkeypatch.setattr(external, "scan", lambda: (_ for _ in ()).throw(PermissionError("nope")))
    res = client.get("/api/store/conflicts")
    assert res.status_code == 500
    assert "could not scan" in res.json()["detail"]


def test_a_symlinked_directory_is_never_descended(home):
    # A store on a synced volume is where somebody links one library into
    # another; following that is how a walk finds a cycle and never returns.
    write(home / "worlds/realm/lore/pact.md")
    (home / "worlds/loop").symlink_to(home / "worlds", target_is_directory=True)
    write(home / "worlds/realm/lore/pact.sync-conflict-1.md")
    assert paths_of(external.scan()) == ["worlds/realm/lore/pact.sync-conflict-1.md"]
