"""`scripts/profile_report.py` and `scripts/coverage_arcs.py` say what the data says.

The acceleration work's claims -- "this is where the time goes", "nothing went
missing", "no covered arc was lost" -- are read off these two tools, so each is
held here to inputs whose right answers are known: synthetic profiles, and
coverage data files built in `tmp_path` through coverage.py's own API.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from coverage import CoverageData

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import coverage_arcs  # noqa: E402 -- the sys.path line above is what makes it importable
import profile_report  # noqa: E402


def _profile(tests: dict, **header) -> dict:
    return {"schema_version": 1, "wall_s": 10.0, "workers": 0,
            "distribution": "serial", **header, "tests": tests}


def _node(setup=0.0, call=0.0, teardown=0.0, outcome="passed", **extra) -> dict:
    return {"setup_s": setup, "call_s": call, "teardown_s": teardown,
            "outcome": outcome, **extra}


# ------------------------------------------------------------ profile_report

def test_summary_ranks_each_phase_and_groups_by_module():
    s = profile_report.summarize(_profile({
        "tests/test_a.py::test_slow_call": _node(setup=0.1, call=3.0),
        "tests/test_a.py::test_slow_setup": _node(setup=2.0, call=0.1),
        "tests/test_b.py::test_slow_teardown": _node(teardown=1.5),
    }))
    assert [n for n, _ in s["top_total"]] == [
        "tests/test_a.py::test_slow_call", "tests/test_a.py::test_slow_setup",
        "tests/test_b.py::test_slow_teardown"]
    assert s["top_call"][0][0] == "tests/test_a.py::test_slow_call"
    assert s["top_setup"][0][0] == "tests/test_a.py::test_slow_setup"
    assert s["top_teardown"][0][0] == "tests/test_b.py::test_slow_teardown"
    modules = dict(s["modules"])
    assert modules["tests/test_a.py"]["nodes"] == 2
    assert modules["tests/test_a.py"]["seconds"] == pytest.approx(5.2)
    assert s["node_seconds"] == pytest.approx(6.7)
    assert s["phase_seconds"]["setup"] == pytest.approx(2.1)


def test_summary_sums_fixtures_operations_and_outcomes():
    s = profile_report.summarize(_profile({
        "t.py::a": _node(fixtures_s={"client": 0.5, "tmp_path": 0.01},
                         ops={"app_built": 1, "lifespan_entered": 1}),
        "t.py::b": _node(fixtures_s={"client": 0.25}, ops={"app_built": 2}),
        "t.py::c": _node(outcome="failed"),
    }))
    fixtures = dict(s["fixtures"])
    assert fixtures["client"] == {"uses": 2, "seconds": pytest.approx(0.75)}
    assert s["fixtures"][0][0] == "client"
    assert s["ops"] == {"app_built": 3, "lifespan_entered": 1}
    assert s["outcomes"] == {"failed": 1, "passed": 2}


def test_concentration_is_the_share_held_by_the_slowest_nodes():
    # 100 nodes: one of 91 s and 99 of 1 s -> the slowest 1 % holds 91/190.
    seconds = [91.0] + [1.0] * 99
    c = profile_report.concentration(seconds)
    assert c["top_1%"] == pytest.approx(91 / 190)
    assert c["top_5%"] == pytest.approx(95 / 190)
    assert c["top_10%"] == pytest.approx(100 / 190)
    # A suite smaller than 1/share still reports its slowest node.
    assert profile_report.concentration([3.0, 1.0])["top_1%"] == pytest.approx(0.75)


def test_manifest_is_the_sorted_node_ids():
    doc = _profile({"t.py::b": _node(), "t.py::a": _node()})
    assert profile_report.manifest(doc) == ["t.py::a", "t.py::b"]


def test_compare_fails_on_a_node_that_went_missing(tmp_path, capsys):
    before = _profile({"t.py::a": _node(), "t.py::b": _node(), "t.py::c": _node()})
    after = _profile({"t.py::a": _node(), "t.py::c": _node(outcome="failed"),
                      "t.py::d": _node()})
    (tmp_path / "b.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(after), encoding="utf-8")
    diff = profile_report.compare(before, after)
    assert diff["missing"] == ["t.py::b"]
    assert diff["added"] == ["t.py::d"]
    assert diff["outcome_changed"] == [("t.py::c", "passed", "failed")]
    assert profile_report.main(["compare", str(tmp_path / "b.json"),
                                str(tmp_path / "a.json")]) == 1
    assert "MISSING  t.py::b" in capsys.readouterr().out


def test_compare_passes_when_nothing_went_missing(tmp_path):
    doc = _profile({"t.py::a": _node()})
    path = tmp_path / "p.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert profile_report.main(["compare", str(path), str(path)]) == 0


# ------------------------------------------------------------ coverage_arcs

def _source_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """A fake `backend/src` holding a three-module `grimoire` package."""
    src = tmp_path / "src"
    pkg = src / "grimoire"
    pkg.mkdir(parents=True)
    for name in ("__init__.py", "a.py", "never_imported.py"):
        (pkg / name).write_text("x = 1\n", encoding="utf-8")
    return src


def _data_file(tmp_path: pathlib.Path, name: str, arcs: dict) -> pathlib.Path:
    """A real coverage data file holding `arcs` ({path: [(a, b), ...]})."""
    path = tmp_path / name
    data = CoverageData(basename=str(path))
    data.add_arcs({str(p): set(a) for p, a in arcs.items()})
    data.write()
    return path


def test_dump_lists_every_package_file_including_ones_never_imported(tmp_path):
    src = _source_tree(tmp_path)
    a = src / "grimoire" / "a.py"
    doc = coverage_arcs.dump(_data_file(tmp_path, "cov", {a: [(-1, 1), (1, 2), (2, -1)]}),
                             src=src)
    assert set(doc["files"]) == {"grimoire/__init__.py", "grimoire/a.py",
                                 "grimoire/never_imported.py"}
    assert doc["files"]["grimoire/a.py"] == {"lines": [1, 2],
                                             "arcs": [[-1, 1], [1, 2], [2, -1]]}
    assert doc["files"]["grimoire/never_imported.py"] == {"lines": [], "arcs": []}
    fp = coverage_arcs.fingerprint(doc)
    assert (fp["files"], fp["files_executed"], fp["lines"], fp["arcs"]) == (3, 1, 2, 3)


def test_dump_refuses_a_file_from_another_checkout(tmp_path):
    """An editable install from a different worktree measures *its* sources;
    a dump of that run must not pass for this tree's."""
    src = _source_tree(tmp_path)
    elsewhere = tmp_path / "other-checkout" / "grimoire" / "a.py"
    with pytest.raises(SystemExit, match="another checkout"):
        coverage_arcs.dump(_data_file(tmp_path, "cov", {elsewhere: [(1, 2)]}), src=src)


