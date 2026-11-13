"""Real browser migration and paired-client contracts; HTTP reads only assert outcomes.

These run against synthetic roots and the actual shipped UI. A missing control is
an acceptance failure, never an expected failure or a substitute API mutation.
"""

import re
from urllib.parse import quote, unquote, urlsplit

from playwright.sync_api import expect

from card_fixtures import PORTRAIT_2, card
from grimoire.testing.fileops import snapshot
from test_app_release_proof import same_chat, speakers, voiced_apart
from journeys import (
    configure_model,
    create_character,
    create_source,
    navigate,
    import_card,
    post_keep,
    setup,
    start_chat,
)


def chat_ref(url):
    return unquote(urlsplit(url).fragment.removeprefix("/chat/").split("?", 1)[0])


def open_chat(page, ref):
    """Follow the canonical chat's actual list link, not a fabricated record."""
    navigate(page, "chats")
    link = page.locator('a[href="#/chat/' + quote(ref, safe="") + '"]').first
    expect(link).to_be_visible()
    link.click()
    expect(page.get_by_role("region", name="Transcript", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name=re.compile(r"^Playing "))).to_be_visible()


def take_seat(page):
    button = page.get_by_role("button", name="Take your seat", exact=True)
    expect(button).to_be_visible()
    button.click()
    expect(button).to_have_count(0)


def test_migrated_world_review_play_and_process_resume(session):
    before = snapshot(session.v1)
    page = session.page
    try:
        setup(session)
        configure_model(session)
        navigate(page, "import?tab=v1")
        page.get_by_label("Grimoire 0.2.1 library folder", exact=True).fill(str(session.v1))
        page.get_by_label("Archive name", exact=True).fill("fixture")
        page.get_by_role("button", name="Scan library", exact=True).click()
        expect(page.get_by_role("heading", name="What the V1 library holds")).to_be_visible()
        page.get_by_text(re.compile(r"^Findings \(\d+\)$")).click()
        expect(page.get_by_text(re.compile("dangling_object")).first).to_be_visible()
        assert session.app("chats")["chats"] == []
        page.get_by_role("button", name="Preview import", exact=True).click()
        preview = page.get_by_role("region", name="Proposed write", exact=True)
        expect(preview).to_contain_text("Not imported")
        expect(preview).to_contain_text("Unresolved")
        assert session.app("chats")["chats"] == []
        assert session.model.requests == []
        preview.get_by_role("button", name=re.compile(r"^Approve and write ")).click()
        page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Write \d+ files?$" )).click()
        expect(page.get_by_text("Written to your library", exact=True)).to_be_visible()
        assert session.model.requests == []
        migrated = next(row["chat"] for row in session.app("chats")["chats"]
                        if row["scope"]["campaign"] == "the-drowned-ledger")
        open_chat(page, migrated)
        take_seat(page)
        transcript = page.get_by_role("region", name="Transcript", exact=True)
        expect(transcript).to_contain_text("Mara")
        expect(transcript).to_contain_text("Seraphine")
        heard = len(session.model.requests)
        post_keep(session, "What happened to the ledger?")
        calls = session.model.requests[heard:]
        assert sorted(speakers(calls)) == ["Mara", "Seraphine"]
        voiced_apart(calls)
        saved = session.app("open", chat=migrated)
        assert saved["scope"]["world"] is not None
        # Closing the tab/process and reopening must retain the same migrated chat.
        session.restart()
        page = session.page
        open_chat(page, migrated)
        expect(page.get_by_role("region", name="Transcript", exact=True)).to_contain_text("What happened to the ledger?")
        assert same_chat(session.app("open", chat=migrated), saved)
        # Inspect retained art loss, rather than treating a placeholder as imported art.
        missing = session.app("media.art", subject="world:realm/character/mara.default")
        assert any(slot["state"] == "unavailable" and slot["reason"] == "unresolved" for slot in missing["slots"])
        navigate(page, "source/" + quote("world:realm/character/mara.default", safe="") + "?tab=art")
        expect(page.get_by_text(re.compile("unresolved|unavailable|missing", re.I)).first).to_be_visible()
    finally:
        assert snapshot(session.v1) == before, "V1 migration must leave its source bytes unchanged"


