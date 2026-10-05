"""Scene routes compose with post images where the route reads them (#377)."""

import io

from PIL import Image

from grimoire import content_parts as cp
from grimoire import routes, store
from tests.llm_fakes import CapturingOpenRouter


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


def _setup(client, *, send="on", vision="on"):
    conn = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Seraphine", "api_key": "k", "model": "m",
        "vision": vision}).json()["id"]
    client.put("/api/config", json={"active_connection_id": conn, "send_images": send})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch Nights", "world": wid}).json()["id"]
    store.campaign_images.put_image(cid, "coastline", _png(), "png")
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user",
                                f"Look ![a map](/api/campaigns/{cid}/images/coastline)")
    store.scenes.append_message(cid, sid, "assistant", "The tide is out.")
    return cid, sid


def _chat(client, cid, sid):
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "and then?"}) as r:
        for _ in r.iter_lines():
            pass
    return cap.messages


def test_a_turn_on_a_vision_route_carries_the_reference(client):
    cid, sid = _setup(client)
    msgs = _chat(client, cid, sid)
    assert [r["alt"] for m in msgs for r in cp.image_refs(m["content"])] == ["a map"]
    assert msgs.campaign == cid


def test_with_the_setting_off_the_turn_is_text_only(client):
    cid, sid = _setup(client, send="off")
    msgs = _chat(client, cid, sid)
    assert all(isinstance(m["content"], str) for m in msgs)


def test_a_text_only_connection_composes_text(client):
    cid, sid = _setup(client, vision="off")
    msgs = _chat(client, cid, sid)
    assert all(isinstance(m["content"], str) for m in msgs)


def test_the_inspector_shows_the_images_only_when_they_would_be_sent(client):
    cid, sid = _setup(client)
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert "history_images" in {r["id"] for r in body["sections"]}
    client.put("/api/config", json={"send_images": "off"})
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert "history_images" not in {r["id"] for r in body["sections"]}


def test_a_regenerate_replays_the_frozen_prompt_in_this_campaign(client):
    """A reroll replays the snapshot frozen for the reply it replaces; the
    references in it resolve against the campaign running the reroll."""
    cid, sid = _setup(client)
    _chat(client, cid, sid)
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/regenerate") as r:
        assert r.status_code == 200
        for _ in r.iter_lines():
            pass
    assert cap.messages.campaign == cid
    assert any(cp.image_refs(m["content"]) for m in cap.messages)
