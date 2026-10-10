"""Today's connection routing, sampling and fallback resolution, held fixed.

`fixtures/inference_baseline.json` was generated ONCE, from the tree as it stood
before the inference resolver refactor (`python -m tests.inference_baseline
--write`), and **is never regenerated after the commit that introduced it**: the
refactor is behaviour-neutral, so a failure here means the new resolver answers
differently from the code it replaced, and the fix is in the code. What each
state contains and what is observed is in `tests/inference_baseline.py`.

Since slice I a legacy store is read only through the planner, in memory
(`legacy_plan.overlay`), and resolved as format 2. So the store as built --
format 1 -- is held to the JSON through the migration's named differences
and `retired_expectation`'s, and `test_planned_equals_persisted` holds what
it plays in memory equal to what it plays once migrated.
"""

from __future__ import annotations

import copy
import json
from typing import NamedTuple
from unittest import mock

import pytest

import grimoire.store as store
from grimoire import llm_sampling
from grimoire.store import routing
from grimoire.store.inference import migrate
from grimoire.store.inference import resolve as inference

from . import inference_baseline as baseline
from . import wire_kit

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
        # Migrated, not retired -- the store as a C-H build migrated it
        # (retirement held off): the derivation is still the planner's, in
        # memory, so `retired_expectation`'s differences apply here too. The
        # retired store is held to the same answer by
        # `test_planned_equals_persisted`.
        with mock.patch.object(migrate, "_retire", lambda *_args: None):
            got = baseline.migrate_state(state)
        assert got.state == "done", got
        assert store.inference_keys.is_current(store.read_config())
        observed = without_new_tasks(baseline.observe(client, ctx))
        assert (without_allowed_differences(observed)
                == without_allowed_differences(
                    retired_expectation(migrated_expectation(BASELINE[state]), state)))


# ---- slice I: the legacy layout, read in memory through the planner ----
class Derived(NamedTuple):
    """A derived reasoning preset, as the planner names it (ruling 4)."""

    id: str
    name: str
    params: dict


#: Rule 1's presets: per state, the base preset each derived preset replaces
#: on the GLM provider's slots, exact. Only slice C's two GLM states derive.
DERIVED: dict[str, dict[str, Derived]] = {
    "glm_reasoning": {"warm": Derived("warm-reasoning-low", "warm · reasoning low",
                                      {"temperature": 0.9, "reasoning_effort": "low"})},
    "glm_max_under_route_preset": {
        "warm": Derived("warm-reasoning-max", "warm · reasoning max",
                        {"temperature": 0.9, "reasoning_effort": "max"})},
}
#: The provider those slots are on (`inference_baseline_c._glm`).
GLM = "glm"
#: Rule 2: the legacy effort each state's builder set on the GLM connection
#: whose route preset sets none (`_glm(client, "max")`).
LEGACY_EFFORT: dict[str, str] = {"glm_max_under_route_preset": "max"}
#: Where the baseline records a ROUTE-level preset (`sampling.scope`).
ROUTE_SCOPES = ("global", "campaign")


def _derive_sampling(sampling: dict, derived: dict[str, Derived]) -> bool:
    """Rule 1 on one recorded `sampling` block, in place: its base preset
    replaced by the derived one. True when it applied."""
    made = derived.get(sampling.get("preset_id", ""))
    if made is None:
        return False
    sampling.update(preset_id=made.id, preset_name=made.name, params=dict(made.params))
    return True


def _derive_report(report: dict, derived: dict[str, Derived]) -> bool:
    """Rule 1 on a recorded `llm_sampling.report` (the context breakdown): the
    derived preset's id and name, and the effort it now names among what is
    applied (`report` lists a requested level as applied; the wire is
    `effective`'s, held equal by the `c.lowered` cells)."""
    made = derived.get(report.get("preset_id", ""))
    if made is None:
        return False
    report.update(preset_id=made.id, preset_name=made.name,
                  applied={**report["applied"],
                           "reasoning_effort": made.params["reasoning_effort"]})
    return True


