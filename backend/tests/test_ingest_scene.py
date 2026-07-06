import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_scene  # noqa: E402
from grimoire.store import campaigns, worlds  # noqa: E402


def _world(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("ashgrove")


def test_ensure_campaign_creates_once(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid1 = ingest_scene.ensure_campaign("Silver Oath", wid)
    cid2 = ingest_scene.ensure_campaign("Silver Oath", wid)
    assert cid1 == cid2
    assert campaigns.read_campaign(cid1)["meta"]["world"] == wid


def test_manifest_round_trips(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    assert ingest_scene.load_manifest(cid) == {}
    ingest_scene.save_manifest(cid, {"file1-scene01": {"status": "done", "sid": "001--x"}})
    assert ingest_scene.load_manifest(cid) == {"file1-scene01": {"status": "done", "sid": "001--x"}}
