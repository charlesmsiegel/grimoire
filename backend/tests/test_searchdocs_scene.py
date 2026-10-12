"""The scene SearchDocument (roadmap 08-C1, `store/searchdocs/`).

Pinned here: the document is deterministic and bounded; it carries no
transcript text; a running summary reaches it only while the app's own
intactness test passes; untimed sid-keyed rows reach only an absorbed scene and
a timed link only one it cannot predate; relationship rows a later undo
reversed are gone; the key moves with this scene's slices and holds against
everything else; and damage degrades rather than raises. Placeholder names only.
"""

import dataclasses
import json

import pytest

from grimoire.store import (
    campaigns,
    characters,
    chronicle,
    commitments,
    compiled,
    entities,
    facts,
    plot,
    regex,
    relationship_history,
    rolling_intact,
    rolling_summary,
    scenes,
    statcache,
    worlds,
)
from grimoire.store.appearances import transitions
from grimoire.store.continuity import doc as continuity_doc
from grimoire.store.searchdocs import rolling, scene, slices


@pytest.fixture(autouse=True)
def _trust_fresh_files(monkeypatch):
    """Files these tests write are seconds old; let the memos trust them."""
    monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 0)


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


def doc(cid, sid):
    inputs = slices.CampaignInputs.load(cid)
    return scene.render(slices.scene_slices(inputs, slices.read_head(cid, sid)))


def _actor(cid, sid, name, role="npc"):
    root = campaigns.campaign_root(cid)
    aid, vid = characters.create_character(root, name, card={"data": {"name": name}})
    transitions.appear(cid, sid, "characters", aid, vid, role, narrate=False)
    return aid, vid


def _rename_actor(cid, aid, vid, name):
    p = characters.require_version(campaigns.campaign_root(cid), aid, vid)
    card = json.loads(p.read_text(encoding="utf-8"))
    card.setdefault("data", {})["name"] = name
    p.write_text(json.dumps(card), encoding="utf-8")


def _location(cid, sid, name):
    eid = entities.create_entity(campaigns.campaign_root(cid), "locations", name)
    scenes.set_location(cid, sid, eid)
    return eid


def _absorb(cid, sid, *, one_line="Mara gives up the ledger.",
            summary="Seraphine corners Mara in the counting room.", keywords=("harbour ledger",)):
    chronicle.absorb(cid, {"id": sid, "one_line": one_line, "summary": summary,
                           "keywords": list(keywords), "cast": [], "location": "", "date": ""})
    scenes.mark_absorbed(cid, sid, one_line, summary)


def _played(cid, title="Saltmarch", posts=4):
    sid = scenes.create_scene(cid, title)
    for i in range(posts):
        scenes.append_message(cid, sid, "user" if i % 2 == 0 else "assistant",
                              f"Post {i} about the counting room.")
    return sid


def _fold(cid, sid, covered=None, text="Mara and the ledger, folded."):
    sc = scenes.read_scene(cid, sid)
    messages = sc["messages"]
    covered = len(messages) if covered is None else covered
    digest = rolling_intact.prefix_digest(cid, sid, messages[:covered], len(messages))
    fdig = rolling_summary.facts_digest(chronicle.scene_facts(cid, sid))
    scenes.set_rolling_summary(cid, sid, text, covered, digest, fdig)


# --- determinism and bounds -----------------------------------------------------

def test_two_builds_and_a_cold_restart_render_identically(cid):
    sid = _played(cid)
    _actor(cid, sid, "Mara")
    _absorb(cid, sid)
    plot.set_movement(cid, "missing-ledger", "The missing ledger", "open",
                      "Mara hides it under the floor", sid)
    first = doc(cid, sid)
    second = doc(cid, sid)
    slices._POOL.clear()
    rolling._POOL.clear()
    third = doc(cid, sid)
    assert first == second == third
    assert "Scene: Saltmarch" in first.text and "Cast: Mara" in first.text
    assert "Threads: The missing ledger: Mara hides it under the floor" in first.text


