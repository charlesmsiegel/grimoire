"""Stale browser actions cannot silently mutate a replacement or newer caption."""

import pytest

from tests.record_art_fixtures import KINDS, art_url, image_url, setup_records, upload


@pytest.fixture
def records(client):
    return setup_records(client)


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("operation", ("delete", "promote", "description", "focus"))
def test_named_mutations_matrix_rejects_replaced_image(client, records, scope, kind, operation):
    name = "gallery_1" if operation == "promote" else "avatar"
    upload(client, records, scope, kind, name)
    old = client.get(art_url(records, scope, kind)).json()["images"][0]
    replaced = upload(client, records, scope, kind, name, 1)
    url = image_url(records, scope, kind, name)
    guard = {"expected_v": old["v"]}
    if operation == "delete":
        response = client.delete(url, params=guard)
    elif operation == "promote":
        response = client.post(url + "/promote", json=guard)
    elif operation == "description":
        response = client.put(url + "/description", json={**guard, "description": "A portrait"})
    else:
        response = client.put(url + "/focus", json={**guard, "focus": 80})
    assert response.status_code == 409, response.text
    assert response.json()["kind"] == "stale_image"
    [current] = client.get(art_url(records, scope, kind)).json()["images"]
    assert current["image_id"] == replaced["image_id"] and current["name"] == name
    assert not current["described"]


def test_primary_expectation_distinguishes_absent_from_omitted(client, records):
    primary = upload(client, records, "world", "characters", "avatar")
    selected = upload(client, records, "world", "characters", "gallery_1", 1)
    url = image_url(records, "world", "characters", "gallery_1") + "/promote"
    response = client.post(url, json={"expected_primary_v": None})
    assert response.status_code == 409, response.text
    assert response.json()["kind"] == "stale_primary"
    # An old caller that omits the field keeps its established behavior.
    assert client.post(url).status_code == 200
    rows = client.get(art_url(records, "world", "characters")).json()["images"]
    assert {row["image_id"] for row in rows} == {primary["image_id"], selected["image_id"]}
    assert rows[0]["image_id"] == selected["image_id"]


@pytest.mark.parametrize("kind", KINDS)
def test_inherited_focus_changes_campaign_only(client, records, kind):
    upload(client, records, "world", kind, "avatar")
    world = client.get(art_url(records, "world", kind)).json()["images"][0]
    url = image_url(records, "campaign", kind, "avatar") + "/focus"
    response = client.put(url, json={"focus": 90, "expected_v": world["v"]})
    assert response.status_code == 200, response.text
    own = client.get(art_url(records, "campaign", kind)).json()["images"][0]
    unchanged = client.get(art_url(records, "world", kind)).json()["images"][0]
    assert own["focus"] == 90 and unchanged["focus"] is None
    assert own["v"] == unchanged["v"] == world["v"]
    if kind not in ("characters", "pcs"):
        summary_url = f'/api/campaigns/{records["campaign"]}/{kind}'
        summary = next(i for i in client.get(summary_url).json() if i["id"] == records["records"][kind])
        assert summary["image_focus"] == 90 and summary["image_v"] == world["v"]
    assert client.put(url, json={"focus": 101}).status_code == 400
