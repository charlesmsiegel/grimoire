"""The `overview` hook (spec 05-S5, section 6.2): 04's `warm_paths`, wrapped.

A `tmp_path` store with placeholder names (Realm, Saltmarch) and the compiled
cache on. `overview.continuity_summary` lost spec 03's section 14 measurement,
so it is registered for these tests only, the way 04's own tests reach it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.store import cache_sync, campaigns, commitments, compiled, readstats, worlds
from grimoire.store.overview import kinds as overview_kinds
from grimoire.store.overview import read as overview_read
from tests import cache_sync_fixtures as fx

cache = fx.cache
SRC = Path(__file__).resolve().parents[1] / "src"
LEDGERS = ("plot.json", "commitments.json", "events.json", "continuity.json",
           "continuity_candidates.json")


@pytest.fixture
def home(tmp_path, monkeypatch, cache):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def continuity(monkeypatch):
    kind = compiled.OVERVIEW_CONTINUITY
    monkeypatch.setitem(compiled.KINDS, kind.name, kind)
    return kind


def _campaign():
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def _rows(home, path):
    with compiled.batch(home) as request:
        rows = request.materialized([path])[path]
        request.disabled = True
    return rows


def _rel(home, path):
    return Path(path).relative_to(home).as_posix()


def _commit(cid, title):
    commitments.set_movement(cid, "m-oath", title, "promise", "open", None,
                             "Mara swore it at the harbour.", "arrival")


def test_the_hook_runs_between_files_and_vectors():
    assert [hook.name for hook in cache_sync.HOOKS] == ["files", "overview", "vectors"]
    assert all(cache_sync.OVERVIEW.owns(name) for name in compiled.OVERVIEW_CANDIDATES)
    assert not cache_sync.OVERVIEW.owns("vector:lore-entry:x")


def test_five_ledger_edits_reach_warm_paths_in_one_call(home, monkeypatch):
    cid = _campaign()
    root = campaigns.campaign_root(cid)
    rels = []
    with compiled.batch(home) as request:
        for name in LEDGERS:
            path = root / name
            path.write_text("{}\n", encoding="utf-8")
            request.mark_materialized(path, compiled.OVERVIEW_CONTINUITY.name)
            rels.append(_rel(home, path))
    calls = []
    monkeypatch.setattr(cache_sync.overview_warm, "warm_paths",
                        lambda paths: calls.append(list(paths)) or dict.fromkeys(paths, "warmed"))
    report = cache_sync.sync_paths(rels, mode="explicit")
    assert calls == [sorted(rels)]
    assert report.hooks["overview"].get("built") == 5 and report.ok
    assert {p.status for p in report.paths} == {"refreshed"}


def test_an_edited_ledger_warms_the_continuity_summary_the_next_read_hits(home, continuity):
    cid = _campaign()
    _commit(cid, "Keep the harbour lit")
    overview_read.continuity_summary(cid)                 # the read that records the rows
    ledger = overview_kinds.continuity_paths(cid)["commitments"]
    assert [row.kind for row in _rows(home, ledger)] == [continuity.name]
    _commit(cid, "Keep the harbour lit, and the bell rung")
    report = cache_sync.sync_paths([_rel(home, ledger)], mode="explicit")
    assert report.paths[0].kinds == {continuity.name: "built"} and report.ok
    with readstats.collect() as counters:
        summary = overview_read.continuity_summary(cid)
    assert counters.total("overview.computed.continuity") == 0     # a hit, never a compute
    assert summary == overview_kinds.continuity_live(cid)


def test_a_dry_run_plans_the_overview_and_stores_nothing(home, monkeypatch):
    cid = _campaign()
    path = campaigns.campaign_root(cid) / "plot.json"
    path.write_text("{}\n", encoding="utf-8")
    with compiled.batch(home) as request:
        request.mark_materialized(path, compiled.OVERVIEW_CONTINUITY.name)
    monkeypatch.setattr(cache_sync.overview_warm, "warm_paths",
                        lambda paths: pytest.fail("a dry run stored an overview artifact"))
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit", dry_run=True)
    assert report.paths[0].kinds == {compiled.OVERVIEW_CONTINUITY.name: "planned"}


@pytest.mark.parametrize(("answer", "outcome", "status"), [
    ("none", "skipped", "current"),
    ("failed", "failed:not_saved", "failed"),
])
def test_warm_paths_answers_map_onto_outcomes(home, monkeypatch, answer, outcome, status):
    cid = _campaign()
    path = campaigns.campaign_root(cid) / "events.json"
    path.write_text("{}\n", encoding="utf-8")
    with compiled.batch(home) as request:
        request.mark_materialized(path, compiled.OVERVIEW_CONTINUITY.name)
    monkeypatch.setattr(cache_sync.overview_warm, "warm_paths",
                        lambda paths: dict.fromkeys(paths, answer))
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    assert report.paths[0].kinds == {compiled.OVERVIEW_CONTINUITY.name: outcome}
    assert report.paths[0].status == status


def test_every_materializable_kind_is_claimed_in_a_fresh_process(home):
    script = ("import json\n"
              "from grimoire.store import cache_sync, compiled\n"
              "kinds = sorted(compiled.OVERVIEW_CANDIDATES) + [\n"
              "    'vector:lore-entry:x', 'vector:art-description:x', 'vector:search-passage:x']\n"
              "print(json.dumps([k for k in kinds if not any(h.owns(k) for h in cache_sync.HOOKS)]))\n")
    env = {**os.environ, "PYTHONPATH": str(SRC), "GRIMOIRE_HOME": str(home)}
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env,
                          timeout=120, check=True)
    assert json.loads(done.stdout) == []
