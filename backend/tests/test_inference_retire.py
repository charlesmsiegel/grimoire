"""Retirement: the legacy settings layout persisted away (slice I, Task 6a).

The last stage of `migrate.ensure`, after the marker: a `pre-retirement-`
archive when the pass deletes or replaces a stored value, then `config.md`'s
derived presets, repoint, legacy-key deletion and retirement marker in one
write, then each campaign likewise -- every read strict, every unreadable item
left as it is. The connection strip is Task 6b's.

Invented names (Realm, Saltmarch, Winifred, Mara) and fake keys only.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import zipfile
from pathlib import Path

import pytest

import grimoire.store as store
from grimoire import llm_sampling
from grimoire.store import (
    atomic,
    backups,
    campaigns,
    config,
    frontmatter,
    inference_keys,
    llm_connections,
    locks,
    revision,
    sampler_presets,
    worlds,
)
from grimoire.store import inference_retired as retired
from grimoire.store.campaigns import lifecycle as campaign_lifecycle
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import legacy_plan, migrate, retire
from grimoire.store.inference import resolve as inference
from grimoire.store.inference import settings as inference_settings
from tests import inference_baseline_c as base_c
from tests import inference_fixtures
from tests import test_inference_equivalence as equivalence

keys = inference_keys
FORMAT = keys.FORMAT_KEY
RETIRED = keys.RETIRED_KEY
WAIT = 10.0


@pytest.fixture()
def home(monkeypatch, tmp_path):
    """A throwaway store root, born at format 2 (and retired) on first read;
    a test about a legacy library calls `_legacy()` first."""
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(root))
    return root


# ---- building stores ----
def _glm(name: str, effort: str, preset: str = "", model: str = "glm-5.3") -> str:
    return llm_connections.create_connection(
        "openai_compatible", name, base_url=base_c.GLM_URL, api_key="sk-test-glm",
        model=model, reasoning_effort=effort, sampler_preset=preset)


def _campaign(name: str = "Saltmarch") -> str:
    existing = worlds.list_worlds()
    wid = existing[0]["id"] if existing else worlds.create_world("Realm")
    return campaigns.create_campaign(name, wid)


def _legacy() -> None:
    """A format-1 library: `config.md` without the marker, the seeded
    connections, OpenRouter keyed."""
    inference_fixtures.legacy_store()
    llm_connections.update_connection("openrouter", api_key="sk-test-active",
                                      model="vendor/active")
    assert not keys.is_current(config.read_config())


def _legacy_glm(*, pin: bool = True) -> str:
    """`_legacy` plus the Task 4 store: Primary on `glm` (effort high, its own
    preset "warm"), the role fallback on `glm2` (effort low, no preset), and
    Saltmarch routing its scene to glm (so it runs on glm's own preset).
    Returns Saltmarch's id."""
    _legacy()
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    _glm("glm", "high", preset="warm")
    _glm("glm2", "low")
    config.write_config(active_connection_id="glm", fallback_connection_id="glm2")
    cid = _campaign("Saltmarch")
    if pin:
        campaigns.set_campaign_routing(cid, {"route_scene": "glm", "preset_scene": ""})
    return cid


def _c_era() -> None:
    """Migrate the store as a C-H build did: format 2, the legacy keys left
    in place, nothing retired."""
    assert inference_fixtures.migrate_as_c_h().state == "done"
    assert keys.is_current(config.read_config())
    assert not _raw_config().get(RETIRED)


def _stripped() -> None:
    """Every connection's legacy model fields taken off by hand (their values
    recorded), as a pass that already ran the strip left them -- for a test
    about a scope's own work, with no strip left to do."""
    for conn_id in llm_connections.legacy_fields_on_disk():
        llm_connections.strip_model_fields(conn_id)
    assert not llm_connections.legacy_fields_on_disk()


def _raw_config() -> dict:
    return parse_frontmatter((store.home() / "config.md").read_text(encoding="utf-8"))[0]


def _meta(cid: str) -> dict:
    return parse_frontmatter(
        campaigns.paths.campaign_meta_path(cid).read_text(encoding="utf-8"))[0]


def _write_meta(cid: str, meta: dict) -> None:
    path = campaigns.paths.campaign_meta_path(cid)
    _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")  # atomic-ok: test fixture


def _write_config(meta: dict) -> None:
    path = store.home() / "config.md"
    path.write_text(dump_frontmatter(meta, ""), encoding="utf-8")  # atomic-ok: test fixture


def _digest(root: Path, *, skip: tuple[str, ...] = (".cache", "backups")) -> dict[str, str]:
    """Every file under `root` (minus `skip` at the top) -> its sha256."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[0] in skip or not path.is_file():
            continue
        out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _archives(prefix: str) -> list[str]:
    d = backups.backup_dir()
    return sorted(p.name for p in d.iterdir()
                  if p.name.startswith(prefix)) if d.exists() else []


def _retired(meta: dict) -> bool:
    return meta.get(RETIRED) == "1"


def _revs() -> dict[str, str]:
    return {c["id"]: llm_connections.read_connection_raw(c["id"])["rev"]
            for c in llm_connections.list_connections()}


def _no_legacy_keys(meta: dict, legacy) -> None:
    held = sorted(k for k in legacy if k in meta)
    assert not held, held


def _recording_backups(monkeypatch, root: Path) -> list[dict[str, str]]:
    """Wrap `create_backup` to record the store's digest as each archive is
    taken: what had been written before it."""
    seen: list[dict[str, str]] = []
    real = backups.create_backup

    def recording(*args, **kwargs):
        seen.append(_digest(root))
        return real(*args, **kwargs)

    monkeypatch.setattr(backups, "create_backup", recording)
    return seen


# ---- the archive ----
def test_retirement_takes_a_pre_retirement_archive_first(home, monkeypatch):
    cid = _legacy_glm()
    _c_era()
    before = _digest(home)
    seen = _recording_backups(monkeypatch, home)

    assert migrate.ensure().state == "done"

    assert len(_archives(backups.RETIRE_PREFIX)) == 1
    assert seen == [before]                  # taken before the pass's first write
    assert _retired(_raw_config()) and _retired(_meta(cid))


def test_a_failed_retirement_archive_writes_nothing(home, monkeypatch):
    """R2-8: the archive precedes every write of the pass, marker-only ones
    included -- so with it failing, no preset file, no repoint, no deletion
    and no `RETIRED_KEY` -- and play is what it was."""
    cid = _legacy_glm()
    other = _campaign("Winifred")             # marker-only work, also held back
    _c_era()
    before = _digest(home)
    played = equivalence.planned_cells(cid)

    def refuse(*_a, **_k):
        raise OSError("the backup disk is full")

    monkeypatch.setattr(backups, "create_backup", refuse)
    got = migrate.ensure()

    assert got.state == "done"
    assert "the backup disk is full" in got.retirement["failed"]
    assert _digest(home) == before
    assert not _retired(_raw_config()) and not _retired(_meta(cid))
    assert not _retired(_meta(other))
    assert not (home / "sampler_presets" / "warm-reasoning-high.json").exists()
    assert equivalence.planned_cells(cid) == played


def test_a_resumed_pass_with_only_campaign_work_left_takes_an_archive(home, monkeypatch):
    """R2-3: `config.md` already retired, one campaign still needing its
    repoint and no note of an earlier archive: the pass takes one, before
    that campaign's write."""
    cid = _legacy_glm()
    _c_era()
    retire.retire_global(legacy_plan.lookup(mode="retire"))
    assert _retired(_raw_config()) and not _retired(_meta(cid))
    (home / ".cache" / "inference-migration.json").unlink()
    campaign_before = campaigns.paths.campaign_meta_path(cid).read_bytes()
    seen = _recording_backups(monkeypatch, home)

    assert migrate.ensure().state == "done"

    assert len(_archives(backups.RETIRE_PREFIX)) == 1
    assert len(seen) == 1
    assert seen[0]["campaigns/" + cid + "/campaign.md"] == hashlib.sha256(
        campaign_before).hexdigest()
    assert _meta(cid)[keys.pin_key("scene", "preset")] == "warm-reasoning-high"
    assert _retired(_meta(cid))


def test_a_marker_only_pass_takes_no_archive(home):
    """R2-3: a C-era campaign with no legacy key gets `RETIRED_KEY` and
    nothing else, and no archive is taken for it."""
    _legacy()
    cid = _campaign("Saltmarch")
    _c_era()
    retire.retire_global(legacy_plan.lookup(mode="retire"))
    _stripped()
    for name in _archives(backups.RETIRE_PREFIX):
        (backups.backup_dir() / name).unlink()
    (home / ".cache" / "inference-migration.json").unlink()
    before = _meta(cid)

    assert migrate.ensure().state == "done"

    assert _archives(backups.RETIRE_PREFIX) == []
    assert _meta(cid) == {**before, RETIRED: "1"}


def _born_by_c_h() -> dict:
    """`config.md` as a C-H build's `read_config` materializes it: every
    legacy key present as "", the format marker, and no retirement marker.
    Not yet what a real C-H install holds on disk -- its connection seeding
    writes `active_connection_id: openrouter` on the first connection read
    (`test_a_real_c_h_fresh_install_takes_one_archive`) -- but the shape that
    proves an empty legacy value is no work."""
    meta = {**dict.fromkeys(keys.LEGACY_GLOBAL_KEYS, ""), "theme": "system",
            FORMAT: keys.CURRENT_FORMAT}
    assert len(keys.LEGACY_GLOBAL_KEYS) == 16
    return meta


def test_a_fresh_install_with_empty_legacy_keys_takes_no_archive(home):
    """N3: empty legacy values are absent ones. A `config.md` holding the 16
    legacy keys as "" (`_born_by_c_h`) and one campaign born the same way,
    with connections seeded by this build: `left()` names only the missing
    markers, the pass needs no archive, and `ensure` writes the marker
    (dropping the empty keys with it) and nothing else; a second `ensure`
    writes nothing."""
    _write_config(_born_by_c_h())
    cid = _campaign("Saltmarch")
    llm_connections.list_connections()
    assert RETIRED not in _meta(cid)
    assert sorted(retire.left()) == ["campaign saltmarch: not retired yet",
                                     "config.md: not retired yet"]
    assert not retire.needs_archive(retire.pass_plan(legacy_plan.lookup(mode="retire")))
    meta_before = _meta(cid)

    assert migrate.ensure().state == "done"

    assert _archives(backups.RETIRE_PREFIX) == []
    expected = {k: v for k, v in _born_by_c_h().items() if k not in keys.LEGACY_GLOBAL_KEYS}
    assert _raw_config() == {**expected, RETIRED: "1"}
    assert _meta(cid) == {**meta_before, RETIRED: "1"}
    after = _digest(home)
    assert migrate.ensure().state == "done"
    assert _digest(home) == after


def test_a_retired_store_holding_empty_legacy_keys_is_left_alone(home):
    """N3's other half: the marker already there, the empty keys still
    there -- nothing is left, and `ensure` writes nothing at all."""
    _write_config({**_born_by_c_h(), RETIRED: "1"})
    _campaign("Saltmarch")
    llm_connections.list_connections()
    assert retire.left() == ()
    before = _digest(home)
    assert migrate.ensure().state == "done"
    assert _digest(home) == before
    assert _archives(backups.RETIRE_PREFIX) == []


def test_a_unit_that_grew_work_after_planning_is_left_for_the_next_run(home, monkeypatch):
    """R3-1: the pass plans a marker-only campaign and takes no archive; by
    the time its unit runs, a legacy key has arrived in it (an older build on
    another device). Planned again in the hold, the unit now deletes a value,
    so it writes nothing; the next `ensure` takes the archive and retires it."""
    _legacy()
    cid = _campaign("Saltmarch")
    _c_era()
    retire.retire_global(legacy_plan.lookup(mode="retire"))
    _stripped()
    (home / ".cache" / "inference-migration.json").unlink()
    real = retire.retire_campaign

    def late_writer(unit_cid, lookup, **kwargs):
        _write_meta(unit_cid, {**_meta(unit_cid), "route_scene": "openrouter"})
        return real(unit_cid, lookup, **kwargs)

    monkeypatch.setattr(retire, "retire_campaign", late_writer)
    assert migrate.ensure().state == "done"
    assert not _retired(_meta(cid)) and _meta(cid)["route_scene"] == "openrouter"
    assert _archives(backups.RETIRE_PREFIX) == []

    monkeypatch.setattr(retire, "retire_campaign", real)
    assert migrate.ensure().state == "done"
    assert len(_archives(backups.RETIRE_PREFIX)) == 1
    assert _retired(_meta(cid)) and "route_scene" not in _meta(cid)


def test_an_unreadable_connection_drops_only_its_units_from_the_pass(home):
    """R3-1: a connection only Saltmarch names cannot be read: Saltmarch is
    dropped from the pass with its reason; `config.md` and Winifred retire."""
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    held = _campaign("Saltmarch")
    free = _campaign("Winifred")
    campaigns.set_campaign_routing(held, {"route_scene": "spare"})
    _c_era()
    (home / "llm_connections" / "spare.md").write_bytes(b"")
    held_before = campaigns.paths.campaign_meta_path(held).read_bytes()

    plan = retire.pass_plan(legacy_plan.lookup(mode="retire"))
    assert {u.scope for u in plan.units} == {"global", "campaign:winifred"}
    assert len(plan.dropped) == 1 and "campaign saltmarch" in plan.dropped[0]
    assert "spare" in plan.dropped[0]

    got = migrate.ensure()
    assert got.state == "done"
    assert _retired(_raw_config()) and _retired(_meta(free))
    assert campaigns.paths.campaign_meta_path(held).read_bytes() == held_before
    assert any("spare" in item for item in got.retirement["left"])


def test_the_archive_is_taken_once_per_root_and_reused_on_resume(home, monkeypatch):
    cid = _legacy_glm()
    _c_era()

    def stuck(*_a, **_k):
        raise OSError("held by a sync client")

    with monkeypatch.context() as m:
        m.setattr(retire, "retire_campaign", stuck)
        got = migrate.ensure()
    assert "held by a sync client" in got.retirement["failed"]
    assert _retired(_raw_config()) and not _retired(_meta(cid))
    taken = _archives(backups.RETIRE_PREFIX)
    assert len(taken) == 1

    got = migrate.ensure()
    assert got.retirement == {"left": [], "failed": ""}
    assert _retired(_meta(cid))
    assert _archives(backups.RETIRE_PREFIX) == taken


def test_a_format_1_store_retires_under_its_pre_inference_archive(home):
    """The run that migrates a format-1 store took its `pre-inference-`
    archive moments ago; retirement takes no second one."""
    cid = _legacy_glm()
    assert migrate.ensure().state == "done"
    assert len(_archives(backups.SAFETY_PREFIX)) == 1
    assert _archives(backups.RETIRE_PREFIX) == []
    cfg = _raw_config()
    assert _retired(cfg) and _retired(_meta(cid))
    _no_legacy_keys(cfg, keys.LEGACY_GLOBAL_KEYS)
    _no_legacy_keys(_meta(cid), retire.LEGACY_CAMPAIGN_KEYS)


def test_a_reused_pre_inference_archive_does_not_stand_in(home, monkeypatch):
    """N13: a `pre-inference-` archive kept from an earlier run predates every
    edit since, so retirement takes its own."""
    _legacy_glm()
    with monkeypatch.context() as m:
        m.setattr(migrate, "_switch", lambda run: (_ for _ in ()).throw(OSError("disk")))
        assert migrate.ensure().state == "failed"
    assert len(_archives(backups.SAFETY_PREFIX)) == 1

    assert migrate.ensure().state == "done"
    assert len(_archives(backups.SAFETY_PREFIX)) == 1
    assert len(_archives(backups.RETIRE_PREFIX)) == 1
    assert _retired(_raw_config())


@pytest.mark.product_birth
def test_a_product_birth_store_takes_no_archive_and_writes_nothing(home):
    """N3: born retired, so no pass ever runs: no archive, nothing written,
    and the seeding of the connections writes no legacy key into it."""
    cfg = config.read_config()
    assert cfg[RETIRED] == "1" and keys.is_current(cfg)
    llm_connections.list_connections()
    assert not _raw_config().get("active_connection_id")
    before = _digest(home)
    for _ in range(2):
        assert migrate.ensure().state == "done"
    assert _archives(backups.RETIRE_PREFIX) == []
    assert _digest(home) == before
    assert _raw_config()[RETIRED] == "1"


def test_a_new_campaign_on_a_retired_store_triggers_no_pass(home):
    config.read_config()
    cid = _campaign("Saltmarch")
    assert _meta(cid)[RETIRED] == "1" and _meta(cid)[FORMAT] == "2"
    llm_connections.list_connections()
    assert retire.left() == ()
    before = _digest(home)
    assert migrate.ensure().state == "done"
    assert _digest(home) == before


def test_a_new_campaign_on_an_unretired_store_joins_the_next_pass(home):
    inference_fixtures.unretired()
    cid = _campaign("Saltmarch")
    assert _meta(cid)[FORMAT] == "2" and RETIRED not in _meta(cid)
    assert migrate.ensure().state == "done"
    assert _retired(_meta(cid)) and _retired(_raw_config())


def test_a_fork_is_never_stamped_retired_by_its_birth(home):
    """R2-1: only creation stamps. On a retired store, a fork of an unretired
    campaign copies it as it stands, and a fork of an unmarked one is
    translated and marked current -- the format marker alone -- so either
    joins the next pass."""
    inference_fixtures.unretired()
    marked = _campaign("Saltmarch")
    unmarked = _campaign("Winifred")
    _write_meta(unmarked, {**{k: v for k, v in _meta(unmarked).items() if k != FORMAT},
                           "route_scene": "openrouter"})
    _write_config({**_raw_config(), RETIRED: "1"})
    for source in (marked, unmarked):
        fork = store.fork.fork_campaign(source, f"{source} again")["id"]
        assert RETIRED not in _meta(fork) and _meta(fork)[FORMAT] == "2", source
    assert _meta(fork)[keys.pin_key("scene", "provider")] == "openrouter"


def _glm_pinned_campaign() -> str:
    """On a retired store: `glm` (effort high), the preset "Warm", and
    Saltmarch -- marked, NOT retired -- pinning its scene route to glm at
    "warm"."""
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    _glm("glm", "high")
    cid = _campaign("Saltmarch")
    campaign_lifecycle.set_campaign_inference(cid, {
        keys.use_key("scene"): keys.PIN, keys.pin_key("scene", "provider"): "glm",
        keys.pin_key("scene", "model"): "glm-5.3", keys.pin_key("scene", "preset"): "warm"})
    _write_meta(cid, {k: v for k, v in _meta(cid).items() if k != RETIRED})
    return cid


def _scene_effort(cid: str) -> str:
    target = inference.resolve("chat", cid).attempts[0].target
    return llm_sampling.effective(target)["effective"].get("reasoning_effort", "")


def test_a_settings_write_never_marks_a_campaign_retired(client):
    """R2-1: a role edit on a campaign whose retirement is pending stamps the
    format marker alone; the pin and its in-memory effort stay; the next pass
    retires it with its repoint."""
    cid = _glm_pinned_campaign()
    assert _scene_effort(cid) == "high"

    campaign_lifecycle.set_campaign_inference(cid, {
        keys.role_key("fast", "provider"): "openrouter",
        keys.role_key("fast", "model"): "vendor/fast"})

    meta = _meta(cid)
    assert RETIRED not in meta
    assert meta[keys.pin_key("scene", "preset")] == "warm"
    assert _scene_effort(cid) == "high"
    assert migrate.ensure().state == "done"
    assert _retired(_meta(cid))
    assert _meta(cid)[keys.pin_key("scene", "preset")] == "warm-reasoning-high"
    assert _scene_effort(cid) == "high"


def test_an_unmarked_campaign_written_on_a_retired_store_is_not_marked_retired(client):
    """R2-1: the settings write path (spec 11.1) migrates an unmarked
    campaign in its hold -- the format marker, never the retirement one --
    and the next pass retires it."""
    cid = _campaign("Saltmarch")
    _write_meta(cid, {**{k: v for k, v in _meta(cid).items() if k not in (FORMAT, RETIRED)},
                      "route_scene": "openrouter"})

    inference_settings.write("campaign", cid, {"roles": {"fast": {"selection": {
        "provider": "openrouter", "model": "vendor/fast"}}}})

    meta = _meta(cid)
    assert meta[FORMAT] == "2" and RETIRED not in meta
    assert meta[keys.pin_key("scene", "provider")] == "openrouter"
    assert meta["route_scene"] == "openrouter"
    assert migrate.ensure().state == "done"
    assert _retired(_meta(cid)) and "route_scene" not in _meta(cid)


def test_a_c_era_writer_keeps_the_retired_marker(home):
    """N17: the writers C-H shipped merge onto the raw frontmatter, so the
    marker survives them."""
    config.read_config()
    cid = _campaign("Saltmarch")
    config.write_config(theme="dark")
    campaign_lifecycle.set_campaign_inference(cid, {
        keys.role_key("fast", "provider"): "openrouter",
        keys.role_key("fast", "model"): "vendor/fast"})
    llm_connections.update_connection("openrouter", name="Router")
    assert _raw_config()[RETIRED] == "1"
    assert _meta(cid)[RETIRED] == "1"


# ---- the archive's series ----
def test_sweep_never_prunes_a_pre_retirement_archive(home):
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 1, 1, tzinfo=UTC)
    kept = backups.create_backup(when=start, prefix=backups.RETIRE_PREFIX)
    for n in range(3):
        backups.create_backup(when=start + timedelta(hours=n + 1))
    backups.sweep(keep=1)
    assert kept.exists()
    assert kept.name.startswith("pre-retirement-grimoire-")
    assert [r["name"] for r in backups.list_backups()].count(kept.name) == 1