def test_hand_reordered_ledgers_render_identically(cid):
    sid = _played(cid)
    _absorb(cid, sid)
    facts.record(cid, "The ledger counts the tide tax twice.", "", sid)
    facts.record(cid, "The harbour master owes Mara.", "", sid)
    before = doc(cid, sid)
    p = campaigns.campaign_root(cid) / "facts.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    p.write_text(json.dumps(dict(reversed(list(data.items()))), indent=4), encoding="utf-8")
    after = doc(cid, sid)
    assert (after.text, after.key) == (before.text, before.key)


def test_fact_ids_differing_in_case_order_one_way(cid):
    sid = _played(cid)
    _absorb(cid, sid)
    p = campaigns.campaign_root(cid) / "facts.json"
    rows = {fid: {"text": f"Fact {fid}.", "date": "", "scene": sid, "status": "active",
                  "superseded_by": "", "retired_scene": ""} for fid in ("fB", "fb", "f10", "f2")}
    p.write_text(json.dumps(rows), encoding="utf-8")
    a = doc(cid, sid)
    p.write_text(json.dumps(dict(reversed(list(rows.items())))), encoding="utf-8")
    b = doc(cid, sid)
    assert a.text == b.text and a.meta["facts"]["recorded"] == b.meta["facts"]["recorded"]
    assert a.meta["facts"]["recorded"][:2] == ["f2", "f10"]


def test_a_worst_case_scene_stays_within_its_bounds(cid):
    big = "界𝔘 " * 2000
    sid = _played(cid, title="Saltmarch " + big)
    for i in range(12):
        _actor(cid, sid, f"Mara {i} {big[:300]}", role="player")
    for i in range(6):
        _location(cid, sid, f"Saltmarch harbour {i} {big[:200]}")
    _absorb(cid, sid, one_line=big, summary=big, keywords=[f"kw{i} {big[:80]}" for i in range(20)])
    for i in range(8):
        plot.set_movement(cid, f"t{i}", f"Thread {i} {big[:300]}", "open", big[:900], sid)
        commitments.set_movement(cid, f"c{i}", f"Promise {i} {big[:300]}", "promise",
                                 "open", None, big[:900], sid)
        facts.record(cid, f"Fact {i} {big[:500]}", "", sid)
    relationship_history.append(cid, [
        {"scene": sid, "source": "absorb", "kind": "feeling", "a": "characters:mara",
         "b": f"pcs:p{i}", "label": big[:300], "before": big[:300], "after": big[:300]}
        for i in range(6)])
    d = doc(cid, sid)
    raw = d.text.encode("utf-8")
    assert len(raw) <= scene.MAX_TEXT_BYTES == 5803
    assert d.meta["backstop"] is False and d.meta["bytes"] == len(raw)
    caps = {label: cap for label, _n, _item, cap in scene.LINES}
    caps["Running summary:"] = caps["Summary:"]
    for line in d.text.split("\n"):
        label = next(lbl for lbl in sorted(caps, key=len, reverse=True) if line.startswith(lbl))
        assert len(line[len(label) + 1:].encode("utf-8")) <= caps[label], label
    d.text.encode("utf-8").decode("utf-8")            # every clip on a character boundary
    assert len(d.meta["title"].encode()) <= 160
    assert all(len(k.encode()) <= 32 for k in d.meta["keywords"])
    assert len(d.meta["cast"]) == 12 and len(d.meta["players"]) == 12


def test_meta_lists_are_capped_and_say_so(cid):
    sid = _played(cid)
    _absorb(cid, sid)
    for i in range(70):
        plot.set_movement(cid, f"t{i:02d}", f"Thread {i}", "open", f"beat {i}", sid)
    d = doc(cid, sid)
    assert len(d.meta["threads"]) == scene.MAX_META_REFS
    assert "threads" in d.meta["truncated"]


def test_a_lone_surrogate_renders_digests_and_is_named(cid):
    sid = _played(cid)
    _absorb(cid, sid)
    p = campaigns.campaign_root(cid) / "facts.json"
    p.write_text('{"f1": {"text": "The seal \\ud800 is cracked.", "scene": "' + sid
                 + '", "status": "active"}}', encoding="utf-8")
    d = doc(cid, sid)
    assert "The seal" in d.text and "facts.json" in d.meta["degraded"]
    d.text.encode("utf-8")


# --- what moves the key, and what does not ------------------------------------------

def _key(cid, sid):
    return doc(cid, sid).key


