"""109's Library pages in the 108 shell: the ``sources`` adapter ops over the open library, the two pages'
manifests, and the pages end to end in Node's stand-in DOM (``js/library.e2e.mjs``) against a real app server.

The proving scenario: two same-named characters listed apart, a portrayal derived from one with an override and an
explicit clear, a stale edit kept as a draft and saved over the new head, and its exact revision attached to a chat.
Synthetic libraries from ``textapi_fixtures``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from textapi_fixtures import Device, desktop_library

from grimoire.app import AppServer
from grimoire.app.adapters.sources import ADAPTER, OPS
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.app.server import STATIC
from grimoire.textapi import ApiRefused

MARA = "card:mara-vell/character/mara-vell"
TESTS = Path(__file__).with_name("js")
NODE = shutil.which("node")


@pytest.fixture
def app(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        yield AppApi(opened.api), opened, str(chat)


def _derive(app: AppApi) -> str:
    app.call("sources.create_source", {"source": "world:harbor", "title": "Harbor"})
    etag = next(item["etag"] for item in app.call("sources.catalog", {"limit": 1})["sources"] if item["source"] == "world:harbor")
    return app.call("sources.derive", {"base": MARA, "source": "world:harbor", "name": "Mara of the quay",
                                       "expected": etag})["ref"]


def _edit(app: AppApi, ref: str, op: str, **args) -> str:
    read = app.call("sources.read", {"ref": ref})
    return app.call(op, {"ref": read["entity"], "head": read["head"], "etag": read["etag"],
                         "expected": read["manifest_etag"], **args})["ref"]


def _states(read: dict) -> dict:
    return {item["field"]: item["state"] for item in read["effective"]["fields"]}


def test_the_library_pages_are_registered_and_every_op_they_need_is_served(app):
    api, _opened, _chat = app
    pages = {page.route: page for page in load_pages(STATIC)}
    assert pages["sources"].slot == "library" and pages["sources"].owner == "109"
    assert pages["source"].slot == "hidden" and pages["source"].owner == "109"
    served = set(api.ops)
    for route in ("sources", "source"):
        assert set(pages[route].requires) <= served
    assert {"sources." + name for name in OPS} <= served
    assert ADAPTER.owner == "109"


def test_same_named_characters_are_listed_apart_by_their_exact_refs(app):
    api, _opened, _chat = app
    entries = api.call("sources.catalog", {"query": "mara", "kind": "character"})["entries"]
    named = [entry for entry in entries if entry["name"] == "Mara"]
    assert len(named) == 3 and len({entry["ref"] for entry in named}) == 3
    for entry in named:
        assert set(entry["namesakes"]) == {other["ref"] for other in named} - {entry["ref"]}


def test_the_adapter_writes_as_the_text_apis_own_handle_and_never_asks_for_one(app):
    api, opened, _chat = app

    def refuse(*_args, **_kwargs):
        raise AssertionError("an adapter asked the authority for a handle")

    opened.api.authority.handle_for = refuse  # instance attribute: only this test's authority
    ref = _derive(api)
    assert ref.endswith("@r1") and api.call("sources.read", {"ref": ref})["derived"] is True


def test_any_chat_can_be_found_to_attach_to_not_only_the_most_recent(app):
    api, _opened, chat = app
    everything = api.call("sources.chats", {})
    assert len(everything["chats"]) == 2 and everything["truncated"] is False
    newest = api.call("sources.chats", {"limit": 1})
    assert len(newest["chats"]) == 1 and newest["truncated"] is True
    older = next(item["chat"] for item in everything["chats"] if item["chat"] != newest["chats"][0]["chat"])
    found = api.call("sources.chats", {"limit": 1, "query": older.split("#")[0].split(":")[1].upper()})
    assert [item["chat"] for item in found["chats"]] == [older] and found["truncated"] is False
    by_label = api.call("sources.chats", {"query": "tide comes"})
    assert [item["label"] for item in by_label["chats"]] == ["The tide comes in"]
    assert api.call("sources.chats", {"query": "no such chat"}) == {"chats": [], "truncated": False}
    assert chat in {item["chat"] for item in everything["chats"]}


def test_the_proving_scenario_through_the_adapter(app):
    api, _opened, chat = app
    portrayal = _derive(api)
    r2 = _edit(api, portrayal, "sources.portrayal", edit="override_section", name="personality", text="Sharp, and done waiting.")
    r3 = _edit(api, r2, "sources.portrayal", edit="clear_description")
    read = api.call("sources.read", {"ref": r3})
    states = _states(read)
    assert states["section:personality"] == "overridden" and states["description"] == "cleared"
    assert read["revisions"] == ["r1", "r2", "r3"] and read["base"] == MARA + "@r1"
    # the older revisions are still exactly what they were
    assert _states(api.call("sources.read", {"ref": r2}))["description"] == "inherited"

    target = next(item for item in api.call("sources.chats", {})["chats"] if item["chat"] == chat)
    done = api.call("sources.attach", {"ref": r3, "chat": chat, "expected": target["etag"]})
    assert done["ref"] == r3 and done["role"] == "character"
    bound = api.call("sources.read", {"ref": r3})["chats"]
    assert [(item["chat"], item["revisions"]) for item in bound] == [(chat, ["r3"])]


def test_a_stale_edit_through_the_adapter_is_refused_changed_and_writes_nothing(app):
    api, _opened, _chat = app
    portrayal = _derive(api)
    seen = api.call("sources.read", {"ref": portrayal})
    _edit(api, portrayal, "sources.save", fields={"name": "Mara Quayside"})
    with pytest.raises(ApiRefused) as refused:
        api.call("sources.save", {"ref": seen["entity"], "head": seen["head"], "etag": seen["etag"],
                                  "expected": seen["manifest_etag"], "fields": {"name": "Mara of the lamps"}})
    assert refused.value.reason == "changed" and refused.value.status == 409
    head = api.call("sources.read", {"ref": seen["entity"]})
    assert head["revisions"] == ["r1", "r2"] and head["name"] == "Mara Quayside"


def test_the_raw_editor_keeps_unknown_fields_through_the_adapter(app):
    api, _opened, _chat = app
    portrayal = _derive(api)
    payload = dict(api.call("sources.read", {"ref": portrayal})["payload"])
    payload["x_lamp_colour"] = {"hue": "amber", "kept": [1, 2]}
    saved = _edit(api, portrayal, "sources.save_raw", payload=payload)
    assert api.call("sources.read", {"ref": saved})["payload"]["x_lamp_colour"] == {"hue": "amber", "kept": [1, 2]}
    renamed = _edit(api, saved, "sources.save", fields={"name": "Mara of the lamps"})
    assert api.call("sources.read", {"ref": renamed})["payload"]["x_lamp_colour"] == {"hue": "amber", "kept": [1, 2]}


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_source_page_drafts_in_node():
    result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(TESTS / "library.unit.mjs")],
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# fail 0" in result.stdout


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_library_pages_end_to_end(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk) as device:
        server = AppServer(device.api)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": server.token,
                   "GRIMOIRE_CHAT": str(chat)}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(TESTS / "library.e2e.mjs")],
                                    capture_output=True, text=True, timeout=300, check=False, env=env)
        finally:
            server.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        # what the pages wrote is in the library: the portrayal's exact revision is bound to the chat
        read = AppApi(device.api).call("sources.read", {"ref": "world:harbor/character/mara-of-the-quay"})
        bound = {item["chat"]: item["revisions"] for item in read["chats"]}
        assert bound == {str(chat): [read["head"]]}
        assert read["payload"]["name"] == "Mara of the lamps" and read["payload"]["description"] == "Pale from the lamps."
        # the source and the scenario made in their dialogs are there, the source under the title it was renamed to
        made = AppApi(device.api).call("sources.read", {"ref": "world:harbor/scenario/night-watch"})
        assert made["source_title"] == "Harbor" and made["payload"]["premise"] == "The lamps are out along the quay."
        # the lorebook's first version was restored as a third: the rename between them is kept as it was written
        lore = AppApi(device.api).call("sources.read", {"ref": "card:mara-vell/lorebook/mara-vell-lore"})
        assert lore["revisions"] == ["r1", "r2", "r3"] and lore["payload"]["name"] == "Harbor lore"
        renamed = AppApi(device.api).call("sources.read", {"ref": "card:mara-vell/lorebook/mara-vell-lore@r2"})
        assert renamed["payload"]["name"] == "Mara's lore, renamed"
        # the tags and pins the pages wrote are on the shelf, by exact ref; the saved filter and the chat's pin are gone
        shelf = AppApi(device.api).call("shelf.read", {})
        marks = {item["ref"]: (item["tags"], item["pinned"]) for item in shelf["entries"]}
        assert marks[MARA] == (["crew"], False) and marks["card:mara-vell/lorebook/mara-vell-lore"] == (["old lore"], False)
        assert sum(1 for _tags, pinned in marks.values() if pinned) == len(marks) - 3  # Mara, the lorebook, one unpinned
        assert (shelf["filters"], shelf["chats"], shelf["problems"]) == ([], [], [])
