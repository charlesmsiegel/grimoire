"""Supplement the deterministic admission tests with actual browser reload bursts.

Each cycle is a new user navigation, never a retry of a failed assertion. Required
module transport failures remain failures even when a later navigation might work.
"""

from urllib.parse import urlsplit

from playwright.sync_api import expect

from journeys import composer, post_keep
from test_journeys import cast


def test_chats_back_and_reload_preserve_draft_through_connection_bursts(session):
    chat_url = cast(session)
    page = session.page
    post_keep(session, '"The word on the ledger is BRINE."')
    origin = urlsplit(chat_url)
    chats_url = f"{origin.scheme}://{origin.netloc}/#/chats"
    module_failures = []
    cancelled = []
    phase = "chat ready"
    navigating = False

    def local_module(request):
        address = urlsplit(request.url)
        return ((address.scheme, address.netloc) == (origin.scheme, origin.netloc)
                and address.path.startswith("/static/") and address.path.endswith(".js")
                and request.resource_type == "script")

    def failed(request):
        if not local_module(request):
            return
        detail = (phase, urlsplit(request.url).path, request.failure)
        # Deliberately leaving a page can cancel its outstanding import. A required
        # import on the destination must still succeed for the mount checks below.
        if navigating and request.failure == "net::ERR_ABORTED":
            cancelled.append(detail)
        else:
            module_failures.append(detail)

    def answered(response):
        if local_module(response.request) and response.status >= 400:
            module_failures.append((phase, urlsplit(response.url).path, response.status))

    page.on("requestfailed", failed)
    page.on("response", answered)
    draft = "Unsent draft survives back navigation"
    try:
        for cycle in range(12):
            composer(page).fill(draft)
            phase, navigating = f"cycle {cycle + 1}: Chats", True
            page.get_by_role("link", name="Chats", exact=True).first.click()
            expect(page).to_have_url(chats_url)
            expect(page.get_by_role("heading", name="Chats", exact=True, level=1)).to_be_visible()
            expect(page.get_by_role("searchbox", name="Search chats", exact=True)).to_be_visible()
            expect(composer(page)).not_to_be_visible()
            navigating = False
            assert not module_failures, module_failures

            phase, navigating = f"cycle {cycle + 1}: Back", True
            page.go_back()
            expect(page).to_have_url(chat_url)
            expect(page.get_by_role("textbox", name="Playing Winifred", exact=True)).to_be_visible()
            expect(composer(page)).to_have_value(draft)
            navigating = False
            assert not module_failures, module_failures

            phase, navigating = f"cycle {cycle + 1}: Reload", True
            response = page.reload()
            assert response is not None and response.status == 200
            expect(page).to_have_url(chat_url)
            expect(page.get_by_role("textbox", name="Playing Winifred", exact=True)).to_be_visible()
            expect(composer(page)).to_have_value(draft)
            navigating = False
            assert not module_failures, module_failures
        assert len(session.model.requests) == 1, "navigation and draft recovery must not call a model"
    except BaseException:
        # Include transport evidence even when a missing mount is the first symptom.
        print("Local module failures:", module_failures)
        print("Module requests cancelled during explicit navigation:", cancelled)
        raise
    finally:
        page.remove_listener("requestfailed", failed)
        page.remove_listener("response", answered)
