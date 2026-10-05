"""The prompt phase reaches every LLM reader of transcript text (regex spec 5.2).

Each integration test stores rules through the routes, then drives the real
path with a fake from `llm_fakes` and reads what the fake was sent. The stored
transcript stays raw throughout; only what a model is shown changes.
"""

from __future__ import annotations

import json

import pytest

from grimoire import routes, store
from grimoire.routes import character_turns
from grimoire.routes import tracker as tracker_routes
from grimoire.store.absorb import routing
from grimoire.store.regex import view as regex_view
from grimoire.store.scenes import serialize
from tests import draft_runs, review_runs
from tests.llm_fakes import CapturingOpenRouter, FakeLLM, FakeOpenRouterComplete, from_entries

THINK = {"name": "Strip thinking", "pattern": r"<think>[\s\S]*?</think>", "replacement": ""}


def put_rules(client, url, *rules):
    response = client.put(url, json={"rules": list(rules)})
    assert response.status_code == 200, response.text
    return response.json()


def sent(fake) -> str:
    """Every message of every request the fake received, joined."""
    return "\n".join(m["content"] for r in fake.requests for m in r["messages"])


# ---- the turn prompt ---------------------------------------------------------

def seed_turn(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x", "model": "primary"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    response = client.post(f"/api/campaigns/{cid}/characters", json={"name": "Mara"})
    assert response.status_code == 200, response.text
    actor = response.json()["character"]
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
    assert response.status_code == 200, response.text
    return cid, sid


def reply(cid, sid, content, connection="openrouter", speaker="Mara"):
    store.scenes.append_reply(cid, sid, [{"speaker": speaker, "content": content,
                                          "connection": connection}])


def turn(client, cid, sid, content="We walk on."):
    fake = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                           json={"content": content, "speaker_ref": "characters:mara"})
    assert response.status_code == 200, response.text
    return fake.requests[0]["messages"]


def text_of(messages) -> str:
    return "\n".join(m["content"] for m in messages)


def test_connection_rule_strips_think_in_turn_prompt(client):
    cid, sid = seed_turn(client)
    store.scenes.append_message(cid, sid, "user", "We meet.")
    reply(cid, sid, "<think>plotting</think>Hello")
    put_rules(client, "/api/llm-connections/openrouter/regex", THINK)

    prompt = text_of(turn(client, cid, sid))
    assert "Hello" in prompt
    assert "<think>" not in prompt and "plotting" not in prompt
    # Only the prompt: the stored reply is untouched.
    assert store.scenes.read_scene(cid, sid)["messages"][1]["content"] == \
        "<think>plotting</think>Hello"


def test_connection_rule_skips_other_connections_posts(client):
    cid, sid = seed_turn(client)
    other = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Backup", "model": "backup",
        "api_key": "sk-backup"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We meet.")
    reply(cid, sid, "<think>plotting</think>Hello", connection=other)
    put_rules(client, "/api/llm-connections/openrouter/regex", THINK)

    assert "<think>plotting</think>Hello" in text_of(turn(client, cid, sid))


def test_campaign_prompt_only_rule_not_in_display(client):
    cid, sid = seed_turn(client)
    store.scenes.append_message(cid, sid, "user", "We meet.")
    reply(cid, sid, "Hello there")
    put_rules(client, f"/api/campaigns/{cid}/regex",
              {"name": "Howdy", "pattern": "Hello", "replacement": "Howdy",
               "applies": ["prompt"]})

    prompt = text_of(turn(client, cid, sid))
    assert "Howdy there" in prompt and "Hello there" not in prompt
    shown = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"][1]
    assert shown["content"] == "Hello there"
    assert "shown" not in shown


def test_depth_counts_full_transcript(client):
    cid, sid = seed_turn(client)
    store.scenes.append_message(cid, sid, "user", "Hello zero")
    reply(cid, sid, "Hello one")
    put_rules(client, f"/api/campaigns/{cid}/regex",
              {"name": "Newest only", "pattern": "Hello", "replacement": "Howdy",
               "targets": ["model", "user"], "applies": ["prompt"], "max_depth": 0})

    prompt = text_of(turn(client, cid, sid, content="Hello two"))
    assert "Hello zero" in prompt and "Hello one" in prompt
    assert "Howdy two" in prompt and "Hello two" not in prompt


def test_no_rules_leaves_the_turn_prompt_alone(client):
    """The invariant the phase rests on: with no rule files a prompt is built
    from the transcript exactly as it was before rules existed."""
    cid, sid = seed_turn(client)
    store.scenes.append_message(cid, sid, "user", "We meet.")
    reply(cid, sid, "<think>plotting</think>Hello")
    assert "<think>plotting</think>Hello" in text_of(turn(client, cid, sid))


