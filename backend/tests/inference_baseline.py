"""A characterization snapshot of how LLM connections are resolved today.

`fixtures/inference_baseline.json` records, for each of a handful of store
states, what the routing layer answers: which connection (and model, sampling
preset and fallback) every task resolves to at the global and the campaign
scope, what a per-call override resolves to, what the display surfaces say
(`send_images_reach`, a connection's own sampling, the scene context
breakdown's sampling), what `store.embed_space.resolve` returns, and the
routing GET endpoints' bodies.

The routing endpoints were retired in slice C (Task 8): `observe` no longer
reads them, and the equivalence tests compare every state without the
`routing` key the JSON still holds (`test_inference_equivalence.RETIRED`).

The point is the refactor of that layer (inference slice A): the resolver is
rewritten underneath all of these call sites, and `test_inference_equivalence`
holds every one of them to this file. **The JSON is frozen -- it records what
the code did BEFORE the refactor and is never regenerated after the commit that
introduced it.** `--write` exists to produce it once, from the unmodified tree;
a later change that makes the test fail has changed behaviour, and the fix is
in the code, not in the fixture.

Connection `rev`s are random per write, so `observe` normalises each one to
`<rev:{id}>` before returning, and asserts that no stray 16-hex-digit run (a
rev that escaped the replacement) is left in the output.

Names are the codebase's placeholders (Realm, Mara, Saltmarch Run) and
invented connection ids; nothing here describes a real library.
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.llm import effective_model
from grimoire.main import create_app
from grimoire.store import routing
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import resolve as inference
from tests.inference_fixtures import legacy_store

FIXTURE = Path(__file__).parent / "fixtures" / "inference_baseline.json"

#: The marker a sampler preset takes to mean "no preset" at a route.
PRESET_CLEAR = store.sampler_presets.PRESET_CLEAR


# ---- a client on a fresh store ----
@contextlib.contextmanager
def client_at(home: Path) -> Iterator[TestClient]:
    """A TestClient on an app whose store root is `home`.

    Mirrors the `client` fixture of `test_routing_routes.py`, without
    `monkeypatch`, so `python -m tests.inference_baseline` can build the same
    stores the test does. The environment is put back afterwards."""
    # os.environ directly, not monkeypatch: the `__main__` path has no pytest.
    before = os.environ.get("GRIMOIRE_HOME")
    os.environ["GRIMOIRE_HOME"] = str(home)
    try:
        importlib.reload(store)
        # A fresh store is born at format 2 now; the baselines were frozen at
        # format 1, so every one of them starts from a legacy library.
        legacy_store(home)
        with TestClient(create_app()) as client:
            yield client
    finally:
        if before is None:
            os.environ.pop("GRIMOIRE_HOME", None)
        else:
            os.environ["GRIMOIRE_HOME"] = before


# ---- building blocks: one small helper per ingredient ----
def _ok(response):
    """`response`, after asserting the write was accepted: a builder whose API
    call silently failed would freeze a baseline of the wrong state."""
    assert response.status_code == 200, (response.request.url, response.status_code,
                                         response.text)
    return response


def _config(**fields) -> None:
    """`store.write_config`, then check the raw file holds exactly these values
    (a key the store narrows away, or a value it rewrote, would otherwise be
    frozen into the baseline unnoticed)."""
    store.write_config(**fields)
    raw, _ = parse_frontmatter((store.home() / "config.md").read_text(encoding="utf-8"))
    for key, value in fields.items():
        assert raw.get(key) == value, (key, value, raw.get(key))


def _campaign_routing(cid: str, fields: dict) -> None:
    store.campaigns.set_campaign_routing(cid, fields)
    meta = store.campaigns.read_campaign(cid)["meta"]
    for key, value in fields.items():
        assert meta.get(key) == value, (key, value, meta.get(key))


def _world_and_campaign(client: TestClient) -> str:
    """World "Realm", character "Mara", campaign "Saltmarch Run" with one scene
    and a short transcript; returns the campaign id."""
    wid = _ok(client.post("/api/worlds", json={"name": "Realm"})).json()["id"]
    _ok(client.post(f"/api/worlds/{wid}/characters",
                    json={"name": "Mara", "version_name": "main"}))
    cid = _ok(client.post("/api/campaigns",
                          json={"name": "Saltmarch Run", "world": wid})).json()["id"]
    sid = _ok(client.post(f"/api/campaigns/{cid}/scenes",
                          json={"title": "Saltmarch"})).json()["id"]
    store.scenes.append_message(cid, sid, "user", "Something happened at the docks.")
    store.scenes.append_message(cid, sid, "assistant", "The keeper said nothing.")
    return cid


def _fresh(client: TestClient) -> dict:
    """The seeded openrouter connection holds a key and a model; nothing else."""
    cid = _world_and_campaign(client)
    _ok(client.put("/api/llm-connections/openrouter",
                   json={"api_key": "sk-test-active", "model": "vendor/active"}))
    return {"cid": cid}


def _connection(client: TestClient, name: str, **fields) -> str:
    body = {"kind": "openrouter", "name": name, **fields}
    return _ok(client.post("/api/llm-connections", json=body)).json()["id"]


def _local(client: TestClient, preset: str = "") -> str:
    return _connection(client, "local", kind="openai_compatible",
                       base_url="http://localhost:1234/v1", model="local-model",
                       sampler_preset=preset)


def _spare(client: TestClient) -> str:
    return _connection(client, "spare", api_key="sk-spare", model="vendor/spare")


def _presets() -> None:
    store.sampler_presets.create_preset("warm", {"temperature": 0.9})
    store.sampler_presets.create_preset("cold", {"temperature": 0.2, "top_k": 40})


def _catalog_for_openrouter() -> None:
    """A catalog sidecar for the seeded connection, tagged with its CURRENT rev
    (written last, so no later edit can strand it)."""
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models("openrouter", [
        {"id": "vendor/active", "params": ["temperature", "top_p"]},
        {"id": "vendor/bigger", "params": ["temperature"]},
    ], rev)


def _routed(client: TestClient) -> dict:
    ctx = _fresh(client)
    cid = ctx["cid"]
    _presets()
    _local(client, preset="warm")
    _spare(client)
    _ok(client.put("/api/llm-connections/openrouter", json={"sampler_preset": "warm"}))
    # Global scope. `voice` and `tracker`'s preset name things that do not exist
    # (a connection / a preset), which the PUT refuses, so these are written the
    # way a deletion upstream would have left them.
    _config(
        route_dossier="local", route_summary="claude", route_voice="gone",
        route_tracker="local", preset_scene="cold", preset_summary=PRESET_CLEAR,
        preset_tracker="nosuch", fallback_connection_id="spare",
        # Beyond the brief's list: with the setting off `send_images_reach` is
        # "off" for every state and records nothing about the connection.
        send_images="on")
    # Campaign scope. `absorb` names a connection that existed and was deleted:
    # a delete cannot reach into a campaign's frontmatter, so it is made that
    # way here rather than invented.
    gone = _connection(client, "deleted", api_key="sk-deleted")
    _campaign_routing(cid, {
        "route_scene": "local", "preset_scene": "warm", "route_tracker": "spare",
        "route_absorb": gone, "preset_dossier": PRESET_CLEAR})
    _ok(client.delete(f"/api/llm-connections/{gone}"))
    _catalog_for_openrouter()
    return ctx


def _keyless(client: TestClient) -> dict:
    ctx = _fresh(client)
    _connection(client, "nokey")
    _config(route_absorb="nokey", fallback_connection_id="nokey")
    return ctx


def _no_active(client: TestClient) -> dict:
    ctx = _routed(client)
    _config(active_connection_id="")
    return ctx


def _claude_active(client: TestClient) -> dict:
    ctx = _fresh(client)
    store.llm_connections.update_connection("claude", model="")
    assert store.llm_connections.read_connection_raw("claude")["model"] == ""
    _local(client)
    _config(active_connection_id="claude", embeddings_connection_id="local",
                       embeddings_model="embed-small", send_images="on")
    return ctx


def _embed_with_dangling(client: TestClient) -> dict:
    ctx = _claude_active(client)
    _spare(client)
    _config(route_voice="gone", route_dossier="spare")
    # Not valid UTF-8: the connection exists as a file and cannot be read.
    (store.home() / "llm_connections" / "spare.md").write_bytes(b"\xff\xfe\x00 not text")
    assert store.embed_space.resolve({"embeddings_connection_id": "spare",
                                      "embeddings_model": "m"}) is None
    return ctx


def _whitespace(client: TestClient) -> dict:
    ctx = _fresh(client)
    _local(client)
    _spare(client)
    _config(route_dossier="  local  ", fallback_connection_id="  spare  ",
                       embeddings_connection_id="  local  ", embeddings_model="embed-small")
    return ctx


def _pre_connections(client: TestClient) -> dict:
    """A store from before `llm_connections/` existed: only legacy flat keys."""
    ctx = _fresh(client)
    shutil.rmtree(store.home() / "llm_connections")
    legacy = {"openrouter_key": "sk-test-legacy", "model": "vendor/legacy"}
    (store.home() / "config.md").write_text(dump_frontmatter(legacy, ""), encoding="utf-8")
    return ctx


#: States whose store holds a connection FILE that cannot be read -> its id.
#: The migration refuses to switch over one (it cannot tell what that
#: connection's model was, and would write the guess down for good), and says
#: which; removing the file is the remedy its reason points at. Every reader
#: but the migration's already takes an unreadable connection for an absent
#: one, so nothing this module observes moves with it.
UNREADABLE: dict[str, str] = {"embed_with_dangling": "spare"}


def migrate_state(state: str):
    """`migrate.ensure()` on a store `STATES[state]` built: for a state in
    `UNREADABLE`, the refused first run is checked and the file removed before
    the run that switches. Returns the last run's status."""
    from grimoire.store.inference import migrate

    got = migrate.ensure()
    if state in UNREADABLE:
        conn_id = UNREADABLE[state]
        assert got.state == "failed" and f"connection {conn_id}" in got.reason, got
        assert not store.inference_keys.is_current(store.read_config())
        (store.home() / "llm_connections" / f"{conn_id}.md").unlink()
        got = migrate.ensure()
    return got


