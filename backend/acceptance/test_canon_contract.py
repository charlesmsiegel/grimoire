"""113 integration gate: visible effect review reaches the real authority service."""
import json
from urllib.parse import quote

from playwright.sync_api import expect

from journeys import navigate, post_keep
from test_journeys import cast, chat_ref


def effects(session, chat):
    hub = session.app("campaign.hub", chat=chat, audience="director")
    return [effect for turn in hub["turns"] for effect in turn["effects"]]


def test_effect_review_reaches_revision_bound_authority(session):
    url = cast(session)
    post_keep(session, '*I introduce myself to Mara*')
    chat = chat_ref(url)
    scope = session.app("open", chat=chat)["scope"]["campaign"]
    page = session.page
    hub_route = "campaign/" + scope + "?chat=" + quote(chat, safe="") + "&audience=director"
    navigate(page, hub_route)
    proposal = page.get_by_role("region", name="Propose an effect", exact=True)
    expect(proposal.get_by_label("What happens", exact=True)).to_be_visible()
    proposal.get_by_label("What happens", exact=True).fill("Mara knows Winifred")
    proposal.get_by_role("button", name="Add an operation", exact=True).click()
    proposal.get_by_label("Kind", exact=True).select_option("begin")
    proposal.get_by_label("Relation", exact=True).fill("knows")
    proposal.get_by_label("Subject", exact=True).select_option(label="Mara Vell")
    proposal.get_by_label("Object", exact=True).select_option(label="Winifred")
    proposal.get_by_label("Done by", exact=True).select_option(label="Winifred")
    with page.expect_response(lambda response: response.url.endswith("/api/campaign.propose_effect")) as proposed:
        proposal.get_by_role("button", name="Propose", exact=True).click()
    assert proposed.value.status == 200, proposed.value.json()
    waiting = page.get_by_role("region", name="Changes waiting on approval", exact=True)
    expect(waiting).to_contain_text("Mara knows Winifred")
    before = effects(session, chat)
    assert len(before) == 1 and before[0]["state"] == "proposed"
    assert before[0]["endorsers"] and not before[0]["endorsers"][0]["given"]
    navigate(page, "inbox?chat=" + quote(chat, safe=""))
    endorse = page.get_by_role("button", name="Endorse as shown", exact=True)
    endorse.click()
    page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
    assert effects(session, chat) == before

    # Fault-inject only the revision token on a UI-originated request. The real server must refuse it.
    captured = []
    def stale(route):
        body = route.request.post_data_json
        captured.append(body.copy())
        body["fingerprint"] = "0" * len(body["fingerprint"])
        route.continue_(post_data=json.dumps(body))

    pattern = f"http://127.0.0.1:{session.app.port}/api/campaign.endorse_effect"
    page.route(pattern, stale)
    endorse.click()
    with page.expect_response(lambda response: response.url.endswith("/api/campaign.endorse_effect")) as reply:
        page.get_by_role("dialog").get_by_role("button", name="Endorse", exact=True).click()
    assert reply.value.json()["error"] == "fingerprint_mismatch"
    assert captured[0]["fingerprint"] == before[0]["fingerprint"]
    expect(page.get_by_role("alert")).to_contain_text("fingerprint")
    assert effects(session, chat) == before
    page.unroute(pattern, stale)

    endorse.click()
    page.get_by_role("dialog").get_by_role("button", name="Endorse", exact=True).click()
    expect(endorse).not_to_be_visible()
    endorsed = effects(session, chat)[0]
    assert endorsed["endorsers"][0]["given"] and endorsed["state"] != "accepted"
    navigate(page, hub_route)
    page.get_by_role("button", name="Approve and accept", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Approve", exact=True).click()
    expect(page.get_by_role("region", name="World truth")).to_contain_text("Mara knows Winifred")
    accepted = effects(session, chat)[0]
    assert accepted["state"] == "accepted" and accepted["event"]
    page.reload()
    expect(page.get_by_role("region", name="World truth")).to_contain_text("Mara knows Winifred")
    assert effects(session, chat) == [accepted]