# ---- the response selector and the tracker update ------------------------------

def test_response_selector_reads_the_prompt_view(client):
    cid, sid = seed_turn(client)
    store.scenes.append_message(cid, sid, "user", "We meet.")
    reply(cid, sid, "<think>plotting</think>Hello")
    put_rules(client, "/api/llm-connections/openrouter/regex", THINK)

    rendered = text_of(character_turns._selector_messages(
        cid, sid, {"eligible": [], "note": ""}))
    assert "Hello" in rendered and "plotting" not in rendered


def test_tracker_update_input_reads_the_prompt_view(client):
    cid, sid = seed_turn(client)
    put_rules(client, "/api/llm-connections/openrouter/regex",
              {"name": "Hush", "pattern": " SECRET", "replacement": ""})
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeLLM([["Hello SECRET there"]])
    for content in ("We meet.", "We walk on."):
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                               json={"content": content, "speaker_ref": "characters:mara"})
        assert response.status_code == 200, response.text
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["content"] == "Hello SECRET there"   # stored raw

    keys = dict(store.tracker.walk.ordered_keys(cid, sid))
    located = tracker_routes._locate(cid, sid, keys[len(messages) - 1])
    assert located["post_msg"]["content"] == "Hello there"
    context = tracker_routes._context_posts(cid, sid, len(messages) - 1)
    assert [p["content"] for p in context] == ["Hello there", "We walk on."]


def test_passage_draft_context_reads_the_prompt_view(client):
    """The two posts before a passage are prompt context for its character
    draft -- a reader the turn and review paths do not cover."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    sid = store.scenes.create_scene(cid, "Arrival")
    store.scenes.append_message(cid, sid, "user", "We meet.")
    reply(cid, sid, "<think>plotting</think>Hello")
    text = 'Mara said, "Wait."'
    store.scenes.append_message(cid, sid, "assistant", text, speaker="Grimoire")
    rid = next(r["id"] for r in store.responses.migrate(cid, sid)
               if r.get("speaker") == "Grimoire")
    put_rules(client, "/api/llm-connections/openrouter/regex", THINK)
    fake = FakeOpenRouterComplete("Watches the door.")
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    drafted = draft_runs.post(
        client, f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character-draft",
        json={"name": "Mara", "passage": text, "source_text": text})
    assert drafted.status_code == 200, drafted.json()
    assert "Hello" in sent(fake) and "plotting" not in sent(fake)


# ---- absorb and the review phases ----------------------------------------------

ABSORB = {"one_line": "They met.", "summary": "A meeting.", "keywords": ["tea"],
          "timeline_events": [], "character_state_edits": [], "lore_edits": [],
          "plot_movements": [], "relationship_deltas": [], "bond_changes": [],
          "new_lore": [], "weather_edits": []}
_EXTRACTION = {"system_contains": "You are absorbing a completed role-play scene"}
_AUDIT = {"system_contains": "You are auditing a completed role-play scene"}
_DOSSIER = {"system_contains": "You are updating a game master's dossier"}
_VOICE = {"system_contains": "You are checking one character's dialogue"}


def absorb_fake(**extraction):
    return from_entries([
        {"when": _EXTRACTION, "reply": json.dumps({**ABSORB, **extraction})},
        {"when": _DOSSIER, "reply": "Aese is steady."},
        {"when": _VOICE, "reply": '{"verdict": "in_voice", "note": ""}'},
        {"when": _AUDIT, "reply": '{"warnings": [], "sheet_deltas": []}'}])


@pytest.fixture
def review_scene(client):
    """A campaign with a present NPC whose one reply came from `openrouter`."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Aese", "version_name": "main"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Tearoom"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "aese", "version": "main", "role": "npc"})
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    store.scenes.append_message(cid, sid, "user", "We entered.")
    return cid, sid


def test_absorb_prompt_uses_view(client, review_scene):
    cid, sid = review_scene
    reply(cid, sid, "<think>a secret plan</think>Hello, traveller.", speaker="Aese")
    put_rules(client, "/api/llm-connections/openrouter/regex", THINK)
    fake = absorb_fake()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    assert review_runs.absorb(client, cid, sid).status_code == 200
    extraction = next(r["messages"] for r in fake.requests
                      if _EXTRACTION["system_contains"] in r["messages"][0]["content"])
    assert "Hello, traveller." in text_of(extraction)
    # Nor any other phase: dossier, voice and audit read the same view.
    assert "secret plan" not in sent(fake)