def test_backups_lists_a_pre_retirement_archive(client):
    made = backups.create_backup(prefix=backups.RETIRE_PREFIX)
    body = client.get("/api/backups").json()
    assert made.name in [r["name"] for r in body["backups"]]
    # A restore point, so the schedule counts it.
    assert not backups.due()


def test_an_abandoned_pre_retirement_temp_is_swept(home):
    import os
    import time

    directory = backups.backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / ".pre-retirement-grimoire-20260101T000000Z.zip.abcd1234.tmp"
    temp.write_bytes(b"half")
    old = time.time() - 7 * 24 * 3600
    os.utime(temp, (old, old))
    assert backups._is_backup_artifact(temp.name)
    backups.create_backup()
    assert not temp.exists()


# ---- what retirement writes ----
def test_derived_presets_keep_the_wire(home):
    """The Task 4 store: what every task resolves to (provider, model,
    preset, sampling and what it sends, the fallback too) is the same
    in memory before and once migrated and retired, and the derived presets
    are files now."""
    cid = _legacy_glm()
    before = equivalence.planned_cells(cid)
    assert migrate.ensure().state == "done"
    assert equivalence.planned_cells(cid) == before
    for pid in ("warm-reasoning-high", "reasoning-low"):
        assert (store.home() / "sampler_presets" / f"{pid}.json").exists(), pid
    assert sampler_presets.read_preset("warm-reasoning-high")["params"] == {
        "temperature": 0.9, "reasoning_effort": "high"}