def _rule_1(out: dict, state: str) -> bool:
    """Rule 1 over every recorded cell of `out`, in place: whether it applied."""
    derived = DERIVED.get(state, {})
    cells = [cell for by_scope in out["tasks"].values() for cell in by_scope.values()]
    cells += list(out["overrides"].values())
    applied = [_derive_sampling(cell["sampling"], derived)
               for cell in cells if cell.get("conn") == GLM]
    context = out["display"]["context_sampling"]["sampling"]
    if context is not None:
        applied.append(_derive_report(context, derived))
    return any(applied)


def _rule_2(out: dict, recorded: dict, state: str) -> bool:
    """Rule 2 over `out`'s lowered cells, in place: whether it applied."""
    applied = False
    for task, cells in out.get("c", {}).get("lowered", {}).items():
        for scope, lowered in cells.items():
            sampling = recorded["tasks"][task][scope].get("sampling")
            if (lowered is None or sampling is None or sampling["scope"] not in ROUTE_SCOPES
                    or "reasoning_effort" in sampling["params"]
                    or "reasoning_effort" not in lowered["effective"]):
                continue
            assert lowered["effective"]["reasoning_effort"] == LEGACY_EFFORT[state], (
                state, task, scope)
            del lowered["effective"]["reasoning_effort"]
            applied = True
    return applied


def _retired(recorded: dict, state: str) -> tuple[dict, set[str]]:
    """`(expectation, rules applied)`: `retired_expectation`, and which of its
    rules touched this state ("derived", "route_preset")."""
    out = copy.deepcopy(recorded)
    touched = {rule for rule, applied in (("derived", _rule_1(out, state)),
                                          ("route_preset", _rule_2(out, recorded, state)))
               if applied}
    return out, touched


def retired_expectation(recorded: dict, state: str) -> dict:
    """`recorded` as a legacy store answers it once play reads the legacy
    layout through the planner (slice I) -- the only named differences, each
    exact (`test_the_named_differences_touch_exactly_their_states`):

    1. **Derived slots.** A GLM slot whose legacy effort the planner carries
       over runs on its derived preset (`DERIVED`): `sampling.preset_id`,
       `preset_name` and `params` become the derived preset's (its params are
       the base's plus that effort), and the context breakdown names the
       effort as applied. The wire (`c.lowered`'s `effective`) is unchanged.
    2. **Route preset over a GLM effort.** A cell whose recorded preset is
       route-level (`ROUTE_SCOPES`), sets no reasoning effort, and whose wire
       carried the connection's legacy effort anyway: that effort is asserted
       (`LEGACY_EFFORT`) and dropped from the wire, and nothing else. It is
       noted durably instead (ratification item 3).

    Rule 3 of the plan (a GLM `max` the planner cannot carry) is for a
    declined ratification item 1; item 1 was ratified, so `max` is derived
    under rule 1 and rule 3 has nothing to name. Everything else must be
    equal."""
    return _retired(recorded, state)[0]


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_resolution_matches_the_baseline(state, tmp_path):
    """The store as each state builds it -- format 1, unmigrated -- played
    in memory through the planner, answers exactly what the JSON recorded,
    minus `routing` (`RETIRED`) alone: a format-1 store still plays as
    format 1 where it always answered differently (a reroll naming a
    provider runs that provider's own model and preset; the `missing_key`
    sentence), so neither `migrated_expectation` nor the provider-only cells
    are excused here (user ruling 2026-10-09). `retired_expectation`'s rules
    name only the GLM states of the other baseline, so it is the identity
    on this one, and is not applied."""
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        observed = without_new_tasks(baseline.observe(client, ctx))
        assert not store.inference_keys.is_current(store.read_config())
        assert retired_expectation(BASELINE[state], state) == BASELINE[state]
        assert without_retired(observed) == without_retired(BASELINE[state])


#: Rule 3, the retired-and-stripped store only: the states whose recorded
#: provider-editor readout shows a connection's OWN preset (`sampler_preset`),
#: which the strip removes. Ruled covered by ratification item 4 (the provider
#: editor's readout of a connection field retirement removes on purpose;
#: nothing in the frontend renders `LLMConnectionDetail.sampling`).
STRIPPED_OWN_PRESET = ("no_active", "routed", "glm_reasoning", "glm_max_under_route_preset")


