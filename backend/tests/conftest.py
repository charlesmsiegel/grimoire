"""Shared fixtures: the route-test HTTP client, and the mechanics-Phase5
sheet/audit/absorb store fixtures."""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.store import appearances, campaigns, characters, modules, scenes, sheets, worlds
from tests.llm_fakes import FakeOpenRouter


@pytest.fixture
def client(monkeypatch, tmp_path):
    """An app over a throwaway store, with the gateway faked.

    One copy, here, rather than the identical one three route-test files each
    carried: `store` is reloaded against this test's `GRIMOIRE_HOME`, so the
    fixture has to run *before* the app is built, and every route suite needs
    exactly that. A suite wanting a different fake overrides
    `routes.get_llm` again in the test itself, or declares its own `client`
    fixture, which still shadows this one.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


_WARRIOR_FIELDS = [
    {"key": "hp", "label": "Hit Points", "type": "resource", "max": 12},
    {"key": "xp", "label": "Experience", "type": "resource", "max": 999},
    {"key": "athletics", "label": "Athletics", "type": "number",
     "default": 2, "min": 0, "max": 5},
    {"key": "wounds", "label": "Wounds", "type": "track", "max": 5},
    {"key": "conditions", "label": "Conditions", "type": "list"},
    {"key": "notes", "label": "Notes", "type": "text"},
]

SHEETS_DEF = {
    "groups": {},
    "sheet_types": {
        "warrior": {
            "label": "Warrior",
            "kind": "characters",
            "groups": [],
            "fields": _WARRIOR_FIELDS,
        },
        # A second type sharing "warrior"'s shape -- exercises a type-change
        # write (sheets.write's "different sheet_type" path) without needing
        # a dedicated field set.
        "adventurer": {
            "label": "Adventurer",
            "kind": "characters",
            "groups": [],
            "fields": _WARRIOR_FIELDS,
        },
    },
}


@pytest.fixture
def user_pack_path(monkeypatch, tmp_path):
    """A module pack that lives in the user library (GRIMOIRE_HOME/modules),
    so tests can mutate sheets.json in place (schema_stamp mtime tests)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = modules.create_module("Test Pack")
    root = modules.user_dir() / mid
    (root / "sheets.json").write_text(json.dumps(SHEETS_DEF), encoding="utf-8")
    return root


@pytest.fixture
def cid_with_sheet(user_pack_path):
    mid = user_pack_path.name
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=mid)
    sheets.write(cid, "characters", "mara", "warrior",
                 {"hp": {"current": 12, "max": 12}}, expected=None)
    return cid


@pytest.fixture
def scene_with_sheeted_cast(user_pack_path):
    """A scene with one present, sheeted cast member (mara) whose baseline
    was captured at scene creation -- the ground every materialize test
    stands on."""
    mid = user_pack_path.name
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Mara", "default", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid, module=mid)
    sheets.write(cid, "characters", "mara", "warrior",
                 {"hp": {"current": 12, "max": 12}, "xp": {"current": 0, "max": 999},
                  "wounds": 0, "conditions": []}, expected=None)
    sid = scenes.create_scene(cid, "Landing")           # captures baseline
    appearances.appear(cid, sid, "characters", "mara", "default", "npc")
    return cid, sid