def test_retirement_repoints_and_deletes_in_one_write(home, monkeypatch):
    cid = _legacy_glm()
    _c_era()
    revs = _revs()
    written: list[str] = []
    real = atomic.write_text

    def counting(path, *args, **kwargs):
        written.append(Path(path).name)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", counting)
    assert migrate.ensure().state == "done"
    monkeypatch.setattr(atomic, "write_text", real)

    assert written.count("config.md") == 1
    assert written.count("campaign.md") == 1
    cfg = _raw_config()
    _no_legacy_keys(cfg, keys.LEGACY_GLOBAL_KEYS)
    assert _retired(cfg)
    assert cfg[keys.role_key("primary", "preset")] == "warm-reasoning-high"
    assert cfg[keys.fallback_key("primary", "preset")] == "reasoning-low"
    meta = _meta(cid)
    _no_legacy_keys(meta, retire.LEGACY_CAMPAIGN_KEYS)
    assert _retired(meta) and meta[FORMAT] == "2"
    assert meta[keys.pin_key("scene", "preset")] == "warm-reasoning-high"
    assert _revs() == revs


def test_retirement_leaves_a_campaigns_updated_stamp_and_bumps_its_token(home):
    cid = _legacy_glm()
    _c_era()
    updated = _meta(cid)["updated"]
    token = revision.current(cid)
    assert migrate.ensure().state == "done"
    assert _meta(cid)["updated"] == updated
    assert revision.current(cid) != token


def test_space_ids_survive_retirement(home):
    _legacy()
    llm_connections.create_connection("openai_compatible", "vectors",
                                      base_url="http://localhost:1234/v1")
    config.write_config(embeddings_connection_id="vectors", embeddings_model="embed-small")
    before = store.embed_space.endpoint()
    assert before is not None
    assert migrate.ensure().state == "done"
    assert _retired(_raw_config())
    assert store.embed_space.endpoint()["space"] == before["space"]


def test_a_campaign_skipped_by_step_8_is_migrated_before_it_is_retired(home, monkeypatch):
    """C2: busy when the migration reached it, free by retirement: one write
    migrates it (its pin), deletes its legacy key and stamps both markers."""
    cid = _legacy_glm()
    monkeypatch.setattr(migrate, "_campaign_step",
                        lambda c, skipped: skipped.append(f"campaign {c}: busy"))
    written: list[str] = []
    real = atomic.write_text

    def counting(path, *args, **kwargs):
        if Path(path) == campaigns.paths.campaign_meta_path(cid):
            written.append("campaign.md")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", counting)
    migrate.ensure()
    monkeypatch.setattr(atomic, "write_text", real)

    meta = _meta(cid)
    assert written == ["campaign.md"]
    assert meta[keys.pin_key("scene", "provider")] == "glm"
    assert meta[keys.pin_key("scene", "preset")] == "warm-reasoning-high"
    assert "route_scene" not in meta
    assert meta[FORMAT] == "2" and _retired(meta)


def test_a_newer_campaign_is_never_retired(home):
    """C2, N18: a campaign a newer build marked is not this build's to
    write; it stays named in `left`, holding the strip."""
    _legacy()
    cid = _campaign("Saltmarch")
    _c_era()
    _write_meta(cid, {**_meta(cid), FORMAT: "3", "route_scene": "openrouter"})
    before = campaigns.paths.campaign_meta_path(cid).read_bytes()
    got = migrate.ensure()
    assert campaigns.paths.campaign_meta_path(cid).read_bytes() == before
    assert _retired(_raw_config())
    assert ("campaign saltmarch: written by a newer build; the strip waits for it"
            in got.retirement["left"])
    with locks.campaign_lock(cid):
        assert retire.retire_campaign(cid, legacy_plan.lookup(mode="retire")) is None


_DAMAGE = {"zero_bytes": b"", "whitespace": b"   \n",
           "unfenced": b"---\nactive_connection_id: glm\ntheme: dark\n"}


@pytest.mark.parametrize("damage", [*sorted(_DAMAGE), "no_marker"])
def test_retire_global_refuses_a_config_it_cannot_parse(home, damage):
    """I4: zero bytes, a truncated fence and a `config.md` with no format
    marker raise `RecordUnreadableError`; the bytes are never written over."""
    _legacy()
    _c_era()
    path = store.home() / "config.md"
    bad = (_DAMAGE[damage] if damage != "no_marker"
           else b"---\nactive_connection_id: glm\ntheme: dark\n---\n")
    path.write_bytes(bad)
    with pytest.raises(frontmatter.RecordUnreadableError):
        retire.retire_global(legacy_plan.lookup(mode="retire"))
    assert path.read_bytes() == bad
    assert not (store.home() / "sampler_presets").exists() or not any(
        (store.home() / "sampler_presets").iterdir())


@pytest.mark.parametrize("damage", sorted(_DAMAGE))
def test_retire_campaign_refuses_an_unparseable_campaign(home, damage):
    """I4: the same damage on a `campaign.md` raises in its own hold and is
    never written over; through `ensure`, the other campaigns retire."""
    _legacy()
    bad_cid = _campaign("Saltmarch")
    good = _campaign("Winifred")
    _c_era()
    path = campaigns.paths.campaign_meta_path(bad_cid)
    path.write_bytes(_DAMAGE[damage])
    with locks.campaign_lock(bad_cid), pytest.raises(frontmatter.RecordUnreadableError):
        retire.retire_campaign(bad_cid, legacy_plan.lookup(mode="retire"))
    got = migrate.ensure()
    assert path.read_bytes() == _DAMAGE[damage]
    assert _retired(_meta(good)) and _retired(_raw_config())
    assert any(item.startswith("campaign saltmarch") for item in got.retirement["left"])


def test_retire_campaign_needs_the_callers_lock(home):
    config.read_config()
    cid = _campaign("Saltmarch")
    with pytest.raises(RuntimeError):
        retire.retire_campaign(cid, legacy_plan.lookup(mode="retire"))


