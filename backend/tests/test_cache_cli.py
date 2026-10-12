"""The CLI, scoped modes, renames, the root purge and `collecting()` (05-S3)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from grimoire import cache as cli
from grimoire.store import atomic, cache_sync, compiled, revision, writeset
from tests import cache_sync_fixtures as fx

# The shared fixtures, bound here so pytest finds them by name.
cache, home, upper = fx.cache, fx.home, fx.upper
SENTINEL, _by_path, _read, _write = fx.SENTINEL, fx._by_path, fx._read, fx._write


SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def trusted_clock(monkeypatch, cache, home):
    """A filesystem clock far enough ahead that every file is outside the
    persist window, so a `sources` row can be recorded and trusted."""
    def clock(boundary):
        return compiled.FilesystemClock(time.time_ns() + 3_600_000_000_000, home.stat().st_dev)
    monkeypatch.setattr(compiled, "filesystem_clock", clock)


def _run(argv, capsys):
    code = cli.main(argv)
    return code, capsys.readouterr()


# -- exit statuses -----------------------------------------------------------


def test_exit_zero_for_a_cold_path_and_one_for_a_missing_one(home, capsys):
    _write(home, "worlds/realm/lore/pact.md", "x")
    code, out = _run(["sync", "worlds/realm/lore/pact.md"], capsys)
    assert code == 0 and "cold=1" in out.out
    code, out = _run(["sync", "worlds/realm/lore/gone.md"], capsys)
    assert code == 1 and "--deleted" in out.out


def test_exit_one_for_a_refusal_and_two_for_a_usage_error(home, capsys):
    assert _run(["sync", "logs/x.jsonl"], capsys)[0] == 1
    with pytest.raises(SystemExit) as no_selector:
        cli.main(["sync"])
    assert no_selector.value.code == 2
    with pytest.raises(SystemExit) as bad_rename:
        cli.main(["sync", "--renamed", "worlds/realm/a.md"])
    assert bad_rename.value.code == 2


def test_exit_three_when_the_root_cannot_be_used(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "nowhere"))
    code, out = _run(["sync", "worlds/realm/lore/pact.md"], capsys)
    assert code == 3 and "does not exist" in out.err


def test_json_output_is_one_object_with_a_version(home, capsys):
    _write(home, "worlds/realm/lore/pact.md", "x")
    code, out = _run(["sync", "--json", "worlds/realm/lore/pact.md"], capsys)
    report = json.loads(out.out)
    assert code == 0 and report["version"] == cache_sync.REPORT_VERSION
    assert tuple(report) == cache_sync.BATCH_FIELDS


def test_quiet_prints_only_the_counts(home, capsys):
    _write(home, "worlds/realm/lore/pact.md", "x")
    _, out = _run(["sync", "--quiet", "worlds/realm/lore/pact.md"], capsys)
    assert out.out.strip() == "cold=1"


def test_the_parser_carries_every_flag(home):
    flags = {option for action in cli.build_parser()._subparsers._group_actions[0].choices["sync"]._actions
             for option in action.option_strings}
    assert {"--campaign", "--world", "--all", "--renamed", "--deleted", "--no-embed", "--dry-run",
            "--verify", "--json", "--quiet"} <= flags


def test_the_cli_runs_in_a_fresh_process(home):
    _write(home, "worlds/realm/lore/pact.md", "x")
    env = {**os.environ, "GRIMOIRE_HOME": str(home), "PYTHONPATH": str(SRC)}
    done = subprocess.run([sys.executable, "-m", "grimoire.cache", "sync", "--json",
                           "worlds/realm/lore/pact.md", "worlds/realm/gone.md"],
                          capture_output=True, text=True, env=env, timeout=120, check=False)
    assert done.returncode == 1, done.stderr
    report = json.loads(done.stdout)
    assert [(p["path"], p["status"]) for p in report["paths"]] == [
        ("worlds/realm/lore/pact.md", "cold"), ("worlds/realm/gone.md", "missing")]


# -- scoped modes ------------------------------------------------------------


def test_a_scoped_sync_reads_only_hot_files_not_proved_current(home, upper, trusted_clock, monkeypatch):
    for name in ("cold", "hot", "current"):
        _write(home, f"worlds/realm/lore/{name}.md", name)
    _read(home, upper, "worlds/realm/lore/hot.md")
    _read(home, upper, "worlds/realm/lore/current.md")
    _write(home, "worlds/realm/lore/hot.md", "hot, edited by an agent")
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self.name) or real(self))
    report = cache_sync.run(worlds=["realm"])
    assert reads == ["hot.md"]
    assert [(p.given, p.status) for p in report.paths if p.status not in ("current", "cold")] == [
        ("worlds/realm/lore/hot.md", "refreshed")]
    shown = report.as_dict()
    assert shown["listed"] == "notable"
    assert [p["path"] for p in shown["paths"]] == ["worlds/realm/lore/hot.md"]
    assert shown["counts"]["cold"] == 1 and shown["counts"]["current"] == 1


def test_a_read_that_refreshed_sources_does_not_hide_a_stale_built_from(home, upper, trusted_clock):
    """A GET hashed the edited file, so its `sources` row is current -- but
    the kind was never rebuilt, and `built_from` says so."""
    _write(home, "worlds/realm/lore/pact.md", "first")
    _read(home, upper, "worlds/realm/lore/pact.md")
    path = _write(home, "worlds/realm/lore/pact.md", "second, by hand")
    with compiled.batch(home) as request:
        request.validate(compiled.SYNC_SOURCES, [compiled.Input("body", path)])
    with compiled.batch(home) as request:
        assert request.known_hashes(compiled.SYNC_SOURCES, [path])[path] == hashlib.sha256(
            b"second, by hand").hexdigest()
    report = cache_sync.run(worlds=["realm"])
    assert _by_path(report)["worlds/realm/lore/pact.md"].status == "refreshed"


def test_a_missing_scope_is_not_found_and_purges_nothing(home, monkeypatch):
    purges = []
    monkeypatch.setattr(compiled, "purge_for_delete", purges.append)
    report = cache_sync.run(campaigns=["mara-typo"], worlds=["../realm"])
    assert {(p.given, p.reason) for p in report.paths} == {
        ("campaigns/mara-typo", "not_found"), ("../realm", "bad_id")}
    assert purges == [] and not report.ok


def test_all_lists_every_syncable_directory_and_nothing_else(home):
    _write(home, "worlds/realm/lore/pact.md", "x")
    _write(home, "campaigns/saltmarch/plot.json", "{}")
    _write(home, "logs/2026-10.jsonl", "{}")
    _write(home, ".cache/compiled/x", "x")
    report = cache_sync.run(all_records=True)
    assert report.counts()["cold"] == 2 and report.tokens_bumped == 1


def test_a_scoped_campaign_bumps_its_token_even_when_everything_is_cold(home):
    _write(home, "campaigns/saltmarch/plot.json", "{}")
    before = revision.current("saltmarch")
    report = cache_sync.run(campaigns=["saltmarch"])
    assert report.tokens_bumped == 1 and revision.current("saltmarch") != before


# -- deletes and the root purge ----------------------------------------------


def test_a_deleted_campaign_root_purges_and_a_plain_path_does_not(home, monkeypatch):
    purges = []
    monkeypatch.setattr(compiled, "purge_for_delete", purges.append)
    report = cache_sync.run(["campaigns/winifred"])
    assert [p.status for p in report.paths] == ["missing"] and purges == []
    report = cache_sync.run(campaigns=["winifred"])
    assert purges == [] and report.purged is None
    report = cache_sync.run(deleted=["campaigns/winifred"])
    assert purges == [home] and report.purged is True and report.ok


def test_a_deleted_file_does_not_purge_and_a_dry_run_never_does(home, monkeypatch):
    purges = []
    monkeypatch.setattr(compiled, "purge_for_delete", purges.append)
    cache_sync.run(deleted=["campaigns/saltmarch/gone.json"])
    cache_sync.run(deleted=["worlds/vanished"], dry_run=True)
    assert purges == []


# -- renames -----------------------------------------------------------------


def _mark_units(home, rel, units):
    path = home / rel
    with compiled.batch(home) as request:
        for unit in units:
            request.mark_materialized(path, "a:passages", instance={"unit": unit},
                                      built_from=hashlib.sha256(path.read_bytes()).hexdigest())


def test_a_stated_rename_carries_units_and_old_is_an_intended_delete(home):
    _write(home, "campaigns/saltmarch/scenes/old.md", "transcript")
    _mark_units(home, "campaigns/saltmarch/scenes/old.md", (1, 4, 9))
    (home / "campaigns/saltmarch/scenes/old.md").rename(home / "campaigns/saltmarch/scenes/new.md")
    seen = {}

    class Units:
        name, on_write = "units", "quiet"

        def owns(self, kind):
            return kind == "a:passages"

        def local(self, ctx, hot):
            seen.update({rel: sorted(json.loads(r.instance)["unit"] for r in rows) for rel, rows in hot.items()})
            return cache_sync.LocalResult()

        def network(self, ctx, plan):
            return cache_sync.NetworkResult()

    report = cache_sync.sync_paths([], mode="explicit", hooks=(Units(),),
                                   renamed={"campaigns/saltmarch/scenes/old.md": "campaigns/saltmarch/scenes/new.md"})
    assert seen == {"campaigns/saltmarch/scenes/new.md": [1, 4, 9]}
    assert {(p.given, p.status) for p in report.paths} >= {("campaigns/saltmarch/scenes/old.md", "deleted")}
    assert report.tokens_bumped == 1
    with compiled.batch(home) as request:
        rows = request.materialized([home / "campaigns/saltmarch/scenes/new.md"])[
            home / "campaigns/saltmarch/scenes/new.md"]
    assert sorted(json.loads(r.instance)["unit"] for r in rows) == [1, 4, 9]
    assert {r.built_from for r in rows} == {""}


def test_a_dry_run_rename_writes_no_rows(home):
    _write(home, "campaigns/saltmarch/scenes/old.md", "transcript")
    _mark_units(home, "campaigns/saltmarch/scenes/old.md", (2,))
    (home / "campaigns/saltmarch/scenes/old.md").rename(home / "campaigns/saltmarch/scenes/new.md")
    report = cache_sync.sync_paths([], mode="explicit", dry_run=True,
                                   renamed={"campaigns/saltmarch/scenes/old.md": "campaigns/saltmarch/scenes/new.md"})
    assert _by_path(report)["campaigns/saltmarch/scenes/new.md"].kinds == {"a:passages": "lazy"}
    with compiled.batch(home) as request:
        new = home / "campaigns/saltmarch/scenes/new.md"
        assert request.materialized([new])[new] == ()


def test_a_rename_whose_old_path_still_exists_is_refused(home):
    _write(home, "worlds/realm/lore/a.md", "x")
    _write(home, "worlds/realm/lore/b.md", "x")
    report = cache_sync.sync_paths([], mode="explicit",
                                   renamed={"worlds/realm/lore/a.md": "worlds/realm/lore/b.md"})
    assert _by_path(report)["worlds/realm/lore/a.md"].reason == "not_deleted"


def test_the_cli_parses_a_rename(home, capsys):
    _write(home, "worlds/realm/lore/b.md", "x")
    code, out = _run(["sync", "--json", "--renamed", "worlds/realm/lore/a.md=worlds/realm/lore/b.md"], capsys)
    statuses = {p["path"]: p["status"] for p in json.loads(out.out)["paths"]}
    assert code == 0 and statuses == {"worlds/realm/lore/a.md": "deleted", "worlds/realm/lore/b.md": "cold"}


# -- collecting() ------------------------------------------------------------


def test_collecting_syncs_what_the_block_wrote(home, upper):
    _write(home, "worlds/realm/lore/pact.md", "first")
    _read(home, upper, "worlds/realm/lore/pact.md")
    with cache_sync.collecting() as synced:
        atomic.write_text(home / "worlds/realm/lore/pact.md", "second")
        atomic.write_text(home / "worlds/realm/lore/new.md", "brand new")
        atomic.append_line(home / "logs" / "2026-10.jsonl", "{}")
    report = synced.report
    assert {(p.given, p.status) for p in report.paths} == {
        ("worlds/realm/lore/pact.md", "refreshed"), ("worlds/realm/lore/new.md", "cold"),
        ("logs/2026-10.jsonl", "refused")}


def test_collecting_syncs_then_reraises_on_failure(home, upper):
    _write(home, "worlds/realm/lore/pact.md", "first")
    _read(home, upper, "worlds/realm/lore/pact.md")
    with pytest.raises(RuntimeError, match="half-applied"), cache_sync.collecting() as synced:
        atomic.write_text(home / "worlds/realm/lore/pact.md", "second")
        raise RuntimeError("half-applied")
    assert _by_path(synced.report)["worlds/realm/lore/pact.md"].status == "refreshed"


def test_collecting_hands_deletes_over_and_bumps(home):
    before = revision.current("saltmarch")
    with cache_sync.collecting(deleted=["campaigns/saltmarch/gone.json"]) as synced:
        pass
    assert [p.status for p in synced.report.paths] == ["deleted"]
    assert revision.current("saltmarch") != before


def test_collecting_is_isolated_from_an_enclosing_scope(home):
    with writeset.collecting() as outer, cache_sync.collecting():
        atomic.write_text(home / "worlds/realm/lore/pact.md", "x")
    assert str(home / "worlds/realm/lore/pact.md") not in outer.close()
