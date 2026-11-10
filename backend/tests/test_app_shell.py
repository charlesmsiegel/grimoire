"""108's shell contract: the page registry (``static/pages/<id>/page.json``), the static modules the shell is
built from, and the adapter seam (``<adapter>.<op>``) through which Wave 7 sprints add ops.

Synthetic libraries from ``textapi_fixtures``; test-owned page folders and adapters in a temporary directory.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import threading
from pathlib import Path

import pytest
from textapi_fixtures import Device, desktop_library

from grimoire.app import AppServer
from grimoire.app.adapter import Adapter
from grimoire.app.appapi import AppApi
from grimoire.app.pages import CONTRACT, SLOTS, PageManifestError, load_pages
from grimoire.app.server import STATIC, TOKEN_HEADER
from grimoire.store import StaleWrite
from grimoire.textapi import ApiRefused, field


@pytest.fixture
def device(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        yield opened, str(chat)


def _page(folder: Path, page_id: str, **changes) -> Path:
    manifest = {"contract": CONTRACT, "id": page_id, "route": page_id, "title": page_id.title(), "slot": "play",
                "order": 1, "owner": "test", "entry": "page.js"}
    manifest.update(changes)
    manifest = {key: value for key, value in manifest.items() if value is not None}
    target = folder / "pages" / page_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "page.json").write_text(json.dumps(manifest), encoding="utf-8")
    (target / str(manifest.get("entry", "page.js"))).write_text("export function mount() {}\n", encoding="utf-8")
    return target


def _static(tmp_path: Path) -> Path:
    """A static folder of the shell's own files and no pages, for a test to add its own."""
    static = tmp_path / "static"
    shutil.copytree(STATIC / "shell", static / "shell")
    shutil.copy(STATIC / "index.html", static / "index.html")
    return static