_CONNECTION_DAMAGE = {"zero_bytes": b"", "unfenced": b"kind: openai_compatible\nname: glm\n",
                      "no_kind": b"---\nname: glm\n---\n"}


@pytest.mark.parametrize("damage", sorted(_CONNECTION_DAMAGE))
def test_an_unreadable_connection_holds_the_scopes_that_name_it(home, damage):
    """N1: the GLM connection a global slot and an unmarked Saltmarch pin both
    name cannot be read: `config.md` and Saltmarch keep their bytes, neither
    is marked retired, no preset is written; once it reads again, the next
    run retires both."""
    cid = _legacy_glm(pin=False)
    _c_era()
    _write_meta(cid, {**{k: v for k, v in _meta(cid).items() if k != FORMAT},
                      "route_scene": "glm"})
    conn_path = store.home() / "llm_connections" / "glm.md"
    good = conn_path.read_bytes()
    conn_path.write_bytes(_CONNECTION_DAMAGE[damage])
    cfg_before = (store.home() / "config.md").read_bytes()
    meta_before = campaigns.paths.campaign_meta_path(cid).read_bytes()

    migrate.ensure()

    assert (store.home() / "config.md").read_bytes() == cfg_before
    assert campaigns.paths.campaign_meta_path(cid).read_bytes() == meta_before
    assert not (store.home() / "sampler_presets" / "warm-reasoning-high.json").exists()

    conn_path.write_bytes(good)
    assert migrate.ensure().state == "done"
    assert _retired(_raw_config()) and _retired(_meta(cid))
    assert _meta(cid)[keys.pin_key("scene", "preset")] == "warm-reasoning-high"


def test_a_busy_campaign_is_retired_on_the_next_run(home):
    cid = _legacy_glm()
    _c_era()
    held, release = threading.Event(), threading.Event()

    def holder():
        with locks.campaign_lock(cid):
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
    assert got.state == "done"
    assert not _retired(_meta(cid))
    assert "campaign saltmarch: not retired yet" in got.retirement["left"]

    assert migrate.ensure().state == "done"
    assert _retired(_meta(cid))


def test_retirement_is_idempotent(home):
    _legacy_glm()
    _campaign("Winifred")
    assert migrate.ensure().state == "done"
    after = _digest(home)
    got = migrate.ensure()
    assert _digest(home) == after
    assert got.retirement == {"left": [], "failed": ""}


@pytest.mark.product_birth
def test_retirement_never_moves_the_migration_state(home):
    config.read_config()
    got = migrate.status()
    assert got.state == "done" and got.retirement == {"left": [], "failed": ""}

    inference_fixtures.unretired()
    cid = _campaign("Saltmarch")
    _write_meta(cid, {**_meta(cid), FORMAT: "3"})
    got = migrate.ensure()
    assert got.state == "done"
    assert "config.md: not retired yet" not in got.retirement["left"]
    assert any(cid in item for item in got.retirement["left"])
    assert migrate.status().as_dict()["state"] == "done"


def test_status_never_raises_on_an_unreadable_connection(home):
    config.read_config()
    llm_connections.list_connections()
    (store.home() / "llm_connections" / "held.md").write_bytes(b"")
    got = migrate.status()
    assert got.state == "done"
    assert any("connection held" in item for item in got.retirement["left"])
    assert migrate.status().as_dict()["retirement"]["left"] == got.retirement["left"]


def test_two_devices_write_the_same_bytes(monkeypatch, tmp_path):
    """Two copies of one legacy library, migrated and retired separately,
    hold identical files -- the write tokens aside, which are unique by
    design, and the archives, named for when they were taken."""
    first = tmp_path / "first"
    first.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(first))
    _legacy_glm()
    _campaign("Winifred")
    second = tmp_path / "second"
    shutil.copytree(first, second)

    for root in (first, second):
        monkeypatch.setenv("GRIMOIRE_HOME", str(root))
        assert migrate.ensure().state == "done"
        assert _retired(_raw_config())

    def files(root: Path) -> dict[str, str]:
        return {k: v for k, v in _digest(root).items()
                if not k.endswith(revision.FILENAME)}

    assert files(first) == files(second)


def test_a_retired_but_unmarked_campaign_plays_as_it_is_persisted(home):
    """N1: a campaign carrying the retirement marker and no format marker,
    under a format-1 `config.md`, is mapped in memory exactly as the
    migration then persists it."""
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    cid = _campaign("Saltmarch")
    _write_meta(cid, {**_meta(cid), RETIRED: "1", "route_scene": "spare"})
    played = equivalence.planned_cells(cid)
    assert played["chat"]["campaign"]["provider"] == "spare"
    assert migrate.ensure().state == "done"
    assert equivalence.planned_cells(cid) == played
    meta = _meta(cid)
    assert meta[keys.pin_key("scene", "provider")] == "spare" and "route_scene" not in meta


# ---- the strict pieces ----
def test_read_record_refuses_what_holds_no_record(home):
    path = home / "record.md"
    for text in ("", "   \n", "---\ntheme: dark\n", "---\n---\n"):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(frontmatter.RecordUnreadableError):
            frontmatter.read_record(path, "record.md")
    path.write_text("---\ntheme: dark\n---\nbody\n", encoding="utf-8")
    assert frontmatter.read_record(path, "record.md") == ({"theme": "dark"}, "body\n")
    with pytest.raises(frontmatter.RecordUnreadableError):
        frontmatter.read_record(path, "record.md", require=FORMAT)
    path.write_text(f"---\n{FORMAT}: ' '\n---\n", encoding="utf-8")
    with pytest.raises(frontmatter.RecordUnreadableError):
        frontmatter.read_record(path, "record.md", require=FORMAT)
    with pytest.raises(FileNotFoundError):
        frontmatter.read_record(home / "absent.md", "absent.md")
    assert migrate.RecordUnreadableError is frontmatter.RecordUnreadableError


def test_retire_write_sets_drops_and_adds_nothing_else(home):
    _write_config({**_born_by_c_h(), "active_connection_id": "glm"})
    config.retire_write({RETIRED: "1"}, drop=keys.LEGACY_GLOBAL_KEYS)
    assert _raw_config() == {"theme": "system", FORMAT: "2", RETIRED: "1"}
    before = (store.home() / "config.md").read_bytes()
    config.retire_write({RETIRED: "1"}, drop=keys.LEGACY_GLOBAL_KEYS)
    assert (store.home() / "config.md").read_bytes() == before


def test_retire_write_refuses_a_store_not_at_format_2(home):
    _write_config({"theme": "dark", FORMAT: "1"})
    with pytest.raises(ValueError):
        config.retire_write({RETIRED: "1"}, drop=())
    _write_config({"theme": "dark", FORMAT: "3"})
    with pytest.raises(config.NewerFormatError):
        config.retire_write({RETIRED: "1"}, drop=())
    _write_config({"theme": "dark"})
    with pytest.raises(frontmatter.RecordUnreadableError):
        config.retire_write({RETIRED: "1"}, drop=())
    assert _raw_config() == {"theme": "dark"}


def test_an_unreadable_base_preset_stops_its_scope(home):
    """A base preset a sync client holds is never derived from: the scope
    fails closed rather than saving a derived preset without its samplers."""
    cid = _legacy_glm()
    _c_era()
    (store.home() / "sampler_presets" / "warm.json").write_text("", encoding="utf-8")
    cfg_before = (store.home() / "config.md").read_bytes()
    got = migrate.ensure()
    assert (store.home() / "config.md").read_bytes() == cfg_before
    assert "warm" in got.retirement["failed"]
    assert not (store.home() / "sampler_presets" / "warm-reasoning-high.json").exists()
    assert not _retired(_meta(cid))     # its pin runs on "warm" too
    assert "config.md" in got.retirement["failed"]
    assert "campaign saltmarch" in got.retirement["failed"]


# ======================================================================
# Task 6b: the record, the strip, and the notice
# ======================================================================
MODEL_FIELDS = llm_connections.MODEL_FIELDS


def _conn_file(conn_id: str) -> Path:
    return store.home() / "llm_connections" / f"{conn_id}.md"


def _conn_meta(conn_id: str) -> dict:
    return parse_frontmatter(_conn_file(conn_id).read_text(encoding="utf-8"))[0]


def _held(conn_id: str) -> list[str]:
    return [f for f in MODEL_FIELDS if f in _conn_meta(conn_id)]


def _spare_stripped() -> None:
    """A legacy library with a `spare` provider (model `vendor/spare`),
    migrated, retired and stripped: `spare.md` holds no legacy field, and the
    retirement record holds what it had."""
    _legacy()
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare", sampler_preset="warm")
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    assert migrate.ensure().state == "done"
    assert _held("spare") == []
    assert retired.read()["fields"]["spare"] == {"model": "vendor/spare",
                                                 "sampler_preset": "warm"}


def _late_unmarked(name: str = "Saltmarch", route: str = "spare") -> str:
    """A campaign as an older build (or a restore) leaves one after the
    strip: no format marker, no retirement marker, a legacy route."""
    cid = _campaign(name)
    _write_meta(cid, {**{k: v for k, v in _meta(cid).items() if k not in (FORMAT, RETIRED)},
                      "route_scene": route})
    return cid


def _corrupt_record() -> bytes:
    good = retired.path().read_bytes()
    retired.path().write_text("{not json", encoding="utf-8")
    return good


# ---- the strip ----
def test_retirement_deletes_legacy_fields(home):
    _legacy_glm()
    revs = _revs()
    raw_before = {c: _conn_meta(c) for c in revs}

    assert migrate.ensure().state == "done"

    for conn_id in revs:
        assert _held(conn_id) == [], conn_id
    assert _revs() == revs
    recorded = retired.read()["fields"]
    for conn_id, meta in raw_before.items():
        assert recorded.get(conn_id, {}) == {
            f: meta[f] for f in MODEL_FIELDS if str(meta.get(f, "")).strip()}, conn_id
    assert recorded["glm"]["reasoning_effort"] == "high"
    assert migrate.status().retirement == {"left": [], "failed": ""}


