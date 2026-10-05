"""The regex rule routes: one GET/PUT per level, and the test pane's trace."""

import pytest

from grimoire import store


@pytest.fixture
def ids(client):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    conn = store.llm_connections.create_connection("openrouter", "Seraphine", api_key="k")
    return wid, cid, conn


def _rule(name: str, pattern: str = "a", replacement: str = "b", **extra) -> dict:
    return {"name": name, "pattern": pattern, "replacement": replacement, **extra}


def _url(level: str, ids) -> str:
    wid, cid, conn = ids
    return {"global": "/api/regex",
            "world": f"/api/worlds/{wid}/regex",
            "campaign": f"/api/campaigns/{cid}/regex",
            "connection": f"/api/llm-connections/{conn}/regex"}[level]


def _scope(level: str, ids) -> dict:
    wid, cid, conn = ids
    return {"global": {"kind": "global"},
            "world": {"kind": "world", "wid": wid},
            "campaign": {"kind": "campaign", "cid": cid},
            "connection": {"kind": "connection", "id": conn}}[level]


@pytest.mark.parametrize("level", ["global", "world", "campaign", "connection"])
def test_put_get_each_level(client, ids, level):
    url = _url(level, ids)
    empty = client.get(url).json()
    assert empty["layer"] == {"rules": [], "off": []}
    assert empty["warnings"] == {}
    put = client.put(url, json={"rules": [_rule("One", replacement="$7")]})
    assert put.status_code == 200
    body = put.json()
    rule = body["layer"]["rules"][0]
    assert rule["name"] == "One" and rule["id"].startswith("r-")
    assert client.get(url).json() == body
    # A reference to a group the pattern lacks saves, with a warning beside it.
    assert list(body["warnings"]) == [rule["id"]]


def test_put_invalid_rule_reports_index(client, ids):
    rules = [_rule("Fine"), _rule("Broken", pattern="(", enabled=True), _rule("Also fine")]
    res = client.put(_url("global", ids), json={"rules": rules})
    assert res.status_code == 400
    detail = res.json()  # the app flattens a dict detail into the body
    assert detail["kind"] == "invalid_rule"
    assert detail["index"] == 1
    assert detail["field"] == "pattern"
    assert "compile" in detail["detail"]
    # Nothing was stored.
    assert client.get(_url("global", ids)).json()["layer"]["rules"] == []


def test_campaign_get_lists_inherited_with_levels(client, ids):
    conn = ids[2]
    client.put(_url("connection", ids), json={"rules": [_rule("Conn")]})
    client.put(_url("global", ids), json={"rules": [_rule("Glob")]})
    world = client.put(_url("world", ids), json={"rules": [_rule("Wld")]}).json()
    client.put(_url("campaign", ids), json={"rules": [_rule("Camp")],
                                            "off": [world["layer"]["rules"][0]["id"]]})
    body = client.get(_url("campaign", ids)).json()
    assert [(e["level"], e["rule"]["name"], e["off"]) for e in body["inherited"]] == [
        ("connection", "Conn", False), ("global", "Glob", False), ("world", "Wld", True)]
    assert body["inherited"][0]["source"] == conn
    assert [r["name"] for r in body["layer"]["rules"]] == ["Camp"]
    assert client.get(_url("global", ids)).json()["inherited"] == []


def test_inherited_id_collision_is_a_400(client, ids):
    glob = client.put(_url("global", ids), json={"rules": [_rule("Glob")]}).json()
    clash = _rule("Clash", id=glob["layer"]["rules"][0]["id"])
    res = client.put(_url("world", ids), json={"rules": [clash]})
    assert res.status_code == 400
    assert res.json()["field"] == "id"


@pytest.mark.parametrize("url", [
    "/api/worlds/nope/regex", "/api/campaigns/nope/regex", "/api/llm-connections/nope/regex"])
def test_unknown_level_404(client, url):
    assert client.get(url).status_code == 404
    assert client.put(url, json={"rules": []}).status_code == 404


def test_unknown_world_404(client):
    res = client.post("/api/regex/test", json={
        "scope": {"kind": "world", "wid": "nope"}, "text": "a"})
    assert res.status_code == 404
    res = client.post("/api/regex/test", json={
        "scope": {"kind": "campaign", "cid": "nope"}, "text": "a"})
    assert res.status_code == 404
    res = client.post("/api/regex/test", json={
        "scope": {"kind": "connection", "id": "nope"}, "text": "a"})
    assert res.status_code == 404
    res = client.post("/api/regex/test", json={"scope": {"kind": "moon"}, "text": "a"})
    assert res.status_code == 400


