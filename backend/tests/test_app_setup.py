"""First-run setup (108): the app started on a place with no library makes or opens one through ``setup.*``,
refuses a V1 library and any unusable place before anything is written, and then serves the library's own ops.
Synthetic folders only."""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
from pathlib import Path

import pytest

from grimoire.app import AppServer
from grimoire.app.appapi import AppApi
from grimoire.app.cli import main
from grimoire.app.launcher import SETUP_OPS, Launcher
from grimoire.app.libraries import DEFAULT_OWNER, SetupError, create_library, inspect, open_authority
from grimoire.app.server import TOKEN_HEADER
from grimoire.ledgers import DurableAuthority
from grimoire.store import LibraryStore
from grimoire.sync import device_of, identify
from grimoire.textapi import ApiRefused, TextApi


def _build(store, authority):  # type: ignore[no-untyped-def]
    return TextApi(store, authority, owner=DEFAULT_OWNER)


@pytest.fixture
def launcher(tmp_path):
    made = Launcher(owner=DEFAULT_OWNER, build=_build, suggested=tmp_path / "library")
    yield made
    made.close()


def _v1_library(root: Path) -> Path:
    """A synthetic V1 library layout."""
    (root / "worlds" / "w").mkdir(parents=True)
    (root / "campaigns").mkdir()
    (root / "worlds" / "w" / "world.md").write_text("synthetic\n")
    (root / "llm_connections").mkdir()
    (root / "config.md").write_text("synthetic\n")
    return root


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _refused(launcher: Launcher, op: str, args: dict) -> ApiRefused:
    with pytest.raises(ApiRefused) as refused:
        launcher.call(op, args)
    return refused.value


def test_before_a_library_is_open_only_setup_is_served(launcher, tmp_path):
    assert launcher.ops == tuple(sorted(SETUP_OPS))
    assert launcher.call("setup.status", {}) == {
        "open": False, "library": None, "suggested": str(tmp_path / "library"), "owner": DEFAULT_OWNER,
        "device": None, "generation": False}
    assert _refused(launcher, "chats", {}).reason == "no_library"
    assert _refused(launcher, "library", {}).reason == "no_library"
    with pytest.raises(ApiRefused, match="no library"):
        launcher.image("img_x")


@pytest.mark.parametrize("make, state", [
    (lambda root: root / "new", "new"),
    (lambda root: (root / "empty").mkdir() or root / "empty", "empty"),
    (lambda root: (root / "busy").mkdir() or (root / "busy" / "notes.txt").write_text("x") and root / "busy", "occupied"),
])
def test_a_place_is_checked_without_writing(launcher, tmp_path, make, state):
    place = make(tmp_path)
    before = _tree(tmp_path)
    assert launcher.call("setup.check", {"path": str(place)}) == {"path": str(place.resolve()), "state": state}
    assert _tree(tmp_path) == before


def test_a_v1_library_or_an_unusable_place_is_refused_before_anything_is_written(launcher, tmp_path):
    v1 = _v1_library(tmp_path / "v1")
    before = _tree(tmp_path)
    for place in (v1, v1 / "inside"):
        refused = _refused(launcher, "setup.create", {"path": str(place), "device": "desk"})
        assert refused.reason == "v1_library" and refused.status == 409
        assert _refused(launcher, "setup.check", {"path": str(place)}).reason == "v1_library"
    for bad in ("relative/folder", str(tmp_path / "missing" / "deeper")):
        refused = _refused(launcher, "setup.create", {"path": bad, "device": "desk"})
        assert (refused.reason, refused.status) == ("bad_path", 400)
    refused = _refused(launcher, "setup.create", {"path": str(tmp_path / "new"), "device": "Not A Device!"})
    assert (refused.reason, refused.status) == ("invalid_device", 400)
    assert _refused(launcher, "setup.create", {"path": str(tmp_path / "new"), "device": 3}).reason == "invalid_field"
    assert _tree(tmp_path) == before
    assert not launcher.is_open


def test_making_a_library_opens_it_and_serves_its_ops(launcher, tmp_path):
    place = tmp_path / "library"
    status = launcher.call("setup.create", {"path": str(place), "device": "desk"})
    assert status == {"open": True, "library": str(place.resolve()), "suggested": str(place), "owner": DEFAULT_OWNER,
                      "device": "desk", "generation": False}
    library = launcher.call("library", {})
    assert library["device"] == "desk" and library["owner"] == DEFAULT_OWNER
    assert set(SETUP_OPS) <= set(library["ops"]) and "chats" in library["ops"]
    assert launcher.call("chats", {})["chats"] == []
    # one library per serve
    assert _refused(launcher, "setup.create", {"path": str(tmp_path / "other"), "device": "desk"}).reason == "library_open"
    assert _refused(launcher, "setup.open", {"path": str(place)}).reason == "library_open"
    assert not (tmp_path / "other").exists()