def test_the_key_moves_with_this_scenes_own_slices(cid):
    sid = _played(cid)
    aid, vid = _actor(cid, sid, "Mara")
    eid = _location(cid, sid, "Saltmarch harbour")
    _absorb(cid, sid)
    seen = [_key(cid, sid)]

    def moved():
        k = _key(cid, sid)
        assert k not in seen
        seen.append(k)

    chronicle.absorb(cid, {**chronicle.get_record(cid, sid), "summary": "A new summary."})
    moved()
    chronicle.set_line(cid, sid, "A new line.")
    moved()
    chronicle.absorb(cid, {**chronicle.get_record(cid, sid), "keywords": ["tide tax"]})
    moved()
    plot.set_movement(cid, "ledger", "The ledger", "open", "A beat here.", sid)
    moved()
    _rename_actor(cid, aid, vid, "Mara of Saltmarch")
    moved()
    p = campaigns.campaign_root(cid) / "locations" / f"{eid}.md"
    p.write_text(p.read_text(encoding="utf-8").replace("Saltmarch harbour", "The Old Harbour"),
                 encoding="utf-8")
    moved()
    fid = facts.record(cid, "The tide tax is counted twice.", "", sid)
    moved()
    facts.retire(cid, fid, sid)
    moved()
    relationship_history.append(cid, [{"scene": sid, "source": "absorb", "kind": "feeling",
                                       "a": "characters:mara", "b": "pcs:seraphine",
                                       "label": "Mara → Seraphine", "before": "wary",
                                       "after": "grudging trust"}])
    moved()
    assert "Relationships: Mara → Seraphine: wary -> grudging trust" in doc(cid, sid).text


def test_a_refold_moves_the_key_of_an_open_scene(cid):
    sid = _played(cid, posts=6)
    _fold(cid, sid, text="First fold.")
    a = doc(cid, sid)
    assert a.meta["summary_source"] == "rolling" and "Running summary: First fold." in a.text
    _fold(cid, sid, text="Second fold.")
    assert doc(cid, sid).key != a.key


def test_a_title_rename_moves_the_key(cid):
    sid = _played(cid)
    before = doc(cid, sid)
    new_sid = scenes.rename_scene(cid, sid, "The ledger at Saltmarch")
    after = doc(cid, new_sid)
    assert after.key != before.key and "Scene: The ledger at Saltmarch" in after.text


def test_the_key_holds_for_an_append(cid):
    sid = _played(cid, posts=6)
    _fold(cid, sid)
    before = doc(cid, sid)
    scenes.append_message(cid, sid, "user", "Another post about something else.")
    assert doc(cid, sid) == before


def test_the_key_holds_against_an_unrelated_absorb(cid):
    sid = _played(cid)
    _actor(cid, sid, "Mara")
    _absorb(cid, sid)
    plot.set_movement(cid, "ledger", "The ledger", "open", "Here.", sid)
    before = doc(cid, sid)
    other = _played(cid, title="Elsewhere")
    _absorb(cid, other, one_line="Winifred sails.", summary="Winifred leaves port.")
    plot.set_movement(cid, "voyage", "The voyage", "open", "She sails.", other)
    facts.record(cid, "Winifred owns a ship.", "", other)
    assert doc(cid, sid) == before


def test_a_shared_records_status_moves_meta_and_key_not_text(cid):
    sid = _played(cid)
    _absorb(cid, sid)
    commitments.set_movement(cid, "repay", "Repay Winifred", "promise", "open", None,
                             "Seraphine swears to clear the debt by spring", sid)
    before = doc(cid, sid)
    later = _played(cid, title="Spring")
    _absorb(cid, later, one_line="The debt is cleared.", summary="Paid.")
    commitments.set_movement(cid, "repay", "Repay Winifred", "promise", "fulfilled", None,
                             "The debt is paid", later)
    after = doc(cid, sid)
    assert after.text == before.text
    assert after.key != before.key
    assert after.meta["current_status"] != before.meta["current_status"]


# --- hidden and cut posts never reach the document ---------------------------------

WORD = "Quicksilverlantern"


