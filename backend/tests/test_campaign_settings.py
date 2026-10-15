"""Campaign play defaults are partial opinions, including zero and empty text (#73)."""

import json

import pytest

from grimoire import store
from grimoire.routes import character_turns, decision_capture, history_recall
from grimoire.store import atomic, campaign_settings, context, frontmatter, revision
from grimoire.store.context import archive, assemble, pack, story
from grimoire.store.context import history as history_section
from grimoire.store.history import query
from tests import wire_kit

KEYS = {
    "context_scan_depth", "context_budget", "recap_depth", "archive_depth",
    "rolling_summary_every", "scene_break_every", "system_prompt", "user_label",
    "assistant_label",
}
COUNTS = KEYS - {"system_prompt", "user_label", "assistant_label"}


def _campaign():
    wid = store.worlds.create_world("Realm")
    return store.campaigns.create_campaign("Saltmarch", wid)


def _url(cid):
    return f"/api/campaigns/{cid}/settings"


def test_inheritance_partial_updates_reset_and_isolation(client):
    cid = _campaign()
    other = store.campaigns.create_campaign("Mara", "realm")
    store.write_config(context_budget="8000", user_label="Mara", system_prompt="Global")
    path = store.campaigns.campaign_meta_path(cid)
    before = path.read_bytes()
    view = client.get(_url(cid))
    assert view.status_code == 200
    body = view.json()
    assert set(body) == {"overrides", "inherited", "effective"}
    assert body["overrides"] == {}
    assert set(body["inherited"]) == set(body["effective"]) == KEYS
    assert body["effective"]["context_budget"] == "8000"
    assert path.read_bytes() == before
    changed = client.put(_url(cid), json={"context_budget": "0", "system_prompt": ""})
    assert changed.status_code == 200
    assert changed.json()["overrides"] == {"context_budget": "0", "system_prompt": ""}
    assert changed.json()["effective"]["system_prompt"] == ""
    assert client.get(_url(other)).json()["effective"]["system_prompt"] == "Global"
    store.write_config(context_budget="9000")
    assert client.get(_url(cid)).json()["inherited"]["context_budget"] == "9000"
    reset = client.put(_url(cid), json={"context_budget": None}).json()
    assert reset["overrides"] == {"system_prompt": ""}
    assert reset["effective"]["context_budget"] == "9000"


def test_prompt_round_trip_preserves_record_and_prevents_key_injection(client):
    cid = _campaign()
    path = store.campaigns.campaign_meta_path(cid)
    meta, _ = frontmatter.read_record(path, "campaign.md")
    meta["unrelated"] = "kept"
    body = "# Saltmarch\n\nStanding text.\n"
    atomic.write_text(path, frontmatter.dump_frontmatter(meta, body))
    prompt = "Mara's words: \"yes\"\\next" + "".join(frontmatter.LINE_BREAKS) + "\nworld: injected"
    response = client.put(_url(cid), json={"system_prompt": prompt})
    assert response.status_code == 200
    assert response.json()["effective"]["system_prompt"] == prompt
    actual, actual_body = frontmatter.read_record(path, "campaign.md")
    assert actual_body == body
    assert {k: v for k, v in actual.items() if k not in {"updated", "system_prompt_json"}} == {
        k: v for k, v in meta.items() if k != "updated"}
    assert json.loads(actual["system_prompt_json"]) == prompt
    assert actual["system_prompt_json"].isascii()
    assert not frontmatter.breaks_line(actual["system_prompt_json"])
    assert client.put(_url(cid), json={"system_prompt": None}).json()["overrides"] == {}
    assert "system_prompt_json" not in frontmatter.read_record(path, "campaign.md")[0]


@pytest.mark.parametrize("fields", [
    {"unknown": "1"}, {"system_prompt_json": '"x"'}, {"context_budget": 4},
    {"context_budget": True}, {"system_prompt": []}, {"assistant_label": {}},
    {"context_budget": ""}, {"context_budget": "-1"}, {"context_budget": "+1"},
    {"context_budget": " 1"}, {"context_budget": "1.0"}, {"context_budget": "\u0661"},
    {"context_budget": "2147483648"}, {"context_budget": "9" * 5000},
    {"user_label": "  "}, {"assistant_label": ""},
] + [{"user_label": "Mara" + boundary + "world: injected"}
     for boundary in sorted(frontmatter.LINE_BREAKS)])