#: state name -> a builder that fills a fresh store and returns `{"cid": ...}`.
STATES: dict[str, Callable[[TestClient], dict]] = {
    "fresh": _fresh,
    "routed": _routed,
    "keyless": _keyless,
    "no_active": _no_active,
    "claude_active": _claude_active,
    "embed_with_dangling": _embed_with_dangling,
    "whitespace": _whitespace,
    "pre_connections": _pre_connections,
}


# ---- what is observed ----
def _failure(exc: HTTPException) -> dict:
    return {"status": exc.status_code, "detail": exc.detail}


def _resolved(conn: dict) -> dict:
    return {"conn": conn["id"], "model": effective_model(conn),
            "sampling": conn["sampling"], "model_params": conn.get("model_params")}


def _task(client: TestClient, task: str, cid: str) -> dict:
    """What the seam answered for `task` when this baseline was recorded: the
    resolution, refused only on the terms it refused on then (a missing
    connection or credential, `_refuse_unusable`).

    Not `require_inference` itself any more. Slice B gave the seam one new,
    intended refusal -- a primary KNOWN unable to do what its route needs
    (`_refuse_incapable`) -- and its one change to a recorded state is the
    `image-description` task on a `claude` connection, which no user could
    ever reach as recorded: the only route naming that task
    (`image_draft_prompt`) refused that connection itself before the seam
    took the check over. That refusal is pinned in `test_inference_resolve.py`
    and `test_image_description_draft.py`; this keeps reading the slice-A
    answer so the frozen fixture stays what it was.
    """
    try:
        resolved = inference.resolve(task, cid)
        routes.common._refuse_unusable(resolved)
        narrowed = routes.common._narrowed(resolved)
    except HTTPException as exc:
        return _failure(exc)
    conn = narrowed.conn
    # The routes the facade builds from the chain it is sent: the fallback's
    # target, read back as the id and sampling block its dict carried.
    attempts = client.app.state.llm._routes(narrowed.chain)
    fallback = attempts[1].target if len(attempts) > 1 else None
    return {**_resolved(conn),
            "fallback": None if fallback is None
            else {"id": fallback.provider_id, "sampling": dataclasses.asdict(fallback.sampling)}}


