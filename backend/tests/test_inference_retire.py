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
    for name in _archives(backups.RETIRE_PREFIX):
        (backups.backup_dir() / name).unlink()
    (home / ".cache" / "inference-migration.json").unlink()
    before = _meta(cid)

    assert migrate.ensure().state == "done"

    assert _archives(backups.RETIRE_PREFIX) == []
    assert _meta(cid) == {**before, RETIRED: "1"}


def _born_by_c_h() -> dict:
    """`config.md` as a C-H build -- or this build, before 6b takes the legacy
    defaults out of `read_config` -- births it: every legacy key present as
    "", the format marker, and no retirement marker."""
    meta = {**dict.fromkeys(keys.LEGACY_GLOBAL_KEYS, ""), "theme": "system",
            FORMAT: keys.CURRENT_FORMAT}
    assert len(keys.LEGACY_GLOBAL_KEYS) == 16
    return meta


def test_a_fresh_install_with_empty_legacy_keys_takes_no_archive(home):
    """N3: empty legacy values are absent ones. A fresh C-H install and one
    campaign born the same way: `left()` names only the missing markers, the
    pass needs no archive, and `ensure` writes the marker (dropping the empty
    keys with it) and nothing else; a second `ensure` writes nothing."""
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
    conn = inference.resolve("chat", cid).attempts[0].conn
    return llm_sampling.effective(conn)["effective"].get("reasoning_effort", "")


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