def test_a_place_that_holds_a_library_or_other_things_is_never_made_over(launcher, tmp_path):
    create_library(tmp_path / "kept", device="desk")
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "notes.txt").write_text("x")
    assert _refused(launcher, "setup.create", {"path": str(tmp_path / "kept"), "device": "phone"}).reason == "already_a_library"
    assert _refused(launcher, "setup.create", {"path": str(tmp_path / "busy"), "device": "phone"}).reason == "occupied"
    assert _refused(launcher, "setup.open", {"path": str(tmp_path / "busy")}).reason == "not_a_library"
    assert _refused(launcher, "setup.open", {"path": str(tmp_path / "nothing")}).reason == "not_a_library"
    assert (tmp_path / "busy" / "notes.txt").read_text() == "x" and not (tmp_path / "nothing").exists()


def test_opening_a_library_keeps_its_device_and_names_an_unnamed_one_once(tmp_path):
    create_library(tmp_path / "named", device="desk")
    named = Launcher(owner=DEFAULT_OWNER, build=_build)
    try:
        # a different name is refused, not dropped: the person must never think the device was renamed
        refused = _refused(named, "setup.open", {"path": str(tmp_path / "named"), "device": "phone"})
        assert refused.reason == "device_differs" and not named.is_open
        assert named.call("setup.open", {"path": str(tmp_path / "named"), "device": "desk"})["device"] == "desk"
    finally:
        named.close()
    LibraryStore.create(tmp_path / "bare").close()
    bare = Launcher(owner=DEFAULT_OWNER, build=_build)
    try:
        refused = _refused(bare, "setup.open", {"path": str(tmp_path / "bare"), "device": "Not A Device!"})
        assert (refused.reason, refused.status) == ("invalid_device", 400)
        assert bare.call("setup.open", {"path": str(tmp_path / "bare"), "device": "phone"})["device"] == "phone"
    finally:
        bare.close()
    store = LibraryStore.open(tmp_path / "bare")
    try:
        assert device_of(store) == "phone"
    finally:
        store.close()


def test_a_closed_launcher_lets_the_library_be_opened_again(launcher, tmp_path):
    launcher.call("setup.create", {"path": str(tmp_path / "library"), "device": "desk"})
    launcher.close()
    LibraryStore.open(tmp_path / "library").close()  # not left locked