def _folded_with_word(cid):
    sid = scenes.create_scene(cid, "Saltmarch")
    for i in range(10):
        text = f"Post {i} {WORD}." if i == 7 else f"Post {i}."
        scenes.append_message(cid, sid, "user" if i % 2 == 0 else "assistant", text)
    _fold(cid, sid, covered=10, text=f"They argue; {WORD} appears.")
    d = doc(cid, sid)
    assert WORD in d.text and d.meta["summary_source"] == "rolling"
    return sid


def _gone(cid, sid):
    d = doc(cid, sid)
    assert WORD not in d.text
    assert d.meta["summary_source"] == "none"


def test_hiding_a_covered_post_drops_the_running_summary(cid):
    sid = _folded_with_word(cid)
    scenes.set_excluded(cid, sid, 7, True)
    _gone(cid, sid)


def test_a_cut_into_the_covered_prefix_drops_it(cid):
    sid = _folded_with_word(cid)
    scenes.trim_continuation(cid, sid, 5)
    _gone(cid, sid)


def test_an_edit_of_a_covered_post_drops_it(cid):
    sid = _folded_with_word(cid)
    scenes.edit_message(cid, sid, 3, "Post 3, rewritten.")
    _gone(cid, sid)


def test_a_prompt_rule_over_a_covered_post_drops_it(cid):
    sid = _folded_with_word(cid)
    regex.layers.write_campaign(cid, {"rules": [{"name": "Hide it", "pattern": WORD,
                                                 "replacement": "something"}]})
    _gone(cid, sid)


def test_an_intact_summary_is_memoized_on_the_files_it_read(cid):
    sid = _folded_with_word(cid)
    count = rolling.parses["count"]
    doc(cid, sid)
    assert rolling.parses["count"] == count          # warm: no transcript parse
    scenes.append_message(cid, sid, "user", "A new post.")
    doc(cid, sid)
    assert rolling.parses["count"] > count            # the scene file moved


# --- gates ------------------------------------------------------------------------

def test_relationships_after_a_retcon_render_only_the_last_delta(cid):
    sid = _played(cid)
    _absorb(cid, sid)
    pair = {"kind": "feeling", "a": "characters:mara", "b": "pcs:seraphine",
            "label": "Mara → Seraphine"}
    relationship_history.append(cid, [{**pair, "scene": sid, "source": "absorb",
                                       "before": "wary", "after": "hostile"}])
    relationship_history.append(cid, [{**pair, "a": "pcs:seraphine", "b": "characters:mara",
                                       "scene": sid, "source": "undo",
                                       "before": "hostile", "after": "wary"}])
    relationship_history.append(cid, [{**pair, "scene": sid, "source": "absorb",
                                       "before": "wary", "after": "grudging trust"}])
    text = doc(cid, sid).text
    assert "wary -> grudging trust" in text and "hostile" not in text


def test_a_recycled_id_inherits_nothing(cid):
    sid = _played(cid)
    _absorb(cid, sid)
    plot.set_movement(cid, "ledger", "The ledger", "open", "Hidden.", sid)
    facts.record(cid, "Dead fact.", "", sid)
    relationship_history.append(cid, [{"scene": sid, "source": "absorb", "kind": "bond",
                                       "a": "characters:mara", "b": "pcs:seraphine",
                                       "label": "Mara / Seraphine", "before": "", "after": "allies"}])
    scenes.delete_scene(cid, sid)
    again = _played(cid)
    assert again == sid
    d = doc(cid, again)
    assert d.meta["summary_source"] == "none"
    assert not d.meta["threads"] and not d.meta["facts"]["recorded"]
    assert "Dead fact" not in d.text and "allies" not in d.text and "Mara gives up" not in d.text


def test_a_timed_link_is_admitted_on_an_open_scene_only_when_it_postdates_it(cid):
    sid = _played(cid)
    created = scenes.read_scene(cid, sid)["meta"]["created"]
    continuity_doc.put_link(cid, "l-new", {"a": "thread:x", "b": "thread:y",
                                           "relation": "causes", "created": created,
                                           "scene": sid, "note": ""})
    continuity_doc.put_link(cid, "l-old", {"a": "thread:x", "b": "thread:z",
                                           "relation": "causes", "created": "2000-01-01T00:00:00Z",
                                           "scene": sid, "note": ""})
    assert doc(cid, sid).meta["links"] == ["l-new"]


