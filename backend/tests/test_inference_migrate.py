"""The migration from the legacy settings layout to format 2 (slice C, Task 2).

Backup first, marker last; providers, model facts, roles, routes and split
routes from `translate.global_view` verbatim; each campaign under its own
no-wait lock. Behaviour-neutrality over the two frozen fixtures lives in the
equivalence tests (`test_each_baseline_state_resolves_identically_after_migration`);
what is here is the migration's own contract.

Invented names (Realm, Saltmarch, Mara, Winifred) and fake keys only.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import shutil
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from grimoire import main, routes
from grimoire import store as store_pkg
from grimoire.store import (
    backups,
    campaigns,
    config,
    inference_keys,
    llm_connections,
    locks,
    revision,
    worlds,
)
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import facts, migrate
from tests.fixtures.frozen_campaign import sweep as frozen
from tests.llm_fakes import FakeOpenRouter

FORMAT = inference_keys.FORMAT_KEY
WAIT = 10.0
_REAL_WRITE = config.write_config


@pytest.fixture()
def home(monkeypatch, tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(root))
    return root


# ---- building a legacy store ----

def _legacy() -> None:
    """A legacy store: `config.md` without the marker, the seeded connections,
    and an OpenRouter key and model."""
    config.read_config()
    llm_connections.update_connection("openrouter", api_key="sk-test-active",
                                      model="vendor/active")
    assert not inference_keys.is_current(config.read_config())


def _campaign(name: str = "Saltmarch") -> str:
    existing = worlds.list_worlds()
    wid = existing[0]["id"] if existing else worlds.create_world("Realm")
    return campaigns.create_campaign(name, wid)


def _meta(cid: str) -> dict:
    meta, _ = parse_frontmatter(
        campaigns.paths.campaign_meta_path(cid).read_text(encoding="utf-8"))
    return meta


def _raw_config(root: Path) -> dict:
    meta, _ = parse_frontmatter((root / "config.md").read_text(encoding="utf-8"))
    return meta


def _digest(root: Path, *, skip: tuple[str, ...] = (".cache", "backups")) -> dict[str, str]:
    """Every file under `root` (minus `skip` at the top) -> its sha256."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[0] in skip or not path.is_file():
            continue
        out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _safety_archives(root: Path) -> list[str]:
    d = root / "backups"
    return sorted(p.name for p in d.iterdir()
                  if p.name.startswith(backups.SAFETY_PREFIX)) if d.exists() else []


# ---- the shape of a migrated store ----

def test_a_legacy_store_migrates_roles_routes_and_marker_last(home):
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare", sampler_preset="warm")
    config.write_config(route_summary="spare", preset_scene="cold",
                        fallback_connection_id="spare")
    assert migrate.status().state == "pending"

    got = migrate.ensure()

    assert got.state == "done", got
    cfg = _raw_config(home)
    assert cfg[FORMAT] == "2"
    assert cfg["role_primary_provider"] == "openrouter"
    assert cfg["role_primary_model"] == "vendor/active"
    for role in inference_keys.GENERATIVE_ROLES:
        assert cfg[f"role_{role}_fallback_provider"] == "spare"
        assert cfg[f"role_{role}_fallback_preset"] == "warm"
    # Fast and Decision stay unset: they inherit.
    assert not cfg.get("role_fast_provider") and not cfg.get("role_decision_provider")
    assert cfg["use_summary"] == "model"
    assert (cfg["use_summary_provider"], cfg["use_summary_model"],
            cfg["use_summary_preset"]) == ("spare", "vendor/spare", "warm")
    # Split routes copy their parents.
    assert cfg["use_scene_break"] == "model"
    assert cfg["use_scene_break_provider"] == "spare"
    assert cfg["preset_speaker"] == "cold"
    # The legacy keys stay, frozen, for older builds.
    assert cfg["route_summary"] == "spare" and cfg["active_connection_id"] == "openrouter"
    # The safety backup exists.
    assert len(_safety_archives(home)) == 1


def test_migration_is_idempotent(home):
    _legacy()
    config.write_config(route_dossier="openrouter", preset_tracker="cold")
    cid = _campaign()
    campaigns.set_campaign_routing(cid, {"route_scene": "openrouter"})
    assert migrate.ensure().state == "done"
    before = _digest(home, skip=())

    assert migrate.ensure().state == "done"

    assert _digest(home, skip=()) == before


def test_a_failed_backup_writes_nothing_and_says_why(home, monkeypatch):
    _legacy()
    cid = _campaign()
    before = _digest(home)

    def broken(**_kw):
        raise OSError("disk full")

    monkeypatch.setattr(backups, "create_backup", broken)
    got = migrate.ensure()

    assert got.state == "failed"
    assert "disk full" in got.reason
    assert _digest(home) == before
    assert migrate.status() == got
    assert FORMAT not in _meta(cid)

    # The next start retries, and succeeds.
    monkeypatch.undo()
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    assert migrate.ensure().state == "done"
    assert migrate.status().reason == ""


def test_a_busy_backup_is_a_failure_too(home, monkeypatch):
    _legacy()

    def busy(**_kw):
        raise locks.BackupBusy("backups")

    monkeypatch.setattr(backups, "create_backup", busy)
    got = migrate.ensure()
    assert got.state == "failed" and got.reason
    assert not inference_keys.is_current(config.read_config())


def test_the_safety_backup_survives_retention(home):
    _legacy()
    assert migrate.ensure().state == "done"
    safety = _safety_archives(home)
    assert len(safety) == 1
    # Listed as a restore point...
    assert safety[0] in {row["name"] for row in backups.list_backups()}
    for n in range(3):
        backups.create_backup(when=_at(n))

    backups.sweep(keep=1)

    # The safety archive is untouched and takes none of the `keep` places:
    # exactly one ordinary archive is left beside it.
    assert _safety_archives(home) == safety
    assert len([r["name"] for r in backups.list_backups()
                if not r["name"].startswith(backups.SAFETY_PREFIX)]) == 1


def _at(n: int):
    from datetime import UTC, datetime
    return datetime(2030, 1, 1, 0, 0, n, tzinfo=UTC)


def test_create_backup_refuses_an_unknown_prefix(home):
    _legacy()
    with pytest.raises(ValueError):
        backups.create_backup(prefix="whatever-")


def test_rev_is_preserved(home):
    _legacy()
    rev = llm_connections.read_connection_raw("openrouter")["rev"]
    llm_connections.set_cached_models("openrouter", [{"id": "vendor/active"}], rev)
    assert migrate.ensure().state == "done"
    after = llm_connections.read_connection_raw("openrouter")
    assert after["rev"] == rev
    assert after["preset"] == "openrouter" and after["billing"] == "metered"
    assert llm_connections.cached_row("openrouter", "vendor/active") is not None
    claude = llm_connections.read_connection_raw("claude")
    assert (claude["preset"], claude["billing"]) == ("claude", "subscription")


