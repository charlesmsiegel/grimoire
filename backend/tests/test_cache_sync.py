"""The sync primitive and the `files` hook (spec 05-S2).

Every store is a `tmp_path` with placeholder names (Realm, Saltmarch, Mara).
The compiled cache is on, with a fresh process state per test; the kinds are
registered for the test, because 03 wired no single-file kind of its own.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from grimoire.store import cache_sync, compiled, logs, revision
from tests import cache_sync_fixtures as fx

# The shared fixtures, bound here so pytest finds them by name.
cache, home, upper = fx.cache, fx.home, fx.upper
SENTINEL, _by_path, _read, _write = fx.SENTINEL, fx._by_path, fx._read, fx._write


# -- classification ----------------------------------------------------------


def test_a_hot_path_is_rebuilt_then_current(home, upper):
    _write(home, "worlds/realm/lore/pact.md", "the pact")
    assert _read(home, upper, "worlds/realm/lore/pact.md") == "THE PACT"
    _write(home, "worlds/realm/lore/pact.md", "the pact, amended")
    report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit")
    item = _by_path(report)["worlds/realm/lore/pact.md"]
    assert (item.status, item.kinds) == ("refreshed", {"test:upper": "built"})
    assert report.hooks["files"]["built"] == 1
    again = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit")
    assert _by_path(again)["worlds/realm/lore/pact.md"].status == "current"
    computed = []
    upper_compute = upper.compute
    object.__setattr__(upper, "compute", lambda v, p: computed.append(1) or upper_compute(v, p))
    assert _read(home, upper, "worlds/realm/lore/pact.md") == "THE PACT, AMENDED"
    assert computed == []                      # the reader's next read was a hit


def test_a_cold_path_reads_nothing_and_runs_no_hook(home, upper, monkeypatch):
    _write(home, "worlds/realm/lore/pact.md", "never read by a hook")
    calls = []

    class Spy:
        name, on_write = "spy", "quiet"

        def owns(self, kind):
            return True

        def local(self, ctx, hot):
            calls.append(hot)
            return cache_sync.LocalResult()

        def network(self, ctx, plan):
            calls.append(plan)
            return cache_sync.NetworkResult()

    report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit", hooks=(Spy(),))
    assert _by_path(report)["worlds/realm/lore/pact.md"].status == "cold"
    assert calls == []


def test_a_kind_no_hook_owns_is_lazy(home):
    path = _write(home, "worlds/realm/lore/pact.md", "x")
    with compiled.batch(home) as request:
        request.mark_materialized(path, "overview:unclaimed")
    report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit")
    item = _by_path(report)["worlds/realm/lore/pact.md"]
    assert item.kinds == {"overview:unclaimed": "lazy"} and item.status == "current"


def test_hooks_run_in_order_and_every_local_phase_precedes_any_network_phase(home):
    path = _write(home, "worlds/realm/lore/pact.md", "x")
    with compiled.batch(home) as request:
        request.mark_materialized(path, "a:one")
        request.mark_materialized(path, "b:two")
    calls = []

    def hook(name, prefix):
        class Hook:
            on_write = "quiet"

            def owns(self, kind):
                return kind.startswith(prefix)

            def local(self, ctx, hot):
                calls.append(f"{name}.local:{sorted(r.kind for rows in hot.values() for r in rows)}")
                return cache_sync.LocalResult(outcomes={(rel, r.kind): "hit" for rel, rows in hot.items()
                                                        for r in rows}, plan=name)

            def network(self, ctx, plan):
                calls.append(f"{name}.network")
                return cache_sync.NetworkResult()
        Hook.name = name
        return Hook()

    cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit",
                          hooks=(hook("first", "a:"), hook("second", "b:")))
    assert calls == ["first.local:['a:one']", "second.local:['b:two']", "first.network", "second.network"]


def test_a_dry_run_and_no_embed_send_nothing_to_a_network_phase(home):
    path = _write(home, "worlds/realm/lore/pact.md", "x")
    with compiled.batch(home) as request:
        request.mark_materialized(path, "a:one")
    sent = []

    class Hook:
        name, on_write = "net", "quiet"

        def owns(self, kind):
            return True

        def local(self, ctx, hot):
            return cache_sync.LocalResult(plan="p")

        def network(self, ctx, plan):
            sent.append(plan)
            return cache_sync.NetworkResult()

    cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit", dry_run=True, hooks=(Hook(),))
    cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit", embed=False, hooks=(Hook(),))
    assert sent == []
    cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit", hooks=(Hook(),))
    assert sent == ["p"]


def test_write_mode_defers_an_explicit_hook(home):
    path = _write(home, "worlds/realm/lore/pact.md", "x")
    with compiled.batch(home) as request:
        request.mark_materialized(path, "a:one")
    ran = []

    class Hook:
        name, on_write = "search", "explicit"

        def owns(self, kind):
            return True

        def local(self, ctx, hot):
            ran.append(1)
            return cache_sync.LocalResult()

        def network(self, ctx, plan):
            return cache_sync.NetworkResult()

    report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="write", hooks=(Hook(),))
    assert ran == [] and _by_path(report)["worlds/realm/lore/pact.md"].kinds == {"a:one": "deferred"}


def test_a_failing_hook_is_a_status_and_the_next_hook_runs(home):
    path = _write(home, "worlds/realm/lore/pact.md", "x")
    with compiled.batch(home) as request:
        request.mark_materialized(path, "a:one")
        request.mark_materialized(path, "b:two")
    ran = []

    class Broken:
        name, on_write = "broken", "quiet"

        def owns(self, kind):
            return kind == "a:one"

        def local(self, ctx, hot):
            raise ValueError(SENTINEL)

        def network(self, ctx, plan):
            return cache_sync.NetworkResult()

    class Fine(Broken):
        name = "fine"

        def owns(self, kind):
            return kind == "b:two"

        def local(self, ctx, hot):
            ran.append(1)
            return cache_sync.LocalResult(outcomes={("worlds/realm/lore/pact.md", "b:two"): "hit"})

    report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit", hooks=(Broken(), Fine()))
    item = _by_path(report)["worlds/realm/lore/pact.md"]
    assert ran == [1]
    assert item.kinds == {"a:one": "failed:ValueError", "b:two": "hit"} and item.status == "failed"
    assert SENTINEL not in json.dumps(report.as_dict())


# -- validation (section 8) ---------------------------------------------------


def test_every_refusal_carries_its_reason(home, tmp_path, monkeypatch):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x")
    _write(home, "worlds/realm/lore/pact.md", "x")
    (home / "worlds" / "linked").symlink_to(home / "worlds" / "realm", target_is_directory=True)
    _write(home, ".cache/x.md", "x")
    _write(home, "logs/2026-10.jsonl", "x")
    _write(home, "llm_connections/openrouter.json", "x")
    _write(home, "config.md", "x")
    _write(home, "worlds/realm/lore/.pact.md.abcd1234.tmp", "x")
    os.mkfifo(home / "worlds" / "realm" / "pipe")
    report = cache_sync.sync_paths([
        str(outside), "../elsewhere.md", "worlds/../../elsewhere.md", "worlds/linked/lore/pact.md",
        ".cache/x.md", "logs/2026-10.jsonl", "llm_connections/openrouter.json", "config.md",
        "worlds/realm/lore/.pact.md.abcd1234.tmp", "worlds/realm/pipe"], mode="explicit")
    reasons = {p.given: p.reason for p in report.paths}
    assert reasons == {
        str(outside): "outside_store", "../elsewhere.md": "outside_store",
        "worlds/../../elsewhere.md": "outside_store", "worlds/linked/lore/pact.md": "link",
        ".cache/x.md": "not_syncable", "logs/2026-10.jsonl": "not_syncable",
        "llm_connections/openrouter.json": "not_syncable", "config.md": "not_syncable",
        "worlds/realm/lore/.pact.md.abcd1234.tmp": "temp_file", "worlds/realm/pipe": "not_a_file"}
    assert all(p.status == "refused" for p in report.paths) and not report.ok
    assert set(reasons.values()) <= set(cache_sync.REFUSAL_REASONS)


def test_an_absolute_path_inside_the_store_is_accepted_and_reported_as_given(home):
    path = _write(home, "worlds/realm/lore/pact.md", "x")
    report = cache_sync.sync_paths([str(path)], mode="explicit")
    assert [(p.given, p.status) for p in report.paths] == [(str(path), "cold")]


def test_a_batch_past_max_paths_is_truncated_and_says_so(home, monkeypatch):
    monkeypatch.setattr(cache_sync, "MAX_PATHS", 2)
    for name in ("a", "b", "c"):
        _write(home, f"worlds/realm/lore/{name}.md", "x")
    report = cache_sync.sync_paths([f"worlds/realm/lore/{n}.md" for n in "abc"], mode="explicit")
    assert report.truncated
    assert [p.reason for p in report.paths] == [None, None, "too_many"]


def test_a_directory_expands_to_its_files_skipping_temps_and_links(home):
    _write(home, "worlds/realm/lore/a.md", "x")
    _write(home, "worlds/realm/lore/b.md", "x")
    _write(home, "worlds/realm/lore/.a.md.abcd1234.tmp", "x")
    (home / "worlds" / "realm" / "lore" / "c.md").symlink_to(home / "worlds" / "realm" / "lore" / "a.md")
    report = cache_sync.sync_paths(["worlds/realm/lore"], mode="explicit")
    assert sorted(p.given for p in report.paths) == ["worlds/realm/lore/a.md", "worlds/realm/lore/b.md"]


def test_duplicates_are_coalesced(home):
    path = _write(home, "worlds/realm/lore/pact.md", "x")
    report = cache_sync.sync_paths(["worlds/realm/lore/pact.md", str(path),
                                    "worlds/realm/./lore/pact.md"], mode="explicit")
    assert len(report.paths) == 1


def test_write_mode_skips_refused_and_missing_paths_silently(home):
    report = cache_sync.sync_paths(["logs/x.jsonl", "worlds/realm/gone.md", "usage/2026-10.jsonl"],
                                   mode="write")
    assert report.paths == [] and report.ok


# -- missing versus deleted, and the token -----------------------------------


def test_missing_bumps_nothing_and_deleted_bumps_once(home):
    before = revision.current("saltmarch")
    report = cache_sync.sync_paths(["campaigns/saltmarch/commitments.json"], mode="explicit")
    assert [(p.status, p.reason) for p in report.paths] == [("missing", None)]
    assert not report.ok and revision.current("saltmarch") == before and report.tokens_bumped == 0
    report = cache_sync.sync_paths([], deleted=["campaigns/saltmarch/commitments.json"], mode="explicit")
    assert [p.status for p in report.paths] == ["deleted"] and report.ok
    assert report.tokens_bumped == 1 and revision.current("saltmarch") != before


def test_a_deleted_path_that_still_exists_is_refused(home):
    _write(home, "campaigns/saltmarch/plot.json", "{}")
    report = cache_sync.sync_paths([], deleted=["campaigns/saltmarch/plot.json"], mode="explicit")
    assert [(p.status, p.reason) for p in report.paths] == [("refused", "not_deleted")]


def test_the_token_bumps_once_per_campaign_before_the_first_rebuild(home, monkeypatch):
    path_a = _write(home, "campaigns/saltmarch/plot.json", "{}")
    path_b = _write(home, "campaigns/saltmarch/commitments.json", "[]")
    _write(home, "worlds/realm/lore/pact.md", "x")
    for path in (path_a, path_b):
        with compiled.batch(home) as request:
            request.mark_materialized(path, "a:one")
    bumps = []
    real = revision.bump
    monkeypatch.setattr(revision, "bump", lambda cid: bumps.append(cid) or real(cid))
    seen_at_rebuild = []

    class Hook:
        name, on_write = "h", "quiet"

        def owns(self, kind):
            return True

        def local(self, ctx, hot):
            seen_at_rebuild.append(list(bumps))
            raise RuntimeError("killed mid-batch")

        def network(self, ctx, plan):
            return cache_sync.NetworkResult()

    report = cache_sync.sync_paths(["campaigns/saltmarch/plot.json", "campaigns/saltmarch/commitments.json",
                                    "worlds/realm/lore/pact.md"], mode="explicit", hooks=(Hook(),))
    assert bumps == ["saltmarch"] and seen_at_rebuild == [["saltmarch"]]
    assert report.tokens_bumped == 1


def test_a_world_path_bumps_nothing_and_write_mode_bumps_nothing(home, monkeypatch):
    _write(home, "worlds/realm/lore/pact.md", "x")
    _write(home, "campaigns/saltmarch/plot.json", "{}")
    bumps = []
    monkeypatch.setattr(revision, "bump", bumps.append)
    cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit")
    cache_sync.sync_paths(["campaigns/saltmarch/plot.json"], mode="write")
    assert bumps == []


def test_a_dry_run_stores_and_bumps_nothing(home, upper, monkeypatch):
    _write(home, "campaigns/saltmarch/notes.md", "first")
    _read(home, upper, "campaigns/saltmarch/notes.md")
    _write(home, "campaigns/saltmarch/notes.md", "second")
    bumps = []
    monkeypatch.setattr(revision, "bump", bumps.append)
    report = cache_sync.sync_paths(["campaigns/saltmarch/notes.md"], mode="explicit", dry_run=True)
    item = _by_path(report)["campaigns/saltmarch/notes.md"]
    assert item.kinds == {"test:upper": "planned"} and item.status == "refreshed"
    assert bumps == [] and report.dry_run
    again = cache_sync.sync_paths(["campaigns/saltmarch/notes.md"], mode="explicit")
    assert _by_path(again)["campaigns/saltmarch/notes.md"].kinds == {"test:upper": "built"}


# -- verify, the cache switch and the lane -----------------------------------


def test_verify_catches_a_kind_whose_key_omits_an_input(home, monkeypatch, cache):
    """The compute reads a sibling it never declared, so the key cannot see it."""
    sibling = _write(home, "worlds/realm/lore/suffix.txt", "!")
    kind = compiled.Kind("test:leaky", 1, (), (), True)
    monkeypatch.setitem(compiled.KINDS, kind.name, kind)
    recipe = cache_sync.FileRecipe(kind, "body", lambda path, instance: {},
                                   lambda view, params: view.files[0].data.decode() + sibling.read_text())
    monkeypatch.setitem(cache_sync.FILE_RECIPES, kind.name, recipe)
    _write(home, "worlds/realm/lore/pact.md", "pact")
    _read(home, recipe, "worlds/realm/lore/pact.md")
    assert _by_path(cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit",
                                          verify=True))["worlds/realm/lore/pact.md"].status == "current"
    sibling.write_text("?")
    report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit", verify=True)
    assert _by_path(report)["worlds/realm/lore/pact.md"].status == "verify_failed" and not report.ok


def test_the_cache_off_is_a_successful_no_op_that_still_tells_the_app(home, upper, monkeypatch):
    _write(home, "campaigns/saltmarch/notes.md", "x")
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    report = cache_sync.sync_paths(["campaigns/saltmarch/notes.md"], mode="explicit")
    assert (report.cache, report.cache_reason) == ("off", "disabled")
    assert [p.status for p in report.paths] == ["cold"] and report.ok and report.tokens_bumped == 1


def test_a_write_batch_that_cannot_take_the_lane_gives_its_paths_back(home, upper):
    _write(home, "worlds/realm/lore/pact.md", "x")
    _read(home, upper, "worlds/realm/lore/pact.md")
    with cache_sync._lane:
        report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="write")
    assert report.stopped == "lane_busy"
    assert [p.status for p in report.paths] == ["unsynced"]


def test_a_moved_root_stops_the_batch(home, tmp_path, monkeypatch):
    path = _write(home, "worlds/realm/lore/pact.md", "x")
    with compiled.batch(home) as request:
        request.mark_materialized(path, "a:one")
    ran = []
    other = tmp_path / "other"
    other.mkdir()

    class Mover:
        name, on_write = "mover", "quiet"

        def owns(self, kind):
            return True

        def local(self, ctx, hot):
            ran.append(self.name)
            monkeypatch.setenv("GRIMOIRE_HOME", str(other))
            return cache_sync.LocalResult()

        def network(self, ctx, plan):
            return cache_sync.NetworkResult()

    class Later(Mover):
        name = "later"

    report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit", hooks=(Mover(), Later()))
    assert ran == ["mover"] and report.stopped == "root_moved"


def test_a_stop_signal_stops_between_paths(home, upper):
    for name in "ab":
        _write(home, f"worlds/realm/lore/{name}.md", "x")
        _read(home, upper, f"worlds/realm/lore/{name}.md")
    stop = threading.Event()
    stop.set()
    report = cache_sync.sync_paths(["worlds/realm/lore/a.md", "worlds/realm/lore/b.md"],
                                   mode="explicit", stop=stop.is_set)
    assert report.stopped == "cancelled"
    assert {p.status for p in report.paths} == {"unsynced"} and not report.ok


def test_an_unknown_mode_is_a_programming_error(home):
    with pytest.raises(ValueError):
        cache_sync.sync_paths([], mode="eager")


# -- privacy (section 9) -----------------------------------------------------


def test_the_report_and_the_log_never_carry_a_body(home, monkeypatch, cache):
    logs.install()
    kind = compiled.Kind("test:json", 1, (), (), True)
    monkeypatch.setitem(compiled.KINDS, kind.name, kind)
    recipe = cache_sync.FileRecipe(kind, "body", lambda path, instance: {},
                                   lambda view, params: json.loads(view.files[0].data))
    monkeypatch.setitem(cache_sync.FILE_RECIPES, kind.name, recipe)
    _write(home, "campaigns/saltmarch/plot.json", json.dumps({"note": SENTINEL}))
    _read(home, recipe, "campaigns/saltmarch/plot.json")
    _write(home, "campaigns/saltmarch/plot.json", '{"note": "' + SENTINEL + '" broken')
    _write(home, "worlds/realm/lore/pact.md", SENTINEL)
    report = cache_sync.sync_paths(["campaigns/saltmarch/plot.json", "worlds/realm/lore/pact.md"],
                                   mode="explicit", verify=True)
    as_json = json.dumps(report.as_dict())
    assert _by_path(report)["campaigns/saltmarch/plot.json"].kinds == {"test:json": "failed:JSONDecodeError"}
    assert SENTINEL not in as_json
    logged = "".join(p.read_text(errors="replace") for p in (home / "logs").glob("*"))
    assert SENTINEL not in logged


def test_the_report_uses_only_the_exported_vocabulary(home, upper):
    _write(home, "worlds/realm/lore/pact.md", "x")
    _read(home, upper, "worlds/realm/lore/pact.md")
    _write(home, "worlds/realm/lore/pact.md", "y")
    out = cache_sync.sync_paths(["worlds/realm/lore/pact.md", "logs/x", "worlds/gone.md"],
                                mode="explicit").as_dict()
    assert tuple(out) == cache_sync.BATCH_FIELDS
    for item in out["paths"]:
        assert item["status"] in cache_sync.PATH_STATUSES
        assert item.get("reason") in (None, *cache_sync.REFUSAL_REASONS)
        for outcome in item.get("kinds", {}).values():
            assert outcome.split(":", 1)[0] in cache_sync.KIND_OUTCOMES
    assert set(out["counts"]) == set(cache_sync.PATH_STATUSES)
    assert set(out["embedding"]) == set(cache_sync.EMBEDDING_FIELDS)


# -- review regressions ------------------------------------------------------


def test_a_stop_inside_a_hook_leaves_the_unreached_paths_unsynced(home, upper):
    for name in "abc":
        _write(home, f"worlds/realm/lore/{name}.md", name)
        _read(home, upper, f"worlds/realm/lore/{name}.md")
        _write(home, f"worlds/realm/lore/{name}.md", f"{name}, edited")
    built = []
    real = upper.compute
    object.__setattr__(upper, "compute", lambda v, p: built.append(1) or real(v, p))
    report = cache_sync.sync_paths([f"worlds/realm/lore/{n}.md" for n in "abc"], mode="explicit",
                                   stop=lambda: bool(built))
    statuses = [p.status for p in report.paths]
    assert statuses == ["refreshed", "unsynced", "unsynced"]
    assert report.stopped == "cancelled" and not report.ok


def test_an_unreadable_history_is_unsynced_never_cold(home, upper, monkeypatch):
    _write(home, "worlds/realm/lore/pact.md", "x")
    _read(home, upper, "worlds/realm/lore/pact.md")
    monkeypatch.setattr(compiled, "STRICT_LOCK_SECONDS", 0.01)
    with compiled._state.lock:                         # a concurrent reader holds the cache
        report = cache_sync.sync_paths(["worlds/realm/lore/pact.md"], mode="explicit")
    assert [p.status for p in report.paths] == ["unsynced"] and not report.ok


def test_a_cancel_still_bumps_the_token(home):
    _write(home, "campaigns/saltmarch/plot.json", "{}")
    report = cache_sync.sync_paths(["campaigns/saltmarch/plot.json"], mode="explicit", stop=lambda: True)
    assert report.tokens_bumped == 1 and report.stopped == "cancelled"


def test_only_a_path_inside_an_existing_campaign_bumps(home, monkeypatch):
    _write(home, "campaigns/notes.md", "x")
    bumps = []
    monkeypatch.setattr(revision, "bump", bumps.append)
    report = cache_sync.sync_paths(["campaigns/notes.md"], deleted=["campaigns/winifred",
                                                                      "campaigns/vanished/plot.json"],
                                   mode="explicit")
    assert bumps == [] and report.tokens_bumped == 0


def test_duplicates_do_not_use_up_the_quota(home, monkeypatch):
    monkeypatch.setattr(cache_sync, "MAX_PATHS", 2)
    for name in "ab":
        _write(home, f"worlds/realm/lore/{name}.md", "x")
    report = cache_sync.sync_paths(["worlds/realm/lore/a.md", "worlds/realm/lore/a.md",
                                    "worlds/realm/lore/b.md"], mode="explicit")
    assert [p.status for p in report.paths] == ["cold", "cold"] and not report.truncated


def test_the_top_level_directory_is_matched_without_case(home):
    _write(home, "worlds/realm/lore/pact.md", "x")
    report = cache_sync.sync_paths(["Worlds/realm/lore/pact.md"], mode="explicit")
    item = report.paths[0]
    assert item.reason != "not_syncable"
    assert item.rel == "worlds/realm/lore/pact.md"
