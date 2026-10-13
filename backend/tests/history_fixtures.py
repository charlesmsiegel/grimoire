"""Synthetic history fixtures shared by the core, query and merge suites."""

import pytest

from grimoire.store import (
    campaigns,
    characters,
    chronicle,
    config,
    entities,
    scenes,
    statcache,
    worlds,
)
from grimoire.store.appearances import transitions


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 0)
    wid = worlds.create_world("Realm")
    campaign = campaigns.create_campaign("Saltmarch", wid)
    config.write_config(history_recall_depth="3")
    return campaign


def actor(cid, sid, name):
    root = campaigns.campaign_root(cid)
    aid, vid = characters.create_character(root, name, card={"data": {"name": name}})
    transitions.appear(cid, sid, "characters", aid, vid, "npc", narrate=False)
    return aid, vid


def appear(cid, sid, aid, vid):
    transitions.appear(cid, sid, "characters", aid, vid, "npc", narrate=False)


def played(cid, title="Saltmarch", text="Mara gives Seraphine the silver ledger."):
    sid = scenes.create_scene(cid, title)
    scenes.append_message(cid, sid, "user", text)
    return sid


def absorbed(cid, sid, summary="Mara gives Seraphine the silver ledger."):
    chronicle.absorb(
        cid,
        {
            "id": sid,
            "one_line": summary,
            "summary": summary,
            "keywords": ["silver ledger"],
            "cast": [],
            "location": "",
            "date": "",
        },
    )
    scenes.mark_absorbed(cid, sid, summary, summary)


def location(cid, sid, *, secrecy="public"):
    eid = entities.create_entity(
        campaigns.campaign_root(cid), "locations", "Saltmarch", secrecy=secrecy
    )
    scenes.set_location(cid, sid, eid)
    return eid