def test_ollama_and_zai_urls_get_their_presets(home):
    _legacy()
    made = {
        "ollama": llm_connections.create_connection(
            "openai_compatible", "box", base_url="http://localhost:11434/v1", model="m"),
        "lmstudio": llm_connections.create_connection(
            "openai_compatible", "studio", base_url="http://127.0.0.1:1234/v1", model="m"),
        "zai": llm_connections.create_connection(
            "openai_compatible", "zed", base_url="https://api.z.ai/api/paas/v4", model="m"),
        "zai_coding": llm_connections.create_connection(
            "openai_compatible", "zed code", base_url="https://api.z.ai/api/coding/paas/v4",
            model="m"),
        "custom": llm_connections.create_connection(
            "openai_compatible", "elsewhere", base_url="https://saltmarch.invalid/v1",
            model="m"),
    }
    assert migrate.ensure().state == "done"
    for preset, conn_id in made.items():
        raw = llm_connections.read_connection_raw(conn_id)
        assert raw["preset"] == preset, conn_id
    assert llm_connections.read_connection_raw(made["zai_coding"])["billing"] == "subscription"
    assert llm_connections.read_connection_raw(made["zai"])["billing"] == "metered"


def test_a_connection_that_already_has_a_preset_is_left_alone(home):
    _legacy()
    llm_connections.update_connection("openrouter", preset="openrouter", billing="subscription")
    before = (home / "llm_connections" / "openrouter.md").read_bytes()
    assert migrate.ensure().state == "done"
    assert (home / "llm_connections" / "openrouter.md").read_bytes() == before