def test_no_transcript_text_reaches_the_document(cid):
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", f"Only the transcript says {WORD}.")
    before = doc(cid, sid)
    assert WORD not in before.text and before.meta["phase"] == "none"
    regex.layers.write_campaign(cid, {"rules": [{"name": "x", "pattern": "Saltmarch",
                                                 "replacement": "Elsewhere"}]})
    assert doc(cid, sid) == before


def test_damage_degrades_and_names_each_input(cid):
    sid = _played(cid)
    aid, vid = _actor(cid, sid, "Mara")
    _absorb(cid, sid)
    root = campaigns.campaign_root(cid)
    (root / "plot.json").write_text("{ not json", encoding="utf-8")
    (root / "facts.json").write_text("[]", encoding="utf-8")
    characters.require_version(root, aid, vid).write_text("{ nope", encoding="utf-8")
    d = doc(cid, sid)
    assert {"plot.json", "facts.json", f"characters/{aid}"} <= set(d.meta["degraded"])
    assert f"Cast: {aid}" in d.text and "Scene: Saltmarch" in d.text


def test_a_scene_without_identity_has_an_empty_one_and_none_is_written(cid):
    sid = _played(cid)
    p = scenes.paths._scene_path(cid, sid)
    raw = p.read_text(encoding="utf-8")
    stripped = "\n".join(line for line in raw.split("\n") if not line.startswith("identity:"))
    p.write_text(stripped, encoding="utf-8")
    d = doc(cid, sid)
    assert d.meta["identity"] == ""
    assert p.read_text(encoding="utf-8") == stripped


def test_a_campaign_that_is_not_there_raises(cid):
    with pytest.raises(campaigns.CampaignNotFound):
        slices.CampaignInputs.load("no-such-campaign")


def test_the_example_shape(cid):
    sid = _played(cid, title="The ledger at Saltmarch")
    _actor(cid, sid, "Seraphine", role="player")
    _actor(cid, sid, "Mara")
    _location(cid, sid, "Saltmarch")
    _absorb(cid, sid, keywords=("harbour ledger", "tide tax", "Harbour Ledger"))
    lines = doc(cid, sid).text.split("\n")
    assert lines[0] == "Scene: The ledger at Saltmarch"
    assert "Cast: Seraphine (player); Mara" in lines
    assert "Where: Saltmarch" in lines
    assert "Keywords: harbour ledger; tide tax" in lines
    assert "In brief: Mara gives up the ledger." in lines
    assert "Summary: Seraphine corners Mara in the counting room." in lines


def test_the_key_holds_across_a_re_pad(cid):
    """A width re-pad renames the file and moves nothing the document says, so
    neither the key nor the stored payload (which carries no sid) moves."""
    sid = _played(cid)
    _absorb(cid, sid)
    before = doc(cid, sid)
    old = scenes.paths._scene_path(cid, sid)
    padded = "0" + sid
    old.rename(old.with_name(f"{padded}.md"))
    chronicle_path = campaigns.campaign_root(cid) / "chronicle.json"
    data = json.loads(chronicle_path.read_text(encoding="utf-8"))
    data[padded] = data.pop(sid)               # what scene_refs.repoint does on a re-pad
    chronicle_path.write_text(json.dumps(data), encoding="utf-8")
    assert doc(cid, padded) == before


# --- groups (08-S6) ---------------------------------------------------------------

def _group(cid, name, *, members="", leader="", secrecy=""):
    return entities.create_entity(campaigns.campaign_root(cid), "groups", name,
                                  secrecy=secrecy, fields={"members": members, "leader": leader})


def test_a_membership_edit_moves_the_key_and_meta_not_the_text(cid, monkeypatch):
    sid = _played(cid)
    aid, _vid = _actor(cid, sid, "Mara")
    _absorb(cid, sid)
    gid = _group(cid, "Salt Circle")
    before = doc(cid, sid)
    assert before.meta["groups"] == []
    sent = []
    from grimoire.store.inference import embed as inference_embed
    monkeypatch.setattr(inference_embed, "embed_sync", lambda *a, **k: sent.append(a))
    entities.update_entity(campaigns.campaign_root(cid), "groups", gid,
                           fields={"members": f"characters:{aid}"})
    after = doc(cid, sid)
    assert after.text == before.text
    assert after.key != before.key
    assert [g["group"] for g in after.meta["groups"]] == [f"groups:{gid}"]
    assert not sent