def test_the_strip_keeps_every_other_key_and_the_catalog(home):
    _legacy_glm()
    rev = llm_connections.read_connection_raw("glm")["rev"]
    llm_connections.set_cached_models("glm", [{"id": "glm-5.3"}], rev)
    assert migrate.ensure().state == "done"
    meta = _conn_meta("glm")
    assert (meta["kind"], meta["name"], meta["base_url"], meta["rev"]) == (
        "openai_compatible", "glm", base_c.GLM_URL, rev)
    assert [m["id"] for m in llm_connections.cached_models("glm")["models"]] == ["glm-5.3"]


def test_fields_are_stripped_only_after_every_campaign_is_retired(home):
    cid = _legacy_glm()
    held, release = threading.Event(), threading.Event()

    def holder():
        with locks.campaign_lock(cid):
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
    assert not _retired(_meta(cid))
    assert _conn_meta("glm")["reasoning_effort"] == "high"
    assert "fields" not in retired.read() or "glm" not in retired.read()["fields"]
    assert any(item.startswith("connection glm") for item in got.retirement["left"])

    assert migrate.ensure().state == "done"
    assert _retired(_meta(cid)) and _held("glm") == []


def test_an_unreadable_campaign_holds_the_strip(home):
    """C3: a zero-byte unmarked `campaign.md` -- nothing is stripped and
    `left` names it; once it reads again, the next run migrates it, retires
    it and strips."""
    _legacy()
    _glm("glm", "high")
    cid = _campaign("Saltmarch")
    campaigns.set_campaign_routing(cid, {"route_scene": "glm"})
    path = campaigns.paths.campaign_meta_path(cid)
    good = path.read_bytes()
    path.write_bytes(b"")

    got = migrate.ensure()
    assert path.read_bytes() == b""
    assert _conn_meta("glm")["reasoning_effort"] == "high"
    assert any(item.startswith("campaign saltmarch") for item in got.retirement["left"])

    path.write_bytes(good)
    assert migrate.ensure().state == "done"
    meta = _meta(cid)
    assert meta[FORMAT] == "2" and _retired(meta)
    assert meta[keys.pin_key("scene", "preset")] == "reasoning-high"
    assert _held("glm") == []


@pytest.mark.parametrize("damage", [b"", b"kind: openrouter\nmodel: x\n",
                                    b"---\nmodel: vendor/spare\n---\n"],
                         ids=["zero-bytes", "unfenced", "no-kind"])
def test_strip_model_fields_refuses_an_unparseable_connection(home, damage):
    config.read_config()
    llm_connections.list_connections()
    path = _conn_file("held")
    path.write_bytes(damage)
    with pytest.raises(frontmatter.RecordUnreadableError):
        llm_connections.strip_model_fields("held")
    assert path.read_bytes() == damage
    assert not retired.path().exists()


def test_a_connection_edit_during_the_strip_survives(client, monkeypatch):
    """N4: the strip holds the model-settings lock across its read, its
    record and its write, so a provider edit that arrives meanwhile waits --
    and lands after it, with its own key and rev, over the stripped file."""
    _glm("glm", "high")
    assert _conn_meta("glm")["reasoning_effort"] == "high"
    real = retired.record_fields
    edit: dict = {}

    def record_then_edit(conn_id, values):
        real(conn_id, values)
        worker = threading.Thread(target=lambda: edit.update(
            got=client.put("/api/llm-connections/glm", json={"api_key": "sk-new"})))
        worker.start()
        worker.join(0.5)
        assert worker.is_alive(), "the edit was not held back by the strip"
        edit["worker"] = worker

    monkeypatch.setattr(retired, "record_fields", record_then_edit)
    rev = _conn_meta("glm")["rev"]
    assert retire.strip_connection("glm") == ()
    edit["worker"].join(WAIT)
    assert edit["got"].status_code == 200, edit["got"].text
    meta = _conn_meta("glm")
    assert meta["api_key"] == "sk-new" and meta["rev"] != rev
    assert [f for f in MODEL_FIELDS if f in meta] == []
    assert retired.read()["fields"]["glm"]["reasoning_effort"] == "high"


# ---- the record, read by the planner ----
def test_the_strip_records_the_fields_first_and_the_planner_reads_them(home):
    """C3, R2-2: after the strip a late unmarked campaign naming `spare`
    resolves spare's recorded model in memory; the next `ensure`, with step
    8 active, persists that model and its preset -- never "" -- and then
    retires it."""
    _spare_stripped()
    cid = _late_unmarked()
    first = inference.resolve("chat", cid).attempts[0]
    assert (first.provider_id, first.model) == ("spare", "vendor/spare")

    assert migrate.ensure().state == "done"
    meta = _meta(cid)
    assert meta[keys.pin_key("scene", "model")] == "vendor/spare"
    assert meta[keys.pin_key("scene", "preset")] == "warm"
    assert _retired(meta) and "route_scene" not in meta


def test_an_unreadable_record_holds_a_late_unmarked_campaign(home):
    """N6, R2-2: with the record unreadable, step 8 cannot read spare's
    fields and leaves the campaign as it is; once repaired, the migration
    maps it with spare's recorded model and retirement retires it."""
    _spare_stripped()
    cid = _late_unmarked()
    before = campaigns.paths.campaign_meta_path(cid).read_bytes()
    good = _corrupt_record()

    got = migrate.ensure()
    assert campaigns.paths.campaign_meta_path(cid).read_bytes() == before
    assert any(cid in item and "retirement record" in item for item in got.skipped), got
    assert "campaign saltmarch: not migrated yet" in got.retirement["left"]

    retired.path().write_bytes(good)
    assert migrate.ensure().state == "done"
    meta = _meta(cid)
    assert meta[keys.pin_key("scene", "model")] == "vendor/spare" and _retired(meta)


def test_the_write_path_reads_the_record_on_a_retired_store(client):
    """R2-2, R3-3: the settings write that reaches a late unmarked campaign
    first migrates it with spare's recorded model; with the record
    unreadable it answers 409 `retirement_unreadable` and writes nothing."""
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    assert retire.strip() == []
    assert _held("spare") == []
    cid = _late_unmarked()
    before = campaigns.paths.campaign_meta_path(cid).read_bytes()
    good = _corrupt_record()
    body = {"roles": {"fast": {"selection": {"provider": "openrouter", "model": "vendor/fast"}}}}

    got = client.put(f"/api/campaigns/{cid}/inference", json=body)
    assert got.status_code == 409, got.text
    assert got.json() == {
        "kind": "retirement_unreadable",
        "detail": "The record of retired model settings could not be read; "
                  "try again once it has synced."}
    assert campaigns.paths.campaign_meta_path(cid).read_bytes() == before

    retired.path().write_bytes(good)
    got = client.put(f"/api/campaigns/{cid}/inference", json=body)
    assert got.status_code == 200, got.text
    assert _meta(cid)[keys.pin_key("scene", "model")] == "vendor/spare"


def test_the_record_fallback_needs_no_config_marker(home):
    """R3-2: the record's entry proves the strip happened, whatever
    `config.md` says: with the retirement marker taken off it by hand, step 8
    still persists spare's recorded model."""
    _spare_stripped()
    _write_config({k: v for k, v in _raw_config().items() if k != RETIRED})
    cid = _late_unmarked()
    migrate.ensure()
    assert _meta(cid)[keys.pin_key("scene", "model")] == "vendor/spare"


def test_an_empty_legacy_field_still_falls_back_to_the_record(home):
    """N6: a C-H edit wrote `model: ""` back after the strip; an empty field
    is no field, so the record still answers."""
    _spare_stripped()
    _conn_file("spare").write_text(
        dump_frontmatter({**_conn_meta("spare"), "model": ""}, ""), encoding="utf-8")
    for mode in ("soft", "migrate", "retire"):
        assert legacy_plan.lookup(mode=mode)("spare")["model"] == "vendor/spare", mode


def test_the_record_never_answers_for_a_deleted_connection(home):
    _spare_stripped()
    llm_connections.delete_connection("spare")
    assert "spare" not in retired.read()["fields"]
    for mode in ("soft", "migrate", "retire"):
        assert legacy_plan.lookup(mode=mode)("spare") is None, mode


def test_a_recreated_slug_never_inherits_the_dead_ones_model(home):
    """6b review M-6: the delete forgets the record's entry in its own
    hold, so a provider created later under the same slug answers with its
    own (empty) model, not the stripped one's."""
    _spare_stripped()
    llm_connections.delete_connection("spare")
    assert llm_connections.create_connection("openrouter", "spare", api_key="sk-2") == "spare"
    for mode in ("soft", "migrate", "retire"):
        assert legacy_plan.lookup(mode=mode)("spare")["model"] == "", mode


def test_a_delete_over_an_unreadable_record_deletes_nothing(client):
    """6b re-review N-1: the record is read before anything is written, so a
    refused delete of the Primary's provider leaves `config.md` -- the
    Primary included -- byte for byte, and the file in place."""
    llm_connections.create_connection("openrouter", "spare", api_key="sk-spare",
                                      model="vendor/spare")
    assert retire.strip() == []
    inference_fixtures.put_settings(client, {"roles": {"primary": {"selection": {
        "provider": "spare", "model": "vendor/spare"}}}})
    cfg_before = (store.home() / "config.md").read_bytes()
    conn_before = _conn_file("spare").read_bytes()
    good = _corrupt_record()
    got = client.delete("/api/llm-connections/spare")
    assert got.status_code == 409 and got.json()["kind"] == "retirement_unreadable"
    assert (store.home() / "config.md").read_bytes() == cfg_before
    assert _raw_config()[keys.role_key("primary", "provider")] == "spare"
    assert _conn_file("spare").read_bytes() == conn_before
    assert retired.path().read_text(encoding="utf-8") == "{not json"
    retired.path().write_bytes(good)
    assert client.delete("/api/llm-connections/spare").status_code == 200
    assert "spare" not in retired.read()["fields"]


