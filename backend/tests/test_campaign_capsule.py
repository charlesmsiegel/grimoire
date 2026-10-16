"""Campaign-side capsule and tags for an emergent character (#62).

An NPC who walked on mid-scene and was never in the library (#60) has a card
too thin to write a capsule from, so the draft reads the first scene that
character appears in. These pin the contract:

  * the draft is PREVIEW ONLY -- generating writes neither the capsule nor the
    tags, and the caller saves through PUT (the same rule as every other draft);
  * the capsule is written campaign-side, so it lands on the emergent character
    and nowhere else;
  * the model's reply is read by its labels, tolerantly: a reply with no label
    is still a capsule, and the tags are cleaned, de-duplicated and capped.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests.draft_runs import post as draft_post
from tests.llm_fakes import FakeOpenRouterComplete

MARKER = "The gate-warden hands the iron key across the threshold"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


def _campaign(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]


def _emergent(client, cid, name="Mara"):
    """An NPC who exists only in this campaign (#60): no world counterpart."""
    made = client.post(f"/api/campaigns/{cid}/characters", json={"name": name}).json()
    return made["character"], made["version"]


def _answers(client, reply):
    fake = FakeOpenRouterComplete(reply)
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


# ---- the reply reader -------------------------------------------------------

def test_a_labelled_reply_splits_into_capsule_and_tags():
    out = store.taglines.parse_capsule_output(
        "Capsule: A gate-warden who keeps every promise.\nTags: loyal, wary, Iron Gate")
    assert out == {"capsule": "A gate-warden who keeps every promise.",
                   "tags": ["loyal", "wary", "Iron Gate"]}


def test_an_unlabelled_reply_is_read_as_the_capsule():
    out = store.taglines.parse_capsule_output("A gate-warden who keeps every promise.")
    assert out == {"capsule": "A gate-warden who keeps every promise.", "tags": []}


def test_tags_are_cleaned_deduplicated_and_capped():
    reply = ("Capsule: Someone.\n"
             'Tags: "loyal.", Loyal, , wary, wary, ' + ", ".join(f"t{i}" for i in range(20)))
    tags = store.taglines.parse_capsule_output(reply)["tags"]
    assert tags[:3] == ["loyal", "wary", "t0"]
    assert len(tags) == store.taglines.TAGS_MAX
    assert len({t.casefold() for t in tags}) == len(tags)


def test_an_overlong_tag_is_dropped_rather_than_stored():
    long_tag = "x" * (store.taglines.TAG_MAX_CHARS + 1)
    out = store.taglines.parse_capsule_output(f"Capsule: Someone.\nTags: ok, {long_tag}")
    assert out["tags"] == ["ok"]


def test_a_blank_reply_reads_as_nothing():
    assert store.taglines.parse_capsule_output("  \n ") == {"capsule": "", "tags": []}


# ---- the prompt ------------------------------------------------------------

def test_the_prompt_carries_the_first_scene_and_is_bounded():
    long = "word " * 5000
    messages = store.taglines.build_capsule_prompt({"name": "Mara"}, long)
    user = messages[1]["content"]
    assert "first scene" in user
    assert len(user) < store.taglines.CAPSULE_SOURCE_CHARS + 500


def test_the_prompt_without_a_scene_says_nothing_about_one():
    user = store.taglines.build_capsule_prompt({"name": "Mara"}, "")[1]["content"]
    assert "first scene" not in user
    assert "Mara" in user


# ---- the route -------------------------------------------------------------

def test_generate_reads_the_first_scene_the_character_appears_in(client):
    cid = _campaign(client)
    aid, vid = _emergent(client, cid)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The gate"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", MARKER)
    store.appearances.transitions.appear(cid, sid, "characters", aid, vid, "npc")
    fake = _answers(client, "Capsule: A gate-warden.\nTags: loyal")

    resp = draft_post(client, f"/api/campaigns/{cid}/characters/{aid}/capsule/generate")

    assert resp.status_code == 200
    assert resp.json() == {"capsule": "A gate-warden.", "tags": ["loyal"]}
    assert MARKER in fake.messages[-1]["content"]


def test_generate_is_preview_only(client):
    cid = _campaign(client)
    aid, _ = _emergent(client, cid)
    _answers(client, "Capsule: A gate-warden.\nTags: loyal")

    assert draft_post(client, f"/api/campaigns/{cid}/characters/{aid}/capsule/generate"
                      ).status_code == 200

    root = store.overlay.char_root(cid, aid)
    assert store.taglines.read(root, aid) == ""


