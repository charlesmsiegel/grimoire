"""Today's connection routing, sampling and fallback resolution, held fixed.

`fixtures/inference_baseline.json` was generated ONCE, from the tree as it stood
before the inference resolver refactor (`python -m tests.inference_baseline
--write`), and **is never regenerated after the commit that introduced it**: the
refactor is behaviour-neutral, so a failure here means the new resolver answers
differently from the code it replaced, and the fix is in the code. What each
state contains and what is observed is in `tests/inference_baseline.py`.
"""

from __future__ import annotations

import json

import pytest

import grimoire.store as store

from . import inference_baseline as baseline

BASELINE = json.loads(baseline.FIXTURE.read_text(encoding="utf-8"))


#: Observations the frozen JSON still records and `observe` no longer makes:
#: `routing`, the `/api/routing` bodies, retired with those endpoints in slice
#: C, Task 8. The JSON is untouched; every comparison drops the key.
RETIRED = ("routing",)


def without_retired(observed: dict) -> dict:
    """`observed` minus the retired observations (`RETIRED`)."""
    return {k: v for k, v in observed.items() if k not in RETIRED}


#: Tasks claimed since the JSON was recorded, each by the route sibling whose
#: cells it must equal (slice F, Task 6). `scene-break-title` is the title a
#: YES suggests, drafted on the `summary` route beside `rolling-summary`, so
#: it resolves wherever that does -- at both scopes, in every state.
NEW_TASKS = {"scene-break-title": "rolling-summary"}


def without_new_tasks(observed: dict, cells: tuple[str, ...] = ("tasks",)) -> dict:
    """`observed` with each `NEW_TASKS` cell asserted equal to its sibling's
    and then dropped, under each path in `cells` (a dotted path to a
    `{task: {"global", "campaign"}}` map), so the frozen JSON, which never saw
    the task, compares against the rest unchanged."""
    out = dict(observed)
    for path in cells:
        *parents, leaf = path.split(".")
        holder = out
        for key in parents:
            holder[key] = dict(holder[key])
            holder = holder[key]
        by_task = dict(holder[leaf])
        for task, sibling in NEW_TASKS.items():
            assert by_task[task] == by_task[sibling], (path, task, by_task[task])
            assert set(by_task[task]) == {"global", "campaign"}
            del by_task[task]
        holder[leaf] = by_task
    return out


def test_every_state_has_a_recorded_baseline():
    assert sorted(BASELINE) == sorted(baseline.STATES)


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_resolution_matches_the_baseline(state, tmp_path):
    """Before any migration, every state answers what the JSON recorded --
    minus `routing`, whose endpoints slice C's Task 8 retired (`RETIRED`)."""
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        observed = without_new_tasks(baseline.observe(client, ctx))
        assert without_retired(observed) == without_retired(BASELINE[state])


#: The only differences a migrated store may show against the frozen baseline
#: (slice C, Task 2), each by name:
#:
#: - `routing`: the `/api/routing` bodies. A retired surface (Task 8: the
#:   endpoints are gone and `observe` no longer reads them, `RETIRED`).
#: - the provider-only reroll overrides (`PROVIDER_ONLY_OVERRIDES`): what a
#:   connection named alone means at format 2 is Task 7's to define, and Task 7
#:   asserts those cells in full.
#: - the PRESET of a reroll onto another provider at a named model: spec 5.6
#:   replaces only the parts an override names, so the standing selection's
#:   preset rides along where a legacy store took the named connection's own.
#:   Not dropped but rewritten (`migrated_expectation`): the cell must then
#:   carry exactly the standing route's sampling.
#:
#: Nothing else may differ -- the fallback cells included. (A task claimed
#: since, `NEW_TASKS`, is held to its sibling and dropped before either
#: comparison.)
ALLOWED_AFTER_MIGRATION = ("routing",)
PROVIDER_ONLY_OVERRIDES = ("connection", "unknown_connection", "keyless_connection",
                           "openrouter_connection")


def without_allowed_differences(observed: dict) -> dict:
    """`observed` minus what a migration may change (above)."""
    out = {k: v for k, v in observed.items() if k not in ALLOWED_AFTER_MIGRATION}
    out["overrides"] = {name: cell for name, cell in observed["overrides"].items()
                        if name not in PROVIDER_ONLY_OVERRIDES}
    return out


def migrated_expectation(recorded: dict) -> dict:
    """`recorded` as a migrated store answers it: each reroll naming another
    provider AND a model carries the standing route's sampling (the `none`
    cell's), and nothing else about the cell changes."""
    cells = recorded["overrides"]
    standing = cells["none"]
    if "conn" not in standing:
        return recorded
    out = dict(cells)
    for name, body in baseline.OVERRIDE_BODIES.items():
        cell = cells[name]
        if (body.get("connection_id") and body.get("model") and "conn" in cell
                and cell["conn"] != standing["conn"]):
            out[name] = {**cell, "sampling": standing["sampling"]}
    return {**recorded, "overrides": out}


def test_the_provider_only_overrides_are_every_one_naming_a_connection_alone():
    named_alone = tuple(name for name, body in baseline.OVERRIDE_BODIES.items()
                        if body.get("connection_id") and not body.get("model"))
    assert named_alone == PROVIDER_ONLY_OVERRIDES


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_each_baseline_state_resolves_identically_after_migration(state, tmp_path):
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        got = baseline.migrate_state(state)
        assert got.state == "done", got
        assert store.inference_keys.is_current(store.read_config())
        observed = without_new_tasks(baseline.observe(client, ctx))
        assert (without_allowed_differences(observed)
                == without_allowed_differences(migrated_expectation(BASELINE[state])))