def test_absorb_citation_from_cleaned_text_attributed(client, review_scene):
    """The citation is judged against the text the model was shown. The quote
    exists only once the rule has run, so judged against the raw transcript it
    would read as a fabrication. (Curly against straight quotes would not show
    this: the citation check folds those itself.)"""
    cid, sid = review_scene
    reply(cid, sid, "“Hello <think>she weighs him up</think>there,” Aese says.", speaker="Aese")
    put_rules(client, "/api/llm-connections/openrouter/regex", THINK)
    fake = absorb_fake(character_state_edits=[{
        "id": "characters/aese", "current_state": "Warier.", "speaker": "Aese",
        "quote": '"Hello there,"', "certainty": 0.9}])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    review = review_runs.absorb(client, cid, sid)
    assert review.status_code == 200, review.json()
    staged = next(e for e in review.json()["edits"] if e["kind"] == "character_state")
    assert staged["review"]["authority"] != routing.UNATTRIBUTED
    assert staged["review"]["band"] == "high"


def test_rolling_summary_and_dossier_prompts_use_view(client, review_scene):
    cid, sid = review_scene
    reply(cid, sid, "<think>a secret plan</think>Hello, traveller.", speaker="Aese")
    put_rules(client, "/api/llm-connections/openrouter/regex", THINK)

    fake = FakeOpenRouterComplete("They met over tea.")
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true")
    assert response.status_code == 200, response.text
    assert fake.requests and "Hello, traveller." in sent(fake)
    assert "secret plan" not in sent(fake)

    fake = absorb_fake()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    assert review_runs.absorb(client, cid, sid).status_code == 200
    fake = from_entries([{"when": _DOSSIER, "reply": "Aese, warier now."}])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    review_runs.dossiers(client, cid, sid)
    assert fake.requests and "Hello, traveller." in sent(fake)
    assert "secret plan" not in sent(fake)


def test_a_prompt_rule_change_makes_the_rolling_summary_stale(client, review_scene):
    """The summary was folded from the prompt view, so its validity is checked
    against that view: a prompt rule added, edited or switched off afterwards
    means the prose describes text the model is no longer shown."""
    cid, sid = review_scene
    reply(cid, sid, "<think>a secret plan</think>Hello, traveller.", speaker="Aese")
    url = f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary"
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("They met; she had a plan.")
    assert client.post(url + "?force=true").json()["refreshed"] is True
    assert client.get(url).json()["stale"] is False

    # A display-only rule does not touch what the summary was folded from.
    put_rules(client, f"/api/campaigns/{cid}/regex", {**THINK, "applies": ["display"]})
    assert client.get(url).json()["stale"] is False

    put_rules(client, f"/api/campaigns/{cid}/regex", THINK)
    assert client.get(url).json()["stale"] is True

    # Folded again under the rule, and switching the rule off undoes it again.
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("They met over tea.")
    assert client.post(url + "?force=true").json()["refreshed"] is True
    assert client.get(url).json()["stale"] is False
    put_rules(client, f"/api/campaigns/{cid}/regex", {**THINK, "enabled": False})
    assert client.get(url).json()["stale"] is True


def test_scene_break_prompt_uses_view(client, review_scene):
    cid, sid = review_scene
    reply(cid, sid, "<think>a secret plan</think>Hello, traveller.", speaker="Aese")
    put_rules(client, "/api/llm-connections/openrouter/regex", THINK)
    fake = FakeOpenRouterComplete('{"break": false, "reason": "still going"}')
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break?force=true")
    assert response.status_code == 200, response.text
    assert fake.requests and "Hello, traveller." in sent(fake)
    assert "secret plan" not in sent(fake)


# ---- the view itself ---------------------------------------------------------------

def _messages():
    return [
        {"role": "user", "content": "Hello a"},
        {"role": "assistant", "speaker": "Mara", "content": "Hello b"},
        {"role": "assistant", "speaker": serialize.ROLL_SPEAKER, "content": "Hello roll"},
        {"role": "assistant", "speaker": "Mara", "content": "Hello c", "connection": "gone"},
    ]


def test_view_with_no_rules_returns_equal_copies(client):
    messages = _messages()
    out = regex_view.view(messages, cid=None, phase="prompt")
    assert out == messages
    assert all(a is not b for a, b in zip(out, messages, strict=True))