#: Each body is what a reroll request carries; only the two override fields are
#: ever read, so a namespace stands in for the pydantic model.
OVERRIDE_BODIES: dict[str, dict] = {
    "none": {},
    "connection": {"connection_id": "spare"},
    "model": {"model": "vendor/bigger"},
    "both": {"connection_id": "spare", "model": "vendor/bigger"},
    "unknown_connection": {"connection_id": "nope"},
    "long_model": {"model": "x" * 10_000},
    "keyless_connection": {"connection_id": "nokey"},
    # Added after review: a named OpenRouter connection, so the catalog lookup
    # for `model_params` runs under an override (in `routed`, `vendor/bigger`
    # is in its sidecar and `model` alone never reaches it).
    "openrouter_connection": {"connection_id": "openrouter"},
    "openrouter_bigger": {"connection_id": "openrouter", "model": "vendor/bigger"},
}


def _override(body: dict, cid: str) -> dict:
    try:
        resolved, routed = routes.common.override_inference(
            SimpleNamespace(**body), "regenerate", cid)
    except HTTPException as exc:
        return _failure(exc)
    conn = resolved.conn
    return {**_resolved(conn), "routed": routed}


def _connection_ids() -> list[str]:
    """Every connection the store lists, plus the ids the states name that may
    not be listable (deleted, unreadable, or never created)."""
    listed = {c["id"] for c in store.llm_connections.list_connections()}
    return sorted(listed | {"openrouter", "claude", "local", "spare", "nokey", "gone"})


