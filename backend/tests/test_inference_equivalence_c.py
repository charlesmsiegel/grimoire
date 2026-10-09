"""What slice A's fixture never exercised, held fixed across slice C.

`fixtures/inference_baseline_c.json` was generated ONCE, from the tree at the
base of inference slice C (`python -m tests.inference_baseline_c --write`), and
**is never regenerated after the commit that introduced it**. Slice C migrates
the states it records -- a GLM reasoning effort, a route preset over one, post
images switched against the catalog, prefill and strict post-processing, a
legacy OpenRouter embedding choice -- and the migration is behaviour-neutral,
so a failure here means a migrated (or unmigrated) store answers differently
from the code it replaced, and the fix is in the code. What each state contains
and what is observed is in `tests/inference_baseline_c.py`.
"""

from __future__ import annotations

import json

import pytest

import grimoire.store as store
from grimoire.store.inference import migrate

from . import inference_baseline_c as baseline
from . import test_inference_equivalence as equivalence

BASELINE = json.loads(baseline.FIXTURE.read_text(encoding="utf-8"))

#: Where this sweep observes a task: the seam's answer (`tasks`) and the
#: lowered connection (`c.lowered`). A task claimed since the JSON was recorded
#: (`test_inference_equivalence.NEW_TASKS`) is held to its sibling in both.
CELLS = ("tasks", "c.lowered")
NEW_TASKS = equivalence.NEW_TASKS


def test_every_state_has_a_recorded_baseline():
    assert sorted(BASELINE) == sorted(baseline.STATES)


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_resolution_matches_the_baseline(state, tmp_path):
    """Before any migration, played in memory through the planner, every state
    answers what the JSON recorded -- minus `routing`
    (`test_inference_equivalence.RETIRED`), with the migration's named
    differences and `test_inference_equivalence.retired_expectation`'s."""
    with baseline.base.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        observed = equivalence.without_new_tasks(baseline.observe(client, ctx), CELLS)
        assert not store.inference_keys.is_current(store.read_config())
        assert (equivalence.without_allowed_differences(observed)
                == equivalence.without_allowed_differences(
                    equivalence.retired_expectation(
                        equivalence.migrated_expectation(BASELINE[state]), state)))


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_each_baseline_state_resolves_identically_after_migration(state, tmp_path):
    """The same states migrated to format 2, held to the same JSON, minus the
    differences `test_inference_equivalence` names and nothing else -- the
    migration is not retirement, so the derivation is still in memory and
    `retired_expectation` applies as it does before the migration."""
    with baseline.base.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        got = migrate.ensure()
        assert got.state == "done", got
        assert store.inference_keys.is_current(store.read_config())
        observed = equivalence.without_new_tasks(baseline.observe(client, ctx), CELLS)
        assert (equivalence.without_allowed_differences(observed)
                == equivalence.without_allowed_differences(
                    equivalence.retired_expectation(
                        equivalence.migrated_expectation(BASELINE[state]), state)))


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_planned_equals_persisted(state, tmp_path):
    """`test_inference_equivalence.test_planned_equals_persisted`, over this
    baseline's states: the GLM efforts, the facts, the legacy embedding."""
    with baseline.base.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        planned = equivalence.planned_cells(ctx["cid"])
        assert migrate.ensure().state == "done"
        assert store.inference_keys.is_current(store.read_config())
        assert equivalence.planned_cells(ctx["cid"]) == planned
