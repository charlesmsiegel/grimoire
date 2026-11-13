"""113 production CLI + real Chromium, using only synthetic library/provider inputs.

Run with Playwright installed and CHROMIUM (or chromium on PATH). The test launches
`python -m grimoire.app serve`, never substitutes a CampaignApi or page context.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
from urllib.parse import quote, urlsplit

import pytest
from provider_servers import base_url, sse_lines, sse_server, stream
from scene_fixtures import Table, attach_tamsin, card_library
from store_helpers import OWNER
from test_campaignapi import BRIBE, SECRET, hold

from grimoire.campaignapi import CampaignApi
from grimoire.core import Event, Moment, Perception

ROOT = Path(__file__).resolve().parents[2]
USAGE = {"prompt_tokens": 14, "completion_tokens": 8}
SUMMARY = "Anya pays Mara off."
AGENDA = "Keep the hatch safe."


@contextlib.contextmanager
def cli(folder, config, model):
    """Wait for the real CLI's announced ephemeral address; bound startup failures."""
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire.app", "serve", str(folder), "--port", "0",
         "--campaign-config", str(config), "--model", str(model)],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "backend/src")},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    output = queue.Queue()
    reader = threading.Thread(target=lambda: [output.put(line) for line in process.stdout], daemon=True)
    reader.start()
    lines = []
    try:
        for _ in range(30):
            try:
                line = output.get(timeout=1)
            except queue.Empty:
                if process.poll() is not None:
                    pytest.fail("CLI exited before serving: " + "".join(lines))
                continue
            lines.append(line)
            if line.startswith("open http"):
                yield line.strip().removeprefix("open ")
                break
        else:
            pytest.fail("CLI startup timed out: " + "".join(lines))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(5)


def prepare(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder, runtime=False) as table:
        hub = CampaignApi(table.store, table.authority, owner=OWNER, handle=table.owner)
        tamsin = attach_tamsin(table.store, table.owner, chat)
        hub.runner.seat(chat, OWNER)
        scene = hub.runner.open(chat)
        anya = scene.persona
        mara = next(actor for actor in scene.cast if actor not in (anya, tamsin))
        hub.obstacles.record(hold(mara))
        hub.evidence.append(chat.campaign_id, (
            Event(chat.campaign_id, "e-ledger", "A second ledger exists.", Moment(3), "director"),
            Perception(chat.campaign_id, "ev-ledger", tamsin, "e-ledger", "sight", SECRET,
                       Moment(3), "director", True),
        )).commit()
        hub.runner.post(chat, table.owner, "*I press five coins into Mara's hand*")
    configuration = {
        "version": 1,
        "stages": [{"chat": str(chat), "as_of": 3,
                    "areas": [{"area_id": "hold", "label": "Cargo hold"}],
                    "ways": [{"way_id": "exit", "sides": ["hold", None], "state": "closed"}],
                    "actors": [str(anya), str(mara), str(tamsin)],
                    "placements": [{"who": str(anya), "area": "hold"}, {"who": str(mara), "area": "hold"}],
                    "absences": [{"actor": str(tamsin), "via": "exit"}]}],
        "agendas": [{"actor": str(mara), "goals": [{"goal_id": "safe", "aim": AGENDA, "priority": 1,
                     "approaches": [{"approach_id": "wait", "action": "Wait by the hatch."}]}]}],
    }
    return folder, chat, anya, mara, tamsin, configuration


