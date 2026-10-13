"""04-S10: the overview benchmark's counter assertions on the `small` profile.

Spec 04 section 12.3, run inside `make check-py` on a synthetic library built
by `scripts/synth_library.py` through the store's own writers, with the
compiled and statcache clocks injected past their trust windows (POSIX ctime
cannot be backdated). Counters are deterministic, so these are the gate; no
timing is asserted (Open question 10). The persisted kinds are registered for
the run, whatever `compiled.KINDS` ships with, so the restart assertions hold
the persisted layer to its contract either way.

Also the harness's own guard: it refuses anything but a marked synthetic
library outside every real store, and its output carries no name, id or path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from grimoire.store import paths
from scripts import bench_overview, synth_library
from tests.overview_support import audited


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    target = tmp_path_factory.mktemp("synthetic") / "library"
    synth_library.generate_library(target, "small", 11)
    return target


@pytest.fixture
def bench(library, monkeypatch):
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    with bench_overview.Bench(library, register="all", clocks="injected") as b:
        yield b


def _warm_restarted(bench):
    bench.cold()
    bench.warm()
    bench.warm()
    bench.restart()


# ---- 12.3 -------------------------------------------------------------------

def test_warm_after_restart(bench):
    _warm_restarted(bench)
    scenes = sum(len(list(d.glob("*.md"))) for d in (bench.work / "campaigns").glob("*/scenes"))
    campaigns = bench.get("campaigns")["counters"]
    assert campaigns.get("overview.computed.campaign_row", 0) == 0
    # Scene heads are not persisted: one head-only parse per scene, today's cost.
    assert campaigns.get("frontmatter.parse_head", 0) == scenes
    bench.restart()
    shell = bench.get("shell_campaign")["counters"]
    assert shell.get("transcript.parse", 0) == 0
    assert shell.get("compiled.hit.overview.scene_turns", 0) >= 1
    bench.restart()
    todo = bench.get("todo")["counters"]
    assert todo.get("overview.computed.continuity_summary", 0) == 0


def test_warm_same_process_lists_no_scenes_dir(bench):
    bench.cold()
    bench.warm()
    bench.warm()
    with audited() as audit:
        bench.get("campaigns")
    assert not [p for p in audit.listed if p.endswith("scenes")]


def test_relevant_change_costs_one_head_and_one_listing(bench):
    bench.cold()
    bench.warm()
    bench.warm()
    bench.append_post()
    largest = bench.largest_campaign()
    with audited() as audit:
        counters = bench.get("campaigns")["counters"]
    assert counters.get("frontmatter.parse_head", 0) == 1
    assert [p for p in audit.listed if p.endswith("scenes")] == \
        [str(bench.work / "campaigns" / largest / "scenes")]


def test_visit_after_turn_reads_the_warmed_count(bench):
    bench.cold()
    bench.warm()
    bench.turn()
    shell = bench.get("shell_campaign")["counters"]
    assert shell.get("compiled.hit.overview.scene_turns", 0) == 1
    assert shell.get("transcript.parse", 0) == 0


def test_unrelated_change_misses_no_overview_kind(bench):
    bench.cold()
    bench.warm()
    bench.warm()
    bench.edit_lore()
    for endpoint in ("campaigns", "todo_campaign"):
        counters = bench.get(endpoint)["counters"]
        assert not [k for k, v in counters.items() if v and k.startswith(("compiled.miss.overview.",
                                                                           "overview.computed."))]


def test_one_campaign_changed_misses_only_its_own_projections(bench):
    bench.cold()
    bench.warm()
    bench.warm()
    bench.append_post()
    bench.edit_commitments()
    bench.restart()
    counters = bench.get("todo")["counters"]
    campaigns = len(list((bench.work / "campaigns").iterdir()))
    assert campaigns > 1
    assert counters.get("compiled.miss.overview.continuity_summary", 0) == 1
    assert counters.get("compiled.hit.overview.continuity_summary", 0) == campaigns - 1


def test_shell_and_todo_share_one_continuity_computation(bench):
    bench.cold()
    bench.settle()
    shell = bench.get("shell_campaign")["counters"]
    todo = bench.get("todo_campaign")["counters"]
    assert shell.get("overview.computed.continuity_summary", 0) + \
        todo.get("overview.computed.continuity_summary", 0) == 1


def test_no_closed_scene_transcript_is_read_in_any_state(bench, monkeypatch):
    from grimoire.store.overview import read as overview_read
    asked: list[str] = []
    real = overview_read._turns
    monkeypatch.setattr(overview_read, "_turns",
                        lambda cid, sid, p, players: (asked.append(sid), real(cid, sid, p, players))[1])
    for state in (bench.cold, bench.warm, bench.restart, bench.cache_off):
        state()
        for endpoint in bench_overview.ENDPOINTS:
            bench.get(endpoint)
    from grimoire.store import overview
    largest = bench.largest_campaign()
    closed = {r["sid"] for r in overview.scene_summary(largest)["rows"] if r["done"]}
    assert closed and not closed & set(asked)


def test_live_path_fixes(bench):
    bench.cold()
    bench.warm()
    bench.warm()
    with audited() as audit:
        bench.get("todo")
        bench.get("todo")
    opened = [p for p in audit.opened if p.endswith("character.md")]
    assert len(opened) == len(set(opened))


# ---- the harness's guard and its output ------------------------------------

def test_harness_refuses_an_unmarked_directory(tmp_path):
    with pytest.raises(ValueError, match="marker"):
        bench_overview.guard(tmp_path, inherited_home=None)


def _marked(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / synth_library.SYNTHETIC_MARKER).write_text("{}", encoding="utf-8")
    return path


@pytest.mark.parametrize("where", ["default", "pointer", "inherited"])
@pytest.mark.parametrize("inside", [False, True])
def test_harness_refuses_real_stores_and_paths_inside_them(tmp_path, monkeypatch, where, inside):
    real = tmp_path / "real-store"
    library = _marked(real / "nested" if inside else real)
    monkeypatch.setattr(paths, "DEFAULT_HOME", real if where == "default" else tmp_path / "elsewhere")
    monkeypatch.setattr(paths, "_parse_pointer",
                        lambda p: {"data_dir": str(real)} if where == "pointer" else None)
    inherited = str(real) if where == "inherited" else None
    with pytest.raises(ValueError, match="real store"):
        bench_overview.guard(library, inherited_home=inherited)


def test_harness_refuses_a_library_holding_a_link(tmp_path):
    library = _marked(tmp_path / "library")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (library / "campaigns").mkdir()
    (library / "campaigns" / "linked").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="link"):
        bench_overview.guard(library, inherited_home=None)


def test_harness_refuses_a_library_holding_a_hard_link(tmp_path):
    library = _marked(tmp_path / "library")
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("Saltmarch", encoding="utf-8")
    os.link(elsewhere, library / "linked.md")
    with pytest.raises(ValueError, match="link"):
        bench_overview.guard(library, inherited_home=None)


def test_the_large_profiles_hold_scenes_past_the_admission_size():
    from grimoire.store.overview import read as overview_read
    assert synth_library.LONG_SCENE_BYTES > overview_read.SCENE_TURNS_PERSIST_MIN_BYTES
    assert synth_library.PROFILES["medium"]["long_scenes"] >= 1
    assert synth_library.PROFILES["large"]["long_scenes"] >= 1


def test_the_scenes_and_ledger_changes_are_separate_scenarios():
    assert {"relevant_change", "relevant_change_ledger"} <= set(bench_overview.SCENARIOS)


def test_harness_output_carries_no_name_id_or_path(library, monkeypatch, tmp_path):
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    result = bench_overview.measure(library, repeat=1, scenarios=("cold", "relevant_change"),
                                    endpoints=("campaigns", "shell_campaign"))
    text = json.dumps(result)
    for needle in ("Realm", "Saltmarch", "Seraphine", "Mara", "Winifred", "realm-", "saltmarch-",
                   str(library), str(tmp_path), os.sep + "tmp"):
        assert needle not in text
    assert set(result["scenarios"]) == {"cold", "relevant_change"}
    assert set(result["scenarios"]["cold"]["campaigns"]) == {"median_ms", "counters", "opened", "listed"}


def test_the_generator_refuses_a_non_empty_target(tmp_path):
    (tmp_path / "Realm.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        synth_library.generate_library(tmp_path, "small", 1)


def test_the_generator_marks_and_backdates(library):
    marker = json.loads((library / synth_library.SYNTHETIC_MARKER).read_text(encoding="utf-8"))
    assert (marker["profile"], marker["seed"]) == ("small", 11)
    import time
    newest = max(p.stat().st_mtime_ns for p in library.rglob("*"))
    assert newest < time.time_ns() - 60 * 1_000_000_000