def test_the_first_recorded_fields_win(home):
    """N20: a pre-C build writes `model: other` back after the strip; the next
    strip takes it off again and the record keeps the first value."""
    _spare_stripped()
    _conn_file("spare").write_text(
        dump_frontmatter({**_conn_meta("spare"), "model": "vendor/other"}, ""),
        encoding="utf-8")
    assert migrate.ensure().state == "done"
    assert _held("spare") == []
    assert retired.read()["fields"]["spare"]["model"] == "vendor/spare"


def test_the_planners_record_read_is_strict_for_writers_only(home):
    _spare_stripped()
    _corrupt_record()
    assert legacy_plan.lookup(mode="soft")("spare")["model"] == ""
    for mode in ("migrate", "retire"):
        with pytest.raises(retired.RecordUnreadableError):
            legacy_plan.lookup(mode=mode)("spare")


# ---- a stripped connection whose record entry has not arrived (brutal review 1, 🟡1) ----
def _arrived_before_the_record(root: Path) -> bytes:
    """A device's state mid-sync after another device retired the store: the
    stripped connection files are here, the record and the scope files the
    pass rewrote are not. Retires `root` first, then puts `config.md`, every
    `campaign.md` and the sampler presets back as they were before, and
    removes the record. Returns the record's bytes."""
    before = {p.relative_to(root).as_posix(): p.read_bytes()
              for p in [root / "config.md", *root.glob("campaigns/*/campaign.md"),
                        *root.glob("sampler_presets/*")] if p.is_file()}
    assert migrate.ensure().retirement == {"left": [], "failed": ""}
    assert not llm_connections.legacy_fields_on_disk()
    record = retired.path().read_bytes()
    retired.path().unlink()
    for path in root.glob("sampler_presets/*"):
        if path.relative_to(root).as_posix() not in before:
            path.unlink()
    for rel, data in before.items():
        (root / rel).write_bytes(data)
    shutil.rmtree(root / ".cache", ignore_errors=True)
    return record


def test_the_strip_marks_what_it_recorded_and_an_edit_keeps_the_mark(home):
    """The strip writes `STRIPPED_KEY` in the write that takes the fields
    off -- only where it recorded some -- and this build's own edit of the
    connection keeps it. A connection created at format 2 carries none."""
    _spare_stripped()
    assert _conn_meta("spare")[llm_connections.STRIPPED_KEY] == "1"
    assert llm_connections.stripped("spare")
    llm_connections.update_connection("spare", name="Spare Again")
    assert _conn_meta("spare")[llm_connections.STRIPPED_KEY] == "1"
    fresh = llm_connections.create_connection("openrouter", "Mara", api_key="sk-mara")
    assert llm_connections.STRIPPED_KEY not in _conn_meta(fresh)
    assert not llm_connections.stripped(fresh)
    # Never part of the record as read, so no API body carries it.
    assert llm_connections.STRIPPED_KEY not in llm_connections.read_connection_raw("spare")


def test_a_stripped_connection_with_no_entry_refuses_a_strict_lookup(home):
    """The marker tells "stripped, entry not here yet" from "created at
    format 2 with no legacy field": a strict lookup raises for the first and
    answers the second; play's soft lookup answers both as they stand."""
    _spare_stripped()
    fresh = llm_connections.create_connection("openrouter", "Mara", api_key="sk-mara")
    retired.path().unlink()
    for mode in ("migrate", "retire"):
        with pytest.raises(retired.EntryMissingError):
            legacy_plan.lookup(mode=mode)("spare")
        assert legacy_plan.lookup(mode=mode)(fresh)["model"] == ""
    assert legacy_plan.lookup(mode="soft")("spare")["model"] == ""


def test_a_partial_sync_ahead_of_the_record_retires_nothing_until_it_arrives(home):
    """Path 1: another device retired and stripped the store, and this one
    has the stripped connection files but not yet the record, the retired
    `config.md` and `campaign.md` or the derived presets. Its pass writes
    nothing -- no scope is marked retired with its GLM efforts read as
    absent -- and says why; once the record arrives the next start retires
    every scope as the first device did."""
    cid = _legacy_glm()
    _c_era()
    root = store.home()
    record = _arrived_before_the_record(root)
    digest = _digest(root)

    got = migrate.ensure()
    assert _digest(root) == digest
    assert got.state == "done"
    assert "holds no entry" in got.retirement["failed"], got.retirement
    assert "config.md: not retired yet" in got.retirement["left"]
    assert not _retired(_raw_config()) and not _retired(_meta(cid))
    # The same again on the next start: never marked, never "done" by default.
    migrate.ensure()
    assert _digest(root) == digest

    retired.path().write_bytes(record)
    assert migrate.ensure().retirement == {"left": [], "failed": ""}
    done_cfg = _raw_config()
    assert _retired(done_cfg)
    assert done_cfg[keys.role_key("primary", "preset")] == "warm-reasoning-high"
    assert done_cfg[keys.fallback_key("primary", "preset")] == "reasoning-low"
    meta = _meta(cid)
    assert _retired(meta) and meta[keys.pin_key("scene", "model")] == "glm-5.3"
    assert meta[keys.pin_key("scene", "preset")] == "warm-reasoning-high"
    assert inference.resolve("chat").chain.primary.sampling.params["reasoning_effort"] == "high"


def test_a_format_1_config_ahead_of_the_record_is_not_switched(home):
    """Path 1 on a device whose `config.md` is still format 1: the switch
    reads every connection through the migration's lookup -- the facts step
    included -- so it fails rather than switching (or taking back the facts
    copies) from connections read as having no model."""
    _legacy_glm()
    root = store.home()
    legacy_cfg = (root / "config.md").read_bytes()
    assert migrate.ensure().retirement == {"left": [], "failed": ""}
    facts_before = {p.name: p.read_bytes() for p in root.glob("llm_connections/*.facts.json")}
    record = retired.path().read_bytes()
    retired.path().unlink()
    (root / "config.md").write_bytes(legacy_cfg)

    got = migrate.ensure()
    assert got.state == "failed" and "holds no entry" in got.reason, got
    assert (root / "config.md").read_bytes() == legacy_cfg
    assert {p.name: p.read_bytes()
            for p in root.glob("llm_connections/*.facts.json")} == facts_before

    retired.path().write_bytes(record)
    assert migrate.ensure().state == "done"
    cfg = _raw_config()
    assert cfg[keys.role_key("primary", "model")] == "glm-5.3"
    assert cfg[keys.role_key("primary", "preset")] == "warm-reasoning-high"


def test_a_late_campaign_waits_for_a_stripped_connections_record(home):
    """Path 2: a late unmarked campaign (a restore, an old folder copied in)
    pins a stripped connection whose record entry is not here. It is neither
    migrated nor retired -- never persisted with an empty model -- and is
    finished, with the recorded model, once the record is back."""
    _spare_stripped()
    cid = _late_unmarked()
    before = campaigns.paths.campaign_meta_path(cid).read_bytes()
    record = retired.path().read_bytes()
    retired.path().unlink()

    got = migrate.ensure()
    assert campaigns.paths.campaign_meta_path(cid).read_bytes() == before
    assert any(cid in item and "holds no entry" in item for item in got.skipped), got
    assert f"campaign {cid}: not migrated yet" in got.retirement["left"]

    retired.path().write_bytes(record)
    assert migrate.ensure().state == "done"
    meta = _meta(cid)
    assert meta[keys.pin_key("scene", "model")] == "vendor/spare"
    assert meta[keys.pin_key("scene", "preset")] == "warm"
    assert _retired(meta)


# ---- the facts check (N9) ----
def test_a_fact_the_migration_skipped_is_noted_before_the_strip(home, monkeypatch):
    """C's facts copy for `glm` did not land (its write raised), so glm
    states `prefill: true` that its model's facts do not: one
    `fact_not_carried` note, in the record before the field goes. `glm2`,
    whose fact was copied, gives none."""
    _legacy()
    _glm("glm", "high")
    _glm("glm2", "low")
    llm_connections.update_connection("glm", prefill=True)
    llm_connections.update_connection("glm2", prefill=True)
    assert inference_fixtures.migrate_as_c_h().state == "done"
    llm_connections.facts_path("glm").unlink()        # the copy that never landed
    real = llm_connections.strip_model_fields
    order: list[tuple[str, list[str]]] = []

    def watched(conn_id):
        order.append((conn_id, [n["kind"] for n in retired.read()["notes"]
                                if n["provider_id"] == conn_id]))
        return real(conn_id)

    monkeypatch.setattr(llm_connections, "strip_model_fields", watched)
    assert migrate.ensure().state == "done"

    notes = [n for n in retired.read()["notes"] if n["kind"] == "fact_not_carried"]
    assert [(n["provider_id"], n["subject"]) for n in notes] == [("glm", "prefill")]
    assert "this was not carried over" in notes[0]["text"]
    assert ("glm", ["fact_not_carried"]) in order
    assert ("glm2", []) in order
    assert _held("glm") == [] and _held("glm2") == []


def test_an_unreadable_facts_file_stops_its_connections_strip(home):
    _legacy()
    _glm("glm", "high")
    llm_connections.update_connection("glm", prefill=True)
    assert inference_fixtures.migrate_as_c_h().state == "done"
    llm_connections.facts_path("glm").write_text("", encoding="utf-8")
    got = migrate.ensure()
    assert _conn_meta("glm").get("prefill") == "true"
    assert "connection glm" in got.retirement["failed"]
    assert llm_connections.facts_path("glm").read_text(encoding="utf-8") == ""


