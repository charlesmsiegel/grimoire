"""Listing row memos: unchanged files are parsed once, edits invalidate."""

import os
import time

import pytest

from grimoire.store import statcache
from grimoire.store.campaigns import read as campaigns_read
from grimoire.store.worlds import read as worlds_read


def _age(*paths):
    old = time.time_ns() - 2 * statcache.RACY_WINDOW_NS
    for p in paths:
        os.utime(p, ns=(old, old))


def _age_tree(root):
    _age(*(p for p in root.rglob("*") if p.is_file()))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    (tmp_path / "campaigns" / "saltmarch").mkdir(parents=True)
    (tmp_path / "campaigns" / "saltmarch" / "campaign.md").write_text(
        '---\nname: Saltmarch\nworld: realm\ncreated: \'2026-01-01T00:00:00Z\'\n'
        'updated: \'2026-01-02T00:00:00Z\'\n---\nA pitch paragraph.\n',
        encoding="utf-8")
    (tmp_path / "worlds" / "realm").mkdir(parents=True)
    (tmp_path / "worlds" / "realm" / "world.md").write_text(
        '---\nname: Realm\ncreated: \'2026-01-01T00:00:00Z\'\n'
        'updated: \'2026-01-01T00:00:00Z\'\n---\n', encoding="utf-8")
    _age_tree(tmp_path)
    return tmp_path


def test_campaign_rows_parse_once(store, monkeypatch):
    calls = []
    real = campaigns_read.parse_frontmatter
    monkeypatch.setattr(campaigns_read, "parse_frontmatter",
                        lambda text: calls.append(1) or real(text))
    first = campaigns_read.list_campaigns()
    n = len(calls)
    assert n >= 1
    second = campaigns_read.list_campaigns()
    assert calls[n:] == []            # no re-parse for unchanged files
    assert second == first            # payload identical


def test_campaign_row_invalidates_on_edit(store, monkeypatch):
    campaigns_read.list_campaigns()
    mp = store / "campaigns" / "saltmarch" / "campaign.md"
    mp.write_text(mp.read_text(encoding="utf-8").replace("Saltmarch", "Saltmarch II"),
                  encoding="utf-8")
    _age(mp)
    assert campaigns_read.list_campaigns()[0]["name"] == "Saltmarch II"


def test_world_rows_parse_once(store, monkeypatch):
    calls = []
    real = worlds_read.parse_frontmatter
    monkeypatch.setattr(worlds_read, "parse_frontmatter",
                        lambda text: calls.append(1) or real(text))
    worlds_read.list_worlds()
    n = len(calls)
    worlds_read.list_worlds()
    assert calls[n:] == []