def test_invalid_mixed_writes_are_atomic(client, fields):
    cid = _campaign()
    path = store.campaigns.campaign_meta_path(cid)
    before, token = path.read_bytes(), revision.current(cid)
    response = client.put(_url(cid), json={"recap_depth": "1", **fields})
    assert response.status_code == 400
    assert path.read_bytes() == before
    assert revision.current(cid) == token


@pytest.mark.parametrize("key", sorted(COUNTS))
def test_numeric_boundaries_and_noop_stamps(client, monkeypatch, key):
    cid = _campaign()
    path = store.campaigns.campaign_meta_path(cid)
    monkeypatch.setattr(store.paths, "now_iso", lambda: "2099-01-01T00:00:00Z")
    old = revision.current(cid)
    assert client.put(_url(cid), json={key: "2147483647"}).status_code == 200
    assert revision.current(cid) != old
    for value in ("2147483647", "0"):
        assert client.put(_url(cid), json={key: value}).json()["effective"][key] == value
        before, token = path.read_bytes(), revision.current(cid)
        assert client.put(_url(cid), json={key: value}).status_code == 200
        assert client.put(_url(cid), json={}).status_code == 200
        assert client.put(_url(cid), json={"user_label": None}).status_code == 200
        assert path.read_bytes() == before
        assert revision.current(cid) == token


@pytest.mark.parametrize("bad", [b"", b"body only", b"---\nname: Saltmarch\n", b"\xff"])
def test_unreadable_records_are_not_rewritten(client, bad):
    cid = _campaign()
    path = store.campaigns.campaign_meta_path(cid)
    atomic.write_bytes(path, bad)
    token = revision.current(cid)
    assert client.get(_url(cid)).status_code == 409
    assert client.put(_url(cid), json={"context_budget": "100"}).status_code == 409
    assert path.read_bytes() == bad
    assert revision.current(cid) == token


def test_missing_campaigns_are_404(client):
    assert client.get(_url("absent")).status_code == 404
    assert client.put(_url("absent"), json={"system_prompt": ""}).status_code == 404


def test_invalid_manual_overrides_fall_back_and_response_is_allowlisted(client):
    cid = _campaign()
    store.write_config(context_budget="9000", system_prompt="Global", user_label="Mara")
    path = store.campaigns.campaign_meta_path(cid)
    meta, body = frontmatter.read_record(path, "campaign.md")
    meta.update(context_budget="-2", user_label="", system_prompt_json="broken",
                api_key="private", unrelated="secret")
    atomic.write_text(path, frontmatter.dump_frontmatter(meta, body))
    view = client.get(_url(cid))
    assert view.status_code == 200
    assert view.json()["overrides"] == {}
    assert view.json()["effective"]["context_budget"] == "9000"
    assert view.json()["effective"]["system_prompt"] == "Global"
    assert "secret" not in view.text and "private" not in view.text


@pytest.mark.parametrize("scalar,expected", [
    ("null", "GLOBAL_MARKER"), ("123", "GLOBAL_MARKER"),
    ("true", "GLOBAL_MARKER"), ("{}", "GLOBAL_MARKER"),
    ("[" * 100000, "GLOBAL_MARKER"),
    (json.dumps(chr(0xD800)), "GLOBAL_MARKER"),
    (json.dumps("Mara waits.\nSeraphine replies.\u2028Winifred listens."),
     "Mara waits.\nSeraphine replies.\u2028Winifred listens."),
], ids=["null", "number", "boolean", "object", "deep-malformed", "surrogate", "multiline-string"])
def test_manual_prompt_scalars_fall_back_without_breaking_readers(client, scalar, expected):
    cid = _campaign()
    store.write_config(system_prompt="GLOBAL_MARKER")
    path = store.campaigns.campaign_meta_path(cid)
    meta, body = frontmatter.read_record(path, "campaign.md")
    meta["system_prompt_json"] = scalar
    atomic.write_text(path, frontmatter.dump_frontmatter(meta, body))
    response = client.get(_url(cid))
    assert response.status_code == 200
    assert response.json()["effective"]["system_prompt"] == expected
    assert campaign_settings.effective_config(cid)["system_prompt"] == expected
    assert ("system_prompt" in response.json()["overrides"]) == (expected != "GLOBAL_MARKER")


