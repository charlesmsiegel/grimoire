"""`scripts/profile_report.py` and `scripts/coverage_arcs.py` say what the data says.

The acceleration work's claims -- "this is where the time goes", "nothing went
missing", "no covered arc was lost" -- are read off these two tools, so each is
held here to inputs whose right answers are known: synthetic profiles, and
coverage data files built in `tmp_path` through coverage.py's own API.
"""

from __future__ import annotations

import hashlib
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


def test_manifest_lists_what_was_collected_even_if_it_never_reported():
    """A run stopped by -x, an interrupt or a crash reported on fewer nodes
    than it collected; the manifest is the collection."""
    doc = _profile({"t.py::a": _node(outcome="failed")},
                   collected=["t.py::c", "t.py::a", "t.py::b"])
    assert profile_report.manifest(doc) == ["t.py::a", "t.py::b", "t.py::c"]


@pytest.mark.parametrize("header,code", [({"exitstatus": 0}, 0), ({"exitstatus": 1}, 1),
                                         ({"exitstatus": 5}, 5), ({}, 1)])
def test_summary_can_exit_with_the_profiled_runs_status(tmp_path, capsys, header, code):
    """`make test-py-profile` summarises a failing selection and still fails;
    a profile with no recorded status is not read as a pass."""
    path = tmp_path / "p.json"
    path.write_text(json.dumps(_profile({"t.py::a": _node()}, **header)), encoding="utf-8")
    assert profile_report.main(["summary", str(path), "--exit-with-run"]) == code
    assert "t.py::a" in capsys.readouterr().out
    assert profile_report.main(["summary", str(path)]) == 0


def test_summary_fails_on_a_profile_that_was_never_written(tmp_path, capsys):
    """`make test-py-profile` deletes the last profile before pytest runs, so a
    pytest that stopped before writing one leaves nothing -- which fails,
    rather than an earlier run's file being summarised as this one."""
    missing = tmp_path / "p.json"
    assert profile_report.main(["summary", str(missing), "--exit-with-run"]) == 1
    assert "no profile" in capsys.readouterr().out