def test_a_leader_only_group_is_listed(cid):
    sid = _played(cid)
    aid, _vid = _actor(cid, sid, "Seraphine", role="player")
    gid = _group(cid, "Harbour Watch", leader=f"characters:{aid}")
    groups = doc(cid, sid).meta["groups"]
    assert [(g["group"], g["leader_present"]) for g in groups] == [(f"groups:{gid}", True)]


def test_more_than_sixty_four_groups_are_all_listed(cid):
    sid = _played(cid)
    aid, _vid = _actor(cid, sid, "Mara")
    for i in range(70):
        _group(cid, f"Circle {i:02d}", members=f"characters:{aid}")
    d = doc(cid, sid)
    assert len(d.meta["groups"]) == 70 and "groups" not in d.meta["truncated"]


def test_a_gm_only_group_is_listed_and_marked(cid):
    sid = _played(cid)
    aid, _vid = _actor(cid, sid, "Mara")
    gid = _group(cid, "The Quiet Hand", members=f"characters:{aid}", secrecy="gm-only")
    d = doc(cid, sid)
    assert [(g["group"], g["secrecy"]) for g in d.meta["groups"]] == [(f"groups:{gid}", "gm-only")]
    assert "Quiet Hand" not in d.text


# --- review fixes ---------------------------------------------------------------

def test_every_consulted_ledger_is_an_input_even_when_empty(cid):
    """A ledger that holds nothing for this scene today is still the file
    whose next write can change it, so it is recorded as an input."""
    sid = _played(cid)
    _absorb(cid, sid)
    inputs = slices.CampaignInputs.load(cid)
    sl = slices.scene_slices(inputs, slices.read_head(cid, sid))
    root = campaigns.campaign_root(cid)
    for name in ("facts.json", "plot.json", "commitments.json", "relationship_history.json",
                 "continuity.json", "chronicle.json", "appearances.json"):
        assert root / name in sl.paths, name


def test_an_open_scene_records_every_campaign_input_too(cid):
    """The absorbed gate keeps the untimed ledgers out of an open scene's
    TEXT, not out of its inputs: absorbing it, or repairing a ledger named in
    `degraded`, is a write to one of them. The overlay's control files are
    inputs as well."""
    sid = _played(cid)
    inputs = slices.CampaignInputs.load(cid)
    sl = slices.scene_slices(inputs, slices.read_head(cid, sid))
    root = campaigns.campaign_root(cid)
    for name in ("facts.json", "plot.json", "appearances.json", "continuity.json",
                 "campaign.md", "deleted.json", "detached.json"):
        assert root / name in sl.paths, name


def test_an_unreadable_location_is_named_in_degraded(cid):
    sid = _played(cid)
    eid = _location(cid, sid, "Saltmarch harbour")
    (campaigns.campaign_root(cid) / "locations" / f"{eid}.md").write_bytes(b"\xff\xfe\x00garbage")
    d = doc(cid, sid)
    assert f"locations/{eid}" in d.meta["degraded"]
    assert f"Where: {eid}" in d.text


def test_a_missing_location_is_its_id_and_not_degraded(cid):
    sid = _played(cid)
    eid = _location(cid, sid, "Saltmarch harbour")
    (campaigns.campaign_root(cid) / "locations" / f"{eid}.md").unlink()
    d = doc(cid, sid)
    assert f"locations/{eid}" not in d.meta["degraded"]


def test_the_key_moves_with_build(cid, monkeypatch):
    sid = _played(cid)
    before = doc(cid, sid)
    monkeypatch.setattr(compiled, "build_fingerprint", lambda: "another-build")
    after = doc(cid, sid)
    assert after.key != before.key and after.text == before.text


def test_the_degraded_list_is_bounded(cid):
    sid = _played(cid)
    inputs = slices.CampaignInputs.load(cid)
    sl = slices.scene_slices(inputs, slices.read_head(cid, sid))
    many = tuple(f"characters/c{i:03d}" for i in range(100))
    d = scene.render(dataclasses.replace(sl, degraded=many))
    assert len(d.meta["degraded"]) == scene.MAX_META_REFS
    assert "degraded" in d.meta["truncated"]