def test_the_registry_follows_the_launcher_from_setup_to_the_library(launcher, tmp_path):
    server = AppServer(launcher, token="t")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def get(path: str) -> tuple[int, dict]:
            host, port = server.address
            connection = http.client.HTTPConnection(host, port, timeout=10)
            connection.request("GET", path, headers={"Host": f"{host}:{port}"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        def post(op: str, args: dict) -> tuple[int, dict]:
            host, port = server.address
            connection = http.client.HTTPConnection(host, port, timeout=10)
            connection.request("POST", f"/api/{op}", body=json.dumps(args).encode(),
                               headers={"Host": f"{host}:{port}", "Content-Type": "application/json", TOKEN_HEADER: "t"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        _, registry = get("/shell/pages")
        pages = {page["id"]: page for page in registry["pages"]}
        assert set(registry["ops"]) == set(SETUP_OPS)
        assert pages["setup"]["missing"] == [] and "library" in pages["home"]["missing"]
        assert post("chats", {}) == (409, {"error": "no_library", "message": "no library is open yet: set one up first"})
        assert post("setup.create", {"path": str(tmp_path / "library"), "device": "desk"})[0] == 200
        _, registry = get("/shell/pages")
        assert {page["id"]: page for page in registry["pages"]}["home"]["missing"] == []
        assert post("library", {})[1]["device"] == "desk"
    finally:
        server.close()


def test_a_library_the_serve_opened_is_reported_and_left_to_it_to_close(tmp_path):
    create_library(tmp_path / "library", device="desk")
    store = LibraryStore.open(tmp_path / "library")
    try:
        authority = open_authority(store, DEFAULT_OWNER)
        try:
            launcher = Launcher(owner=DEFAULT_OWNER, build=_build, opened=(tmp_path / "library",
                                                                             AppApi(_build(store, authority))))
            assert launcher.call("setup.status", {})["library"] == str(tmp_path / "library")
            launcher.close()
            for op in ("library", "setup.status"):  # a request in flight as the serve stops meets a refusal
                assert _refused(launcher, op, {}).reason == "closed"
            assert device_of(store) == "desk"  # still open: the serve closes it
        finally:
            authority.close()
    finally:
        store.close()
    assert os.path.isdir(tmp_path / "library")


def test_a_closed_launcher_opens_nothing_more(launcher, tmp_path):
    launcher.close()
    assert _refused(launcher, "setup.create", {"path": str(tmp_path / "late"), "device": "desk"}).reason == "closed"
    assert not (tmp_path / "late").exists()


def test_a_create_that_stopped_before_its_marker_is_offered_and_finished(launcher, tmp_path):
    place = tmp_path / "library"
    (place / ".txn").mkdir(parents=True)
    (place / ".txn" / "lock").write_bytes(b"")  # what a create killed before its marker leaves
    assert launcher.call("setup.check", {"path": str(place)})["state"] == "empty"
    assert launcher.call("setup.create", {"path": str(place), "device": "desk"})["open"] is True
    (tmp_path / "busy" / ".txn").mkdir(parents=True)
    (tmp_path / "busy" / ".txn" / "notes.txt").write_text("x")  # anything else in .txn is not a create's
    assert inspect(tmp_path / "busy").state == "occupied"
    (tmp_path / "file").mkdir()
    (tmp_path / "file" / ".txn").write_text("x")  # a file named .txn is not a create's either
    assert inspect(tmp_path / "file").state == "occupied"


def test_a_create_that_fails_after_the_store_exists_says_so(tmp_path, monkeypatch):
    def broken(self, grant):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr(DurableAuthority, "add_grant", broken)
    with pytest.raises(SetupError) as failed:
        create_library(tmp_path / "library", device="desk")
    assert failed.value.reason == "create_incomplete" and "move that folder aside" in str(failed.value)


def test_a_server_over_one_open_library_serves_the_setup_ops_too(tmp_path):
    create_library(tmp_path / "library", device="desk")
    store = LibraryStore.open(tmp_path / "library")
    try:
        authority = open_authority(store, DEFAULT_OWNER)
        try:
            server = AppServer(_build(store, authority), token="t")
            try:
                assert set(SETUP_OPS) <= set(server.registry["ops"])
                assert {page["id"]: page for page in server.registry["pages"]}["setup"]["missing"] == []
                assert server.call("setup.status", {})["device"] == "desk"
                other = {"path": str(tmp_path / "other"), "device": "desk"}
                assert _refused_on(server, "setup.create", other) == "library_open"
            finally:
                server.close()
            with pytest.raises(ValueError, match="own adapters"):
                AppServer(Launcher(owner=DEFAULT_OWNER, build=_build), adapters=())
        finally:
            authority.close()
    finally:
        store.close()


def _refused_on(server: AppServer, op: str, args: dict) -> str:
    with pytest.raises(ApiRefused) as refused:
        server.call(op, args)
    return refused.value.reason


def test_serve_opens_first_run_only_where_no_library_was_named_or_setup_was_asked(tmp_path, capsys):
    named = tmp_path / "Seraphne"  # a typo, or a drive not mounted
    assert main(["serve", str(named)]) == 2
    assert "--setup" in capsys.readouterr().err and not named.exists()
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "notes.txt").write_text("x")
    assert main(["serve", str(tmp_path / "busy"), "--setup"]) == 2
    assert main(["serve", str(named), "--setup", "--peer", "desktop=127.0.0.1:9"]) == 2
    assert not named.exists()


def test_serve_takes_a_library_path_relative_to_where_it_was_typed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "Seraphine", "--device", "desk"]) == 0
    capsys.readouterr()
    with socket.socket() as taken:  # the library opens; only the port stops the serve
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        assert main(["serve", "Seraphine", "--port", str(taken.getsockname()[1])]) == 2
        assert "cannot listen" in capsys.readouterr().err


def test_a_marker_this_version_cannot_open_is_neither_opened_nor_made_over(launcher, tmp_path, capsys):
    place = tmp_path / "odd"
    place.mkdir()
    (place / "grimoire-library.json").write_text('{"format": "not-a-v2-library"}')
    before = _tree(tmp_path)
    assert launcher.call("setup.check", {"path": str(place)})["state"] == "invalid"
    assert _refused(launcher, "setup.create", {"path": str(place), "device": "desk"}).reason == "occupied"
    assert _refused(launcher, "setup.open", {"path": str(place)}).reason == "not_a_library"
    assert main(["serve", str(place), "--setup"]) == 2 and "cannot open" in capsys.readouterr().err
    assert _tree(tmp_path) == before


def test_a_refused_device_name_writes_nothing_to_the_library(launcher, tmp_path):
    bare = LibraryStore.create(tmp_path / "library")
    try:
        identify(bare, "desk")  # named, but no authority yet: opening it would write the first ledger entry
    finally:
        bare.close()
    before = _tree(tmp_path / "library")
    assert _refused(launcher, "setup.open", {"path": str(tmp_path / "library"), "device": "phone"}).reason == \
        "device_differs"
    assert [name for name in _tree(tmp_path / "library") if name not in before and not name.startswith(".txn")] == []