def test_the_capsule_is_saved_campaign_side_only(client):
    cid = _campaign(client)
    aid, _ = _emergent(client, cid)

    resp = client.put(f"/api/campaigns/{cid}/characters/{aid}/tagline",
                      json={"tagline": "A gate-warden who keeps every promise."})

    assert resp.json()["ok"] is True
    root = store.overlay.char_root(cid, aid)
    assert store.taglines.read(root, aid) == "A gate-warden who keeps every promise."


def test_capsule_routes_404_on_an_unknown_character(client):
    cid = _campaign(client)
    assert client.post(f"/api/campaigns/{cid}/characters/nope/capsule/generate"
                       ).status_code == 404
    assert client.put(f"/api/campaigns/{cid}/characters/nope/tagline",
                      json={"tagline": "x"}).status_code == 404


def test_the_saved_capsule_reads_back_through_the_campaign(client):
    cid = _campaign(client)
    aid, _ = _emergent(client, cid)
    assert client.get(f"/api/campaigns/{cid}/characters/{aid}/tagline").json() == {"tagline": ""}

    client.put(f"/api/campaigns/{cid}/characters/{aid}/tagline",
               json={"tagline": "A gate-warden who keeps every promise."})

    assert client.get(f"/api/campaigns/{cid}/characters/{aid}/tagline").json() == {
        "tagline": "A gate-warden who keeps every promise."}


def test_the_capsule_read_404s_on_an_unknown_character(client):
    cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/characters/nope/tagline").status_code == 404


ALT_MARKER = "Keeps the salt-ledger for the Saltmarch wharf"


def test_generate_drafts_from_the_version_being_edited(client):
    cid = _campaign(client)
    aid, _ = _emergent(client, cid)
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Mara", "description": ALT_MARKER}}
    alt = client.post(f"/api/campaigns/{cid}/characters/{aid}/versions",
                      json={"name": "alt", "card": card}).json()["version"]
    fake = _answers(client, "Capsule: A ledger-keeper.\nTags: careful")

    resp = draft_post(client,
                      f"/api/campaigns/{cid}/characters/{aid}/capsule/generate?version={alt}")

    assert resp.status_code == 200
    assert ALT_MARKER in fake.messages[-1]["content"]


def test_generate_404s_on_a_version_the_character_does_not_have(client):
    cid = _campaign(client)
    aid, _ = _emergent(client, cid)
    resp = client.post(f"/api/campaigns/{cid}/characters/{aid}/capsule/generate?version=nope")
    assert resp.status_code == 404


def test_a_capsule_for_an_unknown_character_creates_no_folder(client):
    cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/characters/ghost/tagline", json={"tagline": "x"})
    assert not (store.overlay.croot_of(cid) / "characters" / "ghost").exists()


def test_a_capsule_on_an_inherited_character_leaves_it_inherited(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    aid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Seraphine", "version_name": "main"}).json()["character"]

    resp = client.put(f"/api/campaigns/{cid}/characters/{aid}/tagline",
                      json={"tagline": "A snowleopardgirl."})

    assert resp.json()["ok"] is True
    croot = store.overlay.croot_of(cid)
    assert not (croot / "characters" / aid / "character.md").exists()
    assert store.overlay.tagline(cid, aid) == "A snowleopardgirl."


def test_generate_reads_the_first_scene_after_the_character_has_left_it(client):
    cid = _campaign(client)
    aid, vid = _emergent(client, cid)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The gate"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", MARKER)
    store.appearances.transitions.appear(cid, sid, "characters", aid, vid, "npc")
    store.appearances.transitions.leave(cid, sid, "characters", aid)
    fake = _answers(client, "Capsule: A gate-warden.\nTags: loyal")

    resp = draft_post(client, f"/api/campaigns/{cid}/characters/{aid}/capsule/generate")

    assert resp.status_code == 200
    assert MARKER in fake.messages[-1]["content"]