class Served:
    def __init__(self, api, **options) -> None:
        self.app = AppServer(api, **options)
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.app.address[1]

    def get(self, path: str, *, host: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("GET", path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def post(self, op: str, args: dict, *, token: str | None = ""):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if token is not None:
            headers[TOKEN_HEADER] = self.app.token if token == "" else token
        connection.request("POST", f"/api/{op}", body=json.dumps(args).encode(), headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def close(self) -> None:
        self.app.close()
        self.thread.join(10)


@pytest.fixture
def served(device):
    opened, chat = device
    server = Served(opened.api)
    try:
        yield server, opened, chat
    finally:
        server.close()


# -- the page registry ---------------------------------------------------------------------------------

def test_the_registry_lists_each_page_with_its_module_and_what_it_lacks(device, tmp_path):
    opened, _chat = device
    static = _static(tmp_path)
    _page(static, "alpha", slot="library", order=5, requires=["chats", "probe.list", "probe.gone"])
    _page(static, "beta", slot="play", order=9, unavailable="Waits on 046 milestone 2.")
    _page(static, "gamma", slot="play", order=2)
    probe = Adapter("probe", owner="test", ops={"list": lambda ctx, args: {}})
    server = Served(opened.api, static=static, adapters=(probe,))
    try:
        response, data = server.get("/shell/pages")
    finally:
        server.close()
    assert response.status == 200
    registry = json.loads(data)
    assert registry["contract"] == CONTRACT and registry["slots"] == list(SLOTS)
    # slot order first, then each page's own order
    assert [page["id"] for page in registry["pages"]] == ["gamma", "beta", "alpha"]
    alpha = registry["pages"][2]
    assert alpha["module"] == "/static/pages/alpha/page.js"
    assert alpha["missing"] == ["probe.gone"] and alpha["unavailable"] is None
    assert registry["pages"][1]["unavailable"] == "Waits on 046 milestone 2." and registry["pages"][1]["missing"] == []


@pytest.mark.parametrize("change, message", [
    ({"id": "other"}, "name its own folder"),
    ({"route": "Bad Route"}, "route must match"),
    ({"slot": "sidebar"}, "slot must be one of"),
    ({"contract": 2}, "contract must be 1"),
    ({"order": True}, "order must be"),
    ({"entry": "../server.py"}, "entry must be"),
    ({"requires": "chats"}, "requires must be"),
    ({"title": ""}, "title must be"),
    ({"extra": 1}, "unknown keys"),
])
def test_a_bad_manifest_stops_the_app_before_it_listens(device, tmp_path, change, message):
    opened, _chat = device
    static = _static(tmp_path)
    _page(static, "alpha", **change)
    with pytest.raises(PageManifestError, match=message):
        AppServer(opened.api, static=static)


def test_two_pages_on_one_route_a_missing_entry_or_a_bare_folder_are_refused(tmp_path):
    static = _static(tmp_path)
    _page(static, "alpha", route="shared")
    _page(static, "beta", route="shared")
    with pytest.raises(PageManifestError, match="already pages/alpha's"):
        load_pages(static)
    shutil.rmtree(static / "pages")
    (_page(static, "alpha") / "page.js").unlink()
    with pytest.raises(PageManifestError, match="entry page.js is missing"):
        load_pages(static)
    shutil.rmtree(static / "pages")
    (static / "pages" / "stray").mkdir(parents=True)
    with pytest.raises(PageManifestError, match="has no page.json"):
        load_pages(static)


def test_the_shipped_pages_load_and_every_op_they_need_is_served(served):
    server, _opened, _chat = served
    response, data = server.get("/shell/pages")
    pages = json.loads(data)["pages"]
    assert pages and all(page["missing"] == [] for page in pages)
    for page in pages:
        response, _ = server.get(page["module"])
        assert response.status == 200


# -- static files ---------------------------------------------------------------------------------------

def test_static_modules_are_served_as_code_without_the_token(served):
    server, _opened, _chat = served
    response, data = server.get("/static/shell/shell.js")
    assert response.status == 200 and data
    assert response.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("Content-Security-Policy") == "default-src 'none'; sandbox"
    response, _ = server.get("/static/shell/shell.css")
    assert response.status == 200 and response.getheader("Content-Type") == "text/css; charset=utf-8"
    # the Host check still holds: no DNS-rebound page can read the code either
    response, _ = server.get("/static/shell/shell.js", host="evil.example")
    assert response.status == 403


@pytest.mark.parametrize("path", [
    "/static/../server.py", "/static/%2e%2e/server.py", "/static/shell/../../server.py", "/static/shell%2f..%2f..%2fserver.py",
    "/static/pages/chats/page.json", "/static/index.html", "/static/shell/", "/static/shell", "/static/.x.js",
    "/static/shell/shell.js?v=1", "/static//shell/shell.js", "/static/shell\\shell.js", "/static/nothing.js",
])
def test_static_serves_nothing_but_its_own_modules(served, path):
    server, _opened, _chat = served
    response, _ = server.get(path)
    assert response.status == 404


def test_a_link_out_of_the_static_folder_is_not_followed(device, tmp_path):
    opened, _chat = device
    static = _static(tmp_path)
    secret = tmp_path / "secret.js"
    secret.write_text("secret", encoding="utf-8")
    try:
        os.symlink(secret, static / "shell" / "leak.js")
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot make the link")
    server = Served(opened.api, static=static)
    try:
        response, data = server.get("/static/shell/leak.js")
    finally:
        server.close()
    assert response.status == 404 and b"secret" not in data


# -- adapters -------------------------------------------------------------------------------------------

def _probe(seen: list):
    def echo(ctx, args):
        seen.append(ctx.owner)
        return {"said": field(args, "text", str), "owner": ctx.owner}

    def stale(ctx, args):
        raise StaleWrite([("sources/probe.json", "changed")])

    def opened(ctx, args):
        return {"turns": len(ctx.core("open", {"chat": field(args, "chat", str)})["turns"])}

    def refuse(ctx, args):
        raise ApiRefused("probe_refused", "the probe says no")

    def foreign(ctx, args):
        raise _NarrowCardRefusal("the card has no name")

    def broken(ctx, args):
        raise _OtherServiceError("a bug, not a refusal")

    def not_json(ctx, args):
        return {"when": object()}

    return Adapter("probe", owner="test", refusals={_CardRefusal: "card_refused"},
                   ops={"echo": echo, "stale": stale, "open": opened, "refuse": refuse, "foreign": foreign,
                        "broken": broken, "not_json": not_json})


class _CardRefusal(Exception):
    """Stands in for another service's refusal the text API does not know (109's card errors, say)."""


class _NarrowCardRefusal(_CardRefusal):
    pass


class _OtherServiceError(Exception):
    pass


def test_an_adapter_op_runs_over_the_open_library_as_its_owner(device):
    opened, chat = device
    seen: list = []
    app = AppApi(opened.api, (_probe(seen),))
    assert app.call("probe.echo", {"text": "hi"}) == {"said": "hi", "owner": opened.api.owner}
    # a core op inside an adapter op runs in the same serialized call
    assert app.call("probe.open", {"chat": chat}) == {"turns": len(opened("open", chat=chat)["turns"])}
    assert "probe.echo" in app.call("library", {})["ops"] and "open" in app.ops


def test_an_adapter_refuses_as_a_core_op_would(device):
    opened, _chat = device
    app = AppApi(opened.api, (_probe([]),))
    with pytest.raises(ApiRefused) as refused:
        app.call("probe.stale", {})
    assert refused.value.reason == "changed" and refused.value.status == 409
    with pytest.raises(ApiRefused) as refused:
        app.call("probe.echo", {"text": 3})
    assert refused.value.reason == "invalid_field" and refused.value.status == 400
    with pytest.raises(ApiRefused) as refused:
        app.call("probe.missing", {})
    assert refused.value.reason == "unknown_op" and refused.value.status == 404
    with pytest.raises(ApiRefused) as refused:
        app.call("probe.echo", ["not", "an", "object"])  # type: ignore[arg-type]
    assert refused.value.reason == "invalid_field"


def test_an_adapters_own_refusals_are_answered_with_its_reason_and_anything_else_is_a_bug(device):
    opened, _chat = device
    app = AppApi(opened.api, (_probe([]),))
    with pytest.raises(ApiRefused) as refused:
        app.call("probe.foreign", {})  # a subclass of a declared refusal matches it
    assert (refused.value.reason, refused.value.status, str(refused.value)) == ("card_refused", 409, "the card has no name")
    with pytest.raises(_OtherServiceError):
        app.call("probe.broken", {})
    with pytest.raises(TypeError, match="not JSON"):
        app.call("probe.not_json", {})


def test_a_bug_in_an_adapter_is_answered_500_over_http_and_the_app_goes_on(device):
    opened, _chat = device
    server = Served(opened.api, adapters=(_probe([]),))
    try:
        assert server.post("probe.not_json", {}) == (500, {"error": "internal", "message": "the app failed; see its console"})
        assert server.post("probe.broken", {})[0] == 500
        assert server.post("probe.foreign", {}) == (409, {"error": "card_refused", "message": "the card has no name"})
        assert server.post("probe.echo", {"text": "still here"})[0] == 200
    finally:
        server.close()


@pytest.mark.parametrize("refusals", [{ValueError: "Bad Reason"}, {"ValueError": "fine"}, {int: "fine"}])
def test_an_adapters_refusals_are_exception_classes_with_reasons(refusals):
    with pytest.raises(ValueError):
        Adapter("ok", owner="test", refusals=refusals)


def test_adapter_ops_need_the_token_over_http(device):
    opened, _chat = device
    server = Served(opened.api, adapters=(_probe([]),))
    try:
        assert server.post("probe.echo", {"text": "hi"}) == (200, {"said": "hi", "owner": opened.api.owner})
        assert server.post("probe.echo", {"text": "hi"}, token=None)[0] == 401
        assert server.post("probe.refuse", {}) == (409, {"error": "probe_refused", "message": "the probe says no"})
    finally:
        server.close()


@pytest.mark.parametrize("name, ops", [("Bad", {}), ("ok", {"Bad-Op": lambda ctx, args: {}}), ("ok", {"op": "nope"})])
def test_an_adapter_must_be_well_named(name, ops):
    with pytest.raises(ValueError):
        Adapter(name, owner="test", ops=ops)


def test_two_adapters_may_not_share_a_name(device):
    opened, _chat = device
    with pytest.raises(ValueError, match="two adapters"):
        AppApi(opened.api, (Adapter("same", owner="a"), Adapter("same", owner="b")))
