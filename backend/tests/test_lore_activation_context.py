"""The activation engine as context assembly runs it (spec §2, §5).

`test_lore_activation.py` holds the engine to its rules over plain dicts; these
hold the wiring: the controls a record's frontmatter carries reach the engine,
the posts it is handed are the transcript's own (with their own indices, a
director note included), and `lore_recursion_depth` is a setting the page can
write, the store caps and assembly reads. `test_lore_golden.py` is the other
half -- that none of this moves a prompt for a store that sets nothing.
"""

from __future__ import annotations

import pytest

from grimoire.store import (
    appearances,
    campaigns,
    characters,
    config,
    context,
    entities,
    scenes,
    worlds,
)
from grimoire.store.context import assemble
from grimoire.store.scenes import serialize


@pytest.fixture
def scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    for name in ("Mara", "Winifred"):
        card = characters.blank_card(name)
        card["data"]["description"] = f"{name} of Saltmarch."
        characters.create_character(root, name, "main", card)
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Scene")
    return cid, sid, campaigns.campaign_root(cid)


def _system(cid: str, sid: str) -> str:
    return context.build_messages(cid, sid)[0]["content"]


def _hits(cid: str, sid: str, **kwargs) -> dict:
    return {h.ref: h for h in assemble._assemble(cid, sid, **kwargs)["wi_result"].keyword}


# ---- the setting -------------------------------------------------------------

def test_recursion_setting_rejects_out_of_range(client):
    assert client.get("/api/config").json()["lore_recursion_depth"] == "0"
    for bad in ("9", "-1", "4", "1.5", "two"):
        r = client.put("/api/config", json={"lore_recursion_depth": bad})
        assert r.status_code == 400, bad
        assert r.json()["detail"] == "lore_recursion_depth must be 0-3"
    # Refused before anything was written: neither this key nor another one
    # sent beside it.
    r = client.put("/api/config", json={"lore_recursion_depth": "9", "archive_depth": "7"})
    assert r.status_code == 400
    assert config.read_config()["lore_recursion_depth"] == "0"
    assert client.get("/api/config").json()["archive_depth"] == "3"

    assert client.put("/api/config", json={"lore_recursion_depth": "2"}).status_code == 200
    assert client.get("/api/config").json()["lore_recursion_depth"] == "2"
    assert config.lore_recursion_depth() == 2
    assert client.put("/api/config", json={"lore_recursion_depth": " 3 "}).status_code == 200
    assert config.lore_recursion_depth() == 3

    assert client.put("/api/config", json={"lore_recursion_depth": ""}).status_code == 200
    assert client.get("/api/config").json()["lore_recursion_depth"] == "0"
    assert config.lore_recursion_depth() == 0


def test_hand_edited_recursion_depth_is_capped(client):
    from grimoire import health
    from grimoire.routes import config as config_routes

    config.write_config(lore_recursion_depth="9")       # a hand edit: no route refused it
    assert config.lore_recursion_depth() == 3
    assert config_routes._public_config(config.read_config(),
                                        health.ProviderHealth())["lore_recursion_depth"] == "3"
    assert client.get("/api/config").json()["lore_recursion_depth"] == "3"
    for garbage, effective in (("-2", 0), ("many", 0), ("1", 1)):
        config.write_config(lore_recursion_depth=garbage)
        assert config.lore_recursion_depth() == effective
        assert client.get("/api/config").json()["lore_recursion_depth"] == str(effective)


# ---- the wiring --------------------------------------------------------------

def test_secondary_keys_change_activation_end_to_end(scene):
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Calm harbour", "The harbour is glass-still.",
                           keys="harbour",
                           fields={"secondary_keys": "storm", "key_logic": "not_any"})
    scenes.append_message(cid, sid, "user", "We reach the harbour.")
    assert "glass-still" in _system(cid, sid)
    scenes.append_message(cid, sid, "user", "The harbour heaves in the storm.")
    assert "glass-still" not in _system(cid, sid)


def test_per_entry_scan_depth_reaches_assembly(scene):
    cid, sid, croot = scene
    config.write_config(context_scan_depth="1")
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes in deep winter.",
                           keys="harbour", fields={"scan_depth": "3"})
    entities.create_entity(croot, "lore", "Ferry", "The ferryman skims the toll.",
                           keys="ferry")
    for text in ("The harbour and the ferry.", "Calm.", "Calm again."):
        scenes.append_message(cid, sid, "user", text)
    sys = _system(cid, sid)
    assert "freezes in deep winter" in sys          # its own window reaches post 0
    assert "skims the toll" not in sys              # the global one does not


def test_lore_recursion_depth_reaches_assembly(scene):
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Harbour", "The harbour lies below the lighthouse.",
                           keys="harbour")
    entities.create_entity(croot, "lore", "Lighthouse", "The lighthouse keeper counts ships.",
                           keys="lighthouse")
    scenes.append_message(cid, sid, "user", "We reach the harbour.")
    assert "keeper counts ships" not in _system(cid, sid)
    config.write_config(lore_recursion_depth="1")
    assert "keeper counts ships" in _system(cid, sid)
    hit = _hits(cid, sid)["lore:lighthouse"]
    assert hit.reason["type"] == "recursion" and hit.reason["via"] == "lore:harbour"


def test_narrator_posts_carry_transcript_indices(scene):
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes.", keys="harbour")
    for text in ("Calm.", "Calm.", "The harbour.", "Calm."):
        scenes.append_message(cid, sid, "user", text)
    reason = _hits(cid, sid)["lore:harbour"].reason
    assert reason["type"] == "key" and reason["post"] == 2 and reason["seed"] is False


