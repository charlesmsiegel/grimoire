"""112 through Chromium and the real CLI app, with a synthetic loopback model.

Install Playwright and Chromium to run this optional browser acceptance test.
No product API or runner is replaced; fixtures are created before launching the app.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

from provider_servers import base_url, sse_lines, sse_server, stream
from chat_fixtures import Clock
from scene_fixtures import MARA_VELL, Tagged, attach_tamsin, card_library
from store_helpers import OWNER
from grimoire.app.libraries import open_authority, open_store
from grimoire.chatstore import ChatLibrary

playwright = pytest.importorskip("playwright.sync_api")
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="Chromium is not installed")
SRC = Path(__file__).resolve().parent.parent / "src"


@contextlib.contextmanager
def running_app(folder, settings):
    """Use exactly the CLI construction path, with bounded startup and shutdown."""
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]))
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire.app", "serve", str(folder), "--owner", OWNER,
         "--port", "0", "--model", str(settings)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
    )
    ready = queue.Queue()
    threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
    try:
        line = ready.get(timeout=30)
        assert line.startswith("open http://127.0.0.1:"), line
        yield line.removeprefix("open ").strip()
    finally:
        process.terminate()
        try:
            process.wait(15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(15)
        diagnostics = process.stderr.read()
        if diagnostics:
            print(diagnostics)
        process.stdout.close()
        process.stderr.close()


def model_reply(handler):
    request = json.loads(handler.server.requests[-1][2])
    decision = "Answer with the number of one choice" in request["messages"][-1]["content"]
    reply = "2" if decision else "The tide keeps its own ledger."
    stream(sse_lines(deltas=(reply,), usage={"prompt_tokens": 120, "completion_tokens": 9}))(handler)


def decision_requests(server):
    return [body for _, _, body in server.requests if
            "Answer with the number of one choice" in json.loads(body)["messages"][-1]["content"]]


def navigate(page, route):
    page.evaluate("route => { location.hash = route; }", "#/" + route)
    if route.startswith("chat/"):
        playwright.expect(page.get_by_role("list", name="In this scene", exact=True)).to_be_visible()


def save_models(page):
    with page.expect_response("**/api/providers.save_models") as response:
        page.get_by_role("button", name="Save", exact=True).focus()
        page.keyboard.press("Enter")
    assert response.value.ok, response.value.text()


def select_npc(page, actor):
    with page.expect_response(lambda r: r.url.endswith("/api/providers.context") and
                              r.request.post_data_json.get("actor") == str(actor)) as response:
        page.get_by_label("NPC context", exact=True).select_option(str(actor))
    assert response.value.ok, response.value.text()


def test_owner_configures_inspects_and_plays_optional_decisions_in_chromium(tmp_path):
    folder, chat_ref, _ = card_library(tmp_path)
    with open_store(folder) as store:
        authority = open_authority(store, OWNER)
        try:
            handle = authority.handle_for(OWNER, trusted=True)
            attach_tamsin(store, handle, chat_ref)
            record, _ = ChatLibrary(store, handle, owner=OWNER, clock=Clock(), new_id=Tagged("browser")).read_chat(chat_ref)
            actor = next(binding.actor for binding in record.active_bindings("character")
                         if binding.ref.unpinned() == MARA_VELL)
        finally:
            authority.close()
    chat = str(chat_ref)
    settings = tmp_path / "config" / "model.json"
    with sse_server(model_reply) as model, playwright.sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(executable_path=CHROMIUM, headless=True,
                                              args=["--no-sandbox"],
                                              env={**os.environ,
                                                   "XDG_CONFIG_HOME": str(tmp_path / "browser-config"),
                                                   "XDG_CACHE_HOME": str(tmp_path / "browser-cache")})
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(15_000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            with running_app(folder, settings) as url:
                page.goto(url)
                navigate(page, "chat/" + quote(chat, safe=""))
                with page.expect_response("**/api/continue") as response:
                    page.get_by_role("button", name="Take your seat", exact=True).click()
                assert response.value.ok, response.value.text()
                navigate(page, "providers")
                playwright.expect(page.get_by_text("No model is set up", exact=True)).to_be_visible()
                page.get_by_label("Chat", exact=True).select_option(chat)
                select_npc(page, actor)
                inspection = page.get_by_role("region", name="Current context", exact=True)
                playwright.expect(inspection.get_by_text("No model configured; inspection still works.", exact=True)).to_be_visible()
                playwright.expect(inspection.locator("pre").first).not_to_be_empty()
                assert "Every crate has a story, and a stamp." not in inspection.inner_text()
                inspection.get_by_text("Selected context and provenance", exact=True).click()
                playwright.expect(inspection).to_contain_text("card:mara-vell")
                # A failed refresh must erase the previously displayed private context, then recover.
                page.route("**/api/providers.context", lambda route: route.abort())
                inspection.get_by_role("button", name="Refresh context", exact=True).click()
                playwright.expect(inspection).to_contain_text("Current context could not be read")
                playwright.expect(inspection.locator("pre")).to_have_count(0)
                playwright.expect(inspection.get_by_label("Goal", exact=True)).to_have_count(0)
                page.unroute("**/api/providers.context")
                inspection.get_by_role("button", name="Refresh context", exact=True).focus()
                page.keyboard.press("Enter")
                playwright.expect(inspection.locator("pre").first).not_to_be_empty()
                assert not model.requests
                page.get_by_label("Endpoint address", exact=True).fill(base_url(model))
                page.get_by_role("textbox", name="Model", exact=True).fill("synthetic-model")
                page.get_by_label("Optional decision calls per turn", exact=True).fill("1")
                save_models(page)
                page.get_by_label("Chat", exact=True).select_option(chat)
                select_npc(page, actor)
                page.get_by_label("Goal", exact=True).fill("Keep the quay safe")
                page.get_by_label("Candidate actions", exact=True).fill("Watch the water\nCheck the ledger")
                with page.expect_response("**/api/providers.save_agenda") as response:
                    page.get_by_role("button", name="Save tactics", exact=True).click()
                assert response.value.ok, response.value.text()
                assert not model.requests  # editing and current inspection never issue inference

                navigate(page, "chat/" + quote(chat, safe=""))
                with page.expect_response("**/api/generate") as response:
                    page.get_by_role("button", name="Ask for a reply", exact=True).click()
                assert response.value.ok, response.value.text()
                assert len(model.requests) == 3  # one optional choice and two independent NPC replies
                assert len(decision_requests(model)) == 1
                with page.expect_response("**/api/generate") as response:
                    page.get_by_role("button", name="Try again", exact=True).click()
                assert response.value.ok, response.value.text()
                assert len(model.requests) == 5  # a reroll writes again but never decides again
                assert len(decision_requests(model)) == 1
                page.get_by_role("button", name="Keep this take", exact=False).click()

                navigate(page, "providers")
                page.get_by_label("Optional decision calls per turn", exact=True).fill("0")
                save_models(page)
                page.get_by_label("Chat", exact=True).select_option(chat)
                playwright.expect(page.get_by_text("NPC decisions", exact=True).first).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                navigate(page, "chat/" + quote(chat, safe=""))
                with page.expect_response("**/api/generate") as response:
                    page.get_by_role("button", name="Ask for a reply", exact=True).click()
                assert response.value.ok, response.value.text()
                assert len(model.requests) == 7
                assert len(decision_requests(model)) == 1

            with running_app(folder, settings) as url:
                page.goto(url)
                navigate(page, "providers")
                playwright.expect(page.get_by_label("Optional decision calls per turn", exact=True)).to_have_value("0")
                page.get_by_label("Chat", exact=True).select_option(chat)
                select_npc(page, actor)
                playwright.expect(page.get_by_label("Goal", exact=True)).to_have_value("Keep the quay safe")
                assert len(model.requests) == 7
            assert errors == []
        finally:
            browser.close()
