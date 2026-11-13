"""Pending source-qualified cast choices survive real metadata refresh/refusal.

Only delivery timing is controlled: the app receives its actual metadata response,
unchanged. No successful write is mocked and the server still authorizes each Add.
"""

from contextlib import contextmanager
from urllib.parse import urlsplit

from playwright.sync_api import expect

from journeys import import_card
from test_app_release_proof import SERAPHINE_CARD
from test_journeys import cast, chat_ref


@contextmanager
def delayed_metadata(page):
    """Hold this app's real metadata responses until the test releases them."""
    origin = urlsplit(page.url)
    endpoint = f"{origin.scheme}://{origin.netloc}/api/chats"
    pending = []
    released = False

    def intercept(route):
        if released:
            route.fallback()
            return
        # This is the exact local app origin, never an external or fabricated reply.
        pending.append(route)

    def release():
        nonlocal released
        assert pending, "the chat's background metadata request must have reached the barrier"
        released = True
        while pending:
            route = pending[0]
            response = route.fetch(timeout=8000)
            assert response.status == 200
            route.fulfill(response=response)
            pending.pop(0)

    page.route(endpoint, intercept)
    try:
        yield release
    finally:
        # A failed assertion must not leave an intercepted request hanging.
        for route in pending:
            route.abort()
        page.unroute(endpoint, intercept)


def reopen_picker(session, url):
    page = session.page
    origin = urlsplit(url)
    with page.expect_request(f"{origin.scheme}://{origin.netloc}/api/chats"):
        page.goto(url)
    expect(page.get_by_role("textbox", name="Playing Winifred", exact=True)).to_be_visible()
    page.get_by_role("button", name="Add a character", exact=True).click()
    pick = page.get_by_label("Card to add", exact=True)
    expect(pick).to_be_visible()
    return pick


def add_response(page):
    origin = urlsplit(page.url)
    endpoint = f"{origin.scheme}://{origin.netloc}/api/play.add_card"
    return page.expect_response(lambda response: response.url == endpoint)


def corrected_add(session, pick, expected_value):
    """One real Add succeeds and its selection cannot be silently reused."""
    page = session.page
    with add_response(page) as added:
        page.get_by_role("button", name="Add", exact=True).click()
    assert added.value.status == 200, added.value.json()
    role, ref = expected_value.split(" ", 1)
    sent = added.value.request.post_data_json
    assert sent["role"] == role and sent["ref"] == ref
    expect(page.get_by_role("button", name="Ask Seraphine Lark to speak", exact=True)).to_be_visible()
    expect(pick).not_to_be_visible()
    page.get_by_role("button", name="Add a character", exact=True).click()
    expect(pick).to_have_value("")
    return added.value.json()


def test_metadata_redraw_preserves_qualified_cast_choice(session):
    url = cast(session)
    import_card(session, SERAPHINE_CARD)
    page = session.page
    requests = []
    endpoint = page.url.split("#", 1)[0] + "api/play.add_card"
    page.on("request", lambda request: requests.append(request.post_data_json) if request.url == endpoint else None)
    with delayed_metadata(page) as release:
        pick = reopen_picker(session, url)
        pick.select_option(label="Seraphine Lark")
        selected = pick.input_value()
        assert selected == "character card:seraphine-lark/character/seraphine-lark"
        previous = pick.element_handle()
        release()
        page.wait_for_function("node => !node.isConnected", arg=previous, timeout=8000)
        expect(pick).to_have_value(selected)
        result = corrected_add(session, pick, selected)
    assert len(requests) == 1
    assert requests[0]["ref"] == "card:seraphine-lark/character/seraphine-lark"
    assert len([member for member in result["cast"] if member["name"] == "Seraphine Lark"]) == 1
    assert session.model.requests == [], "adding a card must not generate a reply"


def test_refused_cast_choice_is_retained_then_can_be_corrected(session):
    url = cast(session)
    chat = chat_ref(url)
    import_card(session, SERAPHINE_CARD)
    page = session.page
    before = session.app("open", chat=chat)["cast"]
    requests = []
    endpoint = page.url.split("#", 1)[0] + "api/play.add_card"
    page.on("request", lambda request: requests.append(request.post_data_json) if request.url == endpoint else None)
    with delayed_metadata(page) as release:
        pick = reopen_picker(session, url)
        previous = pick.element_handle()
        release()
        page.wait_for_function("node => !node.isConnected", arg=previous, timeout=8000)
        pick.select_option(label="Mara Vell (a character of that name is here)")
        selected = pick.input_value()
        assert selected == "character card:mara-vell/character/mara-vell"
        with add_response(page) as refused:
            page.get_by_role("button", name="Add", exact=True).click()
        assert refused.value.status != 200
        assert refused.value.json()["error"] == "duplicate_binding"
        expect(page.get_by_role("alert")).to_contain_text("duplicate_binding")
        expect(page.get_by_role("button", name="Post", exact=True)).to_be_enabled()
        expect(pick).to_have_value(selected)
        assert session.app("open", chat=chat)["cast"] == before
        pick.select_option(label="Seraphine Lark")
        result = corrected_add(session, pick, pick.input_value())
    assert len(requests) == 2
    assert [request["ref"] for request in requests] == [
        "card:mara-vell/character/mara-vell", "card:seraphine-lark/character/seraphine-lark"]
    names = [member["name"] for member in result["cast"]]
    assert sorted(names) == ["Mara Vell", "Seraphine Lark", "Winifred"]
    assert len({member["actor"] for member in result["cast"]}) == len(names)
    assert session.model.requests == []