def test_actor_call_uses_observed_post_indices(scene):
    """An NPC reads only what it observed, but a post is still the post the
    transcript numbers it as -- an inspector reason must point at the real one."""
    cid, sid, croot = scene
    for who in ("mara", "winifred"):
        appearances.appear(cid, sid, "characters", who, "main", "npc", narrate=False)
    appearances.leave(cid, sid, "characters", "mara")
    for text in ("Calm.", "The harbour, while Mara is away.", "Calm.", "Calm."):
        scenes.append_message(cid, sid, "user", text)
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "The harbour bell rings.")    # post 4
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes.", keys="harbour")

    reason = _hits(cid, sid, actor_ref="characters:mara")["lore:harbour"].reason
    assert reason["type"] == "key" and reason["post"] >= 4
    assert reason["post"] == 4
    # The narrator sees the whole transcript; the newest match is the same post.
    assert _hits(cid, sid)["lore:harbour"].reason["post"] == 4


def test_actor_call_window_excludes_unobserved_posts(scene):
    cid, sid, croot = scene
    for who in ("mara", "winifred"):
        appearances.appear(cid, sid, "characters", who, "main", "npc", narrate=False)
    appearances.leave(cid, sid, "characters", "mara")
    scenes.append_message(cid, sid, "user", "The lighthouse burns.")      # unobserved
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "Calm.")
    entities.create_entity(croot, "lore", "Lighthouse", "The keeper counts ships.",
                           keys="lighthouse")
    assert "lore:lighthouse" not in _hits(cid, sid, actor_ref="characters:mara")
    assert _hits(cid, sid)["lore:lighthouse"].reason["post"] == 0


def test_a_stored_director_note_is_a_post_for_the_window_and_the_timers(scene):
    """A director note is stored as a synthetic line (`DIRECTOR_SPEAKER`), and
    it stays in the list the scan window reads: the lore it names arrives, and
    it is one post on the sticky clock like any other (§5.3)."""
    cid, sid, croot = scene
    config.write_config(context_scan_depth="1")
    entities.create_entity(croot, "lore", "Lighthouse", "The lighthouse keeper counts ships.",
                           keys="lighthouse", fields={"sticky": "2"})
    scenes.append_message(cid, sid, "user", "We wait.")                       # post 0
    scenes.append_message(cid, sid, "assistant", "Bring up the lighthouse.",  # post 1
                          speaker=serialize.DIRECTOR_SPEAKER)
    hit = _hits(cid, sid)["lore:lighthouse"]
    assert hit.reason["type"] == "key" and hit.reason["post"] == 1
    assert "keeper counts ships" in _system(cid, sid)
    # The note is not in the history the model reads, only in the window.
    assert "Bring up the lighthouse." not in str(context.build_messages(cid, sid)[1:])

    scenes.append_message(cid, sid, "assistant", "A beam sweeps the water.")  # post 2
    scenes.append_message(cid, sid, "user", "We keep waiting.")               # post 3
    hit = _hits(cid, sid)["lore:lighthouse"]
    assert hit.reason == {"type": "sticky", "from_post": 1, "remaining": 1}
    assert "keeper counts ships" in _system(cid, sid)
    scenes.append_message(cid, sid, "assistant", "Nothing stirs.")            # post 4
    assert "lore:lighthouse" not in _hits(cid, sid)
    assert "keeper counts ships" not in _system(cid, sid)


def test_a_cut_through_the_triggering_post_ends_its_sticky_carry(scene):
    """Timers are derived from the transcript (§5.3), so a cut changes them as
    it changes the posts: with the post that started a carry cut away, the
    same number of posts after it carries nothing."""
    cid, sid, croot = scene
    config.write_config(context_scan_depth="1")
    entities.create_entity(croot, "lore", "Lighthouse", "The lighthouse keeper counts ships.",
                           keys="lighthouse", fields={"sticky": "2"})
    scenes.append_message(cid, sid, "user", "We wait.")                    # post 0
    scenes.append_message(cid, sid, "assistant", "The lighthouse turns.")  # post 1
    scenes.append_message(cid, sid, "user", "We keep waiting.")            # post 2
    hit = _hits(cid, sid)["lore:lighthouse"]
    assert hit.reason == {"type": "sticky", "from_post": 1, "remaining": 2}
    assert "keeper counts ships" in _system(cid, sid)

    assert scenes.delete_from(cid, sid, 1) == 2
    scenes.append_message(cid, sid, "assistant", "The water turns.")       # post 1
    scenes.append_message(cid, sid, "user", "We keep waiting.")            # post 2
    assert "lore:lighthouse" not in _hits(cid, sid)
    assert "keeper counts ships" not in _system(cid, sid)


def test_a_director_turn_seeds_activation_as_the_seed(scene):
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Ferry", "The ferryman skims the toll.", keys="ferry")
    scenes.append_message(cid, sid, "user", "Calm.")
    reason = _hits(cid, sid, wi_seed="Take the ferry.")["lore:ferry"].reason
    assert reason["seed"] is True and reason["post"] is None
    assert "skims the toll" in context.build_director_messages(
        cid, sid, "Take the ferry.")[0]["content"]


def test_controls_and_refs_ride_on_every_entry(scene):
    cid, sid, croot = scene
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    entities.create_entity(croot, "items", "Lantern", "A brass lantern.", keys="unsaid",
                           fields={"holder": "characters:mara", "priority": "7"})
    entities.create_entity(croot, "lore", "Oil", "The lantern burns whale oil.",
                           owners="items:lantern")
    scenes.append_message(cid, sid, "user", "Calm.")
    result = assemble._assemble(cid, sid)["wi_result"]
    assert result.present["items:lantern"] == {"type": "held_by", "via": "characters:mara"}
    oil = {h.ref: h for h in result.keyword}["lore:oil"]
    assert oil.reason["owner"] == "items:lantern"
    assert "whale oil" in _system(cid, sid)
