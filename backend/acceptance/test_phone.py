"""Emulated-phone touch journeys: the manual phone checklist in docs/ACCEPTANCE.md, as far as Chromium goes.

The `phone` session is a Pixel-sized touch context (conftest.PHONE). Every control is tapped, an on-screen
keyboard is a viewport that shrinks while the composer has focus, and scrolling is a synthesized touch drag.
This is Chromium emulation on this machine, not a physical phone, its keyboard or its file chooser.
"""
from playwright.sync_api import expect

from card_fixtures import PORTRAIT_2
from conftest import PHONE
from journeys import composer, navigate, post_keep, press
from test_journeys import cast

# An Android keyboard takes about 40% of a phone's height; 520 of 839 CSS px is left above it.
KEYBOARD_LEFT = 520


def reachable(page, control):
    """The control is on screen and a tap at its centre lands on it, not on something drawn over it."""
    control.scroll_into_view_if_needed()
    box = control.bounding_box()
    width, height = page.viewport_size["width"], page.viewport_size["height"]
    assert box and box["width"] >= 24 and box["height"] >= 24, box
    assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, (box, width)
    assert box["y"] >= -1 and box["y"] + box["height"] <= height + 1, (box, height)
    centre = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
    assert control.evaluate("(node, [x, y]) => node.contains(document.elementFromPoint(x, y))", centre), box


def drag_down(page, region, distance=400):
    """A finger drawn down the region, toward its start: Chromium's touch scroll gesture, not a wheel or a
    scrollTop write. (The gesture's positive yDistance scrolls up.)"""
    box = region.bounding_box()
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("Input.synthesizeScrollGesture", {
            "x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] * 0.6,
            "yDistance": distance, "gestureSourceType": "touch", "speed": 1600})
    finally:
        cdp.detach()


def test_phone_play_keyboard_scroll_picture_and_back(phone):
    page = phone.page
    assert page.evaluate("matchMedia('(hover: none)').matches"), "the phone session must be a touch screen"
    url = cast(phone)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for words in ("The tide is turning.", "Where does the ledger go?", "Who keeps the lamps?",
                  "I hold the lantern up.", "Tell me about the harbor master."):
        post_keep(phone, words)

    # The transcript scrolls under a finger.
    log = page.get_by_role("region", name="Transcript", exact=True)
    scroller = log.locator("xpath=ancestor-or-self::*[contains(@class,'chat-log')]").first
    start = scroller.evaluate("node => node.scrollTop")
    assert scroller.evaluate("node => node.scrollHeight > node.clientHeight"), "five turns should overflow"
    drag_down(page, scroller)
    page.wait_for_function("([node, start]) => node.scrollTop < start", arg=[scroller.element_handle(), start])

    # With the keyboard up, what is being typed and the Post button stay above it and take a tap.
    press(composer(page))
    expect(composer(page)).to_be_focused()
    page.set_viewport_size({"width": page.viewport_size["width"], "height": KEYBOARD_LEFT})
    composer(page).fill("Under the keyboard?")
    reachable(page, composer(page))
    reachable(page, page.get_by_role("button", name="Post", exact=True))
    page.set_viewport_size(phone.devices[PHONE]["viewport"])

    # A picture from the cast panel: the tap opens the phone's chooser (camera or files) for an image.
    press(page.get_by_role("button", name="Cast and details", exact=True))
    add = page.get_by_role("button", name="Add picture for Mara Vell", exact=True)
    reachable(page, add)
    with page.expect_file_chooser() as choosing:
        press(add)
    assert "image/*" in (choosing.value.element.get_attribute("accept") or "")
    choosing.value.set_files({"name": "synthetic.png", "mimeType": "image/png", "buffer": PORTRAIT_2})
    expect(page.get_by_role("button", name="Change picture for Mara Vell", exact=True)).to_be_visible()
    press(page.get_by_role("button", name="Close panel", exact=True))

    # Back from the chats list returns to the same chat and its unsent draft.
    navigate(page, "chats")
    page.go_back()
    expect(composer(page)).to_have_value("Under the keyboard?")
    assert page.url == url


# Standalone controls a finger may need: buttons, fields, disclosures, and the links that act as buttons. A link
# in running text is exempt (WCAG 2.5.8), and the skip link is off screen until a keyboard focuses it.
SMALL_TARGETS = """() => [...document.querySelectorAll(
    'button, select, summary, textarea, input:not([type=hidden]), a.g-button, .g-crumb a, a.home-more')]
  .filter((node) => !node.closest('.g-skip') && node.getClientRects().length)
  .map((node) => {
    const label = ['checkbox', 'radio'].includes(node.type) && node.closest('label');
    const box = (label || node).getBoundingClientRect();
    return [node, box];
  })
  .filter(([, box]) => box.width > 0 && (box.width < 24 || box.height < 24))
  .map(([node, box]) => (node.getAttribute('aria-label') || node.textContent || node.name || node.tagName).trim()
    .slice(0, 40) + ' ' + Math.round(box.width) + 'x' + Math.round(box.height))"""


def test_phone_targets_take_a_finger_on_every_page(phone):
    page = phone.page
    url = cast(phone)
    post_keep(phone, "The tide is turning.")
    small = {}
    ready = {"chats": "By character", "home": None, "start": "1 · Start from", "sources": "New entry",
             "art": None, "sync": "Pair a device", "providers": "Send a test call", "import": "Choose files",
             "campaign": "Needs you 0"}
    for route, marker in ready.items():
        navigate(page, route)
        main = page.locator('.g-page[data-page="' + route + '"]')
        expect(main.get_by_text(marker, exact=True).first if marker else main.locator("button, h2").first).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), route + " overflows"
        small[route] = page.evaluate(SMALL_TARGETS)
    page.goto(url)
    press(page.get_by_role("button", name="Cast and details", exact=True))
    expect(page.get_by_role("button", name="Add a character", exact=True)).to_be_visible()
    small["chat"] = page.evaluate(SMALL_TARGETS)
    assert not any(small.values()), {route: found for route, found in small.items() if found}