# ---- N5: a legacy key an older build writes back, one scope per test ----
def _retired_glm_store() -> str:
    cid = _legacy_glm()
    assert migrate.ensure().state == "done"
    assert migrate.status().retirement["left"] == []
    return cid


def _chat(cid: str = "") -> tuple:
    first = inference.resolve("chat", cid).attempts[0]
    return (first.provider_id, first.model, first.preset_id,
            llm_sampling.effective(first.target)["effective"])


def test_a_legacy_config_key_written_after_retirement_is_ignored_then_removed(home):
    _retired_glm_store()
    played = _chat()
    clean = _raw_config()
    _write_config({**clean, "active_connection_id": "openrouter"})
    assert _chat() == played
    assert retire.left() == ("config.md: holds legacy settings again (active_connection_id)",)
    assert migrate.ensure().state == "done"
    assert _raw_config() == clean


def test_a_legacy_campaign_key_written_after_retirement_is_ignored_then_removed(home):
    cid = _retired_glm_store()
    played = _chat(cid)
    clean = _meta(cid)
    _write_meta(cid, {**clean, "route_scene": "openrouter"})
    assert _chat(cid) == played
    assert retire.left() == ("campaign saltmarch: holds legacy settings again (route_scene)",)
    assert migrate.ensure().state == "done"
    assert _meta(cid) == clean


def test_a_legacy_connection_field_written_after_retirement_is_ignored_then_removed(home):
    _retired_glm_store()
    played = _chat()
    clean = _conn_meta("glm")
    _conn_file("glm").write_text(dump_frontmatter({**clean, "reasoning_effort": "low"}, ""),
                                 encoding="utf-8")
    assert _chat() == played
    assert retire.left() == ("connection glm: holds legacy model settings (reasoning_effort)",)
    assert migrate.ensure().state == "done"
    assert _conn_meta("glm") == clean


# ---- births and edits after retirement (I2) ----
@pytest.mark.product_birth
def test_a_product_birth_store_reads_done(home):
    config.read_config()
    llm_connections.list_connections()
    got = migrate.status()
    assert got.state == "done" and got.retirement == {"left": [], "failed": ""}


@pytest.mark.product_birth
def test_a_fresh_store_never_gets_active_connection_id(home):
    config.read_config()
    llm_connections.list_connections()
    raw = _raw_config()
    assert "active_connection_id" not in raw
    held = [k for k in keys.LEGACY_GLOBAL_KEYS if k in raw]
    assert held == []
    for conn_id in ("openrouter", "claude"):
        assert _held(conn_id) == [], conn_id


def test_a_connection_edit_after_retirement_writes_no_legacy_field(client):
    for body in ({"name": "Router"}, {"api_key": "sk-new"}):
        got = client.put("/api/llm-connections/openrouter", json=body)
        assert got.status_code == 200, got.text
        assert _held("openrouter") == [], body


def test_a_resumed_pass_with_only_the_strip_left_takes_an_archive(home):
    """R2-3: every scope retired, the fields still on the connections, no
    note of an archive: the strip deletes stored values, so the pass takes
    one first."""
    cid = _legacy_glm()
    _c_era()
    retire.retire_global(legacy_plan.lookup(mode="retire"))
    with locks.campaign_lock(cid):
        retire.retire_campaign(cid, legacy_plan.lookup(mode="retire"))
    for name in _archives(backups.RETIRE_PREFIX):
        (backups.backup_dir() / name).unlink()
    (store.home() / ".cache" / "inference-migration.json").unlink()
    plan = retire.pass_plan(legacy_plan.lookup(mode="retire"))
    assert {u.conn_id for u in plan.units if u.work} >= {"glm", "glm2"}
    assert retire.needs_archive(plan)
    before = _conn_meta("glm")

    assert migrate.ensure().state == "done"
    taken = _archives(backups.RETIRE_PREFIX)
    assert len(taken) == 1
    with zipfile.ZipFile(backups.backup_dir() / taken[0]) as z:
        archived = parse_frontmatter(z.read("llm_connections/glm.md").decode("utf-8"))[0]
    assert archived == before
    assert _held("glm") == []


# ---- the notes on /models ----
def _route_preset_loss(client) -> None:
    """A legacy library whose summary route wears "Cold" (no reasoning
    effort) over a GLM Primary at effort high: the route's effort cannot be
    carried (ratification item 3)."""
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    config.write_config(active_connection_id="glm", preset_summary="cold")


def _notes(client, cid: str = "") -> list[dict]:
    url = f"/api/campaigns/{cid}/inference" if cid else "/api/inference/settings"
    got = client.get(url)
    assert got.status_code == 200, got.text
    return got.json()["retirement_notes"]


def test_a_noted_loss_is_shown_before_retirement_writes_it(legacy_client):
    _route_preset_loss(legacy_client)
    notes = _notes(legacy_client)
    assert [(n["kind"], n["subject"], n["provider_id"], n["effort"], n["scope_name"])
            for n in notes] == [("route_preset", "summary", "glm", "high", ""),
                                ("route_preset", "scene_break", "glm", "high", "")]
    assert all(n["text"].endswith("— this was not carried over.") for n in notes)
    assert not retired.path().exists()


def test_retired_notes_are_durable_and_dismissable(legacy_client, tmp_path, monkeypatch):
    """After retirement the planner plans nothing there, and the notes are
    the record's: shown on `/models`, kept by a second run, dismissed for
    good -- through another retirement pass, and on a second device reading
    the same files."""
    _route_preset_loss(legacy_client)
    before = _notes(legacy_client)
    assert migrate.ensure().state == "done"
    assert _retired(_raw_config())
    assert _notes(legacy_client) == before          # end to end: the record's now
    assert {n["id"] for n in retired.read()["notes"]} == {n["id"] for n in before}
    assert migrate.ensure().state == "done"
    assert _notes(legacy_client) == before

    target = before[0]["id"]
    got = legacy_client.post(f"/api/inference/retired-notes/{target}/dismiss")
    assert got.status_code == 200, got.text
    assert [n["id"] for n in _notes(legacy_client)] == [before[1]["id"]]

    # Another pass computes the same note again (config.md unretired by hand,
    # glm's effort read from the record): it stays dismissed.
    _write_config({k: v for k, v in _raw_config().items() if k != RETIRED})
    assert migrate.ensure().state == "done"
    assert [n["id"] for n in _notes(legacy_client)] == [before[1]["id"]]

    second = tmp_path / "second-device"
    shutil.copytree(store.home(), second)
    monkeypatch.setenv("GRIMOIRE_HOME", str(second))
    dismissed = {n["id"]: n["dismissed"] for n in retired.read()["notes"]}
    assert dismissed == {before[0]["id"]: True, before[1]["id"]: False}


def test_a_planner_note_can_be_dismissed_before_retirement(legacy_client):
    """N14: dismissing a note the planner computed records it, dismissed, so
    retirement never brings it back."""
    _route_preset_loss(legacy_client)
    target = _notes(legacy_client)[0]
    got = legacy_client.post(f"/api/inference/retired-notes/{target['id']}/dismiss")
    assert got.status_code == 200, got.text
    assert [n["dismissed"] for n in retired.read()["notes"]] == [True]
    assert target["id"] not in [n["id"] for n in _notes(legacy_client)]
    assert migrate.ensure().state == "done"
    assert target["id"] not in [n["id"] for n in _notes(legacy_client)]


def test_dismissing_an_unknown_note_is_a_404(client):
    got = client.post("/api/inference/retired-notes/0000000000000000/dismiss")
    assert got.status_code == 404


def test_dismissing_on_an_unreadable_record_is_a_409(client):
    retired.path().write_text("{not json", encoding="utf-8")
    got = client.post("/api/inference/retired-notes/0000000000000000/dismiss")
    assert got.status_code == 409
    assert got.json()["kind"] == "retirement_unreadable"
    assert retired.path().read_text(encoding="utf-8") == "{not json"


def test_a_note_id_ignores_its_wording(home):
    config.read_config()
    note = retired.Note(retired.note_id("global", "summary", "glm", "high", "route_preset"),
                        "global", "summary", "glm", "high", "route_preset", "Old words.")
    retired.record_notes([note])
    assert retired.dismiss(note.id)
    reworded = note._replace(text="New words.")
    assert reworded.id == retired.note_id("global", "summary", "glm", "high", "route_preset")
    retired.record_notes([reworded])
    rows = retired.read()["notes"]
    assert [(r["id"], r["text"], r["dismissed"]) for r in rows] == [
        (note.id, "New words.", True)]


def test_a_campaign_note_names_its_campaign(legacy_client):
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    cid = _campaign("Saltmarch")
    campaigns.set_campaign_routing(cid, {"route_tracker": "glm", "preset_tracker": "cold"})
    assert migrate.ensure().state == "done"
    on_models = [n for n in _notes(legacy_client) if n["scope"] == f"campaign:{cid}"]
    assert [(n["subject"], n["scope_name"]) for n in on_models] == [("tracker", "Saltmarch")]
    assert [n["id"] for n in _notes(legacy_client, cid)] == [n["id"] for n in on_models]


def test_the_record_is_strict_for_writers(home):
    config.read_config()
    for bad in ("", "[]", '{"fields": [], "notes": []}', '{"notes": [{"id": 3}]}'):
        retired.path().write_text(bad, encoding="utf-8")
        assert retired.read() == {"fields": {}, "notes": []}
        with pytest.raises(retired.RecordUnreadableError):
            retired.read(strict=True)
        with pytest.raises(retired.RecordUnreadableError):
            retired.record_fields("glm", {"model": "glm-5.3"})
        assert retired.path().read_text(encoding="utf-8") == bad