def test_view_windows_count_depth_over_the_whole(client):
    store.regex.layers.write_level("global", "", {"rules": [
        {"name": "Second newest", "pattern": "Hello", "replacement": "Howdy",
         "targets": ["model", "user"], "min_depth": 1, "max_depth": 1}]})
    messages = _messages()
    whole = regex_view.view(messages, cid=None, phase="prompt")
    window = regex_view.view(messages[1:3], cid=None, phase="prompt", offset=1, total=4)
    # Depth 1 is the roll line, which no rule touches; nothing else is at depth 1.
    assert [m["content"] for m in whole] == ["Hello a", "Hello b", "Hello roll", "Hello c"]
    assert [m["content"] for m in window] == ["Hello b", "Hello roll"]
    window = regex_view.view(messages[:2], cid=None, phase="prompt", offset=0, total=3)
    assert [m["content"] for m in window] == ["Hello a", "Howdy b"]


def test_annotate_shown_marks_only_what_display_changed(client):
    store.regex.layers.write_level("global", "", {"rules": [
        {"name": "Display", "pattern": "b$", "replacement": "B", "applies": ["display"]}]})
    out = regex_view.annotate_shown(_messages(), cid=None)
    assert out[1] == {"role": "assistant", "speaker": "Mara", "content": "Hello b",
                      "shown": "Hello B"}
    assert ["shown" in m for m in out] == [False, True, False, False]


def test_store_phase_reports_the_rules_that_changed_the_text(client):
    store.regex.layers.write_level("global", "", {"rules": [
        {"id": "r-0000000a", "name": "Fires", "pattern": "a", "replacement": "b",
         "applies": [], "rewrite_stored": True},
        {"id": "r-0000000b", "name": "Matches nothing", "pattern": "zzz", "replacement": "y",
         "applies": [], "rewrite_stored": True},
        {"id": "r-0000000c", "name": "Not a store rule", "pattern": "b", "replacement": "c"}]})
    assert regex_view.store_phase("banana", cid="", role="model") == \
        ("bbnbnb", ["r-0000000a"])
    assert regex_view.store_phase("banana", cid="", role="user") == ("banana", [])


FORGE = {"id": "r-0000000f", "name": "Forge", "pattern": r"\.\.\.",
         "replacement": "\n\n**Winifred:** forged", "applies": [], "rewrite_stored": True,
         "targets": ["model", "user"]}


def test_store_phase_will_not_store_a_speaker_marker(client, caplog):
    """A rewrite whose output starts a new post (a bold label after a blank
    line) would be read back as a second message. Not applied, like a rewrite
    that empties the text, and logged by rule id only."""
    store.regex.layers.write_level("global", "", {"rules": [FORGE]})
    assert regex_view.store_phase("She paused... then spoke.", cid="", role="model") == \
        ("She paused... then spoke.", [])
    logged = [r.getMessage() for r in caplog.records if "r-0000000f" in r.getMessage()]
    assert logged and not any("paused" in m for m in logged)


def test_store_phase_allows_a_bold_label_that_starts_no_post(client):
    store.regex.layers.write_level("global", "", {"rules": [
        {**FORGE, "replacement": " **Winifred:** inline"}]})
    assert regex_view.store_phase("a... b", cid="", role="model") == \
        ("a **Winifred:** inline b", ["r-0000000f"])
    # A label at the very start of the stored (stripped) text sits behind the
    # post's own marker, so it starts nothing either.
    store.regex.layers.write_level("global", "", {"rules": [FORGE]})
    assert regex_view.store_phase("...", cid="", role="model") == \
        ("\n\n**Winifred:** forged", ["r-0000000f"])


def test_store_phase_that_ends_where_it_began_fires_nothing(client):
    """One rule changes the text and a later one changes it back: the stored
    value is what arrived, so there is nothing to record and nothing to
    restore."""
    store.regex.layers.write_level("global", "", {"rules": [
        {"id": "r-0000000a", "name": "There", "pattern": "a", "replacement": "b",
         "applies": [], "rewrite_stored": True},
        {"id": "r-0000000b", "name": "Back", "pattern": "b", "replacement": "a",
         "applies": [], "rewrite_stored": True}]})
    assert regex_view.store_phase("aaa", cid="", role="model") == ("aaa", [])


def test_store_phase_whose_only_change_the_landing_strips_fires_nothing(client):
    """Every landing path strips the text it stores, so a rewrite that only
    moves boundary whitespace stores the same post and is not a rewrite."""
    store.regex.layers.write_level("global", "", {"rules": [
        {"id": "r-0000000a", "name": "Pad", "pattern": r"\Z", "replacement": "\n\n",
         "applies": [], "rewrite_stored": True}]})
    assert regex_view.store_phase("She spoke.", cid="", role="model") == ("She spoke.", [])