def _display(client: TestClient, cid: str) -> dict:
    sid = store.scenes.list_scenes(cid)[0]["id"]
    context = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context")
    return {
        "send_images_reach": client.get("/api/config").json()["send_images_reach"],
        "connection_sampling": {i: routes.config._connection_sampling(i)
                                for i in _connection_ids()},
        "context_sampling": {"status": context.status_code,
                             "sampling": context.json().get("sampling")
                             if context.status_code == 200 else None},
    }


#: Explicit configs for `embed_space.resolve(cfg)`, asked in every state: the
#: configured pair is covered by `embedding`; these are the other ways of not
#: having one (an id naming nothing, an unreadable file where a state has one,
#: a kind with no /embeddings route, no model, stray whitespace).
EMBEDDING_CFGS: dict[str, dict] = {
    "dangling": {"embeddings_connection_id": "gone", "embeddings_model": "embed-small"},
    "spare": {"embeddings_connection_id": "spare", "embeddings_model": "embed-small"},
    "local": {"embeddings_connection_id": "local", "embeddings_model": "embed-small"},
    "local_padded": {"embeddings_connection_id": "  local  ", "embeddings_model": "embed-small"},
    "openrouter_kind": {"embeddings_connection_id": "openrouter",
                        "embeddings_model": "embed-small"},
    "no_model": {"embeddings_connection_id": "local", "embeddings_model": ""},
}


def _revs() -> dict[str, str]:
    """rev -> connection id, for every connection that can be read."""
    out = {}
    for conn in store.llm_connections.list_connections():
        if conn.get("rev"):
            out[conn["rev"]] = conn["id"]
    return out


def _normalise(value, revs: dict[str, str]):
    """`value` with every rev replaced by `<rev:{id}>`, timestamps by `<time>`."""
    if isinstance(value, dict):
        return {k: "<time>" if k == "fetched_at" and v else _normalise(v, revs)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v, revs) for v in value]
    if isinstance(value, str):
        for rev, conn_id in revs.items():
            value = value.replace(rev, f"<rev:{conn_id}>")
    return value


def observe(client: TestClient, ctx: dict) -> dict:
    """Everything the baseline records, for the store `client` is serving."""
    cid = ctx["cid"]
    out = {
        "tasks": {task: {"global": _task(client, task, ""),
                         "campaign": _task(client, task, cid)}
                  for task in [*sorted(routing.TASK_ROUTE), ""]},
        "overrides": {name: _override(body, cid) for name, body in OVERRIDE_BODIES.items()},
        "display": _display(client, cid),
        "embedding": store.embed_space.resolve(),
        "embedding_cfgs": {name: store.embed_space.resolve(cfg)
                           for name, cfg in EMBEDDING_CFGS.items()},
    }
    out = _normalise(out, _revs())
    leftover = re.search(r"[0-9a-f]{16}", json.dumps(out))
    assert leftover is None, f"an unnormalised rev survives: {leftover.group()}"
    return out


def _snapshot(name: str, home: Path) -> dict:
    with client_at(home) as client:
        return observe(client, STATES[name](client))


def main(argv: list[str]) -> int:
    if argv != ["--write"]:
        sys.stderr.write("usage: python -m tests.inference_baseline --write\n")
        return 2
    baseline = {}
    for name in STATES:
        with tempfile.TemporaryDirectory() as tmp:
            baseline[name] = _snapshot(name, Path(tmp))
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {FIXTURE}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