def _strip_rule(out: dict) -> bool:
    """Rule 3 on `out`, in place: every connection readout
    (`display.connection_sampling`) at `connection` scope reads as a
    connection with no preset of its own (scope `none`, nothing applied or
    dropped). True when it applied."""
    readouts = out["display"]["connection_sampling"]
    applied = False
    for conn_id, cell in readouts.items():
        if cell is not None and cell["scope"] == "connection":
            readouts[conn_id] = {**cell, "applied": {}, "dropped": [], "preset_id": "",
                                 "preset_name": "", "scope": "none"}
            applied = True
    return applied


def stripped_expectation(expected: dict) -> dict:
    """`expected` (a `retired_expectation`) as the store answers it once
    retirement has stripped the connections' legacy fields: rule 3 above and
    nothing else -- the wire, the reroll overrides, the embedding and every
    lowered dict are held to the JSON unchanged."""
    out = copy.deepcopy(expected)
    _strip_rule(out)
    return out


def test_the_named_differences_touch_exactly_their_states():
    """Each of `retired_expectation`'s rules applies to the states it names
    and to no other, across both frozen baselines -- and so does rule 3,
    the stripped store's."""
    from . import test_inference_equivalence_c as equivalence_c

    touched: dict[str, set[str]] = {"derived": set(), "route_preset": set(), "stripped": set()}
    for frozen in (BASELINE, equivalence_c.BASELINE):
        for state, recorded in frozen.items():
            expected, rules = _retired(migrated_expectation(recorded), state)
            for rule in rules:
                touched[rule].add(state)
            if _strip_rule(copy.deepcopy(expected)):
                touched["stripped"].add(state)
    assert touched == {"derived": set(DERIVED), "route_preset": set(LEGACY_EFFORT),
                       "stripped": set(STRIPPED_OWN_PRESET)}


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_each_baseline_state_resolves_identically_after_retirement(state, tmp_path):
    """Guarantee 9: the full `ensure` -- migrated, retired, stripped, nothing
    left -- held to the JSON with the migration's named differences,
    `retired_expectation`'s and rule 3's (`stripped_expectation`) only."""
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        assert baseline.migrate_state(state).state == "done"
        status = migrate.status()
        assert status.retirement == {"left": [], "failed": ""}, status.retirement
        observed = without_new_tasks(baseline.observe(client, ctx))
        assert (without_allowed_differences(observed)
                == without_allowed_differences(stripped_expectation(
                    retired_expectation(migrated_expectation(BASELINE[state]), state))))


def planned_cells(cid: str) -> dict:
    """What every task resolves to at both scopes, as the seam sees it: the
    primary's provider, model, preset and sampling, what it sends
    (`effective`), its fallback (and whether the facade sends it), and the
    seam's refusal. The comparison `test_planned_equals_persisted` makes."""
    def cell(task: str, scope_cid: str) -> dict:
        resolved = inference.resolve(task, scope_cid)
        out: dict = {"refusal": inference.refusal(resolved)}
        if not resolved.attempts:
            return out
        first = resolved.attempts[0]
        out.update(provider=first.provider_id, model=first.model, preset=first.preset_id,
                   sampling=wire_kit.sampling_block(first.target.sampling),
                   effective=llm_sampling.effective(first.target)["effective"])
        if len(resolved.attempts) > 1:
            fb = resolved.attempts[1]
            out["fallback"] = {"provider": fb.provider_id, "model": fb.model,
                               "preset": fb.preset_id, "sampling": wire_kit.sampling_block(fb.target.sampling),
                               "sent": resolved.chain is not None
                               and resolved.chain.fallback is not None}
        return out

    return {task: {"global": cell(task, ""), "campaign": cell(task, cid)}
            for task in [*sorted(routing.TASK_ROUTE), ""]}


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_planned_equals_persisted(state, tmp_path):
    """What a legacy store plays in memory, through the planner, is what it
    plays once the migration and retirement have persisted the plan (`mapped`,
    then the derived presets and their repoint) and deleted the legacy keys
    it was planned from (slice I, Task 6a)."""
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        planned = planned_cells(ctx["cid"])
        assert baseline.migrate_state(state).state == "done"
        cfg = store.read_config()
        assert store.inference_keys.is_current(cfg)
        assert cfg[store.inference_keys.RETIRED_KEY] == "1"
        assert planned_cells(ctx["cid"]) == planned
