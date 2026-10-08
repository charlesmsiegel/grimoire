"""Every model-settings write refuses a store a newer build switched, inside
the hold that writes (`config.format_hold`).

A format check made before a write takes its lock lets a newer build's switch
land between the two -- three call sites shipped that shape before the check
moved into one hold. This file enumerates the writers that go through it, on
a store already at a newer format: each raises `config.NewerFormatError` (the
model test's verdict filing reports "not recorded" instead) and leaves every
settings file as it was. Then two races, through the routes: a switch landing
after the route's own early check.

Invented connection ids and the codebase's placeholder names only.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable

import pytest

import grimoire.store as store
from grimoire import routes
from grimoire.store import config, llm_connections, locks, proclock, sampler_presets
from grimoire.store import inference_keys as keys
from grimoire.store.campaigns import lifecycle as campaign_lifecycle
from grimoire.store.inference import facts, migrate, settings
from tests.llm_fakes import FakeCatalog

from . import inference_baseline as base

NEWER = "3"


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _current(client) -> str:
    """A migrated store: `spare` with a stated fact, two presets, one campaign."""
    cid = base._fresh(client)["cid"]
    base._spare(client)
    base._presets()
    assert migrate.ensure().state == "done"
    facts.set_stated("spare", "vendor/spare", prefill=True)
    return cid


def _settings_files() -> dict[str, bytes]:
    """Every file a model-settings write can touch, by path."""
    home = store.home()
    found = [home / "config.md", *sorted((home / "llm_connections").glob("*")),
             *sorted(sampler_presets._dir().glob("*")),
             *sorted((home / "campaigns").glob("*/campaign.md"))]
    return {str(p.relative_to(home)): p.read_bytes() for p in found if p.is_file()}


SEL = {"roles": {"fast": {"selection": {"provider": "spare", "model": "vendor/spare"}}}}

#: Every model-settings writer, as a call on a store whose campaign is `cid`.
WRITERS: dict[str, Callable[[str], object]] = {
    "settings.write global": lambda cid: settings.write("global", "", SEL),
    "settings.write campaign": lambda cid: settings.write("campaign", cid, SEL),
    "create_connection": lambda cid: llm_connections.create_connection(
        "openrouter", "Saltmarch Two", api_key="sk-two"),
    "update_connection name": lambda cid: llm_connections.update_connection(
        "spare", name="Saltmarch Renamed"),
    "update_connection key": lambda cid: llm_connections.update_connection(
        "spare", api_key="sk-other"),
    "update_connection model field": lambda cid: llm_connections.update_connection(
        "spare", refuse_model_fields=True, prefill=True),
    "delete_connection": lambda cid: llm_connections.delete_connection("spare"),
    "facts.set_stated": lambda cid: facts.set_stated("spare", "vendor/spare", vision="off"),
    "facts.set_overrides": lambda cid: facts.set_overrides(
        "spare", "vendor/spare", {"vision": "yes"}),
    "facts.state": lambda cid: facts.state("spare", "vendor/spare", overrides={"embed": "no"}),
    "facts.adopt_legacy": lambda cid: facts.adopt_legacy(
        "spare", "vendor/spare", {"vision": "off"}),
    "create_preset": lambda cid: sampler_presets.create_preset("hot", {"temperature": 1.2}),
    "update_preset": lambda cid: sampler_presets.update_preset(
        "warm", "warm", {"temperature": 0.5}),
    "delete_preset": lambda cid: sampler_presets.delete_preset("cold"),
}


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_every_model_settings_writer_refuses_a_newer_store(client, writer):
    cid = _current(client)
    store.write_config(**{keys.FORMAT_KEY: NEWER})
    before = _settings_files()
    with pytest.raises(config.NewerFormatError):
        WRITERS[writer](cid)
    assert _settings_files() == before


def test_a_test_verdict_is_not_filed_on_a_newer_store(client):
    _current(client)
    rev = llm_connections.read_connection_raw("spare")["rev"]
    store.write_config(**{keys.FORMAT_KEY: NEWER})
    before = _settings_files()
    assert facts.record_verified("spare", "vendor/spare", rev, {"generate": {"ok": True}}) is False
    assert _settings_files() == before


def test_every_writer_still_writes_at_the_current_format(client):
    """The hold refuses only a newer store: the same calls land at format 2
    (a legacy model field excepted, which format 2 refuses on its own)."""
    cid = _current(client)
    # The delete last: the other writers name the provider it removes.
    for name in sorted(WRITERS, key=lambda n: n == "delete_connection"):
        if name == "update_connection model field":
            with pytest.raises(llm_connections.ModelFieldsRefusedError):
                WRITERS[name](cid)
            continue
        WRITERS[name](cid)
    assert "saltmarch-two" in {c["id"] for c in llm_connections.list_connections()}


def _then_newer(monkeypatch, module, name: str) -> None:
    """`module.name` as it is, then a newer build's switch -- another process
    landing between the route's early check and the write's hold."""
    real = getattr(module, name)

    def checked_then_switched(*args, **kwargs):
        out = real(*args, **kwargs)
        store.write_config(**{keys.FORMAT_KEY: NEWER})
        return out

    monkeypatch.setattr(module, name, checked_then_switched)


def test_a_provider_edit_after_the_switch_is_409(client, monkeypatch):
    """A rename changes no legacy model field, so the store's model-field check
    never ran: the record a newer build wrote was rewritten (and `_write_raw`
    keeps only the fields this build knows). The hold refuses it."""
    _current(client)
    _then_newer(monkeypatch, routes.config, "refuse_newer")
    before = _settings_files()
    got = client.put("/api/llm-connections/spare", json={"name": "Saltmarch Renamed"})
    assert got.status_code == 409, got.text
    assert got.json() == routes.common.NEWER_FORMAT
    after = _settings_files()
    assert after.pop("config.md") != before.pop("config.md")   # the switch itself
    assert after == before


def test_the_campaign_write_holds_the_format_through_its_write(client, monkeypatch):
    """The campaign settings write checks the global format and writes the
    campaign in one `config_lock` hold -- the lock a format switch takes, from
    any process -- so no switch lands between the check and the write."""
    cid = _current(client)
    real = campaign_lifecycle.set_campaign_inference
    held: list[bool] = []

    def observed(*args, **kwargs):
        got: list[bool] = []

        def try_switch() -> None:
            lock = store.locks.config_lock()
            ok = lock.acquire(blocking=False)
            if ok:
                lock.release()
            got.append(ok)

        t = threading.Thread(target=try_switch)
        t.start()
        t.join()
        held.append(not got[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(campaign_lifecycle, "set_campaign_inference", observed)
    settings.write("campaign", cid, SEL)
    assert held == [True]


def test_settings_writes_are_refused_by_the_hold_through_the_routes(client, monkeypatch):
    """The switch landing after `refuse_unmigrated`: both scopes answer 409
    `newer_format` and write nothing of their own."""
    cid = _current(client)
    for where in ("", cid):
        _then_newer(monkeypatch, routes.inference, "refuse_unmigrated")
        url = f"/api/campaigns/{cid}/inference" if where else "/api/inference/settings"
        before = _settings_files()
        got = client.put(url, json=SEL)
        assert got.status_code == 409, got.text
        assert got.json() == routes.common.NEWER_FORMAT
        after = _settings_files()
        after.pop("config.md"), before.pop("config.md")
        assert after == before
        monkeypatch.undo()
        store.write_config(**{keys.FORMAT_KEY: keys.CURRENT_FORMAT})


# ---- the catalog cache (Codex round 5) ----

def _wait_run(client, run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["state"] in ("landed", "failed"):
            return run
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never settled")


def test_the_catalog_refresh_refuses_a_newer_store(client):
    """Capability resolution reads the catalog cache, so it is model settings:
    a newer store is refused before anything is fetched."""
    _current(client)
    store.write_config(**{keys.FORMAT_KEY: NEWER})
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeCatalog(models=[{"id": "m"}])
    got = client.post("/api/llm-connections/spare/models/refresh")
    assert got.status_code == 409, got.text
    assert got.json() == routes.common.NEWER_FORMAT
    assert llm_connections.cached_models("spare")["fetched_at"] == ""


def test_a_switch_landing_during_the_fetch_leaves_the_cache_alone(client):
    """The fetch is a provider round trip; a newer build's switch landing while
    it is out is refused in the hold the cache is written in, and the run says
    so rather than landing."""
    _current(client)

    class SwitchedMeanwhile(FakeCatalog):
        async def list_models(self, conn):
            store.write_config(**{keys.FORMAT_KEY: NEWER})
            return await super().list_models(conn)

    client.app.dependency_overrides[routes.get_llm] = \
        lambda: SwitchedMeanwhile(models=[{"id": "m"}])
    started = client.post("/api/llm-connections/spare/models/refresh")
    assert started.status_code == 202, started.text
    run = _wait_run(client, started.json()["run"]["id"])
    assert run["state"] == "failed", run
    assert run["error"]["kind"] == "newer_format", run
    assert llm_connections.cached_models("spare")["fetched_at"] == ""


# ---- one cross-process hold over every read -> check -> write ----

def _held() -> bool:
    """Whether this process holds the cross-process side of `config_lock`
    (its OS file lock), not only a thread lock."""
    return locks.config_lock()._fd is not None


def test_the_connection_lock_is_the_cross_process_hold():
    assert llm_connections.LOCK is locks.config_lock()


def _spy(monkeypatch, module, name: str, seen: list[bool]) -> None:
    real = getattr(module, name)

    def spied(*args, **kwargs):
        seen.append(_held())
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spied)


@pytest.mark.parametrize("case", ["facts write", "verdict", "provider edit", "preset edit",
                                  "campaign write"])
def test_each_model_settings_read_is_inside_the_cross_process_hold(client, monkeypatch, case):
    """Two servers on one store are supported, and a read-modify-write whose
    read sits outside the cross-process lock loses the other server's write.
    Each writer's READ -- not only its write -- happens with the OS file lock
    held."""
    cid = _current(client)
    seen: list[bool] = []
    if case == "facts write":
        _spy(monkeypatch, facts, "_load_for_write", seen)
        facts.set_stated("spare", "vendor/spare", vision="off")
    elif case == "verdict":
        rev = llm_connections.read_connection_raw("spare")["rev"]
        _spy(monkeypatch, facts, "_load_for_write", seen)
        _spy(monkeypatch, llm_connections, "read_connection_raw", seen)
        assert facts.record_verified("spare", "vendor/spare", rev, {"generate": {"ok": True}})
    elif case == "provider edit":
        _spy(monkeypatch, llm_connections, "_read", seen)
        llm_connections.update_connection("spare", name="Saltmarch Renamed")
    elif case == "preset edit":
        _spy(monkeypatch, sampler_presets, "read_preset", seen)
        sampler_presets.update_preset("warm", "warm", {"temperature": 0.5})
    else:
        _spy(monkeypatch, settings, "_campaign_meta", seen)
        settings.write("campaign", cid, SEL)
    assert seen and all(seen), seen


def test_the_migration_copies_facts_inside_the_cross_process_hold(client, monkeypatch):
    """The facts copy and the marker share one cross-process hold, so another
    server's legacy provider edit cannot land between them."""
    base._fresh(client)
    llm_connections.update_connection("openrouter", vision="off")
    assert not keys.is_current(store.read_config())
    seen: list[bool] = []
    _spy(monkeypatch, facts, "_load_for_write", seen)
    assert migrate.ensure().state == "done"
    assert seen and all(seen), seen


_HOLDER = """
import sys, time
sys.path[:0] = {path!r}
from grimoire.store import proclock
fd = proclock.acquire({lock!r}, None)
print("HELD", flush=True)
time.sleep({hold})
proclock.release(fd)
"""


def test_another_process_holding_the_hold_stops_a_facts_write(client, monkeypatch):
    """A real second process: while it holds the model-settings lock, a facts
    write here waits for it -- and, past the timeout, refuses -- having written
    nothing."""
    _current(client)
    path = llm_connections.facts_path("spare")
    before = path.read_bytes()
    lock_file = proclock.lock_path(store.home(), "domain", "config")
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLDER.format(path=sys.path, lock=str(lock_file), hold=10.0)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "HELD"
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.3)
        with pytest.raises(locks.StoreBusy):
            facts.set_stated("spare", "vendor/spare", vision="off")
        assert path.read_bytes() == before
    finally:
        child.kill()
        child.wait()