def test_every_group_file_the_snapshot_read_is_an_input(cid):
    """A group none of the cast belongs to today is still a file whose next
    edit can make it this scene's."""
    sid = _played(cid)
    _actor(cid, sid, "Mara")
    gid = _group(cid, "Harbour Watch")
    inputs = slices.CampaignInputs.load(cid)
    sl = slices.scene_slices(inputs, slices.read_head(cid, sid))
    assert sl.values["groups"] == []
    assert campaigns.campaign_root(cid) / "groups" / f"{gid}.md" in sl.paths


def test_a_running_summary_records_the_rule_files_it_was_checked_against(cid):
    sid = _folded_with_word(cid)
    inputs = slices.CampaignInputs.load(cid)
    sl = slices.scene_slices(inputs, slices.read_head(cid, sid))
    assert regex.layers.path("campaign", cid) in sl.paths
    assert regex.layers.path("global") in sl.paths
    plain = _played(cid, title="Elsewhere")
    sl = slices.scene_slices(slices.CampaignInputs.load(cid), slices.read_head(cid, plain))
    assert regex.layers.path("campaign", cid) not in sl.paths     # no summary, no test


def test_a_summary_with_moved_facts_reads_no_transcript_and_no_rules(cid):
    sid = _folded_with_word(cid)
    _location(cid, sid, "Saltmarch harbour")          # the first location: facts move
    slices._POOL.clear()
    rolling._POOL.clear()
    count = rolling.parses["count"]
    inputs = slices.CampaignInputs.load(cid)
    sl = slices.scene_slices(inputs, slices.read_head(cid, sid))
    assert rolling.parses["count"] == count
    assert regex.layers.path("global") not in sl.paths
    assert sl.values["rolling"] == ""


def test_statuses_follow_the_refs_the_metadata_kept(cid):
    sid = _played(cid)
    _absorb(cid, sid)
    for i in range(70):
        plot.set_movement(cid, f"a{i:02d}", f"Thread {i}", "open", f"beat {i}", sid)
        commitments.set_movement(cid, f"z{i:02d}", f"Promise {i}", "promise", "open", None,
                                 f"vow {i}", sid)
    meta = doc(cid, sid).meta
    assert len(meta["threads"]) == len(meta["commitments"]) == scene.MAX_META_REFS
    assert set(meta["current_status"]) == set(meta["threads"]) | set(meta["commitments"])


def test_a_long_actor_ref_still_joins_its_groups(cid):
    sid = _played(cid)
    aid, _vid = _actor(cid, sid, "m" * 170)
    assert len(f"characters:{aid}".encode()) > slices.CAPS["ref"]
    gid = _group(cid, "Harbour Watch", members=f"characters:{aid}")
    meta = doc(cid, sid).meta
    assert [g["group"] for g in meta["groups"]] == [f"groups:{gid}"]
    assert all(len(r.encode()) <= slices.CAPS["ref"] for r in meta["cast"])
    assert all(len(r.encode()) <= slices.CAPS["ref"]
               for g in meta["groups"] for r in [g["group"], *g["present"]])


def test_an_unreadable_card_stays_an_input(cid):
    sid = _played(cid)
    aid, vid = _actor(cid, sid, "Mara")
    card = characters.require_version(campaigns.campaign_root(cid), aid, vid)
    card.write_text("{ nope", encoding="utf-8")
    inputs = slices.CampaignInputs.load(cid)
    sl = slices.scene_slices(inputs, slices.read_head(cid, sid))
    assert card in sl.paths and f"characters/{aid}" in sl.degraded


def test_a_lone_surrogate_in_an_actor_ref_degrades_instead_of_raising(cid):
    sid = _played(cid)
    p = campaigns.campaign_root(cid) / "appearances.json"
    p.write_text('{"characters/x\\ud800": {"version": "default", "role": "npc", "scenes": ["'
                 + sid + '"]}}', encoding="utf-8")
    d = doc(cid, sid)
    assert d.meta["degraded"] and len(d.meta["cast"]) == 1
    d.text.encode("utf-8")
