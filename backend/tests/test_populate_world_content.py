from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import populate_world_content as pwc  # noqa: E402
from grimoire.store import characters, entities, greetings, tags, worlds  # noqa: E402


def _world(monkeypatch, tmp_path) -> tuple[str, Path]:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Ashgrove")
    return wid, worlds.world_root(wid)


def test_build_index_lists_existing_entities_tags_and_greetings(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")
    entities.create_entity(root, "lore", "Gangs")
    tags.add_tag(root, "Farmer")
    greetings.create_greeting(root, "Hariel", "hariel", "default", "hi")

    idx = pwc.build_index(root)

    assert {"kind": "locations", "id": "blind-lion", "name": "Blind Lion"} in idx["entities"]
    assert {"kind": "lore", "id": "gangs", "name": "Gangs"} in idx["entities"]
    assert {"id": "farmer", "display_name": "Farmer"} in idx["tags"]
    g = idx["greetings"][0]
    assert g["name"] == "Hariel" and g["character"] == "hariel" and g["version"] == "default"


def test_index_cli_prints_json(monkeypatch, tmp_path, capsys):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")

    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "index", "--world", wid])
    assert pwc.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["entities"][0]["name"] == "Blind Lion"
