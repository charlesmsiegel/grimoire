import json

from grimoire.store import campaigns, characters, chronicle, greetings, migrations, scenes, worlds
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter


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