@pytest.mark.parametrize("key", ["system_prompt", "user_label", "assistant_label"])
@pytest.mark.parametrize("surrogate", [chr(0xD800), chr(0xDFFF)], ids=["high", "low"])
def test_unpaired_unicode_surrogates_are_rejected_atomically(client, key, surrogate):
    cid = _campaign()
    path = store.campaigns.campaign_meta_path(cid)
    before, token = path.read_bytes(), revision.current(cid)
    # Escape the request explicitly: httpx's encoder would reject the text
    # before the request reaches the same JSON decoder an external client uses.
    response = client.put(_url(cid), content=json.dumps({"recap_depth": "1", key: surrogate}),
                          headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert path.read_bytes() == before
    assert revision.current(cid) == token


@pytest.mark.parametrize("key", [chr(0xD800), chr(0xDFFF)], ids=["high", "low"])
def test_surrogate_field_names_are_rejected_atomically(client, key):
    cid = _campaign()
    path = store.campaigns.campaign_meta_path(cid)
    before, token = path.read_bytes(), revision.current(cid)
    # JSON decodes escaped keys too. A refusal must itself remain encodable,
    # rather than echoing the invalid scalar into an HTTP error response.
    response = client.put(_url(cid), content=json.dumps({"recap_depth": "1", key: "x"}),
                          headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert path.read_bytes() == before
    assert revision.current(cid) == token


def test_activity_and_revision_follow_only_durable_changes(client, monkeypatch):
    cid = _campaign()
    root = store.campaigns.campaign_root(cid)
    activity = root / "activity.txt"
    activity.unlink(missing_ok=True)
    before = revision.current(cid)
    assert client.put(_url(cid), json={}).status_code == 200
    assert not activity.exists() and revision.current(cid) == before
    assert client.put(_url(cid), json={"user_label": "Mara"}).status_code == 200
    assert activity.exists() and revision.current(cid) != before
    stamp, token = activity.read_bytes(), revision.current(cid)
    assert client.put(_url(cid), json={"user_label": "Mara"}).status_code == 200
    assert activity.read_bytes() == stamp and revision.current(cid) == token
    real_write = atomic.write_text

    def fail_settings(path, text):
        if path.name == "campaign.md":
            raise OSError("publication refused")
        return real_write(path, text)

    monkeypatch.setattr(atomic, "write_text", fail_settings)
    assert client.put(_url(cid), json={"user_label": "Winifred"}).status_code == 409
    assert activity.read_bytes() == stamp and revision.current(cid) == token


def test_context_prompt_scan_and_both_packers_use_campaign_values(client):
    cid = _campaign()
    root = store.campaigns.campaign_root(cid)
    store.entities.create_entity(root, "lore", "Saltmarch", "LORE_MARKER", keys="ledger")
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "The ledger.")
    store.scenes.append_message(cid, sid, "assistant", "Nothing follows.")
    store.write_config(system_prompt="GLOBAL_MARKER", context_scan_depth="8", context_budget="10000")
    campaign_settings.write(cid, {"system_prompt": "CAMPAIGN_MARKER", "context_scan_depth": "1",
                                  "context_budget": "7000"})
    messages, detail = context.compose_turn(cid, sid)
    assert "CAMPAIGN_MARKER" in str(messages) and "GLOBAL_MARKER" not in str(messages)
    assert "LORE_MARKER" not in str(messages)
    assert detail["budget_tokens"] == 7000
    a = assemble._assemble(cid, sid)
    assert assemble._packed(a, cid, sid)["budget"] == 7000
    assert pack.budget_tokens() == 10000
    campaign_settings.write(cid, {"system_prompt": "", "context_scan_depth": "0", "context_budget": "0"})
    messages, detail = context.compose_turn(cid, sid)
    assert "GLOBAL_MARKER" not in str(messages) and "CAMPAIGN_MARKER" not in str(messages)
    assert "LORE_MARKER" not in str(messages) and detail["budget_tokens"] == 0
    # Explicit seeds still wake lore even when the transcript window is zero.
    opener, _ = context.compose_opener(cid, sid, "ledger")
    assert "LORE_MARKER" in str(opener)
    campaign_settings.write(cid, {"system_prompt": None, "context_scan_depth": None})
    restored, _ = context.compose_turn(cid, sid)
    assert "GLOBAL_MARKER" in str(restored) and "LORE_MARKER" in str(restored)


def test_recap_archive_omissions_and_opener_share_campaign_windows(client):
    cid = _campaign()
    for n in range(3):
        store.chronicle.absorb(cid, {"id": f"000--{n}", "one_line": f"RECAP_{n}",
                                     "summary": f"ARCHIVE_{n}", "keywords": ["ledger"]})
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "ledger")
    store.write_config(recap_depth="3", archive_depth="3")
    campaign_settings.write(cid, {"recap_depth": "1", "archive_depth": "1"})
    assert story._story_entries(cid) == ["RECAP_2"]
    assert story._recap_ids(cid) == frozenset({"000--2"})
    assert [r["id"] for r in archive._archive_entries(cid, "ledger", story._recap_ids(cid))] == ["000--1"]
    assert history_section.summary_omissions(cid, sid, "ledger") == {"000--1": "omitted:archive"}
    messages, _ = context.compose_turn(cid, sid)
    assert "RECAP_2" in str(messages) and "RECAP_1" not in str(messages)
    assert "ARCHIVE_1" in str(messages) and "ARCHIVE_0" not in str(messages)
    campaign_settings.write(cid, {"recap_depth": "0", "archive_depth": "0"})
    messages, _ = context.compose_turn(cid, sid)
    assert "RECAP_" not in str(messages) and "ARCHIVE_" not in str(messages)
    opener, _ = context.compose_opener(cid, sid, "Continue.")
    assert all(f"ARCHIVE_{n}" in str(opener) for n in range(3))