def test_compare_fails_on_a_node_that_went_missing(tmp_path, capsys):
    before = _profile({"t.py::a": _node(), "t.py::b": _node()})
    after = _profile({"t.py::a": _node(), "t.py::d": _node()})
    (tmp_path / "b.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(after), encoding="utf-8")
    diff = profile_report.compare(before, after)
    assert diff["missing"] == ["t.py::b"]
    assert diff["added"] == ["t.py::d"]
    assert profile_report.main(["compare", str(tmp_path / "b.json"),
                                str(tmp_path / "a.json")]) == 1
    assert "MISSING  t.py::b" in capsys.readouterr().out


def test_compare_fails_on_an_outcome_change_even_behind_a_teardown_error():
    """Both runs say `error` in one word; the call underneath went from passed
    to failed, and that is a regression the per-phase record still shows."""
    was = _node(outcome="error", phases={"setup": "passed", "call": "passed",
                                         "teardown": "failed"})
    now = _node(outcome="error", phases={"setup": "passed", "call": "failed",
                                         "teardown": "failed"})
    diff = profile_report.compare(_profile({"t.py::a": was}), _profile({"t.py::a": now}))
    assert [nid for nid, _, _ in diff["outcome_changed"]] == ["t.py::a"]
    assert profile_report.failed(diff)


def test_compare_fails_on_a_collected_node_that_never_reported():
    before = _profile({"t.py::a": _node(), "t.py::b": _node()},
                      collected=["t.py::a", "t.py::b"])
    after = _profile({"t.py::a": _node()}, collected=["t.py::a", "t.py::b"])
    diff = profile_report.compare(before, after)
    assert diff["missing"] == [] and diff["unreported"] == ["t.py::b"]
    assert profile_report.failed(diff)


def test_added_nodes_alone_do_not_fail_compare():
    diff = profile_report.compare(_profile({"t.py::a": _node()}),
                                  _profile({"t.py::a": _node(), "t.py::new": _node()}))
    assert diff["added"] == ["t.py::new"] and not profile_report.failed(diff)


def test_compare_passes_when_nothing_went_missing(tmp_path):
    doc = _profile({"t.py::a": _node()})
    path = tmp_path / "p.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert profile_report.main(["compare", str(path), str(path)]) == 0


def _budget(**over) -> dict:
    return {"schema_version": 1, "wall_s": 100.0, "tolerance": 0.2, "collected": 3,
            "skipped": 1, "slow_test_s": 10.0} | over


def _budgeted(wall=100.0, collected=("t.py::a", "t.py::b", "t.py::c"), skipped=1,
              slow=None, **header) -> dict:
    tests = {nid: _node(call=0.1) for nid in collected}
    for nid in list(tests)[:skipped]:
        tests[nid] = _node(outcome="skipped")
    if slow:
        tests[collected[-1]] = _node(call=slow)
    header = {"python": "3.11.16", "coverage": True, "exitstatus": 0,
              "packages": {"pytest": "9.1.1", "fastapi": "0.142.4"}, **header}
    return _profile(tests, wall_s=wall, collected=list(collected), **header)


def _earlier(*walls, **header) -> list[dict]:
    return [_budgeted(wall=w, **header) for w in walls]


def test_a_run_within_budget_warns_of_nothing():
    assert profile_report.budget_findings(_budgeted(wall=119.0), _budget(),
                                          _earlier(119, 119, 119, 119)) == []


def test_the_budget_warns_on_time_count_skips_and_slow_tests():
    found = profile_report.budget_findings(
        _budgeted(wall=125.0, collected=("t.py::a", "t.py::b", "t.py::c"), skipped=2,
                  slow=11.0),
        _budget(collected=4), _earlier(125, 125, 125, 125))
    kinds = sorted(kind for kind, _ in found)
    assert kinds == ["fewer-tests", "more-skips", "slow-test", "slower"]


def test_one_slow_session_is_noise_and_five_are_a_regression():
    """Session time is judged on the median of five comparable runs: one slow
    run warns of nothing, nor does a short window however slow, and a fast
    outlier cannot hide a slow median."""
    slow = _budgeted(wall=500.0)

    def kinds(doc, previous):
        return [k for k, _ in profile_report.budget_findings(doc, _budget(), previous)]

    assert kinds(slow, _earlier(100, 100, 100, 100)) == []
    assert kinds(slow, _earlier(500, 500, 500)) == []                  # only four runs
    assert kinds(_budgeted(wall=50.0), _earlier(500, 500, 500, 500)) == ["slower"]
    # Only the newest four earlier runs count.
    assert kinds(slow, _earlier(500, 500, 500, 500, 100, 100, 100)) == ["slower"]


@pytest.mark.parametrize("other", [
    {"python": "3.14.6"}, {"workers": 2}, {"distribution": "worksteal"},
    {"coverage": False}, {"exitstatus": 2}, {"exitstatus": None},
    {"packages": {"pytest": "9.1.1", "fastapi": "0.143.0"}},
])
def test_only_comparable_complete_runs_join_the_window(other):
    """A different interpreter, worker count, scheduler, coverage setting or
    dependency release times a different job, and an interrupted run timed
    less of the suite."""
    window = profile_report.session_window(
        _budgeted(wall=500.0), _earlier(500, 500, 500, 500, **other))
    assert window == [500.0]
    assert profile_report.session_window(
        _budgeted(wall=500.0), _earlier(500, exitstatus=1)) == [500.0, 500.0]


def test_more_tests_is_a_notice_not_a_warning():
    found = profile_report.budget_findings(
        _budgeted(collected=("t.py::a", "t.py::b", "t.py::c", "t.py::d")), _budget())
    assert [kind for kind, _ in found] == ["more-tests"]


def test_the_budget_command_never_fails_the_job(tmp_path, capsys):
    """A warning is something to investigate on a shared runner, not a red
    gate -- and a run that died before writing its profile says so the same
    way."""
    (tmp_path / "b.json").write_text(json.dumps(_budget()), encoding="utf-8")
    (tmp_path / "p.json").write_text(json.dumps(_budgeted(slow=60.0)), encoding="utf-8")
    assert profile_report.main(["budget", str(tmp_path / "p.json"),
                                str(tmp_path / "b.json")]) == 0
    assert "::warning" in capsys.readouterr().out
    assert profile_report.main(["budget", str(tmp_path / "missing.json"),
                                str(tmp_path / "b.json")]) == 0
    assert "no profile" in capsys.readouterr().out


def test_the_budget_command_reads_the_earlier_runs_it_was_handed(tmp_path, capsys):
    """CI fetches up to four earlier profiles and passes them all; the ones it
    could not fetch are paths that do not exist, and are skipped."""
    (tmp_path / "b.json").write_text(json.dumps(_budget()), encoding="utf-8")
    (tmp_path / "p.json").write_text(json.dumps(_budgeted(wall=500.0)), encoding="utf-8")
    previous = []
    for i in range(4):
        path = tmp_path / str(i) / "backend-profile.json"
        if i < 3:
            path.parent.mkdir()
            path.write_text(json.dumps(_budgeted(wall=500.0)), encoding="utf-8")
        previous.append(str(path))
    args = ["budget", str(tmp_path / "p.json"), str(tmp_path / "b.json")]
    assert profile_report.main([*args, "--previous", *previous]) == 0
    out = capsys.readouterr().out
    assert "median of 4 comparable run(s); 5 are needed" in out
    assert "::warning" not in out
    (tmp_path / "3").mkdir()
    (tmp_path / "3" / "backend-profile.json").write_text(
        json.dumps(_budgeted(wall=500.0)), encoding="utf-8")
    assert profile_report.main([*args, "--previous", *previous]) == 0
    assert "test budget (slower)" in capsys.readouterr().out


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


def _recorded(src: pathlib.Path) -> dict[str, str]:
    """What a phase profile records for `src`, as the run saw it."""
    return {f"grimoire/{p.relative_to(src / 'grimoire').as_posix()}":
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((src / "grimoire").rglob("*.py"))}


def test_dump_lists_every_package_file_including_ones_never_imported(tmp_path):
    src = _source_tree(tmp_path)
    a = src / "grimoire" / "a.py"
    doc = coverage_arcs.dump(_data_file(tmp_path, "cov", {a: [(-1, 1), (1, 2), (2, -1)]}),
                             _recorded(src), src=src)
    assert set(doc["files"]) == {"grimoire/__init__.py", "grimoire/a.py",
                                 "grimoire/never_imported.py"}
    source = hashlib.sha256(b"x = 1\n").hexdigest()
    assert doc["files"]["grimoire/a.py"] == {"sha256": source, "lines": [1, 2],
                                             "arcs": [[-1, 1], [1, 2], [2, -1]]}
    assert doc["files"]["grimoire/never_imported.py"] == {"sha256": source,
                                                          "lines": [], "arcs": []}
    fp = coverage_arcs.fingerprint(doc)
    assert (fp["files"], fp["files_executed"], fp["lines"], fp["arcs"]) == (3, 1, 2, 3)


def test_dump_refuses_a_file_from_another_checkout(tmp_path):
    """An editable install from a different worktree measures *its* sources;
    a dump of that run must not pass for this tree's."""
    src = _source_tree(tmp_path)
    elsewhere = tmp_path / "other-checkout" / "grimoire" / "a.py"
    with pytest.raises(SystemExit, match="another checkout"):
        coverage_arcs.dump(_data_file(tmp_path, "cov", {elsewhere: [(1, 2)]}),
                           _recorded(src), src=src)


def test_dump_refuses_a_tree_that_changed_since_the_run(tmp_path):
    """A coverage file kept across an edit, dumped afterwards: its numbers
    describe the text the run measured, which the tree no longer holds -- and
    a dump with no record of that text at all is refused too."""
    src = _source_tree(tmp_path)
    data = _data_file(tmp_path, "cov", {src / "grimoire" / "a.py": [(1, 2)]})
    recorded = _recorded(src)
    (src / "grimoire" / "a.py").write_text("x = 2\ny = 3\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="the tree changed since the run"):
        coverage_arcs.dump(data, recorded, src=src)
    (src / "grimoire" / "new.py").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="changed since the run"):
        coverage_arcs.dump(data, _recorded(src) | {"grimoire/new.py": "gone"}, src=src)
    with pytest.raises(SystemExit, match="no recorded sources"):
        coverage_arcs.dump(data, None, src=src)


def test_dump_reads_a_run_made_in_another_tree_when_told_so(tmp_path):
    """`--src` points the dump at the worktree the run was made in; the keys
    come out the same as a dump made in place, so the two compare."""
    src = _source_tree(tmp_path)
    data = _data_file(tmp_path, "cov", {src / "grimoire" / "a.py": [(1, 2)]})
    out = tmp_path / "dump.json"
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"sources": _recorded(src)}), encoding="utf-8")
    assert coverage_arcs.main(["dump", str(data), str(out), "--src", str(src),
                               "--profile", str(profile)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["files"]["grimoire/a.py"]["arcs"] == [[1, 2]]


def _file(lines=(), arcs=(), sha256="s1") -> dict:
    return {"sha256": sha256, "lines": list(lines), "arcs": [list(a) for a in arcs]}


def test_stable_keeps_what_every_run_executed_and_lists_the_rest():
    def run(arcs):
        return {"files": {"grimoire/a.py": _file(sorted({b for _, b in arcs if b > 0}), arcs)}}
    always = [(-1, 1), (1, 2)]
    doc = coverage_arcs.stable([run([*always, (2, 3)]), run([*always, (2, 4)]), run(always)])
    assert doc["files"]["grimoire/a.py"]["arcs"] == [[-1, 1], [1, 2]]
    assert doc["files"]["grimoire/a.py"]["sha256"] == "s1"
    assert doc["variable"]["grimoire/a.py"]["arcs"] == [[2, 3], [2, 4]]
    assert doc["runs"] == 3


def test_stable_refuses_dumps_of_different_source_trees():
    with pytest.raises(ValueError, match="different files"):
        coverage_arcs.stable([{"files": {"a": _file()}}, {"files": {"b": _file()}}])


@pytest.mark.parametrize("combine", ["stable", "diff"])
def test_dumps_of_different_source_text_never_combine(tmp_path, combine):
    """Same file names, different code (a rebase, a production edit): line and
    arc numbers point at different statements, so neither command compares
    them -- nor a dump that cannot say what text it measured."""
    one = {"files": {"grimoire/a.py": _file([1], [(1, 2)], sha256="s1")}}
    edited = {"files": {"grimoire/a.py": _file([1], [(1, 2)], sha256="s2")}}
    unhashed = {"files": {"grimoire/a.py": {"lines": [1], "arcs": [[1, 2]]}}}
    run = (coverage_arcs.stable if combine == "stable"
           else lambda docs: coverage_arcs.diff(*docs))
    with pytest.raises(ValueError, match="different source text"):
        run([one, edited])
    with pytest.raises(ValueError, match="no source hash"):
        run([one, unhashed])
    (tmp_path / "1.json").write_text(json.dumps(one), encoding="utf-8")
    (tmp_path / "2.json").write_text(json.dumps(edited), encoding="utf-8")
    args = (["stable", str(tmp_path / "out.json")] if combine == "stable" else ["diff"])
    assert coverage_arcs.main([*args, str(tmp_path / "1.json"), str(tmp_path / "2.json")]) == 2


@pytest.mark.parametrize("after,lost", [
    ({"grimoire/a.py": {"lines": [1], "arcs": [[-1, 1]]}}, "arc"),
    ({"grimoire/a.py": {"lines": [1, 2], "arcs": [[-1, 1], [1, 2]]}}, None),
    ({}, "file"),
])
def test_diff_fails_on_any_loss_and_only_on_a_loss(tmp_path, after, lost):
    before = {"files": {"grimoire/a.py": _file([1], [(-1, 1), (1, 2)])}}
    after = {k: {"sha256": "s1", **v} for k, v in after.items()}
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
    before = {"files": {"grimoire/a.py": _file([1, 5])}}
    after = {"files": {"grimoire/a.py": _file([1])}}
    assert coverage_arcs.diff(before, after)["lost"]["grimoire/a.py"]["lines"] == [5]


def test_contexts_reports_what_each_matching_test_executed(tmp_path):
    """A `--cov-context=test` run tags every arc with the test that ran it;
    `contexts` reads them back per test, for the tests asked about."""
    src = _source_tree(tmp_path)
    a = str(src / "grimoire" / "a.py")
    path = tmp_path / "ctx"
    data = CoverageData(basename=str(path))
    for ctx, arcs in (("tests/t.py::test_one|run", {(1, 2)}),
                      ("tests/t.py::test_two|run", {(1, 3)}),
                      ("tests/u.py::test_other|run", {(1, 4)})):
        data.set_context(ctx)
        data.add_arcs({a: arcs})
    data.write()
    out = coverage_arcs.contexts(path, "tests/t.py::", src=src)
    assert out == {"tests/t.py::test_one|run": {"grimoire/a.py": [[1, 2]]},
                   "tests/t.py::test_two|run": {"grimoire/a.py": [[1, 3]]}}


def test_contexts_refuses_data_recorded_without_arcs(tmp_path):
    """A run measured without branch = true has lines and no arcs; read per
    test it would look like tests that executed nothing, which is exactly the
    wrong evidence for a consolidation."""
    src = _source_tree(tmp_path)
    path = tmp_path / "lines-only"
    data = CoverageData(basename=str(path))
    data.set_context("tests/t.py::test_one|run")
    data.add_lines({str(src / "grimoire" / "a.py"): {1}})
    data.write()
    with pytest.raises(SystemExit, match="no arc data"):
        coverage_arcs.contexts(path, "tests/t.py::", src=src)