def test_model_facts_are_written_from_the_legacy_fields(home):
    _legacy()
    llm_connections.update_connection("openrouter", vision="off", prefill=True,
                                      post_process="strict")
    llm_connections.update_connection("claude", model="")
    llm_connections.create_connection("openai_compatible", "blank",
                                      base_url="http://localhost:1234/v1", vision="on")
    assert migrate.ensure().state == "done"

    def stated(conn_id: str) -> dict:
        path = llm_connections.facts_path(conn_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    copied = {"vision": "off", "prefill": True, "post_process": "strict"}
    # Beside the copy, a record of it: what makes a resumed run's copy re-doable.
    assert stated("openrouter") == {
        "vendor/active": {**copied, facts.MIGRATED_KEY: copied}}
    # Nothing was stated for Claude, so nothing is written for it.
    assert stated("claude") == {}
    # Any other empty model is keyed "".
    assert stated("blank") == {"": {"vision": "on", facts.MIGRATED_KEY: {"vision": "on"}}}
    assert facts.of("blank", "", "")["vision"] == "on"


def test_a_legacy_model_edit_during_the_campaigns_lands_in_the_facts_or_is_refused(
        home, monkeypatch):
    """The facts are copied in the hold that switches, and the campaigns are
    migrated after it, so a legacy model edit made by the time the campaigns
    run is refused (the store is at format 2) rather than accepted and then
    switched past: whatever the connection says, its model's facts say too."""
    _legacy()
    _campaign("Saltmarch")
    real = migrate._campaign_step

    def edited_meanwhile(cid, skipped):
        for conn_id, fields in (("openrouter", {"vision": "off", "prefill": True}),
                                ("claude", {"model": "vendor/late", "prefill": True})):
            with pytest.raises(llm_connections.ModelFieldsRefusedError):
                llm_connections.update_connection(conn_id, refuse_model_fields=True,
                                                  **fields)
        real(cid, skipped)

    monkeypatch.setattr(migrate, "_campaign_step", edited_meanwhile)
    assert migrate.ensure().state == "done"
    assert inference_keys.is_current(config.read_config())
    conn = llm_connections.read_connection_raw("openrouter")
    stated = facts.of("openrouter", "vendor/active", "")
    assert (stated["vision"], bool(stated["prefill"])) == (conn["vision"], conn["prefill"])


def test_a_campaign_is_not_migrated_while_its_connection_can_still_move(home, monkeypatch):
    """A campaign's routes are translated from the connections they name. Were
    it migrated while the store was still at format 1, a legacy `model` edit
    landing next (from this server or another) would be switched past: the
    store's roles follow it, the campaign's pin keeps the old model. The
    campaigns come after the marker, where that edit is refused."""
    _legacy()
    cid = _campaign("Saltmarch")
    campaigns.set_campaign_routing(cid, {"route_scene": "openrouter"})
    real = migrate._campaign_step

    def then_edited(cid, skipped):
        real(cid, skipped)
        # The provider editor's write, as the route makes it.
        with contextlib.suppress(llm_connections.ModelFieldsRefusedError):
            llm_connections.update_connection("openrouter", refuse_model_fields=True,
                                              model="vendor/late")

    monkeypatch.setattr(migrate, "_campaign_step", then_edited)
    assert migrate.ensure().state == "done"
    model = llm_connections.read_connection_raw("openrouter")["model"]
    assert _meta(cid)[inference_keys.pin_key("scene", "model")] == model
    assert _raw_config(home)["role_primary_model"] == model


def test_a_campaign_created_while_the_migration_runs_is_moved_too(home, monkeypatch):
    """The run lists the campaigns to move. One created after that list was
    taken but before the switch is born at format 1, unmarked; listed only
    once, it was left behind and the status went back to pending. The list
    is taken after the switch, where every campaign born later is marked."""
    _legacy()
    _campaign("Saltmarch")
    real = migrate._switch
    made: list[str] = []

    def created_then_switched(run):
        made.append(_campaign("Winifred"))         # another tab, mid-run
        return real(run)

    monkeypatch.setattr(migrate, "_switch", created_then_switched)
    assert migrate.ensure().state == "done"
    assert inference_keys.is_current(_meta(made[0]))
    assert migrate.status().state == "done"


def test_a_fork_landing_after_the_migrations_list_is_born_translated_and_marked(
        home, monkeypatch):
    """A fork copies its source's `campaign.md`, legacy route keys and all. A
    fork of a campaign the migration has not reached yet, landing after the
    migration listed the campaigns to move, was born unmarked and left behind
    (the status went back to pending). It is born through the same seam as a
    created campaign: translated as the migration translates, and marked."""
    from grimoire.store import fork

    _legacy()
    source = _campaign("Saltmarch")
    campaigns.set_campaign_routing(source, {"route_scene": "openrouter"})
    real = migrate._campaign_marks
    forks: list[str] = []

    def listed_then_forked():
        out = real()
        if inference_keys.is_current(config.read_config()) and not forks:
            forks.append(fork.fork_campaign(source, "Saltmarch Branch")["id"])
        return out

    monkeypatch.setattr(migrate, "_campaign_marks", listed_then_forked)
    assert migrate.ensure().state == "done"
    branch = _meta(forks[0])
    assert inference_keys.is_current(branch)
    assert branch[inference_keys.pin_key("scene", "provider")] == "openrouter"
    assert branch[inference_keys.pin_key("scene", "model")] == _meta(source)[
        inference_keys.pin_key("scene", "model")]
    assert migrate.status().state == "done"


def test_a_campaign_born_across_the_switch_is_born_marked(home, monkeypatch):
    """The format a new campaign is stamped with is read in the hold that
    writes its `campaign.md` -- `config_lock`, which the switch writes its
    marker in -- so a switch landing after creation's first look at the
    format still marks the campaign."""
    _legacy()
    real = campaigns.lifecycle._inference_marker
    calls: list[int] = []

    def switched_after_the_first_look():
        out = real()
        if not calls:                                # the pre-lock check
            assert migrate.ensure().state == "done"  # the switch lands here
        calls.append(1)
        return out

    monkeypatch.setattr(campaigns.lifecycle, "_inference_marker",
                        switched_after_the_first_look)
    cid = _campaign("Mara")
    assert inference_keys.is_current(config.read_config())
    assert inference_keys.is_current(_meta(cid))
    assert migrate.status().state == "done"


def test_a_newer_store_without_the_seeding_marker_is_left_byte_identical(home):
    """A store a newer build wrote, with no `llm_connections/.migrated`: the
    named-connection seeder ran before the newer-format check and created or
    replaced the seeded records and wrote `active_connection_id` into the
    newer `config.md`. Neither the migration nor a read of a connection may
    touch it."""
    (home / "config.md").write_text(dump_frontmatter(
        {FORMAT: "3", "provider": "openrouter", "openrouter_key": "sk-old"}, ""),
        encoding="utf-8")
    conns = home / "llm_connections"
    conns.mkdir()
    (conns / "openrouter.md").write_text(dump_frontmatter(
        {"kind": "openrouter", "name": "Saltmarch Router", "rev": "abc",
         "endpoint_family": "a field this build does not know"}, ""), encoding="utf-8")
    before = _digest(home)

    assert migrate.ensure().state == "newer"
    llm_connections.list_connections()
    llm_connections.read_connection("openrouter")

    assert _digest(home) == before
    assert not (conns / ".migrated").exists()


def test_an_unset_claude_models_facts_are_keyed_opus(home):
    _legacy()
    llm_connections.update_connection("claude", model="", prefill=True)
    assert migrate.ensure().state == "done"
    assert facts.of("claude", "opus", "")["prefill"] is True


def test_an_unset_claude_model_is_written_as_opus(home):
    _legacy()
    llm_connections.update_connection("claude", model="")
    config.write_config(active_connection_id="claude", route_voice="claude")
    assert migrate.ensure().state == "done"
    cfg = _raw_config(home)
    assert cfg["role_primary_provider"] == "claude"
    assert cfg["role_primary_model"] == "opus"
    assert cfg["use_voice_model"] == "opus"
    assert cfg["use_voice_drift_model"] == "opus"


def test_dangling_and_padded_references_stay_as_they_were(home):
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    config.write_config(route_voice="gone", fallback_connection_id="  spare  ",
                        route_dossier="  spare  ",
                        preset_summary=store_pkg.sampler_presets.PRESET_CLEAR)
    assert migrate.ensure().state == "done"
    cfg = _raw_config(home)
    # A route naming a connection that does not exist is a pin on that id.
    assert (cfg["use_voice"], cfg["use_voice_provider"]) == ("model", "gone")
    # The fallback id is used raw, padding and all (so it still fails to exist).
    assert cfg["role_primary_fallback_provider"] == "  spare  "
    # A route value is stripped, as the legacy cascade stripped it.
    assert cfg["use_dossier_provider"] == "spare"
    # PRESET_CLEAR carries over to the split route; the parent key is untouched.
    assert cfg["preset_scene_break"] == store_pkg.sampler_presets.PRESET_CLEAR
    assert cfg["preset_summary"] == store_pkg.sampler_presets.PRESET_CLEAR


def test_a_legacy_openrouter_embedding_stays_off(home):
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    config.write_config(embeddings_connection_id="spare", embeddings_model="vendor/embed")
    assert store_pkg.embed_space.resolve() is None
    assert migrate.ensure().state == "done"
    cfg = _raw_config(home)
    assert not cfg.get("role_embedding_provider") and not cfg.get("role_embedding_model")
    assert store_pkg.embed_space.resolve() is None


def test_a_working_legacy_embedding_becomes_the_embedding_role(home):
    _legacy()
    llm_connections.create_connection("openai_compatible", "vectors",
                                      base_url="http://localhost:1234/v1")
    config.write_config(embeddings_connection_id="vectors", embeddings_model="embed-small")
    before = store_pkg.embed_space.resolve()
    assert before is not None
    assert migrate.ensure().state == "done"
    cfg = _raw_config(home)
    assert (cfg["role_embedding_provider"], cfg["role_embedding_model"]) == (
        "vectors", "embed-small")
    assert store_pkg.embed_space.resolve() == before


# ---- campaigns ----

def test_a_campaign_is_migrated_with_its_marker(home):
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    cid = _campaign()
    campaigns.set_campaign_routing(cid, {"route_voice": "spare", "preset_scene": "warm"})
    assert migrate.ensure().state == "done"
    meta = _meta(cid)
    assert meta[FORMAT] == "2"
    assert (meta["use_voice"], meta["use_voice_provider"]) == ("model", "spare")
    assert meta["use_voice_drift_provider"] == "spare"
    assert meta["preset_speaker"] == "warm"
    assert meta["route_voice"] == "spare"


def test_a_busy_campaign_is_skipped_and_finished_next_time(home):
    _legacy()
    busy = _campaign("Saltmarch")
    free = _campaign("Winifred")
    campaigns.set_campaign_routing(busy, {"route_scene": "openrouter"})
    held, release = threading.Event(), threading.Event()

    def holder():
        with locks.campaign_lock(busy):
            held.set()
            release.wait(WAIT)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(WAIT)
        got = migrate.ensure()
    finally:
        release.set()
        t.join(WAIT)

    assert inference_keys.is_current(config.read_config())
    assert FORMAT not in _meta(busy)
    assert _meta(free)[FORMAT] == "2"
    assert got.state == "pending"
    assert any(busy in item for item in got.skipped), got
    safety = _safety_archives(home)

    # The global layout is current; the next ensure finishes the campaign
    # without another backup.
    got = migrate.ensure()
    assert got.state == "done", got
    assert _meta(busy)[FORMAT] == "2"
    assert _meta(busy)["use_scene_provider"] == "openrouter"
    assert _safety_archives(home) == safety


def test_each_migrated_campaign_bumps_its_revision(home):
    _legacy()
    one, two = _campaign("Saltmarch"), _campaign("Winifred")
    before = {cid: revision.current(cid) for cid in (one, two)}
    assert migrate.ensure().state == "done"
    for cid in (one, two):
        assert revision.current(cid) != before[cid]


def test_campaign_migration_does_not_reorder_the_library(home):
    _legacy()
    for name in ("Saltmarch", "Winifred", "Mara"):
        _campaign(name)
    before = [(c["id"], c["updated"]) for c in campaigns.list_campaigns()]
    assert migrate.ensure().state == "done"
    assert [(c["id"], c["updated"]) for c in campaigns.list_campaigns()] == before


def test_campaign_migrates_under_the_callers_lock(home):
    _legacy()
    cid = _campaign()
    campaigns.set_campaign_routing(cid, {"route_scene": "openrouter"})
    with pytest.raises(RuntimeError):
        migrate.campaign(cid)
    assert FORMAT not in _meta(cid)
    with locks.campaign_lock(cid):
        assert migrate.campaign(cid) is True
        assert migrate.campaign(cid) is False
    assert _meta(cid)["use_scene"] == "model"


def test_an_unmigratable_item_is_skipped_not_fatal(home):
    _legacy()
    good = _campaign("Saltmarch")
    bad = _campaign("Winifred")
    campaigns.paths.campaign_meta_path(bad).write_bytes(b"\xff\xfe not text")
    # A legacy field the facts writer refuses.
    llm_connections.update_connection("openrouter", post_process="sideways")

    got = migrate.ensure()

    assert inference_keys.is_current(config.read_config())
    assert _meta(good)[FORMAT] == "2"
    assert any(bad in item for item in got.skipped), got
    assert any("openrouter" in item for item in got.skipped), got
    assert got.state == "done"


def test_the_frozen_campaign_is_migrated_only_as_a_copy(monkeypatch, tmp_path):
    before = _digest(frozen.HOME, skip=())
    copy = tmp_path / "frozen"
    shutil.copytree(frozen.HOME, copy)
    monkeypatch.setenv("GRIMOIRE_HOME", str(copy))

    assert migrate.ensure().state == "done"

    assert _raw_config(copy)[FORMAT] == "2"
    assert _digest(frozen.HOME, skip=()) == before


def test_a_newer_format_store_is_not_migrated(home):
    _legacy()
    cid = _campaign()
    config.write_config(**{FORMAT: "3"})
    before = _digest(home, skip=())
    got = migrate.ensure()
    assert got.state == "newer"
    assert _digest(home, skip=()) == before
    assert FORMAT not in _meta(cid)


def test_a_fresh_store_is_not_migrated(home):
    got = migrate.ensure()
    assert got.state == "done"
    assert not (home / "config.md").exists()
    assert not (home / "backups").exists()


# ---- concurrency and the root ----

def _blocking_backup(monkeypatch):
    """Make the safety backup wait on `release` once it has started."""
    entered, release = threading.Event(), threading.Event()
    real = backups.create_backup

    def slow(**kw):
        entered.set()
        assert release.wait(WAIT)
        return real(**kw)

    monkeypatch.setattr(backups, "create_backup", slow)
    return entered, release


def test_two_concurrent_ensures_migrate_once(home, monkeypatch):
    _legacy()
    entered, release = _blocking_backup(monkeypatch)
    results = []
    t = threading.Thread(target=lambda: results.append(migrate.ensure()))
    t.start()
    try:
        assert entered.wait(WAIT)
        assert migrate.status().state == "running"
        second = migrate.ensure()          # returns at once, without waiting
        assert second.state == "running"
    finally:
        release.set()
        t.join(WAIT)
    assert results[0].state == "done"
    assert len(_safety_archives(home)) == 1


def test_a_root_switch_mid_run_writes_no_marker(home, monkeypatch, tmp_path):
    _legacy()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = backups.create_backup

    def then_move(**kw):
        made = real(**kw)
        monkeypatch.setenv("GRIMOIRE_HOME", str(elsewhere))
        return made

    monkeypatch.setattr(backups, "create_backup", then_move)
    got = migrate.ensure()

    assert got.state == "pending"
    assert got.reason
    assert not inference_keys.is_current(_raw_config(home))
    assert not _raw_config(home).get("role_primary_provider")
    assert list(elsewhere.iterdir()) == []


def test_a_root_switch_before_the_lock_migrates_the_new_root(home, monkeypatch, tmp_path):
    """The root is resolved inside the migration lock's hold: a data-dir switch
    that lands just before the lock is taken (it holds the lock across the
    move, so it cannot land after) is migrated as the root the run finds."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(elsewhere))
    _legacy()
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    real = locks.inference_migration_lock()

    class MovesFirst:
        def acquire(self, blocking: bool = True) -> bool:
            monkeypatch.setenv("GRIMOIRE_HOME", str(elsewhere))
            return real.acquire(blocking=blocking)

        def release(self) -> None:
            real.release()

    monkeypatch.setattr(locks, "inference_migration_lock", MovesFirst)
    got = migrate.ensure()

    assert got.state == "done", got
    assert inference_keys.is_current(_raw_config(elsewhere))
    assert len(_safety_archives(elsewhere)) == 1
    assert not (home / "backups").exists()


# ---- what a run reads, and resuming one ----

def _spare_summary() -> None:
    """A legacy store whose summary route names a second connection."""
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    config.write_config(route_summary="spare")


def test_a_legacy_edit_during_the_backup_survives(home, monkeypatch):
    """The format-2 keys are derived from a read taken after the backup, under
    the lock of the write that lands them -- not from a snapshot taken before a
    backup that can run for minutes while the app serves at format 1."""
    _spare_summary()
    real = backups.create_backup

    def then_edit(**kw):
        made = real(**kw)
        # Legacy Settings, at format 1, while the backup runs.
        config.write_config(route_summary="", theme="saltmarch")
        return made

    monkeypatch.setattr(backups, "create_backup", then_edit)
    assert migrate.ensure().state == "done"

    cfg = _raw_config(home)
    assert cfg[FORMAT] == "2"
    assert cfg["route_summary"] == ""
    assert (cfg["use_summary"], cfg["use_summary_provider"]) == ("", "")
    assert (cfg["use_scene_break"], cfg["use_scene_break_provider"]) == ("", "")
    # A setting that is not the migration's is not rewritten from a stale
    # read either.
    assert cfg["theme"] == "saltmarch"


def _failing_marker(monkeypatch) -> None:
    """Make every `config.md` write that carries the marker fail."""

    def flaky(**fields):
        if FORMAT in fields:
            raise OSError("disk hiccup writing the marker")
        return _REAL_WRITE(**fields)

    monkeypatch.setattr(config, "write_config", flaky)


def test_a_resumed_run_clears_a_key_the_user_cleared_in_between(home, monkeypatch):
    _spare_summary()
    _failing_marker(monkeypatch)
    got = migrate.ensure()
    assert got.state == "failed" and "disk hiccup" in got.reason
    monkeypatch.setattr(config, "write_config", _REAL_WRITE)
    # At format 1, between the two runs, the user clears the route.
    config.write_config(route_summary="")

    assert migrate.ensure().state == "done"

    cfg = _raw_config(home)
    assert (cfg["use_summary"], cfg["use_summary_provider"]) == ("", "")
    assert (cfg["use_scene_break"], cfg["use_scene_break_provider"]) == ("", "")


def test_every_key_the_migration_owns_is_written_so_a_stale_one_is_cleared(home):
    """A format-2 key already in a legacy `config.md` (an interrupted run of an
    earlier build, a hand edit) is overwritten with what the legacy settings
    translate to -- "" included, and the Embedding role's keys too."""
    _legacy()
    llm_connections.create_connection("openai_compatible", "vectors",
                                      base_url="http://localhost:1234/v1")
    config.write_config(use_summary="model", use_summary_provider="vectors",
                        role_fast_provider="vectors",
                        role_embedding_provider="vectors",
                        role_embedding_model="embed-small")
    assert store_pkg.embed_space.resolve() is None   # the legacy layout embeds nothing

    assert migrate.ensure().state == "done"

    cfg = _raw_config(home)
    for key in ("use_summary", "use_summary_provider", "role_fast_provider",
                "role_embedding_provider", "role_embedding_model"):
        assert cfg[key] == "", key
    assert store_pkg.embed_space.resolve() is None


def test_a_failed_run_then_a_success_takes_one_safety_backup(home, monkeypatch):
    _spare_summary()
    _failing_marker(monkeypatch)
    assert migrate.ensure().state == "failed"
    assert len(_safety_archives(home)) == 1
    monkeypatch.setattr(config, "write_config", _REAL_WRITE)

    assert migrate.ensure().state == "done"

    assert len(_safety_archives(home)) == 1


def test_a_safety_backup_that_is_gone_is_taken_again(home, monkeypatch):
    _legacy()
    _failing_marker(monkeypatch)
    assert migrate.ensure().state == "failed"
    for name in _safety_archives(home):
        (home / "backups" / name).unlink()
    monkeypatch.setattr(config, "write_config", _REAL_WRITE)

    assert migrate.ensure().state == "done"

    assert len(_safety_archives(home)) == 1


def _note(root: Path) -> dict:
    return json.loads((root / ".cache" / "inference-migration.json")
                      .read_text(encoding="utf-8"))


def test_an_unexpected_error_leaves_no_running_note(home, monkeypatch):
    _legacy()

    def boom(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(migrate, "_providers", boom)
    with pytest.raises(RuntimeError):
        migrate.ensure()

    note = _note(home)
    assert note["running"] is False
    assert "boom" in note["failed"]
    assert migrate.status().state == "failed"


def test_a_done_store_is_done_whatever_a_stale_note_says(home):
    _legacy()
    assert migrate.ensure().state == "done"
    # Another process's run, killed after its marker landed but before it
    # could say it had finished.
    (home / ".cache" / "inference-migration.json").write_text(
        json.dumps({"root": str(home), "running": True, "pid": os.getpid() + 1,
                    "failed": "", "skipped": []}), encoding="utf-8")
    assert migrate.status().state == "done"


def test_an_unreadable_campaign_is_skipped_once_not_on_every_ensure(home, monkeypatch):
    _legacy()
    _campaign("Saltmarch")
    bad = _campaign("Winifred")
    campaigns.paths.campaign_meta_path(bad).write_bytes(b"\xff\xfe not text")
    got = migrate.ensure()
    assert got.state == "done"
    assert any(bad in item for item in got.skipped), got

    remembered = []
    monkeypatch.setattr(migrate, "_remember", lambda *a, **kw: remembered.append(kw))
    before = _digest(home, skip=())
    again = migrate.ensure()

    assert again.state == "done"
    assert any(bad in item for item in again.skipped), again
    assert remembered == []
    assert _digest(home, skip=()) == before
    # Derived from the store, not only remembered: with the note gone too.
    (home / ".cache" / "inference-migration.json").unlink()
    assert any(bad in item for item in migrate.status().skipped)


def test_a_model_facts_write_that_fails_is_skipped_not_fatal(home, monkeypatch):
    _legacy()
    llm_connections.update_connection("openrouter", vision="off")

    def unwritable(*_a, **_kw):
        raise OSError("facts file is read-only")

    monkeypatch.setattr(facts, "adopt_legacy", unwritable)
    got = migrate.ensure()

    assert got.state == "done", got
    assert any("facts openrouter" in item and "read-only" in item
               for item in got.skipped), got


def test_a_stopped_run_is_not_done_and_resumes(home, monkeypatch):
    _legacy()
    stop = threading.Event()
    real = backups.create_backup

    def then_stop(**kw):
        made = real(**kw)
        stop.set()                         # the app is shutting down
        return made

    monkeypatch.setattr(backups, "create_backup", then_stop)
    got = migrate.ensure(stop=stop)

    assert got.state == "pending" and got.reason == migrate.STOPPED
    assert not inference_keys.is_current(_raw_config(home))
    # Stopped between items: no connection was touched after the flag.
    assert not llm_connections.read_connection_raw("openrouter").get("preset")
    assert migrate.status().state == "pending"

    monkeypatch.setattr(backups, "create_backup", real)
    assert migrate.ensure().state == "done"
    assert len(_safety_archives(home)) == 1


# ---- the triggers (main.start, PUT /config/data-dir) ----

def _app_client(monkeypatch, root: Path) -> TestClient:
    monkeypatch.setenv("GRIMOIRE_HOME", str(root))
    importlib.reload(store_pkg)
    app = main.create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


def test_startup_does_not_block_on_the_migration(home, monkeypatch):
    _legacy()
    monkeypatch.delenv(inference_keys.AUTOMIGRATE_ENV, raising=False)
    entered, release = threading.Event(), threading.Event()
    real = migrate.ensure

    def slow(stop=None):
        entered.set()
        assert release.wait(WAIT)
        return real(stop=stop)

    monkeypatch.setattr(migrate, "ensure", slow)
    client = _app_client(monkeypatch, home)
    with client:
        assert entered.wait(WAIT)
        # The app serves while the migration is still inside its first step.
        assert client.get("/api/config/data-dir").status_code == 200
        release.set()
        thread = client.app.state.inference_migration
        thread.join(WAIT)
        assert not thread.is_alive()
    assert inference_keys.is_current(config.read_config())


def test_shutdown_stops_the_migration(home, monkeypatch):
    """Lifespan shutdown sets the run's stop flag rather than leaving a thread
    writing into whatever `home()` resolves to after the app is gone."""
    _legacy()
    monkeypatch.delenv(inference_keys.AUTOMIGRATE_ENV, raising=False)
    entered, seen = threading.Event(), []

    def until_stopped(stop=None):
        entered.set()
        seen.append(stop is not None and stop.wait(WAIT))
        return migrate.Status("pending", migrate.STOPPED)

    monkeypatch.setattr(migrate, "ensure", until_stopped)
    with _app_client(monkeypatch, home) as client:
        assert entered.wait(WAIT)
        thread = client.app.state.inference_migration
    assert seen == [True]
    assert not thread.is_alive()


def test_shutdown_stops_every_run_and_a_new_one_never_unstops_an_old(home, monkeypatch):
    """Each run has its own stop flag. Shutdown flags every run the app started
    (a data-dir switch starts one beside the lifespan's), and a run started
    afterwards gets a fresh flag rather than clearing a straggler's."""
    _legacy()
    monkeypatch.delenv(inference_keys.AUTOMIGRATE_ENV, raising=False)
    flags, entered = [], threading.Semaphore(0)

    def until_stopped(stop=None):
        flags.append(stop)
        entered.release()
        stop.wait(WAIT)
        return migrate.Status("pending", migrate.STOPPED)

    monkeypatch.setattr(migrate, "ensure", until_stopped)
    with _app_client(monkeypatch, home) as client:
        main.start(client.app)
        assert entered.acquire(timeout=WAIT) and entered.acquire(timeout=WAIT)
        threads = [t for t, _ in client.app.state.inference_migrations]
    assert len(flags) == 2 and flags[0] is not flags[1]
    assert all(f.is_set() for f in flags)
    assert not any(t.is_alive() for t in threads)
    main.start(client.app)
    assert entered.acquire(timeout=WAIT)
    assert not flags[2].is_set() and flags[0].is_set()
    main._stop_migration(client.app)


def test_automigrate_off_in_tests(home, monkeypatch, tmp_path):
    assert os.environ[inference_keys.AUTOMIGRATE_ENV] == "0"
    calls = []
    monkeypatch.setattr(migrate, "ensure", lambda **_kw: calls.append(1))
    _legacy()
    with _app_client(monkeypatch, home) as client:
        dest = tmp_path / "moved"
        r = client.put("/api/config/data-dir", json={"data_dir": str(dest)})
        assert r.status_code == 200, r.text
        assert client.app.state.inference_migration is None
    assert calls == []


def test_put_data_dir_is_busy_while_migrating(home, monkeypatch, tmp_path):
    _legacy()
    entered, release = _blocking_backup(monkeypatch)
    t = threading.Thread(target=migrate.ensure)
    with _app_client(monkeypatch, home) as client:
        t.start()
        try:
            assert entered.wait(WAIT)
            dest = tmp_path / "moved"
            r = client.put("/api/config/data-dir", json={"data_dir": str(dest)})
            assert r.status_code == 409, r.text
            assert r.json()["kind"] == "busy"
            assert not dest.exists()
        finally:
            release.set()
            t.join(WAIT)
        r = client.put("/api/config/data-dir", json={"data_dir": str(tmp_path / "moved")})
        assert r.status_code == 200, r.text


def test_the_migration_holds_image_maintenance_off(home, monkeypatch):
    _legacy()
    monkeypatch.delenv(inference_keys.AUTOMIGRATE_ENV, raising=False)
    entered, release = threading.Event(), threading.Event()
    real = migrate.ensure

    def slow(stop=None):
        entered.set()
        assert release.wait(WAIT)
        return real(stop=stop)

    monkeypatch.setattr(migrate, "ensure", slow)
    with _app_client(monkeypatch, home) as client:
        assert entered.wait(WAIT)
        try:
            with pytest.raises(HTTPException) as refused:
                routes.runs.run_maintenance(client.app, "gc", "attempt-x",
                                            lambda _run: {})
            assert refused.value.status_code == 409
            assert refused.value.detail["kind"] == "busy"
        finally:
            release.set()
            client.app.state.inference_migration.join(WAIT)


def test_status_is_remembered_per_root(home, monkeypatch, tmp_path):
    _legacy()

    def broken(**_kw):
        raise OSError("disk full")

    monkeypatch.setattr(backups, "create_backup", broken)
    assert migrate.ensure().state == "failed"
    other = tmp_path / "other"
    other.mkdir()
    (other / "config.md").write_text(dump_frontmatter({"active_connection_id": "openrouter"},
                                                      ""), encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_HOME", str(other))
    assert migrate.status().state == "pending"
    assert migrate.status().reason == ""


# ---- a connection that cannot be read is not a connection that is missing ----

def _flaky_read(monkeypatch, conn_id: str, exc: Exception) -> list[str]:
    """`read_connection_strict` raising `exc` the first time it is asked for
    `conn_id`, then reading as it does; returns the ids it was asked for."""
    real = llm_connections.read_connection_strict
    asked: list[str] = []

    def flaky(cid: str):
        asked.append(cid)
        if cid == conn_id and asked.count(cid) == 1:
            raise exc
        return real(cid)

    monkeypatch.setattr(llm_connections, "read_connection_strict", flaky)
    return asked


@pytest.mark.parametrize("exc", [OSError("held by a sync client"),
                                 UnicodeDecodeError("utf-8", b"\xff", 0, 1, "half-synced")])
def test_a_connection_unreadable_during_the_switch_fails_the_run(home, monkeypatch, exc):
    """A transient read failure of the active connection inside the switch is
    not "no such connection": persisting the translation would write Primary
    with an empty model and no preset beside the marker, for good."""
    _legacy()
    asked = _flaky_read(monkeypatch, "openrouter", exc)

    got = migrate.ensure()

    assert "openrouter" in asked
    assert got.state == "failed", got
    assert not inference_keys.is_current(config.read_config())
    assert not _raw_config(home).get("role_primary_provider")
    assert migrate.status().state == "failed"

    # The next start retries, and reads the connection this time.
    assert migrate.ensure().state == "done"
    cfg = _raw_config(home)
    assert (cfg["role_primary_provider"], cfg["role_primary_model"]) == (
        "openrouter", "vendor/active")


def test_a_connection_file_that_cannot_be_decoded_fails_the_run(home):
    """A half-synced connection file is unreadable, not absent: the providers
    and facts steps refuse to go on without it, and nothing is switched."""
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare", prefill=True)
    config.write_config(fallback_connection_id="spare")
    path = home / "llm_connections" / "spare.md"
    good = path.read_bytes()
    path.write_bytes(b"\xff\xfe" + good)

    got = migrate.ensure()

    assert got.state == "failed", got
    assert "spare" in got.reason
    assert not inference_keys.is_current(config.read_config())

    path.write_bytes(good)
    assert migrate.ensure().state == "done"
    cfg = _raw_config(home)
    assert cfg["role_primary_fallback_model"] == "vendor/spare"
    assert facts.of("spare", "vendor/spare", "")["prefill"] is True


@pytest.mark.parametrize("placeholder", [b"", b"kind: openrouter\nmodel: vendor/spare\n"],
                         ids=["zero-byte", "unfenced"])
def test_a_connection_file_with_no_record_in_it_fails_the_run(home, placeholder):
    """A sync placeholder -- a zero-byte file, or one whose frontmatter fence
    has not arrived -- is there but unreadable, not absent. Read as no
    connection, the switch would persist the fallback with an empty model and
    stamp format 2 over it for good; it stops the run instead, writing nothing,
    and the next start finishes once the file is whole."""
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    config.write_config(fallback_connection_id="spare")
    path = home / "llm_connections" / "spare.md"
    good = path.read_bytes()
    path.write_bytes(placeholder)

    got = migrate.ensure()

    assert got.state == "failed", got
    assert "spare" in got.reason
    assert not inference_keys.is_current(config.read_config())
    assert not _raw_config(home).get("role_primary_fallback_provider")
    assert migrate.status().state == "failed"

    path.write_bytes(good)
    assert migrate.ensure().state == "done"
    cfg = _raw_config(home)
    assert (cfg["role_primary_fallback_provider"],
            cfg["role_primary_fallback_model"]) == ("spare", "vendor/spare")


def test_a_missing_connection_is_still_a_dangling_reference(home):
    """Only an unreadable file fails the run: a reference to a connection that
    does not exist is persisted as it was."""
    _legacy()
    config.write_config(fallback_connection_id="nowhere")
    assert migrate.ensure().state == "done"
    cfg = _raw_config(home)
    assert (cfg["role_primary_fallback_provider"],
            cfg["role_primary_fallback_model"]) == ("nowhere", "")


def test_a_campaign_whose_connection_is_unreadable_is_skipped_and_retried(home, monkeypatch):
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    cid = _campaign()
    campaigns.set_campaign_routing(cid, {"route_scene": "spare"})
    # Armed for the campaign step: the providers step reads every connection
    # too, and an unreadable one there fails the run instead (above).
    real_step = migrate._campaign_step

    def step_with_a_flaky_read(*args):
        _flaky_read(monkeypatch, "spare", OSError("held by a sync client"))
        monkeypatch.setattr(migrate, "_campaign_step", real_step)
        return real_step(*args)

    monkeypatch.setattr(migrate, "_campaign_step", step_with_a_flaky_read)

    got = migrate.ensure()

    assert any(cid in item and "sync client" in item for item in got.skipped), got
    assert FORMAT not in _meta(cid)
    assert migrate.ensure().state == "done"
    assert _meta(cid)["use_scene_model"] == "vendor/spare"


# ---- brutal review 1: what the migration may never write over ----

_PLACEHOLDERS = [b"", b"---\nname: Saltmarch\nworld: realm\nroute_summary: openrouter\ncrea",
                 b"---\n---\n"]
_PLACEHOLDER_IDS = ["zero-byte", "unfenced", "empty-block"]


@pytest.mark.parametrize("placeholder", _PLACEHOLDERS, ids=_PLACEHOLDER_IDS)
def test_a_campaign_md_holding_no_record_is_never_rewritten(home, placeholder):
    """A zero-byte or unfenced `campaign.md` (a sync placeholder, a conflict
    stub, a hand edit that lost its closing fence) parses as `{}`. Taken for a
    campaign with nothing set, it was overwritten with a marker-only file and
    marked -- its name, world and every override gone for good. It is skipped,
    left byte for byte, and finished once the file is whole."""
    _legacy()
    good_cid = _campaign("Saltmarch")
    cid = _campaign("Winifred")
    path = campaigns.paths.campaign_meta_path(cid)
    whole = path.read_bytes()
    path.write_bytes(placeholder)

    got = migrate.ensure()

    assert path.read_bytes() == placeholder
    assert any(cid in item and "no settings" in item for item in got.skipped), got
    assert _meta(good_cid)[FORMAT] == "2"
    assert any(cid in item for item in migrate.status().skipped)
    # The campaign step itself refuses it too (the settings write calls it).
    with locks.campaign_lock(cid), pytest.raises(migrate.RecordUnreadableError):
        migrate.campaign(cid)
    assert path.read_bytes() == placeholder

    path.write_bytes(whole)
    assert migrate.ensure().state == "done"
    assert _meta(cid)[FORMAT] == "2" and _meta(cid)["name"] == "Winifred"


@pytest.mark.parametrize("placeholder", _PLACEHOLDERS, ids=_PLACEHOLDER_IDS)
def test_a_config_md_holding_no_record_fails_the_run_and_is_never_rewritten(
        home, placeholder):
    """A `config.md` that holds no record is not a legacy store with nothing
    set: switched, it was stamped current over an empty layout, so the legacy
    keys were never read again once the real file arrived. The run fails,
    writes nothing, says why, and the next start finishes."""
    _legacy()
    cid = _campaign()
    path = home / "config.md"
    whole = path.read_bytes()
    path.write_bytes(placeholder)
    before = _digest(home)

    got = migrate.ensure()

    assert got.state == "failed", got
    assert "config.md" in got.reason
    assert path.read_bytes() == placeholder
    assert _digest(home) == before
    assert migrate.status().state == "failed"

    path.write_bytes(whole)
    assert migrate.ensure().state == "done"
    assert _raw_config(home)[FORMAT] == "2"
    assert _raw_config(home)["role_primary_model"] == "vendor/active"
    assert _meta(cid)[FORMAT] == "2"


def test_a_config_md_emptied_before_the_switch_fails_the_run(home, monkeypatch):
    """The same rule in the switch's own hold: a placeholder arriving after
    the run began is not stamped either."""
    _legacy()
    path = home / "config.md"
    whole = path.read_bytes()
    real = migrate._providers

    def then_placeholder(run):
        out = real(run)
        path.write_bytes(b"")
        return out

    monkeypatch.setattr(migrate, "_providers", then_placeholder)
    got = migrate.ensure()

    assert got.state == "failed", got
    assert path.read_bytes() == b""
    monkeypatch.setattr(migrate, "_providers", real)
    path.write_bytes(whole)
    assert migrate.ensure().state == "done"


def _verified_other(conn_id: str = "openrouter") -> bytes:
    """A facts file holding a paid test's results for another model."""
    rev = llm_connections.read_connection_raw(conn_id)["rev"]
    assert facts.record_verified(conn_id, "vendor/other", rev, {"vision": {"ok": True}})
    return llm_connections.facts_path(conn_id).read_bytes()


@pytest.mark.parametrize("placeholder", [b"", b'{"vendor/other": {"verif', b"[]"],
                         ids=["zero-byte", "truncated", "not-an-object"])
def test_a_facts_file_that_cannot_be_read_fails_the_run_and_is_kept(home, placeholder):
    """A provider's facts file the migration cannot read is not an empty one:
    rewritten from `{}`, it held only the copy and every verified result (paid
    test calls) and override of its other models was gone. The run fails,
    the file is left, and the next start copies onto the whole file."""
    _legacy()
    llm_connections.update_connection("openrouter", vision="off")
    whole = _verified_other()
    path = llm_connections.facts_path("openrouter")
    path.write_bytes(placeholder)

    got = migrate.ensure()

    assert got.state == "failed", got
    assert "openrouter" in got.reason
    assert path.read_bytes() == placeholder
    assert not inference_keys.is_current(config.read_config())

    path.write_bytes(whole)
    assert migrate.ensure().state == "done"
    doc = facts.read("openrouter")
    assert doc["vendor/other"]["verified"]["caps"]["vision"]["ok"] is True
    assert facts.of("openrouter", "vendor/active", "")["vision"] == "off"


def test_a_facts_file_a_sync_client_holds_fails_the_run(home, monkeypatch):
    _legacy()
    llm_connections.update_connection("openrouter", vision="off")
    whole = _verified_other()
    path = llm_connections.facts_path("openrouter")
    real = Path.read_text

    def held(self, *a, **kw):
        if self == path:
            raise PermissionError("held by a sync client")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", held)
    got = migrate.ensure()
    monkeypatch.setattr(Path, "read_text", real)

    assert got.state == "failed", got
    assert "sync client" in got.reason
    assert path.read_bytes() == whole
    assert migrate.ensure().state == "done"
    assert set(facts.read("openrouter")) == {"vendor/other", "vendor/active"}


def _stated_of(conn_id: str, model: str) -> tuple:
    got = facts.of(conn_id, model, "")
    return got["vision"], got["prefill"], got["post_process"]


def test_a_resumed_run_takes_back_a_copied_fact_the_user_reset(home, monkeypatch):
    """A run copies the legacy fields into the facts and fails before the
    marker; at format 1 the user resets them to their defaults; the resumed
    run states nothing for them -- and the copy the first run made must not
    go on saying the old values at format 2."""
    _legacy()
    llm_connections.update_connection("openrouter", vision="off", prefill=True,
                                      post_process="strict")
    _failing_marker(monkeypatch)
    assert migrate.ensure().state == "failed"
    assert _stated_of("openrouter", "vendor/active") == ("off", True, "strict")
    monkeypatch.setattr(config, "write_config", _REAL_WRITE)

    llm_connections.update_connection("openrouter", vision="", prefill=False,
                                      post_process="none")
    assert migrate.ensure().state == "done"

    assert _stated_of("openrouter", "vendor/active") == ("", None, "")
    assert facts.read("openrouter") == {}


def test_a_resumed_run_moves_a_copied_fact_to_the_model_now_named(home, monkeypatch):
    _legacy()
    llm_connections.update_connection("openrouter", vision="off", prefill=True)
    whole = _verified_other()
    _failing_marker(monkeypatch)
    assert migrate.ensure().state == "failed"
    monkeypatch.setattr(config, "write_config", _REAL_WRITE)

    llm_connections.update_connection("openrouter", model="vendor/next", prefill=False)
    assert migrate.ensure().state == "done"

    assert _stated_of("openrouter", "vendor/active") == ("", None, "")
    assert _stated_of("openrouter", "vendor/next") == ("off", None, "")
    # What the migration never wrote is never taken back.
    assert json.loads(whole)["vendor/other"] == facts.read("openrouter")["vendor/other"]


def test_a_resumed_run_leaves_a_fact_somebody_wrote_over_the_copy(home, monkeypatch):
    _legacy()
    llm_connections.update_connection("openrouter", vision="off")
    _failing_marker(monkeypatch)
    assert migrate.ensure().state == "failed"
    monkeypatch.setattr(config, "write_config", _REAL_WRITE)
    facts.set_stated("openrouter", "vendor/active", vision="on")

    llm_connections.update_connection("openrouter", vision="")
    assert migrate.ensure().state == "done"

    assert _stated_of("openrouter", "vendor/active")[0] == "on"


def test_a_newer_marker_landing_during_the_campaigns_stops_the_marking(home, monkeypatch):
    """A newer build switching the store while the campaign loop runs: the
    campaigns it has not reached are not this build's to mark (spec 11.3)."""
    _legacy()
    first = _campaign("Saltmarch")
    second = _campaign("Winifred")
    real = migrate._campaign_step

    def newer_meanwhile(cid, skipped):
        config.write_config(**{FORMAT: "3"})
        return real(cid, skipped)

    monkeypatch.setattr(migrate, "_campaign_step", newer_meanwhile)
    got = migrate.ensure()

    assert got.state == "newer", got
    assert FORMAT not in _meta(first) and FORMAT not in _meta(second)
    assert migrate.status().state == "newer"


def test_a_newer_marker_seen_by_the_switch_stops_the_run(home, monkeypatch):
    _legacy()
    cid = _campaign()
    real = migrate._providers

    def newer_meanwhile(run):
        out = real(run)
        config.write_config(**{FORMAT: "3"})
        return out

    monkeypatch.setattr(migrate, "_providers", newer_meanwhile)
    got = migrate.ensure()

    assert got.state == "newer", got
    assert FORMAT not in _meta(cid)
    assert _raw_config(home)[FORMAT] == "3"
