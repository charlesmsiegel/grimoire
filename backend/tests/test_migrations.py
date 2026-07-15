import json

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, chronicle, greetings, migrations, overlay, scenes, sync, worlds
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter


def _unbake_card(path, description):
    """Overwrite a card file with unbaked content, bypassing the now-baking
    write paths -- simulates a file saved before {{char}} was baked at write
    time (or before this feature existed at all)."""
    card = json.loads(path.read_text(encoding="utf-8"))
    card["data"]["description"] = description
    path.write_text(json.dumps(card), encoding="utf-8")


def _unbake_greeting(path, body):
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def _legacy_scene(cid, stem, title, created, time_history=""):
    d = campaigns.campaign_root(cid) / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"title": title, "model": "m", "created": created, "updated": created}
    if time_history:
        meta["time_history"] = time_history
    (d / f"{stem}.md").write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def test_migrates_legacy_scenes_in_created_order(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _legacy_scene(cid, "2026-06-28-second", "Second", "2026-06-28T10:00:00Z")
    _legacy_scene(cid, "2026-06-27-first", "First", "2026-06-27T10:00:00Z",
                  time_history="1023-05-12,1023-05-13T09:00")
    chronicle.absorb(cid, {"id": "2026-06-27-first", "one_line": "x", "summary": "", "keywords": []})

    migrations.migrate_scene_ids()

    d = campaigns.campaign_root(cid) / "scenes"
    assert sorted(p.stem for p in d.glob("*.md")) == [
        "001--1023-05-12--first",   # dated: start date from time_history[0], time stripped
        "002--second",              # undated: no date section
    ]
    assert "001--1023-05-12--first" in chronicle.read_chronicle(cid)


def test_migration_is_idempotent_and_continues_numbering(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scenes.create_scene(cid, "Already New")   # 001--already-new
    _legacy_scene(cid, "2026-06-28-old", "Old", "2026-06-28T10:00:00Z")
    migrations.migrate_scene_ids()
    d = campaigns.campaign_root(cid) / "scenes"
    assert sorted(p.stem for p in d.glob("*.md")) == ["001--already-new", "002--old"]
    before = sorted(p.stem for p in d.glob("*.md"))
    migrations.migrate_scene_ids()            # second run: no changes
    assert sorted(p.stem for p in d.glob("*.md")) == before


def test_migration_handles_empty_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    migrations.migrate_scene_ids()  # no campaigns — must not raise


def test_bake_char_macros_backfills_legacy_unbaked_content(monkeypatch, tmp_path):
    # #137 P1: content saved before {{char}} was baked at write time (or from
    # before this feature existed at all) must get baked on the next startup
    # -- scene-time substitution no longer resolves {{char}} at all.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    cid, vid = characters.create_character(wroot, "Seraphine", "default")
    gid = greetings.create_greeting(wroot, "Open", cid, vid, body="Hello.")

    # simulate pre-fix files on disk, bypassing the now-baking write paths
    card_path = wroot / "characters" / cid / f"{vid}.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["data"]["description"] = "{{char}} keeps the harbor."
    card_path.write_text(json.dumps(card), encoding="utf-8")
    greeting_path = wroot / "greetings" / f"{gid}.md"
    meta, _ = parse_frontmatter(greeting_path.read_text(encoding="utf-8"))
    greeting_path.write_text(dump_frontmatter(meta, "{{char}} arrives."), encoding="utf-8")

    migrations.bake_char_macros()

    assert characters.read_card(wroot, cid, vid)["data"]["description"] == "Seraphine keeps the harbor."
    assert greetings.read_greeting(wroot, gid)["body"].strip() == "Seraphine arrives."

    # idempotent: a second run touches nothing further (no crash, same content)
    migrations.bake_char_macros()
    assert characters.read_card(wroot, cid, vid)["data"]["description"] == "Seraphine keeps the harbor."
    assert greetings.read_greeting(wroot, gid)["body"].strip() == "Seraphine arrives."


def test_bake_char_macros_handles_empty_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    migrations.bake_char_macros()  # no worlds/campaigns — must not raise


def test_bake_char_macros_marker_skips_later_startups(monkeypatch, tmp_path):
    # scalability: a full card/greeting scan on every startup isn't free for a
    # large store, so a marker file makes this a true one-time migration.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    cid, vid = characters.create_character(wroot, "Seraphine", "default")
    migrations.bake_char_macros()
    assert (tmp_path / ".char_macros_baked").exists()

    # unbaked content appearing after the marker was written (e.g. a restored
    # backup) is deliberately left alone -- the marker means "never scan again"
    card_path = wroot / "characters" / cid / f"{vid}.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["data"]["description"] = "{{char}} keeps the harbor."
    card_path.write_text(json.dumps(card), encoding="utf-8")
    migrations.bake_char_macros()
    assert characters.read_card(wroot, cid, vid)["data"]["description"] == "{{char}} keeps the harbor."


def test_bake_char_macros_repairs_materialized_greeting_baseline(monkeypatch, tmp_path):
    # #137 P1: a materialized campaign greeting that was never actually
    # diverged from its world source must not show a spurious "conflict"
    # after both copies get mechanically baked the same way.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    cid_, vid = characters.create_character(wroot, "Seraphine", "default")
    gid = greetings.create_greeting(wroot, "Open", cid_, vid, body="placeholder")
    _unbake_greeting(wroot / "greetings" / f"{gid}.md", "{{char}} arrives.")

    cid = campaigns.create_campaign("Run", wid)
    overlay.materialize_entity(cid, "greetings", gid)  # unmodified copy, base == unbaked hash

    migrations.bake_char_macros()

    assert overlay.read_greeting(cid, gid)["body"].strip() == "Seraphine arrives."
    assert sync.incoming(cid) == []  # no spurious conflict from the mechanical bake


def test_bake_char_macros_repairs_unpicked_actor_baseline(monkeypatch, tmp_path):
    # same as above, for a materialized-but-unlocked whole-actor (manifest
    # baseline, not appearances.json).
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, vid = characters.create_character(wroot, "Seraphine", "default")
    _unbake_card(wroot / "characters" / char_id / f"{vid}.json", "{{char}} keeps the harbor.")

    cid = campaigns.create_campaign("Run", wid)
    overlay.materialize_actor(cid, "characters", char_id)  # unmodified copy

    migrations.bake_char_macros()

    croot = campaigns.campaign_root(cid)
    assert characters.read_card(croot, char_id, vid)["data"]["description"] == "Seraphine keeps the harbor."
    assert sync.incoming(cid) == []


def test_bake_char_macros_repairs_locked_actor_version_baseline(monkeypatch, tmp_path):
    # same as above, for a locked version (appearances.json base).
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, vid = characters.create_character(wroot, "Seraphine", "default")
    _unbake_card(wroot / "characters" / char_id / f"{vid}.json", "{{char}} keeps the harbor.")

    cid = campaigns.create_campaign("Run", wid)
    overlay.materialize_actor(cid, "characters", char_id)
    ap.pick_version(cid, "characters", char_id, vid)

    migrations.bake_char_macros()

    croot = campaigns.campaign_root(cid)
    assert characters.read_card(croot, char_id, vid)["data"]["description"] == "Seraphine keeps the harbor."
    assert sync.incoming(cid) == []


def test_bake_char_macros_does_not_mask_a_real_pre_existing_conflict(monkeypatch, tmp_path):
    # a genuine divergence (campaign edited independently of the world) must
    # still show as a conflict after baking -- the baseline repair only fires
    # when both sides land on the same content.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, vid = characters.create_character(wroot, "Seraphine", "default")
    _unbake_card(wroot / "characters" / char_id / f"{vid}.json", "{{char}} keeps the harbor.")

    cid = campaigns.create_campaign("Run", wid)
    overlay.materialize_actor(cid, "characters", char_id)
    croot = campaigns.campaign_root(cid)
    # the campaign copy diverges independently, unrelated to {{char}}
    _unbake_card(croot / "characters" / char_id / f"{vid}.json", "A campaign-only edit.")

    migrations.bake_char_macros()

    # the campaign's own edit must survive baking untouched (no {{char}} in it)
    assert characters.read_card(croot, char_id, vid)["data"]["description"] == "A campaign-only edit."
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", char_id)]["status"] == "conflict"
