"""Visible-control journeys shared by the acceptance tests; no API writes."""
import json
import re
from urllib.parse import urlsplit

from playwright.sync_api import expect


def press(control):
    """Tap on a touch screen (`hover: none`), click otherwise.

    A phone journey that clicked would pass on mouse events alone; `tap` sends touch events, so a control that
    only answers a mouse fails there (docs/ACCEPTANCE.md).
    """
    if control.page.evaluate("matchMedia('(hover: none)').matches"):
        control.tap()
    else:
        control.click()


def navigate(page, route):
    page.goto(page.url.split("#", 1)[0] + "#/" + route)


def setup(session):
    page = session.page
    expect(page.get_by_label("Folder", exact=True)).to_be_visible()
    page.get_by_label("This device's name").fill(session.home.name)
    press(page.get_by_role("button", name="Check", exact=True))
    press(page.get_by_role("button", name="Make the library here"))
    expect(page.get_by_role("heading", name="Who do you want to meet first?")).to_be_visible()
    assert session.app("setup.status")["open"]


def configure_model(session):
    page = session.page
    navigate(page, "providers")
    page.get_by_label("Provider name", exact=True).fill("local")
    page.get_by_role("textbox", name="Model", exact=True).fill("synthetic-model")
    page.get_by_label("Endpoint address", exact=True).fill(f"http://127.0.0.1:{session.model.server_address[1]}/v1")
    page.get_by_label("Key variable", exact=True).fill("")
    press(page.get_by_role("button", name="Check", exact=True))
    expect(page.get_by_text("Usable. Nothing was sent to the provider.")).to_be_visible()
    press(page.get_by_role("button", name="Save", exact=True))
    expect(page.get_by_text(re.compile("Saved"))).to_be_visible()
    # Shell refreshes model readiness when mounted again.
    page.reload()


def create_source(page):
    navigate(page, "sources")
    press(page.get_by_role("button", name="New source", exact=True))
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Source", exact=True).fill("local:acceptance")
    dialog.get_by_label("Title (optional)").fill("Synthetic cast")
    press(dialog.get_by_role("button", name="Create source"))
    expect(dialog).not_to_be_visible()


def create_character(page, name, description=""):
    navigate(page, "sources?source=local%3Aacceptance")
    press(page.get_by_role("button", name="New entry here"))
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name", exact=True).fill(name)
    dialog.get_by_label("Description", exact=True).fill(description)
    press(dialog.get_by_role("button", name="Create", exact=True))
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role("heading", name=name, exact=True, level=1)).to_be_visible()


def start_chat(session, npc="Mara", persona="Winifred"):
    page = session.page
    navigate(page, "start")
    press(page.get_by_role("group", name="Character", exact=True).get_by_role("button", name=re.compile(re.escape(npc))))
    press(page.get_by_role("group", name="You play", exact=True).get_by_role("button", name=persona, exact=True))
    press(page.get_by_role("button", name="Start chat", exact=True))
    expect(page.get_by_role("textbox", name="Playing " + persona, exact=True)).to_be_visible()
    return page.url


def composer(page):
    return page.locator(".chat-composer textarea")


def post_keep(session, text):
    page = session.page
    composer(page).fill(text)
    press(page.get_by_role("button", name="Post", exact=True))
    expect(page.get_by_role("button", name="Keep this take", exact=True)).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith("/api/keep")) as kept:
        press(page.get_by_role("button", name="Keep this take", exact=True))
    assert kept.value.status == 200, kept.value.json()
    expect(page.get_by_role("button", name="Keep this take", exact=True)).not_to_be_visible()
    expect(page.get_by_role("button", name="Post", exact=True)).to_be_enabled()


def import_card(session, card, portrait=None):
    """Preview then authorize a synthetic card through the real file controls."""
    page = session.page
    navigate(page, "import?tab=card")
    page.locator('input[type="file"]').first.set_input_files(
        {"name": "synthetic-card.json", "mimeType": "application/json", "buffer": json.dumps(card).encode()})
    expect(page.get_by_role("button", name="Preview import", exact=True)).to_be_visible()
    if portrait is not None:
        page.get_by_label("Portrait file", exact=True).set_input_files(
            {"name": "synthetic.png", "mimeType": "image/png", "buffer": portrait})
    press(page.get_by_role("button", name="Preview import", exact=True))
    press(page.get_by_role("region", name="Proposed write", exact=True).get_by_role(
        "button", name=re.compile(r"^Approve and write ")))
    press(page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Write \d+ files?$" )))
    expect(page.get_by_text("Written to your library", exact=True)).to_be_visible()