def test_production_campaign_review_browser_and_restart(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    chromium = os.environ.get("CHROMIUM") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        pytest.skip("Chromium is required for the production browser proof")
    # A protected V1 sentinel is never a fixture source or destination.
    v1 = tmp_path / "v1-protected"
    v1.mkdir()
    sentinel = v1 / "sentinel.txt"
    sentinel.write_text("untouched synthetic V1 sentinel")
    monkeypatch.setenv("GRIMOIRE_HOME", str(v1))
    folder, chat, anya, mara, tamsin, configuration = prepare(tmp_path)
    config = tmp_path / "campaign.json"
    model = tmp_path / "model.json"
    labels = 0

    def respond(handler):
        nonlocal labels
        request = json.loads(handler.server.requests[-1][2])
        if request["model"] == "label-model":
            labels += 1
            # A failed provider request is recorded, remains inspectable, and a new press can retry.
            if labels == 1:
                handler.respond(503, b'{}')
                return
            text = BRIBE
        else:
            text = "Mara rests one hand on the hatch."
        stream(sse_lines(deltas=(text,), usage=USAGE))(handler)

    with sse_server(respond) as provider, playwright.sync_playwright() as pw:
        configuration["labelling"] = {"provider_id": "labels", "base_url": base_url(provider),
                                      "model": "label-model", "attempts": 1, "max_output_tokens": 1200}
        config.write_text(json.dumps(configuration))
        model.write_text(json.dumps({"provider_id": "voice", "base_url": base_url(provider), "model": "voice-model"}))
        browser = pw.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"],
                                    env={**os.environ, "XDG_CACHE_HOME": str(tmp_path / "browser-cache"),
                                         "XDG_CONFIG_HOME": str(tmp_path / "browser-config")})
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            route = "#/campaign/" + quote(chat.campaign_id) + "?chat=" + quote(str(chat), safe="")
            with cli(folder, config, model) as url:
                base = url.split("#", 1)[0]
                token = urlsplit(url).fragment.removeprefix("token=")

                def api(op, **args):
                    response = page.request.post(base + "api/" + op, data=args,
                                                 headers={"X-Grimoire-Token": token})
                    assert response.ok, response.text()
                    return response.json()

                def visit(target=route, at=page):
                    at.goto(url)
                    at.wait_for_url("**/#/home")
                    at.goto(base + target)

                def section(title, at=page):
                    return at.get_by_role("region", name=title, exact=True)

                def label_again(name):
                    page.get_by_role("button", name=name, exact=True).click()
                    dialog = page.get_by_role("dialog")
                    playwright.expect(dialog).to_be_visible()
                    dialog.get_by_role("button", name="Request labels", exact=True).click()

                visit()
                playwright.expect(section("Cast and stage")).to_contain_text("Cargo hold")
                playwright.expect(section("Cast and stage")).to_contain_text("absent (exit)")
                playwright.expect(section("NPC goals")).to_have_count(0)
                assert SECRET not in page.locator("body").inner_text()
                assert AGENDA not in page.locator("body").inner_text()
                assert not provider.requests
                page.get_by_role("button", name="Director view", exact=True).click()
                playwright.expect(section("NPC goals")).to_contain_text(AGENDA)
                assert SECRET not in page.locator("body").inner_text()
                section("Knowledge").get_by_role("button", name="Tamsin Hale", exact=True).click()
                playwright.expect(section("Knowledge")).to_contain_text(SECRET)
                page.get_by_role("button", name="Table view", exact=True).click()
                playwright.expect(section("Knowledge")).not_to_contain_text(SECRET)
                playwright.expect(section("Knowledge").get_by_role("button", name="Tamsin Hale", exact=True)).to_have_count(0)
                page.get_by_label("Against").select_option("hold")
                button = page.get_by_role("button", name="Label this action", exact=True)
                button.focus()
                page.keyboard.press("Enter")
                dialog = page.get_by_role("dialog")
                playwright.expect(dialog).to_contain_text("label-model")
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                assert not provider.requests
                playwright.expect(button).to_be_enabled()
                label_again("Label this action")
                playwright.expect(section("Open action")).to_contain_text("refused")
                assert labels == 1
                page.get_by_label("Against").select_option("hold")
                label_again("Label again")
                playwright.expect(section("Open action")).to_contain_text("proposed")
                assert labels == 2

                # A second browser tab holds the old inbox review while the first relabels.
                stale = context.new_page()
                stale.on("pageerror", lambda error: errors.append(str(error)))
                visit(at=stale)
                stale.get_by_role("link", name="Decide in Needs you", exact=True).click()
                playwright.expect(stale.get_by_role("button", name="Confirm the labelling", exact=True)).to_be_visible()
                page.get_by_label("Against").select_option("hold")
                label_again("Label again")
                playwright.expect(section("Open action")).to_contain_text("Labelled: 3")
                with stale.expect_response(lambda response: response.url.endswith("/api/campaign.confirm")) as refused:
                    stale.get_by_role("button", name="Confirm the labelling", exact=True).click()
                assert refused.value.status == 409
                assert api("campaign.hub", chat=str(chat))["open_action"]["framed"] is None
                # Its refreshed inbox offers the exact replacement, which now can be confirmed.
                stale.get_by_role("button", name="Confirm the labelling", exact=True).click()
                playwright.expect(stale.locator("body")).to_contain_text("Labelling confirmed")
                stale.close()
                page.reload()
                playwright.expect(section("Open action")).to_contain_text("success")
                page.get_by_role("button", name="Director view", exact=True).click()
                page.get_by_label("What happens", exact=True).fill(SUMMARY)
                page.get_by_role("button", name="Propose", exact=True).click()
                playwright.expect(section("Changes waiting on approval")).to_contain_text(SUMMARY)
                assert api("campaign.hub", chat=str(chat), audience="director")["truth"]["events"] == []
                page.get_by_role("button", name="Table view", exact=True).click()
                page.get_by_role("link", name="Decide in Needs you", exact=True).click()
                page.get_by_role("button", name="Endorse as shown", exact=True).click()
                page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
                assert api("campaign.hub", chat=str(chat), audience="director")["truth"]["events"] == []
                page.get_by_role("button", name="Endorse as shown", exact=True).click()
                page.get_by_role("dialog").get_by_role("button", name="Endorse", exact=True).click()
                playwright.expect(page.locator("body")).to_contain_text("Your key is given")
                assert api("campaign.hub", chat=str(chat), audience="director")["truth"]["events"] == []
                page.goto(base + route + "&audience=director")
                page.get_by_role("button", name="Approve and accept", exact=True).click()
                page.get_by_role("dialog").get_by_role("button", name="Approve", exact=True).click()
                playwright.expect(section("World truth")).to_contain_text(SUMMARY)
                accepted = api("campaign.hub", chat=str(chat), audience="director")
                assert len(accepted["truth"]["events"]) == 1
                event_id = accepted["truth"]["events"][0]["event"]
                assert accepted["holdings"][str(anya)]["counts"] == {"debt-chit": 1}
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors
                assert all(SECRET not in body.decode() for _, _, body in provider.requests)
                # Ordinary chat consumes the same stage: only Mara gets a generation call, not absent Tamsin.
                before = len(provider.requests)
                api("generate", chat=str(chat))
                assert len(provider.requests) == before + 1
                generated = json.loads(provider.requests[-1][2])
                assert generated["model"] == "voice-model"
                assert "Wait by the hatch." in json.dumps(generated)
                assert SECRET not in json.dumps(generated)

            with cli(folder, config, model) as reopened:
                page.goto(reopened)
                page.wait_for_url("**/#/home")
                base = reopened.split("#", 1)[0]
                token = urlsplit(reopened).fragment.removeprefix("token=")
                page.goto(base + route + "&audience=director")
                playwright.expect(section("World truth")).to_contain_text(SUMMARY)
                after = api("campaign.hub", chat=str(chat), audience="director")
                assert [event["event"] for event in after["truth"]["events"]] == [event_id]
                assert after["holdings"][str(anya)]["counts"] == {"debt-chit": 1}
                playwright.expect(section("NPC goals")).to_contain_text(AGENDA)
                playwright.expect(section("Cast and stage")).to_contain_text("Cargo hold")
            context.close()
        finally:
            browser.close()
    assert sentinel.read_text() == "untouched synthetic V1 sentinel"
    assert list(v1.iterdir()) == [sentinel]
