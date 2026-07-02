from grimoire.store import campaigns, chronicle, migrations, scenes, worlds
from grimoire.store.frontmatter import dump_frontmatter


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