# ---- review round 1 ----
def test_a_real_c_h_fresh_install_takes_one_archive(home):
    """M-1: a C-H build's fresh install, as it is on disk: its seeding wrote
    `active_connection_id: openrouter` and the two seeded connections' model
    fields. Those are stored values, so retirement takes one archive, deletes
    and strips them, and a second `ensure` writes nothing. (A fresh install
    of THIS build takes none: `test_a_product_birth_store_takes_no_archive_and_writes_nothing`.)"""
    _write_config({**_born_by_c_h(), "active_connection_id": "openrouter"})
    for conn_id, kind, model in (("openrouter", "openrouter", config.DEFAULT_MODEL),
                                 ("claude", "claude", config.DEFAULT_CLAUDE_MODEL)):
        _conn_file(conn_id).parent.mkdir(parents=True, exist_ok=True)
        _conn_file(conn_id).write_text(dump_frontmatter(
            {"kind": kind, "name": conn_id.capitalize(), "base_url": "", "api_key": "",
             "model": model, "post_process": "none", "rev": "0123456789abcdef"}, ""),
            encoding="utf-8")
    (_conn_file("openrouter").parent / ".migrated").write_text("1", encoding="utf-8")
    assert retire.needs_archive(retire.pass_plan(legacy_plan.lookup(mode="retire")))

    assert migrate.ensure().state == "done"
    assert len(_archives(backups.RETIRE_PREFIX)) == 1
    assert "active_connection_id" not in _raw_config() and _retired(_raw_config())
    assert _held("openrouter") == [] and _held("claude") == []
    after = _digest(store.home())
    assert migrate.ensure().state == "done"
    assert _digest(store.home()) == after
    assert len(_archives(backups.RETIRE_PREFIX)) == 1


def test_a_retired_unmarked_campaigns_lost_effort_is_noted(home):
    """M-2, guarantee 7: a campaign marked retired but never migrated, its
    scene pinned to a GLM provider with an effort, is derived nothing -- so
    the effort is lost, and the record says so, from step 8's write."""
    _legacy()
    _glm("glm", "high")
    cid = _campaign("Saltmarch")
    _write_meta(cid, {**_meta(cid), RETIRED: "1", "route_scene": "glm"})
    assert migrate.ensure().state == "done"
    noted = [(n["kind"], n["scope"], n["subject"], n["effort"], n["dismissed"])
             for n in retired.read()["notes"] if n["scope"] == f"campaign:{cid}"]
    assert noted == [("unrepresentable", f"campaign:{cid}", keys.pin_key(r, "preset"),
                      "high", False) for r in ("scene", "speaker")]
    meta = _meta(cid)
    assert meta[keys.pin_key("scene", "provider")] == "glm" and _retired(meta)


class _Killed(BaseException):
    """A process killed mid-write, as far as the code under test can tell."""


def _route_preset_store() -> None:
    _legacy()
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    config.write_config(active_connection_id="glm", preset_summary="cold")


def _route_note_ids() -> list[str]:
    return sorted(n["id"] for n in retired.read()["notes"] if n["kind"] == "route_preset")


def test_a_kill_between_the_notes_and_the_scope_write_loses_nothing(home, monkeypatch):
    """I-1: a scope's notes can be planned only before its own write, so
    they are recorded first, in the same hold. Killed between the two, the
    notes are there; the next start finishes the scope and records them
    again by id -- nothing duplicated, nothing lost."""
    _route_preset_store()
    real = config.retire_write

    def killed(*_a, **_k):
        raise _Killed

    monkeypatch.setattr(config, "retire_write", killed)
    with pytest.raises(_Killed):
        migrate.ensure()
    noted = _route_note_ids()
    assert len(noted) == 2
    assert not _retired(_raw_config())

    monkeypatch.setattr(config, "retire_write", real)
    assert migrate.ensure().state == "done"
    assert _retired(_raw_config())
    assert _route_note_ids() == noted
    assert len(retired.read()["notes"]) == len({n["id"] for n in retired.read()["notes"]})


def test_a_kill_after_the_scope_write_keeps_the_notes(home, monkeypatch):
    """I-1: killed once `config.md` is retired -- when the planner can no
    longer plan its notes -- they are already in the record."""
    _route_preset_store()
    _campaign("Saltmarch")
    real = retire.retire_campaign

    def killed(*_a, **_k):
        raise _Killed

    monkeypatch.setattr(retire, "retire_campaign", killed)
    with pytest.raises(_Killed):
        migrate.ensure()
    assert _retired(_raw_config())
    noted = _route_note_ids()
    assert len(noted) == 2

    monkeypatch.setattr(retire, "retire_campaign", real)
    assert migrate.ensure().state == "done"
    assert _route_note_ids() == noted


def test_models_shows_a_campaigns_planner_note_before_retirement(legacy_client):
    """6b review I-2, spec 11.4: on a format-1 store, a campaign's
    route-preset loss is on `/models` -- with the campaign's name -- before
    any retirement has recorded it; dismissing it from there holds."""
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    cid = _campaign("Saltmarch")
    campaigns.set_campaign_routing(cid, {"route_tracker": "glm", "preset_tracker": "cold"})
    assert not keys.is_current(config.read_config())

    on_models = [n for n in _notes(legacy_client) if n["scope"] == f"campaign:{cid}"]
    assert [(n["subject"], n["scope_name"], n["kind"]) for n in on_models] == [
        ("tracker", "Saltmarch", "route_preset")]
    assert not retired.path().exists()

    got = legacy_client.post(f"/api/inference/retired-notes/{on_models[0]['id']}/dismiss")
    assert got.status_code == 200, got.text
    assert on_models[0]["id"] not in [n["id"] for n in _notes(legacy_client)]
    assert on_models[0]["id"] not in [n["id"] for n in _notes(legacy_client, cid)]


def test_an_unreadable_record_leaves_a_scope_with_nothing_written(home):
    """6b review M-1: a scope with notes to record and a preset to derive,
    over a record that cannot be read, writes neither the preset nor its
    settings: the notes are recorded first, strictly."""
    _legacy()
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    config.write_config(active_connection_id="glm", preset_summary="cold")
    _c_era()
    retired.path().write_text("{not json", encoding="utf-8")
    before = (store.home() / "config.md").read_bytes()

    got = migrate.ensure()

    assert not (store.home() / "sampler_presets" / "reasoning-high.json").exists()
    assert (store.home() / "config.md").read_bytes() == before
    assert "retirement record" in got.retirement["failed"]
    assert retired.path().read_text(encoding="utf-8") == "{not json"



def test_the_notes_speak_the_pages_words(home):
    """6b review M-2: the fact note names the model panel's own labels and
    where that entry is; the stranded-effort note says why, without naming
    a marker."""
    _legacy()
    _glm("glm", "high")
    llm_connections.update_connection("glm", prefill=True, vision="on")
    assert inference_fixtures.migrate_as_c_h().state == "done"
    llm_connections.facts_path("glm").unlink()
    cid = _campaign("Saltmarch")
    _write_meta(cid, {**{k: v for k, v in _meta(cid).items() if k != FORMAT},
                      RETIRED: "1", "route_scene": "glm"})
    assert migrate.ensure().state == "done"
    texts = {(n["kind"], n["subject"]): n["text"] for n in retired.read()["notes"]}
    assert texts[("fact_not_carried", "prefill")] == (
        "The provider “glm” had Keep writing set to prefill for the model “glm-5.3”, and "
        "that model's entry under Providers does not say so, so it no longer applies — "
        "this was not carried over.")
    assert texts[("fact_not_carried", "vision")].startswith(
        "The provider “glm” had Reads images set to “on” for the model “glm-5.3”")
    assert texts[("unrepresentable", keys.pin_key("scene", "preset"))] == (
        "On the Scene turns route in this campaign, the GLM provider “glm” no longer sends "
        "its reasoning effort (high), because no preset there sets one — this was not "
        "carried over.")


def test_a_retired_but_unmarked_config_notes_its_lost_effort_at_the_switch(home):
    """6b review M-7: a format-1 `config.md` already carrying the retirement
    marker, its Primary on a GLM provider at high, is switched with no
    derivation -- and the switch records the lost effort first."""
    _legacy()
    _glm("glm", "high")
    config.write_config(active_connection_id="glm", inference_retired="1")
    assert not keys.is_current(config.read_config())
    assert migrate.ensure().state == "done"
    noted = [(n["scope"], n["kind"], n["subject"], n["provider_id"], n["effort"])
             for n in retired.read()["notes"]]
    assert ("global", "unrepresentable", keys.role_key("primary", "preset"), "glm",
            "high") in noted
    cfg = _raw_config()
    assert cfg[keys.role_key("primary", "provider")] == "glm"
    assert not cfg.get(keys.role_key("primary", "preset"))


def test_an_unreadable_record_leaves_a_campaign_with_nothing_written(home):
    """6b re-review N-3, the campaign twin of the global case: a campaign
    with a route-preset loss to record and a pin that derives a preset, over
    a record that cannot be read, writes neither."""
    _legacy()
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    cid = _campaign("Saltmarch")
    campaigns.set_campaign_routing(cid, {"route_scene": "glm", "preset_scene": "cold"})
    _c_era()
    retire.retire_global(legacy_plan.lookup(mode="retire"))
    plan = retire.pass_plan(legacy_plan.lookup(mode="retire"))
    unit = next(u for u in plan.units if u.cid == cid)
    assert unit.notes and unit.work
    retired.path().write_text("{not json", encoding="utf-8")
    before = campaigns.paths.campaign_meta_path(cid).read_bytes()

    got = migrate.ensure()

    assert not (store.home() / "sampler_presets" / "reasoning-high.json").exists()
    assert campaigns.paths.campaign_meta_path(cid).read_bytes() == before
    assert f"campaign {cid}" in got.retirement["failed"]
    assert retired.path().read_text(encoding="utf-8") == "{not json"
