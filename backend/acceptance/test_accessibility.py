"""Keyboard and emulated viewport evidence; not physical-phone or screen-reader certification."""
import pytest
from playwright.sync_api import expect

from journeys import navigate, setup, create_source
from test_journeys import cast


def tab_to(page, target, limit=90):
    """Reach a control by real Tab events; locator.focus would mask keyboard traps."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        if target.evaluate("node => node === document.activeElement"):
            return
    raise AssertionError("Keyboard traversal could not reach " + str(target))


def test_keyboard_setup_error_and_modal_cancel(session):
    page = session.page
    folder = page.get_by_label("Folder", exact=True)
    expect(folder).to_be_visible()
    tab_to(page, folder)
    page.keyboard.press("Enter")
    make = page.get_by_role("button", name="Make the library here")
    tab_to(page, make)
    page.keyboard.press("Enter")
    device = page.get_by_label("This device's name")
    expect(device).to_be_focused()
    expect(page.get_by_text("Name this device first.")).to_be_visible()
    page.keyboard.type("desktop")
    tab_to(page, make)
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Who do you want to meet first?")).to_be_visible()
    navigate(page, "sources")
    trigger = page.get_by_role("button", name="New source", exact=True)
    tab_to(page, trigger)
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    assert session.app("sources.catalog")["sources"] == []
    assert session.model.requests == []


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_setup_and_source_controls_fit_viewport(session, width, height):
    page = session.page
    page.set_viewport_size({"width": width, "height": height})
    setup(session)
    create_source(page)
    ready = {"setup": ("heading", "Library setup"), "sources": ("button", "New source"),
             "providers": ("button", "Save"), "sync": ("button", "Pair a device"),
             "import": ("button", "Choose files")}
    for route, (role, name) in ready.items():
        navigate(page, route)
        expect(page.get_by_role(role, name=name, exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), route + " overflows"
        # Measure rendered controls, not CSS declarations; 24 CSS px is the basic target minimum.
        controls = page.get_by_role("main").locator("button,input:not([type=hidden]),select,textarea")
        for control in controls.all():
            if not control.is_visible():
                continue
            control.scroll_into_view_if_needed()
            # A checkbox's associated label is part of its clickable target.
            target = control
            if control.get_attribute("type") in ("checkbox", "radio"):
                parent = control.locator("xpath=ancestor::label")
                if parent.count():
                    target = parent
            box = target.bounding_box()
            assert box and box["width"] > 0 and box["height"] > 0, (route, box)
            if width == 390:
                assert box["width"] >= 24 and box["height"] >= 24, (route, box)
            assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, (route, box)


def test_phone_chat_composer_and_cast_are_reachable(session):
    cast(session)
    page = session.page
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_role("textbox", name="Playing Winifred", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for control in (page.get_by_role("button", name="Post", exact=True),
                    page.get_by_role("button", name="Cast and details", exact=True)):
        control.scroll_into_view_if_needed()
        box = control.bounding_box()
        assert box and box["width"] >= 24 and box["height"] >= 24
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    page.get_by_role("button", name="Cast and details", exact=True).click()
    expect(page.get_by_role("button", name="Add a character", exact=True)).to_be_visible()
    page.get_by_role("button", name="Close panel", exact=True).click()
    expect(page.get_by_role("textbox", name="Playing Winifred", exact=True)).to_be_visible()