def test_test_endpoint_trace_order_and_reasons(client, ids):
    client.put(_url("connection", ids), json={"rules": [_rule("Conn", "x", "y")]})
    client.put(_url("global", ids), json={"rules": [
        _rule("Glob", "y", "z"), _rule("Off", "q", "r", enabled=False)]})
    client.put(_url("campaign", ids), json={"rules": [_rule("Camp", "z", "done")]})
    res = client.post("/api/regex/test", json={
        "scope": _scope("campaign", ids), "text": "x", "connection": ids[2]})
    assert res.status_code == 200
    body = res.json()
    assert body["result"] == "done"
    assert [(s["level"], s["name"]) for s in body["steps"]] == [
        ("connection", "Conn"), ("global", "Glob"), ("global", "Off"), ("campaign", "Camp")]
    off = body["steps"][2]
    assert off["applied"] is False and off["reason"] == "disabled"
    assert body["steps"][0]["matches"] == 1 and body["steps"][0]["text_after"] == "y"
    # Another connection's rules do not run.
    other = client.post("/api/regex/test", json={
        "scope": _scope("campaign", ids), "text": "x"}).json()
    assert [s["name"] for s in other["steps"]] == ["Glob", "Off", "Camp"]
    assert other["result"] == "x"


def test_test_endpoint_world_scope_is_inherited_plus_own(client, ids):
    client.put(_url("global", ids), json={"rules": [_rule("Glob", "a", "b")]})
    client.put(_url("world", ids), json={"rules": [_rule("Wld", "b", "c")]})
    body = client.post("/api/regex/test", json={
        "scope": _scope("world", ids), "text": "a"}).json()
    assert [s["name"] for s in body["steps"]] == ["Glob", "Wld"]
    assert body["result"] == "c"


def test_test_endpoint_respects_role_phase_depth(client, ids):
    client.put(_url("global", ids), json={"rules": [
        _rule("Deep", "a", "b", min_depth=2, applies=["display"])]})
    base = {"scope": {"kind": "global"}, "text": "a"}
    shallow = client.post("/api/regex/test", json={**base, "depth": 0}).json()
    assert shallow["steps"][0]["reason"] == "outside depth"
    deep = client.post("/api/regex/test", json={**base, "depth": 3}).json()
    assert deep["result"] == "b"
    prompt = client.post("/api/regex/test", json={**base, "depth": 3, "phase": "prompt"}).json()
    assert prompt["steps"][0]["reason"] == "not for this phase"
    user = client.post("/api/regex/test", json={**base, "depth": 3, "role": "user"}).json()
    assert user["steps"][0]["reason"] == "not for this role"
    assert client.post("/api/regex/test", json={**base, "phase": "bogus"}).status_code == 400


def test_test_endpoint_draft_overrides_saved(client, ids):
    saved = client.put(_url("global", ids), json={"rules": [_rule("Saved", "a", "saved")]}).json()
    rid = saved["layer"]["rules"][0]["id"]
    body = client.post("/api/regex/test", json={
        "scope": {"kind": "global"}, "text": "a",
        "draft": _rule("Edited", "a", "edited", id=rid)}).json()
    assert [s["name"] for s in body["steps"]] == ["Edited"]
    assert body["result"] == "edited"
    # Not saved.
    assert client.get("/api/regex").json()["layer"]["rules"][0]["name"] == "Saved"


def test_test_endpoint_new_draft_is_appended_at_the_scope_level(client, ids):
    client.put(_url("global", ids), json={"rules": [_rule("Glob", "a", "b")]})
    client.put(_url("campaign", ids), json={"rules": [_rule("Camp", "b", "c")]})
    body = client.post("/api/regex/test", json={
        "scope": _scope("campaign", ids), "text": "a",
        "draft": _rule("New", "c", "d")}).json()
    assert [(s["level"], s["name"]) for s in body["steps"]] == [
        ("global", "Glob"), ("campaign", "Camp"), ("campaign", "New")]
    assert body["result"] == "d"
    assert body["steps"][-1]["rule_id"].startswith("r-")


def test_test_endpoint_bad_draft_is_a_400(client, ids):
    res = client.post("/api/regex/test", json={
        "scope": {"kind": "global"}, "text": "a",
        "draft": _rule("Bad", "(", enabled=True)})
    assert res.status_code == 400
    detail = res.json()  # the app flattens a dict detail into the body
    assert detail["kind"] == "invalid_rule" and detail["field"] == "pattern"
    assert detail["index"] is None
