"""Release contracts in an actual browser, not an HTTP-only stand-in."""
import json
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect

from journeys import (setup, configure_model, create_source, create_character, start_chat, post_keep,
                      navigate, composer, import_card)
from test_app_release_proof import speakers, voiced_apart, V3, SERAPHINE_CARD


def cast(session, imported=True):
    setup(session)
    configure_model(session)
    assert session.model.requests == []
    create_source(session.page)
    if imported:
        import_card(session, V3)
    else:
        create_character(session.page, "Mara", "A tide-reader who keeps the drowned ledger")
    create_character(session.page, "Winifred", "A courier")
    return start_chat(session, npc="Mara Vell" if imported else "Mara")


def chat_ref(url):
    return unquote(urlsplit(url).fragment.removeprefix("/chat/"))


def test_fresh_worldless_two_voices(session):
    url = cast(session)
    chat = chat_ref(url)
    page = session.page
    assert session.app("open", chat=chat)["scope"]["world"] is None
    post_keep(session, '"The word on the ledger is BRINE."')
    assert speakers(session.model.requests) == ["Mara Vell"]
    import_card(session, SERAPHINE_CARD)
    page.goto(url)
    page.get_by_role("button", name="Add a character", exact=True).click()
    expect(page.get_by_role("button", name="Post", exact=True)).to_be_enabled()
    page.get_by_label("Card to add").select_option(label="Seraphine Lark")
    page.get_by_role("button", name="Add", exact=True).click()
    expect(page.get_by_role("button", name="Ask Seraphine Lark to speak")).to_be_visible()
    post_keep(session, '"Who else is here tonight?"')
    calls = session.model.requests[1:]
    assert sorted(speakers(calls)) == ["Mara Vell", "Seraphine Lark"]
    voiced_apart(calls)
    newcomer = calls[speakers(calls).index("Seraphine Lark")]
    assert "BRINE" not in json.dumps(newcomer)
    assert "Who else is here tonight?" in json.dumps(newcomer)
    transcript = page.get_by_role("region", name="Transcript")
    expect(transcript).to_contain_text("Keep your voice down; the harbor master listens.")
    expect(transcript).to_contain_text("I light the lamps. Who are you to ask?")
    usage = session.app("providers.usage", chat=chat)
    assert usage["total"]["attempts"] == len(session.model.requests) == 3
    assert all(attempt["measurement"] == "provider_reported"
               for turn in usage["turns"] for call in turn["calls"] for attempt in call["attempts"])
    navigate(page, "providers")
    page.get_by_label("Most tokens per reply").fill("512")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved.", exact=False)).to_be_visible()
    session.restart()
    navigate(page, "chat/" + urlsplit(url).fragment.removeprefix("/chat/"))
    expect(page.get_by_role("region", name="Transcript")).to_contain_text("Who else is here tonight?")
    assert session.app("providers.models")["settings"]["max_output_tokens"] == 512
    assert len(session.model.requests) == 3


def test_rejected_post_preserves_draft_and_retry_is_reviewed(session):
    url = cast(session)
    page = session.page
    text = "The private word is BRINE."
    composer(page).fill(text)
    page.get_by_role("button", name="Post", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("put the words in quotes")
    expect(composer(page)).to_have_value(text)
    assert session.model.requests == []
    assert not any(turn.get("post") for turn in session.app("open", chat=chat_ref(url))["turns"])
    composer(page).fill('"The word is BRINE."')
    page.get_by_role("button", name="Post", exact=True).click()
    expect(page.get_by_role("button", name="Keep this take", exact=True)).to_be_visible()
    assert len(session.model.requests) == 1
    with page.expect_response(lambda response: response.url.endswith("/api/generate")) as generated:
        page.get_by_role("button", name="Try again", exact=True).click()
    assert generated.value.status == 200, generated.value.json()
    expect(page.get_by_role("button", name="Keep this take", exact=True)).to_be_enabled()
    assert len(session.model.requests) == 2
    # No candidate is silently kept merely by retrying.
    assert not session.app("open", chat=chat_ref(url))["turns"][-1].get("kept")
    with page.expect_response(lambda response: response.url.endswith("/api/keep")) as kept:
        page.get_by_role("button", name="Keep this take", exact=True).click()
    assert kept.value.status == 200
    expect(page.get_by_role("button", name="Post", exact=True)).to_be_enabled()
    composer(page).fill("Unsent draft survives back navigation")
    page.get_by_role("link", name="Chats", exact=True).first.click()
    page.go_back()
    expect(composer(page)).to_have_value("Unsent draft survives back navigation")
    page.reload()
    expect(composer(page)).to_have_value("Unsent draft survives back navigation")
    assert len(session.model.requests) == 2


def test_manually_created_character_can_reply(session):
    cast(session, imported=False)
    post_keep(session, '"Hello Mara."')
    assert speakers(session.model.requests) == ["Mara"]


def test_canonical_chat_indexes_and_draft(session):
    url = cast(session)
    page = session.page
    navigate(page, "sources?source=local%3Aacceptance")
    page.get_by_role("button", name="New entry here").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Kind", exact=True).select_option("scenario")
    dialog.get_by_label("Title", exact=True).fill("Saltmarch rain")
    dialog.get_by_label("Premise", exact=True).fill("Rain keeps everyone indoors.")
    dialog.get_by_role("button", name="Create", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role("heading", name="Saltmarch rain", level=1)).to_be_visible()
    page.goto(url)
    expect(page.get_by_role("textbox", name="Playing Winifred", exact=True)).to_be_visible()
    page.get_by_role("button", name="Add a character", exact=True).click()
    expect(page.get_by_role("button", name="Post", exact=True)).to_be_enabled()
    page.get_by_label("Card to add").select_option(label="Scenario: Saltmarch rain")
    page.get_by_role("button", name="Add", exact=True).click()
    expect(page.get_by_label("Card to add")).not_to_be_visible()
    composer(page).fill("Draft belongs to one canonical chat")
    for filter_name, label in (("all", None), ("character", "Mara Vell"), ("scenario", "Saltmarch rain")):
        navigate(page, "chats?filter=" + filter_name)
        if label:
            page.get_by_label(filter_name.title(), exact=True).select_option(label=label)
        links = page.get_by_role("main").locator('a[href="' + ("#" + urlsplit(url).fragment) + '"]')
        expect(links.first).to_be_visible()
        links.first.click()
        expect(composer(page)).to_have_value("Draft belongs to one canonical chat")
        assert chat_ref(page.url) == chat_ref(url)
    assert len(session.app("chats")["chats"]) == 1
    assert session.model.requests == []