def test_history_query_preview_and_turn_budget_use_campaign_values(client):
    cid = _campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "silver ledger")
    store.scenes.append_message(cid, sid, "assistant", "velvet crown")
    store.write_config(context_scan_depth="8", context_budget="8000", history_recall_depth="3")
    campaign_settings.write(cid, {"context_scan_depth": "1", "context_budget": "2000"})
    prepared = query.prepare(cid, sid)
    assert prepared.scan_text == "velvet crown"
    assert prepared.archive_scan_text == "velvet crown"
    assert history_recall._prepare(cid, sid, "", None).cap == 300
    assert history_section.preview(cid, sid).ceiling == 300
    campaign_settings.write(cid, {"context_scan_depth": "0", "context_budget": "0"})
    assert query.prepare(cid, sid).scan_text == ""
    assert query.prepare(cid, sid, seed="amber seal").scan_text == "amber seal"
    assert history_recall._prepare(cid, sid, "", None).cap == 1200


@pytest.mark.parametrize("setting,path", [("rolling_summary_every", "rolling-summary"),
                                         ("scene_break_every", "scene-break")])
def test_summary_schedules_use_campaign_threshold_and_zero(client, setting, path):
    cid = _campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "The ledger changes hands.")
    store.write_config(**{setting: "20"})
    campaign_settings.write(cid, {setting: "1"})
    base = f"/api/campaigns/{cid}/scenes/{sid}/{path}"
    assert client.get(base).json()["every"] == 1
    campaign_settings.write(cid, {setting: "0"})
    body = client.get(base).json()
    assert body["every"] == 0 and body["due"] is False
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
    body = client.post(base).json()
    assert body["every"] == 0
    assert body["refreshed" if path == "rolling-summary" else "asked"] is False


@pytest.mark.parametrize("kind", ["generation", "decision"])
def test_plain_prompt_captures_report_the_campaign_budget(client, kind):
    cid = _campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    store.write_config(context_budget="8000")
    campaign_settings.write(cid, {"context_budget": "2000"})
    messages = [{"role": "user", "content": "Continue."}]
    target = wire_kit.target(kind="openrouter", model="m")
    if kind == "generation":
        character_turns._capture(cid, sid, "chat", messages, target)
    else:
        scope = decision_capture.Scope("scene-break", True)
        scope._take("", messages, {"mode": "structured"}, target)
        decision_capture._file(scope, cid, sid, lambda: True)
    entry, = store.prompt_log.list_entries(cid, sid)
    payload = store.prompt_log.read_entry(cid, entry["id"], scene=sid)
    assert payload["budget_tokens"] == 2000


@pytest.mark.parametrize("key,value,message", [
    ("context_budget", "", "context_budget must be a whole number, zero or more"),
    ("recap_depth", "-1", "recap_depth must be a whole number, zero or more"),
    ("user_label", "  ", "user_label must be a single non-blank line"),
    ("assistant_label", "Two\nLines", "assistant_label must be a single non-blank line"),
    ("system_prompt", chr(0xD800), "system_prompt must be text"),
])
def test_refusals_name_the_field_and_the_rule_it_broke(client, key, value, message):
    cid = _campaign()
    response = client.put(_url(cid), content=json.dumps({key: value}),
                          headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["detail"] == message


def test_packing_refuses_to_guess_a_campaign_budget():
    # The global default is not a campaign's budget: a caller that omits one
    # must say which campaign it resolved for, not get the global value.
    with pytest.raises(TypeError, match="budget_tokens"):
        pack.pack([], [])
