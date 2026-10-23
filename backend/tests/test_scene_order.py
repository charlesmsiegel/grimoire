"""Equal timestamp scenes keep the same order across copies and filesystems."""

from pathlib import Path

from grimoire.store import campaigns, scenes, worlds
from grimoire.store.scenes import lifecycle


def test_scene_timestamp_ties_have_a_stable_order(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(lifecycle, "now_iso", lambda: "2026-01-01T00:00:00Z")
    cid = campaigns.create_campaign("Saltmarch", worlds.create_world("Realm"))
    ids = [scenes.create_scene(cid, title) for title in ("One", "Two")]
    directory = campaigns.campaign_root(cid) / "scenes"
    original_glob = Path.glob
    files = list(original_glob(directory, "*.md"))
    for order in (files, list(reversed(files))):
        monkeypatch.setattr(Path, "glob", lambda path, pattern, order=order:
                            iter(order) if path == directory and pattern == "*.md" else original_glob(path, pattern))
        assert [row["id"] for row in scenes.list_scenes(cid)] == sorted(ids, reverse=True)