def test_a_campaign_override_governs_its_capsule_draft(client):
    """The draft is a campaign-specific prompt, so the campaign's own model
    settings must apply to it -- the tagline route is campaign-scoped for this
    reason, while its world call sites (no campaign) are unaffected."""
    from grimoire.store.inference import settings as inference_settings

    cid = _campaign(client)
    aid, _ = _emergent(client, cid)
    inference_settings.write("campaign", cid, {"roles": {"fast": {"selection": {
        "provider": "openrouter", "model": "vendor/capsule-fast"}}}})
    fake = _answers(client, "Capsule: A gate-warden.\nTags: loyal")

    resp = draft_post(client, f"/api/campaigns/{cid}/characters/{aid}/capsule/generate")

    assert resp.status_code == 200
    assert fake.target.model == "vendor/capsule-fast"


def test_a_campaign_capsule_on_an_inherited_character_is_searchable(client):
    """The campaign's capsule is written beside the character without a card of
    its own, and search reads only what is on disk, so the capsule must be
    indexed by itself or the words the campaign wrote about the character would
    never find it."""
    from grimoire.store import search

    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    aid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Seraphine", "version_name": "main"}).json()["character"]
    client.put(f"/api/campaigns/{cid}/characters/{aid}/tagline",
               json={"tagline": "A snowleopardgirl who keeps the tide ledger."})

    hits = search.search("snowleopardgirl", scope="campaign", root=cid)["hits"]

    assert [(h["kind"], h["id"]) for h in hits] == [("characters", aid)]


def test_the_excerpt_starts_at_the_entrance_not_the_scene_head(client):
    """A character who walks on after more than the excerpt's worth of scene
    still gets the entrance: the excerpt is taken from where they arrived."""
    cid = _campaign(client)
    aid, vid = _emergent(client, cid)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The gate"}).json()["id"]
    for _ in range(6):
        store.scenes.append_message(cid, sid, "user", "filler " * 300)
    store.scenes.append_message(cid, sid, "user", MARKER)   # the introducing turn
    store.appearances.transitions.appear(cid, sid, "characters", aid, vid, "npc")
    store.scenes.append_message(cid, sid, "user", "filler " * 300)
    fake = _answers(client, "Capsule: A gate-warden.\nTags: loyal")

    resp = draft_post(client, f"/api/campaigns/{cid}/characters/{aid}/capsule/generate")

    assert resp.status_code == 200
    assert MARKER in fake.messages[-1]["content"]


def test_an_inherited_capsule_row_carries_the_characters_display_name(client):
    """The row's name is what a query's name terms match, so it has to be the
    character's name as the library holds it, not the slug the id was made from."""
    from grimoire.store import search

    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    aid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Lady Seraphine", "version_name": "main"}).json()["character"]
    client.put(f"/api/campaigns/{cid}/characters/{aid}/tagline",
               json={"tagline": "A snowleopardgirl who keeps the tide ledger."})

    hits = search.search("lady snowleopardgirl", scope="campaign", root=cid)["hits"]

    assert [(h["id"], h["name"]) for h in hits] == [(aid, "Lady Seraphine")]


def test_a_blank_capsule_save_answers_the_sentence_the_campaign_now_shows(client):
    """Blanking the campaign's copy of an inherited capsule lets the library's
    sentence show through, so the save's answer is that sentence, not the blank
    that was sent."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    aid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Seraphine", "version_name": "main"}).json()["character"]
    client.put(f"/api/worlds/{wid}/characters/{aid}/tagline",
               json={"tagline": "A gate-warden of the old wall."})
    client.put(f"/api/campaigns/{cid}/characters/{aid}/tagline",
               json={"tagline": "Something of the campaign's own."})

    resp = client.put(f"/api/campaigns/{cid}/characters/{aid}/tagline", json={"tagline": ""})

    assert resp.json() == {"ok": True, "tagline": "A gate-warden of the old wall."}


def test_an_unreadable_campaign_record_still_searches_the_inherited_capsule(client, tmp_path):
    """A hand-edited or synced campaign record that is not valid text must not
    take the whole search down; the row falls back to the character's id."""
    from grimoire.store import search

    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    aid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Seraphine", "version_name": "main"}).json()["character"]
    client.put(f"/api/campaigns/{cid}/characters/{aid}/tagline",
               json={"tagline": "A snowleopardgirl who keeps the tide ledger."})
    (store.campaigns.paths.campaign_root(cid) / "campaign.md").write_bytes(b"---\nworld: \xff\xfe\n---\n")

    hits = search.search("snowleopardgirl", scope="campaign", root=cid)["hits"]

    assert [(h["id"], h["name"]) for h in hits] == [(aid, aid)]