def pair(page, peer, *, key="", address=""):
    navigate(page, "sync")
    page.get_by_role("button", name="Pair a device", exact=True).click()
    page.get_by_label("Other device's name", exact=True).fill(peer)
    page.get_by_label("Pairing key (optional)", exact=True).fill(key)
    page.get_by_label("Its address, if this device dials it (optional)", exact=True).fill(address)
    page.get_by_role("button", name="Pair", exact=True).click()
    expect(page.get_by_text("Paired with " + peer + ".", exact=True)).to_be_visible()
    if not key:
        card = page.get_by_role("region", name="Pairing key", exact=True)
        match = re.search(r"\b[0-9a-f]{64}\b", card.inner_text())
        assert match, "pairing must expose the new key for the other client"
        return match.group()
    return key


def sync(page, peer):
    navigate(page, "sync")
    page.get_by_role("region", name=peer, exact=True).get_by_role("button", name="Sync now", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="Synced with " + peer + " just now.")).to_be_visible()


def test_image_sync_and_offline_fork_preserves_both_candidates(session):
    setup(session)
    configure_model(session)
    create_source(session.page)
    import_card(session, card(name="Mara", description="A tide-reader who keeps the drowned ledger", first_mes="The tide turns."))
    create_character(session.page, "Winifred", "A traveler carrying a lamp")
    url = start_chat(session)
    expect(session.page.get_by_role("textbox", name="Playing Winifred", exact=True)).to_be_visible()
    chat = chat_ref(url)
    post_keep(session, "The tide is turning.")
    with session.new_client("phone") as phone:
        setup(phone)
        configure_model(phone)
        phone.restart()
        key = pair(session.page, "phone", address=f"127.0.0.1:{phone.app.sync_port}")
        pair(phone.page, "desktop", key=key)
        open_chat(session.page, chat)
        # Upload through the actual visible cast action's file chooser.
        with session.page.expect_file_chooser() as choosing:
            session.page.get_by_role("button", name="Add picture for Mara", exact=True).click()
        choosing.value.set_files({"name": "synthetic.png", "mimeType": "image/png", "buffer": PORTRAIT_2})
        expect(session.page.get_by_role("button", name="Change picture for Mara", exact=True)).to_be_visible()
        here = session.app("open", chat=chat)
        actor = next(row for row in here["cast"] if row["name"] == "Mara")
        slot = next(row for row in actor["art"]["slots"] if row["slot"] == "avatar")
        assert session.app.picture(slot["image"]) == PORTRAIT_2
        heard = len(session.model.requests)
        sync(session.page, "phone")
        assert len(session.model.requests) == heard, "sync must not invoke inference"
        open_chat(phone.page, chat)
        take_seat(phone.page)
        there = phone.app("open", chat=chat)
        twin = next(row for row in there["cast"] if row["name"] == "Mara")
        received = next(row for row in twin["art"]["slots"] if row["slot"] == "avatar")
        assert phone.app.picture(received["image"]) == PORTRAIT_2
        expect(phone.page.get_by_role("button", name="Change picture for Mara", exact=True)).to_be_visible()
        # Independently play without syncing, then choose one branch visibly.
        open_chat(session.page, chat)
        post_keep(session, "I keep the lamp here.")
        mine = session.app("open", chat=chat)["turns"][-1]["turn"]
        post_keep(phone, "I carry the lamp away.")
        theirs = phone.app("open", chat=chat)["turns"][-1]["turn"]
        sync(session.page, "phone")
        open_chat(session.page, chat)
        expect(session.page.get_by_text("This chat forked: two devices played on. Choose the branch that goes on.", exact=True)).to_be_visible()
        fork = session.app("open", chat=chat)["fork"]
        assert {mine, theirs} <= {branch["head"] for branch in fork["branches"]}
        ours = next(branch for branch in fork["branches"] if branch["head"] == mine)
        branch_button = session.page.get_by_role(
            "button", name=f'Keep the branch from {ours["device"]} ({len(ours["turns"])} turns)', exact=True)
        expect(branch_button).to_be_visible()
        branch_button.click()
        expect(session.page.get_by_text("1 turn(s) set aside by a fork choice; nobody heard them.", exact=True)).to_be_visible()
        chosen = session.app("open", chat=chat)
        assert chosen["fork"] is None
        assert theirs in {turn["turn"] for turn in chosen["set_aside"]}
        sync(session.page, "phone")
        assert same_chat(phone.app("open", chat=chat), session.app("open", chat=chat))
