"""Contention reaches the user as a 409, and never as a hang, a silent skip,
or a mislabelled error (#234)."""

import pytest
from fastapi.testclient import TestClient

from grimoire import main, store
from grimoire.store import campaigns, locks, worlds


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return TestClient(main.create_app())


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid, module="pool-basic")


def test_store_busy_becomes_a_409(client, monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)

    def busy(*a, **k):
        raise locks.CampaignBusy(cid)

    monkeypatch.setattr(store.scenes, "create_scene", busy)
    r = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"})
    assert r.status_code == 409
    assert "another grimoire process" in r.json()["detail"]


def test_module_edit_busy_becomes_a_409_too(client, monkeypatch, tmp_path):
    """The handler is registered on the StoreBusy base, not on CampaignBusy,
    so the module-edit domain gets the same treatment."""
    _campaign(monkeypatch, tmp_path)

    def busy(*a, **k):
        raise locks.ModuleEditBusy()

    monkeypatch.setattr(store.module_edit, "create_module", busy)
    r = client.post("/api/modules", json={"name": "Whatever"})
    assert r.status_code == 409
    assert "another grimoire process" in r.json()["detail"]


def test_startup_survives_a_busy_recover(monkeypatch, tmp_path):
    """A second backend starting while the first is mid-edit must serve, not
    refuse to start: recovery is idempotent and the holder is already doing it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))

    def busy():
        raise locks.ModuleEditBusy()

    monkeypatch.setattr(main.module_edit, "recover", busy)
    with TestClient(main.create_app()) as c:
        assert c.get("/api/config").status_code == 200


def test_capture_baseline_propagates_contention(monkeypatch, tmp_path):
    """capture_baseline swallows every Exception so a capture failure cannot
    fail scene creation. Contention is the one exception: a scene that quietly
    cannot be audited is not recoverable, a 409 is.

    Safe because its only caller, scenes._create_scene, already holds the
    campaign lock (reentrantly) -- so this re-raise guards a future caller that
    does not, rather than a live path.
    """
    from grimoire.store import audit

    cid = _campaign(monkeypatch, tmp_path)

    def busy(c):
        raise locks.CampaignBusy(c)

    # The shared module, not `audit.locks`: `audit` is a package now, and its
    # facade re-exports only names its own files define -- the `locks` import
    # lives on `audit.baselines`. Pointing at `grimoire.store.locks` is what
    # this always meant and survives any further reshuffling.
    monkeypatch.setattr(locks, "campaign_lock", busy)
    with pytest.raises(locks.CampaignBusy):
        audit.capture_baseline(cid, "0001-x")


# ---- contention in an SSE finalizer (#234) ----


@pytest.mark.parametrize("lazy", [False, True])
def test_a_busy_finalize_emits_an_error_frame_and_persists_nothing(
        monkeypatch, tmp_path, lazy):
    """finalize() runs OUTSIDE _fence_stream's try, so contention there would
    abort the stream with no frame at all.

    It must NOT route through on_error: that persists watcher.narration, and
    narration whose roll fence has no proposal record destroys the
    proposal-before-narration guarantee.

    Parameterized over a list-returning and a GENERATOR finalize, because
    guarding only the call leaves a generator's StoreBusy escaping the `for`.
    """
    import anyio

    from grimoire.routes import streaming

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    persisted = []
    monkeypatch.setattr(streaming, "_persist_reply",
                        lambda *a, **k: persisted.append(("persist",) + a))

    class _Client:
        async def stream(self, messages, conn, usage=None):
            yield "narrated text"

    def finalize(watcher):
        raise store.locks.CampaignBusy("run")

    def lazy_finalize(watcher):
        def gen():
            raise store.locks.CampaignBusy("run")
            yield  # pragma: no cover
        return gen()

    def on_error(watcher):
        persisted.append(("on_error",))

    resp = streaming._fence_stream("run", "0001-x", [], {}, _Client(),
                                   lazy_finalize if lazy else finalize, on_error)

    async def drain():
        return [chunk async for chunk in resp.body_iterator]

    frames = "".join(anyio.run(drain))
    assert '"busy"' in frames, frames
    assert not persisted, f"a busy finalize persisted something: {persisted}"