def test_stable_keeps_what_every_run_executed_and_lists_the_rest():
    def run(arcs):
        return {"files": {"grimoire/a.py": {"lines": sorted({b for _, b in arcs if b > 0}),
                                            "arcs": [list(x) for x in arcs]}}}
    always = [(-1, 1), (1, 2)]
    doc = coverage_arcs.stable([run([*always, (2, 3)]), run([*always, (2, 4)]), run(always)])
    assert doc["files"]["grimoire/a.py"]["arcs"] == [[-1, 1], [1, 2]]
    assert doc["variable"]["grimoire/a.py"]["arcs"] == [[2, 3], [2, 4]]
    assert doc["runs"] == 3


def test_stable_refuses_dumps_of_different_source_trees():
    with pytest.raises(ValueError, match="different files"):
        coverage_arcs.stable([{"files": {"a": {"lines": [], "arcs": []}}},
                              {"files": {"b": {"lines": [], "arcs": []}}}])


@pytest.mark.parametrize("after,lost", [
    ({"grimoire/a.py": {"lines": [1], "arcs": [[-1, 1]]}}, "arc"),
    ({"grimoire/a.py": {"lines": [1, 2], "arcs": [[-1, 1], [1, 2]]}}, None),
    ({}, "file"),
])
def test_diff_fails_on_any_loss_and_only_on_a_loss(tmp_path, after, lost):
    before = {"files": {"grimoire/a.py": {"lines": [1], "arcs": [[-1, 1], [1, 2]]}}}
    result = coverage_arcs.diff(before, {"files": after})
    (tmp_path / "b.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps({"files": after}), encoding="utf-8")
    code = coverage_arcs.main(["diff", str(tmp_path / "b.json"), str(tmp_path / "a.json")])
    if lost is None:
        assert code == 0 and not result["lost"] and not result["lost_files"]
        assert result["gained"]["grimoire/a.py"]["lines"] == [2]   # gains never fail
    elif lost == "arc":
        assert code == 1 and result["lost"]["grimoire/a.py"]["arcs"] == [[1, 2]]
    else:
        assert code == 1 and result["lost_files"] == ["grimoire/a.py"]


def test_a_lost_line_alone_fails_the_diff():
    before = {"files": {"grimoire/a.py": {"lines": [1, 5], "arcs": []}}}
    after = {"files": {"grimoire/a.py": {"lines": [1], "arcs": []}}}
    assert coverage_arcs.diff(before, after)["lost"]["grimoire/a.py"]["lines"] == [5]
